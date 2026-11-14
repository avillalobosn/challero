"""Initialize hosted storage or process queued artwork and metadata updates."""
import argparse
import os
import time

import psycopg

from hosted_store import PostgresStore, SCHEMA, connection_url, DatabaseURLConfigurationError
import server


def migrate(connection):
    connection.execute(SCHEMA)
    connection.execute('SELECT pg_advisory_xact_lock(6152847001)')
    store = PostgresStore(connection)
    if store.read('collection.json') is not None:
        raise SystemExit('This database already contains a collection.')
    for required in ('collection.json', 'auth.json'):
        if not (server.DATA / required).is_file():
            raise SystemExit(f'Run local setup first. Missing data/{required}.')
    for name in ('collection.json', 'collection.backup.json', 'auth.json', 'cache.json',
                 'catalog.json', 'spanish-names.json'):
        if (server.DATA / name).is_file():
            store.save(name, server.read(name))
    count = 0
    for path in (server.DATA / 'assets').iterdir():
        if path.suffix in ('.jpg', '.webp') and path.stem.isascii() and path.stem.isdigit() and path.is_file():
            store.save_image(f'assets/{path.stem}.webp', server.compressed_image(path.read_bytes()))
            count += 1
    return f'Migrated the collection, admin password hash, and {count} artwork files.'


def refresh(connection, force):
    if not connection.execute('SELECT pg_try_advisory_xact_lock(6152847002)').fetchone()[0]:
        return 'A metadata refresh is already running.'
    store = PostgresStore(connection)
    request = store.read('refresh-request.json', {})
    requested_at = request.get('requested_at', 0)
    completed_at = store.read('refresh-completed.json', {}).get('requested_at', 0)
    if not force and requested_at <= completed_at:
        return 'No refresh is queued.'

    token = server.HOSTED_STORE.set(store)
    try:
        server.refresh_cache(force or request.get('force', False))
        cache = store.read('cache.json', {})
        if cache.get('error') or cache.get('failures'):
            return 'Refresh completed with errors. See admin for details. The request remains queued.'
        store.save('refresh-completed.json', {'requested_at': requested_at})
        return 'Hosted metadata and artwork refreshed.'
    finally:
        server.HOSTED_STORE.reset(token)


def check(connection):
    connection.execute('SELECT 1')
    missing = []
    for table in ('duel_documents', 'duel_artwork', 'duel_runtime'):
        if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None:
            missing.append(table)
    if missing:
        return 'Connected. Missing tables: ' + ', '.join(missing) + '. Run migrate against this database.'
    store = PostgresStore(connection)
    collection = store.read('collection.json')
    auth = store.read('auth.json')
    if collection is None or auth is None:
        return 'Connected. Tables exist, but collection or admin credentials are missing.'
    return f"Connected. Migration complete. Collection contains {len(collection['rows'])} entries."


def cleanup(connection, apply=False):
    # Match only the obsolete format. Originals and current v2 thumbnails stay intact.
    pattern = r'^assets/[0-9]+-thumb\.webp$'
    if apply:
        rows = connection.execute(
            'DELETE FROM duel_artwork WHERE path ~ %s '
            'RETURNING path, octet_length(image)', (pattern,)
        ).fetchall()
        verb = 'Removed'
    else:
        rows = connection.execute(
            'SELECT path, octet_length(image) FROM duel_artwork WHERE path ~ %s ORDER BY path',
            (pattern,),
        ).fetchall()
        verb = 'Would remove'
    size = sum(row[1] for row in rows)
    return (f'{verb} {len(rows)} obsolete thumbnails ({size / 1024 / 1024:.2f} MiB). '
            'Original JPEGs and current v2 thumbnails are preserved.'
            + ('' if apply else ' Run cleanup --apply to delete them.'))


