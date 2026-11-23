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
- **Four NASA-source-checked scripted episodes are the initial pilot.**
  When exhausted, the system can keep discovering **future Earth close
  approaches in official NASA/JPL numerical data** (free, no API key).
  Every event is unique, values are validated against the documented v1.5
  schema, and the astronomy narration is assembled from a meaning-preserving
  Arabic template. Events are capped at one new asteroid subject per 10 days.
  If there is no eligible or reliable event, **the publisher skips that day**.
  This is NOT a guarantee of daily or endless high-quality creative content.
  Source: https://ssd-api.jpl.nasa.gov/doc/cad.html
  Fully automated video production still needs editorial originality and
  science accuracy reviews before public use.
- The free default Arabic voice is downloaded automatically on first use
  from a **pinned Hugging Face model revision**: 
  https://huggingface.co/vadimbelsky/arabic-emirati-female-piper
  The model publisher declares **MIT**; the initial download is bounded,
  stored privately and its SHA-256 is recorded to detect subsequent changes.
  The model-card declaration is **not an independent audit of training data
  rights**, so independent rights and clear Arabic pronunciation are still
  required before commercial/public release. No voice weights are committed
  to GitHub. Custom voices require explicit rights confirmation.
  Avoid noncommercial weights such as CC BY-NC voices.
- Narration audio must actually sound intelligible in Arabic on the chosen
  local model. Do not upload the generated CI's synthetic/silent test WAV.
- **No actual upload has been made** and YouTube YPP status is not known.

## Phone-first dashboard

The sidebar now has a dedicated **YouTube Autopilot** page at
`/app/autopilot` instead of the old Campaigns navigation entry.
It reports your original pilot catalog, pending topics, authorized channel,
commercial voice readiness, schedule and upload history. Legacy campaign
records/APIs remain intact for users with old projects, but campaigns are
**not** used by this original-content automation.

## Review / test a preview

1. In PeakCut **Settings → Connected apps**, configure the Google OAuth
   client, then connect the intended YouTube channel once in the browser.
   The OAuth app must be in an appropriate production state; a Google
   external OAuth app in *Testing* typically gets a refresh token expiring
   after seven days. Source:
   https://developers.google.com/identity/protocols/oauth2
2. The container installs the free Piper engine. The selected built-in
   Arabic model and config are automatically fetched from a pinned,
   MIT-declared Hugging Face repository **after an authorized preview or
   publisher run**. With a custom model, set its absolute path and review
   its commercial rights separately.
3. Run `python tools/original_youtube_autopilot.py plan` without any key.
4. Render a **local-only** pilot (the voice downloads when missing):
   `python tools/original_youtube_autopilot.py preview --output-dir /data/preview-science`
   Check narration, scientific claims, originality, captions/text direction,
   bitrate, volume, and final 1080×1920 H264/AAC output.
5. Run local or ARM64 tests before enabling unattended publication:
   `python tests/test_original_youtube.py`, `python tests/run.py`.

## One-time YouTube authorization, entirely from the phone

After the creator has reviewed the initial spoken Short and verified
the channel's monetization eligibility and content rights:

1. In **Settings → Connected apps**, connect **your own** YouTube channel
   through Google's official OAuth consent page. YouTube must authorize
   access once; the program cannot bypass this or accept YPP/AdSense terms
   for the channel owner.
2. Open **YouTube Autopilot** in the main PeakCut sidebar.
3. Select that linked YouTube account and the preferred daily publishing
   hour in **Asia/Amman**. Read and check the explicit consent to unattended
   **public** uploads, then press **Enable automatic publishing**.
4. From that point the server remembers only the owner ID, connected channel
   ID, local hour and consent timestamp in private persistent storage. **No
   cron, shell, ENV edits, subscriptions or daily user clicks are required**.
   It schedules itself while the PeakCut server is online and its storage
   persists. The first action checks the current date, previous upload
   ledger and YouTube authorization.
5. To stop: open the same page, press **Disable automated publishing** and
   confirm. Revoking consent while the video renders prevents the new
   upload request from starting. Deleting a PeakCut account revokes and
   removes its saved autopilot consent.

Only the signed-in owner who connected a YouTube account can authorize that
account's automated uploads. The API rejects selecting another user's
channel, enabling without an explicit confirmation, or invalid schedules.
A synthetic end-to-end Chromium browser fixture verifies enable/disable
without any real YouTube credentials or network upload.

**For existing advanced deployments only:** The older
`PEAKCUT_ORIGINAL_AUTOPOST=1` plus owner ID / YouTube account environment
settings are still supported. An environment-managed owner cannot disable
the publishing override through the web UI: switch off the service flag
instead. Do not publish two conflicting configurations for the same owner.

The scheduler checks once daily at the chosen Amman hour, and posts a unique
original 1080×1920 H.264/AAC Short only if all source/voice/rights checks
pass. If NASA data are unavailable, the model breaks, Google OAuth expires,
the service is offline, or no suitable original topic exists, **the job
skips rather than recycling or uploading a fabricated/poor-quality video**.
The next eligible day is considered automatically. An ambiguous upload
outcome is stored as `upload_unknown` and is never blindly retried.

Because the publisher is a public-YouTube uploader, it remains **disabled
by default**; it is not enabled automatically by opening or using the app.
The existing clip queue/campaigns are independent and untouched.

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
