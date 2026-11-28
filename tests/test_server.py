import csv, hashlib, io, json, tempfile, threading, time, unittest, urllib.error, urllib.request
from pathlib import Path
from unittest.mock import patch
import server as app

class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(); cls.old_data=app.DATA;app.DATA=Path(cls.temp.name)
        cls.http=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.origin=f'http://127.0.0.1:{cls.http.server_port}'
        threading.Thread(target=cls.http.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();app.DATA=cls.old_data;cls.temp.cleanup()
    def setUp(self):
        app.SESSIONS.clear();app.STAGED.clear();app.ATTEMPTS.clear()
        def row(folder,name,market):
            r={k:'' for k in app.HEADERS};r.update({'Folder Name':folder,'Card Name':name,'Quantity':'2','Trade Quantity':'1','MARKET':market,'LOW':'7.33','MID':'8.77','Price Bought':'4.41','Date Bought':'2026-01-01','Card Number':'ABC-EN001'})
            return r
        self.rows=[row('Public binder','Visible card','10.01'),row('Secret storage','Secret card','999.99')]
        app.save('collection.json',{'rows':self.rows,'settings':{'visible_folders':['Public binder'],'markup':25,'currency':'USD'},'revision':1})
        app.save('cache.json',{'cards':{'Visible card':{'id':1,'desc':'Visible effect','image':'assets/1.jpg'},'Secret card':{'id':2,'desc':'Secret effect','image':'assets/2.jpg'}},'unmatched':[]})
        (app.DATA/'assets').mkdir(exist_ok=True)
        from PIL import Image
        for old in (app.DATA/'assets').iterdir(): old.unlink()
        for number in (1,2): Image.new('RGB', (800,1200), 'purple').save(app.DATA/f'assets/{number}.jpg', 'JPEG')
        salt=b'test-only-salt-16';app.save('auth.json',{'salt':salt.hex(),'hash':hashlib.scrypt(b'test-password-123',salt=salt,n=16384,r=8,p=1).hex()})
        self.cookie=None
    def call(self,path,body=None,authenticated=True,headers=None):
        h={'X-Requested-With':'DuelArchive','Origin':self.origin}
        if self.cookie and authenticated:h['Cookie']=self.cookie
        if headers:h.update(headers)
        req=urllib.request.Request(self.origin+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
        try:r=urllib.request.urlopen(req)
        except urllib.error.HTTPError as error:r=error
        raw=r.read();r.close();return r.code,(json.loads(raw) if r.headers.get_content_type()=='application/json' else raw),r.headers
    def login(self):
        status,_,h=self.call('/api/login',{'password':'test-password-123'});self.assertEqual(status,200)
        self.cookie=h['Set-Cookie'].split(';')[0]
    def csv(self,rows):
        f=io.StringIO();writer=csv.DictWriter(f,fieldnames=app.HEADERS);writer.writeheader();writer.writerows(rows);return '\ufeff"sep=,"\n'+f.getvalue()
    def test_price_visibility_hides_values_from_public_api(self):
        self.login()
        status, _, _ = self.call('/api/admin/settings', {'visible_folders':['Public binder'], 'currency':'USD', 'markup':None, 'show_prices':False})
        self.assertEqual(status, 200)
        status, public, _ = self.call('/api/collection')
        self.assertFalse(public['show_prices'])
        self.assertIsNone(public['rows'][0]['public_price'])
        self.assertIsNotNone(self.call('/api/admin')[1]['rows'][0]['public_price'])
        self.assertEqual(self.call('/api/admin/settings', {'visible_folders':['Public binder'], 'currency':'USD', 'show_prices':'false'})[0], 400)

    def test_password_change_requires_current_password_and_revokes_sessions(self):
        self.login()
        old_cookie=self.cookie
        body={'current_password':'wrong','new_password':'replacement-password-123','confirm_password':'replacement-password-123'}
        self.assertEqual(self.call('/api/admin/password',body)[0],401)
        body['current_password']='test-password-123'
        body['confirm_password']='mismatch'
        self.assertEqual(self.call('/api/admin/password',body)[0],400)
        body['confirm_password']=body['new_password']
        self.assertEqual(self.call('/api/admin/password',body)[0],200)
        self.cookie=old_cookie
        self.assertEqual(self.call('/api/admin')[0],401)
        app.ATTEMPTS.clear()
        self.assertEqual(self.call('/api/login',{'password':'test-password-123'})[0],401)
        self.assertEqual(self.call('/api/login',{'password':body['new_password']})[0],200)

    def test_password_checks_are_throttled_across_ips(self):
        for i in range(30):
            self.assertFalse(app.password_throttled('test-ip-' + str(i)))
        self.assertTrue(app.password_throttled('different-ip'))
        app.ATTEMPTS.clear()
        for _ in range(5): self.assertEqual(self.call('/api/login',{'password':'wrong'})[0],401)
        with patch.object(app, 'password_matches') as check:
            self.assertEqual(self.call('/api/login',{'password':'wrong'})[0],429)
            check.assert_not_called()

    def test_csv_timestamp_only_changes_on_committed_import(self):
        app.save('csv-import.json',{})
        self.login()
        self.assertIsNone(self.call('/api/admin')[1]['csv_updated_at'])
        preview=self.call('/api/admin/preview',{'csv':self.csv(self.rows)})[1]
        self.assertIsNone(self.call('/api/admin')[1]['csv_updated_at'])
        with patch.object(app,'queue_refresh'):
            self.assertEqual(self.call('/api/admin/import',{'token':preview['token']})[0],200)
        updated=self.call('/api/admin')[1]['csv_updated_at']
        self.assertGreater(updated,0)
        self.call('/api/admin/settings',{'visible_folders':['Public binder'],'currency':'USD'})
        self.assertEqual(self.call('/api/admin')[1]['csv_updated_at'],updated)

    def test_sync_credential_is_scoped_and_preview_owned(self):
        credential = 'test-sync-credential-' + 'a' * 32
        digest = hashlib.sha256(credential.encode()).hexdigest()
        headers = {'Authorization': 'Bearer ' + credential}
        with patch.dict('os.environ', {'DUEL_SYNC_TOKEN_SHA256': digest}), patch.object(app, 'queue_refresh'):
            self.assertEqual(self.call('/api/sync/preview', {'csv': self.csv(self.rows)})[0], 401)
            status, preview, _ = self.call('/api/sync/preview', {'csv': self.csv(self.rows)}, headers=headers)
            self.assertEqual(status, 200)
            self.assertEqual(self.call('/api/admin/settings', {}, headers=headers)[0], 401)
            self.assertEqual(self.call('/api/admin', headers=headers)[0], 401)
            self.assertEqual(self.call('/api/sync/settings', {}, headers=headers)[0], 404)
            self.login()
            self.assertEqual(self.call('/api/admin/import', {'token': preview['token']})[0], 400)
            self.assertEqual(self.call('/api/sync/import', {'token': preview['token']}, headers=headers)[0], 200)
            self.assertEqual(self.call('/api/sync/import', {'token': preview['token']}, headers=headers)[0], 400)

    def test_inventory_guard_ignores_price_updates_and_tracks_quantity_losses(self):
        changed = [dict(r, MARKET='1.23', LOW='0.50', MID='0.80',
                        **{'Price Bought': '2.34', 'Date Bought': '2026-11-23', 'Trade Quantity': '0'}) for r in self.rows]
        self.assertEqual(app.inventory_changes(self.rows, changed),
                         {'removed_copies': 0, 'added_copies': 0, 'removed_folders': []})
        changed[0]['Quantity'] = '1'
        self.assertEqual(app.inventory_changes(self.rows, changed)['removed_copies'], 1)
        loss = app.inventory_changes(self.rows, changed[:1])
        self.assertEqual(loss['removed_copies'], 3)
        self.assertEqual(loss['removed_folders'], ['Secret storage'])

    def test_manual_dragonshield_sync_requires_session_and_valid_origin(self):
        with patch('manual_sync.invoke', return_value=(200, {'status': 'imported', 'entries': 2, 'copies': 4, 'folders': 2})) as invoke:
            self.assertEqual(self.call('/api/admin/dragonshield-sync', {})[0], 401)
            invoke.assert_not_called()
            self.login()
            self.assertEqual(self.call('/api/admin/dragonshield-sync', {}, headers={'Origin': 'https://attacker.example'})[0], 403)
            self.assertEqual(self.call('/api/admin/dragonshield-sync', {'mode': 'resume'})[0], 400)
            invoke.assert_not_called()
            self.assertEqual(self.call('/api/admin/dragonshield-sync', {})[0], 200)
            invoke.assert_called_once()

    def test_artwork_batch_requires_admin_and_hosted_storage(self):
        self.assertEqual(self.call('/api/admin/artwork-batch',{})[0],401)
        self.login()
        self.assertEqual(self.call('/api/admin/artwork-batch',{})[0],400)

    def test_public_allowlist_and_offer_price(self):
        status,data,h=self.call('/api/collection');self.assertEqual(status,200);self.assertEqual(len(data['rows']),1)
        row=data['rows'][0];self.assertEqual(row['public_price'],'8.67')
        self.assertTrue(row['available_to_trade'])
        self.assertEqual(row['copies'], 2)
        for k in ['Folder Name','Quantity','Trade Quantity','LOW','MID','MARKET','Price Bought','Date Bought']:self.assertNotIn(k,row)
        self.assertNotIn('Public binder',json.dumps(data));self.assertNotIn('Secret',json.dumps(data));self.assertNotIn('settings',data);self.assertEqual(h['Cache-Control'],'no-store')
    def test_reference_prices_admin_only_and_csv_untouched(self):
        cache=app.read('cache.json')
        cache['cards']['Visible card']['pricing']=app.pricing_metadata({'card_sets':[{'set_code':'ABC-EN001','set_rarity_code':'(SR)','set_price':'987.65'}],'card_prices':[{'tcgplayer_price':'876.54'}]},time.time())
        app.save('cache.json',cache)
        state=app.read('collection.json');state['rows'][0]['Rarity']='SR';app.save('collection.json',state)
        self.login()
        admin=self.call('/api/admin')[1]['rows'][0]
        self.assertEqual(admin['reference_prices']['set_price'],'987.65')
        self.assertEqual(admin['MARKET'],'10.01')
        public=self.call('/api/collection')[1]
        for value in ['reference_prices','pricing','987.65','876.54']:
            self.assertNotIn(value,json.dumps(public))
        self.assertEqual(public['rows'][0]['public_price'],'8.67')

    def test_admin_and_mutations_require_auth(self):
        self.assertEqual(self.call('/api/admin')[0],401)
        for path in ['settings','preview','import','refresh']:self.assertEqual(self.call('/api/admin/'+path,{})[0],401)
        self.login();self.assertEqual(len(self.call('/api/admin')[1]['rows']),2)
        self.assertEqual(self.call('/api/admin')[1]['rows'][0]['Folder Name'],'Public binder')
        # User endpoint stays redacted even in an admin browser.
        self.assertEqual(len(self.call('/api/collection')[1]['rows']),1)
    def test_detail_images_use_compressed_webp_and_preserve_permissions(self):
        status, body, headers = self.call('/assets/1.webp')
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), 'image/webp')
        from PIL import Image
        with Image.open(io.BytesIO(body)) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertLessEqual(image.width, 480)
            self.assertLessEqual(image.height, 700)
        info = self.call('/api/collection')[1]['rows'][0]['info']
        self.assertEqual(info['image'], 'assets/1.webp')
        self.assertEqual(info['thumbnail'], 'assets/1-thumb-v2.webp')
        self.assertEqual(self.call('/assets/2.webp')[0], 404)
        self.login()
        self.assertEqual(self.call('/assets/2.webp')[0], 200)

    def test_thumbnails_are_small_persistent_and_private(self):
        from PIL import Image
        original = io.BytesIO()
        Image.new('RGB', (800, 1200), 'purple').save(original, 'JPEG')
        (app.DATA/'assets/1.jpg').write_bytes(original.getvalue())
        self.assertEqual(self.call('/assets/2-thumb-v2.webp')[0], 404)
        status, content, _ = self.call('/assets/1-thumb-v2.webp')
        self.assertEqual(status, 200)
        thumbnail = app.DATA/'assets/1-thumb-v2.webp'
        self.assertTrue(thumbnail.is_file())
        with Image.open(thumbnail) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertLessEqual(image.width, 200)
            self.assertLessEqual(image.height, 292)
        first = thumbnail.read_bytes()
        self.call('/assets/1-thumb-v2.webp')
        self.assertEqual(first, thumbnail.read_bytes())
        self.assertLess(len(first), len(original.getvalue()))

    def test_revision_is_lightweight_and_tracks_metadata(self):
        with patch.object(app, 'read', side_effect=AssertionError('Full document read')):
            status, first, _ = self.call('/api/revision')
        self.assertEqual(status, 200)
        self.assertEqual(set(first), {'sync_token'})
        app.save('cache.json', {'cards': {}, 'checked': time.time()})
        self.assertNotEqual(first['sync_token'], self.call('/api/revision')[1]['sync_token'])
        self.assertEqual(self.call('/api/admin/revision')[0], 401)

    def test_image_revalidation_checks_current_visibility(self):
        status, _, headers = self.call('/assets/1.jpg')
        self.assertEqual(status, 200)
        self.assertIn('private', headers['Cache-Control'])
        self.assertIn('no-cache', headers['Cache-Control'])
        conditional = {'If-None-Match': headers['ETag']}
        status, body, _ = self.call('/assets/1.jpg', headers=conditional)
        self.assertEqual(status, 304)
        self.assertEqual(body, b'')
        state = app.read('collection.json')
        state['settings']['visible_folders'] = []
        app.save('collection.json', state)
        self.assertEqual(self.call('/assets/1.jpg', headers=conditional)[0], 404)

    def test_raw_files_and_private_assets_blocked(self):
        for path in ['/collection.js','/cards.js','/data/collection.json','/data/auth.json','/server.py','/../data/collection.json','/assets/2.jpg']:
            self.assertEqual(self.call(path)[0],404,path)
        self.assertEqual(self.call('/assets/1.jpg')[0],200)
        self.login();self.assertEqual(self.call('/assets/2.jpg')[0],200)
    def test_settings_prices_and_private_defaults(self):
        self.login();status,_,_=self.call('/api/admin/settings',{'markup':10,'currency':'EUR','visible_folders':[]});self.assertEqual(status,200)
        self.assertEqual(self.call('/api/collection')[1]['rows'],[])
        self.assertEqual(self.call('/assets/1.jpg',authenticated=False)[0],404)
        self.assertEqual(app.public_price(self.rows[0],{'markup':None,'currency':'USD'}),'8.67')
    def test_pricing_settings_validation_and_public_conversion(self):
        self.login()
        settings = {'currency':'CLP','visible_folders':['Public binder'],'min_price_usd':'10','usd_to_clp':'950'}
        self.assertEqual(self.call('/api/admin/settings',settings)[0],200)
        public = self.call('/api/collection')[1]
        self.assertEqual(public['currency'],'CLP')
        self.assertEqual(public['rows'][0]['public_price'],'9500')
        self.assertNotIn('usd_to_clp',json.dumps(public))
        before = app.read('collection.json')
        for key, value in [('usd_to_clp',None),('usd_to_clp','0'),('usd_to_clp','-2'),('usd_to_clp','NaN'),('min_price_usd','-1'),('min_price_usd',True),('min_price_usd','Infinity')]:
            self.assertEqual(self.call('/api/admin/settings',dict(settings,**{key:value}))[0],400,(key,value))
            self.assertEqual(app.read('collection.json'),before)
        with patch.object(app,'queue_refresh'):
            preview=self.call('/api/admin/preview',{'csv':self.csv(self.rows)})[1]
            self.assertEqual(self.call('/api/admin/import',{'token':preview['token']})[0],200)
        self.assertEqual(app.read('collection.json')['settings'],before['settings'])

    def test_csv_preview_replace_preserves_settings_and_backup(self):
        self.login();new=self.rows+[dict(self.rows[0],**{'Folder Name':'New private folder','Card Name':'New card'})]
        status,preview,_=self.call('/api/admin/preview',{'csv':self.csv(new)});self.assertEqual(status,200);self.assertEqual(preview['new_folders'],['New private folder'])
        self.assertEqual(len(app.read('collection.json')['rows']),2)
        with patch.object(app,'queue_refresh') as refresh:
            self.assertEqual(self.call('/api/admin/import',{'token':preview['token']})[0],200);refresh.assert_called_once()
        self.assertEqual(len(app.read('collection.json')['rows']),3);self.assertEqual(len(app.read('collection.backup.json')['rows']),2)
        self.assertEqual(app.read('collection.json')['settings']['visible_folders'],['Public binder'])
        self.assertEqual(len(self.call('/api/collection')[1]['rows']),1)
        self.assertEqual(self.call('/api/admin/import',{'token':preview['token']})[0],400)
    def test_invalid_csv_is_atomic(self):
        self.login()
        for text in ['a,b\n1,2','',self.csv([dict(self.rows[0],Quantity='-1')]),self.csv([dict(self.rows[0],MARKET='NaN')]),self.csv([dict(self.rows[0],Quantity='0')])]:
            self.assertEqual(self.call('/api/admin/preview',{'csv':text})[0],400)
        self.assertEqual(len(app.read('collection.json')['rows']),2)
    def test_origin_host_and_logout(self):
        self.login();self.assertEqual(self.call('/api/admin/refresh',{},headers={'Origin':'https://other.test'})[0],403)
        self.assertEqual(self.call('/api/collection',headers={'Host':'other.test'})[0],403)
        self.assertEqual(self.call('/api/logout',{})[0],200);self.assertEqual(self.call('/api/admin')[0],401)
    def test_stale_preview_rejected(self):
        self.login();preview=self.call('/api/admin/preview',{'csv':self.csv(self.rows)})[1]
        self.call('/api/admin/settings',{'markup':20,'currency':'USD','visible_folders':[]})
        self.assertEqual(self.call('/api/admin/import',{'token':preview['token']})[0],400)
    def test_cache_reuses_existing_images_and_downloads_only_missing(self):
        catalog=[{'id':1,'name':'Visible card','type':'Spell Card','desc':'Updated effect'},{'id':2,'name':'Secret card','type':'Trap Card','desc':'Secret'}]
        app.save('catalog.json',{'updated':time.time(),'data':catalog})
        with patch.object(urllib.request,'urlopen') as network:
            app.refresh_cache();network.assert_not_called()
        self.assertEqual(app.read('cache.json')['cards']['Visible card']['desc'],'Updated effect')
        (app.DATA/'assets/1.webp').unlink()
        from PIL import Image
        response=io.BytesIO()
        Image.new('RGB', (800,1200), 'green').save(response, 'JPEG')
        response.seek(0)
        with patch.object(urllib.request,'urlopen',return_value=response) as network:
            app.refresh_cache();self.assertEqual(network.call_count,1)
        self.assertTrue((app.DATA/'assets/1.webp').is_file())
        self.assertFalse((app.DATA/'assets/1.jpg').exists())
    def test_cache_network_failure_keeps_existing_data(self):
        app.save('catalog.json',{'updated':time.time(),'data':[{'id':1,'name':'Visible card','desc':'Cached'},{'id':2,'name':'Secret card','desc':'Cached secret'}]})
        with patch.object(urllib.request,'urlopen',side_effect=OSError('Offline')):app.refresh_cache(True)
        cache=app.read('cache.json');self.assertIn('Offline',cache['error']);self.assertTrue((app.DATA/'assets/1.webp').exists());self.assertIn('Visible card',cache['cards'])

if __name__=='__main__':unittest.main()
