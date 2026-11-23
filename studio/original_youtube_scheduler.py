"""Autonomous daily YouTube scheduler for opt-in PeakCut channel owners.

Can run with the legacy explicit environment configuration OR a consenting
channel owner who enabled publishing through the authenticated app. All
background work is inactive when nobody opted in. Duplicate uploads are
blocked by the per-channel ledger + file lock inside publish_once.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import original_voice, original_youtube_settings

_started = False
_lock = threading.Lock()
_ZONE = ZoneInfo("Asia/Amman")


def _publish_due(now=None, hour=11) -> bool:
    now = now or datetime.now(_ZONE)
    return now.hour >= hour


def _legacy_config() -> tuple[str, int] | None:
    if os.environ.get("PEAKCUT_ORIGINAL_AUTOPOST") != "1":
        return None
    uid = os.environ.get("PEAKCUT_ORIGINAL_USER_ID", "").strip()
    account = os.environ.get("PEAKCUT_ORIGINAL_YOUTUBE_ACCOUNT", "").strip()
    if not uid or not account.startswith("yt_") or not original_voice.commercially_allowed():
        return None
    try:
        hour = int(os.environ.get("PEAKCUT_ORIGINAL_PUBLISH_HOUR_AMMAN", "11"))
    except ValueError:
        hour = 11
    return uid, hour if 0 <= hour <= 23 else 11


def _targets() -> list[tuple[str, int, str | None]]:
    """(user, local hour, in-app user id or None for legacy env)."""
    targets = []
    legacy = _legacy_config()
    if legacy:
        targets.append((legacy[0], legacy[1], None))
    for uid, cfg in original_youtube_settings.enabled_owners():
        if legacy and uid == legacy[0]:
            continue  # respect env-managed legacy binding without double jobs
        hour = cfg.get("hour", 11)
        if type(hour) is not int or not 0 <= hour <= 23:
            continue
        targets.append((uid, hour, uid))
    return targets


def start() -> bool:
    """Initialize once. Disabled by default until at least one owner opts in."""
    global _started
    try:
        if not _targets():
            return False
    except (OSError, RuntimeError, ValueError):
        print("[original-youtube] invalid publisher registry; not starting", flush=True)
        return False
    with _lock:
        if _started:
            return True
        _started = True
        threading.Thread(target=_loop, daemon=True,
                         name="original-youtube-autopilot").start()
        return True


def _loop():
    from tools.original_youtube_autopilot import publish_once, refresh_views

    last_checked: dict[str, str] = {}
    while True:
        now = datetime.now(_ZONE)
        day = now.date().isoformat()
        try:
            targets = _targets()
        except (OSError, RuntimeError, ValueError):
            targets = []  # do not publish with a damaged/corrupt registry
            print("[original-youtube] invalid publisher registry; no posts", flush=True)
        for uid, hour, configured_user in targets:
            if not _publish_due(now, hour) or last_checked.get(uid) == day:
                continue
            last_checked[uid] = day
            try:
                refresh_views(owner_id=configured_user)
            except Exception:
                print("[original-youtube] views unavailable, keeping last verified data", flush=True)
            try:
                result = publish_once(original_voice.selected_model(), owner_id=configured_user)
                print(f"[original-youtube] {uid[:10]}: {result.get('status')}", flush=True)
            except Exception:
                # A network timeout after an upload can mean YouTube received
                # the video; never retry blindly. Manual reconciliation via
                # authenticated YouTube Studio is the only safe way.
                print("[original-youtube] failed; inspect the video ledger in app", flush=True)
        time.sleep(300)
