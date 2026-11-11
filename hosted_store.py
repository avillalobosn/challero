"""PostgreSQL storage for the hosted FastAPI entry point."""
from contextlib import contextmanager
import time
import re
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

    def image_paths(self):
        return {row[0] for row in self.connection.execute('SELECT path FROM duel_artwork')}

    def image(self, path):
        row = self.connection.execute(
            'SELECT image FROM duel_artwork WHERE path = %s', (path,)
        ).fetchone()
        return row[0] if row else None

    def save_image(self, path, image):
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
def transaction(url, write=False):
    with psycopg.connect(connection_url(url), connect_timeout=10, prepare_threshold=None) as connection:
        if write:
            # Serialize admin mutations across instances, including revision and session checks.
            connection.execute('SELECT pg_advisory_xact_lock(6152847001)')
            connection.execute('DELETE FROM duel_runtime WHERE expires_at <= %s', (time.time(),))
        else:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        yield PostgresStore(connection)
