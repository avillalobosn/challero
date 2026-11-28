"""PostgreSQL storage for the hosted FastAPI entry point."""
from contextlib import contextmanager
import time
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, unquote

import psycopg
from psycopg.types.json import Jsonb

SCHEMA = """
CREATE TABLE IF NOT EXISTS duel_documents (
    name text PRIMARY KEY,
    value jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS duel_artwork (
    path text PRIMARY KEY,
    image bytea NOT NULL
);
CREATE TABLE IF NOT EXISTS duel_runtime (
    kind text NOT NULL,
    key text NOT NULL,
    value jsonb NOT NULL,
    expires_at double precision NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE INDEX IF NOT EXISTS duel_runtime_expiry ON duel_runtime (expires_at);
"""


S3_SCHEMA = """
CREATE TABLE IF NOT EXISTS duel_artwork_objects (
    bucket text NOT NULL,
    path text NOT NULL,
    sha256 text NOT NULL,
    size bigint NOT NULL,
    PRIMARY KEY (bucket, path)
);
"""


IMAGE_PERMISSIONS_SCHEMA = Path(__file__).with_name('image_permissions.sql').read_text()
READ_MODELS_SCHEMA = Path(__file__).with_name('read_models.sql').read_text()
SCHEMA += IMAGE_PERMISSIONS_SCHEMA + READ_MODELS_SCHEMA


class PostgresStore:
    def __init__(self, connection):
        self.connection = connection

    def read(self, name, default=None):
        row = self.connection.execute(
            'SELECT value FROM duel_documents WHERE name = %s', (name,)
        ).fetchone()
        return row[0] if row else default

    def save(self, name, value):
        self.connection.execute(
            'INSERT INTO duel_documents VALUES (%s, %s) '
            'ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value',
            (name, Jsonb(value)),
        )

    def sync_token(self):
        # Only row versions leave PostgreSQL, never the document bodies.
        rows = self.connection.execute(
            "SELECT name, xmin::text FROM duel_documents WHERE name = ANY(%s) ORDER BY name",
            (['collection.json', 'cache.json', 'spanish-names.json', 'refresh-request.json', 'refresh-completed.json', 'artwork-version.json', 'artwork-storage.json', 'read-model-version.json'],),
        ).fetchall()
        import hashlib
        return hashlib.sha256(repr(rows).encode()).hexdigest()

    def collection_cache_key(self, admin, token):
        import os
        info=self.connection.info
        return (info.host,info.port,info.dbname,info.user,os.getenv('DUEL_S3_BUCKET',''),bool(admin),token)

    def collection_inputs(self, admin):
        head=self.connection.execute("SELECT value FROM public.duel_read_heads WHERE name='collection.json'").fetchone()
        state=dict(head[0]) if head else {'settings':{'visible_folders':[],'currency':'USD'},'revision':0}
        visible=state['settings']['visible_folders']
        entries=self.connection.execute("SELECT position,CASE WHEN %s THEN row_data ELSE row_data - ARRAY['Folder Name','MID','Price Bought']::text[] END "
            'FROM public.duel_collection_entries WHERE %s OR folder=ANY(%s) ORDER BY position',(admin,admin,visible)).fetchall()
        state['rows']=[row for _,row in entries]
        names=list({row['Card Name'] for _,row in entries})
        metadata=self.connection.execute("SELECT metadata.name, "
            "CASE WHEN %s THEN metadata.info ELSE metadata.info-'pricing' END, "
            "coalesce(spanish.name,'') FROM public.duel_card_metadata metadata "
            "LEFT JOIN public.duel_spanish_names spanish ON spanish.id=metadata.info->>'id' "
            "WHERE metadata.name=ANY(%s)",(admin,names)).fetchall()
        cards={name:info for name,info,_ in metadata}
        spanish={str(info.get('id')):translated for _,info,translated in metadata}
        cache={}
        if admin:
            summary=self.connection.execute("SELECT value FROM public.duel_read_heads WHERE name='cache.json'").fetchone()
            if summary:cache=dict(summary[0])
        cache['cards']=cards
        paths=list({str(Path(info['image']).with_suffix('.webp')) for info in cards.values() if info.get('image')})
        if self.s3_enabled():
            import os
            found=self.connection.execute('SELECT path FROM duel_artwork WHERE path=ANY(%s) UNION '
                'SELECT path FROM duel_artwork_objects WHERE bucket=%s AND path=ANY(%s)',
                (paths,os.environ['DUEL_S3_BUCKET'],paths)).fetchall()
        else:
            found=self.connection.execute('SELECT path FROM duel_artwork WHERE path=ANY(%s)',(paths,)).fetchall()
        images={row[0] for row in found}
        return state,cache,spanish,images,[position for position,_ in entries]

    def image_allowed(self, path, admin):
        canonical = path.removesuffix('.jpg') + '.webp' if path.endswith('.jpg') else path
        return self.connection.execute(
            'SELECT EXISTS (SELECT 1 FROM public.duel_image_permissions '
            'WHERE path = %s AND (%s OR public_visible))', (canonical, admin),
        ).fetchone()[0]

    def s3_enabled(self):
        from s3_artwork import enabled, ArtworkStorageError
        if enabled(): return True
        if not hasattr(self, '_s3_required'):
            self._s3_required = self.read('artwork-storage.json', {}).get('backend') == 's3'
        if self._s3_required:
            raise ArtworkStorageError('This database uses S3 artwork. Configure the DUEL_S3 variables before running artwork operations.')
        return False

    def image_paths(self):
        query = 'SELECT path FROM duel_artwork'
        if self.s3_enabled():
            import os
            return {row[0] for row in self.connection.execute(query + ' UNION SELECT path FROM duel_artwork_objects WHERE bucket = %s', (os.environ['DUEL_S3_BUCKET'],))}
        return {row[0] for row in self.connection.execute(query)}

    def object_record(self, path):
        import os
        return self.connection.execute('SELECT sha256, size FROM duel_artwork_objects WHERE bucket = %s AND path = %s', (os.environ['DUEL_S3_BUCKET'], path)).fetchone()

    def save_object(self, path, digest, size):
        import os
        changed = self.connection.execute('INSERT INTO duel_artwork_objects VALUES (%s, %s, %s, %s) '
            'ON CONFLICT (bucket, path) DO UPDATE SET sha256 = EXCLUDED.sha256, size = EXCLUDED.size '
            'WHERE duel_artwork_objects.sha256 IS DISTINCT FROM excluded.sha256 OR duel_artwork_objects.size IS DISTINCT FROM excluded.size '
            'RETURNING path', (os.environ['DUEL_S3_BUCKET'], path, digest, size)).fetchone()
        if changed and re.fullmatch(r'assets/[0-9]+[.]webp',path):
            self.save('artwork-version.json', {'updated_at':time.time_ns()})

    def image(self, path):
        from s3_artwork import storage
        if self.s3_enabled() and (record := self.object_record(path)):
            return storage().download(record[0])
        row = self.connection.execute(
            'SELECT image FROM duel_artwork WHERE path = %s', (path,)
        ).fetchone()
        return row[0] if row else None

    def save_image(self, path, image):
        if not path.endswith('.webp') or image[:4] != b'RIFF' or image[8:12] != b'WEBP':
            raise ValueError('Only compressed WebP artwork can be stored.')
        from s3_artwork import storage
        if self.s3_enabled():
            digest, size = storage().upload(image)
            self.save_object(path, digest, size)
            return
        self.connection.execute(
            'INSERT INTO duel_artwork VALUES (%s, %s) '
            'ON CONFLICT (path) DO NOTHING', (path, image)
        )

    def runtime_get(self, kind, key):
        row = self.connection.execute(
            'SELECT value FROM duel_runtime WHERE kind = %s AND key = %s AND expires_at > %s',
            (kind, key, time.time()),
        ).fetchone()
        return row[0] if row else None

    def runtime_put(self, kind, key, value, expiry):
        self.connection.execute(
            'INSERT INTO duel_runtime VALUES (%s, %s, %s, %s) '
            'ON CONFLICT (kind, key) DO UPDATE SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at',
            (kind, key, Jsonb(value), expiry),
        )

    def runtime_delete(self, kind, key):
        self.connection.execute('DELETE FROM duel_runtime WHERE kind = %s AND key = %s', (kind, key))


