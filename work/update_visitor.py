from pathlib import Path
p=Path('public/app.js')
s=p.read_text()
a=s.index('const num=')
b=s.index('function initialize()',a)
s=s[:a]+'''let language = !adminRoute && globalThis.localStorage?.getItem('duel-language') === 'es' ? 'es' : 'en';
const translations = {
    'All cards':'Todas las cartas', 'All sets':'Todas las colecciones', 'All rarities':'Todas las rarezas',
    'All card types':'Todos los tipos', 'Monster':'Monstruo', 'Spell':'Mágica', 'Trap':'Trampa',
    'Browse the collection':'Explora la colección', 'Ask for price':'Consultar precio', 'From':'Desde',
    'Set':'Colección', 'Card number':'Número de carta', 'Rarity':'Rareza', 'Condition':'Estado',
    'Printing':'Edición', 'Language':'Idioma', 'Offer price · per copy':'Precio por copia',
    'English':'Inglés', 'Spanish':'Español', '1st Edition':'1.ª edición', 'Unlimited':'Ilimitada',
    'NearMint':'Casi nueva', 'LightlyPlayed':'Poco usada', 'ModeratelyPlayed':'Uso moderado',
    'HeavilyPlayed':'Muy usada', 'Damaged':'Dañada', 'Available versions':'Versiones disponibles',
    'Card details':'Detalles de la carta', 'View':'Ver', 'cards':'cartas'
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
const normalize = value => String(value ?? '').normalize('NFD').replace(/\\p{Diacritic}/gu,'').toLowerCase().trim();
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
        return {
            ...first,
            public_price: prices.size ? Math.min(...prices) : null,
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
        ['#viewLink','ADMIN SIGN IN →','ACCESO DE ADMINISTRACIÓN →'],
        ['#tradeLabel','Available to trade','Disponible para intercambio'],
        ['#sort option[value="name"]','Name: A–Z','Nombre: A–Z'],
        ['#sort option[value="value"]','Price: high–low','Precio: de mayor a menor']
    ]) {
        const element = document.querySelector(selector);
        if (element) element.textContent = spanish ? es : en;
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

'''+s[b:]
s=s.replace("$('stats').classList.toggle('public-stats',!payload.admin);", "$('stats').hidden=!payload.admin;\nlocalizeVisitor();")
s=s.replace("$('copies').textContent=num(rows.reduce", "if (payload.admin) {\n$('copies').textContent=num(rows.reduce")
s=s.replace("$('allCount').textContent=num(rows.length);", "}\n$('allCount').textContent=payload.admin?num(rows.length):'';")
s=s.replace("Private CSV prices are admin-only. USD offer pricing is awaiting a LOW/MARKET formula.", "Offers use the average of CSV LOW and MARKET.")
s=s.replace("'Prices shown are the offered price per copy.'", "(language==='es'?'Precios por copia.':'Prices shown are per copy.')")
s=s.replace("$('rarity').innerHTML='<option value=\"\">All rarities</option>';", "fillOptions('set',[...new Set(rows.map(setName).filter(Boolean))].sort().map(name=>[name,name]),'All sets');")
s=s.replace("[...new Set(rows.map(r=>r.Rarity))].sort().forEach(r=>{$('rarity').add(new Option(r,r))});", "fillOptions('rarity',[...new Set(rows.map(r=>r.Rarity).filter(Boolean))].map(code=>[code,rarityLabel(code)]).sort((a,b)=>a[1].localeCompare(b[1],language)),'All rarities');")
s=s.replace("f||'All cards'", "f||t('All cards')")
s=s.replace("'search','type','rarity','sort','trade'","'search','type','set','rarity','sort','trade'").replace("'search','type','rarity'","'search','type','set','rarity'")
a=s.index('function update()')
b=s.index('function fallbackImages()',a)
s=s[:a]+'''function update() {
    page = 1;
    const query = normalize($('search').value);
    const items = rows.filter(r =>
        (!folder || r['Folder Name'] === folder) &&
        (!$('set').value || setName(r) === $('set').value) &&
        (!$('rarity').value || r.Rarity === $('rarity').value) &&
        (!$('type').value || (r.info.type || '').includes($('type').value)) &&
        (!$('trade').checked || r.available_to_trade) &&
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

function image(r, cls='') {
    const name = esc(cardName(r));
    return r.info.image
        ? `<img class="${cls}" src="${esc(r.info.image)}" alt="${name}" loading="lazy">`
        : `<div class="fallback">◈<br>${name}</div>`;
}

function render() {
    $('resultCount').textContent = payload.admin ? `${num(filtered.length)} entries` : '';
    $('scope').textContent = payload.admin ? (folder || `Across ${folders.length} visible folders`) : t('Browse the collection');
    $('empty').hidden = !!filtered.length;
    $('grid').innerHTML = filtered.slice((page-1)*pageSize,page*pageSize).map(r => {
        const name = esc(cardName(r));
        const badge = payload.admin ? `<span class="quantity">×${esc(r.Quantity)}</span>` : '';
        const location = payload.admin ? `<div class="card-folder">▱ ${esc(r['Folder Name'])}</div>` : '';
        const rarities = (r.rarities || [r.Rarity]).map(rarityLabel).join(' · ');
        return `<button class="card" data-id="${r._id}" aria-label="${t('View')} ${name}, ${esc(r['Card Number'])}"><div class="art">${image(r)}${badge}</div><h3 title="${name}">${name}</h3><div class="card-meta"><span>${esc(r['Card Number'])}</span><span class="rarity">${esc(rarities)}</span></div>${location}<div class="offer">${esc(offer(r))}</div></button>`;
    }).join('');
    $('page').textContent = payload.admin
        ? `${filtered.length?(page-1)*pageSize+1:0}–${Math.min(page*pageSize,filtered.length)} of ${num(filtered.length)}`
        : `${language === 'es' ? 'Página' : 'Page'} ${page} / ${Math.max(1, Math.ceil(filtered.length/pageSize))}`;
    $('prev').disabled = page === 1;
    $('next').disabled = page * pageSize >= filtered.length;
    fallbackImages();
}

'''+s[b:]
a=s.index('function openCard(')
s=s[:a]+'''function visitorDetails(r) {
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
'''+s[a:]
s=s.replace("const c=r.info;lastFocus=document.activeElement;", "if(!payload.admin){lastFocus=document.activeElement;$('detailContent').innerHTML=visitorDetails(r);fallbackImages();if(!$('detail').open)$('detail').showModal();$('detail').scrollTop=0;return;}const c=r.info;lastFocus=document.activeElement;")
s=s.replace("['Rarity',r.Rarity]", "['Rarity',rarityLabel(r.Rarity)]")
s=s.replace('Private prices are CSV snapshots. The USD offer formula using LOW and MARKET is pending.', 'Offer prices are the average of CSV LOW and MARKET.')
s=s.replace('USD offer pricing is pending. We will choose how LOW and MARKET combine later. Visitors currently see “Ask for price”; source prices stay private.', 'Offer prices average the imported CSV LOW and MARKET values. Entries missing either price show “Ask for price”.')
s=s.replace("else{$('viewLink').textContent='ADMIN SIGN IN →';$('viewLink').href='/admin';}", "else{$('viewLink').href='/admin';}")
p.write_text(s)
p=Path('public/index.html')
s=p.read_text().replace('<option value="quantity">','<option id="quantitySort" value="quantity">').replace('> Available to trade</label>','> <span id="tradeLabel">Available to trade</span></label>')
p.write_text(s)
