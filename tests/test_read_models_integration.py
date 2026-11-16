"""Opt-in local PostgreSQL/HTTP privacy, import and resource benchmark."""
import unittest,shutil
import os,sys,subprocess,tempfile,time,io,json,csv,gzip,threading,concurrent.futures,statistics,contextlib,socket,urllib.request,urllib.error,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
import psycopg
from hosted_store import SCHEMA,S3_SCHEMA,PostgresStore
from collection_cache import COLLECTION_CACHE
from artwork_jobs import BATCH_SQL
import server
class MeterCursor:
 def __init__(self,cursor,meter):self.cursor=cursor;self.meter=meter
 def _count(self,rows):
  self.meter['bytes']+=len(json.dumps(rows,ensure_ascii=False,default=str).encode());return rows
 def fetchall(self):return self._count(self.cursor.fetchall())
 def fetchone(self):return self._count(self.cursor.fetchone())
class MeterConnection:
 def __init__(self,connection):self.connection=connection;self.info=connection.info;self.meter={'bytes':0,'calls':0}
 def execute(self,*args,**kwargs):self.meter['calls']+=1;return MeterCursor(self.connection.execute(*args,**kwargs),self.meter)

def measured_collection(connect,admin=False):
 with connect() as connection:
  connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
  measured=MeterConnection(connection);token=server.HOSTED_STORE.set(PostgresStore(measured))
  start=time.monotonic()
  try:result=server.collection_payload(admin)
  finally:server.HOSTED_STORE.reset(token)
  return result,measured.meter,(time.monotonic()-start)*1000

def legacy_read(connect):
 with connect() as connection:
  measured=MeterConnection(connection);store=PostgresStore(measured)
  start=time.monotonic()
  for name in ('collection.json','cache.json','spanish-names.json'):store.read(name)
  return measured.meter,(time.monotonic()-start)*1000

def sample_cluster(pid,stop,samples):
 while not stop.is_set():
  children=Path(f'/proc/{pid}/task/{pid}/children')
  pids=[pid]+[int(value) for value in children.read_text().split()] if children.exists() else [pid]
  pss=swap=0
  for process in pids:
   try:
    values={line.split(':',1)[0]:int(line.split()[1]) for line in Path(f'/proc/{process}/smaps_rollup').read_text().splitlines() if line.startswith(('Pss:','SwapPss:'))}
    pss+=values.get('Pss',0);swap+=values.get('SwapPss',0)
   except (FileNotFoundError,ProcessLookupError,PermissionError):pass
  samples.append((pss,swap));stop.wait(.04)

def load_phase(connect,legacy):
 stop=threading.Event();samples=[]
 pid=int(Path(connect.folder,'postmaster.pid').read_text().splitlines()[0])
 watcher=threading.Thread(target=sample_cluster,args=(pid,stop,samples));watcher.start()
 def reader(index):
  results=[]
  for _ in range(3 if legacy else 10):
   if legacy:meter,ms=legacy_read(connect)
   else:
    response,meter,ms=measured_collection(connect,index%2==0)
    assert response['admin']==(index%2==0)
    if not response['admin']:assert all('Folder Name' not in row and 'Price Bought' not in row for row in response['rows'])
   results.append((meter['bytes'],ms))
  return results
 start=time.monotonic()
 try:
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:groups=list(pool.map(reader,range(8)))
 finally:stop.set();watcher.join()
 values=[value for group in groups for value in group]
 return {'requests':len(values),'ms_mean':round(statistics.mean(ms for _,ms in values),2),
  'estimated_bytes_per_request':round(statistics.mean(size for size,_ in values)),
  'db_peak_pss_MiB':round(max(p for p,s in samples)/1024,2),'db_peak_swap_MiB':round(max(s for p,s in samples)/1024,2),
  'elapsed_s':round(time.monotonic()-start,2)}

LEGACY_BATCH_SQL = "WITH names AS MATERIALIZED (\n        SELECT DISTINCT entry->>'Card Name' AS name FROM duel_documents collection\n        CROSS JOIN LATERAL jsonb_array_elements(collection.value->'rows') entry\n        WHERE collection.name='collection.json' AND entry->>'Card Name' > %s\n        ORDER BY name LIMIT 50\n    ), catalog AS MATERIALIZED (\n        SELECT card->>'name' AS name,card FROM duel_documents source\n        CROSS JOIN LATERAL jsonb_array_elements(source.value->'data') card WHERE source.name='catalog.json'\n    ), cached AS MATERIALIZED (\n        SELECT card.key AS name,card.value AS info FROM duel_documents source\n        CROSS JOIN LATERAL jsonb_each(source.value->'cards') card WHERE source.name='cache.json'\n    ) SELECT names.name,catalog.card,cached.info FROM names\n      LEFT JOIN catalog ON lower(catalog.name)=lower(names.name)\n      LEFT JOIN cached ON cached.name=names.name ORDER BY names.name"


