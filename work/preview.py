import sys,pathlib,tempfile,hashlib,threading,shutil
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import server as app
source=app.DATA
preview=pathlib.Path(tempfile.mkdtemp(prefix='duel-preview-'))
for name in ['collection.json','cache.json','catalog.json']:shutil.copy2(source/name,preview/name)
(preview/'assets').symlink_to(source/'assets',target_is_directory=True)
app.DATA=preview
state=app.read('collection.json');state['settings']={'visible_folders':['UltraProBlackBox'],'markup':20,'currency':'USD'};app.save('collection.json',state)
salt=b'preview-only-1234';app.save('auth.json',{'salt':salt.hex(),'hash':hashlib.scrypt(b'local-preview-only',salt=salt,n=16384,r=8,p=1).hex()})
http=app.ThreadingHTTPServer(('127.0.0.1',8765),app.Handler)
print('Temporary preview at http://127.0.0.1:8765 (test settings, production data unchanged)',flush=True)
try:http.serve_forever()
except KeyboardInterrupt:http.server_close();shutil.rmtree(preview)
