# PeakCut Pro changelog

## Release follow-up

- Added sampled face tracking, platform-specific caption insets and reference-based watermark review.
- Added owner-scoped transfer caching, aggregate chat inputs, partial-phase ETA and audio pipeline optimizations.
- Added opt-in source-level A/B assignments, a manual publishing gate for experiments and a connected-channel YouTube Analytics import.
- Added encrypted maintenance backups, isolated verification and separate staging Compose configuration with an ARM64 CI job.

## Prior release

- Added owner-scoped pause, resume, stop, retry, stop and delete, and stage logs for video jobs. Saved stop requests survive restarts and child downloads and encodes are terminated before removal.
- Limited processing to one video job and one encoder at a time by default. Added per-user queue priority and free-space and download limits.
- Reused the original short video as its audio source, reused downloaded source files when safe, and added owner-scoped content-addressed transcript and analysis caches.
- Added Fast, Balanced and Quality exports, three aspect ratios, layout presets, and a CPU motion-aware crop. Tightened the existing Facecam detector to reject stripes and near-black noise.
- Ranked a batch of locally discovered candidates, retained separate editorial ratings, and filtered overlapping or incomplete selections.
- Added timed caption edits, caption appearance controls and original speech captions as the default for new video jobs.
- Added automatic export checks, user-requested full checks, and a publishing gate for failed or unapproved clips.
- Added a burgundy dashboard, clip browsing, campaign rules, verified financial records, optional Telegram job alerts and a retrospective ranking comparison with rollback.
- Added isolated PostgreSQL backup verification instructions, a reproducible benchmark and additional automated tests.


## Follow-up: Windsor.ai read-only analytics integration

- Added owner-scoped, channel-bound Windsor.ai YouTube performance import for
  published clips through a new button, plus optional disabled-by-default
  seven-day cohort auto-import with a one-day retry limit.
- Kept imported observations separate by provider in the opt-in ranking lab;
  protected existing manual observations from automatic replacement.
- Added API error redaction, explicit channel verification, ten offline tests,
  and deployment guidance for a separate staging configuration.
- No Oracle instance, live video, user account or YouTube channel was changed.
