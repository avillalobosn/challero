"""Initialize hosted storage or process queued artwork and metadata updates."""
import argparse
import os

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['migrate', 'refresh', 'check'])
    parser.add_argument('--force', action='store_true', help='Run a refresh even when none is queued.')
    args = parser.parse_args()
    url = os.getenv('DATABASE_URL')
    if not url:
        parser.error('Set DATABASE_URL before running this command.')
    try:
        with psycopg.connect(connection_url(url), connect_timeout=10, prepare_threshold=None) as connection:
            if args.action == 'check':
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
