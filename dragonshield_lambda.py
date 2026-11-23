"""Dragon Shield backup, preview, and guarded import worker."""
import hashlib
import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import dragonshield_sync as sync

STATE_KEY = 'sync/state.json'
LOCK_KEY = 'sync/lock.json'


class LockBusy(Exception):
    pass


@contextmanager
def exclusive_run(s3, bucket):
    # 16 minutes exceeds the standard Lambda maximum runtime of 15 minutes.
    # A timed-out worker cannot still be running when this lease is reclaimed.
    payload = json.dumps({'owner': uuid.uuid4().hex, 'expires_at': time.time() + 960}).encode()
    args = dict(Bucket=bucket, Key=LOCK_KEY, Body=payload,
                ContentType='application/json', ServerSideEncryption='AES256')
    try:
        response = s3.put_object(**args, IfNoneMatch='*')
    except ClientError as exc:
        if exc.response['Error']['Code'] not in ('PreconditionFailed', '412', 'ConditionalRequestConflict', '409'):
            raise
        existing = s3.get_object(Bucket=bucket, Key=LOCK_KEY)
        if json.loads(existing['Body'].read())['expires_at'] > time.time():
            raise LockBusy() from None
        try:
            response = s3.put_object(**args, IfMatch=existing['ETag'])
        except ClientError as conflict:
            if conflict.response['Error']['Code'] in ('PreconditionFailed', '412', 'ConditionalRequestConflict', '409'):
                raise LockBusy() from None
            raise
    try:
        yield
    finally:
        # Releasing is also conditional: never release another owner's lease.
        s3.put_object(Bucket=bucket, Key=LOCK_KEY, Body=b'{"expires_at":0}',
                      IfMatch=response['ETag'], ContentType='application/json', ServerSideEncryption='AES256')


def handler(event, context):
    try:
        if context is not None and context.get_remaining_time_in_millis() > 900000:
            raise ValueError('Worker supports standard Lambda with at most a 15-minute timeout.')
        return run(event)
    except LockBusy:
        return {'status': 'skipped-overlap'}
    except Exception:
        raise RuntimeError('Sync failed. Inspect the backup and site before resuming. Credentials and request details are suppressed.') from None


def run(event):
    if not isinstance(event, dict) or set(event) != {'mode'} or event['mode'] not in ('download-only', 'preview', 'sync', 'resume'):
        raise ValueError('Use download-only, preview, sync, or resume mode.')
    mode = event['mode']
    secret_arn = os.environ['SYNC_SECRET_ARN']
    bucket = os.environ['SYNC_BACKUP_BUCKET']
    minimum = int(os.environ.get('SYNC_MIN_ENTRIES', '2000'))
    min_copies = int(os.environ.get('SYNC_MIN_COPIES', '4000'))
    min_folders = int(os.environ.get('SYNC_MIN_FOLDERS', '20'))
    max_removed_copies = int(os.environ.get('SYNC_MAX_REMOVED_COPIES', '0'))
    if min(minimum, min_copies, min_folders) < 1 or max_removed_copies < 0:
        raise ValueError('Invalid completeness guards.')
    if mode == 'sync' and os.environ.get('SYNC_IMPORT_ENABLED') != 'true':
        raise ValueError('Imports are disabled.')
    config = Config(retries={'total_max_attempts': 1}, connect_timeout=10, read_timeout=30)
    secrets = boto3.client('secretsmanager', config=config)
    s3 = boto3.client('s3', config=config)

    with exclusive_run(s3, bucket):
        return process(mode, secret_arn, bucket, minimum, min_copies, min_folders,
                       max_removed_copies, secrets, s3)


def process(mode, secret_arn, bucket, minimum, min_copies, min_folders,
            max_removed_copies, secrets, s3):
    def save_state(value):
        s3.put_object(Bucket=bucket, Key=STATE_KEY, Body=json.dumps(value).encode(),
                      ContentType='application/json', ServerSideEncryption='AES256')

    state = {}
    if mode != 'download-only':
        try:
            state = json.loads(s3.get_object(Bucket=bucket, Key=STATE_KEY)['Body'].read())
        except ClientError as exc:
            if exc.response['Error']['Code'] not in ('NoSuchKey', '404'):
                raise
        if mode == 'resume':
            state['paused'] = False
            save_state(state)
            return {'status': 'resumed', 'message': 'No download or import attempted.'}
        if state.get('paused'):
            raise ValueError('Sync paused; review the previous run before resuming.')
        # Persist before token renewal. A crash or failure leaves the worker paused.
        state['paused'] = True
        save_state(state)

    secret = json.loads(secrets.get_secret_value(SecretId=secret_arn)['SecretString'])

    def save_rotation(updated):
        secrets.put_secret_value(SecretId=secret_arn, SecretString=json.dumps(updated))

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'auth.json'
        sync.private_write(path, json.dumps(secret).encode())
        content = sync.download(sync.HTTP(), path, save_rotation=save_rotation)
    digest = hashlib.sha256(content).hexdigest()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    key = f'sync/exports/{stamp}-{digest[:12]}.csv'
    s3.put_object(Bucket=bucket, Key=key, Body=content,
                  ContentType='text/csv', ServerSideEncryption='AES256')
    stats = sync.validate(content, minimum)
    if stats['copies'] < min_copies or stats['folders'] < min_folders:
        raise ValueError('Export failed completeness guards.')
    previous = state.get('stats', {})
    if any(stats[k] < previous.get(k, 0) * 0.9 for k in ('entries', 'copies', 'folders')):
        raise ValueError('Export dropped more than 10 percent since last successful import.')
    result = {**stats, 'status': 'download-only', 'sha256': digest, 'backup_key': key}
    if mode != 'download-only':
        if mode == 'sync' and state.get('sha256') == digest:
            result['status'] = 'unchanged-export'
        else:
            origin = secret['duel_site_url'].rstrip('/')
            parsed = sync.urllib.parse.urlsplit(origin)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
                raise ValueError('Invalid site origin.')
            credential = secret['duel_import_token']
            if not isinstance(credential, str) or len(credential) < 32:
                raise ValueError('Invalid import credential.')
            headers = {'Origin': origin, 'X-Requested-With': 'DuelArchive', 'Authorization': 'Bearer ' + credential}
            payload = {'csv': content.decode('utf-8-sig')}
            if len(json.dumps(payload).encode()) > 4_000_000:
                raise ValueError('Hosted request exceeds 4 MB.')
            http = sync.HTTP()
            preview = http.json(origin + '/api/sync/preview', headers=headers, payload=payload)
            result.update({k: preview[k] for k in ('added_entries', 'removed_entries', 'added_copies', 'removed_copies', 'removed_folders')})
            if mode == 'preview':
                result['status'] = 'preview-only'
            elif preview['removed_copies'] > max_removed_copies or preview['removed_folders']:
                raise ValueError('Physical copies or folders would be removed; inspect the preview before importing.')
            elif not preview['added_entries'] and not preview['removed_entries']:
                result['status'] = 'unchanged-collection'
            else:
                response = http.json(origin + '/api/sync/import', headers=headers, payload={'token': preview['token']})
                if response.get('ok') is not True:
                    raise ValueError('Import was not acknowledged.')
                result['status'] = 'imported'
        if mode == 'sync':
            state.update(sha256=digest, stats=stats, backup_key=key)
        state['paused'] = False
        save_state(state)
    print(json.dumps(result))
    return result
