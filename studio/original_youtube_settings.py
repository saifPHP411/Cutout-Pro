"""Persistent opt-in for original YouTube Shorts, isolated by PeakCut user.

A channel owner can authorize automatic publication once from their own
authenticated app session. This file contains NO OAuth tokens, passwords, API
keys, or credentials; only channel/account IDs, scheduling and opt-in times.
The data live under PeakCut's private storage, never in the repository.
"""
from __future__ import annotations
import json
import os
import threading
import time
from pathlib import Path
from . import config

_FILE = config.DATA_DIR / "original_youtube_subscriptions.json"
_lock = threading.RLock()
MAX_STORED_BYTES = 128_000


def _load() -> dict:
    if not _FILE.exists():
        return {}
    if _FILE.stat().st_size > MAX_STORED_BYTES:
        raise RuntimeError("Original channel registry is unexpectedly large.")
    data = json.loads(_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Original channel registry is corrupt.")
    return data


def _save(rows: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    if _FILE.is_symlink():
        raise RuntimeError("Refusing to replace a symbolic link in owner settings.")
    tmp = _FILE.parent / f".{_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(_FILE)
    finally:
        tmp.unlink(missing_ok=True)


def get(uid: str) -> dict:
    """A missing/disabled owner never authorizes publication."""
    if not uid:
        return {"enabled": False, "account_id": "", "hour": 11}
    with _lock:
        val = _load().get(uid, {})
    if not isinstance(val, dict):
        raise RuntimeError("Invalid publisher settings for owner.")
    return {"enabled": val.get("enabled") is True,
            "account_id": val.get("account_id", ""),
            "hour": val.get("hour", 11),
            "consented_at": val.get("consented_at")}


def set_owner(uid: str, *, enabled: bool, account_id: str = "", hour: int = 11) -> dict:
    if not isinstance(uid, str) or not uid or len(uid) > 200:
        raise ValueError("Invalid account owner.")
    if type(enabled) is not bool or type(hour) is not int or not 0 <= hour <= 23:
        raise ValueError("Invalid publication flag or hour.")
    if enabled and (not isinstance(account_id, str) or not account_id.startswith("yt_")
                    or len(account_id) > 180):
        raise ValueError("Select a connected YouTube account.")
    with _lock:
        all_rows = _load()
        old = all_rows.get(uid, {})
        if enabled:
            all_rows[uid] = {"enabled": True, "account_id": account_id,
                             "hour": hour, "consented_at": time.time()}
        else:
            all_rows[uid] = {"enabled": False, "account_id": old.get("account_id", ""),
                             "hour": old.get("hour", 11), "consented_at": old.get("consented_at")}
        _save(all_rows)
    return get(uid)


def enabled_owners() -> list[tuple[str, dict]]:
    with _lock:
        all_rows = _load()
    return [(uid, get(uid)) for uid, val in all_rows.items()
            if isinstance(uid, str) and isinstance(val, dict)
            and val.get("enabled") is True]
