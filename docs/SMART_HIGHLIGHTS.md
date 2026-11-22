# Smart Highlights — optional gameplay discovery

Target branch: `feature/peakcut-pro-smart-highlights`.

These changes are original implementations inspired by the **ideas** of Medusa Clip,
OpenShorts, SupoClip and other clipper tools. No competitor source was copied.
This is a staging feature; no changes have been deployed to Oracle.

## What is new

1. **Visual Highlights** checkbox in the video's advanced Options.
   - OFF by default, so prior behavior and CPU costs remain unchanged.
   - When an actual local video is available, FFmpeg samples 1 frame/second,
     scales to 160 pixels wide, computes a cheap difference between samples,
     and reports frame-change spikes for each processed 10-minute block.
   - Sampling is bounded to 10 minutes per call, 150 seconds maximum execution
     per call. No new model or paid AI service is needed.
   - The sampled frame-change signal can improve the shortlist order **only
     alongside speech-based candidate windows**, before Gemini ranks those
     windows. It does not recognize kills, laughter, people, logos or faces.
   - Audio-only progressive URLs and missing/corrupt local sources safely fall
     back to the pre-existing transcript+audio analysis.

2. **Explainable clip decisions** in the clip editor.
   - Shows measured cues: audio peak, spoken reaction, optional aggregate chat
     burst and optional sampled motion spike.
   - Reveals the AI's existing dimensions (opening hook, entertainment,
     payoff, context clarity, estimated retention, etc.).
   - Editorial scores are predictions, **not** measured views or retention.

3. **Clips library filters**.
   - Filter by all clips, score 80+, ready export, or needs review.
   - Sort by AI score, newest video, or shortest duration.
   - Browser-side sorting/filtering only. No server database change.

## Safety, cost and compatibility

- No new external package, API key, database migration or background worker.
- The motion path uses FFmpeg and respects the existing job cancellation.
- Source and per-owner jobs stay isolated; no raw video is transmitted to AI.
  Gemini sees transcript, timestamp intervals and the fact of observed motion,
  not the sampled image frames.
- Do not enable this on very long videos without measuring its actual CPU cost:
  low-resolution output still requires decoding source frames.
- No silent-only highlight finder is claimed; silent gameplay events still need
  a future video-semantic stage.
- Previously saved clips continue to display without `signals` or `ratings`.

## Test plan

- `python tests/test_smart_highlights.py`: motion sample fixture, invalid/missing
  file fallback, explicit boolean options, motion evidence and Gemini prompt.
- `python tests/test_ui_theme.py`: option control, evidence panel, filter/sort.
- `python tests/run.py`: full isolation tests plus native ARM64 GitHub Actions.
- On real authorized gaming VODs, compare reviewer acceptance, time saved,
  missed highlights, false positives, p50/p95 latency and peak RSS with option
  on/off on the target 2 OCPU / 12GB ARM64 server. This has **not** been measured.
- Manual browser tests on Android and desktop are still required before merge.

## Release gate

Keep this PR in draft and **do not merge or deploy to Oracle** until native ARM64
CI passes on the final SHA and visual QA/performance checks are documented.
