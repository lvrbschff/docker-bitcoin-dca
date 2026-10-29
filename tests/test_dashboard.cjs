// Run with node tests/test_dashboard.cjs; stdlib only, no browser or exchange IO.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const html=JSON.parse(execFileSync(process.env.PYTHON||'python3',['-c',`import ast,json
from pathlib import Path
m=ast.parse(Path('web_ui.py').read_text());print(json.dumps(next(ast.literal_eval(n.value) for n in m.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HTML' for t in n.targets))))`],{encoding:'utf8'}));
const script=html.split('<script>')[1].split('</script>')[0];
class Element{
 constructor(){this.value='';this.children=[];this.handlers={};this.textContent='';this.disabled=false;this.classList={toggle(){},add(){},remove(){}}}
 appendChild(e){this.children.push(e)} append(...e){this.children.push(...e)} replaceChildren(...e){this.children=e}
 set innerHTML(v){this.children=[]} addEventListener(event,handler){this.handlers[event]=handler}
}
const elements={},get=id=>{assert(!["balance","balanceValue"].includes(id),"Removed portfolio element must not be accessed");return elements[id]??=new Element()};get('pageSize').value=html.match(/<option selected>(\d+)<\/option>/)[1];
const records=Array.from({length:45},(_,i)=>({activityId:String(i),datetime:'2026-10-22T12:00:00Z',type:'Buy',identifier:'BTC',shares:'.001',currency:i<22?'EUR':'USD',price:i<22?'80000':'100000',amount:i<22?'80':'100',fee:i<22?'.2':'.25'}));
const requests=[];let checks=0,eurOnly=false,priceUnavailable=false,invalidUsdRate=false,withdrawalUnavailable=false;
const response=data=>({ok:true,json:async()=>data});
const context={document:{getElementById:get,createElement:()=>new Element()},localStorage:{getItem:()=>null,setItem(){}},Intl,Date,URL,console,alert(){throw Error('Withdrawal must not use a browser alert')},confirm:()=>false,setInterval(){},setTimeout(fn){queueMicrotask(fn)},window:{location:{reload(){throw Error('Unexpected reload')}}},fetch:async(url,options)=>{
 requests.push(url);const parsed=new URL(url,'http://example.test');
 if(parsed.pathname==='/api/schedule')return response({quote:'EUR',exchange:'KRAKEN',quoteBalance:'72.7719',balanceUpdatedAt:'2026-10-29T12:00:00Z',nextPurchaseAt:new Date(Date.now()+3600000).toISOString(),serverTime:new Date().toISOString(),timezone:'Europe/Berlin'});
 if(parsed.pathname==='/api/withdrawals')return response([{timestamp:Date.parse('2026-10-20T12:00:00Z')},{timestamp:Date.parse('2026-10-23T12:00:00Z')}]);
 if(parsed.pathname==='/api/withdrawal/config')return response({enabled:!withdrawalUnavailable,destination:'fictional wallet',reason:withdrawalUnavailable?'Manual withdrawal is unavailable: set KRAKEN_WITHDRAW_KEY to your saved Bitcoin withdrawal method in .env.':''});
 if(parsed.pathname==='/api/market-price')return priceUnavailable?{ok:false,json:async()=>({error:'offline'})}:response({price:'80000',conversionRate:'1',conversionRates:{EUR:'1',USD:invalidUsdRate?'invalid':'0.9'},updatedAt:Date.now()/1000});
 if(parsed.pathname==='/api/analytics'){
  const limit=Number(parsed.searchParams.get('limit'))||10,pages=Math.ceil(records.length/limit);
  const page=Math.max(1,Math.min(pages,Number(parsed.searchParams.get('page'))||1));
  return response({quote:'EUR',exchange:'KRAKEN',configuration:{liveTrading:true,buyAmount:0,depositDay:1,withdrawalsEnabled:false,withdrawalThreshold:1000},snapshot:{price:80000,balance:'.045',updatedAt:new Date().toISOString()},summary:{buyCount:45,boughtBtc:'.045',withdrawnBtc:0,withdrawalCount:0,pendingWithdrawalCount:0,netBtc:'.045'},summaryByCurrency:eurOnly?{EUR:{buyVolume:3600,tradeFees:9,withdrawalFees:0}}:{EUR:{buyVolume:1760,tradeFees:4.4,withdrawalFees:0},USD:{buyVolume:2300,tradeFees:5.75,withdrawalFees:0}},activities:records.slice((page-1)*limit,page*limit),pagination:{page,pageSize:limit,total:45,pages}});
 }
 if(parsed.pathname==='/api/withdrawal/preview')return response({jobId:'fictional-job'});
 if(parsed.pathname==='/api/withdrawal/jobs/fictional-job')return response(++checks===1?{state:'queued'}:{state:'completed',result:{token:'single-use'}});
 throw Error('Unexpected URL '+url);
}};
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(setImmediate)};
(async()=>{
 vm.createContext(context);vm.runInContext(script,context);await settle();
 assert(get('start').value&&get('end').value,'Both dates must initialize for the selected interval');
 assert.equal(get('quoteBalance').textContent,'€72.77');
 assert.equal(get('quoteBalanceLabel').textContent,'Available EUR balance');
 assert(html.indexOf('id="quoteBalance"')<html.indexOf('id="nextPurchaseAt"'),'Fiat balance precedes next purchase');
 vm.runInContext('displayCurrency="USD"',context);await vm.runInContext('refreshPurchaseSchedule()',context);
 assert.equal(get('quoteBalance').textContent,'€72.77','Balance stays in the exchange trading currency');
 vm.runInContext('displayCurrency="EUR"',context);
 assert.equal(get('pageSize').value,'10');
 assert(html.includes('<option>5</option>'),'5 entries must be selectable');
 assert(get('pageInfo').textContent.includes('1–10 of 45'));
 assert.equal(get('activities').children.length,10);
 assert.equal(get('buyVolume').textContent,'€3,830.00');
 get('pageNumber').value='3';get('pageJump').handlers.submit({preventDefault(){}});await settle();
 assert(get('pageInfo').textContent.includes('21–30 of 45'));
 assert(get('activities').children[2].children[4].textContent.includes('90.00'),'USD rows use their own conversion rate');
 assert.equal(get('pageNumber').max,5);
 const count=requests.length;
 for(const value of ['0','6','1.5','invalid']){get('pageNumber').value=value;get('pageJump').handlers.submit({preventDefault(){}})}
 await settle();assert.equal(requests.length,count,'Invalid jumps must not send requests');
 get('pageNumber').value='5';get('pageJump').handlers.submit({preventDefault(){}});await settle();
 assert(get('pageInfo').textContent.includes('41–45 of 45'));assert.equal(get('activities').children.length,5);assert(get('nextPage').disabled);
 get('pageSize').value='5';get('pageSize').handlers.change();await settle();
 assert(get('pageInfo').textContent.includes('1–5 of 45'));assert.equal(get('pageNumber').value,1);assert.equal(get('pageNumber').max,9);
 get('nextPage').handlers.click();await settle();assert(get('pageInfo').textContent.includes('6–10 of 45'));
 get('previousPage').handlers.click();await settle();assert(get('pageInfo').textContent.includes('1–5 of 45'));
 assert.equal(requests.filter(u=>u.startsWith('/api/analytics')).length,6,'Each navigation must load exactly once');
 const preview=await vm.runInContext("withdrawalPost('/api/withdrawal/preview',{})",context);
 assert.equal(preview.token,'single-use');assert.equal(checks,2);
 assert.equal(requests.filter(u=>u==='/api/withdrawal/preview').length,1);
 eurOnly=true;await vm.runInContext('loadAnalytics()',context);
 assert.equal(get('buyVolume').textContent,'€3,600.00','EUR string rate must retain monetary totals');
 assert.equal(get('error').textContent,'','EUR-only history must not report mixed currencies');
 priceUnavailable=true;await vm.runInContext('loadAnalytics()',context);
 assert.equal(get('buyVolume').textContent,'€3,600.00','EUR totals need no external conversion rate');
 assert(!get('error').textContent.includes('multiple currencies'));
 eurOnly=false;priceUnavailable=false;invalidUsdRate=true;await vm.runInContext('loadAnalytics()',context);
 assert.equal(get('buyVolume').textContent,'—','Invalid foreign rates must not produce incorrect totals');
 assert.equal(get('error').textContent,'A required currency conversion is unavailable; monetary totals are hidden.');
 assert(html.includes('sup{color:#fff}'),'All superscript references must be white');
 withdrawalUnavailable=true;
 const submissions=requests.filter(u=>u==='/api/withdrawal/preview'||u==='/api/withdrawal/confirm').length;
 await get('manualWithdraw').handlers.click();
 assert(get('withdrawalFeedback').textContent.includes('KRAKEN_WITHDRAW_KEY'),'Missing configuration is displayed inline');
 assert(html.includes('id="withdrawalFeedback" class="export-feedback withdraw-feedback hidden"'),'Uses the export feedback style');
 assert(!html.includes('<dialog'),'Withdrawal feedback must not open a dialog');
 assert.equal(get('manualWithdraw').disabled,false,'Button is available after displaying feedback');
 assert.equal(requests.filter(u=>u==='/api/withdrawal/preview'||u==='/api/withdrawal/confirm').length,submissions,'Missing configuration cannot submit financial actions');
 console.log('Dashboard regressions passed: interval dates, server pages, mixed-currency rows/totals and queued preview polling');
})().catch(e=>{console.error(e);process.exit(1)});
