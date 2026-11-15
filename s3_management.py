"""Resumable S3 copy and separately verified PostgreSQL pruning."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import secrets
import time

from hosted_store import PostgresStore, S3_SCHEMA
from s3_artwork import ArtworkStorageError, enabled, storage


@contextmanager
def maintenance(connection):
    owner = secrets.token_hex(16)
    with connection.transaction():
        if not connection.execute('SELECT pg_try_advisory_xact_lock(6152847002)').fetchone()[0]:
            raise ArtworkStorageError('An artwork worker is running; try again later.')
        store = PostgresStore(connection)
        if store.runtime_get('maintenance', 'artwork-conversion'):
            raise ArtworkStorageError('An artwork worker is running; try again later.')
        store.runtime_put('maintenance', 'artwork-conversion', {'owner': owner}, time.time()+600)
    try:
        yield owner
    finally:
        connection.rollback()
        with connection.transaction():
            connection.execute('SELECT pg_advisory_xact_lock(6152847002)')
            store = PostgresStore(connection)
            if store.runtime_get('maintenance', 'artwork-conversion') == {'owner': owner}:
                store.runtime_delete('maintenance', 'artwork-conversion')


def s3_artwork(connection, apply=False, batch_size=25, prune=False):
    if not enabled():
        raise ArtworkStorageError('Set DUEL_S3_BUCKET, DUEL_S3_REGION and the DUEL_S3 access keys first.')
    target = storage()
    exists = connection.execute("SELECT to_regclass('duel_artwork_objects')").fetchone()[0] is not None
    count, size = connection.execute('SELECT count(*), coalesce(sum(octet_length(image)),0) FROM duel_artwork').fetchone()
    connection.commit()
    if not apply:
        return (f'{count} database artwork files ({size/1024/1024:.2f} MiB). '
                + ('Pruning verifies S3 checksums before deleting database copies.' if prune else 'Migration copies and verifies images; database copies stay intact.')
                + ' Add --apply to run.')
    if not exists:
        connection.execute(S3_SCHEMA)
        connection.commit()
    done = skipped = 0
    cursor = ''
    with maintenance(connection) as owner, ThreadPoolExecutor(max_workers=4) as transfers:
        while True:
            with connection.transaction():
                connection.execute('SELECT pg_advisory_xact_lock(6152847002)')
                store = PostgresStore(connection)
                if store.runtime_get('maintenance', 'artwork-conversion') != {'owner': owner}:
                    raise ArtworkStorageError('Worker lease expired; rerun to resume.')
                store.runtime_put('maintenance', 'artwork-conversion', {'owner': owner}, time.time()+600)
                if prune:
                    batch = connection.execute("""SELECT artwork.path, objects.sha256, objects.size,
                        encode(sha256(artwork.image),'hex'), octet_length(artwork.image)
                        FROM duel_artwork artwork JOIN duel_artwork_objects objects
                        ON objects.path = artwork.path AND objects.bucket = %s
                        WHERE artwork.path > %s ORDER BY artwork.path LIMIT %s""",
                        (target.bucket,cursor,batch_size)).fetchall()
                else:
                    batch = connection.execute("""SELECT path, image FROM duel_artwork artwork
                        WHERE path > %s AND NOT EXISTS (SELECT 1 FROM duel_artwork_objects objects
                        WHERE objects.bucket = %s AND objects.path = artwork.path)
                        ORDER BY path LIMIT %s""", (cursor,target.bucket,batch_size)).fetchall()
                if not batch: break
                cursor = batch[-1][0]
                if prune:
                    def verified(row):
                        _, digest, length, actual, actual_size = row
                        return digest == actual and length == actual_size and target.verified(digest,length)
                    checks = list(transfers.map(verified,batch))
                    deletions = [(row[0],row[1]) for row,valid in zip(batch,checks) if valid]
                    skipped += len(batch)-len(deletions)
                    with connection.cursor() as writer:
                        writer.executemany("DELETE FROM duel_artwork WHERE path = %s AND encode(sha256(image),'hex') = %s", deletions)
                    done += len(deletions)
                else:
                    uploaded = list(transfers.map(lambda row: target.upload(row[1]),batch))
                    with connection.cursor() as writer:
                        writer.executemany('INSERT INTO duel_artwork_objects VALUES (%s,%s,%s,%s) '
                            'ON CONFLICT (bucket,path) DO UPDATE SET sha256=EXCLUDED.sha256, size=EXCLUDED.size',
                            [(target.bucket,row[0],digest,length) for row,(digest,length) in zip(batch,uploaded)])
                    done += len(uploaded)
                store.save('artwork-version.json', {'updated_at': time.time_ns()})
            print(f'Committed {done} {"pruned" if prune else "copied"}; {skipped} retained after failed verification.',flush=True)
    return (f'{done} database copies removed; {skipped} retained.' if prune else
            f'{done} images copied and verified in S3. All database copies retained. Configure Vercel and verify before s3-prune.')
