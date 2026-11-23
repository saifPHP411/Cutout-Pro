"""Job pipeline: get video -> audio -> transcript -> AI picks -> rendered clips. One heavy task at a time."""
import collections
from contextlib import ExitStack, contextmanager
import json
import os
import math
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import ai, brain, cache, campaigns, captions, config, creators, ctx, download_cache, facecam, fetch_model, games, hashtags, learning, load, media, quality, reactions, render, roman, safe, transcribe, usage, work_control

class _FairQueue:
    """Whose video is next. Each person has their own line and the lines take turns, so someone who adds ten
    videos does not make everyone else wait behind all ten. Small tasks (re-making one clip) go first."""

    def __init__(self):
        self.cv = threading.Condition()
        self.quick = collections.deque()
        self.priorities = {}
        self.lines = {}                       # user -> their waiting videos, oldest first
        self.turn = collections.deque()       # users with something waiting, in the order they will be served
        self.epoch = 0                        # goes up whenever the line moves
        self._pos = (0.0, {})
        self.running = 0                      # videos being worked on right now
        self.last_start = 0.0
        self.held = False                     # videos are waiting only because the machine is near full

    def _room(self) -> bool:
        """May one more video start? One always may. More only while the machine has room, and a few seconds
        after the last one started, so its weight is already showing in the measurements."""
        if self.running == 0:
            return True
        return self.running < config.JOB_WORKERS and time.time() - self.last_start > 6 and not load.busy()

    def done(self):
        with self.cv:
            self.running = max(0, self.running - 1)
            self.cv.notify_all()

    def put(self, item, user=None, priority=0):
        with self.cv:
            if user is None:
                self.quick.append(item)
            else:
                if user not in self.lines:
                    self.lines[user] = collections.deque()
                    self.turn.append(user)
                line = self.lines[user]
                index = next((i for i, other in enumerate(line) if self.priorities.get(other[1], 0) < priority), len(line))
                line.insert(index, item)
                self.priorities[item[1]] = priority
            self.epoch += 1
            self.cv.notify()

    def discard(self, jid, user):
        with self.cv:
            self.priorities.pop(jid, None)
            if user in self.lines:
                waiting = collections.deque(item for item in self.lines[user] if item[1] != jid)
                if waiting:
                    self.lines[user] = waiting
                else:
                    del self.lines[user]
                    self.turn.remove(user)
                self._pos = (0.0, {})
                self.epoch += 1

    def get(self, quick_only=False):
        with self.cv:
            while True:
                if self.quick:
                    self.epoch += 1
                    return self.quick.popleft()
                if not quick_only and self.turn:
                    if self._room():
                        break
                    if not self.held:
                        self.held = True
                        self.epoch += 1
                elif self.held:
                    self.held = False
                    self.epoch += 1
                self.cv.wait(1.0)             # looked at again every second: the line moves the moment there is room
            self.epoch += 1
            self.held = False
            self.running += 1
            self.last_start = time.time()
            user = self.turn.popleft()
            item = self.lines[user].popleft()
            self.priorities.pop(item[1], None)
            if self.lines[user]:
                self.turn.append(user)
            else:
                del self.lines[user]
            return item

    def ahead(self) -> dict:
        """job id -> how many videos will start before it. Worked out at most once a second."""
        with self.cv:
            if time.time() - self._pos[0] < 1:
                return self._pos[1]
            out, lines, order, n = {}, {u: list(q) for u, q in self.lines.items()}, list(self.turn), 0
            depth = 0
            while order:
                nxt = []
                for u in order:
                    if depth < len(lines[u]):
                        out[lines[u][depth][1]] = n
                        n += 1
                        if depth + 1 < len(lines[u]):
                            nxt.append(u)
                order, depth = nxt, depth + 1
            self._pos = (time.time(), out)
            return out


_q = _FairQueue()
_live_q: "queue.Queue" = queue.Queue()   # live clips never wait behind a long video
_jobs: dict = {}
_by_user: dict = {}                       # user -> {job id: job}: listing one person's videos never scans everyone's
_lock = threading.RLock()
_workers = []
_active = {}    # job id -> Control, held until all child workers have stopped
_tickets = {}   # job id -> queue ticket, so a pause/resume cannot run an old queue entry
BUSY = ("queued", "running", "cancelling", "uploading")
INACTIVE = ("paused", "cancelled", "cancelling")


class JobConflict(RuntimeError):
    pass


on_clip_ready: list = []   # callbacks (job, clip), run once when a clip first finishes

DEFAULTS = {"max_clips": 6, "min_len": 15, "max_len": 60, "style": "bounce", "layout": "blur", "quality": 720,
            "range_start": None, "range_end": None, "captions": True, "hook_text": "", "description_text": "",
            "lang": "en", "mode": "balanced", "aspect": "9:16", "caption_mode": "original", "template": "", "campaign": "", "priority": 0, "safe_zone": "none", "experiment": False, "visual_triage": False}   # lang: the language spoken


LIMITS = {"max_clips": (1, 20), "min_len": (5, 120), "max_len": (10, 180), "quality": (360, 1080), "priority": (0, 1)}


def clean_options(options) -> dict:
    """Only known settings, each inside its allowed range. Raises ValueError on nonsense."""
    out = {}
    for k, v in (options if isinstance(options, dict) else {}).items():
        if k in ("captions", "experiment", "visual_triage"):
            if not isinstance(v, bool):
                raise ValueError("Captions must be on or off." if k == "captions" else "This option must be on or off.")
            out[k] = v
            continue
        if k in ("hook_text", "description_text"):     # kept even when empty: it clears the creator's own text for this video
            out[k] = creators.clean_hook(v) if k == "hook_text" else creators.clean_description(v)
            continue
        if k not in DEFAULTS or v in ("", None):
            continue
        if k in LIMITS:
            lo, hi = LIMITS[k]
            v = max(lo, min(hi, int(float(v))))
        elif k == "style":
            v = v if v in render.STYLES else DEFAULTS["style"]
        elif k == "layout":
            v = v if v in render.LAYOUTS else DEFAULTS["layout"]
        elif k == "campaign":
            v = str(v)
            if not re.fullmatch(r"[a-f0-9]{32}", v):
                raise ValueError("Choose a valid campaign.")
        elif k == "safe_zone":
            v = v if v in render.SAFE_ZONES else "none"
        elif k == "aspect":
            v = v if v in render.ASPECTS else DEFAULTS["aspect"]
        elif k == "caption_mode":
            v = v if v in captions.MODES else DEFAULTS["caption_mode"]
        elif k == "template":
            v = v if v in render.TEMPLATES else ""
        elif k == "mode":
            v = v if v in render.MODES else DEFAULTS["mode"]
        elif k == "lang":
            v = v if fetch_model.known(v) else DEFAULTS["lang"]
        else:                                    # range_start, range_end: seconds into the video
            v = float(v)
            if not math.isfinite(v) or v < 0:
                raise ValueError("That part of the video isn't valid.")
        out[k] = v
    if out.get("range_end") is not None and out["range_end"] <= out.get("range_start", 0):
        raise ValueError("The end of the chosen part must be after its start.")
    return out


def job_dir(jid) -> Path:
    return Path(_jobs[jid]["dir"])


HIDDEN = ("dir", "user", "source_path", "recover", "stop_action", "resume", "remake", "events", "content_fingerprint", "retry_errors", "chat_samples", "experiment", "eta_history", "phase_started")


