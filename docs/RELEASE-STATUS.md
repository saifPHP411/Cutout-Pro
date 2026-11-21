# PeakCut Pro release status

## Implemented & Tested

- Owner-scoped job controls, process group cancellation, checkpoint resume, safe stop and deletion, non-recovery of cancelled jobs in a fresh process, and on-screen stage logs. The mobile test exercised the real app endpoints using an emulated Pixel 7 viewport.
- FFmpeg concurrency and CPU bounds, bounded resumable media subprocesses, storage checks, transcript and analysis caches, three export profiles and stage timing records. The project has a native CPU pipeline check with a licensed input.
- Component-based Smart Clipper ranking using transcript context, loudness changes and reaction cues. Tests cover batching, weak candidates, duplicate suppression and malformed AI answers.
- Layout presets, fixed-in-clip CPU motion focus, the existing Facecam check, three aspect ratios, editable caption words and timings, a creator spelling dictionary and export inspection.
- Campaign rules and payment evidence, separate opt-in Telegram job alerts, chronological ranking comparison, version selection and rollback. Learning tests use synthetic observations to verify the algorithm; the release contains no user performance measurements.
- PostgreSQL 16 dump and restore was checked with two isolated development instances. Compose v2.39.4 accepted the configuration and keeps the database unbound and the app bound to loopback. Python 3.11 aarch64 wheels resolved successfully for the pinned requirements.

## Implemented, Not Fully Verified

- Native Docker ARM64 runtime and resource ceilings are ready for staging. The image build, live SSH tunnel, long-run memory stability and container restart on Oracle need a deployment test. Separate backup helpers were shell-checked; the actual Docker commands need verification against the staging Compose project.
- Gemini, rate-limit handling, request cancellation and response parsing were tested with fixtures. Client cancellation closes in-flight transport and prevents more calls; a provider may still process a request it already received.
- Stop settles local native transcription after the current decode batch and can wait on model initialization. Pause reuses completed media/checkpoints and may repeat the active block. Live streams retain the existing live stop workflow. A separate in-progress single-clip re-export must finish before its parent can be deleted.
- Basic export checks run on every new render. Quality mode and the full-check action additionally test decoding, black/frozen frames and low audio volume. Editorial endings, audio distortion and branding requirements still need review.
- Arabic timing and rendering were verified at caption level, with the existing Arabic ASR model support retained. End-to-end Arabic transcription on Oracle has not been verified.
- Telegram sending, production-scale budgets, provider approvals and campaign payout records require real owner configuration. Dark Dashboard Pro was exercised in an emulated mobile browser.

## Blocked

- Production/staging rollout on the Oracle VM and a Draft PR. This work had neither an authorized Oracle endpoint nor GitHub write permission to an available PeakCut repository.
- Cold performance on Oracle, real network download timings, and paid Gemini cost/quality comparisons need live authorized infrastructure and data.

## Not Started

- Randomized live A/B publishing, chat ingestion for recorded videos, persistent cross-job URL-download deduplication, platform-specific safe-zone presets and dynamic person/object tracking. Local motion focus uses a stable crop for a clip.
- Automatic detection of logos/watermarks and collection of payout or retention data from platform APIs. The current campaign workflow requires authorized manual evidence.
- Per-job ETA prediction and a browser backup/restore console. The existing backup scripts and logs are available independently.
