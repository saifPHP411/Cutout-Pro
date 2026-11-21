"""Measure actual audio extraction and export on the same local source in two checkouts."""
import argparse
import json
import os
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def trial(args):
    sys.path.insert(0, str(args.checkout))
    if args.cores and hasattr(os, "sched_getaffinity"):
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:args.cores])
    with tempfile.TemporaryDirectory(prefix="peakcut-bench-") as temp:
        os.environ["PEAKCUT_DATA"] = temp
        if args.models:
            os.environ["PEAKCUT_MODELS"] = str(args.models)
            os.environ["PEAKCUT_ASR_THREADS"] = str(args.cores)
            os.environ["PEAKCUT_ASR_COPIES"] = "1"
            os.environ["PEAKCUT_ASR_URL"] = ""
        from studio import media, render, transcribe

        render.install_fonts()
        out = Path(temp) / "measure"
        out.mkdir()
        meta = media.probe(args.source)
        end = min(meta["duration"], args.seconds)
        clip = {"start": 0, "end": end, "layout": args.layout, "style": "bounce",
                "hook": "PeakCut", "mode": args.mode}
        words = [{"w": word, "s": i * 0.42, "e": i * 0.42 + 0.35}
                 for i, word in enumerate("Measure the same input and check every export".split())
                 if i * 0.42 + 0.35 <= end]
        def cpu_time():
            stats = [resource.getrusage(kind) for kind in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)]
            return sum(s.ru_utime + s.ru_stime for s in stats)

        cpu_before = cpu_time()
        t0 = time.perf_counter()
        wav = out / "audio.wav"
        if hasattr(media, "decode_audio") and args.models:
            samples = media.decode_audio(args.source, wav, meta["duration"])
        else:
            media.extract_audio(args.source, wav)
            samples = media.load_audio(wav)
        audio = time.perf_counter() - t0
        speech = None
        if args.models:
            if not transcribe.model_ready(args.lang):
                raise ValueError("Install the requested speech model in the benchmark models directory first.")
            start = time.perf_counter()
            samples = samples[:int(args.asr_seconds * 16000)] if args.asr_seconds else samples
            native_words = transcribe.transcribe(samples, lang=args.lang)
            speech = {"asr_input_seconds": len(samples) / 16000, "asr_cold_seconds": time.perf_counter() - start, "word_count": len(native_words)}
            if not args.cold_only:
                start = time.perf_counter()
                repeated = transcribe.transcribe(samples, lang=args.lang)
                speech["asr_warm_seconds"] = time.perf_counter() - start
                if native_words != repeated:
                    raise ValueError("Speech output changed between cold and warm runs.")
            import hashlib
            speech["word_fingerprint"] = hashlib.sha256(json.dumps(native_words, sort_keys=True).encode()).hexdigest()
            if args.real_captions:
                if native_words:
                    start = max(0, min(native_words[0]["s"] - 0.5, meta["duration"] - args.seconds))
                    end = min(meta["duration"], start + args.seconds)
                    clip.update(start=start, end=end)
                words = [w for w in native_words if clip["start"] <= w["s"] < clip["end"]]
                if not words:
                    raise ValueError("No timed words in the requested export range.")
        start = time.perf_counter()
        render.render_clip(args.source, out, "sample", clip, words, meta["width"], meta["height"])
        rendered = time.perf_counter() - start
        total = time.perf_counter() - t0
        self_stats = resource.getrusage(resource.RUSAGE_SELF)
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = cpu_time() - cpu_before
        mp4 = out / "sample.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(mp4), "-f", "null", "-"], check=True)
        info = media.probe(mp4)
        if abs(info["duration"] - (end - clip["start"])) > 0.16:
            raise ValueError("Export duration is outside tolerance.")
        if args.output:
            shutil.copyfile(mp4, args.output)
        return {**(speech or {}), "audio_seconds": audio, "render_seconds": rendered, "total_seconds": total,
                "cold_pipeline_seconds": audio + rendered + (speech or {}).get("asr_cold_seconds", 0),
                "cpu_seconds": cpu, "cpu_percent_allocated": 100 * cpu / (total * args.cores), "process_peak_rss_kib": max(self_stats.ru_maxrss, children.ru_maxrss),
                "disk_bytes": sum(f.stat().st_size for f in out.iterdir()), "mp4_bytes": mp4.stat().st_size,
                "width": info["width"], "height": info["height"], "duration": info["duration"], "render_start": clip["start"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=lambda x: Path(x).resolve())
    p.add_argument("--checkout", type=lambda x: Path(x).resolve(), default=Path.cwd())
    p.add_argument("--cores", type=int, choices=range(1, 65), default=2)
    p.add_argument("--seconds", type=float, default=25)
    p.add_argument("--mode", choices=("fast", "balanced", "quality"), default="balanced")
    p.add_argument("--layout", choices=("blur", "crop"), default="blur")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--models", type=lambda x: Path(x).resolve())
    p.add_argument("--lang", choices=("en", "ar"), default="en")
    p.add_argument("--asr-seconds", type=float, help="Limit the ASR comparison to the first N seconds")
    p.add_argument("--output", type=lambda x: Path(x).resolve())
    p.add_argument("--cold-only", action="store_true")
    p.add_argument("--real-captions", action="store_true")
    p.add_argument("--trial", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.trial:
        print(json.dumps(trial(args)))
        return
    trials = []
    for _ in range(args.runs):
        result = subprocess.check_output([sys.executable, __file__, *sys.argv[1:], "--trial"], text=True)
        trials.append(json.loads(result.splitlines()[-1]))
    print(json.dumps({"architecture": platform.machine(), "cores": args.cores, "mode": args.mode,
                      "revision": subprocess.check_output(["git", "-C", str(args.checkout), "rev-parse", "HEAD"], text=True).strip(),
                      "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0],
                      "source": args.source.name, "layout": args.layout, "trials": trials,
                      "median": {k: statistics.median(t[k] for t in trials) for k, v in trials[0].items() if isinstance(v, (float, int))}}, indent=2))


if __name__ == "__main__":
    main()
