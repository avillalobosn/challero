"""Initialize hosted storage or process queued artwork and metadata updates."""
import argparse
import os

import psycopg

from hosted_store import PostgresStore, SCHEMA
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
    for path in (server.DATA / 'assets').glob('*.jpg'):
        if path.stem.isascii() and path.stem.isdigit() and path.is_file():
            store.save_image(f'assets/{path.name}', path.read_bytes())
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['migrate', 'refresh'])
    parser.add_argument('--force', action='store_true', help='Run a refresh even when none is queued.')
    args = parser.parse_args()
    url = os.getenv('DATABASE_URL')
    if not url:
        parser.error('Set DATABASE_URL before running this command.')
    try:
        with psycopg.connect(url, connect_timeout=10) as connection:
            message = migrate(connection) if args.action == 'migrate' else refresh(connection, args.force)
    except psycopg.Error as error:
        raise SystemExit(f'PostgreSQL operation failed ({error.sqlstate or type(error).__name__}).') from None
    print(message)


if __name__ == '__main__':
    main()
