# Matched cold benchmark

Baseline e27f56c and processing revision 7f44025. Both ran the same FFmpeg version, English Parakeet model, one model copy, and two pinned x86_64 CPUs per pair. The short pair used logical CPUs 0–1, medium 7–8, and long 5–6. Each before/after pair ran sequentially on its own allocation. Different pairs overlapped, so timing changes may include shared-host contention. Each pair is one cold run per revision.

The three real videos contain speech. The short input uses seconds 23–49 from the 480p *Sintel* trailer. The medium and long inputs use seconds 210–285 and 180–420 from the full film. All are Blender Foundation sources. [License and credits](https://durian.blender.org/about/) · [Full film](https://download.blender.org/durian/movies/) · [Trailer](https://download.blender.org/durian/trailer/)

Cold timing is audio decode, the first full-input ASR pass and one 12-second Balanced export. The export starts half a second before the first recognized word, subject to the source ending, so identical native word timestamps select the same start. All outputs use 1080×1920, the same source, layout, CRF, caption style and words. Render timing includes cut, burn-in, encode, cover and basic inspection. Translation and provider downloads are outside the measured local workload. Neither checkout spent tokens on a paid AI call in this benchmark.

| Input | Audio before → after | ASR before → after | Export before → after | Cold total before → after | Change |
| --- | ---: | ---: | ---: | ---: | ---: |
| Short (26 s) | 0.16 → 0.14 s | 10.56 → 9.41 s | 8.34 → 9.57 s | 19.05 → 19.12 s | 0.4% slower |
| Medium (75 s) | 0.34 → 0.30 s | 20.78 → 19.61 s | 19.94 → 18.70 s | 41.05 → 38.61 s | +5.9% faster |
| Long (240 s) | 0.81 → 0.73 s | 58.19 → 56.51 s | 18.30 → 17.80 s | 77.30 → 75.04 s | +2.9% faster |

Positive change denotes a shorter time. The cold 30–50% target was not reached. Cold ASR and encoding dominate these samples. The micro-optimizations cut repeated passes and memory copies but leave native model inference and the same-quality fixed encoding in place. An experimental crop-before-scale variation was discarded after the medium comparison produced SSIM 0.930052 due to framing differences.

Decoded frame hashes, decoded audio hashes, full timestamped transcript hashes, selected time range and output dimensions match in all three final pairs. FFmpeg decoded the full exports with `-xerror`. See the JSON and fidelity results in [followup-data](followup-data/). x86_64 results do not measure Oracle performance.

A separate native upload integration processed the same 52-second trailer twice using the local fallback. The second upload reused its transcript. It skipped 16.709 seconds of measured first-run ASR and both jobs produced two Ready 1080×1920 clips. The 48 and 34 second job totals include concurrent host work. See [native pipeline log](followup-data/native-pipeline.log).

## Reproduce from an isolated checkout

```bash
python tools/benchmark.py /path/to/speech.mp4 --checkout /path/to/baseline --models /path/to/models --cores 2 --seconds 12 --layout crop --runs 1 --cold-only --real-captions --output /tmp/peakcut-before.mp4
python tools/benchmark.py /path/to/speech.mp4 --checkout /path/to/release --models /path/to/models --cores 2 --seconds 12 --layout crop --runs 1 --cold-only --real-captions --output /tmp/peakcut-after.mp4
```
