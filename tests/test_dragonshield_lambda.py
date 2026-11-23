import json
import os
import unittest
from unittest.mock import Mock, patch

import dragonshield_lambda as worker
from test_dragonshield_sync import sample


class LambdaTests(unittest.TestCase):
    def setUp(self):
        from contextlib import nullcontext
        patcher = patch.object(worker, 'exclusive_run', side_effect=lambda *args: nullcontext())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rejects_import_without_contacting_aws(self):
        with patch.object(worker.boto3, 'client') as client:
            with self.assertRaises(RuntimeError):
                worker.handler({'mode': 'upload'}, None)
            client.assert_not_called()

    def test_rotation_persisted_and_backup_written(self):
        secrets, s3 = Mock(), Mock()
        secrets.get_secret_value.return_value = {'SecretString': json.dumps({
            'client_id': 'test', 'refresh_token': 'old', 'duel_site_url': 'https://example.com'})}

        def download(http, path, save_rotation):
            value = json.loads(path.read_text())
            value['refresh_token'] = 'new'
            save_rotation(value)
            return sample()

        with patch.dict(os.environ, {'SYNC_SECRET_ARN': 'secret', 'SYNC_BACKUP_BUCKET': 'bucket', 'SYNC_MIN_ENTRIES': '1', 'SYNC_MIN_COPIES': '1', 'SYNC_MIN_FOLDERS': '1'}), \
                patch.object(worker.boto3, 'client', side_effect=[secrets, s3]), \
                patch.object(worker.sync, 'download', side_effect=download):
            result = worker.handler({'mode': 'download-only'}, None)
        self.assertEqual(result['entries'], 1)
        self.assertEqual(result['status'], 'download-only')
        saved = json.loads(secrets.put_secret_value.call_args.kwargs['SecretString'])
        self.assertEqual(saved['refresh_token'], 'new')
        self.assertEqual(saved['duel_site_url'], 'https://example.com')
        self.assertEqual(s3.put_object.call_args.kwargs['Body'], sample())

    def test_errors_redact_details(self):
        with patch.object(worker, 'run', side_effect=Exception('sensitive-token')):
            with self.assertRaises(RuntimeError) as raised:
                worker.handler({}, None)
        self.assertNotIn('sensitive-token', str(raised.exception))

    def test_full_import_persists_hash_only_after_acknowledgement(self):
        import io
        from botocore.exceptions import ClientError
        secret = {'client_id': 'test', 'refresh_token': 'old', 'duel_site_url': 'https://example.com', 'duel_import_token': 'x' * 43}
        secrets, s3, http = Mock(), Mock(), Mock()
        secrets.get_secret_value.return_value = {'SecretString': json.dumps(secret)}
        s3.get_object.side_effect = ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        http.json.side_effect = [{'added_entries': 1, 'removed_entries': 1457, 'added_copies': 2, 'removed_copies': 0, 'removed_folders': [], 'token': 'preview'}, {'ok': True}]
        env = {'SYNC_SECRET_ARN': 'secret', 'SYNC_BACKUP_BUCKET': 'bucket', 'SYNC_MIN_ENTRIES': '1',
               'SYNC_MIN_COPIES': '1', 'SYNC_MIN_FOLDERS': '1', 'SYNC_IMPORT_ENABLED': 'true'}
        with patch.dict(os.environ, env), patch.object(worker.boto3, 'client', side_effect=[secrets, s3]), \
                patch.object(worker.sync, 'download', return_value=sample()), patch.object(worker.sync, 'HTTP', return_value=http):
            result = worker.handler({'mode': 'sync'}, None)
        self.assertEqual(result['status'], 'imported')
        states = [json.loads(c.kwargs['Body']) for c in s3.put_object.call_args_list if c.kwargs['Key'] == worker.STATE_KEY]
        self.assertTrue(states[0]['paused'])
        self.assertFalse(states[-1]['paused'])
        self.assertEqual(states[-1]['sha256'], result['sha256'])
        self.assertTrue(http.json.call_args_list[-1].args[0].endswith('/api/sync/import'))

    def test_failed_import_leaves_paused_without_success_hash(self):
        from botocore.exceptions import ClientError
        secrets, s3, http = Mock(), Mock(), Mock()
        secrets.get_secret_value.return_value = {'SecretString': json.dumps({
            'duel_site_url': 'https://example.com', 'duel_import_token': 'x' * 43})}
        s3.get_object.side_effect = ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        http.json.side_effect = [{'added_entries': 1, 'removed_entries': 0, 'added_copies': 2, 'removed_copies': 0, 'removed_folders': [], 'token': 'preview'}, Exception('lost response')]
        env = {'SYNC_SECRET_ARN': 'secret', 'SYNC_BACKUP_BUCKET': 'bucket', 'SYNC_MIN_ENTRIES': '1',
               'SYNC_MIN_COPIES': '1', 'SYNC_MIN_FOLDERS': '1', 'SYNC_IMPORT_ENABLED': 'true'}
        with patch.dict(os.environ, env), patch.object(worker.boto3, 'client', side_effect=[secrets, s3]), \
                patch.object(worker.sync, 'download', return_value=sample()), patch.object(worker.sync, 'HTTP', return_value=http):
            with self.assertRaises(RuntimeError): worker.handler({'mode': 'sync'}, None)
        states = [json.loads(c.kwargs['Body']) for c in s3.put_object.call_args_list if c.kwargs['Key'] == worker.STATE_KEY]
        self.assertEqual(states, [{'paused': True}])

    def test_unchanged_export_skips_site(self):
        import io
        secrets, s3 = Mock(), Mock()
        secrets.get_secret_value.return_value = {'SecretString': '{}'}
        digest = worker.hashlib.sha256(sample()).hexdigest()
        s3.get_object.return_value = {'Body': io.BytesIO(json.dumps({'sha256': digest}).encode())}
        env = {'SYNC_SECRET_ARN': 'secret', 'SYNC_BACKUP_BUCKET': 'bucket', 'SYNC_MIN_ENTRIES': '1',
               'SYNC_MIN_COPIES': '1', 'SYNC_MIN_FOLDERS': '1', 'SYNC_IMPORT_ENABLED': 'true'}
        with patch.dict(os.environ, env), patch.object(worker.boto3, 'client', side_effect=[secrets, s3]), \
                patch.object(worker.sync, 'download', return_value=sample()), patch.object(worker.sync, 'HTTP') as http:
            result = worker.handler({'mode': 'sync'}, None)
        self.assertEqual(result['status'], 'unchanged-export')
        http.return_value.json.assert_not_called()

    def test_physical_removal_blocks_commit_and_leaves_paused(self):
        from botocore.exceptions import ClientError
        secrets, s3, http = Mock(), Mock(), Mock()
        secrets.get_secret_value.return_value = {'SecretString': json.dumps({
            'duel_site_url': 'https://example.com', 'duel_import_token': 'x' * 43})}
        s3.get_object.side_effect = ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        http.json.return_value = {'added_entries': 100, 'removed_entries': 100,
                                 'added_copies': 1, 'removed_copies': 1, 'removed_folders': [], 'token': 'preview'}
        env = {'SYNC_SECRET_ARN': 'secret', 'SYNC_BACKUP_BUCKET': 'bucket', 'SYNC_MIN_ENTRIES': '1',
               'SYNC_MIN_COPIES': '1', 'SYNC_MIN_FOLDERS': '1', 'SYNC_IMPORT_ENABLED': 'true'}
        with patch.dict(os.environ, env), patch.object(worker.boto3, 'client', side_effect=[secrets, s3]), \
                patch.object(worker.sync, 'download', return_value=sample()), patch.object(worker.sync, 'HTTP', return_value=http):
            with self.assertRaises(RuntimeError): worker.handler({'mode': 'sync'}, None)
        self.assertEqual(http.json.call_count, 1)
        self.assertTrue(http.json.call_args.args[0].endswith('/preview'))
        states = [json.loads(c.kwargs['Body']) for c in s3.put_object.call_args_list if c.kwargs['Key'] == worker.STATE_KEY]
        self.assertEqual(states, [{'paused': True}])

    def test_inspection_works_while_paused_without_oauth_or_import(self):
        import io
        secrets, s3, http = Mock(), Mock(), Mock()
        secrets.get_secret_value.return_value = {'SecretString': json.dumps({
            'duel_site_url': 'https://example.com', 'duel_import_token': 'x' * 43})}
        s3.get_object.side_effect = [
            {'Body': io.BytesIO(b'{"paused":true}')}, {'Body': io.BytesIO(sample())}]
        s3.list_objects_v2.return_value = {'Contents': [{'Key': 'sync/exports/latest.csv'}]}
        http.json.return_value = {'added_entries': 100, 'removed_entries': 100,
                                 'added_copies': 1, 'removed_copies': 1, 'removed_folders': [], 'token': 'private-preview-token'}
        env = {'SYNC_SECRET_ARN': 'secret', 'SYNC_BACKUP_BUCKET': 'bucket', 'SYNC_MIN_ENTRIES': '1',
               'SYNC_MIN_COPIES': '1', 'SYNC_MIN_FOLDERS': '1', 'SYNC_IMPORT_ENABLED': 'false'}
        with patch.dict(os.environ, env), patch.object(worker.boto3, 'client', side_effect=[secrets, s3]), \
                patch.object(worker.sync, 'download') as download, patch.object(worker.sync, 'HTTP', return_value=http):
            result = worker.handler({'mode': 'inspect'}, None)
        self.assertTrue(result['paused'])
        self.assertFalse(result['import_would_pass_guards'])
        self.assertFalse(result['guards']['physical_removals'])
        self.assertEqual(result['removed_copies'], 1)
        download.assert_not_called()
        secrets.put_secret_value.assert_not_called()
        s3.put_object.assert_not_called()
        self.assertEqual(http.json.call_count, 1)
        self.assertTrue(http.json.call_args.args[0].endswith('/preview'))
        self.assertNotIn('private-preview-token', json.dumps(result))

    def test_safe_error_reports_aws_code_without_request_details(self):
        import io
        from botocore.exceptions import ClientError
        error = ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'sensitive-token'}}, 'GetObject')
        with patch.object(worker, 'run', side_effect=error), patch('sys.stdout', new_callable=io.StringIO) as output:
            with self.assertRaises(RuntimeError) as caught:
                worker.handler({}, None)
        self.assertIn('AccessDenied', str(caught.exception))
        self.assertNotIn('sensitive-token', str(caught.exception))
        self.assertNotIn('sensitive-token', output.getvalue())


