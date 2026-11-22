# PeakCut Pro: open-source clipper audit and integration guardrails

Date: 2026-11-22
Target: **Oracle ARM64, 2 OCPU, 12 GB RAM**, 1080x1920 portrait output, one heavyweight job at a time.
Working branch: `feature/peakcut-pro-burgundy-studio-ui`
Production policy: **do not deploy to Oracle or touch live volumes/secrets** without staging acceptance.

## Scope and evidence

This is a *repository and license review*, not a head-to-head rendered video benchmark.
No source files from competing clippers were copied into PeakCut; the improvements below are
original changes to the existing PeakCut code and UI. Repo features are reported as vendor
claims unless explicitly exercised through PeakCut's own tests.

| Project | Repository | Current license observed | Worth evaluating | Fit / caution |
|---|---|---|---|---|
| SupoClip | https://github.com/FujiwaraChoki/supoclip | SupoClip Noncommercial Source License 1.1 (earlier AGPL grants preserved) | UX, ranking evidence, post-publication feedback | **Do not copy current source**; monetized self-hosting is only allowed for a qualifying individual creator on their own channels. Company/client/service use requires written permission. API transcription provider can incur cost |
| OpenShorts | https://github.com/mutonby/openshorts | MIT (core) | Per-speaker frame layouts, editor workflow, AI hook/scoring | Project recommends at least 8 GB RAM; self-hosted GPU/CPU speeds are not directly comparable to Oracle ARM |
| Medusa Clip | https://github.com/Edualnog/medusa-clip | AGPL-3.0 | Gameplay-specific audio+motion+frame triage, facecam composition | Desktop/Electron packaging; license obligations and local CPU cost need review before any integration |
| Clipify | https://github.com/Siraj-zaki/clipify | MIT | Speaker-aware crop, transcript editor, word captions | Podcast-focused; does not establish gameplay highlight quality |
| Podcast Shorts Factory | https://github.com/krakonjac300-pixel/podcast-shorts-factory | MIT | Quality-control gate, scheduled pipeline, analytics feedback | Multi-agent descriptions are not evidence that each agent improves outcome; some automation assumes credentials and API publishing permissions |
| jevcut | https://github.com/VBS2004/jevcut | MIT | Candidate boundary enumeration and standalone/payoff checks | Transcript-based; misses silent visual events and needs its own paid-model integration |

Before porting code: inspect the **specific file** for license notices, dependencies,
upstream changes, compatibility with PeakCut's codebase and license, and attribution.
Keeping a repository's top-level license is necessary but not sufficient for every asset.

## What PeakCut already has

- `studio/brain.py`: candidate ranges, sentence-end snapping, Gemini/LLM ranking,
  overlap rejection, context and ending-quality ratings; audio loudness + optional
  consented aggregate chat counts. The newest patch prevents snap padding from
  exceeding the clip's configured maximum.
- `studio/render.py`, `studio/reframe.py`, `studio/facecam.py`: FFmpeg export,
  facecam tracking and fallback on low-confidence detection.
- `studio/quality.py` and export gates: static/quality checks.
- `studio/learning.py`, `studio/analytics_import.py`, `studio/windsor.py`:
  isolated performance cohorts and opt-in learning. Does not establish real
  effectiveness until linked channels have attributable published clips.
- `web/index.html`: interactive studio without a frontend build. The other
  public/admin pages previously had different (orange/light) visual styling.

## Changes made in this branch

1. A consistent, dark burgundy/purple visual language across signed-in studio,
   landing page, sign-in, password reset, administration and legal pages.
2. Higher-contrast foregrounds on panels (separate from dark text on pink actions),
   42–48 px interactive mobile elements, clearer focus outlines, and safe-area spacing.
3. `_snap` respects maximum duration *after* start/end audio padding is applied,
   with a regression check in `tests/test_stuck.py`.
4. `tests/test_ui_theme.py` validates the six-page theme contract from source,
   and the native ARM64 CI workflow includes this feature branch.

## Candidate architecture (not implemented yet)

**Phase A — cheap CPU triage**: decode once; independently sample audio energy/pauses,
speech timeline, sampled-frame motion, and chat counts when consented. Cache deterministic
signals by content hash. Motion is *evidence for review*, never an invented description.

**Phase B — contextual selection**: Gemini ranks a bounded shortlist with visual
keyframes only if the user enabled visual analysis. Evaluate against transcript-only
baseline; enforce rate limits, redaction, prompt safety and cancellation.

**Phase C — boundaries and QA**: enumerate sentence, speaker-change and silence edges;
check setup, payoff, source rights and no overlap; reject unusable clips. Existing
clip review and approvals remain intact.

**Phase D — deliver & learn**: render 1080x1920 once, validate black/frozen frames,
subtitle safe areas, audio and correct durations, then allow manual publication.
Feed genuine published results into separate observational cohorts. Never automatically
turn on learning, posting or ingest external creator analytics.

## Acceptance gates before Oracle deployment

- Native ARM64 container and application tests pass **on the final head SHA**, not
  on a preceding commit; include a mobile browser QA run and contrast check.
- Three real speech-bearing videos (short/medium/long), matched CPU budgets and
  1080x1920 output, compare baseline with candidate and verify decoded fidelity.
- Add **real gameplay VODs** with and without speech, permission to use them, and
  manually annotated highlight boundaries. Measure precision/recall, false positive
  rate, reviewer accept rate, p50/p95 runtime and memory by stage.
- Validate keys and Windsor.ai channel data in an authorized staging environment
  only. No test response or placeholder values may be described as live analytics.
- Keep production untouched; staging Compose uses separate volume, database,
  credentials, port and no automated posting.
- The prior matched cold benchmark reached only +5.9% (medium) and +2.9% (long)
  on x86_64, with the short sample slightly slower. **Do not claim a 30–50%
  improvement until matched measurements on Oracle show it.**