class DatabaseURLConfigurationError(ValueError):
    """A credential-free explanation of an invalid connection URL."""


def connection_url(url):
    if url != url.strip():
        raise DatabaseURLConfigurationError('DATABASE_URL contains leading or trailing whitespace.')
    if url.startswith(('"', "'")) or url.endswith(('"', "'")):
        raise DatabaseURLConfigurationError('DATABASE_URL includes literal quotes. Remove them from the stored value.')
    if url.startswith('DATABASE_URL='):
        raise DatabaseURLConfigurationError('DATABASE_URL contains the variable assignment. Store only the connection URL.')
    if not url.startswith(('postgresql://', 'postgres://')):
        raise DatabaseURLConfigurationError('DATABASE_URL must start with postgresql:// or postgres://. Paste the full PostgreSQL connection URL as its value.')
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        raise DatabaseURLConfigurationError('DATABASE_URL has an invalid host or port. URL-encode special characters in the password.') from None
    if not parts.hostname:
        raise DatabaseURLConfigurationError('DATABASE_URL is missing its database hostname.')
    if parts.fragment:
        raise DatabaseURLConfigurationError('DATABASE_URL contains a # fragment. Encode # in the password as %23.')
    if re.search(r'%(?![0-9a-fA-F]{2})', url):
        raise DatabaseURLConfigurationError('DATABASE_URL contains an invalid percent escape. Encode a literal % as %25.')
    if unquote(parts.password or '').upper() in ('[YOUR-PASSWORD]', 'YOUR_PASSWORD', 'YOUR-PASSWORD'):
        raise DatabaseURLConfigurationError('Replace the password placeholder with your actual Supabase database password.')

    # Integration URLs may include options meant for Prisma or JavaScript clients.
    client_options = {'pgbouncer', 'connection_limit', 'pool_timeout', 'workaround', 'supa'}
    query = [(name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True)
             if name not in client_options]
    result = urlunsplit(parts._replace(query=urlencode(query)))
    try:
        psycopg.conninfo.conninfo_to_dict(result)
    except psycopg.ProgrammingError:
        raise DatabaseURLConfigurationError('DATABASE_URL has an unsupported PostgreSQL option or malformed URI. Copy the PostgreSQL URI from Supabase Connect.') from None
    return result


@contextmanager
def transaction(url, write=False, artwork_write=False):
    with psycopg.connect(connection_url(url), connect_timeout=10, prepare_threshold=None) as connection:
        if write:
            # Serialize admin mutations across instances, including revision and session checks.
            connection.execute('SELECT pg_advisory_xact_lock(6152847001)')
            connection.execute('DELETE FROM duel_runtime WHERE expires_at <= %s', (time.time(),))
        elif not artwork_write:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        yield PostgresStore(connection)
