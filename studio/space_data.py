"""No-key, fact-checked NASA/JPL close-approach topic discovery.

Only numerical fields from the documented JPL SBDB CAD API are used; this
module does NOT summarize arbitrary webpages with an unverified model. The
published data are predictions (orbital solutions can change), not impact
risk statements. A missing/changed feed stops production instead of inventing
a story. Videos use original illustrations and no NASA footage.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

URL = "https://ssd-api.jpl.nasa.gov/cad.api"
DOC = "https://ssd-api.jpl.nasa.gov/doc/cad.html"
AU_KM = 149_597_870.7
MOON_DISTANCE_KM = 384_400
FEED_VERSION = "1.5"
REQUIRED = frozenset({"des", "cd", "dist", "dist_min", "dist_max", "v_rel"})
_ID = re.compile(r"^[A-Za-z0-9 ()+._-]{1,48}$")


def source_url(today: date, days: int = 21) -> str:
    if not 7 <= days <= 30:
        raise ValueError("JPL topic window must be between 7 and 30 days.")
    params = {
        "date-min": today.isoformat(),
        "date-max": (today + timedelta(days=days)).isoformat(),
        "body": "Earth", "kind": "a",
        "dist-max": "0.05", "sort": "dist",
        "fullname": "true", "limit": "100",
    }
    return URL + "?" + urlencode(params)


def parse_approaches(response: dict, today: date) -> list[dict]:
    signature = response.get("signature") or {}
    if signature.get("version") != FEED_VERSION or "NASA/JPL" not in signature.get("source", ""):
        raise ValueError("Unknown JPL API format/source; fail closed.")
    names = response.get("fields")
    records = response.get("data", [])
    if not isinstance(names, list) or not REQUIRED.issubset(names):
        raise ValueError("Missing documented JPL scientific fields.")
    if not isinstance(records, list) or len(records) > 100:
        raise ValueError("JPL data rows exceeded expected bound.")
    parsed = []
    for row in records:
        try:
            if not isinstance(row, list) or len(row) != len(names):
                continue
            r = dict(zip(names, row))
            des = str(r["des"]).strip()
            if not _ID.fullmatch(des):
                continue
            stamp = datetime.strptime(str(r["cd"]), "%Y-%b-%d %H:%M")
            event_day = stamp.date()
            if not today <= event_day <= today + timedelta(days=30):
                continue
            au = float(r["dist"])
            au_min, au_max, speed = float(r["dist_min"]), float(r["dist_max"]), float(r["v_rel"])
            # Reject surprising, inconsistent, or extremely imprecise values.
            if not all(math.isfinite(v) for v in (au, au_min, au_max, speed)):
                continue
            if not (0.001 < au < 0.05 and 0 < au_min <= au <= au_max and
                    (au_max - au_min) / au < 0.12 and 1 <= speed <= 90):
                continue
            lunar = au * AU_KM / MOON_DISTANCE_KM
            # Reject sensational near-collision stories, and uninteresting distant passes.
            if not 1.2 <= lunar <= 15:
                continue
            parsed.append({"des": des, "date": event_day.isoformat(),
                           "time_tdb": stamp.strftime("%H:%M"), "au": au,
                           "lunar": lunar, "km": au * AU_KM, "km_s": speed})
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return sorted(parsed, key=lambda x: (x["lunar"], x["date"], x["des"]))


def fetch_approaches(today: date | None = None, *, opener=urlopen) -> tuple[list[dict], str]:
    """Fetch up to 100 entries and <=512k bytes, with fixed allowlisted endpoint."""
    today = today or datetime.now(timezone.utc).date()
    url = source_url(today)
    request = Request(url, headers={"User-Agent": "PeakCut-OriginalScience/1.0 (+source-linked-video)", "Accept": "application/json"})
    with opener(request, timeout=12) as response:
        if getattr(response, "status", 200) != 200:
            raise RuntimeError("JPL service returned an unsuccessful HTTP status.")
        raw = response.read(512_001)
    if len(raw) > 512_000:
        raise ValueError("Unexpectedly large JPL result.")
    data = json.loads(raw.decode("utf-8"))
    return parse_approaches(data, today), url


def _ar_num(v: float, decimals: int = 1) -> str:
    return f"{v:,.{decimals}f}".replace(",", "٬")


def episode_for(approach: dict, source: str) -> dict:
    """Translate structured physical quantities into original Arabic science."""
    des = approach["des"]
    when = date.fromisoformat(approach["date"])
    if not source.startswith(URL + "?") or not _ID.fullmatch(des):
        raise ValueError("Unverified JPL data/source.")
    km_m = _ar_num(approach["km"] / 1_000_000, 2)
    lunar = _ar_num(approach["lunar"], 1)
    speed = _ar_num(approach["km_s"], 1)
    # A date belongs to the astronomical close-approach solution, not a claim
    # that the approach is visible or dangerous to people on Earth.
    date_text = f"{when.day} / {when.month} / {when.year}"
    slug = re.sub(r"[^a-z0-9]+", "-", des.lower()).strip("-")
    key = f"jpl-{slug}-{when:%Y%m%d}"
    hook = "كيف نقيس اقتراب الكويكبات؟"
    narration = (
        f"بيانات جديدة من مختبر الدفع النفاث التابع لناسا تتوقع اقتراب الكويكب {des} "
        f"من الأرض حول تاريخ {date_text}. المسافة المتوقعة عند أقرب نقطة حوالي "
        f"{km_m} مليون كيلومتر. هذا يعادل تقريباً {lunar} مرة متوسط المسافة "
        f"بين الأرض والقمر. وتبلغ سرعته النسبية حوالي {speed} كيلومتر في الثانية. "
        "لاحظ أن كلمة اقتراب في علم الفلك لا تعني اصطداماً. "
        "هذه أرقام محسوبة من مدارات الأجرام، ويمكن أن تتغير مع الرصد الجديد. "
        "والرسم توضيحي وليس صورة حقيقية للكويكب."
    )
    return {
        "id": key, "theme": "asteroid",
        "title": f"كويكب {des}: ما مسافة مروره قرب الأرض؟ #Shorts",
        "hook": hook, "narration": narration,
        "source": source, "source_name": "NASA/JPL — SBDB Close Approach",
        "source_kind": "jpl_measured", "source_date": approach["date"],
        "data": {k: approach[k] for k in ("des", "date", "lunar", "km", "km_s")},
        "scenes": [
            {"headline": "حدث فلكي محسوب", "detail": f"الكويكب {des} · {date_text}", "stat": "رصد مداري من ناسا"},
            {"headline": "ما أبعد نقطة؟", "detail": "قياس لأقرب مسافة متوقعة من الأرض.", "stat": f"{lunar} مرة مسافة القمر"},
            {"headline": "ماذا تعني السرعة؟", "detail": "سرعة نسبية، وليست دليلاً على خطر اصطدام.", "stat": f"{speed} كم / ثانية"},
        ],
        "tags": ["كويكبات", "الفضاء", "ناسا"],
    }


def discover_unused(state: dict, today: date | None = None, *, loader=fetch_approaches) -> dict | None:
    """At most one entirely new JPL object per 10 days; never pad with repeats."""
    today = today or datetime.now(timezone.utc).date()
    history = state.get("episodes", {})
    recent = [k for k, value in history.items() if k.startswith("jpl-") and
              isinstance(value, dict) and value.get("started")]
    if recent:
        last = max(float(history[k]["started"]) for k in recent)
        if datetime.now(timezone.utc).timestamp() - last < 10 * 86400:
            return None
    approaches, source = loader(today)
    used_objects = {
        str(value.get("source_designation", ""))
        for key, value in history.items() if key.startswith("jpl-") and isinstance(value, dict)
    }
    for candidate in approaches:
        if candidate["des"] in used_objects:
            continue
        episode = episode_for(candidate, source)
        if episode["id"] not in history:
            return episode
    return None
