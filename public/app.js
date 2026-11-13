const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let rows=[], folders=[], payload; const adminRoute=location.pathname.replace(/\/+$/, '')==='/admin';
async function request(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-Requested-With':'DuelArchive'}:{},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok){const error=new Error(data.error||'Request failed');error.status=r.status;throw error;}return data;}
function showLogin(){rows=[];folders=[];if($('detail').open)$('detail').close();$('detailContent').innerHTML='';$('folders').innerHTML='';$('allCount').textContent='';$('sidebarFolderCount').textContent='';$('folderHeading').hidden=true;$('folders').hidden=true;$('folderStat').hidden=true;$('adminPanel').innerHTML='';$('login').hidden=false;$('collectionView').hidden=true;$('adminPanel').hidden=true;}
function notify(text,error=false){$('message').hidden=false;$('message').textContent=text;$('message').classList.toggle('error',error)}
async function load(){try{payload=await request(adminRoute?'/api/admin':'/api/collection');$('login').hidden=true;$('collectionView').hidden=false;rows=payload.rows;folders=payload.admin?[...new Set(rows.map(r=>r['Folder Name']))].sort((a,b)=>a.localeCompare(b)):[];initialize();if(adminRoute)renderAdmin();else{$('viewLink').href='/admin';}}catch(e){if(adminRoute&&e.status===401)showLogin();else notify(e.message,true);}}
let showImages=false;
let folder='',page=1,filtered=[],lastFocus;const pageSize=30;
let language = !adminRoute && globalThis.localStorage?.getItem('duel-language') === 'es' ? 'es' : 'en';
const translations = {
    'All cards':'Todas las cartas', 'All sets':'Todas las colecciones', 'All rarities':'Todas las rarezas',
    'All card types':'Todos los tipos', 'Monster':'Monstruo', 'Spell':'Mágica', 'Trap':'Trampa',
    'Browse the collection':'Explora la colección', 'Ask for price':'Consultar precio', 'From':'Desde',
    'Set':'Colección', 'Card number':'Número de carta', 'Rarity':'Rareza', 'Condition':'Estado',
    'Printing':'Edición', 'Language':'Idioma', 'Offer price · per copy':'Precio por copia',
    'English':'Inglés', 'Spanish':'Español', '1st Edition':'1.ª edición', 'Unlimited':'Ilimitada',
    'NearMint':'Casi nueva', 'LightlyPlayed':'Poco usada', 'ModeratelyPlayed':'Uso moderado',
    'HeavilyPlayed':'Muy usada', 'Damaged':'Dañada', 'Available versions':'Versiones disponibles',
    'Copies':'Copias', 'Show images':'Mostrar imágenes', 'Card details':'Detalles de la carta', 'View':'Ver', 'cards':'cartas'
};
const t = text => language === 'es' ? translations[text] || text : text;
const cardName = r => language === 'es' && r.spanish_name ? r.spanish_name : r['Card Name'];
const num = n => Number(n || 0).toLocaleString(language);
const price = n => n !== '' && n != null
    ? Number(n).toLocaleString(language, {minimumFractionDigits:2,maximumFractionDigits:2})
    : 'Not recorded';
const offer = r => r.public_price == null ? t('Ask for price')
    : (r.price_varies ? t('From') + ' ' : '') + price(r.public_price) + ' ' + payload.currency;

const rarityNames = {
    C:['Common','Común'], R:['Rare','Rara'], SR:['Super Rare','Súper rara'], UR:['Ultra Rare','Ultra rara'],
    ScR:['Secret Rare','Secreta rara'], UtR:['Ultimate Rare','Ultimate rara'],
    PScR:['Prismatic Secret Rare','Secreta rara prismática'], PlScR:['Platinum Secret Rare','Secreta rara platino'],
    QCScR:['Quarter Century Secret Rare','Secreta rara del cuarto de siglo'], CR:['Collector’s Rare','Rara de coleccionista'],
    StR:['Starlight Rare','Rara luz estelar'], GUR:['Gold Rare','Rara dorada'], GScR:['Gold Secret Rare','Secreta rara dorada'],
    PGR:['Premium Gold Rare','Rara dorada prémium'], SP:['Short Print','Tirada corta'],
    DUPR:['Duel Terminal Ultra Parallel Rare','Ultra rara paralela Duel Terminal'],
    DRPR:['Duel Terminal Rare Parallel Rare','Rara paralela Duel Terminal'],
    SHR:['Shatterfoil Rare','Rara Shatterfoil'], EmUR:['Embossed Ultra Rare','Ultra rara en relieve']
};
const rarityLabel = code => rarityNames[code] ? rarityNames[code][language === 'es' ? 1 : 0] + ' (' + code + ')' : code;
const normalize = value => String(value ?? '').normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase().trim();
const setName = r => r['Set Name'] || r['Set Code'];
const groupKey = r => JSON.stringify([normalize(r['Card Name']), normalize(r['Set Code'] || setName(r)) || normalize(r['Card Number'])]);

function groupCards(items) {
    const groups = new Map();
    for (const item of items) {
        const key = groupKey(item);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
    }
    return [...groups.values()].map(variants => {
        const first = variants.find(r => r.info.image) || variants[0];
        const prices = new Set(variants.map(r => r.public_price).filter(p => p != null).map(Number));
        const lowest = [...prices].reduce((min, value) => Math.min(min, value), Infinity);
        return {
            ...first,
            copies: variants.reduce((sum, r) => sum + Number(r.copies || 0), 0),
            public_price: prices.size ? lowest : null,
            price_varies: prices.size > 1 || (prices.size > 0 && variants.some(r => r.public_price == null)),
            rarities: [...new Set(variants.map(r => r.Rarity))]
        };
    });
}

function fillOptions(id, labels, emptyLabel) {
    const select = $(id);
    const previous = select.value;
    select.innerHTML = '';
    select.add(new Option(t(emptyLabel), ''));
    for (const [value, label] of labels) select.add(new Option(label, value));
    select.value = labels.some(([value]) => value === previous) ? previous : '';
}

function localizeVisitor() {
    if (adminRoute) return;
    const spanish = language === 'es';
    $('language').hidden = false;
    $('language').textContent = spanish ? 'English' : 'Español';
    $('language').lang = spanish ? 'en' : 'es';
    if (document.documentElement) document.documentElement.lang = language;
    for (const [selector, en, es] of [
        ['.brand small','YOUR PERSONAL CARD LIBRARY','TU BIBLIOTECA PERSONAL DE CARTAS'],
        ['aside .side-label','LIBRARY','BIBLIOTECA'], ['#all span','All cards','Todas las cartas'],
        ['.intro .eyebrow','THE COLLECTOR’S LIBRARY','LA BIBLIOTECA DEL COLECCIONISTA'],
        ['.intro h1','Your cards. All here.','Todas tus cartas en un solo lugar'],
        ['.intro p','A home for every monster, spell, and trap in your collection.','Un lugar para cada monstruo, carta mágica y trampa de tu colección.'],
        ['.seal small','YU-GI-OH! COLLECTION ARCHIVE','ARCHIVO DE COLECCIÓN YU-GI-OH!'],
        ['#reset','Reset','Restablecer'], ['#prev','← Previous','← Anterior'], ['#next','Next →','Siguiente →'],
        ['#empty h3','No cards found','No se encontraron cartas'],
        ['#empty p','Try another search or reset your filters.','Prueba otra búsqueda o restablece los filtros.'],
        ['#collectionLabel','COLLECTION /','COLECCIÓN /'],
        ['#localLabel','Local collection','Colección local'],
        ['aside footer small','Private. Unpublished. Yours.','Privada. Sin publicar. Tuya.'],
        ['#creditsLabel','Card data & artwork:','Datos e imágenes:'],
        ['#artNote','· Representative card artwork.','· Imagen representativa de la carta.'],
        ['#viewLink','ADMIN SIGN IN →','ACCESO DE ADMINISTRACIÓN →'],
        ['#sort option[value="name"]','Name: A–Z','Nombre: A–Z'],
        ['#sort option[value="value"]','Price: high–low','Precio: de mayor a menor']
    ]) {
        const element = document.querySelector(selector);
        if (element) element.textContent = spanish ? es : en;
    }
    if (document.title !== undefined) document.title = spanish ? 'Duel Archive · Mi colección' : 'Duel Archive · My Collection';
    for (const [id, en, es] of [['type','Card type','Tipo de carta'],['set','Set','Colección'],['rarity','Rarity','Rareza'],['sort','Sort cards','Ordenar cartas']]) {
        $(id).setAttribute?.('aria-label', spanish ? es : en);
    }
    $('search').placeholder = spanish ? 'Busca por nombre, colección o número…' : 'Search name, set, or card number…';
    $('search').setAttribute?.('aria-label', spanish ? 'Buscar en la colección' : 'Search collection');
    $('detail').setAttribute?.('aria-label', t('Card details'));
    $('close').setAttribute?.('aria-label', spanish ? 'Cerrar detalles' : 'Close card details');
    $('quantitySort').hidden = true;
    $('quantitySort').disabled = true;
    fillOptions('type', ['Monster','Spell','Trap'].map(value => [value,t(value)]), 'All card types');
}
$('language').onclick = () => {
    language = language === 'en' ? 'es' : 'en';
    globalThis.localStorage?.setItem('duel-language', language);
    if ($('detail').open) $('detail').close();
    initialize();
};

function initialize(){
$('folderHeading').hidden=!payload.admin;$('folders').hidden=!payload.admin;$('folderStat').hidden=!payload.admin;$('stats').hidden=!payload.admin;
restoreSearch();
localizeVisitor();
if (payload.admin) {
$('copies').textContent=num(rows.reduce((s,r)=>s+Number(r.Quantity),0));$('unique').textContent=num(new Set(rows.map(r=>r['Card Name'])).size);$('entries').textContent=num(rows.length);$('folderCount').textContent=folders.length;}
$('allCount').textContent=payload.admin?num(rows.length):'';
$('sidebarFolderCount').textContent=folders.length;
$('priceNote').textContent=payload.admin?'Offers average LOW and MARKET from the uploaded CSV.':(language==='es'?'Precios por copia.':'Prices shown are per copy.');
document.querySelector('#sort option[value="value"]').disabled=!rows.some(r=>r.public_price!==null);
fillOptions('set',[...new Set(rows.map(setName).filter(Boolean))].sort().map(name=>[name,name]),'All sets');
$('folders').innerHTML=folders.map((f,i)=>`<button class="folder" data-folder="${i}">▱ <span>${esc(f)}</span><b>${rows.filter(r=>r['Folder Name']===f).length}</b></button>`).join('');
$('folders').onclick=e=>{const b=e.target.closest('button');if(b)selectFolder(folders[+b.dataset.folder])};$('all').onclick=()=>selectFolder('');
fillOptions('rarity',[...new Set(rows.map(r=>r.Rarity).filter(Boolean))].map(code=>[code,rarityLabel(code)]).sort((a,b)=>a[1].localeCompare(b[1],language)),'All rarities');
if(folder&&!folders.includes(folder))folder='';selectFolder(folder);
}
function selectFolder(f){folder=payload.admin?f:'';f=folder;document.querySelectorAll('.folder').forEach(b=>b.classList.toggle('active',b.id==='all'?!f:folders[+b.dataset.folder]===f));$('title').textContent=f||t('All cards');$('breadcrumb').textContent=(f||t('All cards')).toUpperCase();update()}

['search','type','set','rarity','sort'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',update));
$('reset').onclick=()=>{['search','type','set','rarity'].forEach(id=>$(id).value='');$('sort').value='name';selectFolder('')};
function update() {
    page = 1;
    const query = normalize($('search').value);
    const items = rows.filter(r =>
        (!folder || r['Folder Name'] === folder) &&
        (!$('set').value || setName(r) === $('set').value) &&
        (!$('rarity').value || r.Rarity === $('rarity').value) &&
        (!$('type').value || (r.info.type || '').includes($('type').value)) &&
        [r['Card Name'], r.spanish_name, setName(r), r['Card Number'], r['Set Code']].some(value => normalize(value).includes(query))
    );
    filtered = payload.admin ? items : groupCards(items);
    filtered.sort((a,b) => {
        if ($('sort').value === 'quantity' && payload.admin) return Number(b.Quantity) - Number(a.Quantity);
        if ($('sort').value === 'value') return Number(b.public_price || 0) - Number(a.public_price || 0);
        return cardName(a).localeCompare(cardName(b), language);
    });
    render();
}

// Public links contain filters only; want lists stay on this device.
let restoredSearch = false;
let wants = [];
try { const saved = JSON.parse(globalThis.localStorage?.getItem('duel-wants') || '[]');
    if (Array.isArray(saved)) wants = saved.filter(x => x && typeof x.key === 'string' && Number.isInteger(x.quantity) && x.quantity > 0).slice(0,500);
} catch {}
function restoreSearch() {
    if (adminRoute || restoredSearch) return;
    restoredSearch = true;
    const params = new URLSearchParams(location.search || '');
    for (const [id, key] of [['search','q'],['set','set'],['rarity','rarity'],['type','type'],['sort','sort']]) {
        const value = params.get(key);
        if (value !== null && (id === 'search' || [...$(id).options].some(o => o.value === value && !o.disabled))) $(id).value = value;
    }
    showImages = params.get('images') === '1';
    if (['en','es'].includes(params.get('lang'))) language = params.get('lang');
}
function searchURL() {
    const url = new URL(location.href);
    url.search = ''; url.hash = '';
    for (const [id,key] of [['search','q'],['set','set'],['rarity','rarity'],['type','type'],['sort','sort']]) {
        const value = $(id).value;
        if (value && !(id === 'sort' && value === 'name')) url.searchParams.set(key,value);
    }
    if (showImages) url.searchParams.set('images','1');
    url.searchParams.set('lang',language);
    return url;
}
async function copyText(text) {
    try { await navigator.clipboard.writeText(text); notify(language === 'es' ? 'Copiado.' : 'Copied.'); }
    catch { $('copyFallback').hidden=false; $('copyFallback').value=text; $('copyFallback').focus(); $('copyFallback').select(); notify(language === 'es' ? 'Copia el texto seleccionado.' : 'Copy the selected text.'); }
}
function versionKey(r) {
    return JSON.stringify([groupKey(r),r['Card Number'],r.Language,r.Printing,r.Rarity,r.Condition]);
}
function versionDetails(item) {
    return item.version ? [item.code,t(item.language),t(item.edition),rarityLabel(item.rarity),t(item.condition)].filter(Boolean).join(' · ')
        : (language==='es'?'Versión sin seleccionar':'Version not selected');
}
let pendingVersions=[], replacingWant=null;
function chooseWantVersion(r, replaceIndex=null) {
    replacingWant=replaceIndex;
    pendingVersions=[...new Map(rows.filter(x=>groupKey(x)===groupKey(r)).map(x=>[versionKey(x),x])).values()];
    if (pendingVersions.length === 1) { addWantVersion(pendingVersions[0]); return; }
    const es=language==='es';
    $('wantVersionTitle').textContent=es?'Elige una versión':'Choose a version';
    $('confirmWantVersion').textContent=es?'Añadir a la lista':'Add to want list';
    $('cancelWantVersion').textContent=es?'Cancelar':'Cancel';
    $('wantVersion').innerHTML=pendingVersions.map((x,i)=>`<option value="${i}">${esc([x['Card Number'],t(x.Language),t(x.Printing),rarityLabel(x.Rarity),t(x.Condition)].filter(Boolean).join(' · '))}</option>`).join('');
    $('wantVersion').value='0';
    $('wantVersionDialog').showModal();
}
$('cancelWantVersion').onclick=()=>$('wantVersionDialog').close();
$('confirmWantVersion').onclick=()=>{
    const r=pendingVersions[Number($('wantVersion').value)];if(!r)return;
    addWantVersion(r);
};
function addWantVersion(r) {
    const version=versionKey(r),existing=wants.find(x=>x.version===version);
    const quantity=replacingWant===null?1:wants[replacingWant].quantity;
    if(replacingWant!==null)wants.splice(replacingWant,1);
    if(existing){if(replacingWant!==null)existing.quantity=Math.min(999,existing.quantity+quantity);}
    else wants.push({key:groupKey(r),version,name:r['Card Name'],set:setName(r),code:r['Card Number'],language:r.Language,edition:r.Printing,rarity:r.Rarity,condition:r.Condition,quantity});
    if ($('wantVersionDialog').open) $('wantVersionDialog').close();
    saveWants();
}
function saveWants() {
    try { globalThis.localStorage?.setItem('duel-wants',JSON.stringify(wants)); }
    catch { notify('Browser storage is unavailable; this list will last only until you reload.',true); }
    render();
}
function renderWants() {
    $('wantPanel').hidden=adminRoute;
    $('shareSearch').hidden=adminRoute;
    if (adminRoute) return;
    const es = language === 'es';
    $('shareSearch').textContent=es?'Copiar enlace de búsqueda':'Copy search link';
    $('wantTitle').textContent=es?'Lista de deseos':'Want list';
    $('wantNote').textContent=es?'Guardada en este navegador. Las cantidades solicitadas no reservan cartas.':'Saved in this browser. Requested quantities do not reserve cards.';
    $('copyWant').textContent=es?'Copiar lista':'Copy list';
    $('clearWant').textContent=es?'Vaciar lista':'Clear list';
    $('copyWant').disabled=$('clearWant').disabled=!wants.length;
    const current = new Set(rows.map(versionKey));
    $('wantItems').innerHTML=wants.map((item,i)=>`<div class="want-item"><span>${esc(item.name)} · ${esc(item.set)}<br>${esc(versionDetails(item))}${!item.version ? `<button type="button" data-choose-want="${i}">${es?'Elegir versión':'Choose version'}</button>` : ''}${current.has(item.version)||(!item.version&&rows.some(r=>groupKey(r)===item.key))?'':(es?' · Ya no está disponible':' · No longer listed')}</span><label>${es?'Cantidad':'Quantity'} <input type="number" min="1" max="999" value="${item.quantity}" data-want-quantity="${i}"></label><button type="button" data-remove-want="${i}">${es?'Quitar':'Remove'}</button></div>`).join('') || `<p>${es?'Selecciona cartas para empezar.':'Select cards to start your list.'}</p>`;
}
$('shareSearch').onclick=()=>copyText(searchURL().href);
$('copyWant').onclick=()=>copyText((language==='es'?'Mi lista de cartas':'My card want list')+'\n'+wants.map(x=>`${x.quantity} × ${x.name} — ${x.set} — ${versionDetails(x)}`).join('\n'));
$('clearWant').onclick=()=>{wants=[];saveWants();};
$('wantItems').onclick=e=>{const choose=e.target.closest('[data-choose-want]');if(choose){const i=+choose.dataset.chooseWant;const r=rows.find(r=>groupKey(r)===wants[i].key);if(r)chooseWantVersion(r,i);return;}const button=e.target.closest('[data-remove-want]');if(button){wants.splice(+button.dataset.removeWant,1);saveWants();}};
$('wantItems').onchange=e=>{const index=e.target.dataset.wantQuantity;if(index===undefined)return;const quantity=Number(e.target.value);if(Number.isInteger(quantity)&&quantity>=1&&quantity<=999){wants[+index].quantity=quantity;saveWants();}else renderWants();};

function image(r, cls='') {
    const name = esc(cardName(r));
    return r.info.image
        ? `<img class="${cls}" src="${esc(cls === 'detail-img' ? r.info.image : (r.info.thumbnail || r.info.image))}" alt="${name}" loading="lazy">`
        : `<div class="fallback">◈<br>${name}</div>`;
}

$('showImages').onchange = () => { showImages = $('showImages').checked; render(); };

function render() {
    renderWants();
    if (!adminRoute && globalThis.history) history.replaceState(null,'',searchURL());
    $('showImagesLabel').textContent = t('Show images');
    $('showImages').checked = showImages;
    $('grid').classList.toggle('text-view', !showImages);
    $('resultCount').textContent = payload.admin ? `${num(filtered.length)} entries` : '';
    $('scope').textContent = payload.admin ? (folder || `Across ${folders.length} visible folders`) : t('Browse the collection');
    $('empty').hidden = !!filtered.length;
    $('grid').innerHTML = filtered.slice((page-1)*pageSize,page*pageSize).map(r => {
        const name = esc(cardName(r));
        const badge = payload.admin ? `<span class="quantity">×${esc(r.Quantity)}</span>` : '';
        const location = payload.admin ? `<div class="card-folder">▱ ${esc(r['Folder Name'])}</div>` : '';
        const rarities = (r.rarities || [r.Rarity]).map(rarityLabel).join(' · ');
        return `<div class="card-entry"><button class="card" data-id="${r._id}" aria-label="${t('View')} ${name}, ${esc(r['Card Number'])}">${showImages ? `<div class="art">${image(r)}${badge}</div>` : badge}<h3 title="${name}">${name}</h3><div class="card-meta"><span>${esc(r['Card Number'])}</span><span class="rarity">${esc(rarities)}</span></div>${location}${!showImages ? `<span class="copy-count">${t('Copies')}: ${num(payload.admin ? r.Quantity : r.copies)}</span>` : ''}<div class="offer">${esc(offer(r))}</div></button>${!payload.admin ? `<button type="button" class="want-add" data-add-want="${r._id}" aria-pressed="${wants.some(x=>x.key===groupKey(r))}">${wants.some(x=>x.key===groupKey(r)) ? (language==='es'?'✓ En la lista':'✓ In want list') : (language==='es'?'+ Lista de deseos':'+ Want list')}</button>` : ''}</div>`;
    }).join('');
    $('page').textContent = payload.admin
        ? `${filtered.length?(page-1)*pageSize+1:0}–${Math.min(page*pageSize,filtered.length)} of ${num(filtered.length)}`
        : `${language === 'es' ? 'Página' : 'Page'} ${page} / ${Math.max(1, Math.ceil(filtered.length/pageSize))}`;
    $('prev').disabled = page === 1;
    $('next').disabled = page * pageSize >= filtered.length;
    fallbackImages();
}

function fallbackImages(){document.querySelectorAll('img').forEach(img=>img.onerror=()=>{const d=document.createElement('div');d.className='fallback';d.textContent=img.alt;img.replaceWith(d)})}
$('prev').onclick=()=>{page--;render();$('title').scrollIntoView({behavior:'smooth'})};$('next').onclick=()=>{page++;render();$('title').scrollIntoView({behavior:'smooth'})};
$('grid').onclick=e=>{const add=e.target.closest('[data-add-want]');if(add){const r=filtered.find(r=>r._id===+add.dataset.addWant);if(r)chooseWantVersion(r);return;}const b=e.target.closest('[data-id]');if(b)openCard(+b.dataset.id)};
function referenceSection(r){
 if(!payload.admin)return '';
 const ref=r.reference_prices||{};
 const match=({matched:'Set code + rarity matched',ambiguous:'Multiple set entries — no price selected',unmatched:'No matching set code + rarity',unpriced:'Matched set has no usable price'})[ref.match]||'Not fetched yet';
 return `<section class="reference-prices"><h3>YGOPRODeck reference prices</h3><p class="detail-note">${esc(match)}. Edition, language, and condition are not verified by this price match.</p><dl><div><dt>Matched set price · USD</dt><dd>${ref.set_price?esc(price(ref.set_price))+' USD':'Unavailable'}</dd></div><div><dt>Data fetched</dt><dd>${ref.fetched_at?esc(new Date(ref.fetched_at*1000).toLocaleString()):'Not fetched'}${ref.stale?' · stale':''}</dd></div></dl><p class="detail-note">Vendor figures below are lowest prices across versions, not prices for your specific printing.</p><div class="vendor-prices">${(ref.vendors||[]).map(v=>`<span>${esc(v.vendor)}: <b>${esc(price(v.amount))} ${esc(v.currency)}</b></span>`).join('')||'<span>No vendor quotes available.</span>'}</div><p class="detail-note">Source: <a href="https://ygoprodeck.com/api-guide/" target="_blank" rel="noopener">YGOPRODeck</a>. Fetch time is not the time of the underlying sale. Zero/missing quotes are treated as unavailable. CSV prices and the public offer are separate.</p></section>`;
}
function visitorDetails(r) {
    const variants = new Map();
    for (const item of rows.filter(item => groupKey(item) === groupKey(r))) {
        const fields = [
            item['Card Number'], rarityLabel(item.Rarity), t(item.Printing),
            t(item.Language), t(item.Condition), offer(item)
        ];
        variants.set(JSON.stringify(fields), fields);
    }
    const labels = ['Card number','Rarity','Printing','Language','Condition','Offer price · per copy'];
    const details = [...variants.values()].map(fields =>
        `<dl class="public-variant">${fields.map((value,index)=>`<div><dt>${t(labels[index])}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`
    ).join('');
    return `<div class="detail-layout"><div>${image(r,'detail-img')}</div><div>
        <h2>${esc(cardName(r))}</h2><p>${esc(setName(r))}</p>
        <p class="effect">${esc(r.info.desc || '')}</p>
        <h3>${t('Available versions')}</h3>${details}
        </div></div>`;
}
function openCard(id){const r=rows.find(x=>x._id===id);if(!r)return;if(!payload.admin){lastFocus=document.activeElement;$('detailContent').innerHTML=visitorDetails(r);fallbackImages();if(!$('detail').open)$('detail').showModal();$('detail').scrollTop=0;return;}const c=r.info;lastFocus=document.activeElement;const attrs=['attribute','race','level','linkval','atk','def'].filter(k=>c[k]!=null).map(k=>`<span>${({level:'LEVEL ',linkval:'LINK ',atk:'ATK ',def:'DEF '})[k]||''}${esc(c[k])}</span>`).join('');const fields=[...(payload.admin?[['Folder',r['Folder Name']]]:[]),['Set',r['Set Name']],['Card number',r['Card Number']],['Rarity',rarityLabel(r.Rarity)],['Condition',r.Condition],['Printing',r.Printing],['Language',r.Language],['Owned / Trade',`${r.Quantity} / ${r['Trade Quantity']}`],['Offer price · per copy',r.public_price===null?'Ask for price':price(r.public_price)+' '+payload.currency],...(payload.admin? [['Date bought',r['Date Bought']||'Not recorded'],['Bought · per copy',price(r['Price Bought'])],['CSV low / mid',`${price(r.LOW)} / ${price(r.MID)}`],['CSV market · per copy',price(r.MARKET)]]:[])];const other=rows.filter(x=>x['Card Name']===r['Card Name']&&x._id!==id);
$('detailContent').innerHTML=`<div class="detail-layout"><div>${image(r,'detail-img')}<p class="detail-note">Representative artwork. Your edition and condition are listed alongside.</p></div><div><div class="type-tag">${esc(c.type||'CARD DETAILS')}</div><h2>${esc(r['Card Name'])}</h2><div class="attributes">${attrs}</div><p class="effect">${esc(c.desc||'Effect text is unavailable for this card. Your collection details are shown below.')}</p><dl>${fields.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>${referenceSection(r)}<p class="detail-note">${payload.admin?'Offer prices average LOW and MARKET from the uploaded CSV.':'Price shown is the offered price per copy.'}</p>${other.length?`<h3>Other entries in your collection (${other.length})</h3>${other.map(x=>`<button class="other" data-other="${x._id}">${esc(x['Card Number'])}${payload.admin?' · '+esc(x['Folder Name']):''} · ×${x.Quantity}</button>`).join('')}`:''}</div></div>`;fallbackImages();if(!$('detail').open)$('detail').showModal();$('detail').scrollTop=0;}
$('detailContent').onclick=e=>{const b=e.target.closest('[data-other]');if(b)openCard(+b.dataset.other)};$('close').onclick=()=>$('detail').close();$('detail').addEventListener('close',()=>lastFocus?.focus());$('detail').onclick=e=>{if(e.target===$('detail')){const r=$('detail').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('detail').close()}};

$('loginForm').onsubmit=async e=>{e.preventDefault();try{await request('/api/login',{password:$('password').value});$('password').value='';$('message').hidden=true;await load()}catch(e){notify(e.message,true)}};
function renderAdmin(){
 const settings=payload.settings;
 $('viewLink').textContent='OPEN USER VIEW →';$('viewLink').href='/';$('viewLink').target='_blank';
 $('adminPanel').hidden=false;
 $('adminPanel').innerHTML=`<div class="admin-heading"><div><div class="eyebrow">ADMIN WORKSPACE</div><h2>Manage your collection</h2></div><button id="logout">Sign out</button></div><div class="admin-columns"><section><h3>Visitor access & pricing</h3><p>Cards from checked folders appear in the user view; folder names remain private. New folders start private.</p><form id="settingsForm"><div class="price-settings"><label>Currency label<input id="currency" maxlength="12" value="${esc(settings.currency)}" placeholder="e.g. USD"></label></div><p class="detail-note">Offer prices average LOW and MARKET from the uploaded CSV. Entries missing either value show “Ask for price”.</p><div class="visibility-list">${Object.entries(payload.folders).sort(([a],[b])=>a.localeCompare(b)).map(([f,n])=>`<label><input type="checkbox" name="visibleFolder" value="${esc(f)}" ${settings.visible_folders.includes(f)?'checked':''}><span>${esc(f)}</span><small>${n} entries</small></label>`).join('')}</div><button type="submit">Save visibility & pricing</button></form></section><section><h3>Update collection</h3><p>Upload a full collection export. This replaces all entries; it does not append them. Existing folder visibility and pricing settings are preserved.</p><label class="upload-box">Choose collection CSV<input type="file" id="csvUpload" accept=".csv,text/csv"></label><div id="importPreview" hidden></div><h3 class="cache-heading">Card data, prices & artwork</h3><p id="cacheStatus"></p><p class="detail-note" id="cacheExtra"></p><button id="refreshCache">Refresh prices, metadata & missing images</button><p class="detail-note">${payload.hosted ? 'Imports and this button queue metadata and artwork updates for the hosted refresh worker.' : 'Already cached artwork is reused. New names and missing artwork are fetched in the background after imports. YGOPRODeck metadata and reference prices refresh when older than 48 hours on startup/import, or with this button.'} Admin card details show CSV prices and YGOPRODeck reference prices.</p></section></div>`;
 showCache(payload.cache);
 $('settingsForm').onsubmit=async e=>{e.preventDefault();try{await request('/api/admin/settings',{markup:null,currency:$('currency').value,visible_folders:[...document.querySelectorAll('[name=visibleFolder]:checked')].map(x=>x.value)});notify('Visitor access and offer pricing saved.');await load()}catch(e){notify(e.message,true)}};
 $('logout').onclick=async()=>{try{await request('/api/logout',{});location.href='/admin'}catch(e){notify(e.message,true)}};
 $('csvUpload').onchange=async()=>{const file=$('csvUpload').files[0];if(!file)return;try{if(file.size>11*1024*1024)throw new Error('CSV must be smaller than 11 MB.');const body={csv:await file.text()};if(payload.hosted&&new TextEncoder().encode(JSON.stringify(body)).length>4_000_000)throw new Error('Hosted CSV uploads must fit within 4 MB.');const preview=await request('/api/admin/preview',body);$('importPreview').hidden=false;$('importPreview').innerHTML=`<h4>Review replacement</h4><p>${num(preview.entries)} entries · ${num(preview.copies)} copies</p><p>${preview.added_entries} added or changed entries · ${preview.removed_entries} removed or changed entries</p><p>New private folders: ${esc(preview.new_folders.join(', ')||'None')}</p><button id="confirmImport">Replace collection with this CSV</button><button id="cancelImport" type="button">Cancel</button>`;$('confirmImport').onclick=async()=>{try{$('confirmImport').disabled=true;const result=await request('/api/admin/import',{token:preview.token});notify(result.message);await load()}catch(e){notify(e.message,true);$('confirmImport').disabled=false}};$('cancelImport').onclick=()=>{$('importPreview').hidden=true;$('csvUpload').value=''}}catch(e){$('importPreview').hidden=true;$('csvUpload').value='';notify(e.message,true)}};
 $('refreshCache').onclick=async()=>{try{const result=await request('/api/admin/refresh',{});notify(result.queued?'Metadata and artwork refresh queued.':result.started?'Price and metadata refresh started. Reopen a card after completion to see updated references.':'A cache refresh is already running.')}catch(e){notify(e.message,true)}};
}
function showCache(cache){if(!$('cacheStatus'))return;$('cacheStatus').textContent=cache.status||'Ready';$('cacheExtra').textContent=[cache.error,cache.refresh_pending?'A metadata and artwork refresh is queued.':'',cache.price_coverage?Object.entries(cache.price_coverage).map(([status,count])=>num(count)+' '+status).join(' · ')+' collection price entries.':'',cache.unmatched?.length?'No database match: '+cache.unmatched.join(', '):'',Object.keys(cache.failures||{}).length+' artwork downloads pending.',cache.last_refresh?'Metadata checked: '+new Date(cache.last_refresh*1000).toLocaleString():''].filter(Boolean).join(' ')}
setInterval(async()=>{try{if(adminRoute){if(!$('adminPanel').hidden){const data=await request('/api/admin');showCache(data.cache);payload.cache=data.cache;rows=data.rows;payload.rows=data.rows;}}else{const data=await request('/api/collection');if(data.revision!==payload?.revision||JSON.stringify(data.rows)!==JSON.stringify(rows)){if($('detail').open)$('detail').close();payload=data;rows=data.rows;folders=payload.admin?[...new Set(rows.map(r=>r['Folder Name']))].sort((a,b)=>a.localeCompare(b)):[];initialize()}}}catch(e){if(adminRoute&&e.status===401){showLogin();notify('Your admin session ended. Sign in again.',true)}}},15000);
load();
