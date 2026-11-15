"""Private, content-addressed S3 artwork. Credentials never reach the browser."""
import base64
from functools import lru_cache
import hashlib
import os
import re


class ArtworkStorageError(RuntimeError):
    pass


def enabled():
    return bool(os.getenv('DUEL_S3_BUCKET'))


def image_origin():
    region = os.getenv('DUEL_S3_REGION', '')
    return f'https://s3.{region}.amazonaws.com' if enabled() and re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-[0-9]+', region) else None


class S3Artwork:
    def __init__(self):
        import boto3
        from botocore.config import Config
        self.bucket = os.environ['DUEL_S3_BUCKET']
        region = os.getenv('DUEL_S3_REGION', '')
        key = os.getenv('DUEL_S3_ACCESS_KEY_ID')
        secret = os.getenv('DUEL_S3_SECRET_ACCESS_KEY')
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', self.bucket) or not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-[0-9]+', region) or not key or not secret:
            raise ArtworkStorageError('S3 configuration is incomplete.')
        self.origin = f'https://s3.{region}.amazonaws.com'
        self.client = boto3.client('s3', region_name=region, endpoint_url=self.origin,
            aws_access_key_id=key, aws_secret_access_key=secret,
            aws_session_token=os.getenv('DUEL_S3_SESSION_TOKEN') or None,
            config=Config(signature_version='s3v4', s3={'addressing_style': 'path'},
                          connect_timeout=5, read_timeout=30, retries={'max_attempts': 3}))

    @staticmethod
    def key(digest):
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise ArtworkStorageError('Invalid artwork checksum.')
        return f'duel-artwork/{digest}.webp'

    def verified(self, digest, size):
        from botocore.exceptions import ClientError
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=self.key(digest), ChecksumMode='ENABLED')
        except ClientError as error:
            if error.response['Error']['Code'] in ('404', 'NoSuchKey', 'NotFound'):
                return False
            raise ArtworkStorageError('Unable to verify S3 artwork.') from None
        return (head.get('ContentLength') == size and head.get('ContentType') == 'image/webp'
                and head.get('ChecksumSHA256') == base64.b64encode(bytes.fromhex(digest)).decode())

    def upload(self, image):
        if image[:4] != b'RIFF' or image[8:12] != b'WEBP':
            raise ArtworkStorageError('Only compressed WebP artwork can be uploaded.')
        digest = hashlib.sha256(image).hexdigest()
        self.client.put_object(Bucket=self.bucket, Key=self.key(digest), Body=image,
            ContentType='image/webp', CacheControl='private, no-cache, must-revalidate',
            ServerSideEncryption='AES256', ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode())
        if not self.verified(digest, len(image)):
            raise ArtworkStorageError('S3 artwork verification failed; database copy retained.')
        return digest, len(image)

    def download(self, digest):
        response = self.client.get_object(Bucket=self.bucket, Key=self.key(digest))
        try:
            image = response['Body'].read(5 * 1024 * 1024 + 1)
        finally:
            response['Body'].close()
        if hashlib.sha256(image).hexdigest() != digest:
            raise ArtworkStorageError('S3 artwork checksum mismatch.')
        return image

    def signed_url(self, digest):
        return self.client.generate_presigned_url('get_object',
            Params={'Bucket': self.bucket, 'Key': self.key(digest)}, ExpiresIn=120)


@lru_cache(maxsize=1)
def storage():
    return S3Artwork()
