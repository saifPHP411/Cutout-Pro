# Original YouTube Autopilot — zero-paid-tools science channel (staging)

**Chosen topic:** Original Arabic space/science explainers, starting with
why sunlight is delayed, the Moon's light-travel time, Venus's slow rotation,
and Mars gravity. Episodes are authored here, verified against specific NASA
Science source pages, and visualised using original vector-like Pillow
drawings. No Twitch or other creator's copyrighted clips, no stock footage,
no paid templates, no paid LLM API, and no ads/sponsorship campaigns.

**Chosen production tools:** PeakCut Pro (existing code + OAuth),
Pillow / FFmpeg, offline **Piper** TTS with a separately licensed Arabic
voice, and YouTube Data API v3's free default quota. This is intended to run
on the existing Oracle 2 OCPU/12 GB ARM64 machine, subject to final on-device
quality checks. Local FFmpeg/ASR and Oracle always-free allocation are not a
guarantee that any provider will never charge: stay inside your free
allowances.

## Explicit limitations (do not misrepresent this as guaranteed profit)

- **No earnings before YouTube Partner Program acceptance.** Jordan is
  eligible. Under the current rules, new creators need 1,000 subscribers
  and 10 million qualified Shorts views in 90 days or 4,000 qualified
  public long-video watch hours in 12 months for ad revenue sharing.
  YouTube announced that **on 1 Feb 2027** new ad/YPP entry thresholds
  become 1,000 subscribers plus **20M Shorts views/90 days OR 8,000 watch
  hours/365 days**. To earn from the Shorts Creator Pool each month,
  maintain **10M qualified Shorts views in the trailing 90 days**.
  Rules: https://support.google.com/youtube/answer/12843009
- Purely repetitive, generic AI-generated or mass-produced formats can be
  ruled ineligible even when everything is self-produced. Each short must
  provide real distinct explanation and value. Source:
  https://support.google.com/youtube/answer/1311392
- **Four source-checked scripts are a pilot, not an endless content
  library.** The queue stops when used. It does not endlessly recycle
  facts or invent claims. Continuous original science research and a
  separate original long-form series remain future work.
- Piper is open source, but **voice model licenses vary**. Example
  MIT-labelled Arabic Emirati female ONNX model:
  https://huggingface.co/vadimbelsky/arabic-emirati-female-piper
  Download the model + JSON manually and verify that its voice/dataset
  license allows commercial use. The ar_JO Kareem model card does **not**
  explicitly state a dataset license, so don't assume it's cleared.
  Avoid Meta MMS-TTS Arabic for commercial output: CC BY-NC 4.0.
- Narration audio must actually sound intelligible in Arabic on the chosen
  local model. Do not upload the generated CI's synthetic/silent test WAV.
- **No actual upload has been made** and YouTube YPP status is not known.

## Review / test a preview

1. In PeakCut **Settings → Connected apps**, configure the Google OAuth
   client, then connect the intended YouTube channel once in the browser.
   The OAuth app must be in an appropriate production state; a Google
   external OAuth app in *Testing* typically gets a refresh token expiring
   after seven days. Source:
   https://developers.google.com/identity/protocols/oauth2
2. Install the free `piper-tts` package (or a local Piper executable) and
   obtain **properly commercially licensed** Arabic ONNX + `.onnx.json`
   files. Optional package with ARM64 Linux wheels:
   `pip install piper-tts`.
3. Run `python tools/original_youtube_autopilot.py plan` without any key.
4. Render a **local-only** pilot:
   `python tools/original_youtube_autopilot.py preview --voice-model /data/voices/arabic.onnx --output-dir /data/preview-science`
   Check narration, scientific claims, originality, captions/text direction,
   bitrate, volume, and final 1080×1920 H264/AAC output.
5. Run local or ARM64 tests before enabling unattended publication:
   `python tests/test_original_youtube.py`, `python tests/run.py`.

## One-time publish enablement (only after review)

Set these on **your own secure Oracle service**, never in a public repo:
```ini
PEAKCUT_ORIGINAL_AUTOPOST=1
PEAKCUT_ORIGINAL_VOICE_LICENSE_APPROVED=1
PEAKCUT_ORIGINAL_VOICE_MODEL=/data/voices/arabic.onnx
PEAKCUT_ORIGINAL_USER_ID=<actual-owning-PeakCut-user-id>
PEAKCUT_ORIGINAL_YOUTUBE_ACCOUNT=yt_<actual-channel-id>
```
Manual one-shot command:
`python tools/original_youtube_autopilot.py publish`.

When PeakCut starts, its opt-in scheduler automatically checks once each day
at **11 AM Asia/Amman** (overridable with `PEAKCUT_ORIGINAL_PUBLISH_HOUR_AMMAN`).
No separate cron, subscription or GitHub Action is required. To run one check
manually, use `python tools/original_youtube_autopilot.py publish` after
configuring the same environment and OAuth. The production service must
restart after those settings are set. The renderer creates source-
original 1080×1920 Shorts and posts to the *explicit connected YouTube
account* in the Education category. No campaign, third-party clips or
Windsor.ai are required. It never chooses a random remote clip.

It refuses to post if the voice license is not explicitly cleared, the
owner/account does not match, the voice renderer fails, the video export
fails, or that UTC date has already published an episode. The upload
ledger has an `upload_unknown` reservation to avoid duplicate uploads
after a timeout; reconcile by visiting YouTube Studio rather than
blindly retrying it.

**Important:** the old PeakCut upload queue remains unchanged for clipping
workflows. This autopilot is separate and cannot upload anything until
an owner opts in. Its status is *staging, not deployed*.

## Next production acceptance gates

- Real local commercial-use voice and checked Arabic pronunciation.
- Two fresh Shorts' acceptance by human viewers and distinct narratives/
  motion graphics across topics, not mass-produced template filler.
- A separately authored 5–8 minute weekly long video series (not automatic
  stitched Shorts), to diversify the YouTube monetization path.
- End-to-end cold ARM64 time/RAM/quality, YouTube OAuth/API authorization
  and permission checks, review of metadata, and explicit publish enable.
- The connected YouTube channel's monetization tab and AdSense review
  can only be completed by the channel owner.