def public(job) -> dict:
    """What the browser may see: no server paths, no owner id."""
    out = {k: v for k, v in job.items() if k not in HIDDEN}
    if not out.get("phase") or out["status"] in ("done", "error"):
        out["phase"] = {"done": "completed", "error": "failed", "running": "downloading"}.get(out["status"], out["status"])
    out.pop("processing_started", None)
    if job.get("processing_started"):
        out["elapsed"] = round((job.get("updated_at") or time.time()) - job["processing_started"])
    out["eta_seconds"] = estimate_remaining(job)
    out["source"] = {k: v for k, v in job["source"].items() if k != "path"}
    out["clips"] = [{k: v for k, v in c.items() if k not in ("src", "fixed_words", "fixed_for", "caption_words")} for c in job["clips"]]
    ai = job.get("ai") or {}
    out["ai"] = {"tokens": ai.get("tokens", 0), "note": ai.get("note", ""), "used": bool(ai.get("model"))}   # never the model, never raw errors
    if job.get("status") == "queued":
        n = _q.ahead().get(job["id"])
        if n:
            out["stage"] = (f"High demand right now: {n} video{'s' if n != 1 else ''} ahead of yours" if _q.held else
                            f"Waiting in line: {n} video{'s' if n != 1 else ''} ahead of yours")
        elif n == 0 and _q.held:
            out["stage"] = "High demand right now: yours is next"
    return out


def _historical_phase_eta(job, now):
    phase = job.get("phase")
    started = job.get("phase_started")
    past = job.get("eta_history") or {}
    if started and phase in past and now - started < past[phase]:
        return max(1, round(past[phase] - (now - started)))
    return None


def _recent_phase_times(job):
    from statistics import median

    size = job.get("duration", 0)
    if not size:
        return {}
    with _lock:
        candidates = list(_by_user.get(job.get("user"), {}).values())[-50:]
    similar = [old for old in candidates if old.get("status") == "done"
               and old.get("source", {}).get("type") == job["source"]["type"]
               and 0.7 * size <= old.get("duration", 0) <= 1.4 * size
               and old["options"].get("mode") == job["options"].get("mode")]
    estimates = {}
    for phase in ("downloading", "transcribing", "analyzing", "rendering"):
        samples = [old["metrics"][phase] for old in similar if old.get("metrics", {}).get(phase, 0) > 0]
        if len(samples) >= 3:
            estimates[phase] = median(samples)
    return estimates


def estimate_remaining(job, now=None):
    """Estimate the measurable phase, or use the owner's recent similar jobs."""
    now = now or time.time()
    if job.get("status") != "running":
        return None
    if job.get("status") != "running" or job.get("phase") != "transcribing":
        if job.get("status") == "running" and job.get("phase") == "rendering":
            elapsed = job.get("render_seconds_elapsed", 0)
            remaining = sum(c.get("status") in ("queued", "rendering") for c in job["clips"])
            ready = sum(c.get("status") == "done" for c in job["clips"])
            if elapsed and ready:
                return max(1, round(elapsed * remaining / ready)) if remaining else None
        return _historical_phase_eta(job, now)
    progress = (job.get("progress", 0) - 0.14) / 0.78
    elapsed = now - (job.get("transcribe_started") or now)
    if not 0.15 <= progress < 0.95 or elapsed < 3:
        return _historical_phase_eta(job, now)
    # Extrapolate only the phase that is currently measurable.
    return min(86400, max(1, round(elapsed * (1 - progress) / progress)))


def set_chat(job, samples):
    with _lock:
        if get(job["id"]) is not job or busy(job) or job["kind"] != "video":
            raise JobConflict("Pause or stop the video before adding chat counts.")
        _persist_control(job, chat_samples=samples, done_blocks=0)
        return len(samples)


def logs(job):
    """Only stage labels and safe summaries, never the provider response or traceback."""
    events = list(job.get("events", []))
    if not events or events[-1]["message"] != job["stage"]:
        events.append({"at": job.get("updated_at") or job["created"], "message": job["stage"]})
    return events + ([{"at": time.time(), "message": job["error"]}] if job.get("error") else [])


def save(job, durable=False):
    ctx.bump(job.get("user"))
    with _lock:
        d = Path(job["dir"])
        for _ in range(5):
            try:
                text = json.dumps(job, indent=1)
                break
            except RuntimeError:  # changed mid-dump by another thread
                time.sleep(0.05)
        else:
            return
        tmp = d / "job.json.tmp"
        tmp.write_text(text, encoding="utf-8")
        if durable:
            with tmp.open("rb+") as f:
                os.fsync(f.fileno())
            tmp.replace(d / "job.json")
            _sync_dir(d)
        else:
            _put_in_place(tmp, d / "job.json")


def _sync_dir(directory):
    if os.name == "posix":
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _put_in_place(tmp: Path, target: Path):
    """Move the freshly written job.json over the old one.

    Windows refuses this ("Access is denied") for a moment whenever anything else has the old file open: a virus
    scanner, the search indexer, or another part of PeakCut reading it. A download saves many times a second, so it
    happens, and it used to stop the whole video. So it is tried again a few times, and if the file is still busy
    it is written in place; as a last resort this save is skipped, because the next one carries the same news."""
    for i in range(8):
        try:
            tmp.replace(target)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    try:
        target.write_text(tmp.read_text(encoding="utf-8"), encoding="utf-8")
        tmp.unlink(missing_ok=True)
    except OSError:
        pass


def _set(job, **kw):
    with _lock:
        work_control.check()
        if "stage" in kw and job.get("stage") != kw["stage"]:
            events = job.setdefault("events", [])
            events.append({"at": time.time(), "message": kw["stage"]})
            del events[:-30]
        if "phase" in kw and kw["phase"] != job.get("phase"):
            job["phase_started"] = time.time()
        job.update(kw, updated_at=time.time())
        save(job)


def _remap(path, folder: Path):
    """Paths saved before data moved into per-user folders (or on another machine): find the file by name."""
    if path and not Path(path).exists():
        cand = folder / Path(str(path).replace("\\", "/")).name
        if cand.exists():
            return str(cand)
    return path


def _load_file(f: Path, uid: str):
    try:
        job = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    job.update(dir=str(f.parent), user=uid)
    job.setdefault("kind", "video")
    job.setdefault("creator", None)
    job.setdefault("options", {}).setdefault("captions", True)
    job.setdefault("events", [])
    shutil.rmtree(f.parent / ".work", ignore_errors=True)
    if job.get("source_path"):
        job["source_path"] = _remap(job["source_path"], f.parent)
    if job.get("source", {}).get("path"):
        job["source"]["path"] = _remap(job["source"]["path"], f.parent)
    if job.get("status") == "uploading":
        job.update(status="error", phase="failed", stage="Upload interrupted. Upload it again.")
    if job.get("status") in ("queued", "running") and job.get("kind") != "live":
        # Picked up again as soon as the server is back (jobs.start), from the last finished part of the transcript.
        job.update(status="queued", stage="Picking up where it stopped", error=None, resume=True)
    if job.get("status") == "live":
        job.update(status="done", stage="Recording paused by a server update", was_live=True)
    buffered = job.get("kind") == "live" and any((f.parent / "buffer").glob("*.ts"))
    for c in job.get("clips", []):
        c.setdefault("captions", job["options"]["captions"])
        if c.get("src"):
            c["src"] = _remap(c["src"], f.parent / "clips")
        if c.get("status") in ("rendering", "queued"):
            if job.get("status") in INACTIVE:
                c.update(status="error" if job["status"] == "cancelled" else "queued")
            elif buffered and not c.get("src"):
                c["status"] = "queued"  # the stream is still on disk: live.recover() finishes these
                job["recover"] = True
            elif job.get("resume"):
                c["status"] = "queued"  # the video is being picked up again: this clip is made then
            elif job.get("kind") != "live" and (f.parent / "words.json").is_file():
                c["status"] = "queued"  # a single clip being re-made: jobs.start() makes it again
                job.setdefault("remake", []).append(c["id"])
            else:
                c.update(status="error", error="Interrupted by a restart. Open it and press Save and re-make.")
    _jobs[job["id"]] = job
    _by_user.setdefault(uid, {})[job["id"]] = job


