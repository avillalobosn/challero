"""Opt in with RUN_POSTGRES_INTEGRATION=1; requires local PostgreSQL binaries."""
import os,subprocess,tempfile,sys,time,concurrent.futures,unittest,shutil,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import psycopg
from psycopg.types.json import Jsonb
from hosted_store import SCHEMA,PostgresStore

@unittest.skipUnless(os.getenv('RUN_POSTGRES_INTEGRATION') == '1' and shutil.which('initdb') and os.geteuid() != 0,
                     'Set RUN_POSTGRES_INTEGRATION=1 with PostgreSQL binaries to run.')
class ImagePermissionsIntegration(unittest.TestCase):
    def test_index_consistency_privacy_and_concurrency(self):
        with tempfile.TemporaryDirectory(prefix='challero-permissions-') as folder:
         subprocess.run(['initdb','-D',folder,'-A','trust','--no-locale','--encoding=UTF8'],check=True,stdout=subprocess.DEVNULL)
         subprocess.run(['pg_ctl','-D',folder,'-o',f"-k {folder} -p 55439 -h ''",'-w','start'],check=True,stdout=subprocess.DEVNULL)
         def connect():return psycopg.connect(host=folder,port=55439,dbname='postgres')
         try:
          with connect() as conn:
           conn.execute(SCHEMA);store=PostgresStore(conn)
           rows=[{'Card Name':f'Card {i}','Folder Name':'Public' if i%2==0 else 'Private'} for i in range(6200)]
           rows.append({'Card Name':'Card 0','Folder Name':'Private'})
           state={'rows':rows,'settings':{'visible_folders':['Public']}}
           cards={f'Card {i}':{'id':i,'image':f'assets/{i}.jpg','desc':'Card artwork effect text. '*40} for i in range(6200)}
           store.save('collection.json',state);store.save('cache.json',{'cards':cards});conn.commit()
           assert store.image_allowed('assets/0.webp',False)
           assert store.image_allowed('assets/0.jpg',False)
           assert not store.image_allowed('assets/1.webp',False)
           assert store.image_allowed('assets/1.webp',True)
           assert not store.image_allowed('assets/99999.webp',True)
           # A revocation is invisible until commit, then takes effect immediately.
           with connect() as observer:
            state['settings']['visible_folders']=[];store.save('collection.json',state)
            assert not store.image_allowed('assets/0.webp',False)
            assert PostgresStore(observer).image_allowed('assets/0.webp',False)
            conn.commit()
            assert not PostgresStore(observer).image_allowed('assets/0.webp',False)
           state['settings']['visible_folders']=['Public'];store.save('collection.json',state);conn.commit()
           # Raw SQL metadata updates (the upload worker) must update the index too.
           conn.execute("UPDATE duel_documents SET value=jsonb_set(value,'{cards,Card 0,image}',%s) WHERE name='cache.json'",(Jsonb('assets/7000.webp'),))
           assert not store.image_allowed('assets/0.webp',True)
           assert store.image_allowed('assets/7000.webp',False)
           conn.rollback()
           assert store.image_allowed('assets/0.webp',False)
           state['rows']=[r for r in state['rows'] if r['Card Name']!='Card 2'];store.save('collection.json',state)
           assert not store.image_allowed('assets/2.webp',True);conn.commit()
           # Concurrent collection and metadata writes must retain both changes.
           state['settings']['visible_folders']=[]
           store.save('collection.json',state)
           started=threading.Event()
           def change_artwork():
            with connect() as writer:
             started.set()
             writer.execute("UPDATE duel_documents SET value=jsonb_set(value,'{cards,Card 0,image}',%s) WHERE name='cache.json'",(Jsonb('assets/7000.webp'),))
           with concurrent.futures.ThreadPoolExecutor(max_workers=1) as writers:
            future=writers.submit(change_artwork)
            assert started.wait(3)
            time.sleep(.03)
            conn.commit()
            future.result(timeout=5)
           assert store.image_allowed('assets/7000.webp',True)
           assert not store.image_allowed('assets/7000.webp',False)
           assert not store.image_allowed('assets/0.webp',True)
           state['settings']['visible_folders']=['Public'];store.save('collection.json',state)
           conn.execute("UPDATE duel_documents SET value=jsonb_set(value,'{cards,Card 0,image}',%s) WHERE name='cache.json'",(Jsonb('assets/0.webp'),))
           conn.commit()
           # No policies: API roles cannot enumerate private card IDs.
           conn.execute('CREATE ROLE permissions_test_visitor')
           conn.execute('GRANT SELECT ON duel_image_permissions TO permissions_test_visitor')
           conn.execute('SET LOCAL ROLE permissions_test_visitor')
           assert conn.execute('SELECT count(*) FROM duel_image_permissions').fetchone()[0]==0
           conn.rollback()
           conn.execute('ANALYZE duel_image_permissions')
           plan=conn.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT EXISTS(SELECT 1 FROM duel_image_permissions WHERE path=%s AND (%s OR public_visible))",('assets/0.webp',False)).fetchone()[0][0]
           assert 'Index Scan' in str(plan)
           assert plan['Plan'].get('Temp Written Blocks',0)==0
           print('Indexed query execution ms:',plan['Execution Time'],flush=True)
           conn.commit()
          def worker(_):
           with connect() as conn:
            store=PostgresStore(conn)
            for i in range(100):
             assert store.image_allowed('assets/0.webp',False)
             assert not store.image_allowed('assets/1.webp',False)
          start=time.monotonic()
          with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(worker,range(8)))
          print(f'1,600 concurrent permission checks passed in {time.monotonic()-start:.2f}s with 6,200 cards.',flush=True)
          print('Permission integration passed: private/admin access, shared artwork, visibility revocation, metadata changes, removals, rollback and RLS.',flush=True)
         finally:subprocess.run(['pg_ctl','-D',folder,'-m','immediate','stop'],check=True,stdout=subprocess.DEVNULL)
