// No browser or network: exercise rendering against deterministic API fixtures.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const script=fs.readFileSync('public/app.js','utf8');
async function fixture(admin=false,denied=false,search="",storage=new Map()){
 let copied="", replaced="";
 const elements=new Map();
 function element(id){if(!elements.has(id))elements.set(id,{id,value:id==='sort'?'name':'',textContent:'',innerHTML:'',hidden:true,checked:false,open:false,options:[{value:'name'},{value:'value'},{value:'Fixture set'},{value:'SR'},{value:'Monster'}],classList:{toggle(){}},add(){},addEventListener(){},scrollIntoView(){},focus(){},showModal(){this.open=true},close(){this.open=false}});return elements.get(id)}
 const row={copies:2,spanish_name:'Carta de prueba','Folder Name':'Visible folder','Quantity':'2','Trade Quantity':'0','Card Name':'<Test Card>','Set Name':'Fixture set','Card Number':'TEST-EN001','Set Code':'TEST','Rarity':'SR','Language':'English','Condition':'NearMint','Printing':'1st Edition',_id:7,info:{image:'assets/1.jpg',thumbnail:'assets/1-thumb-v2.webp',type:'Effect Monster',desc:'Fixture effect'},public_price:null};
 row.reference_prices={match:'matched',set_price:'77.88',fetched_at:1,stale:true,vendors:[{vendor:'TCGplayer',amount:'66.55',currency:'USD'}]};
 if(admin)Object.assign(row,{'Price Bought':'123.45',LOW:'10.11',MID:'20.22',MARKET:'30.33'});
 const response={admin,rows:[row,{...row,_id:8,Language:'Spanish'}],currency:'USD',revision:1,settings:{visible_folders:['Visible folder'],markup:null,currency:'USD'},folders:{'Visible folder':1},cache:{status:'Ready',failures:{}}};
 const context=vm.createContext({document:{getElementById:element,querySelector:element,querySelectorAll:()=>[],activeElement:null},URL,URLSearchParams,location:{pathname:admin?'/admin':'/',href:'http://localhost/'+search,search},localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},navigator:{clipboard:{writeText:async text=>{copied=text;}}},history:{replaceState:(_a,_b,url)=>{replaced=String(url);}},Option:function(){},fetch:async()=>({ok:!denied,status:denied?401:200,json:async()=>denied?{error:'Admin login required.'}:response}),setInterval(){},console});
 vm.runInContext(script,context);await new Promise(resolve=>setImmediate(resolve));
 return {context,element,response,storage,copied:()=>copied,replaced:()=>replaced};
}
(async()=>{
 const user=await fixture();assert.equal(user.element('stats').hidden,true);assert.equal((user.element('grid').innerHTML.match(/data-id=/g)||[]).length,1);assert.doesNotMatch(user.element('grid').innerHTML,/class="quantity"/);assert.match(user.element('grid').innerHTML,/Super Rare \(SR\)/);assert.equal(user.element('folders').hidden,true);assert.equal(user.element('folderHeading').hidden,true);assert.equal(user.element('folderStat').hidden,true);assert.doesNotMatch(user.element('grid').innerHTML,/Visible folder|card-folder/);assert.match(user.element('grid').innerHTML,/Ask for price/);assert.match(user.element('grid').innerHTML,/&lt;Test Card&gt;/);
 assert.doesNotMatch(user.element('grid').innerHTML, /<img/);
 assert.match(user.element('grid').innerHTML, /Copies: 4/);
 user.element('showImages').checked=true;user.element('showImages').onchange();
 assert.match(user.element('grid').innerHTML, /<img/);
 assert.match(user.element('grid').innerHTML, /1-thumb-v2.webp/);
 user.element('showImages').checked=false;user.element('showImages').onchange();
 assert.doesNotMatch(user.element('grid').innerHTML, /<img/);
 // Non-contiguous IDs occur after server-side private-folder filtering.
 vm.runInContext('openCard(7)',user.context);assert.match(user.element('detailContent').innerHTML,/Fixture effect/);assert.doesNotMatch(user.element('detailContent').innerHTML,/CSV low|CSV market|Bought ·|77.88|66.55|YGOPRODeck reference|Visible folder|<dt>Folder/ );assert.equal(user.element('detail').open,true);assert.match(user.element('detailContent').innerHTML,/Spanish/);assert.doesNotMatch(user.element('detailContent').innerHTML,/Owned|Trade|Quantity/);
 user.element('language').onclick();user.element('search').value='carta de prueba';vm.runInContext('update()',user.context);assert.match(user.element('grid').innerHTML,/Carta de prueba/);
 user.element('set').value='Other set';vm.runInContext('update()',user.context);assert.equal(user.element('empty').hidden,false);user.element('set').value='';
 user.element('search').value='no-match';vm.runInContext('update()',user.context);assert.equal(user.element('empty').hidden,false);
 const shared=await fixture(false,false,'?q=Test&set=Fixture+set&rarity=SR&type=Monster&sort=value&images=1&lang=es');
 assert.equal(shared.element('search').value,'Test');assert.equal(shared.element('set').value,'Fixture set');assert.equal(shared.element('rarity').value,'SR');assert.equal(shared.element('type').value,'Monster');assert.equal(shared.element('sort').value,'value');assert.equal(shared.element('showImages').checked,true);
 await shared.element('shareSearch').onclick();assert.match(shared.copied(),/q=Test/);assert.match(shared.copied(),/lang=es/);assert.match(shared.replaced(),/images=1/);
 const selection=await fixture();
 selection.element('grid').onclick({target:{closest:selector=>selector==='[data-add-want]'?{dataset:{addWant:'7'}}:null}});
 selection.element('confirmWantVersion').onclick();
 assert.match(selection.element('wantItems').innerHTML,/&lt;Test Card&gt;/);assert.equal(JSON.parse(selection.storage.get('duel-wants')).length,1);
 selection.element('wantItems').onchange({target:{dataset:{wantQuantity:'0'},value:'3'}});
 await selection.element('copyWant').onclick();assert.match(selection.copied(),/3 × <Test Card> — Fixture set/);assert.doesNotMatch(selection.copied(),/Visible folder|77.88/);assert.match(selection.copied(),/TEST-EN001 · English · 1st Edition · Super Rare \(SR\) · NearMint/);
 selection.element('grid').onclick({target:{closest:selector=>selector==='[data-add-want]'?{dataset:{addWant:'7'}}:null}});
 selection.element('wantVersion').value='1';selection.element('confirmWantVersion').onclick();
 assert.equal(JSON.parse(selection.storage.get('duel-wants')).length,2);
 await selection.element('copyWant').onclick();assert.match(selection.copied(),/Spanish/);assert.match(selection.copied(),/English/);
 const reloaded=await fixture(false,false,'',selection.storage);assert.match(reloaded.element('wantItems').innerHTML,/value="3"/);
 reloaded.element('clearWant').onclick();assert.equal(JSON.parse(selection.storage.get('duel-wants')).length,0);
 const admin=await fixture(true);assert.equal(admin.element('adminPanel').hidden,false);assert.equal(admin.element('folders').hidden,false);assert.match(admin.element('grid').innerHTML,/Visible folder/);assert.match(admin.element('adminPanel').innerHTML,/visibleFolder/);vm.runInContext('openCard(7)',admin.context);assert.match(admin.element('detailContent').innerHTML,/123\.45/);assert.match(admin.element('detailContent').innerHTML,/Visible folder/);assert.match(admin.element('detailContent').innerHTML,/77\.88 USD/);assert.match(admin.element('detailContent').innerHTML,/66\.55 USD/);
 const denied=await fixture(true,true);assert.equal(denied.element('login').hidden,false);assert.equal(denied.element('collectionView').hidden,true);
 console.log('Frontend checks passed: public/admin initialization, redacted details, stable card IDs, escaped CSV content, empty results, and login gate.');
})().catch(e=>{console.error(e);process.exitCode=1});
