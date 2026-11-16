"""Admin-driven, resumable artwork batches. No visitor polling or scheduled worker."""
import json
import time
import urllib.request
from psycopg.types.json import Jsonb

import server


def process_batch(store):
    connection = store.connection
    if not connection.execute('SELECT pg_try_advisory_xact_lock(6152847002)').fetchone()[0]:
        return {'done': False, 'busy': True, 'message': 'An artwork refresh is already running. Reload after it finishes.'}
    if store.runtime_get('maintenance', 'artwork-conversion'):
        return {'done': False, 'busy': True, 'message': 'Artwork maintenance is running. Try again after it finishes.'}
    requested = store.read('refresh-request.json', {})
    request_id = requested.get('requested_at', 0)
    if request_id <= store.read('refresh-completed.json', {}).get('requested_at', 0):
        return {'done': True, 'message': 'Artwork is up to date.'}
    # Fail before any downloads if this database requires S3 but configuration is missing.
    uses_s3 = store.s3_enabled()
    job = store.read('artwork-job.json', {})
    if job.get('requested_at') != request_id:
        job = {'requested_at': request_id, 'cursor': '', 'processed': 0, 'matched': 0,
               'downloaded': 0, 'unmatched': [], 'failures': {}, 'catalog_error': None}
        row = connection.execute("SELECT (value->>'updated')::double precision FROM duel_documents WHERE name='catalog.json'").fetchone()
        updated = row[0] if row else 0
        missing = connection.execute("""WITH cached AS MATERIALIZED (
            SELECT value->'cards' AS cards,coalesce(value->'unmatched','[]'::jsonb) AS unmatched
            FROM duel_documents WHERE name='cache.json'
        ), names AS MATERIALIZED (SELECT DISTINCT entry->>'Card Name' AS name
            FROM duel_documents collection CROSS JOIN LATERAL jsonb_array_elements(collection.value->'rows') entry
            WHERE collection.name='collection.json')
            SELECT EXISTS(SELECT 1 FROM names,cached WHERE NOT cached.cards ? names.name
                AND NOT cached.unmatched ? names.name)""").fetchone()[0]
        # New names may have been added upstream since a recent catalog refresh.
        if requested.get('force') or not updated or time.time()-updated > server.CATALOG_TTL or missing:
            try:
                req=urllib.request.Request('https://db.ygoprodeck.com/api/v7/cardinfo.php',headers={'User-Agent':'DuelArchiveLocal/1.0'})
                with urllib.request.urlopen(req,timeout=20) as response: catalog=json.load(response)['data']
                if not isinstance(catalog,list) or len(catalog)<1000: raise ValueError('Incomplete catalog')
                updated = time.time()
                store.save('catalog.json', {'updated': updated, 'data': catalog})
            except Exception as error:
                job['catalog_error'] = 'Catalog refresh failed ('+type(error).__name__+'). Existing metadata retained.'
                if not row: raise ValueError(job['catalog_error'])
        job['catalog_updated'] = updated
        store.save('artwork-job.json', job)
        # Commit the catalog separately from artwork downloads, including for large imports.
        return {'done': False, 'processed': 0, 'message': 'Card catalog ready. Preparing artwork…'}

    if job.get('finished'):
        return {'done': True, 'processed': job['processed'], 'failures': len(job['failures']),
                'message': 'Some artwork needs a retry. Press Refresh to retry failed downloads.'}

    batch = connection.execute("""WITH names AS MATERIALIZED (
        SELECT DISTINCT entry->>'Card Name' AS name FROM duel_documents collection
        CROSS JOIN LATERAL jsonb_array_elements(collection.value->'rows') entry
        WHERE collection.name='collection.json' AND entry->>'Card Name' > %s
        ORDER BY name LIMIT 50
    ), catalog AS MATERIALIZED (
        SELECT card->>'name' AS name,card FROM duel_documents source
        CROSS JOIN LATERAL jsonb_array_elements(source.value->'data') card WHERE source.name='catalog.json'
    ), cached AS MATERIALIZED (
        SELECT card.key AS name,card.value AS info FROM duel_documents source
        CROSS JOIN LATERAL jsonb_each(source.value->'cards') card WHERE source.name='cache.json'
    ) SELECT names.name,catalog.card,cached.info FROM names
      LEFT JOIN catalog ON lower(catalog.name)=lower(names.name)
      LEFT JOIN cached ON cached.name=names.name ORDER BY names.name""", (job['cursor'],)).fetchall()

    paths = [f"assets/{int((card or info)['id'])}.webp" for _,card,info in batch if (card or info or {}).get('id')]
    if uses_s3:
        import os
        existing = {row[0] for row in connection.execute('SELECT path FROM duel_artwork_objects WHERE bucket=%s AND path=ANY(%s) UNION SELECT path FROM duel_artwork WHERE path=ANY(%s)', (os.environ['DUEL_S3_BUCKET'],paths,paths))}
    else:
        existing = {row[0] for row in connection.execute('SELECT path FROM duel_artwork WHERE path=ANY(%s)', (paths,))}
    patch = {}
    downloads = 0
    for name,card,old in batch:
        if downloads >= 3: break
        info = ({key:card[key] for key in server.INFO_FIELDS if key in card} if card else dict(old or {}))
        if card: info['pricing'] = server.pricing_metadata(card,job['catalog_updated'])
        if not info.get('id'):
            job['unmatched'].append(name)
        else:
            job['matched'] += 1
            path = f"assets/{int(info['id'])}.webp"
            if path not in existing:
                downloads += 1
                try:
                    req=urllib.request.Request('https://images.ygoprodeck.com/images/cards/'+str(int(info['id']))+'.jpg',headers={'User-Agent':'DuelArchiveLocal/1.0'})
                    with urllib.request.urlopen(req,timeout=12) as response: image=response.read(5*1024*1024)
                    if not image.startswith(b'\xff\xd8') or len(image)<1000: raise ValueError('Invalid artwork response')
                    store.save_image(path,server.compressed_image(image))
                    existing.add(path)
                    job['downloaded'] += 1
                except Exception as error:
                    job['failures'][name] = 'Artwork download failed ('+type(error).__name__+').'
                time.sleep(.15)
            if path in existing: info['image'] = path
            patch[name] = info
        job['processed'] += 1
        job['cursor'] = name
    finished = not batch or (len(batch)<50 and job['cursor']==batch[-1][0])
    if finished:
        job['finished'] = True
        if not job['failures'] and not job['catalog_error']:
            store.save('refresh-completed.json', {'requested_at': request_id})
    summary = {'status': f"{'Ready' if finished else 'Refreshing'} · {job['matched']} matched names · {len(job['failures'])} artwork downloads pending",
               'failures': job['failures'], 'unmatched': job['unmatched'], 'error': job['catalog_error'],
               'last_refresh': job['catalog_updated'], 'checked': time.time()}
    connection.execute("""UPDATE duel_documents SET value =
        jsonb_set(value - 'price_coverage','{cards}',coalesce(value->'cards','{}'::jsonb) || %s,true) || %s
        WHERE name='cache.json'""", (Jsonb(patch),Jsonb(summary)))
    store.save('artwork-job.json', job)
    return {'done': finished, 'processed': job['processed'], 'downloaded': job['downloaded'],
            'failures': len(job['failures']), 'message': summary['status']}
