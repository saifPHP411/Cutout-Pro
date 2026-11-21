"""Use isolated data and real subprocesses to test interruption and ownership."""
import asyncio
import errno
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")
root = tempfile.TemporaryDirectory(prefix="peakcut-control-")
os.environ.update(PEAKCUT_DATA=root.name, DATABASE_URL="", PEAKCUT_ACCOUNTS="1", PEAKCUT_FETCH_MODEL="0")
from fastapi.testclient import TestClient
from studio import auth, config, ctx, db, jobs, media, server, work_control

UID = "u_testowner"
OTHER = "test-other"


def wait_for(predicate):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        result = predicate()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError("Worker did not finish on time")


def running(pid):
    stat = Path(f"/proc/{pid}/stat")
    try:
        return stat.read_text().rsplit(")", 1)[1].lstrip()[0] != "Z"
    except FileNotFoundError:
        return False


class JobControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        jobs.start()

    def new(self, enqueue=False):
        with ctx.as_user(UID):
            return jobs.create({"type": "file", "path": "", "name": "Upload"}, {}, enqueue=enqueue)

    def test_interrupt_workers_and_recover(self):
        neighbour = self.new()
        original = Path(neighbour["dir"]) / "original.mp4"
        original.write_bytes(b"untouched")
        script = ("import subprocess,sys,time; from pathlib import Path;"
                  "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(45)']);"
                  "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(45)")
        for phase in ("downloading", "transcribing", "rendering"):
            with self.subTest(phase=phase):
                def process(job, pool):
                    jobs._set(job, status="running", phase=phase, stage=phase)
                    folder = Path(job["dir"])
                    (folder / "audio.wav").write_bytes(b"temporary")
                    if phase == "transcribing":
                        (folder / "active").touch()
                        work_control.sleep(45)
                    else:
                        pool(1).submit(work_control.bind(work_control.run),
                                       [sys.executable, "-c", script, str(folder / "pid")]).result()
                with patch.object(jobs, "_process_job", process):
                    job = self.new(enqueue=True)
                    folder = Path(job["dir"])
                    pid_path = folder / "pid"
                    ready = pid_path if phase != "transcribing" else folder / "active"
                    wait_for(ready.exists)
                    pid = int(pid_path.read_text()) if pid_path.exists() else None
                    with ctx.as_user(UID):
                        self.assertEqual(jobs.control(job["id"], "stop")["status"], "cancelling")
                    wait_for(lambda: job["status"] == "cancelled" and job["id"] not in jobs._active)
                    self.assertFalse((folder / "audio.wav").exists())
                    self.assertEqual(original.read_bytes(), b"untouched")
                    if pid and sys.platform == "linux":
                        wait_for(lambda: not running(pid))
                    with jobs._lock:
                        jobs._jobs.pop(job["id"])
                        jobs._by_user[UID].pop(job["id"])
                        jobs._load_file(folder / "job.json", UID)
                        jobs._pick_up_after_restart()
                        self.assertEqual(jobs._jobs[job["id"]]["status"], "cancelled")
                        self.assertNotIn(job["id"], jobs._tickets)

    def test_cancelled_state_survives_a_fresh_process(self):
        job = self.new()
        with ctx.as_user(UID):
            jobs.control(job["id"], "stop")
        with tempfile.TemporaryDirectory(prefix="peakcut-restart-") as temp:
            path = Path(temp) / "users" / UID / "jobs" / job["id"]
            path.mkdir(parents=True)
            (path / "job.json").write_bytes((Path(job["dir"]) / "job.json").read_bytes())
            code = ("from studio import jobs; import sys; jobs.load_all(); jobs._pick_up_after_restart();"
                    "print(jobs._jobs[sys.argv[1]]['status'], sys.argv[1] in jobs._tickets)")
            result = subprocess.check_output([sys.executable, "-c", code, job["id"]], text=True,
                                             env={**os.environ, "PEAKCUT_DATA": temp})
            self.assertEqual(result.strip(), "cancelled False")

    def test_pause_resume_and_concurrent_stop_delete(self):
        entered = threading.Event()
        resumed = threading.Event()
        sibling = self.new()
        untouched = Path(sibling["dir"]) / "source.mp4"
        untouched.touch()

        def process(job, pool):
            jobs._set(job, status="running", phase="transcribing", stage="Writing the transcript")
            checkpoint = Path(job["dir"]) / "words.json"
            if job.get("resume"):
                self.assertEqual(json.loads(checkpoint.read_text()), [{"w": "checkpoint"}])
                resumed.set()
            else:
                jobs._save_words(checkpoint.parent, [{"w": "checkpoint"}])
                entered.set()
            work_control.sleep(45)

        def action(name):
            with ctx.as_user(UID):
                return jobs.control(job["id"], name)

        with patch.object(jobs, "_process_job", process):
            job = self.new(enqueue=True)
            self.assertTrue(entered.wait(8))
            action("pause")
            wait_for(lambda: job["status"] == "paused")
            action("resume")
            self.assertTrue(resumed.wait(8))
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(action, ["stop-delete"] * 4))
            wait_for(lambda: job["id"] not in jobs._jobs)
            self.assertFalse(Path(job["dir"]).exists())
            self.assertTrue(untouched.exists())

    def test_restart_finishes_saved_stop_delete(self):
        job = self.new()
        folder = Path(job["dir"])
        with jobs._lock:
            jobs._persist_control(job, status="cancelling", stop_action="delete")
            jobs._load_file(folder / "job.json", UID)
            jobs._pick_up_after_restart()
        self.assertFalse(folder.exists())
        self.assertNotIn(job["id"], jobs._tickets)

    def test_http_ownership_and_actions(self):
        owner, _ = auth._create("job-control-owner@example.com", "Owner", password_hash="x")
        other, _ = auth._create("job-control-other@example.com", "Other", password_hash="x")
        with ctx.as_user(owner.id):
            job = jobs.create({"type": "file", "name": "A test video"}, {}, enqueue=False)
        client = TestClient(server.app)
        key = auth.cookie_args("x")["key"]
        url = f"/api/jobs/{job['id']}"
        self.assertIn(client.post(url + "/control/stop").status_code, (401, 403))
        client.cookies.set(key, auth.start_session(other.id))
        self.assertEqual(client.post(url + "/control/stop").status_code, 404)
        self.assertEqual(client.get(url + "/logs").status_code, 404)
        client.cookies.set(key, auth.start_session(owner.id))
        self.assertEqual(client.post(url + "/control/pause").json()["phase"], "paused")
        self.assertEqual(client.post(url + "/control/stop").json()["phase"], "cancelled")
        self.assertEqual(client.get(url + "/logs").status_code, 200)
        self.assertEqual(client.post(url + "/control/stop-delete").json(), {"deleted": True})
        self.assertEqual(client.get(url).status_code, 404)

    def test_storage_error_leaves_control_retryable(self):
        job = self.new()
        with ctx.as_user(UID):
            with patch.object(jobs, "save", side_effect=OSError(errno.ENOSPC, "No space")):
                with self.assertRaises(OSError):
                    jobs.control(job["id"], "stop")
            self.assertEqual(job["status"], "queued")
            self.assertEqual(jobs.control(job["id"], "stop")["status"], "cancelled")

    def test_inflight_ai_request_is_cancelled(self):
        import httpx

        entered, disconnected = threading.Event(), threading.Event()

        async def slow(request):
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                disconnected.set()
            return httpx.Response(200, json={})

        real = httpx.AsyncClient
        control = work_control.Control()

        def call():
            with work_control.use(control):
                with self.assertRaises(work_control.Interrupted):
                    work_control.request("POST", "https://provider.example.invalid/test", json={}, timeout=60)

        with patch.object(httpx, "AsyncClient",
                          side_effect=lambda **kw: real(transport=httpx.MockTransport(slow), **kw)):
            with ThreadPoolExecutor(1) as executor:
                future = executor.submit(call)
                self.assertTrue(entered.wait(3))
                control.stopped.set()
                self.assertTrue(disconnected.wait(3))
                future.result(timeout=3)

    def test_ffmpeg_failure(self):
        with work_control.use(work_control.Control()):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg failed"):
                media.run(["ffmpeg", "-v", "error", "-i", "/no-such-input", "-f", "null", "-"])

    @unittest.skipUnless(os.name == "posix", "Requires symlinks")
    def test_folder_symlink_is_rejected(self):
        job = self.new()
        with ctx.as_user(UID):
            jobs.control(job["id"], "stop")
            folder = Path(job["dir"])
            external = Path(root.name) / "external"
            external.mkdir()
            original = external / "keep"
            original.touch()
            folder.rename(folder.with_name("old-" + folder.name))
            folder.symlink_to(external, target_is_directory=True)
            with self.assertRaises(jobs.JobConflict):
                jobs.delete(job["id"])
            self.assertTrue(original.exists())


if __name__ == "__main__":
    unittest.main()
