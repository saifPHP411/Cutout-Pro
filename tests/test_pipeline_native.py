"""Opt-in real upload -> native speech -> local ranking -> inspected MP4."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("models", type=Path)
    p.add_argument("--repeat", type=int, choices=(1, 2), default=1)
    args = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="peakcut-native-pipeline-") as root:
        os.environ.update(PEAKCUT_DATA=root, PEAKCUT_MODELS=str(args.models.resolve()), DATABASE_URL="",
                          PEAKCUT_ACCOUNTS="0", PEAKCUT_FETCH_MODEL="0", PEAKCUT_UPDATE_CHECK="0",
                          PEAKCUT_AI_MODELS="", PEAKCUT_AI_KEY="", PEAKCUT_AI_KEY_FILE="", PEAKCUT_ASR_URL="",
                          PEAKCUT_ASR_THREADS="2", PEAKCUT_ASR_COPIES="1")
        sys.path.insert(0, ".")
        from fastapi.testclient import TestClient
        from studio import fetch_model, media, server

        if not fetch_model.ready():
            p.error("Provide a directory containing the installed English speech model.")
        with TestClient(server.app, base_url="http://127.0.0.1") as api:
            for run in range(args.repeat):
                with args.source.open("rb") as video:
                    ready = api.post("/api/upload", files={"file": (args.source.name, video, "video/mp4")},
                                     data={"options": json.dumps({"min_len": 5, "max_len": 18, "max_clips": 2,
                                                              "mode": "balanced", "caption_mode": "original"})})
                ready.raise_for_status()
                url = "/api/jobs/" + ready.json()["id"]
                for _ in range(240):
                    job = api.get(url).json()
                    if job["status"] in ("done", "error"):
                        break
                    time.sleep(0.5)
                assert job["status"] == "done", job
                clips = [c for c in job["clips"] if c["status"] == "done"]
                assert clips and all(c["qc"]["status"] != "failed" for c in clips), job
                for clip in clips:
                    file = api.get(f"/media/{job['id']}/{clip['file']}")
                    file.raise_for_status()
                    path = Path(root) / clip["file"]
                    path.write_bytes(file.content)
                    meta = media.probe(path)
                    assert (meta["width"], meta["height"]) == (1080, 1920), meta
                    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path),
                                    "-f", "null", "-"], check=True)
                if run:
                    assert "transcribing" not in job.get("metrics", {}), job
                print(json.dumps({"trial": run + 1, "stage_seconds": job.get("metrics", {}), "clips": len(clips), "words": job["word_count"], "took_seconds": job["took"],
                                  "export_quality": [c["qc"]["status"] for c in clips], "paid_requests": 0}))


if __name__ == "__main__":
    main()
