# Local Dragon Shield sync

`dragonshield_sync.py` defaults to download-only. It uses the existing server CSV validator and does not change the collection unless `--upload` is provided. No AWS resources are needed.

## One-time sign-in

Install Playwright into a local virtual environment (only the bootstrap command requires it):

```bash
python -m venv .venv-sync
.venv-sync/bin/pip install playwright
.venv-sync/bin/python -m playwright install chromium
.venv-sync/bin/python dragonshield_sync.py --bootstrap
```

Sign in in the separate Chromium window. The worker captures the OAuth refresh token and client configuration into `work/dragonshield/auth.json`, with permissions 600. It does not save your email/password or browser profile. This file grants account access; keep it private. `work/` is ignored by Git. Repeat bootstrap if renewal is revoked or expires. Immediate refresh works in testing; long-term token lifetime is unknown.

## Download only

```bash
python dragonshield_sync.py --min-entries 2000
```

The threshold is an explicit completeness guard, not an assertion that every folder was exported. Adjust it if your collection changes significantly. Each run renews authentication, persists any rotated refresh token before exporting, requests the export URL, downloads from the expected Azure host using a separate client, validates all rows, and writes a dated private CSV in `work/dragonshield/exports/`. Backups accumulate; no retention deletion is implemented. A local file lock prevents concurrent runs using the same output directory. Keep one configuration/output directory per account.

Validate an existing export without contacting Dragon Shield:

```bash
python dragonshield_sync.py --input '/absolute/path/all-folders.csv' --min-entries 2000
```

## Explicit upload

```bash
python dragonshield_sync.py --upload --site https://your-duel-archive.example --min-entries 2000 --max-removals 0
```

The admin password is prompted without echo, or can be supplied through `DUEL_ADMIN_PASSWORD` by a secret manager. It is never accepted as a CLI argument or written to disk. The worker signs in, previews, checks the removal limit, and commits the preview token. Unchanged previews skip the commit. The app counts a changed row as an old entry removed and a new entry added, so price changes also count toward `--max-removals`. Review a preview in admin before choosing a higher limit. Imports replace the complete collection; current app backup/settings behavior is preserved. The local CLI uses the admin password; the AWS worker below uses a dedicated sync credential.

Failures return exit code 1 and redact underlying request details. There are no automatic retries: if an import response is lost after commit, inspect the webpage before retrying. The import guard uses absolute removal counts; downloads alone do not verify that Dragon Shield returned every expected folder. No scheduling, AWS deployment, S3 storage or notification is configured here.

## Verification

```bash
python -m unittest discover -s tests -p test_dragonshield_sync.py
```

## AWS worker rollout (prepared locally)

`dist/challero-sync.zip` includes only `dragonshield_lambda.py`,
`dragonshield_sync.py`, and `server.py`. Boto3 is supplied by the Python Lambda
runtime. No secret or collection files are included. The handler remains
`dragonshield_lambda.handler`. The original download-only ZIP is retained.

Dedicated import authentication:

- Set Vercel production `DUEL_SYNC_TOKEN_SHA256` to the contents of private
  `work/dragonshield/duel-import-token-sha256.txt` and deploy the updated backend.
- Add `duel_import_token` to the CURRENT Secrets Manager JSON using the contents
  of private `work/dragonshield/duel-import-token.txt`. Preserve every existing
  field, particularly the refresh token already rotated by the AWS test.
- The credential permits only POST `/api/sync/preview` and `/api/sync/import`.
  Existing host/origin checks still apply. It provides no admin session or reads.
  Preview tokens are bound to this credential and cannot cross admin sessions.
- Rotate by generating a new random token, changing the server hash and secret
  token together. Revocation is immediate on deployment of the new hash.

