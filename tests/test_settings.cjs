/* Setup/settings UI behavior using a small DOM fixture; no browser or exchange access. */
const assert=require('node:assert/strict'),vm=require('node:vm'),{execFileSync}=require('node:child_process'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const page=execFileSync('python3',['-c',`import sys,json;sys.path.insert(0,'.');from app.webpage.settings_ui import PAGE;from app.core.configuration import DEFAULTS;print(PAGE.replace('__INITIAL__',json.dumps(dict(settings={**DEFAULTS,'kraken_api_key_configured':True,'kraken_api_secret_configured':True},username='operator',complete=True,passwordChangeRequired=False,createAccount=False))))`],{cwd:root,encoding:'utf8'});
const script=page.match(/<script>([\s\S]*?)<\/script>/)[1];
class Element{
 constructor(tag='div'){this.style={};this.tag=tag;this.value='';this.type='';this.dataset={};this.children=[];this.listeners={};this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';this.options=[{},{}];this.classList={toggle(){},add(){}}}
 append(...children){for(const c of children){c.parentElement=this;this.children.push(c);if(c.id)elements[c.id]=c}}
 replaceChildren(){this.children=[]}
 addEventListener(name,fn){this.listeners[name]=fn}
 setAttribute(name,value){this[name]=value}
 showModal(){this.open=true}
 close(){this.open=false}
}
const elements={};for(const match of page.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){const el=new Element(match[1]);el.id=match[3];el.type=(match[2].match(/type="([^"]+)"/)||[])[1]||'';if(match[2].includes('data-setting'))el.dataset.setting='';elements[el.id]=el}
const requests=[],location={href:''};let reject=false;
const context={document:{documentElement:{dataset:{}},getElementById:id=>elements[id],createElement:tag=>new Element(tag),querySelectorAll:()=>Object.values(elements).filter(el=>'setting'in el.dataset)},location,
 fetch:async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return {ok:!reject,json:async()=>reject?{error:'Connection failed'}:{message:'Connected',saved:true}}}};
vm.runInNewContext(script,context);
function allText(el){return el.textContent+' '+el.children.map(allText).join(' ')}
async function event(id,name='click'){if(id==='confirm'&&!elements.passwordConfirmation.hidden&&!elements.sensitivePassword.value)elements.sensitivePassword.value='current-long-password';return elements[id].listeners[name]({preventDefault(){},submitter:elements[id]})}
(async()=>{
 assert.equal(elements.withdraw_thresholdLabel.textContent,'Automatic withdrawal threshold (EUR)');elements.quote.value='USD';await event('quote','change');assert.equal(elements.withdraw_thresholdLabel.textContent,'Automatic withdrawal threshold (USD)');
 assert(page.includes('<small id="displayCurrencyHint" class="hint">Display currency affects reporting only; purchases use the purchase currency.</small>'));assert(page.includes('aria-describedby="displayCurrencyHint"'));
 assert.equal(elements.kraken_api_key.value,'');assert.equal(elements.kraken_api_key.placeholder,'Saved · leave empty to keep');
 assert.equal(elements.appearance.value,'system');elements.appearance.value='dark';await event('appearance','change');assert.equal(context.document.documentElement.dataset.appearance,'dark');
 assert.equal(elements.timezone.value,'Europe/Berlin');assert.equal(elements.timezone.tag,'select');assert.equal(elements.timezone.children.length,6);
 assert(elements.timezone.children.some(group=>group.children.some(option=>option.value==='Asia/Tokyo')));
 assert.equal(elements.dashboardEditor.hidden,true);assert.equal(elements.dashboardEditor.disabled,true);assert.equal(elements.exchangeEditor.hidden,true);assert.equal(elements.exchangeEditor.disabled,true);
 assert.equal(elements.buy_amountHint.textContent.length>0,true);assert.equal(elements.withdraw_thresholdHint.textContent.length>0,true);assert.equal(elements.withdraw_threshold.parentElement.parentElement,elements.purchaseFields);
 for(const key of ['buy_amount','deposit_day','startup_delay_seconds','check_interval_seconds','order_status_check_seconds','withdraw_threshold'])assert.equal(elements[key]['aria-describedby'],key+'Hint');
 assert(!page.includes('<h2>Purchases</h2>'));assert(page.includes('<h2>Bot Configuration</h2>'));assert(!page.includes('<h2>Withdrawal settings</h2>'));
 assert.equal(elements.settingsForm.hidden,false);assert.equal(elements.accountPanel.hidden,true);
 elements.exchange.value='BITVAVO';await event('exchange','change');assert.equal(elements.exchangeEditor.hidden,true);assert.equal(elements.exchangeSwitchHint.hidden,false);assert.match(elements.exchangeSwitchHint.textContent,/Bitvavo/);assert.equal(elements.bitvavo_api_key.required,true);const beforeMissingKeys=requests.length;await event('testConnection');await event('settingsForm','submit');assert.equal(requests.length,beforeMissingKeys);assert.match(elements.connectionStatus.textContent,/API key and API secret/);assert.equal(elements.quote.value,'EUR');assert.equal(elements.withdraw_thresholdLabel.textContent,'Automatic withdrawal threshold (EUR)');assert.equal(elements.quote.options[1].disabled,true);assert.equal(elements.kraken_api_key.parentElement.hidden,true);
 await event('editExchangeCredentials');assert.equal(location.href,'/settings?editor=exchange&exchange=BITVAVO');vm.runInContext('setExchangeEditor(true)',context);assert.equal(elements.exchangeEditor.hidden,false);assert.match(elements.exchangeEditorTitle.textContent,/Bitvavo/);assert.match(elements.exchangeCredentialHint.textContent,/not reused/);assert.equal(elements.editExchangeCredentials['aria-expanded'],'true');
 elements.bitvavo_api_key.value='fictional-key';elements.bitvavo_api_secret.value='fictional-secret';
 const testing=event('testConnection');assert.equal(elements.connectionStatus.textContent,'Testing connection…');assert.equal(elements.testConnection.disabled,true);await testing;assert.equal(requests.at(-1).url,'/api/settings/test');assert.equal(requests.at(-1).body.bitvavo_api_key,'fictional-key');assert.equal(elements.connectionStatus.textContent,'Connected');
 const count=requests.length;elements.display_currency.value='USD';elements.timezone.value='Asia/Tokyo';elements.withdrawals_enabled.checked=true;elements.withdraw_threshold.value='1200';
 await event('settingsForm','submit');assert.equal(elements.review.open,true);assert.equal(requests.length,count,'Review must not save yet');
 const text=allText(elements.reviewValues);assert.match(text+allText(elements.unchangedReviewValues),/LIVE|Dry run/);assert.match(text,/1200 EUR/);assert.match(text,/1000 EUR/);assert.match(text,/Before/);assert.match(text,/After/);assert.equal(elements.unchangedReview.hidden,false);assert.match(allText(elements.unchangedReviewValues),/BTC per purchase/);assert(!text.includes('fictional-key'));assert(!text.includes('fictional-secret'));assert.match(text,/Will be updated \(hidden\)/);
 await event('cancel');assert.equal(elements.review.open,false);await event('confirm');assert.equal(requests.filter(r=>r.url==='/api/settings').length,0,'Cancelled review must not submit');assert.equal(requests.at(-1).body.event,'settings_save_cancelled');
 await event('settingsForm','submit');await event('confirm');assert.equal(requests.at(-1).url,'/api/settings');assert.equal(requests.at(-1).body.settings.withdrawals_enabled,true);assert.equal(requests.at(-1).body.settings.display_currency,'USD');assert.equal(requests.at(-1).body.settings.timezone,'Asia/Tokyo');assert.equal(requests.at(-1).body.settings.appearance,'dark');
 await event('editDashboardCredentials');assert.equal(location.href,'/settings?editor=dashboard');vm.runInContext('setDashboardEditor(true)',context);assert.equal(elements.dashboardEditor.hidden,false);
 elements.password.value='new-long-password';elements.passwordConfirm.value='different';const before=requests.length;await event('settingsForm','submit');assert.equal(requests.length,before);assert.match(elements.status.textContent,/do not match/);
 vm.runInContext('setDashboardEditor(false)',context);assert.equal(elements.dashboardEditor.hidden,true);assert.equal(elements.password.value,'');
 vm.runInContext('setExchangeEditor(false)',context);assert.equal(elements.exchangeEditor.hidden,true);assert.equal(elements.bitvavo_api_key.value,'');
 const afterClosing=requests.length;await event('settingsForm','submit');assert.equal(requests.length,afterClosing,'A closed editor cannot silently reuse saved keys after switching');elements.exchange.value='KRAKEN';await event('exchange','change');assert.equal(elements.exchangeSwitchHint.hidden,true);assert.equal(elements.kraken_api_key.required,false);
 await event('settingsForm','submit');await event('confirm');const closedPayload=requests.at(-1).body;assert(!('username'in closedPayload));assert(!('password'in closedPayload));assert(!('bitvavo_api_key'in closedPayload.settings));assert(!('withdraw_address'in closedPayload.settings));
 vm.runInContext('initial.passwordChangeRequired=true',context);const beforeUpgrade=requests.length;await event('settingsForm','submit');assert.equal(requests.length,beforeUpgrade);assert.match(elements.status.textContent,/Open Change login credentials/);vm.runInContext('initial.passwordChangeRequired=false',context);
 reject=true;await event('testConnection');assert.equal(elements.testConnection.disabled,false);assert.equal(elements.connectionStatus.textContent,'Connection failed');
 vm.runInContext('pending={settings:{buy_amount:"0.000"}};renderReview()',context);assert.equal(elements.reviewValues.children.length,0);assert.match(elements.reviewDescription.textContent,/No changes/);
 vm.runInContext('pending={settings:{},username:"renamed",password:"PRIVATE-DASHBOARD-PASSWORD"};renderReview()',context);const credentialReview=allText(elements.reviewValues);assert.match(credentialReview,/operator/);assert.match(credentialReview,/renamed/);assert.match(credentialReview,/Will be changed \(hidden\)/);assert(!credentialReview.includes('PRIVATE-DASHBOARD-PASSWORD'));
 vm.runInContext("initial.editor='dashboard';setDashboardEditor(true)",context);elements.password.value='new-long-password';elements.passwordConfirm.value='new-long-password';elements.current_password.value='current-long-password';reject=false;await event('settingsForm','submit');await event('confirm');assert.deepEqual(requests.at(-1).body.settings,{});assert.equal(requests.at(-1).body.password,'new-long-password');
 vm.runInContext("initial.editor='exchange';setDashboardEditor(false);setExchangeEditor(true)",context);elements.kraken_api_key.value='updated-private-key';await event('settingsForm','submit');await event('confirm');const scoped=requests.at(-1).body;assert.equal(scoped.settings.kraken_api_key,'updated-private-key');assert(!('buy_amount' in scoped.settings));assert(!('display_currency' in scoped.settings));assert(!('username' in scoped));
 assert(!page.includes('daylight saving changes are automatic'));
 vm.runInContext("pending={settings:{live:true}};initial.settings.live=false;renderReview()",context);elements.sensitivePassword.value='';const beforePassword=requests.length;await elements.confirm.listeners.click();assert.equal(requests.length,beforePassword,'Sensitive settings cannot be submitted without a password');assert.match(elements.passwordConfirmationError.textContent,/current login password/);elements.sensitivePassword.value='private-confirmation-password';await elements.confirm.listeners.click();assert.equal(requests.at(-1).body.current_password,'private-confirmation-password');assert.equal(elements.sensitivePassword.value,'');
 console.log('Settings UI passed: credentials, exchange fields, read-only test, review/cancel/save and validation');
})().catch(error=>{console.error(error);process.exitCode=1});

// Step two uses the actual server-rendered page without Login Credentials.
const setupPage=execFileSync('python3',['-c',`import sys;from types import SimpleNamespace;sys.path.insert(0,'.');from app.core.configuration import DEFAULTS;from app.webpage.settings_ui import settings_page;print(settings_page(SimpleNamespace(public=lambda:dict(settings={**DEFAULTS,'timezone':'Pacific/Chatham'},username='operator',complete=False,passwordChangeRequired=False))))`],{cwd:root,encoding:'utf8'});
const setupElements={};
class SetupElement extends Element{append(...children){for(const child of children){child.parentElement=this;this.children.push(child);if(child.id)setupElements[child.id]=child}}}
for(const match of setupPage.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){const el=new SetupElement(match[1]);el.id=match[3];el.type=(match[2].match(/type="([^"]+)"/)||[])[1]||'';if(match[2].includes('data-setting'))el.dataset.setting='';setupElements[el.id]=el}
const setupRequests=[];vm.runInNewContext(setupPage.match(/<script>([\s\S]*?)<\/script>/)[1],{document:{getElementById:id=>setupElements[id],createElement:tag=>new SetupElement(tag),querySelectorAll:()=>Object.values(setupElements).filter(el=>'setting'in el.dataset)},location:{href:''},fetch:async(url,options)=>{setupRequests.push({url,body:JSON.parse(options.body)});return {ok:true,json:async()=>({})}}});
assert.equal(setupElements.timezone.value,'Pacific/Chatham');assert.equal(setupElements.timezone.children.at(-1).value,'Pacific/Chatham');
assert.equal(setupElements.dashboardCredentials,undefined);assert.equal(setupElements.username,undefined);
setupElements.settingsForm.listeners.submit({preventDefault(){}});assert.equal(setupElements.review.open,true);assert.match(setupElements.reviewDescription.textContent,/New configuration/);assert(!allText(setupElements.reviewValues).includes('Before'));
assert.equal(setupRequests.length,0,'Step two reviews settings without editing account credentials');
console.log('First-run step two passed without credential fields');

// The dashboard owns a native modal and accepts messages only from its settings frame.
const dashboard=execFileSync('python3',['-c',`import ast,sys;from pathlib import Path;sys.path.insert(0,'.');from app.webpage.scroll_ui import SCROLL_CSS,SCROLL_SCRIPT;from app.webpage.design_system import DESIGN_CSS;m=ast.parse(Path('app/webpage/web_ui.py').read_text());page=next(ast.literal_eval(n.value) for n in m.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HTML' for t in n.targets) and isinstance(n.value,ast.Constant));print(page.replace('__SCROLL_CSS__',SCROLL_CSS).replace('__SCROLL_SCRIPT__',SCROLL_SCRIPT).replace('__DESIGN_CSS__',DESIGN_CSS))`],{cwd:root,encoding:'utf8'});
const modalScript=dashboard.match(/<script data-ui-settings>([\s\S]*?)<\/script>/)[1];
const modalElements=Object.fromEntries(['settingsDialog','settingsFrame','openSettings','closeSettings'].map(id=>[id,new Element()]));
const frameWindow={},events={};modalElements.settingsFrame.contentWindow=frameWindow;let reloads=0;
const modalWindow={location:{origin:'http://dashboard.test',href:'',reload(){reloads++}},addEventListener(name,fn){events[name]=fn}};
vm.runInNewContext(modalScript,{fetch:async()=>({ok:true}),document:{getElementById:id=>modalElements[id]},window:modalWindow});
modalElements.openSettings.listeners.click();assert.equal(modalElements.settingsDialog.open,true);assert.equal(modalElements.settingsFrame.src,'/settings?embedded=1');
modalElements.closeSettings.listeners.click();assert.equal(modalElements.settingsDialog.open,false);
modalElements.settingsDialog.listeners.close();assert.equal(modalElements.settingsFrame.src,'about:blank','Closing must clear sensitive form fields');
events.message({origin:'http://untrusted.test',source:frameWindow,data:{type:'dca-settings-saved'}});assert.equal(reloads,0);
events.message({origin:'http://dashboard.test',source:{},data:{type:'dca-settings-saved'}});assert.equal(reloads,0);
events.message({origin:'http://dashboard.test',source:frameWindow,data:{type:'dca-settings-saved'}});assert.equal(reloads,1);
events.message({origin:'http://dashboard.test',source:frameWindow,data:{type:'dca-settings-auth-required'}});assert.equal(modalWindow.location.href,'/');
console.log('Settings modal passed: open/close, secret cleanup, origin/source checks and save/auth handling');

// Settings share dashboard scroll behavior, including resize and disappearing edge hints.
const scrollSnippet=page.match(/<script data-ui-scroll-areas>([\s\S]*?)<\/script>/)[1];
const scrollClasses=new Set(),scrollEvents={};let resizeScroll,finishScroll;
const area={clientWidth:280,scrollWidth:760,scrollLeft:0,firstElementChild:{},classList:{toggle(name,on){on?scrollClasses.add(name):scrollClasses.delete(name)},add(name){scrollClasses.add(name)},remove(name){scrollClasses.delete(name)}},addEventListener(name,fn){scrollEvents[name]=fn}};
vm.runInNewContext(scrollSnippet,{document:{querySelectorAll:selector=>selector==='.horizontal-scroll'?[area]:[]},ResizeObserver:class{constructor(fn){resizeScroll=fn}observe(){}},WeakMap,setTimeout:fn=>{finishScroll=fn;return 1},clearTimeout(){}});
assert(scrollClasses.has('has-overflow'));assert(scrollClasses.has('has-more-right'));
area.scrollLeft=480;scrollEvents.scroll();assert(!scrollClasses.has('has-more-right'));assert(scrollClasses.has('is-scrolling'));finishScroll();assert(!scrollClasses.has('is-scrolling'));
area.clientWidth=900;resizeScroll();assert(!scrollClasses.has('has-overflow'));
assert(page.includes('grid-template-rows:repeat(2,auto);grid-auto-flow:column'));
assert.equal((page.match(/class="horizontal-scroll"/g)||[]).length,7);
console.log('Settings scrolling passed: two-row layout, overflow, edge fade, resize and scroll feedback');

// All native dialogs lock their own page and restore its exact scroll position.
const lockScript=page.match(/<script data-ui-dialog-lock>([\s\S]*?)<\/script>/)[1];
class InlineStyle{
 constructor(text){this.cssText=text}
 get cssText(){return this._text}
 set cssText(text){for(const key of Object.keys(this))delete this[key];this._text=text;for(const item of text.split(';')){const [key,value]=item.split(':');if(key&&value)this[key.trim().replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())]=value.trim()}}
}
const background={style:new InlineStyle('color:red')},rootElement={clientWidth:980,style:new InlineStyle('scroll-behavior:smooth')};
const modalA={open:false},modalB={open:false},restored=[];let updateModal;
vm.runInNewContext(lockScript,{document:{body:background,documentElement:rootElement,querySelectorAll:()=>[modalA,modalB]},window:{innerWidth:1000,scrollX:30,scrollY:420,getComputedStyle:()=>({paddingRight:'5px'}),scrollTo:(x,y)=>restored.push([x,y])},MutationObserver:class{constructor(fn){updateModal=fn}observe(){}}});
modalA.open=true;updateModal();assert.equal(background.style.position,'fixed');assert.equal(background.style.top,'-420px');assert.equal(background.style.paddingRight,'25px');assert.equal(rootElement.style.overflow,'hidden');
modalB.open=true;updateModal();modalA.open=false;updateModal();assert.equal(restored.length,0,'Another open dialog keeps the page locked');
modalB.open=false;updateModal();assert.deepEqual(restored,[[30,420]]);assert.equal(background.style.cssText,'color:red');assert.equal(background.style.position,undefined);assert.equal(rootElement.style.scrollBehavior,'smooth');
console.log('Dialog scroll lock passed: nested dialogs, scrollbar compensation and position/style restoration');
