"""ARM64-friendly, reproducible FFmpeg motion-sampling overhead benchmark.

This synthetic fixture is a runtime regression smoke test, NOT a real streaming
highlight accuracy benchmark or a cold full-pipeline speedup claim.
"""
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from studio import quality  # noqa: E402


def generate(destination, seconds):
    # Deterministic, dynamic, locally generated scenes. No network, rights or AI.
    subprocess.run([
        "ffmpeg", "-hide_banner", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=12:duration={seconds}",
        "-threads", "2", "-c:v", "mpeg4", "-q:v", "14",
        "-pix_fmt", "yuv420p", str(destination),
    ], check=True, timeout=120)


def inspect(source, duration, start=0, window=None):
    window = duration if window is None else window
    t0 = time.perf_counter()
    samples = quality.sampled_motion_map(source, start=start, seconds=window)
    elapsed = round(time.perf_counter() - t0, 3)
    if len(samples) < max(3, int(window * 0.55)):
        raise AssertionError(f"Motion scan dropped most frames: {duration}s, {len(samples)} samples")
    if any(not 0 <= t <= window + 1 for t, _ in samples):
        raise AssertionError("Non-relative motion timestamp leaked from seeked input")
    if not max(v for _, v in samples) > 0:
        raise AssertionError("Synthetic changing scene registered no movement")
    # ru_maxrss is a CHILD process high-water mark on Linux, not per-run memory.
    peak_kib = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return {"source_seconds": duration, "scan_start_seconds": start,
            "window_seconds": window, "samples": len(samples),
            "visual_overhead_seconds": elapsed, "child_rss_highwater_mib": round(peak_kib / 1024, 1),
            "source_size_mb": round(source.stat().st_size / 1048576, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/smart-motion.json"))
    args = parser.parse_args()
    # Synthetic short / medium / long samples; not real creator footage or ASR.
    durations = (12, 40, 100)
    results = []
    with tempfile.TemporaryDirectory(prefix="peakcut-motion-bench-") as folder:
        for duration in durations:
            source = Path(folder) / f"synthetic-{duration}s.mp4"
            generate(source, duration)
            results.append(inspect(source, duration))
            if duration == durations[-1]:
                results.append(inspect(source, duration, start=20, window=30))
    output = {
        "dataset": "synthetic_ffmpeg_testsrc2",
        "description": "Measured optional visual scan overhead only (not end-to-end latency, not highlight accuracy)",
        "platform": os.uname().machine if hasattr(os, "uname") else sys.platform,
        "max_concurrent_ffmpeg_threads": 2,
        "scan_fps": 1,
        "scan_width": 160,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
