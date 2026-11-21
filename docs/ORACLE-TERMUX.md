# ARM64 staging with Android and Termux

The staging project binds only to Oracle loopback port 8766 and uses its own PostgreSQL database and Compose volumes. These commands do not update the production checkout, start a production container, import production data or enable automatic posting.

## Transfer the release from your phone

Replace `ORACLE_HOST` with your existing SSH host. Download `PeakCut-Pro-Continuation.zip` to your phone and use Termux.

```bash
termux-setup-storage
pkg install openssh unzip coreutils
unzip -p ~/storage/downloads/PeakCut-Pro-Continuation.zip PeakCut-Pro-Continuation.bundle > "$TMPDIR/PeakCut-Pro-Continuation.bundle"
scp -i ~/.ssh/oracle_peakcut "$TMPDIR/PeakCut-Pro-Continuation.bundle" ubuntu@ORACLE_HOST:~/PeakCut-Pro-Continuation.bundle
ssh -i ~/.ssh/oracle_peakcut ubuntu@ORACLE_HOST
```

In the SSH session, create a separate checkout and stage-only environment file. The generated password contains only hexadecimal characters, so it is safe in the Compose database URL. Add a separate Gemini credential only if you intend to test paid calls.

```bash
git clone -b feature/peakcut-pro-windsor-followup ~/PeakCut-Pro-Continuation.bundle ~/PeakCut-Pro-stage
cd ~/PeakCut-Pro-stage
git rev-parse HEAD
umask 077
if [ ! -e .env.stage ]; then
  { printf 'STAGE_POSTGRES_PASSWORD='; openssl rand -hex 24; } > .env.stage
fi
uname -m
docker compose --env-file .env.stage -f docker-compose.stage.yml config --quiet
docker compose --env-file .env.stage -f docker-compose.stage.yml up --build -d
curl -fsS http://127.0.0.1:8766/healthz
docker compose --env-file .env.stage -f docker-compose.stage.yml ps
```

In a separate Termux session, open the tunnel, then use `http://127.0.0.1:8766/app` in Android Chrome.

```bash
ssh -i ~/.ssh/oracle_peakcut -N -L 8766:127.0.0.1:8766 ubuntu@ORACLE_HOST
```

Create a test account. For the admin console, use the app's admin CLI from the staging service after registering the account.

```bash
docker compose --env-file .env.stage -f docker-compose.stage.yml exec app python -m studio.admin --help
```

Staging and production still share the VM's CPU and RAM. Schedule heavy staging measurements around the live service. The stage app limit is 8 GB and two CPUs and its DB limit is 1 GB. Run controlled trials with production load noted. Save container build logs, `docker stats --no-stream`, stage timers and a decoded 1080×1920 export.

## Verify and recover a browser backup

Make a consistent snapshot by setting `STAGE_BACKUP_MAINTENANCE=1` in the staging environment file and recreating only the staging app:

```bash
docker compose --env-file .env.stage -f docker-compose.stage.yml up -d --no-deps --force-recreate app
```

Open `/admin/backup`, enter a dedicated passphrase, and download the encrypted `.pcb`. Verify it from the same screen. The verifier authenticates the complete file, stages it in private temporary storage, and checks SQLite integrity or the PostgreSQL archive catalog. The browser backup has a 512 MiB limit. It includes application data and the database but excludes external host files, model files and regenerable caches. Keep secrets and the passphrase private.

For a recovery drill, install the branch's requirements in an isolated development environment and stage the encrypted archive into a new directory:

```bash
python tools/restore_backup.py /path/to/peakcut.pcb /path/to/new-private-recovery
```

Review the staged directory. Restore its PostgreSQL dump with `pg_restore --exit-on-error --no-owner --no-acl` into a fresh disposable database using a compatible or newer PostgreSQL client. Use a fresh app data volume or a new app data directory for the extracted `restore/data`. Confirm the restored account count, files, checksums and login. On SQLite, copy `restore/db.sqlite` into the isolated app data directory as `peakcut.db` before starting that instance. Exit maintenance by setting `STAGE_BACKUP_MAINTENANCE=0` and recreating only the stage app again.

