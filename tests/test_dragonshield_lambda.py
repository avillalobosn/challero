import json
import os
import unittest
from unittest.mock import Mock, patch

import dragonshield_lambda as worker
from test_dragonshield_sync import sample


class LambdaTests(unittest.TestCase):
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
        http.json.side_effect = [{'added_entries': 1, 'removed_entries': 0, 'token': 'preview'}, {'ok': True}]
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
        http.json.side_effect = [{'added_entries': 1, 'removed_entries': 0, 'token': 'preview'}, Exception('lost response')]
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