def load_user(uid: str):
    d = config.USERS_DIR / uid
    for f in list((d / "jobs").glob("*/job.json")) + list((d / "creators").glob("*/*/*/job.json")):
        if not f.parent.name.startswith(".deleted-"):
            _load_file(f, uid)
    for deleted in list((d / "jobs").glob(".deleted-*")) + list((d / "creators").glob("*/*/.deleted-*")):
        if re.fullmatch(r"\.deleted-\d{8}-\d{6}-[a-f0-9]{10}", deleted.name):
            if deleted.resolve().is_relative_to(d.resolve()) and not deleted.is_symlink():
                shutil.rmtree(deleted, ignore_errors=True)


def load_all():
    for d in config.USERS_DIR.iterdir():
        if d.is_dir():
            load_user(d.name)


def all_jobs() -> list:
    """Every user's jobs. Only for background work; never send this to a browser."""
    with _lock:
        return list(_jobs.values())


def list_jobs():
    u = ctx.uid()
    with _lock:
        return sorted((dict(j) for j in _by_user.get(u, {}).values()), key=lambda j: -j["created"])


def get(jid):
    """The job, only if it belongs to whoever this code is running for."""
    job = _jobs.get(jid)
    return job if job and job["user"] == ctx.uid() else None


def busy_count() -> int:
    u = ctx.uid()
    return sum(1 for j in list(_by_user.get(u, {}).values()) if j["kind"] == "video" and j["status"] in BUSY)


def waiting_total() -> int:
    return sum(1 for j in list(_jobs.values()) if j["kind"] == "video" and j["status"] == "queued")


def queue_epoch() -> int:
    return _q.epoch


def running_now() -> int:
    return _q.running


def create(source: dict, options: dict, enqueue=True, creator=None, kind="video", uploading=False) -> dict:
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:10]
    c = creators.get(creator) if creator else None
    if c:
        d = creators.root(c["id"]) / ("live" if kind == "live" else "videos") / jid
        base = {**DEFAULTS, **{k: c[k] for k in ("max_clips", "min_len", "max_len", "style", "layout", "captions", "lang") if k in c},
                "caption_dictionary": c.get("caption_dictionary") or {},
                "hook_text": creators.clean_hook(c.get("hook_text")),
                "description_text": creators.clean_description(c.get("description_text"))}
    else:
        d, base, creator = config.user_dir(ctx.uid()) / "jobs" / jid, dict(DEFAULTS), None
    opts = {**base, **clean_options(options)}
    if opts["campaign"] and not campaigns.get(opts["campaign"]):
        raise ValueError("The campaign was not found.")
    if opts["template"]:
        opts["layout"] = render.TEMPLATES[opts["template"]]
    if opts["min_len"] >= opts["max_len"]:
        opts["min_len"] = max(5, opts["max_len"] - 5)
    job = {"id": jid, "user": ctx.uid(), "kind": kind, "creator": creator, "dir": str(d), "created": time.time(), "source": source,
           "options": opts, "status": "uploading" if uploading else "queued", "phase": "queued", "stage": "Waiting", "progress": 0.0,
           "title": source.get("name", ""), "uploader": "", "duration": 0, "clips": [], "error": None, "ai": {}}
    d.mkdir(parents=True)
    with _lock:
        _jobs[jid] = job
        _by_user.setdefault(job["user"], {})[jid] = job
    save(job)
    if enqueue:
        with _lock:
            _schedule(job)
    return job


def _schedule(job):
    ticket = object()
    _tickets[job["id"]] = ticket
    _q.put(("job", job["id"], ticket), user=job["user"], priority=job["options"].get("priority", 0))


def enqueue(job):
    with _lock:
        job.update(status="queued", phase="queued")
        save(job)
        _schedule(job)


def submit(fn):
    """Run any heavy task (a render, a cleanup) on the single worker, in order."""
    _q.put(("fn", ctx.bind(fn), None))


def submit_live(fn):
    """Live-stream renders and cleanups: their own worker, so a long video can never hold them up."""
    _live_q.put(ctx.bind(fn))


def _live_worker():
    while True:
        fn = _live_q.get()
        try:
            fn()
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def _owned_directory(job):
    folder = Path(job["dir"])
    user_root = config.USERS_DIR / job["user"]
    expected = user_root / "jobs" / job["id"]
    if job.get("creator"):
        expected = user_root / "creators" / job["creator"] / ("live" if job["kind"] == "live" else "videos") / job["id"]
    base = config.USERS_DIR.resolve()
    if (folder.absolute() != expected.absolute() or folder.resolve() != base / expected.relative_to(config.USERS_DIR)
            or not folder.resolve().is_relative_to(config.USERS_DIR.resolve())):
        raise JobConflict("The video folder could not be verified.")
    return folder


def _detach(job):
    """Atomically remove the directory from the set restart will consider."""
    folder = _owned_directory(job)
    tombstone = folder.with_name(".deleted-" + folder.name)
    if folder.exists():
        folder.rename(tombstone)
        _sync_dir(folder.parent)
    elif not tombstone.is_dir():
        raise JobConflict("The video folder is missing.")
    jid = job["id"]
    _tickets.pop(jid, None)
    _jobs.pop(jid, None)
    _by_user.get(job["user"], {}).pop(jid, None)
    ctx.bump(job["user"])
    return tombstone


def busy(job):
    with _lock:
        job = _jobs.get(job["id"], job)
        return (job["status"] in BUSY + ("live",) or job["id"] in _active
                or (job["status"] != "paused" and any(c.get("status") in ("queued", "rendering")
                                                     for c in job["clips"])))


def delete(jid, unfinished_upload=False):
    with _lock:
        job = get(jid)
        if not job:
            return False
        if busy(job) and not (unfinished_upload and job["status"] == "uploading"):
            raise JobConflict("This video is still being worked on.")
        deleted = _detach(job)
    shutil.rmtree(deleted, ignore_errors=True)
    return True


def _clean_interrupted(job, paused):
    d = _owned_directory(job)
    shutil.rmtree(d / ".work", ignore_errors=True)
    (d / "audio.wav").unlink(missing_ok=True)
    (d / "words.json.tmp").unlink(missing_ok=True)
    for parent in (d, d / "clips"):
        if parent.is_symlink():
            continue
        for pattern in ("*.wav", "*.part", "*.ytdl"):
            if pattern != "*.wav" and paused:  # yt-dlp can resume its partial downloads
                continue
            for temp in parent.glob(pattern):
                if parent != d or temp.name.startswith(("audio_src.", "source.")):
                    temp.unlink(missing_ok=True)
    for clip in job["clips"]:
        if clip["status"] in ("queued", "rendering"):
            if not clip.get("version") and not (d / "clips").is_symlink():
                cid = clip["id"]
                if re.fullmatch(r"[a-zA-Z0-9_-]+", cid):
                    for ext in ("mp4", "ass", "jpg"):
                        (d / "clips" / f"{cid}.{ext}").unlink(missing_ok=True)
            if paused:
                clip.update(status="queued")
            else:
                clip.update(status="error", error="Video processing was stopped.")


def _finish_stop(job):
    action = job.get("stop_action")
    if action == "delete":
        return _detach(job)
    paused = action == "pause"
    _clean_interrupted(job, paused)
    state = "paused" if paused else "cancelled"
    _persist_control(job, status=state, phase=state, stage="Paused" if paused else "Stopped",
                     stop_action=None, resume=False, remake=[])


