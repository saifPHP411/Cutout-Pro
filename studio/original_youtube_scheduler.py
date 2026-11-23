"""Opt-in daily original-science Shorts scheduler for the existing PeakCut server.

No paid service, no external cron dependency. It remains completely dormant
until the channel owner explicitly enables PEAKCUT_ORIGINAL_AUTOPOST=1 and
sets the owner, YouTube channel and voice paths.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_started = False
_lock = threading.Lock()
_ZONE = ZoneInfo("Asia/Amman")


def _publish_due(now=None, hour=11) -> bool:
    now = now or datetime.now(_ZONE)
    return now.hour >= hour


def start() -> bool:
    """Start a daemon only with an explicit, fully configured opt-in."""
    global _started
    if os.environ.get("PEAKCUT_ORIGINAL_AUTOPOST") != "1":
        return False
    required = ("PEAKCUT_ORIGINAL_USER_ID", "PEAKCUT_ORIGINAL_YOUTUBE_ACCOUNT",
                "PEAKCUT_ORIGINAL_VOICE_MODEL", "PEAKCUT_ORIGINAL_VOICE_LICENSE_APPROVED")
    if not all(os.environ.get(k) for k in required):
        print("[original-youtube] disabled: configuration incomplete", flush=True)
        return False
    if os.environ.get("PEAKCUT_ORIGINAL_VOICE_LICENSE_APPROVED") != "1":
        return False
    with _lock:
        if _started:
            return True
        _started = True
        threading.Thread(target=_loop, daemon=True, name="original-youtube-autopilot").start()
        return True


def _loop():
    from tools.original_youtube_autopilot import publish_once

    last_checked = ""
    try:
        hour = int(os.environ.get("PEAKCUT_ORIGINAL_PUBLISH_HOUR_AMMAN", "11"))
    except ValueError:
        hour = 11
    if not 0 <= hour <= 23:
        hour = 11
    while True:
        now = datetime.now(_ZONE)
        day = now.date().isoformat()
        if _publish_due(now, hour) and day != last_checked:
            last_checked = day
            try:
                result = publish_once(Path(os.environ["PEAKCUT_ORIGINAL_VOICE_MODEL"]))
                status = result.get("status")
                print(f"[original-youtube] {status}", flush=True)
            except Exception:
                # Fail closed; do not keep retrying an upload which may have
                # reached YouTube before a network error.
                print("[original-youtube] failed; inspect YouTube Studio and the local upload ledger", flush=True)
        time.sleep(300)
