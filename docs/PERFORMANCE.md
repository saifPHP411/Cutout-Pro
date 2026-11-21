# PeakCut Pro benchmark

## Environment and inputs

Baseline `39c32a878648619e981b57af608ba4571a82247f`. The tested Pro revisions are recorded in each [JSON result](benchmark-data/). Linux x86_64 development host, FFmpeg 7.1.5, two CPU cores pinned with `taskset`, and the same installed English Parakeet model with one recognizer copy and two inference threads. The Oracle VM, ARM64 performance, remote downloader throughput, and paid Gemini latency were outside this measurement.

Balanced used the same source, time range, ASS word cues, output dimensions, frame rate and CRF in both checkouts. The benchmark supplies the same scripted caption cues to each encode so differences in clip selection and speech results cannot change workload size. The source video and audio are real. Each short/trailer number is a median of three independent processes. The long result is one pair and is sensitive to load.

- Short input, the upstream `demo-bounce.mp4` at 4.60 s; exported the entire sample.
- Trailer, *Sintel* by Blender Foundation, 52.21 s; inspected and transcribed the full input and exported its first 8 s. [Blender Foundation license and credits](https://durian.blender.org/about/)
- Long input, *Sintel* by Blender Foundation, 888.03 s; extracted full-length audio, used its first 100 s for the ASR comparison, and exported the first 8 s. The measured ASR sample returned zero words in both versions. [Blender Foundation downloads](https://durian.blender.org/download/)

## Balanced, same output profile

A positive change means less time or lower RAM. Cold pipeline sums audio extraction, the first ASR run and one export per trial. Native ASR comparisons include model loading in the cold column and a second pass with the recognizer already loaded for warm ASR.

| Input / metric | Before | After | Improvement |
| --- | ---: | ---: | ---: |
| Short / Audio extraction | 0.082 s | 0.080 s | 1.7% |
| Short / Cold transcription | 4.510 s | 4.341 s | 3.7% |
| Short / Warm transcription | 1.687 s | 1.685 s | 0.1% |
| Short / Rendering | 4.407 s | 4.330 s | 1.7% |
| Short / Cold pipeline | 8.995 s | 8.847 s | 1.6% |
| Short / Peak RSS | 897.4 MiB | 903.6 MiB | −0.7% |
| Trailer / Audio extraction | 0.183 s | 0.177 s | 3.2% |
| Trailer / Cold transcription | 13.366 s | 15.166 s | −13.5% |
| Trailer / Warm transcription | 10.048 s | 10.299 s | −2.5% |
| Trailer / Rendering | 8.513 s | 8.328 s | 2.2% |
| Trailer / Cold pipeline | 22.062 s | 23.671 s | −7.3% |
| Trailer / Peak RSS | 1316.8 MiB | 1304.9 MiB | 0.9% |
| Long / Audio extraction | 3.849 s | 5.207 s | −35.3% |
| Long / Cold transcription | 23.336 s | 24.946 s | −6.9% |
| Long / Warm transcription | 18.200 s | 19.427 s | −6.7% |
| Long / Rendering | 12.196 s | 10.831 s | 11.2% |
| Long / Cold pipeline | 39.380 s | 40.984 s | −4.1% |
| Long / Peak RSS | 1470.1 MiB | 1352.8 MiB | 8.0% |
| Long / CPU utilization, percent of two allocated cores | 94.6% | 93.5% | — |
| Long / End-of-run output disk usage, including PCM and sidecars | 28.64 MiB | 28.64 MiB | 0.0% |
| Download time | Not measured | Not measured | — |
| Paid Gemini analysis time | Not measured | Not measured | — |
| Paid AI requests / tokens | 0 / 0 | 0 / 0 | — |

Peak RSS is the largest single-process high-water mark reported by Linux `getrusage`. It is not the peak RSS sum of all processes. The short and trailer baseline CPU samples included Python startup instrumentation, so only the comparable long CPU pair is shown.

Balanced did not reach the 30–50% cold processing target. Its rendering times improved on the three inputs, but slower audio extraction and ASR offset them on the larger inputs. The measured runs cannot determine how much of the ASR variance came from ambient system load. Improvements from network transfer and paid API reuse remain unmeasured.

## Preview and quality

Fast exported the same short range in **0.900 s** on the Pro revision, compared with **4.330 s** for Balanced. This is a 79.2% shorter render at **540 × 960** with a faster, lower-resolution encode. Balanced and Quality keep **1080 × 1920** for portrait output.

All six Balanced files decoded with FFmpeg `-xerror` and contained matching dimensions, audio streams and expected durations. The decoded audio hashes matched for all three pairs. Frame SSIM against the baseline exports was 0.999897 for the short, 0.998059 for the trailer, and 0.998698 for the long. Native ASR transcript and timestamp hashes matched in all comparisons. These checks establish file and signal fidelity for the sampled exports. Human review and paid-model editorial comparisons remain separate.

## Repeat upload with the transcript cached

A separate integration run sent the same 52.21 s trailer through the normal upload endpoint twice, using the same settings and the local ranking fallback. End-to-end job time was **35 s on the first upload** and **19 s on the repeat**. The first native ASR stage took 15.654 s. The second job recorded no ASR stage because it reused the owner's saved transcript. Both runs produced two Ready 1080 × 1920 MP4s and the same 30 timestamped words. This 45.7% reduction applies to this repeat workload. The first run loaded the model, so the standalone warm ASR numbers above are the better estimate of the decode work skipped when a model is already resident.

## Reproduce

Place the same licensed input and installed speech model outside both worktrees. Use the Pro benchmark script for both checkouts. Example for the trailer:

```bash
git worktree add --detach ../peakcut-before 39c32a878648619e981b57af608ba4571a82247f
python tools/benchmark.py /path/to/sintel-trailer.mp4 --checkout ../peakcut-before --models /path/to/models --cores 2 --seconds 8 --output /tmp/before.mp4
python tools/benchmark.py /path/to/sintel-trailer.mp4 --checkout . --models /path/to/models --cores 2 --seconds 8 --output /tmp/after.mp4
ffmpeg -nostats -v info -i /tmp/before.mp4 -i /tmp/after.mp4 -lavfi ssim -an -f null -
```
