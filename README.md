# Duel Archive — local collection manager

Python 3.10+ and a modern browser. The original local server uses the Python standard library.

From this project directory, run:

```sh
python server.py
```

On the first run, create your admin password in the terminal (at least 12 characters; typed input is hidden). It is stored as a salted scrypt hash. Then visit:

- User view: http://127.0.0.1:8765/
- Admin view: http://127.0.0.1:8765/admin

The server only binds to this computer's loopback interface. Stop it with Ctrl+C. Use `python server.py --setup` to choose a new password on a later start. Restarting the server invalidates old sessions. This is a local application; a hosted version will need a hosting-specific authentication/session and storage setup before deployment.

## Run with FastAPI

FastAPI uses the same local collection, settings, and password stored in `data/`. From the project directory, run:

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8765
```

If the original server is already running, stop it before starting FastAPI on the same port. To set up an admin password for the first time, run `python -c "from server import setup_password; setup_password()"`.

## Hosted PostgreSQL mode

The FastAPI app uses PostgreSQL when `DATABASE_URL` is configured. The database stores the collection, folder settings, artwork, metadata, admin password hash, sessions, and pending CSV previews. Plan for enough storage for the existing artwork and database overhead.

1. Create a PostgreSQL database and set its connection URL as `DATABASE_URL` in your local shell. Use the connection settings supplied by your database provider.
2. From this project directory, run `.venv/bin/python manage_hosted.py migrate`. It copies the local data and cached artwork into the database in one transaction. It stops if the database already contains a collection.
3. Set `DATABASE_URL` and `DUEL_PUBLIC_ORIGIN` in your hosted FastAPI environment. Set the origin to the full HTTPS address that will serve the app, such as `https://your-project.vercel.app`. The app accepts requests for this hostname and requires that origin on admin writes.
4. Sign in at `/admin` using your existing admin password. Hosted sessions expire after eight hours. CSV previews expire after ten minutes. Hosted CSV uploads must fit within a 4 MB JSON request.

Hosted imports queue metadata and artwork updates and the admin tab processes them automatically in resumable batches. Keep that tab open until completion; use Refresh to resume if interrupted. Visitor pages do not poll. Each batch reads only the selected cards and downloads at most three missing images, compressing them before S3 upload. The CLI `.venv/bin/python manage_hosted.py refresh` remains an optional worker; load both DATABASE_URL and the S3 variables before running it. Prices calculated from CSV values update as soon as an import is confirmed.

## Admin and visitor access

Sign in to see all folders, all source price fields, CSV import controls, cache status, and folder visibility checkboxes. Check the folders visitors should see and save. All folders initially start private. One shared visibility list applies to all visitors.

Visitors receive only visible collection entries and safe card/printing details. Storage folder names are admin-only: they are excluded from public API responses, cards, detail views, other-entry links, and navigation. Their API response excludes Price Bought, LOW, MID, MARKET, and Date Bought. Counts, searches, and other-printing links use only visible entries. The user endpoint stays redacted even when opened by a signed-in administrator. Source collection JSON, credentials, database files, and private-only artwork cannot be downloaded through the server.

Visitor offers use `max((LOW + MARKET) / 2, minimum USD price)` from the uploaded CSV. In `/admin`, set the minimum per copy in USD (default 0), an optional manual rate in CLP per USD, and the display currency (USD or CLP). USD is rounded to cents; CLP multiplies the unrounded USD offer by the configured rate and rounds to whole pesos. CLP requires a positive rate; there is no automatic exchange-rate update. Rows missing either CSV input display **Ask for price**, even with a minimum configured. Settings survive CSV imports. The visitor view groups by card and set, shows total copies in both list and image views, lists variants, and hides owned/trade breakdowns and collection totals.

Use this server-based version for the two views. The earlier standalone HTML export is an older snapshot containing the full collection and does not provide this access separation.

## Updating the collection

1. In admin, select a CSV exported in the same format as `all-folders.csv`.
2. Review the entry count, added/removed or changed entries, and new folders.
3. Click **Replace collection with this CSV**.

