"""PostgreSQL storage for the hosted FastAPI entry point."""
from contextlib import contextmanager
import time

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


@contextmanager
def transaction(url, write=False):
    with psycopg.connect(url, connect_timeout=10) as connection:
        if write:
            # Serialize admin mutations across instances, including revision and session checks.
            connection.execute('SELECT pg_advisory_xact_lock(6152847001)')
            connection.execute('DELETE FROM duel_runtime WHERE expires_at <= %s', (time.time(),))
        else:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        yield PostgresStore(connection)
