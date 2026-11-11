#!/usr/bin/env python3
"""Local-only Duel Archive. Python standard library; no installation needed."""
import argparse, csv, hashlib, hmac, io, json, math, mimetypes, os, secrets, threading, time, urllib.request
from collections import Counter
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from getpass import getpass
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
HOSTED_STORE = ContextVar('hosted_store', default=None)
LOCK = threading.RLock()
SESSIONS = {}; STAGED = {}; ATTEMPTS = {}
CACHE_RUNNING = False
HEADERS = ['Folder Name','Quantity','Trade Quantity','Card Name','Set Code','Set Name','Card Number','Rarity','Condition','Printing','Language','Price Bought','Date Bought','LOW','MID','MARKET']
PUBLIC_FIELDS = HEADERS[3:11]
INFO_FIELDS = ['id','type','desc','race','attribute','level','atk','def','linkval','archetype']
MAX_UPLOAD = 12 * 1024 * 1024
CATALOG_TTL = 2 * 86400

def read(name, default=None):
    if store := HOSTED_STORE.get():
        return store.read(name, default)
    path = DATA / name
    return json.loads(path.read_text()) if path.exists() else default

def save(name, value):
    if store := HOSTED_STORE.get():
        return store.save(name, value)
    DATA.mkdir(exist_ok=True)
    target = DATA / name
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False))
    os.chmod(temp, 0o600)
    temp.replace(target)

def parse_csv(text):
    text = text.lstrip('\ufeff')
    lines = text.splitlines(keepends=True)
    if lines and lines[0].strip().strip('"').lower() == 'sep=,': text = ''.join(lines[1:])
    reader = csv.DictReader(io.StringIO(text), strict=True)
    if not reader.fieldnames or not set(HEADERS).issubset(reader.fieldnames):
        raise ValueError('CSV must include the original collection columns: ' + ', '.join(HEADERS))
    if len(set(reader.fieldnames)) != len(reader.fieldnames): raise ValueError('Duplicate column headers are not allowed.')
    rows = []
    for line, raw in enumerate(reader, 2):
        if len(rows) >= 100000: raise ValueError('Maximum 100,000 entries per upload.')
        if None in raw or any(raw.get(k) is None for k in HEADERS): raise ValueError(f'Row {line}: incorrect number of columns.')
        r = {k: raw[k].strip() for k in HEADERS}
        if not r['Card Name'] or not r['Folder Name']: raise ValueError(f'Row {line}: card and folder names are required.')
        if any(len(v) > 2000 for v in r.values()): raise ValueError(f'Row {line}: a value is too long.')
        for k in ['Quantity','Trade Quantity']:
            if not r[k].isdigit() or int(r[k]) > 1000000: raise ValueError(f'Row {line}: {k} must be a nonnegative integer.')
        if int(r['Trade Quantity']) > int(r['Quantity']): raise ValueError(f'Row {line}: trade quantity exceeds quantity.')
        for k in ['Price Bought','LOW','MID','MARKET']:
            if r[k]:
                try: valid = math.isfinite(float(r[k])) and 0 <= float(r[k]) <= 1e12
                except ValueError: valid = False
                if not valid: raise ValueError(f'Row {line}: {k} must be a nonnegative number or blank.')
        rows.append(r)
    if not rows: raise ValueError('The CSV contains no cards. Empty uploads cannot replace your collection.')
    return rows