Uploads replace the complete collection, rather than appending. Quantities and repeated entries are retained exactly as supplied. Missing/invalid columns or numeric values reject the upload without altering existing data. Changes count as one removed old entry plus one added entry.

Matching folder visibility and currency are retained. New folders start private. Removed folders disappear, and will start private if reintroduced later. Data saves atomically to `data/collection.json`; the immediately previous imported snapshot is retained at `data/collection.backup.json`. Copy the entire private `data` directory to back up the application state. Do not serve it with a generic static web server or commit it to a repository.

The visitor language switch uses a local Spanish card-name index derived from [CDBEsp](https://github.com/ryoken08/CDBEsp) by card ID. Searches match both languages. Unmapped names and effect text appear in English.

The user view checks for collection changes every 15 seconds while open. A page reload also loads the latest saved data.

## How the cache updates

The cache is shared by card name/ID across printings and folders:

- On import, existing artwork is reused; increasing quantity or adding another printing does not redownload that card's image.
- New card names are matched against the locally cached YGOPRODeck catalog. If needed, the catalog refreshes, and only missing artwork downloads in the background.
- The metadata catalog is checked on startup and import. If older than 48 hours it refreshes. **Refresh metadata & retry missing images** forces a metadata refresh and retries failed image downloads.
- Existing artwork is not automatically replaced. Metadata can refresh independently; artwork is representative and may differ from a specific printing or alternate art.
- A failed external request keeps existing data usable. New cards remain browsable from CSV details, with a text placeholder until artwork is available. Admin shows unmatched names, pending images, and network errors; retry with the refresh button.
- Removed cards' artwork stays on disk for reuse. The server still checks current folder access before serving it. No automatic disk cleanup is performed.
- CSV prices update only on CSV replacement. Metadata/artwork refreshes never modify CSV price snapshots. YGOPRODeck reference prices are cached separately.

Files: `data/catalog.json` (full public metadata catalog), `data/cache.json` (matched metadata/status), and `data/assets/` (artwork). HTTP collection responses and artwork are marked `no-store`, so browser caching does not override visibility changes. This disk cache requires internet only when fetching new or refreshed external data.

Data and artwork provider: https://ygoprodeck.com/api-guide/

## Checks

```sh
python -m unittest discover -s tests -v
node tests/test_frontend.cjs
```

Tests cover public data filtering, admin authentication, raw-file/private-artwork protection, CSV preview/replacement and backup behavior, invalid-upload rejection, stale previews, origin checks, cache reuse, offline failures, and frontend rendering logic. Updated screens have not been visually verified: browser preview permission was declined.

## YGOPRODeck pricing trial

Admin card details now include YGOPRODeck reference pricing. A set-level price is selected only when both the full Card Number (e.g. TN19-EN012) and rarity match one API set entry. Multiple matches are marked ambiguous, and missing, invalid, or zero prices are unavailable. This match does not verify edition, language, condition, or alternate artwork. General vendor quotes are labeled separately as lowest prices across versions. USD and EUR quotes keep their source currencies; no conversion is performed.

Reference prices are private to admin. The public API strips all pricing metadata, including general vendor quotes. CSV LOW/MID/MARKET and purchase prices are preserved. Public offers use the CSV LOW/MARKET average with the configured minimum and optional CLP conversion.

The existing refresh button refreshes reference prices and metadata together and reuses artwork. On startup/import the shared catalog refreshes when older than 48 hours; it is not a scheduled task. YGOPRODeck itself documents up to two days of endpoint caching. The displayed fetch timestamp tells you when this app retrieved data, not when a sale occurred. Old prices remain available and are marked stale after 48 hours if refreshes fail. Reopen a card after a background refresh completes to see the newest references.

The first fetched snapshot priced 1,005 of 2,724 collection entries; 1,674 had matching set entries with no usable price and 45 were unmatched. These counts can change with later refreshes and uploads. Admin cache status reports current coverage.

## Compressed artwork

New artwork is encoded as WebP before storage: detail images fit within 480 × 700 at quality 55; grid thumbnails fit within 200 × 292 at quality 40. JPEG originals are no longer inserted. Existing metadata referring to JPEG filenames is supported during conversion.

Deploy the WebP-capable backend before converting the production database. With DATABASE_URL configured, preview and apply:

```sh
.venv/bin/python manage_hosted.py compress-artwork
.venv/bin/python manage_hosted.py compress-artwork --apply --batch-size 25
```

The converter reads a bounded batch at a time and decodes images individually. Each batch commits separately, and replacement plus deletion is transactional. Interrupting and rerunning skips completed images. Corrupt images are retained and reported. Transaction advisory locks and a renewable database lease prevent concurrent refresh/conversion workers, including through transaction poolers. The command downloads each remaining JPEG once, so conversion itself consumes some database egress. Database disk space becomes reusable after deletion; it may not immediately shrink.

## Private S3 storage

S3 support is optional; without `DUEL_S3_BUCKET` the existing PostgreSQL artwork backend remains active. Follow [the S3 setup and migration guide](docs/S3.md) and use the [bucket-scoped IAM policy](docs/s3-iam-policy.json). Copy and verify images first; enable S3 on Vercel next; prune database copies only after verifying the deployed site.

## Indexed image permissions

Hosted image requests use the primary key of `duel_image_permissions`, rather than expanding collection and cache JSON documents per image request. Database triggers rebuild this small index atomically when collection rows, folder visibility or card metadata change, including raw SQL updates from artwork batches. Shared artwork is public if any currently visible entry uses it; admin access still requires an active collection entry. Row-level security blocks direct API enumeration.

Before deploying this version to an existing database, run with DATABASE_URL configured:

```sh
.venv/bin/python manage_hosted.py permissions-migrate
```

New database migrations include the table and triggers automatically. To run the local PostgreSQL integration checks (including concurrent reads and transactional visibility changes):

```sh
RUN_POSTGRES_INTEGRATION=1 .venv/bin/python -m unittest discover -s tests -p 'test_permissions_integration.py'
```

## Indexed catalog and bounded collection caching

Run `manage_hosted.py read-models-migrate` with DATABASE_URL set before deploying this version to an existing database. This creates indexed collection rows, per-card metadata, catalog cards and translations. Authoritative JSON documents remain compatible with the CLI; database triggers synchronize the projections in the same transaction. Artwork batches fetch at most 50 indexed card records and download at most three missing images, without expanding the catalog document on each batch.

Collection responses use a process-local cache capped at four compressed entries / 12 MiB. Every request checks document versions in its consistent database snapshot before using a cache entry. Keys separate databases, public/admin scopes and S3 buckets. Visibility, CSV, metadata and translation updates invalidate entries immediately after commit. Warm requests read only the version token. Cold public requests fetch only visible rows, matching card metadata without vendor pricing, matching translations and required artwork paths. Public browser responses remain uncached (`no-store`); server responses advertise `X-Collection-Cache: HIT` or `MISS` for diagnostics. Gzip reduces HTTP payload sizes when accepted by the client.

Run the opt-in PostgreSQL/HTTP load test locally:

```sh
RUN_POSTGRES_INTEGRATION=1 .venv/bin/python -m unittest discover -s tests -p 'test_read_models_integration.py'
```

It tests a 6,000-row CSV, a 12,000-card catalog, public/admin isolation, committed/rolled-back invalidation, translations, metadata and artwork repairs, HTTP upload/gzip, concurrent image access and concurrent collection reads. The report is written to ignored `work/read_models_benchmark.json`. Transfer counts estimate application data, excluding wire-protocol overhead. Memory/swap results describe the isolated local PostgreSQL processes, not the managed Supabase instance.

Collection filters include a separate set name/code search and card subtypes (Xyz, Synchro, Pendulum, Fusion, Ritual, Link, Continuous, Quick-Play, Counter, Field, and others present in the collection). Subtypes follow the selected card type and use cached YGOPRODeck metadata. Set searches and subtype selections are preserved in shareable URLs. The header theme toggle remembers light/dark preference in this browser. The brand returns to `/`; admin access remains directly at `/admin`, without a public sign-in button.
