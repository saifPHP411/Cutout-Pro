"""Read a closed seven-day cohort from the owner's YouTube Analytics account."""
import re
import time
from datetime import datetime, time as midnight, timedelta
from zoneinfo import ZoneInfo

import httpx

from . import accounts

PACIFIC = ZoneInfo("America/Los_Angeles")
METRICS = ("views", "averageViewPercentage")


def cohort(published, now=None):
    start = datetime.fromtimestamp(published, PACIFIC).date() + timedelta(days=1)
    end = start + timedelta(days=6)
    closed = datetime.combine(end + timedelta(days=1), midnight(0), PACIFIC).timestamp()
    if (now if now is not None else time.time()) < closed + 3 * 86400:
        raise ValueError("Wait until three days after the seven-day YouTube window closes.")
    return start, end, closed


def youtube(account, video_id, published, request=None):
    request = request or httpx.get
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or "") or account.get("platform") != "youtube":
        raise ValueError("Choose a posted video on a connected YouTube channel.")
    start, end, closed = cohort(published)
    headers = {"Authorization": "Bearer " + accounts._yt_token(account)}
    # Wait for reporting lag before reading the aggregate of a fixed window.
    common = {"ids": "channel==MINE", "filters": f"video=={video_id}", "startDate": start.isoformat(),
              "endDate": end.isoformat()}
    response = request("https://youtubeanalytics.googleapis.com/v2/reports",
                       params={**common, "metrics": ",".join(METRICS)}, headers=headers, timeout=20)
    if response.status_code in (401, 403):
        raise ValueError("Reconnect YouTube with analytics permission and enable YouTube Analytics API.")
    if response.status_code != 200:
        raise RuntimeError("YouTube Analytics is unavailable. Try later.")
    data = response.json()
    names = [item["name"] for item in data.get("columnHeaders", [])]
    rows = data.get("rows") or []
    if len(rows) != 1 or len(set(names)) != len(METRICS) or set(names) != set(METRICS) or len(rows[0]) != len(names):
        raise ValueError("YouTube has not returned a complete cohort for this video.")
    row = dict(zip(names, rows[0]))
    if type(row["views"]) is not int or row["views"] <= 0:
        raise ValueError("The YouTube report has no eligible observation to import.")
    return {"published_at": datetime.fromtimestamp(published, PACIFIC).isoformat(),
            "observed_at": datetime.fromtimestamp(closed, PACIFIC).isoformat(),
            "retention": row["averageViewPercentage"], "views": row["views"],
            "interactions": None,
            "evidence": f"YouTube Analytics · {start.isoformat()} to {end.isoformat()}",
            "remote_id": video_id}
