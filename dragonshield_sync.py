#!/usr/bin/env python3
"""Download Dragon Shield exports; importing requires --upload. Secrets never logged."""
import argparse
import asyncio
import hashlib
import http.cookiejar
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from getpass import getpass
from server import parse_csv, MAX_UPLOAD

TOKEN_URL = 'https://auth.dragonshield.com/connect/token'
EXPORT_URL = 'https://api-yugioh.dragonshield.com/api/v1/folders/export'

class SyncError(Exception):
    pass

class SyncHTTPError(SyncError):
    def __init__(self, status):
        self.status = int(status)
        super().__init__('Remote HTTP request failed.')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class HTTP:
    def __init__(self):
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, url, *, headers=None, form=None, payload=None):
        data = None
        headers = dict(headers or {})
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if payload is not None:
            data = json.dumps(payload).encode()
            headers['Content-Type'] = 'application/json'
        try:
            with self.opener.open(urllib.request.Request(url, data=data, headers=headers), timeout=90) as response:
                body = response.read(MAX_UPLOAD + 1)
                if len(body) > MAX_UPLOAD:
                    raise SyncError('Response exceeds 12 MB.')
                return body
        except urllib.error.HTTPError as exc:
            raise SyncHTTPError(exc.code) from None
        except (urllib.error.URLError, TimeoutError):
            raise SyncError('Network request failed; no import was committed by this request.') from None

    def json(self, url, **kwargs):
        try:
            return json.loads(self.request(url, **kwargs))
        except (ValueError, UnicodeError):
            raise SyncError('Expected a JSON response.') from None


def private_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(name): os.unlink(name)


def urls(value):
    if isinstance(value, str) and value.startswith('https://'):
        yield value
    elif isinstance(value, dict):
        for item in value.values(): yield from urls(item)
    elif isinstance(value, list):
        for item in value: yield from urls(item)


def download(http, secret_path, save_rotation=None):
    secret_path = Path(secret_path)
    if secret_path.stat().st_mode & 0o077:
        raise SyncError('Secret file must have permissions 600.')
    secret = json.loads(secret_path.read_text())
    form = {k: secret[k] for k in ('client_id', 'client_secret', 'scope') if secret.get(k)}
    if not secret.get('refresh_token') or not form.get('client_id'):
        raise SyncError('Secret needs client_id and refresh_token. Run --bootstrap.')
    form.update(grant_type='refresh_token', refresh_token=secret['refresh_token'])
    token = http.json(TOKEN_URL, form=form)
    if not token.get('access_token'):
        raise SyncError('Renewal did not return an access token. Run --bootstrap again.')
    # Persist rotation before any export call can fail.
    if token.get('refresh_token'):
        secret['refresh_token'] = token['refresh_token']
        private_write(secret_path, json.dumps(secret).encode())
        if save_rotation is not None:
            save_rotation(secret)
    result = http.json(EXPORT_URL, headers={'Authorization': 'Bearer ' + token['access_token']})
    links = [u for u in urls(result) if urllib.parse.urlsplit(u).hostname == 'dsexport.blob.core.windows.net'
             and not urllib.parse.urlsplit(u).username and urllib.parse.urlsplit(u).port in (None, 443)]
    if len(links) != 1:
        raise SyncError('Export returned an unexpected download URL.')
    # Separate client: never forward account headers/cookies to CSV storage.
    return HTTP().request(links[0])


def validate(content, min_entries=1):
    try:
        rows = parse_csv(content.decode('utf-8-sig'))
    except (ValueError, UnicodeError) as exc:
        raise SyncError('CSV validation failed: ' + str(exc)) from None
    if len(rows) < min_entries:
        raise SyncError('Export contains fewer entries than --min-entries; import blocked.')
    return {'entries': len(rows), 'copies': sum(int(r['Quantity']) for r in rows),
            'folders': len({r['Folder Name'] for r in rows})}


