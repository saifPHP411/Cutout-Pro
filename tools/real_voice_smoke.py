"""Real, free ARM64 Arabic Piper voice + source-original Shorts staging smoke.

May fetch ~64 MB of pinned MIT-declared model weights over HTTPS. Never
authenticates to YouTube and NEVER uploads videos. Output is QA-only.
"""
from __future__ import annotations
import json
import resource
import time
from pathlib import Path
from studio import original_shorts, original_voice

def main():
    dest = Path("artifacts/original-science")
    dest.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    model = original_voice.ensure_builtin()
    downloaded = round(time.perf_counter() - t0, 2)
    episode = original_shorts.catalog()[0]
    wav = dest / "voice.wav"
    original_shorts.voice_over(episode["narration"], wav, model)
    speech = original_shorts._duration(wav)
    if not 10 < speech < 170:
        raise RuntimeError(f"Unexpected Arabic narration length {speech:.2f}s")
    t1 = time.perf_counter()
    video = original_shorts.render(episode, dest, wav)
    render_secs = round(time.perf_counter() - t1, 2)
    report = {"status": "qa-only", "piper_model": original_voice.REPO,
              "source_revision": original_voice.REVISION,
              "model_license_declared": "MIT",
              "model_download_seconds": downloaded, "voice_duration_seconds": round(speech, 2),
              "render_seconds": render_secs, "video_size_mb": round(video.stat().st_size / 1048576, 2),
              "process_max_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
              "resolution": [1080,1920], "video": str(video)}
    (dest / "qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