@unittest.skipUnless(os.getenv('RUN_POSTGRES_INTEGRATION') == '1' and shutil.which('initdb') and os.geteuid() != 0 and Path('/proc').exists(),
                     'Set RUN_POSTGRES_INTEGRATION=1 on Linux with PostgreSQL binaries to run.')
class ReadModelsIntegration(unittest.TestCase):
    def test_large_import_privacy_cache_and_load(self):
        with tempfile.TemporaryDirectory(prefix='challero-readmodels-') as folder,patch.dict(os.environ,{'DUEL_S3_BUCKET':'benchmark-artwork'}):
         subprocess.run(['initdb','-D',folder,'-A','trust','--no-locale','--encoding=UTF8'],check=True,stdout=subprocess.DEVNULL)
         subprocess.run(['pg_ctl','-D',folder,'-o',f"-k {folder} -p 55439 -h '' -c work_mem=2184kB -c shared_buffers=32MB",'-w','start'],check=True,stdout=subprocess.DEVNULL)
         def connect():return psycopg.connect(host=folder,port=55439,dbname='postgres')
         connect.folder=folder
         previous_data=server.DATA
         try:
          with connect() as conn:
           conn.execute(SCHEMA);conn.execute(S3_SCHEMA);store=PostgresStore(conn)
           count=6000
           # Exercise the real CSV parser, including public/private rows and stable positions.
           writer=io.StringIO();csv_writer=csv.DictWriter(writer,fieldnames=server.HEADERS);csv_writer.writeheader()
           for i in range(count):
            row={name:'' for name in server.HEADERS};row.update({'Folder Name':'Private' if i%4==0 else 'Public','Quantity':'2','Trade Quantity':'1','Card Name':f'Card {i:05}','Set Code':'TEST','Set Name':'Benchmark','Card Number':f'TEST-EN{i:05}','Rarity':'SR','Language':'English','Condition':'NearMint','Printing':'1st Edition','LOW':'1','MARKET':'3','MID':'2','Price Bought':'0.5'})
            csv_writer.writerow(row)
           start=time.monotonic();rows=server.parse_csv(writer.getvalue());parse_ms=(time.monotonic()-start)*1000
           state={'rows':rows,'settings':{'visible_folders':['Public'],'currency':'USD','markup':None},'revision':1}
           cards={row['Card Name']:{'id':i+1,'type':'Effect Monster','desc':'Representative effect text. '*20,'image':f'assets/{i+1}.webp','pricing':{'sets':[],'vendors':[],'fetched_at':time.time()}} for i,row in enumerate(rows)}
           cache={'cards':cards,'status':'Ready','checked':time.time(),'unmatched':[],'failures':{}}
           catalog={'updated':time.time(),'data':[{'id':i+1,'name':f'Card {i:05}','type':'Effect Monster','desc':'Representative catalog text. '*55} for i in range(12000)]}
           spanish={str(i+1):f'Nombre español {i}' for i in range(22000)}
           print('Catalog logical bytes:',len(json.dumps(catalog).encode()),flush=True)
           start=time.monotonic()
           for name,value in [('collection.json',state),('cache.json',cache),('catalog.json',catalog),('spanish-names.json',spanish)]:store.save(name,value)
           with conn.cursor() as cur:cur.executemany('INSERT INTO duel_artwork_objects VALUES(%s,%s,%s,%s)',[('benchmark-artwork',f'assets/{i+1}.webp','a'*64,123) for i in range(count)])
           conn.commit();import_ms=(time.monotonic()-start)*1000
           for table in ('duel_collection_entries','duel_catalog_cards','duel_card_metadata','duel_spanish_names'):conn.execute('ANALYZE '+table)
           conn.commit()
           # Compare API rows against the original disk-backed implementation.
           data=Path(folder,'data');data.mkdir();(data/'assets').mkdir();server.DATA=data
           for name,value in [('collection.json',state),('cache.json',cache),('spanish-names.json',spanish)]:Path(data,name).write_text(json.dumps(value))
           for i in range(count):Path(data,'assets',f'{i+1}.webp').touch()
           legacy_public=server.collection_payload(False)
           public,cold,ms=measured_collection(connect,False)
           assert public['rows']==legacy_public['rows']
           assert len(public['rows'])==4500
           assert all(row['_id']%4!=0 for row in public['rows'])
           assert all('Price Bought' not in row and 'Folder Name' not in row and 'pricing' not in row['info'] for row in public['rows'])
           admin,admin_cold,admin_ms=measured_collection(connect,True)
           assert len(admin['rows'])==6000 and admin['rows'][0]['Price Bought']=='0.5'
           second,warm,warm_ms=measured_collection(connect,False)
           assert second==public and warm['calls']==1
           public['rows'].clear();assert len(measured_collection(connect,False)[0]['rows'])==4500
           before,before_ms=legacy_read(connect)
           print('Collection bytes:',json.dumps({'legacy':before,'public_cold':cold,'public_warm':warm,'admin_cold':admin_cold}),flush=True)
           # Artwork repairs invalidate cached existence checks too.
           conn.execute('DELETE FROM duel_artwork_objects WHERE path=%s',('assets/2.webp',))
           store.save('artwork-version.json',{'repair':1});conn.commit()
           assert 'image' not in measured_collection(connect,False)[0]['rows'][0]['info']
           store.save_object('assets/2.webp','a'*64,123);conn.commit()
           assert measured_collection(connect,False)[0]['rows'][0]['info']['image']=='assets/2.webp'
           # API roles cannot enumerate any of the derived private data.
           conn.execute('CREATE ROLE read_model_visitor')
           for relation in ('duel_read_heads','duel_collection_entries','duel_card_metadata','duel_catalog_cards','duel_spanish_names'):
            conn.execute('GRANT SELECT ON '+relation+' TO read_model_visitor')
           conn.execute('SET LOCAL ROLE read_model_visitor')
           for relation in ('duel_read_heads','duel_collection_entries','duel_card_metadata','duel_catalog_cards','duel_spanish_names'):
            assert conn.execute('SELECT count(*) FROM '+relation).fetchone()[0]==0
           conn.rollback()
           # Exercise FastAPI gzip, a real CSV preview/import, and concurrent image redirects.
           import urllib.parse
           with socket.socket() as available:
            available.bind(('127.0.0.1',0));http_port=available.getsockname()[1]
           salt=b'benchmark-salt-only'
           store.save('auth.json',{'salt':salt.hex(),'hash':hashlib.scrypt(b'benchmark-password',salt=salt,n=16384,r=8,p=1).hex()});conn.commit()
           env=dict(os.environ,DATABASE_URL=f'postgresql://{conn.info.user}@localhost/postgres?host={folder}&port=55439',
               DUEL_PUBLIC_ORIGIN='https://benchmark.local',DUEL_S3_REGION='us-east-1',
               DUEL_S3_ACCESS_KEY_ID='benchmark-key',DUEL_S3_SECRET_ACCESS_KEY='benchmark-secret')
           api=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(http_port),'--log-level','warning'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
           def http(path,body=None,cookie=None):
            headers={'Host':'benchmark.local','Accept-Encoding':'gzip'}
            if body is not None:headers.update({'Origin':'https://benchmark.local','X-Requested-With':'DuelArchive','Content-Type':'application/json'})
            if cookie:headers['Cookie']=cookie
            request=urllib.request.Request(f'http://127.0.0.1:{http_port}'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            try:response=urllib.request.urlopen(request,timeout=60)
            except urllib.error.HTTPError as error:response=error
            content=response.read();response.close()
            raw=gzip.decompress(content) if response.headers.get('Content-Encoding')=='gzip' else content
            return response.code,json.loads(raw),response.headers,len(content),len(raw)
           try:
            for retry in range(60):
             try:
              if http('/api/revision')[0]==200:break
             except OSError:time.sleep(.1)
            response=http('/api/collection')
            assert response[0]==200 and response[2]['Content-Encoding']=='gzip',(response[0],response[1].get('error'),str(response[2]))
            assert len(response[1]['rows'])==4500 and response[3]<4_500_000
            print(f'HTTP public payload: {response[4]} uncompressed bytes -> {response[3]} gzip bytes.',flush=True)
            assert http('/api/admin')[0]==401
            status,_,headers,_,_=http('/api/login',{'password':'benchmark-password'})
            assert status==200
            cookie=headers['Set-Cookie'].split(';')[0]
            start=time.monotonic()
            preview=http('/api/admin/preview',{'csv':writer.getvalue()},cookie)
            assert preview[0]==200
            imported=http('/api/admin/import',{'token':preview[1]['token']},cookie)
            assert imported[0]==200
            print(f'HTTP 6,000-row CSV preview/import completed in {time.monotonic()-start:.2f}s.',flush=True)
            # The admin workflow reads only the indexed catalog page, even with a large catalog.
            assert http('/api/admin/artwork-batch',{},cookie)[0]==200
            batch_reply=http('/api/admin/artwork-batch',{},cookie)
            assert batch_reply[0]==200 and batch_reply[1]['processed']==50
            class NoRedirect(urllib.request.HTTPRedirectHandler):
             def redirect_request(self,*args,**kwargs):return None
            def image_requests(index):
             opener=urllib.request.build_opener(NoRedirect)
             for j in range(10):
              public=(index+j)%2==0
              request=urllib.request.Request(f'http://127.0.0.1:{http_port}/assets/{2 if public else 1}.webp',headers={'Host':'benchmark.local'})
              try:image=opener.open(request,timeout=30)
              except urllib.error.HTTPError as error:image=error
              assert image.code==(302 if public else 404)
              image.close()
            image_requests(0)
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(image_requests,range(8)))
            assert conn.execute('SELECT count(*) FROM duel_artwork').fetchone()[0]==0
            print('80 concurrent HTTP image checks passed: authorized S3 redirects and private 404s; zero DB images.',flush=True)
           finally:
            api.terminate();api.wait(timeout=10)
           # Restore the fixture after the real import changed its revision/metadata.
           store.save('collection.json',state);store.save('cache.json',cache);conn.commit()
           # Commit/rollback privacy and raw SQL invalidation, across new connection instances.
           state['settings']['visible_folders']=[];state['revision']+=1;store.save('collection.json',state)
           assert len(measured_collection(connect,False)[0]['rows'])==4500
           conn.commit();assert measured_collection(connect,False)[0]['rows']==[]
           assert len(measured_collection(connect,True)[0]['rows'])==6000
           state['settings']['visible_folders']=['Public'];state['revision']+=1
           state['rows'][1]['LOW']='40';state['rows'][1]['MARKET']='60';state['rows'][1]['Quantity']='5'
           store.save('collection.json',state);conn.commit()
           row=measured_collection(connect,False)[0]['rows'][0]
           assert row['_id']==1 and row['copies']==5 and row['public_price']=='50.00'
           conn.execute("UPDATE duel_documents SET value=jsonb_set(value,'{2}',%s) WHERE name='spanish-names.json'",(psycopg.types.json.Jsonb('Traducción nueva'),));conn.commit()
           assert measured_collection(connect,False)[0]['rows'][0]['spanish_name']=='Traducción nueva'
           state['settings']['visible_folders']=[];store.save('collection.json',state);conn.rollback()
           assert len(measured_collection(connect,False)[0]['rows'])==4500
           conn.execute("UPDATE duel_documents SET value=jsonb_set(value,'{cards,Card 00001,desc}',%s) WHERE name='cache.json'",(psycopg.types.json.Jsonb('Updated effect'),));conn.commit()
           assert measured_collection(connect,False)[0]['rows'][0]['info']['desc']=='Updated effect'
           # Query plans: the new batch must not expand either JSON document.
           old_sql=LEGACY_BATCH_SQL
           old_plan=conn.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+old_sql,('',)).fetchone()[0][0]
           new_plan=conn.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+BATCH_SQL,('',)).fetchone()[0][0]
           assert 'duel_catalog_cards_pkey' in str(new_plan)
           assert 'jsonb_array_elements' not in str(new_plan)
           assert new_plan['Plan'].get('Temp Written Blocks',0)==0
           print('Batch plans:',json.dumps({'old_ms':old_plan['Execution Time'],'old_temp_blocks':old_plan['Plan'].get('Temp Written Blocks',0),'new_ms':new_plan['Execution Time'],'new_temp_blocks':new_plan['Plan'].get('Temp Written Blocks',0)}),flush=True)
           conn.commit()
          measured_collection(connect,False);measured_collection(connect,True)
          legacy_load=load_phase(connect,True)
          optimized_load=load_phase(connect,False)
          results={'csv_rows':count,'parse_ms':round(parse_ms,2),'import_and_indexes_ms':round(import_ms,2),'collection_bytes':{'legacy':before,'cold':cold,'warm':warm},'batch':{'old_ms':old_plan['Execution Time'],'new_ms':new_plan['Execution Time'],'old_temp_blocks':old_plan['Plan'].get('Temp Written Blocks',0),'new_temp_blocks':new_plan['Plan'].get('Temp Written Blocks',0)},'legacy_load':legacy_load,'optimized_load':optimized_load,'response_cache_bytes':COLLECTION_CACHE.size}
          Path('work').mkdir(exist_ok=True)
          Path('work/read_models_benchmark.json').write_text(json.dumps(results,indent=2))
          print(json.dumps(results,indent=2),flush=True)
          print('Read model integration passed: 6,000-row CSV, response equivalence, public/admin isolation, atomic invalidation, rollback, translation/metadata changes and concurrent readers.',flush=True)
         finally:
          server.DATA=previous_data
          subprocess.run(['pg_ctl','-D',folder,'-m','immediate','stop'],check=True,stdout=subprocess.DEVNULL)