## Stop or roll back the staging drill

Stop staging without removing data. To return staging to an earlier revision, create another separate checkout with that revision and rebuild its application against a new stage project and fresh volumes. Use verified backups to recover interrupted data tests. Deployment and application rollback of an approved production upgrade are a separate operation using the maintenance window, existing Compose project, saved revision and verified backups in the repository's deployment guides.

```bash
docker compose --env-file .env.stage -f docker-compose.stage.yml stop app db
```

## Verify the channel connections

For official YouTube retention, enable the YouTube Analytics API and reconnect the channel from the clip editor's import dialog with the additional Analytics permission. The clip must be published by the connected channel through PeakCut. The import uses the first seven complete Pacific calendar days after publication, with a three-day reporting lag. The Analytics comparison keeps official YouTube cohorts separate from manually measured observations.

For a Windsor.ai or other provider export, open the clip's **Record published performance** form and enter the equivalent seven-day dates, views, average audience retention and source report. Use a report for that same published clip and time window. Account access and an actual returned observation are required for provider verification.

For a single bounded Gemini test on the host, use a private shell with `STAGE_GEMINI_KEY` and `STAGE_GEMINI_MODEL` exported securely. Run `python tools/gemini_smoke.py` from the staging checkout. It sends one small request, caps the output at 128 tokens and reports only HTTP status and token count. The browser installation does not need the staging key entered into chat.


## Optional Windsor.ai performance reads (new follow-up)

**No Oracle configuration is required for ordinary clipping.** Linking Windsor.ai
inside ChatGPT does not give the standalone PeakCut server an API token.
If you want to enable Windsor imports later, obtain a separate API key directly
in your Windsor workspace; **do not paste the key into chat, GitHub or logs**.

The stage-only `.env.stage` file (created with `umask 077`) accepts:

```dotenv
STAGE_WINDSOR_CHANNEL_ID=UCxxxxxxxxxxxxxxxxxxxxxx
STAGE_WINDSOR_ACCOUNT_ID=
STAGE_WINDSOR_API_KEY=your-windsor-api-key
STAGE_WINDSOR_AUTOPULL=0
```

Replace `UCxxxxxxxxxxxxxxxxxxxxxx` with your channel's exact YouTube channel ID,
not the `@handle`. If Windsor's data-source account selector is different
from the public channel ID, set `STAGE_WINDSOR_ACCOUNT_ID` to the account ID
shown inside Windsor's data sources. **Returned rows must still match the
public channel ID**, so this does not weaken account isolation.
The key can alternatively be managed using the backend's
`PEAKCUT_WINDSOR_API_KEY_FILE` pointing to a private file in the container; this
avoids leaving it in the container's environment. The convenient stage example
uses an environment variable, which **may be visible to Docker administrators**
using `docker inspect`; keep server and Docker access private.

After restarting **only staging**, connect the same YouTube channel to PeakCut
through its official OAuth account flow and record the publish event in PeakCut.
The clip editor now offers **Import Windsor.ai** after a published YouTube clip
exists and learning is opted in. It checks the exact channel/video and requests
only the first seven complete Pacific days after publication, after a three-day
reporting delay. Older historical data may be unavailable on trial accounts.

`STAGE_WINDSOR_AUTOPULL=1` is an additional explicit opt-in that polls up to
three eligible clips per account every 30 minutes, retries incomplete responses
at most once a day, and does not overwrite an existing manual or official
YouTube observation. An import is counted only when the provider returned a
complete verified observation. Setting it to `0` immediately disables future
imports when the stage container is recreated.

**Provider testing remains blocked** until a properly authorized API key,
matching connected channel and an eligible published clip with sufficient
history are available. The synthetic tests are not proof of a successful live
Windsor import or of increased views or revenue.
