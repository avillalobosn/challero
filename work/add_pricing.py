from pathlib import Path
p=Path('server.py');s=p.read_text();s=s.replace('from collections import Counter','from collections import Counter\nfrom decimal import Decimal, InvalidOperation')
s=s.replace('MAX_UPLOAD = 12 * 1024 * 1024','MAX_UPLOAD = 12 * 1024 * 1024\nCATALOG_TTL = 2 * 86400')
pos=s.index('\ndef collection_payload')
s=s[:pos]+'''
def source_amount(value):
    try:
        if isinstance(value, bool): return None
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount > Decimal('1e12'): return None
        return format(amount.quantize(Decimal('.01')), '.2f')
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
''' +s[pos:]
s=s.replace("if info.get('image')", "pricing = info.pop('pricing', {})\n            if admin: item['reference_prices'] = reference_prices(r, pricing)\n            if info.get('image')",1)
s=s.replace('> 7*86400','> CATALOG_TTL')
s=s.replace("if c: cards[name]={k:c[k] for k in INFO_FIELDS if k in c}","if c:\n                cards[name]={k:c[k] for k in INFO_FIELDS if k in c}\n                cards[name]['pricing']=pricing_metadata(c,catalog['updated'])")
s=s.replace('Refreshing card metadata and missing artwork…','Refreshing card metadata, reference prices, and missing artwork…')
p.write_text(s)
p=Path('public/app.js');s=p.read_text();where=s.index('function openCard(')
s=s[:where]+'''function referenceSection(r){
 if(!payload.admin)return '';
 const ref=r.reference_prices||{};
 const match=({matched:'Set code + rarity matched',ambiguous:'Multiple set entries — no price selected',unmatched:'No matching set code + rarity',unpriced:'Matched set has no usable price'})[ref.match]||'Not fetched yet';
 return `<section class="reference-prices"><h3>YGOPRODeck reference prices</h3><p class="detail-note">${esc(match)}. Edition, language, and condition are not verified by this price match.</p><dl><div><dt>Matched set price · USD</dt><dd>${ref.set_price?esc(price(ref.set_price))+' USD':'Unavailable'}</dd></div><div><dt>Data fetched</dt><dd>${ref.fetched_at?esc(new Date(ref.fetched_at*1000).toLocaleString()):'Not fetched'}${ref.stale?' · stale':''}</dd></div></dl><p class="detail-note">Vendor figures below are lowest prices across versions, not prices for your specific printing.</p><div class="vendor-prices">${(ref.vendors||[]).map(v=>`<span>${esc(v.vendor)}: <b>${esc(price(v.amount))} ${esc(v.currency)}</b></span>`).join('')||'<span>No vendor quotes available.</span>'}</div><p class="detail-note">Source: <a href="https://ygoprodeck.com/api-guide/" target="_blank" rel="noopener">YGOPRODeck</a>. Fetch time is not the time of the underlying sale. Zero/missing quotes are treated as unavailable. CSV prices and the public offer are separate.</p></section>`;
}
''' +s[where:]
s=s.replace("</dl><p class=\"detail-note\">${payload.admin?", "</dl>${referenceSection(r)}<p class=\"detail-note\">${payload.admin?")
s=s.replace('Refresh metadata & retry missing images','Refresh prices, metadata & missing images').replace('Card data & artwork','Card data, prices & artwork')
s=s.replace('Card metadata is refreshed when older than 7 days, or with this button. Collection price values come only from CSV uploads.','YGOPRODeck metadata and reference prices refresh when older than 48 hours on startup/import, or with this button. Reference prices are visible in admin card details. CSV price values remain unchanged.')
s=s.replace('Cache refresh started. Existing cards remain available.','Price and metadata refresh started. Reopen a card after completion to see updated references.')
s=s.replace('showCache(data.cache)}}else',"showCache(data.cache);payload.cache=data.cache;rows=data.rows;payload.rows=data.rows;}}else")
p.write_text(s)
p=Path('public/style.css');p.write_text(p.read_text()+ '\n.reference-prices{border:1px solid #43543d;border-radius:6px;padding:16px;margin:22px 0}.reference-prices h3{font-family:Georgia,serif;font-weight:400;margin:0 0 12px}.vendor-prices{display:flex;flex-direction:column;gap:9px;font-size:11px;color:var(--muted)}.vendor-prices b{color:var(--white);font-weight:400}\n')
