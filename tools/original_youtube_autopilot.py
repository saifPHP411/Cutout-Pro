"""Automated ORIGINAL YouTube Shorts producer. No campaigns and no third-party clips.

The only publish path is enabled by explicit server flags and a pre-connected
YouTube OAuth account. For real unattended operation, launch --publish daily
via an owner-controlled cron job on the existing server. It will never repeat
an episode or choose a random external video.

Usage:
    python tools/original_youtube_autopilot.py plan
    python tools/original_youtube_autopilot.py preview --voice-model /data/voices/ar.onnx
    PEAKCUT_ORIGINAL_AUTOPOST=1 ... python tools/original_youtube_autopilot.py publish
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import accounts, config, ctx, original_shorts, original_voice, space_data  # noqa: E402

AUTO_DIR = "original_youtube"
AUTO_FLAG = "PEAKCUT_ORIGINAL_AUTOPOST"
LICENSE_FLAG = "PEAKCUT_ORIGINAL_VOICE_LICENSE_APPROVED"


def description(episode: dict) -> str:
    return ("قصة علمية قصيرة مع شرح ورسوم متحركة أصلية.\n"
            f"المصدر العلمي: {episode['source_name']}\n{episode['source']}\n\n"
            + " ".join("#" + tag for tag in episode["tags"]) + " #Shorts")


def _read_state(root: Path) -> dict:
    file = root / "ledger.json"
    if not file.exists():
        return {"episodes": {}, "last_post_utc": ""}
    doc = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(doc.get("episodes"), dict):
        raise RuntimeError("Invalid upload ledger. Refusing to risk duplicate posting.")
    return doc


def _write_state(root: Path, document: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "ledger.json"
    tmp = root / f"ledger.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def pick(episodes: list[dict], state: dict, requested: str = "") -> dict | None:
    for episode in episodes:
        if requested and episode["id"] != requested:
            continue
        # A reserved upload may have completed remotely: never retry it blindly.
        if episode["id"] in state.get("episodes", {}):
            continue
        return episode
    return None


def choose_episode(episodes: list[dict], state: dict, requested: str = "",
                   *, today: date | None = None, discover=None) -> dict | None:
    """Prefer distinct reviewed explainers. Then use source-validated JPL events.

    The JPL feed is never asked to invent an explanation: all narration comes
    from numeric measurements and fixed, meaning-preserving Arabic templates.
    The feed is rate-limited to one fresh asteroid per 10 days.
    """
    fixed = pick(episodes, state, requested)
    if fixed is not None or requested:
        return fixed
    if os.environ.get("PEAKCUT_ORIGINAL_LIVE_JPL", "1") != "1":
        return None
    discover = discover or space_data.discover_unused
    return discover(state, today=today)


def require_publishing_config(voice: Path | None = None) -> tuple[str, str, Path]:
    if os.environ.get(AUTO_FLAG) != "1":
        raise RuntimeError("Autopost is disabled. Explicit PEAKCUT_ORIGINAL_AUTOPOST=1 required.")
    if not original_voice.commercially_allowed(voice):
        raise RuntimeError("Confirm the custom voice model permits commercial output before publishing.")
    uid = os.environ.get("PEAKCUT_ORIGINAL_USER_ID", "").strip()
    account_id = os.environ.get("PEAKCUT_ORIGINAL_YOUTUBE_ACCOUNT", "").strip()
    if not uid or not account_id.startswith("yt_"):
        raise RuntimeError("Set the existing PeakCut owner ID and specific linked YouTube account ID.")
    return uid, account_id, config.user_dir(uid) / AUTO_DIR


def render_episode(episode, root: Path, voice: Path) -> Path:
    folder = root / episode["id"]
    folder.mkdir(parents=True, exist_ok=True)
    wav = folder / "narration.wav"
    if original_voice.use_builtin(voice) and not voice.exists():
        voice = original_voice.ensure_builtin()
    original_shorts.voice_over(episode["narration"], wav, voice)
    return original_shorts.render(episode, folder, wav)


def publish_once(voice: Path, requested: str = "", *, today: str | None = None):
    """At most one post/day; no automatic retries once an API upload was attempted.

    The full file lock lasts through uploading (one small daily Short). This
    prevents parallel cron invocations from selecting the same episode.
    """
    import fcntl

    uid, account_id, root = require_publishing_config(voice)
    episodes = original_shorts.catalog()
    root.mkdir(parents=True, exist_ok=True)
    now = today or datetime.now(timezone.utc).date().isoformat()
    with (root / "publish.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _read_state(root)
        if state.get("last_post_utc") == now or state.get("last_attempt_utc") == now:
            return {"status": "daily_limit", "date": now}
        try:
            episode = choose_episode(episodes, state, requested,
                                     today=date.fromisoformat(now))
        except (ValueError, RuntimeError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            # A missing or changed NASA/JPL result never creates an invented video.
            return {"status": "source_unavailable", "reason": type(exc).__name__}
        if not episode:
            return {"status": "no_new_verified_topic", "remaining": 0}
        with ctx.as_user(uid):
            account = accounts.get(account_id)
            if not account or account.get("platform") != "youtube":
                raise RuntimeError("This YouTube channel is not connected to the selected PeakCut owner.")
            video = render_episode(episode, root, voice)
            # Atomic reservation before sending bytes to YouTube. If the server
            # crashes mid-request, operator can reconcile by remote video ID
            # rather than accidentally uploading a duplicate.
            state["episodes"][episode["id"]] = {"status": "upload_unknown", "started": time.time(),
                                                  "title": episode["title"],
                                                  "source": episode["source"],
                                                  "source_designation": episode.get("data", {}).get("des", "")}
            state["last_attempt_utc"] = now
            _write_state(root, state)
            try:
                remote_id, url = accounts.youtube_upload(
                    account, video, episode["title"], description(episode),
                    episode["tags"], "public", category_id="27")
            except Exception as exc:
                state["episodes"][episode["id"]]["error"] = str(exc)[-240:]
                _write_state(root, state)
                raise RuntimeError("Upload status uncertain. Check YouTube Studio before any retry.") from exc
            state["episodes"][episode["id"]].update(status="published", video_id=remote_id,
                                                    url=url, uploaded_at=time.time())
            state["last_post_utc"] = now
            _write_state(root, state)
            return {"status": "published", "episode": episode["id"], "youtube_url": url}


def refresh_views() -> dict:
    """Read only verified YouTube view counts, never fabricate Shorts revenue.

    This is separate from creating/uploading. A missing or partial API result
    does not erase previously verified counts and is not represented as zero.
    """
    import fcntl

    uid, account_id, root = require_publishing_config()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "publish.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _read_state(root)
        posted = [(key, value) for key, value in state["episodes"].items()
                  if value.get("status") == "published" and value.get("video_id")]
        if not posted:
            return {"status": "no_published_videos", "updated": 0}
        with ctx.as_user(uid):
            account = accounts.get(account_id)
            if not account or account.get("platform") != "youtube":
                raise RuntimeError("Connected YouTube account no longer matches the publisher.")
            # Existing PeakCut OAuth refresh and authorized YouTube videos.list.
            counts = accounts.youtube_views(account, [v["video_id"] for _, v in posted])
        updated = 0
        for _, row in posted:
            remote = row["video_id"]
            if remote in counts and isinstance(counts[remote], int) and counts[remote] >= 0:
                row["verified_views"] = counts[remote]
                row["views_checked_at"] = time.time()
                updated += 1
        if updated:
            state["last_views_sync"] = time.time()
            _write_state(root, state)
        return {"status": "checked", "updated": updated,
                "unavailable": len(posted) - updated}


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "preview", "publish"))
    parser.add_argument("--episode", default="", help="Use a specific reviewed episode")
    parser.add_argument("--voice-model", type=Path, default=original_voice.selected_model())
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/original_shorts"))
    args = parser.parse_args(argv)
    episodes = original_shorts.catalog()
    if args.mode == "plan":
        print(json.dumps({"channel": "Space Lab Arabic", "monetization": "YouTube YPP when eligible",
                          "additional_topics": "NASA/JPL validated approaches: max 1 per 10 days",
                          "paid_services": [], "planned_topics": [
                              {"id": e["id"], "title": e["title"], "source": e["source"]}
                              for e in episodes], "autopost_default": "off"},
                         ensure_ascii=False, indent=2))
        return
    if args.mode == "preview":
        episode = next((e for e in episodes if not args.episode or e["id"] == args.episode), None)
        if episode is None:
            raise ValueError("Episode not found.")
        path = render_episode(episode, args.output_dir, args.voice_model)
        print(json.dumps({"status": "preview_only", "path": str(path), "episode": episode["id"]}))
        return
    print(json.dumps(publish_once(args.voice_model, args.episode), ensure_ascii=False))


if __name__ == "__main__":
    run()
