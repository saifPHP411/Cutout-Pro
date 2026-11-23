"""Ad breaks and static screens.

Twitch and YouTube stitch commercials into a stream, and a "be right back" slate
can sit where the good moment is. A clip taken from there is worthless, so those
seconds are found and left out.

Metric: mean absolute luma difference between consecutive frames
(tblend=difference + signalstats). Measured on real captures:

    ad slate         ~0.19
    gameplay clip    ~1.6 - 2.5

A flat 0-255 luma difference separates them with a wide margin, so no OCR, no
reference image and no model is needed. The frames are read once for the whole
video, and clips are then checked against that map.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import work_control, config

_FFMPEG = "ffmpeg"

_PTS = re.compile(r"pts_time:([0-9.]+)")
_YAVG = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")


def motion_map(path: str | Path, bucket: float = 2.0) -> list[tuple[float, float]]:
    """(time, motion) for every bucket of the video, or nothing if it cannot be read.

    Motion is the mean luma difference between consecutive frames: near zero for a
    frozen screen, high for anything actually moving."""
    try:
        proc = work_control.run(
            [_FFMPEG, "-hide_banner", "-loglevel", "error",
             "-i", str(path),
             "-vf", "tblend=all_mode=difference,signalstats,metadata=print:file=-",
             "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=900,
        )
    except Exception:  # noqa: BLE001 - a missing ffmpeg must not stop a job
        return []
    times: list[float] = []
    vals: list[float] = []
    at = 0.0
    for line in (proc.stdout or "").splitlines():
        m = _PTS.search(line)
        if m:
            at = float(m.group(1))
            continue
        m = _YAVG.search(line)
        if m:
            times.append(at)
            vals.append(float(m.group(1)))
    if len(vals) > 1:
        times, vals = times[1:], vals[1:]      # the first blended frame is against black
    if not vals:
        return []
    buckets: dict[int, list[float]] = {}
    for t, v in zip(times, vals):
        buckets.setdefault(int(t / bucket), []).append(v)
    return [(i * bucket, sum(v) / len(v)) for i, v in sorted(buckets.items())]


def sampled_motion_map(path: str | Path, start: float = 0, seconds: float = 600,
                       fps: int = 1) -> list[tuple[float, float]]:
    """Cheap, optional visual activity signal from *sampled* low-resolution frames.

    Timestamps are relative to the requested window, suitable for block-local
    transcript analysis. This is a measure of frame changes, not an AI description
    of a kill, reaction or other visual event. A 10-minute cap protects CPU-only
    hosts from unbounded scans. Failure to read a video is non-fatal.
    """
    import math

    if not Path(path).is_file() or not all(math.isfinite(v) for v in (start, seconds)):
        return []
    if start < 0 or seconds <= 1 or fps not in (1, 2):
        return []
    seconds = min(float(seconds), 600.0)
    try:
        proc = work_control.run(
            [_FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", str(start),
             "-i", str(path), "-t", str(seconds), "-threads", "2", "-filter_threads", "1",
             "-vf", f"fps={fps},scale=160:-2:flags=fast_bilinear,"
                    "tblend=all_mode=difference,signalstats,metadata=print:file=-",
             "-an", "-sn", "-f", "null", "-"],
            capture_output=True, text=True, timeout=150,
        )
    except Exception:  # noqa: BLE001 - an unavailable codec must not stop a video job
        return []
    if proc.returncode:
        return []
    values: list[tuple[float, float]] = []
    at = None
    for line in (proc.stdout or "").splitlines():
        if found := _PTS.search(line):
            at = float(found.group(1))
        elif at is not None and (found := _YAVG.search(line)):
            movement = float(found.group(1))
            if math.isfinite(movement) and math.isfinite(at) and 0 <= at <= seconds + 1:
                values.append((round(at, 2), round(movement, 3)))
    # tblend's first frame is measured against black, not a preceding real frame.
    return values[1:]




def static_ranges(path: str | Path, bucket: float = 2.0, threshold: float | None = None,
                  min_seconds: float | None = None) -> list[tuple[float, float]]:
    """The stretches that look like an ad break, a slate or a frozen screen."""
    threshold = config.STATIC_THRESHOLD if threshold is None else threshold
    min_seconds = config.STATIC_MIN_SECONDS if min_seconds is None else min_seconds
    pairs = motion_map(path, bucket)
    if not pairs:
        return []
    runs: list[tuple[float, float]] = []
    start = None
    for t, v in pairs:
        if v < threshold:
            if start is None:
                start = t
        elif start is not None:
            runs.append((start, t))
            start = None
    if start is not None:
        runs.append((start, pairs[-1][0] + bucket))
    return [(a, b) for a, b in runs if b - a >= min_seconds]


def static_overlap(start: float, end: float, ranges: list[tuple[float, float]]) -> float:
    """How much of a clip (in seconds) sits inside a static stretch."""
    total = 0.0
    for a, b in ranges:
        lo, hi = max(a, start), min(b, end)
        if hi > lo:
            total += hi - lo
    return total


def export_report(path, expected_size, expected_duration, captions_expected=False, ass=None, deep=False, preview=False):
    """Hard format checks and optional full decode, with editorial issues flagged for review."""
    import json
    import math
    from fractions import Fraction

    from . import media

    issues, failures = [], []

    def report():
        return {"status": "failed" if failures else "needs_review" if issues else "ready",
                "reasons": failures + issues, "deep": deep}

    try:
        data = json.loads(media.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                                     "-of", "json", str(path)], timeout=30))
        video = next(s for s in data["streams"] if s["codec_type"] == "video")
        audio = next(s for s in data["streams"] if s["codec_type"] == "audio")
        duration = float(data["format"]["duration"])
        fps = float(Fraction(video["avg_frame_rate"]))
    except (ValueError, KeyError, OSError, RuntimeError, StopIteration, ZeroDivisionError):
        failures.append("The export could not be read with both video and audio streams.")
        return report()
    if (video.get("width"), video.get("height")) != expected_size:
        failures.append("Export dimensions differ from the selected aspect or mode.")
    if not math.isfinite(duration) or abs(duration - expected_duration) > 0.25:
        failures.append("Export duration differs from the requested clip.")
    if abs(fps - config.OUT_FPS) > 1 or video.get("codec_name") != "h264" or audio.get("codec_name") != "aac":
        failures.append("The frame rate or codec is incompatible with the export profile.")
    if captions_expected and (not ass or not any(line.startswith("Dialogue: 1,") or line.startswith("Dialogue: 2,")
                                                for line in ass.splitlines())):
        issues.append("Captions were requested but no timed caption text was exported.")
    if preview:
        issues.append("This is a Fast preview. Export a final version before publishing.")
    if deep and not failures:
        try:
            checked = work_control.run(
                ["ffmpeg", "-hide_banner", "-v", "info", "-xerror", "-threads", "2", "-i", str(path),
                 "-filter_threads", "1", "-vf", "scale=320:-2,blackdetect=d=0.75:pix_th=0.10,freezedetect=n=-55dB:d=2",
                 "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired):
            issues.append("The full decode check did not finish.")
        else:
            if checked.returncode:
                failures.append("FFmpeg could not decode the full export.")
            if "black_start:" in checked.stderr:
                issues.append("A black segment lasts at least 0.75 seconds.")
            if "freeze_start:" in checked.stderr:
                issues.append("A frozen segment lasts at least 2 seconds.")
            mean = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", checked.stderr)
            if mean and float(mean[1]) < -50:
                issues.append("The audio is very quiet or silent.")
    return report()