def public_price(row, settings):
    try:
        low = Decimal(row.get('LOW') or '')
        market = Decimal(row.get('MARKET') or '')
        if any(not value.is_finite() or not 0 <= value <= Decimal('1e12') for value in (low, market)):
            return None
        return str(((low + market) / 2).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return None


def source_amount(value):
    try:
        if isinstance(value, bool): return None
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount > Decimal('1e12'): return None
        rounded=amount.quantize(Decimal('.01'))
        return format(rounded, '.2f') if rounded > 0 else None
    except (InvalidOperation, ValueError, TypeError): return None

def pricing_metadata(card, fetched_at):
    # Kept on disk with card metadata; stripped before any public response.
    sets = [{k:entry.get(k, '') for k in ['set_name','set_code','set_rarity','set_rarity_code','set_price']}
            for entry in card.get('card_sets', []) if isinstance(entry,dict)]
    prices = card.get('card_prices') or []
    raw = prices[0] if prices and isinstance(prices[0],dict) else {}
    vendors = [{'vendor':label,'amount':amount,'currency':currency}
               for key,label,currency in [('tcgplayer_price','TCGplayer','USD'),('cardmarket_price','Cardmarket','EUR'),('ebay_price','eBay','USD'),('amazon_price','Amazon','USD'),('coolstuffinc_price','CoolStuffInc','USD')]
               if (amount := source_amount(raw.get(key))) is not None]
    return {'sets':sets,'vendors':vendors,'fetched_at':fetched_at}

def reference_prices(row, pricing):
    def normalize(value): return str(value).strip().upper().replace('(', '').replace(')', '').replace(' ', '')
    code=normalize(row.get('Card Number','')); rarity=normalize(row.get('Rarity',''))
    matches=[entry for entry in pricing.get('sets',[]) if code and rarity
             and normalize(entry.get('set_code','')) == code
             and rarity in [normalize(entry.get('set_rarity_code','')),normalize(entry.get('set_rarity',''))]]
    amounts={source_amount(entry.get('set_price')) for entry in matches}
    status='matched' if len(matches)==1 else 'ambiguous' if matches else 'unmatched'
    amount=next(iter(amounts)) if status=='matched' else None
    if status=='matched' and amount is None: status='unpriced'
    return {'source':'YGOPRODeck','match':status,'set_price':amount,'set_currency':'USD',
            'set_code':matches[0]['set_code'] if status=='matched' else None,
            'vendors':pricing.get('vendors',[]),'fetched_at':pricing.get('fetched_at'),
            'stale':not pricing.get('fetched_at') or time.time()-pricing['fetched_at']>CATALOG_TTL}

def collection_payload(admin=False):
    with LOCK:
        state = read('collection.json', {'rows': [], 'settings': {'visible_folders': [], 'markup': None, 'currency': 'USD'}, 'revision': 0}); cache = read('cache.json', {'cards':{}})
        store = HOSTED_STORE.get()
        hosted_images = store.image_paths() if store else None
        spanish_names = read('spanish-names.json', {})
        visible = set(state['settings']['visible_folders'])
        result = []
        for i, r in enumerate(state['rows']):
            if not admin and r['Folder Name'] not in visible: continue
            item = dict(r) if admin else {k:r[k] for k in PUBLIC_FIELDS}
            info = dict(cache['cards'].get(r['Card Name'], {}))
            item['spanish_name'] = spanish_names.get(str(info.get('id')), '')
            item['available_to_trade'] = int(r.get('Trade Quantity') or 0) > 0
            pricing = info.pop('pricing', {})
            if admin: item['reference_prices'] = reference_prices(r, pricing)
            if image := info.get('image'):
                exists = image in hosted_images if store else (DATA / image).is_file()
                if not exists:
                    info.pop('image', None)
            item.update(_id=i, info=info, public_price=public_price(r, state['settings']))
            result.append(item)
        response = {'rows':result, 'currency':state['settings']['currency'], 'revision':state['revision'], 'admin':admin}
        if admin:
            if store:
                requested = read('refresh-request.json', {}).get('requested_at', 0)
                completed = read('refresh-completed.json', {}).get('requested_at', 0)
                cache['refresh_pending'] = requested > completed
                response['hosted'] = True
            response.update(settings=state['settings'], folders=dict(Counter(r['Folder Name'] for r in state['rows'])), cache={k:v for k,v in cache.items() if k!='cards'})
        return response

def summarize(old, new):
    def identities(rows): return Counter(tuple(r[k] for k in HEADERS) for r in rows)
    a,b = identities(old),identities(new)
    return {'entries':len(new),'copies':sum(int(r['Quantity']) for r in new), 'added_entries':sum((b-a).values()),'removed_entries':sum((a-b).values()),'new_folders':sorted({r['Folder Name'] for r in new}-{r['Folder Name'] for r in old})}

def queue_refresh(force=False):
    global CACHE_RUNNING
    if HOSTED_STORE.get():
        save('refresh-request.json', {'requested_at': time.time(), 'force': force})
        return True
    with LOCK:
        if CACHE_RUNNING: return False
        CACHE_RUNNING = True
    threading.Thread(target=refresh_cache,args=(force,),daemon=True).start()
    return True

def refresh_cache(force=False):
    global CACHE_RUNNING
    error = None
    try:
        with LOCK:
            cache = read('cache.json',{'cards':{},'failures':{}})
            cache['status'] = 'Refreshing card metadata, reference prices, and missing artwork…'; save('cache.json',cache)
            names = {r['Card Name'] for r in read('collection.json')['rows']}
        catalog = read('catalog.json', {'updated':0,'data':[]})
        known = {c['name'].casefold():c for c in catalog['data']}
        unknown = names - set(cache['cards']) - set(cache.get('unmatched',[]))
        if force or not known or time.time()-catalog['updated'] > CATALOG_TTL or any(n.casefold() not in known for n in unknown):
            try:
                req=urllib.request.Request('https://db.ygoprodeck.com/api/v7/cardinfo.php',headers={'User-Agent':'DuelArchiveLocal/1.0'})
                with urllib.request.urlopen(req,timeout=45) as response: db=json.load(response)['data']
                if not isinstance(db,list) or len(db)<1000: raise ValueError('Incomplete card database response')
                catalog={'updated':time.time(),'data':db}; save('catalog.json',catalog)
                known={c['name'].casefold():c for c in db}
            except Exception as exc: error='Metadata refresh failed; existing data retained. '+str(exc)
        failures={}; unmatched=[]; cards=dict(cache['cards'])
        store = HOSTED_STORE.get()
        hosted_images = store.image_paths() if store else set()
        for name in sorted(names):
            c=known.get(name.casefold())
            if c:
                cards[name]={k:c[k] for k in INFO_FIELDS if k in c}
                cards[name]['pricing']=pricing_metadata(c,catalog['updated'])
            elif name not in cards: unmatched.append(name); continue
            info=cards[name]
            path = f"assets/{int(info['id'])}.jpg"
            dest = DATA / path
            exists = path in hosted_images if store else dest.is_file()
            if not exists:
                try:
                    req=urllib.request.Request('https://images.ygoprodeck.com/images/cards/'+str(int(info['id']))+'.jpg',headers={'User-Agent':'DuelArchiveLocal/1.0'})
                    with urllib.request.urlopen(req,timeout=20) as response: image=response.read(5*1024*1024)
                    if not image.startswith(b'\xff\xd8') or len(image)<1000: raise ValueError('Invalid artwork response')
                    if store:
                        store.save_image(path, image)
                        hosted_images.add(path)
                    else:
                        dest.parent.mkdir(exist_ok=True)
                        temp = dest.with_suffix('.tmp')
                        temp.write_bytes(image)
                        temp.replace(dest)
                    exists = True
                except Exception as exc: failures[name]=str(exc)
                time.sleep(.15)
            if exists: info['image'] = path
        with LOCK:
            coverage=dict(Counter(reference_prices(r,cards.get(r['Card Name'],{}).get('pricing',{}))['match'] for r in read('collection.json')['rows']))
            save('cache.json', {'price_coverage':coverage,'cards':cards,'last_refresh':catalog['updated'],'checked':time.time(),'failures':failures,'unmatched':unmatched,'error':error,'status':f"Ready · {len(names)-len(unmatched)} matched names · {len(failures)} artwork downloads pending"})
    except Exception as exc:
        with LOCK:
            cache=read('cache.json',{'cards':{}}); cache.update(status='Refresh failed; retry from admin.',error=str(exc));save('cache.json',cache)
    finally:
        with LOCK: CACHE_RUNNING=False

def response_headers(content_type, cookie=None):
    headers = {
        'Content-Type': content_type,
        'Cache-Control': 'no-store' if content_type == 'application/json' or content_type.startswith('image/') else 'no-cache',
        'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'same-origin',
        'Content-Security-Policy': "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    }
    if cookie:
        headers['Set-Cookie'] = cookie
    return headers


class ArchiveRoutes:
    """Collection actions shared by the local HTTP and ASGI servers."""

    def valid_host(self, port):
        return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

    def valid_origin(self, scheme='http'):
        origin = self.headers.get('Origin')
        return ((not origin or origin == f"{scheme}://{self.headers.get('Host')}")
                and self.headers.get('X-Requested-With') == 'DuelArchive')

    def session(self):
        cookie=SimpleCookie()
        try: cookie.load(self.headers.get('Cookie','')); token=cookie['duel_admin'].value
        except (KeyError,ValueError,CookieError): return None
        if store := HOSTED_STORE.get():
            key = hashlib.sha256(token.encode()).hexdigest()
            return key if store.runtime_get('session', key) else None
        with LOCK:
            expiry=SESSIONS.get(token,0)
            if expiry>time.time(): return token
            SESSIONS.pop(token,None)
        return None
    def get(self, path):
        admin = bool(self.session())
        if path=='/api/collection': return self.send(200,collection_payload(False))
        if path=='/api/admin': return self.send(200,collection_payload(True)) if admin else self.send(401,{'error':'Admin login required.'})
        if path.startswith('/assets/'):
            file=path.removeprefix('/assets/')
            if not file.endswith('.jpg') or not file[:-4].isdigit(): return self.send(404,{'error':'Not found'})
            state = read('collection.json')
            visible = set(state['settings']['visible_folders'])
            cards = read('cache.json', {'cards': {}})['cards']
            asset_path = path.lstrip('/')
            if not any(cards.get(r['Card Name'], {}).get('image') == asset_path
                       for r in state['rows'] if admin or r['Folder Name'] in visible):
                return self.send(404, {'error': 'Not found'})
            if store := HOSTED_STORE.get():
                image = store.image(asset_path)
                if image is None:
                    return self.send(404, {'error': 'Not found'})
                return self.send(200, image, content_type='image/jpeg')
            target = DATA / asset_path
        elif path in ['/','/admin','/index.html']: target=ROOT/'public/index.html'
        elif path in ['/app.js','/style.css']: target=ROOT/'public'/path[1:]
        else: return self.send(404,{'error':'Not found'})
        if not target.is_file(): return self.send(404,{'error':'Not found'})
        return self.send(200,target.read_bytes(),content_type=mimetypes.guess_type(target)[0] or 'application/octet-stream')
    def post(self, path, raw_body, client_ip):
        if not 0 < len(raw_body) <= MAX_UPLOAD:
            return self.send(413, {'error': 'Upload must be under 12 MB.'})
        try:
            body = json.loads(raw_body)
            if not isinstance(body, dict):
                raise ValueError('Expected JSON object')
        except (ValueError, UnicodeError):
            return self.send(400, {'error': 'Invalid request'})
        store = HOSTED_STORE.get()
        secure = '; Secure' if store else ''
        if path=='/api/login':
            now = time.time()
            attempts = (store.runtime_get('attempts', client_ip) or []) if store else ATTEMPTS.setdefault(client_ip, [])
            attempts[:] = [x for x in attempts if now - x < 60]
            if len(attempts)>=5: return self.send(429,{'error':'Too many attempts; wait one minute.'})
            attempts.append(now)
            if store:
                store.runtime_put('attempts', client_ip, attempts, now + 60)
            auth = read('auth.json')
            if not auth:
                return self.send(503, {'error': 'Set up the admin password before signing in.'})
            password=body.get('password','')
            if not isinstance(password,str) or len(password)>1024: return self.send(400,{'error':'Invalid password'})
            candidate=hashlib.scrypt(password.encode(),salt=bytes.fromhex(auth['salt']),n=16384,r=8,p=1).hex()
            if not hmac.compare_digest(candidate,auth['hash']): return self.send(401,{'error':'Incorrect password.'})
            token = secrets.token_urlsafe(32)
            if store:
                key = hashlib.sha256(token.encode()).hexdigest()
                store.runtime_put('session', key, True, now + 28800)
                store.runtime_delete('attempts', client_ip)
            else:
                SESSIONS[token] = now + 28800
                attempts.clear()
            return self.send(200, {'ok': True}, cookie=f'duel_admin={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{secure}')
        token=self.session()
        if not token: return self.send(401,{'error':'Admin login required.'})
        try:
            if path=='/api/logout':
                if store:
                    store.runtime_delete('session', token)
                else:
                    SESSIONS.pop(token, None)
                return self.send(200, {'ok': True}, cookie=f'duel_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0{secure}')
            if path=='/api/admin/settings':
                markup=body.get('markup');currency=body.get('currency','')
                if markup is not None and (isinstance(markup,bool) or not isinstance(markup,(int,float)) or not math.isfinite(markup) or not 0<=markup<=10000): raise ValueError('Markup must be between 0 and 10,000 percent.')
                if not isinstance(currency,str) or len(currency.strip())>12: raise ValueError('Currency label must be 12 characters or fewer.')
                visible=body.get('visible_folders')
                with LOCK:
                    state=read('collection.json'); folders={r['Folder Name'] for r in state['rows']}
                    if not isinstance(visible,list) or any(not isinstance(x,str) or x not in folders for x in visible): raise ValueError('Invalid folder selection. Reload admin and retry.')
                    state['settings']={'visible_folders':list(dict.fromkeys(visible)),'markup':markup,'currency':currency.strip()};state['revision']+=1;save('collection.json',state)
                return self.send(200,{'ok':True})
            if path=='/api/admin/preview':
                rows=parse_csv(body.get('csv',''))
                with LOCK:
                    state=read('collection.json'); summary=summarize(state['rows'],rows)
                    key = secrets.token_urlsafe(24)
                    staged = (token, time.time() + 600, state['revision'], rows)
                    if store:
                        store.runtime_put('preview', key, staged, staged[1])
                    else:
                        STAGED.clear()
                        STAGED[key] = staged
                return self.send(200,{'token':key,**summary})
            if path=='/api/admin/import':
                with LOCK:
                    key = body.get('token', '')
                    if not isinstance(key, str):
                        raise ValueError('Invalid upload token.')
                    staged = store.runtime_get('preview', key) if store else STAGED.get(key)
                    if not staged or staged[0]!=token or staged[1]<time.time(): raise ValueError('Upload preview expired. Select the CSV again.')
                    state=read('collection.json')
                    if state['revision']!=staged[2]: raise ValueError('Collection settings changed. Preview your CSV again.')
                    save('collection.backup.json',state)
                    state['rows']=staged[3];state['revision']+=1
                    current={r['Folder Name'] for r in state['rows']}
                    state['settings']['visible_folders']=[f for f in state['settings']['visible_folders'] if f in current]
                    save('collection.json', state)
                    if store:
                        store.runtime_delete('preview', key)
                    else:
                        STAGED.clear()
                queue_refresh()
                message = 'Collection replaced. Existing folder settings preserved; new folders are private.'
                return self.send(200, {'ok': True, 'message': message})
            if path=='/api/admin/refresh':
                started = queue_refresh(True)
                return self.send(200, {'ok': True, 'started': started, 'queued': bool(store)})
            return self.send(404,{'error':'Not found'})
        except (ValueError,TypeError,csv.Error,AttributeError) as exc: return self.send(400,{'error':str(exc)})

class Handler(ArchiveRoutes, BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, value, cookie=None, content_type='application/json'):
        data = json.dumps(value, ensure_ascii=False).encode() if content_type == 'application/json' else value
        self.send_response(status)
        for name, header_value in response_headers(content_type, cookie).items():
            self.send_header(name, header_value)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.valid_host(self.server.server_port):
            return self.send(403, {'error': 'Invalid host'})
        return self.get(urlsplit(self.path).path)

    def do_POST(self):
        if not self.valid_host(self.server.server_port):
            return self.send(403, {'error': 'Invalid host'})
        if not self.valid_origin():
            return self.send(403, {'error': 'Invalid request origin'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.send(400, {'error': 'Invalid request'})
        if not 0 < size <= MAX_UPLOAD:
            return self.send(413, {'error': 'Upload must be under 12 MB.'})
        return self.post(urlsplit(self.path).path, self.rfile.read(size), self.client_address[0])


def setup_password():
    first=getpass('Create admin password (at least 12 characters): ')
    if len(first)<12: raise SystemExit('Use at least 12 characters.')
    if first!=getpass('Confirm admin password: '): raise SystemExit('Passwords did not match.')
    salt=secrets.token_bytes(16)
    save('auth.json',{'salt':salt.hex(),'hash':hashlib.scrypt(first.encode(),salt=salt,n=16384,r=8,p=1).hex()})

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--setup',action='store_true');args=parser.parse_args()
    if args.setup or not (DATA/'auth.json').exists(): setup_password()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'User view: http://127.0.0.1:{args.port}/\nAdmin view: http://127.0.0.1:{args.port}/admin',flush=True)
    queue_refresh()
    try: server.serve_forever()
    except KeyboardInterrupt: server.server_close()
