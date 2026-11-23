import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import dragonshield_sync as sync
from server import HEADERS


def sample():
    stream = io.StringIO()
    stream.write('"sep=,"\r\n')
    writer = csv.DictWriter(stream, fieldnames=HEADERS)
    writer.writeheader()
    row = dict.fromkeys(HEADERS, '')
    row.update({'Folder Name': 'Test', 'Card Name': 'Example', 'Quantity': '2', 'Trade Quantity': '0'})
    writer.writerow(row)
    return stream.getvalue().encode()


class SyncTests(unittest.TestCase):
    def test_metadata_and_counts(self):
        self.assertEqual(sync.validate(sample()), {'entries': 1, 'copies': 2, 'folders': 1})
        with self.assertRaises(sync.SyncError): sync.validate(sample(), 2)
        with self.assertRaises(sync.SyncError): sync.validate(b'<html>sign in</html>')

    def test_rotation_saved_even_when_export_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'auth.json'
            sync.private_write(path, b'{"client_id":"test","refresh_token":"old"}')
            class Fake:
                calls = 0
                def json(self, url, **kw):
                    self.calls += 1
                    if self.calls == 1: return {'access_token': 'access', 'refresh_token': 'new'}
                    raise sync.SyncError('Export failed')
            with self.assertRaises(sync.SyncError): sync.download(Fake(), path)
            self.assertEqual(json.loads(path.read_text())['refresh_token'], 'new')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_untrusted_download_host_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'auth.json'
            sync.private_write(path, b'{"client_id":"test","refresh_token":"old"}')
            class Fake:
                def json(self, url, **kw):
                    return {'access_token':'access'} if url == sync.TOKEN_URL else {'data':'https://attacker.example/file.csv'}
            with self.assertRaises(sync.SyncError): sync.download(Fake(), path)

    def test_download_only_never_uploads(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'input.csv'
            path.write_bytes(sample())
            with patch('sys.argv', ['sync', '--input', str(path), '--output-dir', temp]), patch.object(sync, 'upload') as upload:
                self.assertEqual(sync.main(), 0)
                upload.assert_not_called()

    def test_removal_guard_blocks_commit(self):
        class Fake:
            paths=[]
            def json(self, url, **kw):
                self.paths.append(url)
                return {'removed_entries': 1, 'added_entries': 1, 'token': 'preview'}
        http=Fake()
        with patch.dict('os.environ', {'DUEL_ADMIN_PASSWORD': 'test'}):
            with self.assertRaises(sync.SyncError): sync.upload(http, 'https://example.com', sample(), 0)
        self.assertFalse(any(path.endswith('/import') for path in http.paths))

if __name__ == '__main__': unittest.main()
