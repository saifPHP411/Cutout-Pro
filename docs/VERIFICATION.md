# Verification record

The isolated `python tests/run.py` run passed all 34 groups. After the final Facecam refinement, its 32 checks also passed with two CPUs pinned.

## Additional verification

- The real dashboard passed the Chromium Pixel 7 viewport test, including Pause, Resume, Stop, Retry, Stop & Delete, logs, campaign creation and Analytics.
- The native upload pipeline ran twice on the 52-second *Sintel* trailer. Both jobs produced two Ready portrait MP4s. The same transcript was reused on the repeat. See [performance data](PERFORMANCE.md).
- Two isolated PostgreSQL 16.16 instances verified a custom-format dump and restore of the PeakCut schema and a test account. A separate app-data tar archive also restored and matched its original files.
- Docker Compose v2.39.4 accepted the configuration with a temporary environment. The parsed config kept port 8765 on loopback and published no database port. ARM64 Python 3.11 wheels resolved for the application's pinned dependencies.

## Suite groups

- permissions
- abuse and limits
- queue under load
- a day-long video in little memory
- deleting and bulk actions
- job controls and process cancellation
- cache, AI timeouts and playable exports
- ranking, caption edits and export quality
- campaign gates and chronological learning
- saving a video when Windows says busy
- your own big text on the clips
- hashtags made without an AI
- big uploads in pieces
- AI services
- no sign-in, and the AI key from the dashboard
- Hindi and Urdu captions in Roman letters
- turning captions off and back on
- connecting YouTube, Instagram and TikTok
- the new-version notice
- no limits by default
- captions never overlap, stuck clips, sentence endings
- ad breaks and frozen screens
- answers that are not quite right
- waiting out AI rate limits
- knowing which game a stream is
- finding the facecam in a gaming stream
- recording time: the buffer knows where each piece sits
- fresh installs: ffmpeg, the speech model, AI errors
- the storage folder: full drive, open, move
- security: visitor address, email addresses, Google sign-in, prompts
- long logs, the machine monitor, failed recordings, admin day totals
- speech models for other languages: Arabic, downloads, the second machine
- post text: write again with AI, edit, post without re-making
- live recording stops and empty stream URLs
