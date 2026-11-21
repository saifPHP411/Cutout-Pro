# PeakCut Pro continuation — verification record

Base Git commit: `611611d3d4e0221da6257134e749a5cd2ca16d7b`
Branch: `feature/peakcut-pro-windsor-followup`

## Implemented in this continuation

- Read-only Windsor.ai YouTube performance import for clips actually published through PeakCut, with an explicit UI button.
- Strict server-configured YouTube channel binding, configurable Windsor account selector, and verification of both channel and video IDs in the returned data.
- Fixed seven-complete-Pacific-day cohort with a three-day reporting delay; numeric and structural validation fails closed.
- New observations use an independent `windsor_youtube` cohort in the learning engine, preserving chronological separation and manual activation of ranking changes.
- Disabled-by-default background import (`PEAKCUT_WINDSOR_AUTOPULL=1` to opt in). Each owner must opt in to learning, a valid publishing record must exist, and the job must have rated, completed clips. Automatic imports are bounded, never overwrite an existing measurement, and do not publish, delete, or edit videos.
- Sanitized API errors and avoided logging httpx INFO-level URLs, because the Windsor API authenticates through a query parameter.
- Updated Oracle staging Compose and Termux setup documentation, with distinct stage settings.

## Verification performed in this environment

- `python -m unittest tests.test_windsor -q`: **11 passed** (synthetic API responses only, no real key).
- `python -m unittest tests.test_campaign_learning tests.test_windsor -q`: passed (before the eleventh case was added; final Windsor-only suite is 11).
- `python -m compileall -q studio tests`: passed.
- Extracted inline JavaScript from `web/index.html` and ran `node --check`: passed.
- Parsed `docker-compose.stage.yml` with PyYAML: passed structural parse (not Docker Compose verification).
- `git diff --check`: passed.
- Full `python tests/run.py` invoked in an isolated temporary server environment. 36 groups were attempted: 33 passed, 3 failed/blocked:
  1. CPU saturation test measured 81% busy CPU against a configured 82% admission threshold (test load mismatch; 8 queue assertions passed).
  2. Speech model test could not import `sherpa_onnx` in this tool environment.
  3. Live recording test could not import `yt_dlp` in this tool environment.
- `PEAKCUT_TEST_CONTAINER=1 python tests/test_load_queue.py <checkout>` subsequently passed all 7 deterministic queue checks.
- Installing missing packages was attempted, but package-index network/DNS was unavailable. No installation succeeded.

## Still blocked or unverified

- Live Windsor.ai credentials and data: **not run**. The Windsor connector linked to ChatGPT is NOT a credential for the app, and the channel has no eligible published Shorts available for verification.
- Live YouTube/TikTok/Instagram retention, live Gemini calls, native ARM64 Docker build, CPU/RAM measurements on Oracle, genuine A/B test results, and safe-zone validation against real mobile overlays are not validated here.
- No new cold-video 1080×1920 performance claim is made; the prior matched study achieved -0.4% / +5.9% / +2.9% on x86_64, not a 30–50% improvement.
- The existing Oracle production version was not accessed or modified; no changes were pushed to GitHub.

## Deployment decision

**STAGING CANDIDATE ONLY.** Do not promote to production without a disposable Oracle ARM64 stage, a real 1080×1920 media test, a cancellation and restore drill, and verification of any active channel integration. Turn off `PEAKCUT_WINDSOR_AUTOPULL` until the owner explicitly wants automatic read-only analytics.
