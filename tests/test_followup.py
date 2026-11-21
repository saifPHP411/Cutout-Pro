"""Exercise recovery, cache, export signals and the owner-scoped analytics workflow."""
import base64
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")
temp = tempfile.TemporaryDirectory(prefix="peakcut-followup-")
os.environ.update(PEAKCUT_DATA=temp.name, DATABASE_URL="", PEAKCUT_ACCOUNTS="1", PEAKCUT_FETCH_MODEL="0")
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import numpy as np
from studio import accounts, analytics_import, auth, backup, ctx, db, download_cache, jobs, learning, media, posting, reactions, reframe, render, server, watermarks

ROOT = Path(temp.name)
KEY = "mobile recovery check phrase"
USER = "u_testfollowup"


class FollowupTests(unittest.TestCase):
    def test_cached_source_is_private_and_independent(self):
        url = "https://www.youtube.com/watch?v=sample-test"
        calls = []
        def fetch(owner, sequence):
            folder = ROOT / f"download-{owner}-{sequence}"
            folder.mkdir()
            def downloader():
                calls.append(owner)
                p = folder / "source.mp4"
                p.write_bytes(b"the same downloaded video")
                return p
            with ctx.as_user(owner):
                return download_cache.fetch(url, folder, "source", [720], downloader)
        first = fetch(USER, 1)
        first.unlink()
        second = fetch(USER, 2)
        self.assertEqual(second.read_bytes(), b"the same downloaded video")
        second.write_bytes(b"job-modified")
        self.assertEqual(fetch(USER, 3).read_bytes(), b"the same downloaded video")
        self.assertEqual(fetch("u_distinct", 1).read_bytes(), b"the same downloaded video")
        self.assertEqual(calls, [USER, "u_distinct"])

    def test_expiry_and_limit(self):
        with ctx.as_user("u_cachetime"), patch.object(download_cache, "_limit", return_value=5):
            calls = []
            for n in range(3):
                folder = ROOT / f"expires-{n}"
                folder.mkdir()
                def load():
                    calls.append(n)
                    p = folder / "source.mp4"
                    p.write_bytes(b"abcd")
                    return p
                download_cache.fetch("https://youtu.be/sample", folder, "source", [1 if n < 2 else 2], load)
                if n == 0:
                    entry = next(download_cache._directory().iterdir())
                    os.utime(entry, (10, 10))
            self.assertEqual(calls, [0, 1, 2])
            self.assertEqual(len(list(download_cache._directory().iterdir())), 1)

    def test_chat_in_local_time_and_eta(self):
        signal = reactions.signal(reactions.validate([{"seconds": 67, "count": 20}, {"seconds": 61, "count": 1}]), 60)
        self.assertTrue(signal(6, 8))
        self.assertFalse(signal(0, 2))
        with self.assertRaises(ValueError):
            reactions.validate([{"seconds": float("nan"), "count": 4}])
        estimate = jobs.estimate_remaining({"status": "running", "phase": "transcribing", "progress": 0.53,
                                            "transcribe_started": 30}, now=40)
        self.assertEqual(estimate, 10)
        self.assertIsNone(jobs.estimate_remaining({"status": "paused", "phase": "transcribing"}))
        planning = {"status": "running", "phase": "analyzing", "phase_started": 30, "eta_history": {"analyzing": 25}}
        self.assertEqual(jobs.estimate_remaining(planning, now=40), 15)
        self.assertIsNone(jobs.estimate_remaining(planning, now=60))

    def test_tracking_smoothing_and_low_evidence_fallback(self):
        class Detector:
            def detectMultiScale(self, frame, **kwargs):
                x = int(frame[0, 0])
                return [(x, 20, 36, 36)] if x else []
        samples = [np.full((180, 320), x, np.uint8) for x in range(95, 185, 5)]
        points = reframe.track(samples, Detector(), 9, 9/16, (0.10, 0.82))
        self.assertGreater(len(points), 4)
        self.assertLess(points[0][1], points[-1][1])
        self.assertIn("if(lt(t,", reframe.crop_expr(points))
        self.assertEqual(reframe.track([samples[0]] + [samples[0]*0]*18, Detector(), 9, 9/16, (.1,.82)), [])
        word = [{"w": "Hello", "s": 0.1, "e": 0.8}]
        layouts = [render.build_ass(word, 0, 1, "Watch", "clean", [], 210, 1500, safe_zone=zone)
                   for zone in ("youtube", "tiktok", "instagram")]
        self.assertEqual(len(set(layouts)), 3)

    def test_real_pcm_equivalence_and_logo_match(self):
        p = Path("web/brand/demo-bounce.mp4").resolve()
        wav = ROOT / "audio-regular.wav"
        media.extract_audio(p, wav)
        self.assertTrue(np.array_equal(media.load_audio(wav), media.decode_audio(p, ROOT / "audio-unused.wav", 4.6)))
        image = Image.new("L", (52, 38), 10)
        draw = ImageDraw.Draw(image)
        draw.polygon([(2, 30), (25, 3), (50, 31), (25, 23)], fill=230)
        draw.rectangle((18, 22, 26, 33), fill=110)
        png = ROOT / "logo-pro-test.png"
        image.save(png)
        export = ROOT / "known-logo.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-i", str(png), "-filter_complex_threads", "1",
                        "-filter_complex", "[0:v]scale=360:-2[base];[base][1:v]overlay=20:20", "-t", "2",
                        "-c:v", "libx264", "-threads", "2", str(export)], check=True)
        self.assertEqual(watermarks.scan(export, 2, watermarks.normalize(png.read_bytes()))["status"], "detected")

    def test_encrypted_backup_and_restore_do_not_modify_live_data(self):
        db.init()
        marker = ROOT / "private-config.json"
        marker.write_text('{"secret":"fixture-value-never-returned"}')
        try:
            with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as work:
                output = backup.create(Path(work), KEY)
                self.assertNotIn(b"fixture-value-never-returned", output.read_bytes())
                restored = backup.check(output, Path(work), KEY)
                self.assertEqual(restored["verification"], "integrity_check")
                self.assertEqual((Path(work) / "restore/data/private-config.json").read_text(), marker.read_text())
            with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as work:
                folder = Path(work)
                danger = folder / "bad.tar"
                with tarfile.open(danger, "w") as packed:
                    entry = tarfile.TarInfo("data/../../outside")
                    entry.size = 1
                    packed.addfile(entry, io.BytesIO(b"x"))
                encrypted = folder / "bad.pcb"
                backup._crypt(danger, encrypted, KEY)
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    backup.check(encrypted, folder, KEY)
            self.assertEqual(marker.read_text(), '{"secret":"fixture-value-never-returned"}')
        finally:
            marker.unlink()

    def test_admin_backup_requires_role_same_origin_and_maintenance(self):
        db.init()
        admin, _ = auth._create("follow-admin@example.test", "Admin", password_hash="unused")
        user, _ = auth._create("follow-user@example.test", "User", password_hash="unused")
        with db.engine.begin() as con:
            con.execute(db.users.update().where(db.users.c.id == admin.id).values(is_admin=True))
        admin_token, user_token = auth.start_session(admin.id), auth.start_session(user.id)
        client = TestClient(server.app)
        headers = {"Origin": "http://testserver", "X-PeakCut-Backup-Passphrase": base64.b64encode(KEY.encode()).decode()}
        client.cookies.set(auth.COOKIE, user_token)
        self.assertEqual(client.get("/api/admin/backup").status_code, 404)
        client.cookies.set(auth.COOKIE, admin_token)
        self.assertEqual(client.post("/api/admin/backup", headers=headers).status_code, 409)
        self.assertEqual(client.post("/api/admin/backup", headers={**headers, "Origin": "https://wrong.example"}).status_code, 403)
        with patch.object(server, "maintenance", return_value=True):
            self.assertEqual(client.post("/api/auth/signup", headers=headers).status_code, 503)
            self.assertEqual(client.get("/oauth/youtube/callback").status_code, 503)
            saved = client.post("/api/admin/backup", headers=headers)
            self.assertEqual(saved.status_code, 200, saved.text if saved.status_code != 200 else "")
            verified = client.post("/api/admin/backup/verify", headers=headers, content=saved.content)
            self.assertEqual(verified.status_code, 200, verified.text)
            self.assertEqual(verified.json()["database"], "sqlite")
            corrupt = saved.content[:-1] + bytes([saved.content[-1] ^ 1])
            self.assertEqual(client.post("/api/admin/backup/verify", headers=headers, content=corrupt).status_code, 422)

    def test_experiment_assignment_results_and_publish_gate(self):
        with ctx.as_user("u_followab"):
            learning.consent(True)
            data = learning._read()
            data["versions"] = [{"id": "challenger", "eligible": True, "weights": learning.DEFAULT}]
            learning._save(data)
            overview = learning.experiment(True, "challenger")
            self.assertEqual(overview["experiment"]["challenger"], "challenger")
            assigned = learning.assign("same uploaded content")
            self.assertEqual(assigned, learning.assign("same uploaded content"))
            self.assertEqual({learning.assign(str(n))["arm"] for n in range(30)}, {"a", "b"})
            self.assertFalse(posting.can_publish({"status": "done"}, {"experiment": assigned}))
            self.assertTrue(posting.can_publish({"reviewed": True}, {"experiment": assigned}))
            published = time.time() - 8*86400
            at = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat()
            clip = {"id": "c", "status": "done", "start": 1, "end": 20,
                    "ratings": {key: 55 for key in learning.FIELDS}}
            job = {"id": "j", "created": published-60, "source": {}, "experiment": assigned}
            metrics = {"published_at": at(published), "observed_at": at(published + 7*86400), "views": 13,
                       "retention": 62, "evidence": "Seven-day fixture"}
            result = learning.add(job, clip, metrics)["experiments"][assigned["id"]][assigned["arm"]]
            self.assertEqual(result, {"sources": 1, "retention": 62})
            learning.experiment(False)
            self.assertIsNone(learning.assign("same uploaded content"))

    def test_youtube_uses_approved_filters_and_safe_failures(self):
        import httpx
        account = {"platform": "youtube", "expires": time.time()+100, "access_token": "fixture"}
        stamp = time.time() - 20*86400
        def replies(url, **kwargs):
            self.assertEqual(url, "https://youtubeanalytics.googleapis.com/v2/reports")
            self.assertEqual(kwargs["params"]["filters"], "video==TestingID01")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fixture")
            return httpx.Response(200, json={"columnHeaders": [{"name": col} for col in analytics_import.METRICS],
                                             "rows": [[100, 59.5]]})
        row = analytics_import.youtube(account, "TestingID01", stamp, request=replies)
        self.assertEqual((row["retention"], row["interactions"]), (59.5, None))
        with self.assertRaisesRegex(ValueError, "permission"):
            analytics_import.youtube(account, "TestingID01", stamp, request=lambda *a, **k: httpx.Response(403))
        with self.assertRaises(ValueError):
            analytics_import.cohort(time.time())

    def test_pause_preserves_completed_export(self):
        with ctx.as_user(USER):
            job = jobs.create({"type": "file", "path": "", "name": "pause fixture"}, {}, enqueue=False)
            path = Path(job["dir"]) / "clips"
            path.mkdir(exist_ok=True)
            finished = path / "clip01.mp4"
            finished.write_bytes(b"completed export")
            job["clips"] = [{"id": "clip01", "status": "done"}]
            jobs.control(job["id"], "pause")
            self.assertEqual(finished.read_bytes(), b"completed export")
            self.assertEqual(job["clips"][0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
