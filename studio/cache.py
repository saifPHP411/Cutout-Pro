"""Content-addressed JSON results, kept within the owner's data directory."""
import hashlib
import json
import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path

from . import config, ctx, work_control

VERSION = "1"
TTL = 14 * 86400
MAX_BYTES = 64 * 1024 * 1024
MAX_ENTRY = 8 * 1024 * 1024


def digest(*parts):
    h = hashlib.sha256()
    for part in parts:
        raw = part if isinstance(part, bytes) else json.dumps(part, sort_keys=True, separators=(",", ":")).encode()
        h.update(len(raw).to_bytes(8, "big"))
        h.update(raw)
    return h.hexdigest()


def fingerprint(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(2 * 1024 * 1024):
            work_control.check()
            h.update(chunk)
    return h.hexdigest()



def samples_fingerprint(samples):
    """Hash normalized PCM without reopening the compressed video or duplicating PCM."""
    h = hashlib.sha256()
    raw = memoryview(samples).cast("B")
    for offset in range(0, len(raw), 2 * 1024 * 1024):
        work_control.check()
        h.update(raw[offset:offset + 2 * 1024 * 1024])
    return h.hexdigest()

def _file(kind, key):
    return config.user_dir(ctx.uid()) / "cache" / f"{kind}-{digest(VERSION, key)}.json"


def read(kind, key):
    path = _file(kind, key)
    try:
        with path.open("rb") as f:
            stat = os.fstat(f.fileno())
            if stat.st_size > MAX_ENTRY or time.time() - stat.st_mtime > TTL:
                return None
            raw = f.read(MAX_ENTRY + 1)
        return json.loads(raw) if len(raw) <= MAX_ENTRY else None
    except (OSError, ValueError):
        return None


def write(kind, key, data):
    path = _file(kind, key)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > MAX_ENTRY:
        return
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        f = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".result-", delete=False)
        temp = Path(f.name)
        try:
            with f:
                f.write(raw)
            work_control.check()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        _prune(path.parent)
    except OSError:
        pass  # disk pressure must not turn an otherwise valid result into a failure


def _prune(root):
    entries = []
    for path in root.glob("*.json"):
        with suppress(OSError):
            stat = path.stat()
            entries.append((stat.st_mtime, stat.st_size, path))
    size = sum(n for _, n, _ in entries)
    for stamp, n, path in sorted(entries):
        if size <= MAX_BYTES and time.time() - stamp <= TTL:
            break
        with suppress(OSError):
            path.unlink(missing_ok=True)
            size -= n