def compress_artwork(connection, apply=False, batch_size=25):
    pattern = r'^assets/[0-9]+\.jpg$'
    count, size = connection.execute(
        'SELECT count(*), coalesce(sum(octet_length(image)), 0) FROM duel_artwork WHERE path ~ %s',
        (pattern,),
    ).fetchone()
    connection.commit()
    if not apply:
        return (f'{count} JPEGs ({size / 1024 / 1024:.2f} MiB) to replace with WebP. '
                'Run compress-artwork --apply. Each replacement is saved before its JPEG is deleted.')
    # A session lock survives batch commits and excludes concurrent refresh workers.
    locked = connection.execute('SELECT pg_try_advisory_lock(6152847002)').fetchone()[0]
    connection.commit()
    if not locked:
        return 'An artwork refresh or conversion is already running. Try again when it finishes.'
    converted = failed = before = after = 0
    cursor = ''
    try:
        while True:
            with connection.transaction():
                paths = connection.execute(
                    'SELECT path FROM duel_artwork WHERE path ~ %s AND path > %s ORDER BY path LIMIT %s',
                    (pattern, cursor, batch_size),
                ).fetchall()
                if not paths: break
                store = PostgresStore(connection)
                for (path,) in paths:
                    cursor = path
                    try:
                        # One image at a time; a failed conversion retains its original.
                        with connection.transaction():
                            original = store.image(path)
                            if original is None: continue
                            compressed = server.compressed_image(original)
                            destination = path.removesuffix('.jpg') + '.webp'
                            connection.execute(
                                'INSERT INTO duel_artwork VALUES (%s, %s) '
                                'ON CONFLICT (path) DO UPDATE SET image = EXCLUDED.image',
                                (destination, compressed),
                            )
                            connection.execute('DELETE FROM duel_artwork WHERE path = %s', (path,))
                        converted += 1
                        before += len(original)
                        after += len(compressed)
                    except (OSError, ValueError) as error:
                        failed += 1
                        print(f'Kept {path}: {type(error).__name__}.', flush=True)
                store.save('artwork-version.json', {'updated_at': time.time_ns()})
            print(f'Committed {converted} converted; {failed} failed. Safe to interrupt and resume.', flush=True)
    finally:
        connection.rollback()
        connection.execute('SELECT pg_advisory_unlock(6152847002)')
        connection.commit()
    return (f'Replaced {converted} JPEGs: {before / 1024 / 1024:.2f} -> {after / 1024 / 1024:.2f} MiB. '
            f'{failed} failed originals retained. Rerun to retry; completed images are skipped.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['migrate', 'refresh', 'check', 'cleanup', 'compress-artwork'])
    parser.add_argument('--force', action='store_true', help='Run a refresh even when none is queued.')
    parser.add_argument('--apply', action='store_true', help='Apply cleanup or artwork conversion; both default to a preview.')
    parser.add_argument('--batch-size', type=int, default=25, help='Conversion batch size (1–100), default 25.')
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100:
        parser.error('--batch-size must be between 1 and 100.')
    if args.apply and args.action not in ('cleanup', 'compress-artwork'):
        parser.error('--apply is only valid with cleanup or compress-artwork.')
    url = os.getenv('DATABASE_URL')
    if not url:
        parser.error('Set DATABASE_URL before running this command.')
    try:
        with psycopg.connect(connection_url(url), connect_timeout=10, prepare_threshold=None) as connection:
            if args.action == 'compress-artwork':
                message = compress_artwork(connection, args.apply, args.batch_size)
            elif args.action == 'cleanup':
                message = cleanup(connection, args.apply)
            elif args.action == 'check':
                message = check(connection)
            elif args.action == 'migrate':
                message = migrate(connection)
            else:
                message = refresh(connection, args.force)
    except DatabaseURLConfigurationError as error:
        raise SystemExit(str(error)) from None
    except psycopg.Error as error:
        hint = ''
        if isinstance(error, psycopg.ProgrammingError) and not error.sqlstate:
            hint = ' Check that DATABASE_URL is a PostgreSQL URI, with the password URL-encoded and no surrounding quotes in its value.'
        raise SystemExit(f'PostgreSQL operation failed ({error.sqlstate or type(error).__name__}).{hint}') from None
    print(message)


if __name__ == '__main__':
    main()
