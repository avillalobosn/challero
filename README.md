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

Hosted imports and the refresh button queue metadata and artwork updates. With the same `DATABASE_URL` set, run `.venv/bin/python manage_hosted.py refresh` to process a queued update from your computer or an external scheduler. `--force` requests a refresh even if the queue is empty. Prices calculated from CSV values update as soon as an import is confirmed.

## Admin and visitor access

Sign in to see all folders, all source price fields, CSV import controls, cache status, and folder visibility checkboxes. Check the folders visitors should see and save. All folders initially start private. One shared visibility list applies to all visitors.

Visitors receive only visible collection entries and safe card/printing details. Storage folder names are admin-only: they are excluded from public API responses, cards, detail views, other-entry links, and navigation. Their API response excludes Price Bought, LOW, MID, MARKET, and Date Bought. Counts, searches, and other-printing links use only visible entries. The user endpoint stays redacted even when opened by a signed-in administrator. Source collection JSON, credentials, database files, and private-only artwork cannot be downloaded through the server.

Visitor offers use (LOW + MARKET) / 2 from the uploaded CSV, rounded to cents. Rows missing either input display **Ask for price**. The visitor view groups by card and set, lists variants, and omits stock quantities and collection totals.

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

Reference prices are private to admin. The public API strips all pricing metadata, including general vendor quotes. CSV LOW/MID/MARKET and purchase prices are preserved. Public offers use the average of LOW and MARKET from the uploaded CSV.

The existing refresh button refreshes reference prices and metadata together and reuses artwork. On startup/import the shared catalog refreshes when older than 48 hours; it is not a scheduled task. YGOPRODeck itself documents up to two days of endpoint caching. The displayed fetch timestamp tells you when this app retrieved data, not when a sale occurred. Old prices remain available and are marked stale after 48 hours if refreshes fail. Reopen a card after a background refresh completes to see the newest references.

The first fetched snapshot priced 1,005 of 2,724 collection entries; 1,674 had matching set entries with no usable price and 45 were unmatched. These counts can change with later refreshes and uploads. Admin cache status reports current coverage.
