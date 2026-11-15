import base64
import hashlib
import io
import os
import unittest
from unittest.mock import Mock, patch
from PIL import Image
from botocore.stub import Stubber
import server
import s3_artwork

ENV = {'DUEL_S3_BUCKET':'test-card-artwork','DUEL_S3_REGION':'us-east-1',
       'DUEL_S3_ACCESS_KEY_ID':'testing-key','DUEL_S3_SECRET_ACCESS_KEY':'testing-secret'}

class S3Tests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        s3_artwork.storage.cache_clear()
    def tearDown(self):
        s3_artwork.storage.cache_clear()
        self.env.stop()
    def artwork(self):
        data=io.BytesIO();Image.new('RGB',(24,36),'purple').save(data,'WEBP');return data.getvalue()
    def test_upload_verified_by_s3_checksum_and_size(self):
        target=s3_artwork.S3Artwork();image=self.artwork();digest=hashlib.sha256(image).hexdigest()
        checksum=base64.b64encode(bytes.fromhex(digest)).decode()
        with Stubber(target.client) as stub:
            stub.add_response('put_object',{}, {'Bucket':target.bucket,'Key':target.key(digest),'Body':image,
                'ContentType':'image/webp','CacheControl':'private, no-cache, must-revalidate',
                'ServerSideEncryption':'AES256','ChecksumSHA256':checksum})
            stub.add_response('head_object',{'ContentLength':len(image),'ContentType':'image/webp','ChecksumSHA256':checksum},
                {'Bucket':target.bucket,'Key':target.key(digest),'ChecksumMode':'ENABLED'})
            self.assertEqual(target.upload(image),(digest,len(image)))
            stub.assert_no_pending_responses()
        with self.assertRaises(s3_artwork.ArtworkStorageError):target.upload(b'not a WebP')
    def test_corrupt_or_missing_object_is_not_verified(self):
        target=s3_artwork.S3Artwork();digest='a'*64
        with Stubber(target.client) as stub:
            params={'Bucket':target.bucket,'Key':target.key(digest),'ChecksumMode':'ENABLED'}
            stub.add_response('head_object',{'ContentLength':5,'ContentType':'image/webp','ChecksumSHA256':'wrong'},params)
            self.assertFalse(target.verified(digest,5))
            stub.add_client_error('head_object',service_error_code='404',http_status_code=404,expected_params=params)
            self.assertFalse(target.verified(digest,5))
    def test_missing_s3_config_cannot_recreate_database_image_copies(self):
        from hosted_store import PostgresStore
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = ({'backend':'s3'},)
        store = PostgresStore(connection)
        with patch.dict(os.environ, {'DUEL_S3_BUCKET':''}):
            with self.assertRaises(s3_artwork.ArtworkStorageError):
                store.save_image('assets/1.webp',self.artwork())
            with self.assertRaises(s3_artwork.ArtworkStorageError):
                store.image_paths()
        self.assertEqual(connection.execute.call_count,1)
        self.assertNotIn('INSERT',connection.execute.call_args.args[0])

    def test_signed_urls_expire_in_two_minutes(self):
        from urllib.parse import urlsplit,parse_qs
        target=s3_artwork.S3Artwork();url=target.signed_url('b'*64)
        self.assertEqual(urlsplit(url).hostname,'s3.us-east-1.amazonaws.com')
        self.assertEqual(parse_qs(urlsplit(url).query)['X-Amz-Expires'],['120'])
        self.assertIn('/test-card-artwork/duel-artwork/',url)
    def test_existing_database_thumbnail_is_uploaded_without_reencoding(self):
        class Routes(server.ArchiveRoutes):
            headers={}
            def session(self):return None
            def send(self,status,value,**kwargs):return status,value,kwargs
        image=self.artwork();digest=hashlib.sha256(image).hexdigest()
        store=Mock();store.image_allowed.return_value=True
        store.object_record.side_effect=[None,(digest,len(image))]
        store.image.return_value=image
        token=server.HOSTED_STORE.set(store)
        try:
            self.assertEqual(Routes().get('/assets/1-thumb-v2.webp')[0],302)
            store.image.assert_called_once_with('assets/1-thumb-v2.webp')
            store.save_image.assert_called_once_with('assets/1-thumb-v2.webp',image)
        finally:server.HOSTED_STORE.reset(token)

    def test_redirect_only_after_visibility_check_without_reading_image_body(self):
        class Routes(server.ArchiveRoutes):
            headers={}
            def session(self):return None
            def send(self,status,value,**kwargs):return status,value,kwargs
        store=Mock();store.image_allowed.return_value=True;store.object_record.return_value=('b'*64,10)
        store.image.side_effect=AssertionError('Database image body read')
        token=server.HOSTED_STORE.set(store)
        try:
            status,body,options=Routes().get('/assets/1.webp')
            self.assertEqual(status,302);self.assertEqual(body,b'')
            self.assertIn('X-Amz-Signature',options['extra_headers']['Location'])
            self.assertIn('no-store',options['extra_headers']['Cache-Control'])
            store.image.assert_not_called()
            store.image_allowed.return_value=False;store.object_record.reset_mock()
            self.assertEqual(Routes().get('/assets/1-thumb-v2.webp')[0],404)
            store.object_record.assert_not_called()
        finally:server.HOSTED_STORE.reset(token)
        self.assertIn('https://s3.us-east-1.amazonaws.com',server.response_headers('text/html')['Content-Security-Policy'])
