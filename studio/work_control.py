"""Cancellation shared by the workers of one video."""
import asyncio
import contextvars
import os
import signal
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager


class Interrupted(BaseException):
    """A user stop must pass through ordinary recovery and retry handlers."""


class Control:
    def __init__(self, work_dir=None, parent=None, deadline=None, reserve_bytes=0):
        self.stopped = threading.Event()
        self.work_dir = work_dir
        self.parent = parent
        self.deadline = deadline
        self.reserve_bytes = reserve_bytes
        self.disk_checked = 0.0

    def check(self):
        if self.parent:
            self.parent.check()
        if self.stopped.is_set():
            raise Interrupted()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError("This clip took too long. Open it and press Save and re-make to try again.")
        if self.reserve_bytes and time.monotonic() - self.disk_checked >= 1:
            if shutil.disk_usage(self.work_dir.parent).free < self.reserve_bytes:
                raise OSError("Storage reserve reached. Free up space and retry the video.")
            self.disk_checked = time.monotonic()


current = contextvars.ContextVar("video_control", default=None)


@contextmanager
def use(control):
    marker = current.set(control)
    try:
        yield
    finally:
        current.reset(marker)


def check():
    control = current.get()
    if control:
        control.check()


def sleep(seconds):
    stop = time.monotonic() + seconds
    control = current.get()
    if not control:
        time.sleep(seconds)
        return
    while (left := stop - time.monotonic()) > 0:
        check()
        control.stopped.wait(min(0.15, left))
    check()


def bind(fn):
    context = contextvars.copy_context()

    def call(*args, **kwargs):
        return context.copy().run(fn, *args, **kwargs)

    return call


def _terminate(proc):
    if os.name == "posix":
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            if sig == signal.SIGTERM:
                try:
                    proc.communicate(timeout=0.35)
                except subprocess.TimeoutExpired:
                    pass
    else:
        # yt-dlp may start an encoder. Windows needs an explicit tree termination.
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if proc.poll() is None:
            proc.kill()
    proc.communicate()


def run(args, *, timeout=None, input=None, capture_output=False, on_poll=None, **kwargs):
    if current.get() is None:
        return subprocess.run(args, timeout=timeout, input=input, capture_output=capture_output, **kwargs)
    check()
    if os.name == "posix":
        kwargs["start_new_session"] = True
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if input is not None:
        kwargs["stdin"] = subprocess.PIPE
    deadline = time.monotonic() + timeout if timeout else float("inf")
    with subprocess.Popen(args, **kwargs) as proc:
        try:
            while True:
                check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                try:
                    stdout, stderr = proc.communicate(input=input, timeout=min(0.15, remaining))
                except subprocess.TimeoutExpired:
                    input = None   # communicate() remembers the original bytes
                    if on_poll:
                        on_poll()
                    continue
                check()
                return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
        except BaseException:
            _terminate(proc)
            raise


def request(method, url, **kwargs):
    import httpx

    check()
    if current.get() is None:
        # Preserve the existing mockable post entry point outside video workers.
        return httpx.post(url, **kwargs) if method == "POST" else httpx.request(method, url, **kwargs)

    async def send():
        timeout = kwargs.pop("timeout", None)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = asyncio.create_task(client.request(method, url, **kwargs))
            try:
                while not response.done():
                    check()
                    await asyncio.wait({response}, timeout=0.15)
                check()
                return response.result()
            finally:
                if not response.done():
                    response.cancel()
                    await asyncio.gather(response, return_exceptions=True)

    return asyncio.run(send())
