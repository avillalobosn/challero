import io
import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

import manual_sync


ENV = {'DUEL_SYNC_FUNCTION_ARN': 'arn:aws:lambda:us-east-1:468502414905:function:challero-dragonshield-sync',
       'DUEL_SYNC_ACCESS_KEY_ID': 'test-key', 'DUEL_SYNC_SECRET_ACCESS_KEY': 'test-secret'}


class ManualSyncTests(unittest.TestCase):
    def test_only_fixed_sync_action_and_safe_counts_are_returned(self):
        client = Mock()
        client.invoke.return_value = {'Payload': io.BytesIO(json.dumps({
            'status': 'imported', 'entries': 3000, 'copies': 4000, 'folders': 20,
            'backup_key': 'private', 'refresh_token': 'never-return-this'}).encode())}
        with patch.dict(os.environ, ENV), patch('boto3.client', return_value=client) as factory:
            code, result = manual_sync.invoke()
        self.assertEqual(code, 200)
        self.assertEqual(result, {'status': 'imported', 'entries': 3000, 'copies': 4000, 'folders': 20})
        self.assertEqual(json.loads(client.invoke.call_args.kwargs['Payload']), {'mode': 'sync'})
        self.assertEqual(client.invoke.call_args.kwargs['InvocationType'], 'RequestResponse')
        self.assertEqual(factory.call_args.kwargs['config'].retries['total_max_attempts'], 1)

    def test_unconfigured_does_not_use_default_aws_credentials(self):
        with patch.dict(os.environ, {}, clear=True), patch('boto3.client') as factory:
            self.assertEqual(manual_sync.invoke()[0], 503)
        factory.assert_not_called()

    def test_function_error_is_not_reported_as_success_or_leaked(self):
        client = Mock()
        client.invoke.return_value = {'FunctionError': 'Unhandled', 'Payload': io.BytesIO(b'{"errorMessage":"sensitive details"}')}
        with patch.dict(os.environ, ENV), patch('boto3.client', return_value=client):
            code, result = manual_sync.invoke()
        self.assertEqual(code, 409)
        self.assertNotIn('sensitive details', json.dumps(result))

    def test_timeout_does_not_retry_or_claim_failure_to_commit(self):
        from botocore.exceptions import ReadTimeoutError
        client = Mock()
        client.invoke.side_effect = ReadTimeoutError(endpoint_url='https://example.com')
        with patch.dict(os.environ, ENV), patch('boto3.client', return_value=client):
            code, result = manual_sync.invoke()
        self.assertEqual(code, 202)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(client.invoke.call_count, 1)

    def test_overlap_never_claims_imported(self):
        client = Mock()
        client.invoke.return_value = {'Payload': io.BytesIO(b'{"status":"skipped-overlap"}')}
        with patch.dict(os.environ, ENV), patch('boto3.client', return_value=client):
            self.assertEqual(manual_sync.invoke()[0], 409)

    def test_hosted_transaction_is_closed_before_lambda_can_call_back(self):
        import app
        import server
        active = []
        @contextmanager
        def transaction(*args, **kwargs):
            active.append(True)
            try: yield Mock()
            finally: active.pop()
        routes = Mock()
        routes.session.return_value = 'admin-session'
        def invoke_after_commit(body):
            self.assertFalse(active)
            self.assertIsNone(server.HOSTED_STORE.get())
            return 'done'
        routes.dragonshield_sync.side_effect = invoke_after_commit
        with patch('hosted_store.transaction', transaction):
            self.assertEqual(app.hosted_request('db', routes, '/api/admin/dragonshield-sync', b'{}'), 'done')
        routes.session.return_value = None
        with patch('hosted_store.transaction', transaction):
            app.hosted_request('db', routes, '/api/admin/dragonshield-sync', b'{}')
        self.assertEqual(routes.dragonshield_sync.call_count, 1)
        self.assertEqual(routes.send.call_args.args[0], 401)