def _persist_control(job, **changes):
    before = dict(job)
    job.update(changes, updated_at=time.time())
    try:
        save(job, durable=True)
    except OSError:
        job.clear()
        job.update(before)
        raise
    return None


def control(jid, action):
    removed = None
    with _lock:
        job = get(jid)
        if job is None:
            return None
        if job["kind"] != "video" or job["status"] == "uploading":
            raise JobConflict("This action is for videos that have finished uploading.")
        status = job["status"]
        if action in ("start", "resume", "retry"):
            allowed = {"start": ("paused",), "resume": ("paused",), "retry": ("cancelled", "error")}[action]
            if status in ("queued", "running"):
                return job
            if status not in allowed or jid in _active or busy(job):
                raise JobConflict("This video cannot be queued in its current state.")
            if busy_count() >= config.MAX_ACTIVE_JOBS_PER_USER:
                raise JobConflict("Wait for one of your current videos to finish.")
            _persist_control(job, status="queued", phase="queued", stage="Waiting", error=None,
                             resume=True, retry_errors=action == "retry")
            _schedule(job)
        elif action in ("pause", "stop", "stop-delete"):
            if status == "cancelling" and job.get("stop_action") == "delete":
                if jid in _active:
                    return job
                action = "stop-delete"
            if action == "pause" and not (status in ("queued", "running", "paused") or job.get("stop_action") == "pause"):
                raise JobConflict("Only a queued or active video can be paused.")
            if action == "pause" and status == "paused" or action == "stop" and status == "cancelled":
                return job
            if action == "stop" and status in ("done", "error"):
                raise JobConflict("This video is no longer in progress.")
            if status in ("done", "error", "cancelled") and busy(job):
                raise JobConflict("A clip is being re-made. Wait for it to finish.")
            _persist_control(job, status="cancelling", phase="cancelling",
                             stage="Pausing" if action == "pause" else "Stopping",
                             stop_action={"pause": "pause", "stop": "stop", "stop-delete": "delete"}[action])
            _tickets.pop(jid, None)
            _q.discard(jid, job["user"])
            worker = _active.get(jid)
            if worker:
                worker.stopped.set()
            else:
                removed = _finish_stop(job)
        else:
            raise ValueError("Unknown video action.")
    if removed:
        shutil.rmtree(removed, ignore_errors=True)
        return {"deleted": True}
    return job


def request_render(jid, cid, changes: dict):
    with _lock:
        _request_render(jid, cid, changes)


def _request_render(jid, cid, changes: dict):
    job = get(jid)
    if not job or job["status"] in BUSY + ("paused",):
        raise JobConflict("This video is still being worked on.")
    clip = next((c for c in job["clips"] if c["id"] == cid), None)
    if not clip or clip["status"] in ("queued", "rendering"):
        raise JobConflict("This clip is unavailable or already being re-made.")
    new = dict(clip)
    new.update(clean_options({k: changes[k] for k in ("captions", "mode", "aspect", "caption_mode", "safe_zone") if k in changes}))
    for k in ("start", "end"):
        if k in changes:
            new[k] = round(float(changes[k]), 2)
    if "src_offset" in clip:  # live clip: can only move inside the saved piece of stream
        new["start"] = max(new["start"], clip["src_offset"])
        new["end"] = min(new["end"], clip["src_end"])
    if not (math.isfinite(new["start"]) and math.isfinite(new["end"])) or new["start"] < 0:
        raise ValueError("Those times aren't valid.")
    if not 3 <= new["end"] - new["start"] <= 180:
        raise ValueError("A clip can be 3 to 180 seconds long.")
    for k in ("hook", "title", "description", "style", "layout"):
        if k in changes:
            new[k] = str(changes[k])[:{"hook": 60, "title": 100, "description": 2000}.get(k, 20)]
    if "hashtags" in changes:
        new["hashtags"] = clean_hashtags(changes["hashtags"])
    if new.get("style") not in render.STYLES:
        new["style"] = DEFAULTS["style"]
    if new.get("layout") not in render.LAYOUTS:
        new["layout"] = DEFAULTS["layout"]
    if "caption_words" in changes:
        new["caption_words"] = captions.edits(changes["caption_words"], new["start"], new["end"])
    if "caption_appearance" in changes:
        new["caption_appearance"] = captions.appearance(changes["caption_appearance"])
    clip.pop("reviewed", None)
    clip.pop("campaign_approval", None)
    new.pop("reviewed", None)
    new.pop("campaign_approval", None)
    clip.update(new, status="queued")
    save(job)
    _q.put(("clip", jid, cid))

def clean_hashtags(v) -> list:
    """Hashtags typed by a person ("#funny gaming") or sent by the page as a list: plain words, at most 8."""
    if isinstance(v, str):
        v = v.split()
    if not isinstance(v, list):
        raise ValueError("Hashtags must be words separated by spaces.")
    tags = [re.sub(r"\W", "", str(h))[:30] for h in v[:50] if isinstance(h, (str, int))]
    return [t for t in tags if t][:8]