def upload(http, origin, content, max_removals):
    parsed = urllib.parse.urlsplit(origin)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise SyncError('--site must be an HTTPS origin, without a path or credentials.')
    origin = origin.rstrip('/')
    headers = {'Origin': origin, 'X-Requested-With': 'DuelArchive'}
    password = os.environ.get('DUEL_ADMIN_PASSWORD') or getpass('Duel Archive admin password: ')
    http.json(origin + '/api/login', headers=headers, payload={'password': password})
    preview = http.json(origin + '/api/admin/preview', headers=headers, payload={'csv': content.decode('utf-8-sig')})
    if preview['removed_entries'] > max_removals:
        raise SyncError(f"Preview removes {preview['removed_entries']} entries; exceeds --max-removals. Nothing imported.")
    if not preview['added_entries'] and not preview['removed_entries']:
        return 'unchanged'
    http.json(origin + '/api/admin/import', headers=headers, payload={'token': preview['token']})
    return 'imported'


async def bootstrap(path):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise SyncError('Bootstrap requires Playwright; see docs/dragonshield-sync.md.') from None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        captured = asyncio.get_running_loop().create_future()
        async def inspect(response):
            if response.url.split('?')[0] != TOKEN_URL or response.status != 200 or captured.done(): return
            try:
                token = await response.json()
                if token.get('refresh_token'):
                    form = urllib.parse.parse_qs(response.request.post_data or '')
                    secret = {k: form[k][0] for k in ('client_id', 'client_secret', 'scope') if k in form}
                    secret['refresh_token'] = token['refresh_token']
                    if not captured.done(): captured.set_result(secret)
            except Exception:
                if not captured.done(): captured.set_exception(SyncError('Could not capture sign-in response.'))
        context.on('response', inspect)
        page = await context.new_page()
        try:
            await page.goto('https://yugioh.dragonshield.com/folders')
            print('Sign in in the separate Chromium window. Do not close it until capture completes.', flush=True)
            secret = await asyncio.wait_for(captured, 900)
            private_write(path, json.dumps(secret).encode())
            print('Refresh token saved privately. Email and password were not saved.')
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--secret', type=Path, default=Path('work/dragonshield/auth.json'))
    parser.add_argument('--output-dir', type=Path, default=Path('work/dragonshield/exports'))
    parser.add_argument('--bootstrap', action='store_true', help='Interactive sign-in; save refresh token locally with permissions 600.')
    parser.add_argument('--input', type=Path, help='Validate a local CSV instead of contacting Dragon Shield.')
    parser.add_argument('--min-entries', type=int, default=1)
    parser.add_argument('--upload', action='store_true', help='Explicitly enable collection replacement.')
    parser.add_argument('--site', default=os.environ.get('DUEL_SITE_URL'))
    parser.add_argument('--max-removals', type=int, default=0, help='Maximum removed/changed old entries allowed during import.')
    args = parser.parse_args()
    if args.min_entries < 1 or args.max_removals < 0: parser.error('Invalid count threshold.')
    if args.upload and not args.site: parser.error('--upload requires --site.')
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Serialize local runs, including token rotation and imports.
        import fcntl
        with open(args.output_dir / '.sync.lock', 'a') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise SyncError('Another sync is running.') from None
            if args.bootstrap:
                asyncio.run(bootstrap(args.secret))
                return
            content = args.input.read_bytes() if args.input else download(HTTP(), args.secret)
            if len(content) > MAX_UPLOAD: raise SyncError('CSV exceeds 12 MB.')
            stats = validate(content, args.min_entries)
            digest = hashlib.sha256(content).hexdigest()
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            destination = args.output_dir / f'{stamp}-{digest[:12]}.csv'
            private_write(destination, content)
            status = upload(HTTP(), args.site, content, args.max_removals) if args.upload else 'download-only'
            print(json.dumps({**stats, 'status': status, 'sha256': digest, 'backup': str(destination.resolve())}))
    except (SyncError, OSError, ValueError, KeyError, asyncio.TimeoutError):
        # Avoid tracebacks containing request payloads, tokens, or signed URLs.
        import sys
        print('Sync failed. Check network, private auth file, CSV validity and configured import limits. No automatic retry was made; verify the site before retrying an uncertain import.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
