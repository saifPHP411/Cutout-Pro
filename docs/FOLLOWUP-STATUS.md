# Release follow-up status

Starting revision: `e27f56c` on `feature/peakcut-pro-performance-ai`.

| Requirement | Status | Evidence or remaining limit |
| --- | --- | --- |
| Matched 1080×1920 cold-performance study | Implemented & Tested | New speech-bearing short, medium and long sources. See [raw results and quality checks](FOLLOWUP-PERFORMANCE.md). |
| ASR and audio pipeline | Implemented & Tested | Short PCM bypasses the intermediate WAV; ASR only scores potential quiet-cut windows; audio hashes reuse transcripts across remuxes. PCM equivalence and native repeat export checked. |
| Dynamic person/face reframing | Implemented & Tested | Sampled CPU frontal-face tracking and interpolated FFmpeg crop. Measured movement and decoded the rendered portrait MP4. Weak, obstructed or ambiguous tracks fall back to a fit layout. |
| YouTube Shorts, TikTok and Reels safe zones | Implemented, Not Fully Verified | Separate guides constrain captions and the tracked face. Verified renderer outputs; live platform overlays on an Android device have not been compared. |
| Cross-job download cache | Implemented, Not Fully Verified | Same URL, format and interval reuse private downloaded copies. Tested owner isolation, expiry, eviction and independent job files; live yt-dlp transfer across a supported network was not tested. |
| Job ETA | Implemented & Tested | Shows a phase estimate once transcription progress or a completed render provides a useful sample. Network and queue uncertainty is left without an ETA. |
| Logo and watermark review | Implemented & Tested | Signed-in owners can supply a known PNG or JPEG reference. A real encoded test clip was detected. Possible/repeated matches need manual review before publishing. Unknown logos are not classified. |
| Recorded chat signals | Implemented & Tested | Timestamped aggregate counts, owner confirmation, no usernames or raw chat text. Tested the import in the mobile UI and time offset in the ranking signal. |
| Safe admin backup and recovery | Implemented & Tested | Maintenance-only encrypted archive, separate temporary verification, tampering/path checks and an isolated PostgreSQL 16 restore. A host recovery command stages the files outside live storage. |
| Opt-in A/B and publication gate | Implemented & Tested | Source-level repeatable groups, frozen eligible weight sets, explicit clip approval and posting; tests used fixture observations with zero publications. |
| Official YouTube performance import | Implemented, Not Fully Verified | Owner-scoped published-post lookup, fixed seven-day dates and OAuth permissions. Provider-shaped responses tested. Connected Windsor.ai YouTube reads for 7 and 30 days returned 0 rows. |
| Manual performance import | Implemented & Tested | Report attribution and dates in the owner’s clip editor. Suitable for entering provider exports, including Windsor.ai and platforms without a retention adapter. |
| Learning and experiment evaluation | Implemented & Tested | Provider cohorts stay separate; earlier observations must be available before the holdout starts; source groups are disjoint; challenger activation remains manual. |
| Improved Gemini Smart Clipper | Implemented, Not Fully Verified | Batched candidate ranking now incorporates surprise/funny cues and aggregate chat evidence. Mock timeout, invalid output and cancel tests pass. A live Gemini call lacked an authorized staging key. |
| Pause, Resume, Stop, Stop & Delete, Retry | Implemented & Tested | Existing real process and restart checks plus a test that Pause preserves a completed output and mobile actions. |
| ARM64 build and Oracle operation | Blocked | Compose validated and Python 3.11 ARM64 wheels resolved, including OpenCV and cryptography. A native GitHub ARM64 workflow is included and still needs an authorized runner. This work did not access the VM. |
| Live TikTok, Instagram or direct Windsor retention adapter inside the app | Not Started | Manual entry is available. No published-clip retention data was available for verification through these provider paths. |
| Real A/B result and live publishing approvals | Blocked | Requires published clips and consented channel measurements. No channel data or settings were written during the release work. |

The application, benchmark artifacts and QA evidence describe a staging candidate. Production acceptance still requires a native run on the target VM.


## Continuation work after original release (`feature/peakcut-pro-windsor-followup`)

- Implemented a **read-only Windsor.ai YouTube adapter** with exact
  connected-channel and video ID matching, fixed seven-day Pacific cohorts,
  three-day reporting delay, account filter and validated numeric metrics.
- Added a manual clip-editor import button and a disabled-by-default automatic
  daily polling path. No API key is bundled or sent to ChatGPT. Official and
  Windsor-sourced data remain distinct learning cohorts.
- Added no-network ownership, response validation, access-control,
  credential-redaction and opt-in polling tests in `tests/test_windsor.py`.
- This is **implemented and tested with fixtures, not verified live**. The app
  still requires an authorized independent Windsor API key, a matched PeakCut
  YouTube OAuth account, and a published clip sufficiently old for a stable
  reporting cohort. The linked ChatGPT Windsor plugin is not an app credential.
- Live Instagram/TikTok retention adapters, native ARM64 Docker build, Oracle
  end-to-end deployment, real A/B outcomes and a 30–50% cold processing speed
  improvement are **not claimed complete**.