def save_post_text(job, clip, changes: dict):
    """Change a clip's title, description and hashtags without making the clip again. Posting reads them
    from the clip when it uploads, so the next post (or one still waiting in the queue) uses the new text."""
    text = {k: str(changes[k] or "")[:{"title": 100, "description": 2000}[k]] for k in ("title", "description") if k in changes}
    if "hashtags" in changes:
        text["hashtags"] = clean_hashtags(changes["hashtags"])
    if clip.get("hashtags_in_description") and not clip.get("fixed_description") and text:
        # This clip's description ends with its hashtags, and posting does not add them again. So the
        # description is put back together from the Hashtags box: the words, then one line of tags.
        desc = hashtags._TAG_IN_TEXT.sub("", text.get("description", clip.get("description") or "")).strip()
        line = " ".join("#" + t for t in text.get("hashtags", clip.get("hashtags") or []))
        text["description"] = ((desc + "\n\n" + line).strip() if desc else line)[:2000]
    with _lock:
        clip.update(text)
    save(job)
    side = Path(job["dir"]) / "clips" / f"{clip['id']}.json"   # the copy that sits next to the video file
    try:
        if side.is_file():
            side.write_text(json.dumps({**json.loads(side.read_text(encoding="utf-8")), **text}, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass


def spoken_in(job, clip) -> str:
    """What is said during a clip, from the video's transcript."""
    try:
        words = json.loads((Path(job["dir"]) / "words.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return " ".join(str(w.get("w", "")) for w in words
                    if isinstance(w, dict) and clip["start"] - 0.5 <= _f(w.get("s")) <= clip["end"] + 0.5)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return -1.0


def render_one(job, clip, words):
    d = Path(job["dir"])
    clip["status"] = "rendering"
    save(job)
    try:
        if not clip.get("src") and not job.get("source_path"):
            _piece_again(job, clip)
        off = clip.get("src_offset", 0.0)
        src = Path(clip["src"]) if clip.get("src") else Path(job["source_path"])
        if not clip.get("src"):
            clip["end"] = min(clip["end"], job["duration"])
        local = dict(clip, start=clip["start"] - off, end=clip["end"] - off, hook=roman.to_roman(clip.get("hook") or ""))
        with _measure(job, "captions"):
            near = _caption_words(job, clip, words, off) if clip.get("captions", True) else []
        with _measure(job, "rendering"):
            out = render.render_clip(src, d / "clips", clip["id"], local, near, job["width"], job["height"])
        work_control.check()
        if out.get("qc", {}).get("status") == "failed":
            clip.update(out)
            raise RuntimeError(" ".join(out["qc"]["reasons"]))
        clip.pop("reviewed", None)
        clip.pop("watermark", None)
        clip.update(out, status="done", version=clip.get("version", 0) + 1, error=None)
        post = {k: clip[k] for k in ("title", "description", "hashtags", "hook", "start", "end", "score")}
        (d / "clips" / f"{clip['id']}.json").write_text(json.dumps(post, indent=1), encoding="utf-8")
        if clip["version"] == 1:
            clip.setdefault("made_at", time.time())
            creators.log(job["creator"], f"Clip ready (score {clip['score']}): {clip['title'] or clip['hook']}")
            save(job)
            for fn in on_clip_ready:
                try:
                    fn(job, clip)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
    except Exception as e:  # noqa: BLE001
        clip.update(status="error", error=safe.friendly(e, 400))
        creators.log(job["creator"], f"Clip failed: {safe.friendly(e, 200)}")
    save(job)


def _caption_words(job, clip, words, off):
    edited = clip.get("caption_words")
    if edited is not None:
        return [dict(w, s=w["s"] - off, e=w["e"] - off) for w in edited]
    if clip.get("caption_mode") == "original":
        spoken = [w for w in words if clip["start"] - 1 <= w["s"] <= clip["end"] + 1]
        entries = job["options"].get("caption_dictionary")
        if job.get("creator"):
            entries = (creators.get(job["creator"]) or {}).get("caption_dictionary", entries)
        if entries:
            spoken = captions.apply_dictionary(spoken, entries)
        return [dict(w, s=w["s"] - off, e=w["e"] - off) for w in spoken]
    span = [round(clip["start"], 2), round(clip["end"], 2), "en2"]      # "en2": captions made always-English
    if clip.get("fixed_for") == span and clip.get("fixed_words") and not any(
            roman.needs_roman(w.get("w", "")) for w in clip["fixed_words"]):
        raw = clip["fixed_words"]          # already tidied for this exact span: no second AI call
    else:
        raw = [w for w in words if clip["start"] - 1 <= w["s"] <= clip["end"] + 1]
        try:
            usage.set_context(job.get("creator"), job.get("title") or "")
            raw = brain.english_captions(raw, job.get("lang") or "", job.get("title") or "",
                                         youtube_english=lambda: _english_part(job, clip))
            clip.update(fixed_words=raw, fixed_for=span)
        except Exception:  # noqa: BLE001 - captions still render with the words as heard
            pass
    # the last word on it: whatever was said, nothing is drawn in Hindi or Urdu script
    return [dict(w, s=w["s"] - off, e=w["e"] - off) for w in roman.words(raw)]


CLIP_MINUTES = float(config.env("PEAKCUT_CLIP_MINUTES", "15"))     # the longest one clip may take to make


def _english_part(job, clip):
    """YouTube's English captions for just this clip, for a video in another language. Fetched once per video."""
    if (job.get("source") or {}).get("type") != "url":
        return None
    f = Path(job["dir"]) / "words_en.json"
    if f.is_file():
        en = json.loads(f.read_text(encoding="utf-8"))
    else:
        try:
            en = media.youtube_english_words(job["source"]["url"]) or []
        except Exception:  # noqa: BLE001 - YouTube often refuses its translations: the AI translates instead
            en = []
        f.write_text(json.dumps(en), encoding="utf-8")       # asked once per video, refused or not
    return [w for w in en if clip["start"] - 0.5 <= w["s"] <= clip["end"] + 0.5]


LONG_VIDEO = 40 * 60   # above this, fetch only the pieces that become clips
PAD = 8                # seconds kept either side of a fetched piece, so clips can be re-trimmed


def _piece_again(job, clip):
    """Remaking a clip whose own piece of the video was never fetched (its first try failed) and whose whole video
    is not here: fetch the piece now, cut exactly at the asked time, so no sound matching is needed."""
    src = job.get("source") or {}
    if src.get("type") != "url" or not src.get("url"):
        raise RuntimeError("The original video is no longer here, so this clip can't be made again. Add the video again.")
    d = Path(job["dir"])
    want = max(0.0, clip["start"] - PAD)
    until = clip["end"] + PAD if not job.get("duration") else min(job["duration"], clip["end"] + PAD)
    try:
        folder, stem, resolution = d / "clips", f"{clip['id']}.src", int(job["options"]["quality"])
        piece = download_cache.fetch(src["url"], folder, stem, [want, until, resolution, True],
                                     lambda: media.download_section(src["url"], folder, stem, want, until, resolution, exact=True))
        meta = media.probe(piece)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Could not fetch this part of the video: {safe.friendly(e, 250)}") from e
    clip.update(src=str(piece), src_offset=round(want, 2), src_end=round(want + meta["duration"], 2))
    clip["end"] = min(clip["end"], clip["src_end"])
    job.setdefault("width", meta["width"])
    job.setdefault("height", meta["height"])


def _fetch_piece(job, clip, full_audio):
    """Long videos: download just this clip's stretch and pin down exactly where it starts."""
    d = Path(job["dir"])
    o = job["options"]
    try:
        want = max(0.0, clip["start"] - PAD)
        until, resolution = min(job["duration"], clip["end"] + PAD), int(o["quality"])
        url, folder, stem = job["source"]["url"], d / "clips", f"{clip['id']}.src"
        piece = download_cache.fetch(url, folder, stem, [want, until, resolution, False],
                                     lambda: media.download_section(url, folder, stem, want, until, resolution))
        meta = media.probe(piece)
        part = media.decode_audio(piece, folder / f"{clip['id']}.wav", meta["duration"])
        off = media.find_offset(full_audio, part, want)
        clip.update(src=str(piece), src_offset=round(off, 2), src_end=round(off + meta["duration"], 2))
        clip["start"], clip["end"] = max(clip["start"], clip["src_offset"]), min(clip["end"], clip["src_end"])
        job.setdefault("width", meta["width"])
        job.setdefault("height", meta["height"])
    except Exception as e:  # noqa: BLE001
        clip.update(status="error", error=f"Could not fetch this part of the video: {safe.friendly(e, 250)}")


BLOCK = 10 * 60         # long videos are transcribed and clipped one block at a time
PROGRESSIVE_FROM = 20 * 60


def _covers(words: list, start: float, end: float) -> bool:
    """Is a borrowed transcript good enough to find clips with? It has to reach the end of the part being clipped
    and have a normal amount of talking in it; otherwise our own model writes the transcript."""
    inside = [w for w in words if start <= w["s"] <= end]
    minutes = max(0.5, (end - start) / 60)
    return len(inside) >= 25 * minutes and inside[-1]["s"] >= start + 0.85 * (end - start)


def _facecam(job) -> list | None:
    """Where the webcam is in the source: once for the whole video, for every clip of it.

    Only a sure answer is used. A box that is a scoreboard drawn over the game would
    put a picture of a scoreboard in the corner of the clip. One frame is also shown to
    the AI, which tells a person from a game character better than any measurement can;
    what it says is measured again on the picture, so it cannot do any harm."""
    path = job.get("source_path")
    if not path or not Path(path).is_file():
        return None
    try:
        box = facecam.detect(path, ask=brain.look_for_facecam)
    except Exception:  # noqa: BLE001 - a missing facecam is not a failure
        return None
    if not box or box.get("conf") != "high":
        return None
    job["facecam"] = [box["x"], box["y"], box["w"], box["h"]]
    return job["facecam"]


@contextmanager
def _executors(control):
    with ExitStack() as stack:
        def pool(*args, **kwargs):
            from concurrent.futures import ThreadPoolExecutor
            executor = ThreadPoolExecutor(*args, **kwargs)
            stack.callback(executor.shutdown, wait=True, cancel_futures=True)
            return executor

        try:
            yield pool
        except BaseException:
            control.stopped.set()
            raise


def _run_job(job):
    with _executors(work_control.current.get()) as pool:
        _process_job(job, pool)


def _process_job(job, pool):
    import math
    d = Path(job["dir"])
    o = job["options"]
    src = job["source"]
    # Which game this is: what the creator set, or what the title says. It decides the extra rules the AI
    # reads and the tags every post carries.
    game = job.get("game") or ""
    if not game and job.get("creator"):
        try:
            game = (creators.get(job["creator"]) or {}).get("game") or ""
        except Exception:  # noqa: BLE001
            game = ""
    if not game:
        game = games.detect(job["title"], src.get("name"))[0] or ""
    if game and job.get("game") != game:
        job["game"] = game
    t0 = time.time()
    job["processing_started"] = t0
    job.pop("transcribe_started", None)
    job.pop("eta_history", None)
    notes = []
    face = False       # where the webcam is in the source, found once; False means not looked for yet
    usage.set_context(job["creator"], job["title"] or src.get("name"))
    retry_errors = bool(job.pop("retry_errors", False))
    resuming = bool(job.pop("resume", False)) and (d / "words.json").is_file()
    if resuming:   # after a restart: keep the transcript parts, clips and AI answers already made, and carry on
        _set(job, status="running", phase="downloading", stage="Picking up where it stopped", error=None)
    else:
        _set(job, status="running", phase="downloading", stage="Getting the audio", progress=0.03, error=None, clips=[], ai={}, done_blocks=0)
    speech = o.get("lang") if fetch_model.known(o.get("lang")) else fetch_model.DEFAULT   # the language spoken
    could_borrow = src["type"] == "url" and config.YT_TRANSCRIPT and media.is_youtube(src["url"])
    if speech != fetch_model.DEFAULT and not transcribe.REMOTE and not could_borrow and fetch_model.status(speech) == "missing":
        fetch_model.ensure(speech, start=False)      # says what to do, before any time is spent on the download
    if src["type"] == "url":
        meta = download_cache.metadata(src["url"], media.info)
        if meta["kind"] != "video":
            raise RuntimeError("That link is a channel or playlist, not a single video. Paste the link of one video.")
        if ((o["range_end"] or meta["duration"]) - (o["range_start"] or 0)) > config.MAX_VIDEO_HOURS * 3600:
            raise RuntimeError(f"That is longer than {config.MAX_VIDEO_HOURS:g} hours. Choose a shorter part of it under Options.")
        if meta["is_live"]:
            raise RuntimeError("This link is a live stream, so it would never finish downloading. Open the creator, "
                               "put this link under Setup, and press Start recording instead.")
        _set(job, title=meta["title"], uploader=meta["uploader"], duration=meta["duration"])
        job["eta_history"] = _recent_phase_times(job)
        if not job.get("thumb") and media.save_thumb(d, url=src["url"], metadata=meta):
            _set(job, thumb=True)
        usage.set_context(job["creator"], meta["title"])
        span = (o["range_end"] or meta["duration"]) - (o["range_start"] or 0)
        pieces = meta["duration"] > LONG_VIDEO or meta["duration"] <= 0 or span < meta["duration"] / 2
        audio_file = None
        if not pieces:
            try:
                path = Path(job["source_path"]) if job.get("source_path") else None
                if not path or not path.is_file():
                    with _measure(job, "downloading"):
                        path = download_cache.fetch(src["url"], d, "source", [o["quality"]], lambda:
                            media.download(src["url"], d, int(o["quality"]),
                                           lambda p: _set(job, progress=max(job["progress"], 0.03 + 0.1 * p))))
                video_meta = media.probe(path)
                _set(job, source_path=str(path), width=video_meta["width"], height=video_meta["height"])
                audio_file = path
            except Exception as e:
                notes.append(f"Full video unavailable. Trying audio and clip sections. {safe.friendly(e, 120)}")
                pieces = True
        if not audio_file:
            with _measure(job, "downloading"):
                audio_file = download_cache.fetch(src["url"], d, "audio_src", [], lambda:
                    media.download_audio(src["url"], d, lambda p: _set(job, progress=max(job["progress"], 0.03 + 0.1 * p))))
    else:
        pieces, audio_file = False, Path(src["path"])
        info = media.probe(audio_file)
        if info["duration"] > config.MAX_VIDEO_HOURS * 3600:
            raise RuntimeError(f"That video is longer than {config.MAX_VIDEO_HOURS:g} hours, which is the limit.")
        _set(job, source_path=str(audio_file), width=info["width"], height=info["height"], duration=info["duration"])
        job["eta_history"] = _recent_phase_times(job)
        if not job.get("thumb") and media.save_thumb(d, video=audio_file, duration=info["duration"]):
            _set(job, thumb=True)
    with _measure(job, "audio"):
        audio = media.decode_audio(audio_file, d / "audio.wav", job["duration"])

    if not job["duration"]:
        job["duration"] = len(audio) / 16000
    lo = int((o["range_start"] or 0) * 16000)
    hi = min(len(audio), int(o["range_end"] * 16000)) if o["range_end"] else len(audio)
    if hi <= lo:
        raise ValueError("The selected time range has no audio.")
    span = (hi - lo) / 16000
    db = media.loudness_per_second(audio[lo:hi])

    parent_control = work_control.current.get()
    exact = {}      # clip id -> (from, to, words): our own model's words for just that clip

    def make(clip, words_now):
        control = work_control.Control(parent_control.work_dir, parent=parent_control,
                                       deadline=time.monotonic() + CLIP_MINUTES * 60)
        with work_control.use(control):
            try:
                _make(clip, words_now)
            except TimeoutError as e:
                clip.update(status="error", error=safe.friendly(e))
                save(job)

    def _make(clip, words_now):
        if src["type"] == "url" and pieces and not (clip.get("src") and Path(clip["src"]).is_file()):
            _fetch_piece(job, clip, audio)
        if clip["status"] != "error" and clip.get("captions", True) and yt_model:
            # The clip was found with YouTube's transcript. What is shown on screen comes from our own model,
            # run on just this clip: full punctuation, nothing blanked out, timings to the word.
            a0, b0 = max(lo, int((clip["start"] - 1.5) * 16000)), min(hi, int((clip["end"] + 1.5) * 16000))
            try:
                tw = [dict(w, s=round(w["s"] + a0 / 16000, 2), e=round(w["e"] + a0 / 16000, 2))
                      for w in transcribe.transcribe(audio[a0:b0], lang=yt_model)]
            except Exception:  # noqa: BLE001 - the clip is still made, with YouTube's words
                tw = []
            if len(tw) >= 3:
                exact[clip["id"]] = (a0 / 16000, b0 / 16000, tw)
                words_now = tw
        if clip["status"] != "error":
            render_one(job, clip, words_now)

    renders = pool(config.CLIP_WORKERS)  # more clips made at once on a bigger machine
    futures = []
    step = BLOCK * 16000 if span > PROGRESSIVE_FROM else hi - lo
    blocks = [(x, min(hi, x + step)) for x in range(lo, hi, step)] or [(lo, hi)]
    words, tokens, model = [], 0, None
    done_blocks = 0
    yt = None
    if src["type"] == "url" and config.YT_TRANSCRIPT and media.is_youtube(src["url"]):
        try:
            yt = media.youtube_words(src["url"])
        except Exception:  # noqa: BLE001 - no transcript to borrow: ours is written as usual
            yt = None
        if yt and not _covers(yt[0], lo / 16000, hi / 16000):
            yt = None
        job["transcript_from"] = "youtube" if yt else "own"
    # The language spoken decides the captions: YouTube's word for it when its transcript is used, else the choice made.
    job["lang"] = yt[1] if yt else speech
    # A clip found with YouTube's transcript gets its on-screen words from our own model, when there is one here
    # for the language YouTube says was spoken (English always; others once they are downloaded).
    yt_code = (yt[1].lower().split("-")[0] if yt else "")
    yt_model = yt_code if fetch_model.known(yt_code) and (yt_code == fetch_model.DEFAULT or fetch_model.ready(yt_code)) else ""
    if not yt and done_blocks == 0 and speech != fetch_model.DEFAULT and not transcribe.REMOTE and not fetch_model.ready(speech):
        if fetch_model.state[speech]["doing"]:
            _set(job, stage=f"Waiting for the {fetch_model.label(speech)} speech model to finish downloading")
        fetch_model.ensure(speech, start=False)      # waits for a download already running, else says what to do
    if resuming:
        try:
            words = json.loads((d / "words.json").read_text(encoding="utf-8"))
        except ValueError:
            words = []
        done_blocks = min(int(job.get("done_blocks", 0)), len(blocks)) if words else 0
        tokens, model = int((job.get("ai") or {}).get("tokens", 0)), (job.get("ai") or {}).get("model")
        if done_blocks < len(blocks):     # words past the last finished part are transcribed again
            cut = blocks[done_blocks][0] / 16000
            words = [w for w in words if w["s"] < cut]
        for c in job["clips"]:            # clips found before the restart that were not finished yet
            if c.get("status") != "done" and (c.get("status") != "error" or retry_errors):
                c.pop("error", None)
                c["status"] = "queued"
                futures.append((renders.submit(work_control.bind(make), c, list(words)), c))
        save(job)
    # The transcript of the next part is written while the AI is still reading this one (the AI is a wait on the
    # network, the transcript is work on this machine), so neither waits for the other.
    ahead = pool(2, thread_name_prefix="ahead")
    heard, now_on = {}, [done_blocks]
    # Audio identity works across remuxed copies while keeping the cached word time base.
    source_hash = cache.samples_fingerprint(audio) if not transcribe.REMOTE else None
    if src["type"] == "url" and audio_file.name.startswith("audio_src."):
        audio_file.unlink(missing_ok=True)
    if source_hash:
        job["content_fingerprint"] = source_hash
    if "experiment" not in job:
        job["experiment"] = learning.assign(source_hash or src.get("url")) if o.get("experiment") else None

    def hear_block(a, b, progress):
        key = [source_hash, speech, fetch_model.MODELS[speech][1], fetch_model.MODELS[speech][5], a, b]
        if source_hash:
            saved = cache.read("transcript", key)
            if isinstance(saved, list):
                progress(1.0)
                return saved
        with _measure(job, "transcribing"):
            result = transcribe.transcribe(audio[a:b], progress, speech)
        if source_hash:
            cache.write("transcript", key, result)
        return result

    def hear(i):
        if i < len(blocks) and i >= done_blocks and i not in heard:
            a_, b_ = blocks[i]
            last = [0.0]

            def tp(p, i=i, a_=a_, b_=b_):
                if now_on[0] == i and p - last[0] > 0.05:      # only the part being shown moves the bar
                    last[0] = p
                    _set(job, progress=max(job["progress"], 0.14 + 0.78 * ((a_ - lo) + p * (b_ - a_)) / max(1, hi - lo)))
            heard[i] = ahead.submit(work_control.bind(hear_block), a_, b_, tp)

    # The ad breaks and static screens of the whole video, found once. It reads the video end to end,
    # so it is done here rather than for each clip, and only when asked for.
    statics = quality.static_ranges(job.get("source_path") or "") if config.SKIP_STATIC else []
    for bi, (a, b) in enumerate(blocks):
        if bi < done_blocks:
            continue                      # transcribed and checked by the AI before the restart
        shift = a / 16000
        where = f" (minute {round((a - lo) / 16000 / 60)} of {round(span / 60)})" if len(blocks) > 1 else ""
        if yt:
            _set(job, phase="transcribing", stage="Reading the transcript" + where)
            bw = [dict(w, s=round(w["s"] - shift, 2), e=round(w["e"] - shift, 2)) for w in yt[0] if a / 16000 <= w["s"] < b / 16000]
        else:
            _set(job, phase="transcribing", stage="Writing the transcript" + where,
                 transcribe_started=job.get("transcribe_started") or time.time())
            now_on[0] = bi
            hear(bi)
            hear(bi + 1)
            bw = heard.pop(bi).result()
        _set(job, progress=max(job["progress"], 0.14 + 0.78 * (b - lo) / max(1, hi - lo)))
        words += [dict(w, s=round(w["s"] + shift, 2), e=round(w["e"] + shift, 2)) for w in bw]
        _save_words(d, words)
        _set(job, phase="analyzing", stage="Finding the best moments" + where, word_count=len(words))
        left = int(o["max_clips"]) - len(job["clips"])
        if left <= 0 or len(bw) < 8:
            job["done_blocks"] = bi + 1
            save(job)
            continue
        quota = max(1, math.ceil(left / (len(blocks) - bi)))
        s0 = int((a - lo) / 16000)
        levels = db[s0:s0 + int((b - a) / 16000) + 1]
        # Opt-in, low-resolution visual triage. Only inspect an already-local source,
        # and at most this 10-minute block. Audio-only long URL jobs remain supported.
        motion = []
        if o.get("visual_triage"):
            video = Path(job["source_path"]) if job.get("source_path") and not pieces else None
            if video and video.is_file():
                with _measure(job, "visual"):
                    motion = quality.sampled_motion_map(video, start=a / 16000, seconds=(b - a) / 16000)
                if not motion and bi == 0:
                    notes.append("Visual motion samples were unavailable. Ranking used speech and audio.")
            elif bi == 0:
                notes.append("Visual motion scan needs a locally available video; this audio-only job used speech and audio.")
        profile = ai.current()
        analysis_key = cache.digest(bw, levels.tobytes(), (b - a) / 16000, job["title"], job["uploader"],
                                    quota, o["min_len"], o["max_len"], game, profile["provider"], profile["models"],
                                    ai.base_url(), brain.RANK_VERSION, (job.get("experiment") or {}).get("version") or learning.active()[1],
                                    job.get("chat_samples"), bool(o.get("visual_triage")), motion)
        saved = cache.read("analysis", analysis_key) if config.ai_ready() else None
        if saved:
            found, analysis = saved
            notes.append("Reused the previous analysis.")
        else:
            with _measure(job, "analyzing"):
                found, analysis = brain.find_clips(bw, levels, (b - a) / 16000, job["title"], job["uploader"],
                                                   quota, int(o["min_len"]), int(o["max_len"]), notes.append, game=game,
                                                   chat=reactions.signal(job.get("chat_samples"), shift), ranking=job.get("experiment"),
                                                   motion=motion)
            if analysis.get("model") and not analysis.get("partial"):
                cache.write("analysis", analysis_key, [found, analysis])
            tokens += analysis.get("tokens", 0)
        model = analysis.get("model") or model
        if analysis.get("note"):
            notes.append(analysis["note"])
        if found and statics:
            # A commercial or a "be right back" slate is not a clip. Any clip that sits mostly inside one
            # is dropped.
            keep = []
            for c in found:
                a_s, b_s = c["start"] + shift, c["end"] + shift
                inside = quality.static_overlap(a_s, b_s, statics)
                if inside > 0.5 * max(1.0, b_s - a_s):
                    notes.append(f"A clip from {round(a_s)}s was left out: it sat in an ad break or a static screen.")
                else:
                    keep.append(c)
            found = keep
        prepared = []
        next_index = 1 + max((int(c["id"][4:]) for c in job["clips"]
                              if c["id"].startswith("clip") and c["id"][4:].isdigit()), default=0)
        for c in found:
            start, end = round(c["start"] + shift, 2), round(c["end"] + shift, 2)
            if brain._overlaps(start, end, job["clips"]):
                continue
            c.update(id=f"clip{next_index + len(prepared):02d}", start=round(c["start"] + shift, 2),
                     end=round(c["end"] + shift, 2), style=o["style"], layout=o["layout"],
                     captions=o.get("captions", True), mode=o.get("mode", "balanced"), aspect=o.get("aspect", "9:16"),
                     caption_mode=o.get("caption_mode", "english"), safe_zone=o.get("safe_zone", "none"), status="queued", version=0)
            if o.get("hook_text"):        # the person's own big text, on every clip, in place of the AI's
                c["hook"] = o["hook_text"]
            if o.get("description_text"):  # ...and their own description, exactly as written
                c["description"], c["fixed_description"] = o["description_text"], True
            else:                          # no description of their own: 3 to 5 fitting hashtags go at the end of the AI's
                hashtags.apply(c, words, job["title"], job["uploader"], game)
            # Only the gameplay layout uses a webcam, so it is only looked for then, and only once: a
            # stream with no webcam is not looked for again on every clip of it.
            if o["layout"] == "gameplay" and face is False:
                face = _facecam(job)
            if face:
                c["facecam"] = face
            prepared.append(c)
        job["ai"] = {"model": model, "tokens": tokens, "note": "", "log": notes[-5:]}
        with _lock:
            work_control.check()
            job["clips"].extend(prepared)
            job["done_blocks"] = bi + 1
            save(job)
        for c in prepared:
            futures.append((renders.submit(work_control.bind(make), c, list(words)), c))
    if futures:
        _set(job, phase="rendering", stage="Finishing the clips", progress=max(job["progress"], 0.92))
    for future, _clip in futures:
        future.result()
        work_control.check()
    if exact:           # keep our own words for the clips in the saved transcript, so editing a clip later uses them
        for a_s, b_s, tw in exact.values():
            words = [w for w in words if not a_s <= w["s"] < b_s] + tw
        words.sort(key=lambda w: w["s"])
        _save_words(d, words)
    del audio
    clips = job["clips"]
    clips.sort(key=lambda clip: (-clip.get("score", 0), clip["start"]))
    job["ai"] = {"model": model, "tokens": tokens, "log": notes[-5:],
                 "note": "The AI was unavailable, so clips were picked by loudness only." if clips and not model else ""}
    done = sum(c.get("status") == "done" for c in clips)
    if clips and not done:
        _set(job, status="error", phase="failed", stage="Exports did not finish", progress=1.0,
             error="No clip passed the export checks. Open a clip for details or retry the video.", took=round(time.time() - t0))
        from . import alerts
        alerts.job_message("The video finished without a usable clip.")
        return
    stage = "Done" if done else "Done, but nothing clip-worthy was found"
    _set(job, status="done", phase="completed", stage=stage, progress=1.0, took=round(time.time() - t0))
    try:
        from . import alerts, notices
        name = job.get("title") or job["source"].get("name") or "your video"
        notices.add(job.get("user"), "clips", f"{len(clips)} clip{'s' if len(clips) != 1 else ''} ready" if clips else "Nothing clip-worthy found",
                    name, f"/app/creators/{job['creator']}" if job.get("creator") else "/app/videos")
        ready_count = sum(c.get("status") == "done" for c in clips)
        alerts.job_message(f"Video processing finished. {ready_count} clip(s) are available for review.")
    except Exception:  # noqa: BLE001
        pass
    creators.log(job["creator"], f"Video finished with {len(clips)} clips in {round(time.time() - t0)}s: {job['title']}")


@contextmanager
def _measure(job, stage):
    began = time.monotonic()
    try:
        yield
    finally:
        with _lock:
            metrics = job.setdefault("metrics", {})
            metrics[stage] = round(metrics.get(stage, 0) + time.monotonic() - began, 3)
            if stage == "rendering":
                job["render_seconds_elapsed"] = metrics[stage]


def _save_words(d, words):
    path = d / "words.json.tmp"
    path.write_text(json.dumps(words), encoding="utf-8")
    path.replace(d / "words.json")


def _worker(quick_only=False):
    while True:
        kind, a, cid = _q.get(quick_only)
        if kind == "fn":
            try:
                a()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            continue
        with _lock:
            job = _jobs.get(a)
            control = None
            if kind == "job":
                if job and job["status"] == "queued" and _tickets.get(a) is cid:
                    _tickets.pop(a, None)
                    control = _active[a] = work_control.Control(
                        Path(job["dir"]) / ".work", reserve_bytes=int(load.MIN_FREE_DISK_GB * 1e9))
                else:
                    job = None
        if not job:
            if kind == "job":
                _q.done()
            continue
        removed = None
        clip = None
        with ctx.as_user(job["user"]):
            try:
                with work_control.use(control):
                    if kind == "job":
                        _run_job(job)
                    else:
                        clip = next(c for c in job["clips"] if c["id"] == cid)
                        words = []
                        if clip.get("captions", True):
                            words = json.loads((Path(job["dir"]) / "words.json").read_text(encoding="utf-8"))
                        render_one(job, clip, words)
            except work_control.Interrupted:
                pass
            except Exception as e:  # noqa: BLE001
                with _lock:
                    if job["status"] != "cancelling" and kind == "job":
                        message = safe.friendly(e)
                        for clip in job["clips"]:
                            if clip.get("status") in ("queued", "rendering"):
                                clip.update(status="error", error="The video stopped before this clip was made.")
                        job.update(status="error", phase="failed", error=message, stage="Failed")
                        try:
                            save(job)
                        except OSError:
                            ctx.bump(job["user"])
                        from . import alerts
                        alerts.job_message("Video processing failed. Open Jobs to see the error.")
                    elif kind == "clip" and clip is not None:
                        clip.update(status="error", error=safe.friendly(e))
                        try:
                            save(job)
                        except OSError:
                            ctx.bump(job["user"])
            finally:
                if kind == "job":
                    with _lock:
                        _active.pop(a, None)
                        if job.get("status") == "cancelling":
                            try:
                                removed = _finish_stop(job)
                            except (OSError, JobConflict):
                                job.update(error="Could not finish stopping the video. Check storage and retry.")
                                ctx.bump(job["user"])
                    _q.done()
        if removed:
            shutil.rmtree(removed, ignore_errors=True)


def _pick_up_after_restart():
    """Videos and single clips that were being made when the server stopped: queued again, oldest first."""
    waiting = sorted((j for j in all_jobs() if j.get("status") == "queued" and j.get("resume")), key=lambda j: j["created"])
    for j in waiting:
        _schedule(j)
    for j in all_jobs():
        if j["status"] == "cancelling":
            try:
                removed = _finish_stop(j)
                if removed:
                    shutil.rmtree(removed, ignore_errors=True)
            except (OSError, JobConflict):
                j["error"] = "Could not finish stopping the video. Check storage and retry."
            continue
        for cid in j.pop("remake", []):
            _q.put(("clip", j["id"], cid))
        if j.get("status") == "done":
            save(j)


def workers_alive():
    return bool(_workers) and all(worker.is_alive() for worker in _workers)


def start():
    load.start()
    load_all()
    _pick_up_after_restart()
    for _ in range(config.JOB_WORKERS):
        worker = threading.Thread(target=_worker, daemon=True, name="video-worker")
        _workers.append(worker)
        worker.start()
    threading.Thread(target=_worker, args=(True,), daemon=True).start()   # re-making a single clip never waits behind whole videos
    threading.Thread(target=_live_worker, daemon=True).start()