class LockTests(unittest.TestCase):
    def conflict(self):
        from botocore.exceptions import ClientError
        return ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')

    def test_new_lock_released_only_with_own_etag(self):
        s3 = Mock()
        s3.put_object.return_value = {'ETag': 'owner-etag'}
        with worker.exclusive_run(s3, 'bucket'):
            self.assertEqual(s3.put_object.call_count, 1)
            self.assertEqual(s3.put_object.call_args.kwargs['IfNoneMatch'], '*')
        self.assertEqual(s3.put_object.call_args.kwargs['IfMatch'], 'owner-etag')

    def test_active_lock_prevents_entering_or_releasing(self):
        import io
        s3 = Mock()
        s3.put_object.side_effect = self.conflict()
        s3.get_object.return_value = {'ETag': 'other', 'Body': io.BytesIO(json.dumps({'expires_at': worker.time.time() + 960}).encode())}
        with self.assertRaises(worker.LockBusy):
            with worker.exclusive_run(s3, 'bucket'):
                self.fail('Must not enter while another invocation owns the lock')
        self.assertEqual(s3.put_object.call_count, 1)

    def test_expired_lock_takeover_is_conditional(self):
        import io
        s3 = Mock()
        s3.put_object.side_effect = [self.conflict(), {'ETag': 'new-owner'}, {}]
        s3.get_object.return_value = {'ETag': 'expired-owner', 'Body': io.BytesIO(b'{"expires_at":0}')}
        with worker.exclusive_run(s3, 'bucket'): pass
        self.assertEqual(s3.put_object.call_args_list[1].kwargs['IfMatch'], 'expired-owner')
        self.assertEqual(s3.put_object.call_args_list[2].kwargs['IfMatch'], 'new-owner')

    def test_takeover_race_does_not_enter(self):
        import io
        s3 = Mock()
        s3.put_object.side_effect = [self.conflict(), self.conflict()]
        s3.get_object.return_value = {'ETag': 'expired', 'Body': io.BytesIO(b'{"expires_at":0}')}
        with self.assertRaises(worker.LockBusy):
            with worker.exclusive_run(s3, 'bucket'):
                self.fail('Lost takeover race')
        self.assertEqual(s3.put_object.call_count, 2)

    def test_error_releases_lock(self):
        s3 = Mock()
        s3.put_object.return_value = {'ETag': 'owner'}
        with self.assertRaises(ValueError):
            with worker.exclusive_run(s3, 'bucket'):
                raise ValueError('failed work')
        self.assertEqual(s3.put_object.call_args.kwargs['IfMatch'], 'owner')
