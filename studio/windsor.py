"""Read verified, owner-published YouTube cohorts from the Windsor.ai API.

This integration is deliberately read-only and opt-in. A server administrator must
configure both the API key and the exact channel ID; the ChatGPT Windsor plugin
connection does not give this server an API credential. API credentials are only
sent to the fixed, official HTTPS endpoint and never included in exceptions.
"""
import json
import logging
import math
import re

import httpx

from . import config
from .analytics_import import cohort
from datetime import datetime
from zoneinfo import ZoneInfo

# httpx's default INFO log includes full request URLs. Windsor must put its
# key in the query string, so disable outbound URL logging at that level.
logging.getLogger("httpx").setLevel(logging.WARNING)

API = "https://connectors.windsor.ai/youtube"
CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
# Only verified dimensions / metrics from the official Windsor YouTube field list.
FIELDS = "account_id,video,views,average_view_percentage"
PACIFIC = ZoneInfo("America/Los_Angeles")


def configured():
    """Only indicate availability; never return key material to the dashboard."""
    channel = (config.env("PEAKCUT_WINDSOR_YOUTUBE_CHANNEL_ID") or "").strip()
    key = config._secret("PEAKCUT_WINDSOR_API_KEY", "PEAKCUT_WINDSOR_API_KEY_FILE")
    return bool(CHANNEL_ID.fullmatch(channel) and key)


def youtube(account, video_id, published, request=None, now=None):
    """Import a closed seven-day cohort, matched to an *exact* YouTube account.

    ``account`` is obtained from PeakCut's owner-scoped connected accounts, not
    from a client-supplied channel or account id. The returned account/video
    dimensions must both match; no rows or ambiguous rows fail closed.
    """
    account_id = (account or {}).get("id") or ""
    if (account or {}).get("platform") != "youtube" or not VIDEO_ID.fullmatch(video_id or ""):
        raise ValueError("Choose a published video on a connected YouTube channel.")
    channel = (config.env("PEAKCUT_WINDSOR_YOUTUBE_CHANNEL_ID") or "").strip()
    if not CHANNEL_ID.fullmatch(channel) or account_id != "yt_" + channel:
        raise ValueError("Windsor channel binding does not match the connected publishing account.")
    key = config._secret("PEAKCUT_WINDSOR_API_KEY", "PEAKCUT_WINDSOR_API_KEY_FILE")
    if not key:
        raise ValueError("Windsor is not configured on this server. Add the server-side API key.")

    start, end, closed = cohort(published, now=now)
    # Windsor's internal connector-account ID may differ from the public
    # YouTube channel ID. Both are admin-configured; the returned channel ID
    # is still verified against the connected publishing account below.
    selected = (config.env("PEAKCUT_WINDSOR_ACCOUNT_ID") or channel).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", selected):
        raise ValueError("Windsor account selection is invalid.")
    params = {"api_key": key, "fields": FIELDS, "select_accounts": selected,
              "date_from": start.isoformat(), "date_to": end.isoformat(),
              "filter": json.dumps([["video", "eq", video_id]], separators=(",", ":")),
              "_max_rows": "100"}
    # Do not pass the user-provided request URL/host through this function.
    try:
        response = (request or httpx.get)(API, params=params, timeout=25,
                                          follow_redirects=False,
                                          headers={"User-Agent": "Windsor/1.0"})
        if response.status_code != 200:
            raise RuntimeError("Windsor did not return a successful report.")
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        # Do not reveal request URLs: Windsor authenticates using a query param.
        raise RuntimeError("Windsor report failed. Check its API key and channel connection.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or payload.get("error"):
        raise ValueError("Windsor returned an invalid or unavailable report.")
    matches = [r for r in payload["data"] if isinstance(r, dict) and
               r.get("account_id") == channel and r.get("video") == video_id]
    if len(matches) != 1:
        raise ValueError("Windsor has no unambiguous observation for that video and period.")
    row = matches[0]
    try:
        # Some connectors encode numeric fields as JSON strings; reject NaNs and booleans.
        views_raw, percentage_raw = row.get("views"), row.get("average_view_percentage")
        if isinstance(views_raw, bool) or isinstance(percentage_raw, bool):
            raise ValueError
        views = int(views_raw)
        retention = float(percentage_raw)
        if (views <= 0 or str(views) != str(views_raw) and not isinstance(views_raw, int)
                or not math.isfinite(retention) or retention < 0 or retention > 1000):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Windsor report contains invalid views or average watch percentage.") from None
    return {"published_at": datetime.fromtimestamp(published, PACIFIC).isoformat(),
            "observed_at": datetime.fromtimestamp(closed, PACIFIC).isoformat(),
            "retention": retention, "views": views, "interactions": None,
            "evidence": f"Windsor.ai YouTube · {start.isoformat()} to {end.isoformat()}",
            "remote_id": video_id}