The full ZIP uses an S3 conditional-write lock at `sync/lock.json` for all modes,
including download-only and resume. It needs no additional IAM permissions and
works with unreserved concurrency. A running lease causes another invocation to
return `skipped-overlap` before reading credentials or touching state. The lease
expires after 16 minutes, exceeding the standard Lambda maximum 15-minute
runtime; timed-out owners cannot overlap with lease takeover. Normal completion
or errors release it conditionally using the owner's ETag. Do not delete or edit
the lock manually while a worker may be running. Keep this as a standard Lambda
function with a timeout no greater than 15 minutes.

Set asynchronous retry attempts to **0**. Keep all triggers disabled until
verification is complete.

The existing IAM policy needs one additional statement so a missing initial
state object returns 404 rather than AccessDenied:

```json
{
  "Effect": "Allow",
  "Action": "s3:ListBucket",
  "Resource": "arn:aws:s3:::challero-sync-backup"
}
```

Environment variables beyond the existing secret ARN and bucket:

- `SYNC_MIN_ENTRIES=2000`
- `SYNC_MIN_COPIES=4000`
- `SYNC_MIN_FOLDERS=20`
- `SYNC_IMPORT_ENABLED=false` initially

Run `{"mode":"preview"}` first. It downloads and backs up the CSV and returns
added/removed counts without committing. The response includes `removed_copies` and `removed_folders`, computed by
comparing quantities for each card/printing/folder while ignoring all price
fields, purchase date, and trade quantity. Row-level added/removed counts still
include price changes for compatibility. Dragon Shield is the source of truth:
copy removals, folder moves, condition corrections, and removed folders are
reported but do not block imports. `SYNC_MAX_REMOVED_COPIES` and
`SYNC_MAX_REMOVALS` are obsolete and ignored. Absolute minimum counts and the
10% relative-count completeness guard remain enforced.
Set `SYNC_IMPORT_ENABLED=true` only after reviewing the preview. Then manually
run `{"mode":"sync"}` and verify the website before creating any schedule.

Successful syncs save the export hash and counts to `sync/state.json`. Identical
exports skip the site API. Absolute thresholds and a 10% drop guard on entries,
copies, and folder counts reject incomplete exports. Folder count alone does
not establish the identity or completeness of every folder. Preview-only runs do
not establish the successful-import baseline.

Preview/sync marks state paused before OAuth renewal. Any failure, lost response,
or timeout leaves it paused. Later runs fail without attempting authentication or
import. After investigating the site, credential, limits and backups, explicitly
run `{"mode":"resume"}` to clear the pause while retaining the last successful
hash and counts. This performs no import. Then retry a preview or sync manually.
Download-only mode does not change state and can still renew authentication.

Scheduling and failure notifications remain unconfigured. The intended next step
is a separate Scheduler invoke role, hourly schedule, and SNS
email alerts via CloudWatch alarms for Lambda errors and Scheduler delivery
failures. Confirm the SNS email subscription before relying on it. Keep Scheduler
retry attempts at zero and the schedule disabled until manual checks pass.


### Inspect a failed run safely

Upload the current ZIP and run `{"mode":"inspect"}`. Inspection works while
paused. It takes the same S3 lock, reads state and the newest saved CSV, and
requests only a site preview. It does not renew Dragon Shield authentication,
write sync state, clear the pause, or call the import endpoint. It reports
counts and which guards pass. The preview token and credentials are omitted.
Failure responses now report a safe stage and category (for example,
`inventory-guard / inventory-removal-blocked`, or `acquire-lock / AccessDenied`)
without raw exception messages, request bodies, or signed URLs. For an uncertain
import, compare the saved export with the current site before resuming.


### Error alarm missing-data behavior

`challero-sync-errors` evaluates the Lambda `Errors` sum over five minutes, while
the schedule invokes hourly. Use `TreatMissingData=notBreaching` so idle intervals
do not become INSUFFICIENT_DATA. Actual error datapoints still breach at >=1.
An OK error alarm is not evidence of a successful recent sync; inspect the
successful-import state or logs to establish freshness. Keep Scheduler delivery
failure monitoring separate. The diagnostics IAM profile has read-only access;
update the alarm and upload Lambda ZIPs manually in the console.
