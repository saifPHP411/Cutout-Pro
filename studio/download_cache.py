"""Private, expiring copies of fully downloaded VOD streams."""
import contextlib
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from . import cache, config, ctx, work_control

LOCKS = [threading.Lock() for _ in range(32)]


def _directory():
    return config.user_dir(ctx.uid()) / "download-cache"


def _limit():
    return max(0, int(config.env("PEAKCUT_DOWNLOAD_CACHE_MB", "2048"))) * 1024**2


def _ttl():
    return max(1, int(config.env("PEAKCUT_DOWNLOAD_CACHE_HOURS", "24"))) * 3600


@contextlib.contextmanager
def _locked(key):
    lock = LOCKS[int(key[:8], 16) % len(LOCKS)]
    while not lock.acquire(timeout=0.15):
        work_control.check()
    try:
        work_control.check()
        yield
    finally:
        lock.release()


def _copy(src, dst):
    dst.unlink(missing_ok=True)
    try:
        with src.open("rb") as reader, dst.open("xb") as writer:
            if os.name == "posix":
                import fcntl
                try:
                    fcntl.ioctl(writer.fileno(), 0x40049409, reader.fileno())  # Linux FICLONE
                    return
                except OSError:
                    pass
            while chunk := reader.read(1024 * 1024):
                work_control.check()
                writer.write(chunk)
    except BaseException:
        dst.unlink(missing_ok=True)
        raise


def metadata(url, fetch):
    key = ["vod-info-v1", url]
    prior = cache.read("media-info", key)
    if prior and time.time() - prior["time"] < _ttl():
        return prior["info"]
    info = fetch(url)
    if info.get("kind") == "video" and info.get("duration", 0) > 0 and not info.get("is_live"):
        cache.write("media-info", key, {"time": time.time(), "info": info})
    return info


def fetch(url, folder, kind, options, loader):
    """Materialize a private copy under the job, so edits and deletes cannot affect the cache."""
    ceiling = _limit()
    if not ceiling:
        return loader()
    root = _directory()
    stream_kind = "section" if kind.endswith(".src") else kind
    key = cache.digest("vod-v1", url, stream_kind, options)
    with _locked(cache.digest(ctx.uid())):
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        entries = []
        now = time.time()
        for path in root.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            if now - stat.st_mtime >= _ttl() or path.name.startswith("pending-"):
                path.unlink(missing_ok=True)
            else:
                entries.append((stat.st_mtime, stat.st_size, path))
        for _, _, old in entries:
            if old.stem == key:
                folder.mkdir(parents=True, exist_ok=True)
                new = folder / f"{kind}{old.suffix}"
                _copy(old, new)
                return new
        saved = loader()
        size = saved.stat().st_size
        if size > ceiling or shutil.disk_usage(root).free < size + 512 * 1024**2:
            return saved
        remaining = sum(row[1] for row in entries)
        try:
            for _, length, path in sorted(entries):
                if remaining + size <= ceiling:
                    break
                path.unlink(missing_ok=True)
                remaining -= length
            with tempfile.NamedTemporaryFile(prefix="pending-", dir=root, delete=False) as stream:
                pending = Path(stream.name)
            try:
                _copy(saved, pending)
                pending.replace(root / (key + saved.suffix.lower()))
            finally:
                pending.unlink(missing_ok=True)
        except OSError:
            pass
        return saved
