// Run with node tests/test_dashboard.cjs; stdlib only, no browser or exchange IO.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const html=JSON.parse(execFileSync(process.env.PYTHON||'python3',['-c',`import ast,json
from pathlib import Path
import sys;sys.path.insert(0,'.');from app.webpage.scroll_ui import SCROLL_CSS,SCROLL_SCRIPT
m=ast.parse(Path('app/webpage/web_ui.py').read_text());page=next(ast.literal_eval(n.value) for n in m.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HTML' for t in n.targets) and isinstance(n.value,ast.Constant));print(json.dumps(page.replace('__SCROLL_CSS__',SCROLL_CSS).replace('__SCROLL_SCRIPT__',SCROLL_SCRIPT)))`],{encoding:'utf8'}));
const script=html.split('<script>')[1].split('</script>')[0];
class Element{
 constructor(){this.value='';this.children=[];this.handlers={};this.textContent='';this.disabled=false;this.classList={toggle(){},add(){},remove(){}}}
 appendChild(e){this.children.push(e)} append(...e){this.children.push(...e)} replaceChildren(...e){this.children=e}
 showModal(){this.open=true} close(value=''){this.returnValue=value;this.open=false;this.handlers.close?.()}
 set innerHTML(v){this.children=[]} addEventListener(event,handler){this.handlers[event]=handler}
}
const elements={},get=id=>{assert(!["balance","balanceValue"].includes(id),"Removed portfolio element must not be accessed");return elements[id]??=new Element()};get('pageSize').value=html.match(/<option selected>(\d+)<\/option>/)[1];
const records=Array.from({length:45},(_,i)=>({activityId:String(i),datetime:'2026-10-22T12:00:00Z',type:'Buy',identifier:'BTC',shares:'.001',currency:i<22?'EUR':'USD',price:i<22?'80000':'100000',amount:i<22?'80':'100',fee:i<22?'.2':'.25'}));
const stored=new Map([['bitcoinDcaMonthlyDeposit','999']]);let expiryCallback;let configuredMonthlyDeposit='',forecastSaveFails=false;const forecastSaves=[];
const requests=[];let reportFails=false,scheduleFails=false,checks=0,eurOnly=false,priceUnavailable=false,invalidUsdRate=false,withdrawalUnavailable=false,configuredDisplayCurrency="EUR",withdrawalExchange="KRAKEN",withdrawalTarget="Example wallet",withdrawalNetwork="BTC";
const response=data=>({ok:true,json:async()=>data});
const context={document:{getElementById:get,createElement:()=>new Element()},localStorage:{getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,value)},Intl,Date,URL,console,alert(){throw Error('Withdrawal must not use a browser alert')},confirm:()=>false,setInterval(){},setTimeout(fn,ms){if(ms===120000){expiryCallback=fn;return 1}queueMicrotask(fn)},clearTimeout(){},window:{location:{reload(){throw Error('Unexpected reload')}}},fetch:async(url,options)=>{
 requests.push(url);const parsed=new URL(url,'http://example.test');
 if(parsed.pathname==='/api/schedule'&&scheduleFails)throw Error('Schedule offline');
 if(parsed.pathname==='/api/settings/forecast'){const data=JSON.parse(options.body);forecastSaves.push(data);if(forecastSaveFails)return {ok:false,json:async()=>({error:'Forecast save failed'})};configuredMonthlyDeposit=data.monthlyDeposit;return response({monthlyDeposit:configuredMonthlyDeposit})}
 if(parsed.pathname==='/api/schedule')return response({expectedMonthlyDeposit:configuredMonthlyDeposit,quote:'EUR',exchange:'KRAKEN',quoteBalance:'72.7719',balanceUpdatedAt:'2026-10-29T12:00:00Z',nextPurchaseAt:new Date(Date.now()+3600000).toISOString(),serverTime:new Date().toISOString(),timezone:'Europe/Berlin'});
 if(parsed.pathname==='/api/withdrawals')return response([{timestamp:Date.parse('2026-10-20T12:00:00Z')},{timestamp:Date.parse('2026-10-23T12:00:00Z')}]);
 if(parsed.pathname==='/api/withdrawal/config')return response({enabled:!withdrawalUnavailable,exchange:withdrawalExchange,target:withdrawalTarget,network:withdrawalNetwork,destination:'fictional wallet',reason:withdrawalUnavailable?'Manual withdrawal is unavailable: set KRAKEN_WITHDRAW_KEY to your saved Bitcoin withdrawal method in .env.':''});
 if(parsed.pathname==='/api/market-price'&&parsed.searchParams.get('currency')==='USD')return response({price:'88000',conversionRate:'1.1',conversionRates:{EUR:'1.1',USD:'1'},updatedAt:Date.now()/1000});
 if(parsed.pathname==='/api/market-price')return priceUnavailable?{ok:false,json:async()=>({error:'offline'})}:response({price:'80000',conversionRate:'1',conversionRates:{EUR:'1',USD:invalidUsdRate?'invalid':'0.9'},updatedAt:Date.now()/1000});
 if(parsed.pathname==='/api/analytics'){
  if(reportFails)throw Error('Report offline');
  const limit=Number(parsed.searchParams.get('limit'))||10,pages=Math.ceil(records.length/limit);
  const page=Math.max(1,Math.min(pages,Number(parsed.searchParams.get('page'))||1));
  return response({quote:'EUR',exchange:'KRAKEN',configuration:{displayCurrency:configuredDisplayCurrency,liveTrading:true,buyAmount:0,depositDay:1,withdrawalsEnabled:false,withdrawalThreshold:1000},snapshot:{price:80000,balance:'.045',updatedAt:new Date().toISOString()},summary:{buyCount:45,boughtBtc:'.045',withdrawnBtc:0,withdrawalCount:0,pendingWithdrawalCount:0,netBtc:'.045'},summaryByCurrency:eurOnly?{EUR:{buyVolume:3600,tradeFees:9,withdrawalFees:0}}:{EUR:{buyVolume:1760,tradeFees:4.4,withdrawalFees:0},USD:{buyVolume:2300,tradeFees:5.75,withdrawalFees:0}},activities:records.slice((page-1)*limit,page*limit),pagination:{page,pageSize:limit,total:45,pages}});
 }
 if(parsed.pathname==='/api/withdrawal/preview')return response({jobId:'fictional-job'});
 if(parsed.pathname==='/api/withdrawal/jobs/fictional-job')return response(++checks===1?{state:'queued'}:{state:'completed',result:{token:'single-use'}});
 throw Error('Unexpected URL '+url);
}};
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(setImmediate)};
(async()=>{
 vm.createContext(context);vm.runInContext(script,context);await settle();
 vm.runInContext('renderPurchaseAmount(".001",".002",{quote:"EUR",baseAmount:".001",factor:"2",effectiveFactor:"2",mean:"80000",standardDeviation:"5000",zScore:"-2.1",status:"active"},true,"1")',context);
 assert.match(get('adaptiveDca').textContent,/Enabled.*1.0/);assert.match(get('adaptiveDcaNote').textContent,/80,000/);assert.match(get('adaptiveDcaNote').textContent,/5,000/);assert.match(get('buyAmount').textContent,/0.002/);
 assert(html.indexOf('id="adaptiveDca"')<html.indexOf('id="buyAmount"'));

 assert.equal(get('monthlyDeposit').value,'','No value is imported from browser storage');
 get('monthlyDeposit').value='250';get('monthlyDeposit').handlers.input();await get('monthlyDeposit').handlers.change();assert.equal(configuredMonthlyDeposit,'250');assert.equal(forecastSaves.at(-1).monthlyDeposit,'250');assert.equal(stored.get('bitcoinDcaMonthlyDeposit'),'999','Browser storage is not read or rewritten');
 configuredMonthlyDeposit='350';await vm.runInContext('refreshPurchaseSchedule()',context);assert.equal(get('monthlyDeposit').value,'350','Other browsers receive the persisted server value');
 forecastSaveFails=true;get('monthlyDeposit').value='400';get('monthlyDeposit').handlers.input();await get('monthlyDeposit').handlers.change();assert.equal(configuredMonthlyDeposit,'350');assert.equal(get('monthlyDeposit').value,'400');assert.equal(get('monthlyDeposit').disabled,false);assert.match(get('scheduleFeedback').textContent,/Forecast save failed/);forecastSaveFails=false;await get('monthlyDeposit').handlers.change();
 assert(get('start').value&&get('end').value,'Both dates must initialize for the selected interval');
 assert.equal(get('end').value,vm.runInContext('localInput(Date.now())',context),'Each page load uses the current end time');
 assert.equal(get('interval').value,'','A range ending now must not claim to be a closed withdrawal interval');
 assert.equal(get('quoteBalance').textContent,'€72.77');
 assert.equal(get('quoteBalanceLabel').textContent,'Available EUR balance on exchange');
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
 assert(get('pageInfo').textContent.includes('41–45 of 45'));assert.equal(get('activities').children.length,10);assert.equal(get('activities').children.filter(row=>row.className==='activity-placeholder').length,5);assert(get('nextPage').disabled);
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
 assert(!html.includes('<sup'),'Superscript references are no longer displayed');
 withdrawalUnavailable=true;
 const submissions=requests.filter(u=>u==='/api/withdrawal/preview'||u==='/api/withdrawal/confirm').length;
 await get('manualWithdraw').handlers.click();
 assert(get('withdrawalFeedback').textContent.includes('KRAKEN_WITHDRAW_KEY'),'Missing configuration is displayed inline');
 assert(html.includes('id="withdrawalFeedback" class="export-feedback withdraw-feedback hidden"'),'Uses the export feedback style');
 assert(html.includes('<dialog'),'Financial confirmation uses a styled dialog');
 assert(!/\b(confirm|alert|prompt)\(/.test(script),'No browser dialogs remain');
 assert.equal(get('manualWithdraw').disabled,false,'Button is available after displaying feedback');
 assert.equal(requests.filter(u=>u==='/api/withdrawal/preview'||u==='/api/withdrawal/confirm').length,submissions,'Missing configuration cannot submit financial actions');

 const lastBalance=get('quoteBalance').textContent;
 scheduleFails=true;await vm.runInContext('refreshPurchaseSchedule()',context);
 assert.equal(get('quoteBalance').textContent,lastBalance);
 assert(get('scheduleFeedback').textContent.includes('Previous values'));
 scheduleFails=false;
 const lastRows=get('activities').children;
 reportFails=true;await vm.runInContext('loadAnalytics()',context);
 assert.equal(get('activities').children,lastRows,'Failed refresh preserves existing rows');
 assert.equal(get('error').textContent,'Report offline');reportFails=false;
 vm.runInContext('activityRows=[];activityTotal=0;activityPages=1;currentPage=1;renderActivities()',context);
 assert.equal(get('activities').children.length,Number(get('pageSize').value),'Empty periods retain the selected row count');
 assert.equal(get('activities').children.filter(row=>row.className==='activity-placeholder').length,Number(get('pageSize').value)-1);
 assert(get('previousPage').disabled&&get('nextPage').disabled);
 assert.equal(vm.runInContext('activityMoney(null,1,"EUR")',context),'—');
 assert.equal(vm.runInContext('activityMoney("",1,"EUR")',context),'—');
 assert.equal(vm.runInContext('activityMoney("0",1,"EUR")',context),'€0.00');
 assert.equal(vm.runInContext('activityBtc(undefined)',context),'—');
 get('pageSize').value='20';get('start').value='2026-10-01T00:00';get('end').value='2026-10-29T12:00';get('interval').value='';
 vm.runInContext('saveReportView()',context);
 get('pageSize').value='10';get('start').value='';get('end').value='';vm.runInContext('restoreReportView()',context);
 assert.equal(get('pageSize').value,'20');assert.equal(get('start').value,'2026-10-01T00:00');
 assert.equal(get('end').value,vm.runInContext('localInput(Date.now())',context),'Saved end dates must not override the current time');
 stored.set('bitcoinDcaReportView',JSON.stringify({interval:'1,2',start:'2026-10-01T00:00',end:'2026-10-02T00:00',pageSize:'20'}));
 vm.runInContext('restoreReportView()',context);
 assert.equal(get('interval').value,'');assert.equal(get('end').value,vm.runInContext('localInput(Date.now())',context));

 let decision=vm.runInContext('confirmWithdrawal({destination:"Example wallet",availableBtc:".01",feeBtc:".0001",netBtc:".0099"})',context);
 assert(get('confirmationDialog').open);assert.equal(get('confirmGross').textContent,'0.01 BTC');assert.equal(get('confirmFee').textContent,'0.0001 BTC');assert.equal(get('confirmNet').textContent,'0.0099 BTC');
 get('confirmationDialog').close('cancel');assert.equal(await decision,false);
 decision=vm.runInContext('confirmWithdrawal({destination:"Example wallet",availableBtc:".01",feeBtc:null,netBtc:null})',context);
 assert.equal(get('confirmFee').textContent,'Determined by the exchange');
 expiryCallback();assert.equal(await decision,false,'Expired previews cannot confirm');
 decision=vm.runInContext('confirmWithdrawal({destination:"Example wallet",availableBtc:".01"})',context);
 get('confirmationDialog').close('confirm');assert.equal(await decision,true);
 assert.equal(requests.filter(u=>u==='/api/withdrawal/confirm').length,0,'Dialog tests never submit a withdrawal');

 const originalFetch=context.fetch;let release;
 let hold=true;context.fetch=(url,options)=>{
  if(hold&&url.startsWith('/api/analytics')){hold=false;return new Promise(resolve=>{release=()=>originalFetch(url,options).then(resolve)})}
  return originalFetch(url,options);
 };
 invalidUsdRate=false;
 const rowsBefore=get('activities').children;
 const earlier=vm.runInContext('currentPage=1;loadAnalytics()',context);
 assert.equal(get('activities').children,rowsBefore,'Loading keeps current rows');
 assert.equal(get('reportFeedback').textContent,'Updating report…');
 await vm.runInContext('currentPage=2;loadAnalytics()',context);
 const latestRows=get('activities').children;release();await earlier;
 assert.equal(get('activities').children,latestRows,'A late response cannot overwrite the newer report');
 context.fetch=originalFetch;
 withdrawalUnavailable=false;
 const cancelAction=get('manualWithdraw').handlers.click();await settle();
 assert(get('confirmationDialog').open,'Enabled manual withdrawal opens its confirmation dialog');
 get('confirmationDialog').close('cancel');await cancelAction;
 assert(!get('withdrawMessage').textContent.includes('Withdrawal not submitted'));
 assert.equal(get('withdrawalFeedback').textContent,'');
 assert.equal(get('manualWithdraw').disabled,false);
 assert.equal(requests.filter(u=>u==='/api/withdrawal/confirm').length,0,'Cancel never submits a withdrawal');
 // Main panel state is restored and saved independently from report data.
 const panel={open:true,getAttribute:()=>"configurationHeading",addEventListener(event,fn){this.toggle=fn}};
 stored.set('bitcoinDcaPanels',JSON.stringify({configurationHeading:false}));
 const savedDocument=context.document;context.document={querySelectorAll:()=>[panel]};
 vm.runInContext(html.split('<script data-ui-view>')[1].split('</script>')[0],context);
 assert.equal(panel.open,false);panel.open=true;panel.toggle();
 assert.equal(JSON.parse(stored.get('bitcoinDcaPanels')).configurationHeading,true);
 context.document=savedDocument;

 const scrollClasses=new Set(),scrollEvents={};let resizeScroll;
 const scrollArea={clientWidth:300,scrollWidth:900,scrollLeft:0,firstElementChild:{},
  classList:{toggle(name,on){on?scrollClasses.add(name):scrollClasses.delete(name)},add(name){scrollClasses.add(name)},remove(name){scrollClasses.delete(name)}},
  addEventListener(name,fn){scrollEvents[name]=fn}};
 const previousDocument=context.document;
 context.document={querySelectorAll:selector=>selector==='.horizontal-scroll'?[scrollArea]:[]};
 context.ResizeObserver=class{constructor(fn){resizeScroll=fn}observe(){}};
 vm.runInContext(html.split('<script data-ui-scroll-areas>')[1].split('</script>')[0],context);
 assert(scrollClasses.has('has-more-right'),'Overflow shows the right edge hint');
 scrollArea.scrollLeft=600;scrollEvents.scroll();
 assert(!scrollClasses.has('has-more-right'),'Reaching the end removes the hint');
 scrollArea.scrollLeft=100;scrollEvents.scroll();assert(scrollClasses.has('has-more-right'));
 scrollArea.clientWidth=1000;resizeScroll();
 assert(!scrollClasses.has('has-more-right'));assert(!scrollClasses.has('has-overflow'));
 context.document=previousDocument;

 // Display currency comes from saved Settings, never a stale browser preference.
 assert(!html.includes('id="displayCurrency"'),'Currency selector moved out of the dashboard header');
 stored.set('bitcoinDcaDisplayCurrency','EUR');configuredDisplayCurrency='USD';invalidUsdRate=false;
 await vm.runInContext('loadAnalytics()',context);
 assert.equal(vm.runInContext('displayCurrency',context),'USD');
 assert(requests.some(u=>u.startsWith('/api/market-price?currency=USD')));
 assert(get('spot').textContent.includes('$'));
 assert.equal(vm.runInContext('activityStatus({type:"Buy"}).label',context),'Confirmed ✓');
 assert.equal(vm.runInContext('activityStatus({type:"Buy",simulated:true}).label',context),'Simulated');
 assert.equal(vm.runInContext('activityStatus({type:"TransferOut",withdrawalStatus:"completed"}).label',context),'Confirmed ✓');
 assert.equal(vm.runInContext('activityStatus({type:"TransferOut",withdrawalStatus:"submitted"}).label',context),'Pending');
 assert.equal(vm.runInContext('activityStatus({type:"TransferOut",withdrawalStatus:"failed"}).label',context),'Failed');
 assert.equal(vm.runInContext('activityStatus({type:"TransferOut"}).label',context),'Unverified');
 assert.equal(get('activities').children[0].children.length,7);
 assert.equal(get('activities').children[0].children[6].textContent,'Confirmed ✓');

 await vm.runInContext('loadWithdrawalConfiguration()',context);
 assert.equal(get('automaticWithdrawalDestination').textContent,get('withdrawalDestination').textContent);
 assert.match(get('automaticWithdrawalDestination').textContent,/Kraken under “Example wallet”/);
 withdrawalExchange='BITVAVO';withdrawalTarget='bc1-fictional-target';withdrawalNetwork='BTC';await vm.runInContext('loadWithdrawalConfiguration()',context);
 assert.match(get('automaticWithdrawalDestination').textContent,/bc1-fictional-target via BTC on Bitvavo/);
 withdrawalTarget='';await vm.runInContext('loadWithdrawalConfiguration()',context);
 assert.match(get('automaticWithdrawalDestination').textContent,/No withdrawal target is configured/);
 console.log('Dashboard regressions passed: interval dates, server pages, mixed-currency rows/totals and queued preview polling');
})().catch(e=>{console.error(e);process.exit(1)});
