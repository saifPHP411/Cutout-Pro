"""Owner-scoped campaign rules and manually verified financial records."""
import json
import math
import os
import threading
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import config, ctx

_LOCK = threading.RLock()
PLATFORMS = ("youtube", "tiktok", "instagram")
PAYMENTS = ("pending", "submitted", "paid", "disputed")
AMMAN = ZoneInfo("Asia/Amman")


def _path():
    return config.user_dir(ctx.uid()) / "campaigns.json"


def _read():
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _save(rows):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(rows, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)
    ctx.bump()


def get(cid):
    with _LOCK:
        return _read().get(cid)


def _number(data, key, low, high, default=None):
    value = float(data.get(key, default))
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{key.replace('_', ' ').capitalize()} is outside the allowed range.")
    return value


def save(body, cid=None):
    with _LOCK:
        rows = _read()
        prior = rows.get(cid)
        if cid and not prior:
            raise KeyError(cid)
        data = {**(prior or {}), **body}
        platforms = data.get("platforms")
        if not isinstance(platforms, list) or not platforms or not all(p in PLATFORMS for p in platforms):
            raise ValueError("Choose at least one permitted platform.")
        cid = cid or uuid.uuid4().hex
        ends = str(data.get("ends_on") or "")
        if ends:
            date.fromisoformat(ends)
        currency = str(data.get("currency") or "USD").upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("Use a three-letter currency code.")
        row = {"id": cid, "platforms": sorted(set(platforms)), "ends_on": ends, "currency": currency,
               "allow_repost": data.get("allow_repost") is True, "min_len": _number(data, "min_len", 3, 180, 15),
               "max_len": _number(data, "max_len", 3, 180, 60), "rate": _number(data, "rate", 0, 1e5, 0)}
        for key, limit in (("name", 100), ("creator", 100), ("source", 500), ("terms", 4000), ("branding", 1000)):
            row[key] = str(data.get(key) or "").strip()[:limit]
        if not row["name"] or not row["creator"] or not row["source"] or row["min_len"] > row["max_len"]:
            raise ValueError("Enter the campaign name, creator, authorized source and a valid duration range.")
        if row["source"] != "upload" and not row["source"].startswith("https://"):
            raise ValueError('Use the exact authorized HTTPS video link or "upload" for a licensed upload.')
        rule_fields = ("source", "platforms", "terms", "min_len", "max_len", "branding", "allow_repost", "ends_on")
        changed = not prior or any(row[k] != prior[k] for k in rule_fields)
        row["rules_version"] = int((prior or {}).get("rules_version", 0)) + int(changed)
        row["financial"] = (prior or {}).get("financial", {})
        rows[cid] = row
        _save(rows)
        return row


def verify_financial(cid, body):
    with _LOCK:
        rows = _read()
        row = rows[cid]
        views = body.get("eligible_views")
        if views in ("", None):
            views = None
        elif type(views) is not int or not 0 <= views <= 10**12:
            raise ValueError("Enter the number of verified eligible views.")
        confirmed = _number(body, "confirmed", 0, 1e9, 0)
        state = body.get("payment", "pending")
        source = str(body.get("evidence") or "").strip()[:300]
        if state not in PAYMENTS or ((views is not None or confirmed > 0 or state == "paid") and not source):
            raise ValueError("Provide a platform report or a payment reference for confirmed figures.")
        if confirmed > 0 and state != "paid":
            raise ValueError("Confirmed earnings require a confirmed payment.")
        row["financial"] = {"eligible_views": views, "confirmed": confirmed, "payment": state,
                            "evidence": source}
        _save(rows)
        return row


def list_for(jobs):
    with _LOCK:
        rows = list(_read().values())
    for row in rows:
        clips = [c for j in jobs if j["options"].get("campaign") == row["id"] for c in j["clips"]]
        row["produced"] = sum(c["status"] == "done" for c in clips)
        row["posted"] = sum(any(p.get("status") == "posted" for p in c.get("posts", {}).values()) for c in clips)
        views = row["financial"].get("eligible_views")
        row["estimated"] = round(views * row["rate"] / 1000, 2) if views is not None else None
    return rows


def policy(job, clip, platform=None, require_confirmation=True):
    cid = (job.get("options") or {}).get("campaign")
    if not cid:
        return ""
    camp = get(cid)
    if not camp:
        return "The campaign is unavailable."
    if camp["ends_on"] and datetime.now(AMMAN).date() > date.fromisoformat(camp["ends_on"]):
        return "The campaign has ended."
    source = job["source"]
    expected = "upload" if source.get("type") == "file" else source.get("url")
    if camp["source"] != expected:
        return "The video source is outside the campaign rules."
    if not camp["min_len"] <= clip["end"] - clip["start"] <= camp["max_len"]:
        return "Clip duration is outside the campaign rules."
    if platform and platform not in camp["platforms"]:
        return "This platform is outside the campaign rules."
    if require_confirmation and clip.get("campaign_approval") != camp["rules_version"]:
        return "Confirm the current campaign rules on this clip before posting."
    return ""
