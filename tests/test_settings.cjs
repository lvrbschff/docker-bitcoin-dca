/* Setup/settings UI behavior using a small DOM fixture; no browser or exchange access. */
const assert=require('node:assert/strict'),vm=require('node:vm'),{execFileSync}=require('node:child_process'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const page=execFileSync('python3',['-c',`import sys,json;sys.path.insert(0,'app');from settings_ui import PAGE;from configuration import DEFAULTS;print(PAGE.replace('__INITIAL__',json.dumps(dict(settings={**DEFAULTS,'kraken_api_key_configured':True,'kraken_api_secret_configured':True},username='operator',complete=True,passwordChangeRequired=False,createAccount=False))))`],{cwd:root,encoding:'utf8'});
const script=page.match(/<script>([\s\S]*?)<\/script>/)[1];
class Element{
 constructor(tag='div'){this.tag=tag;this.value='';this.type='';this.dataset={};this.children=[];this.listeners={};this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';this.options=[{},{}];this.classList={toggle(){}}}
 append(...children){for(const c of children){c.parentElement=this;this.children.push(c);if(c.id)elements[c.id]=c}}
 replaceChildren(){this.children=[]}
 addEventListener(name,fn){this.listeners[name]=fn}
 setAttribute(name,value){this[name]=value}
 showModal(){this.open=true}
 close(){this.open=false}
}
const elements={};for(const match of page.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){const el=new Element(match[1]);el.id=match[3];el.type=(match[2].match(/type="([^"]+)"/)||[])[1]||'';if(match[2].includes('data-setting'))el.dataset.setting='';elements[el.id]=el}
const requests=[],location={href:''};let reject=false;
const context={document:{getElementById:id=>elements[id],createElement:tag=>new Element(tag),querySelectorAll:()=>Object.values(elements).filter(el=>'setting'in el.dataset)},location,
 fetch:async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return {ok:!reject,json:async()=>reject?{error:'Connection failed'}:{message:'Connected',saved:true}}}};
vm.runInNewContext(script,context);
async function event(id,name='click'){return elements[id].listeners[name]({preventDefault(){},submitter:elements[id]})}
(async()=>{
 assert.equal(elements.kraken_api_key.value,'');assert.equal(elements.kraken_api_key.placeholder,'Saved · leave empty to keep');
 assert.equal(elements.timezone.value,'Europe/Berlin');assert.equal(elements.timezone.tag,'select');assert.equal(elements.timezone.children.length,6);
 assert(elements.timezone.children.some(group=>group.children.some(option=>option.value==='Asia/Tokyo')));
 assert.equal(elements.dashboardEditor.hidden,true);assert.equal(elements.dashboardEditor.disabled,true);assert.equal(elements.exchangeEditor.hidden,true);assert.equal(elements.exchangeEditor.disabled,true);
 assert.equal(elements.buy_amountHint.textContent.length>0,true);assert.equal(elements.withdraw_thresholdHint.textContent.length>0,true);assert.equal(elements.withdraw_threshold.parentElement.parentElement,elements.purchaseFields);
 for(const key of ['buy_amount','deposit_day','startup_delay_seconds','check_interval_seconds','order_status_check_seconds','withdraw_threshold'])assert.equal(elements[key]['aria-describedby'],key+'Hint');
 assert(!page.includes('<h2>Purchases</h2>'));assert(page.includes('<h2>Bot Configuration</h2>'));assert(!page.includes('<h2>Withdrawal settings</h2>'));
 assert.equal(elements.settingsForm.hidden,false);assert.equal(elements.accountPanel.hidden,true);
 elements.exchange.value='BITVAVO';await event('exchange','change');assert.equal(elements.quote.value,'EUR');assert.equal(elements.quote.options[1].disabled,true);assert.equal(elements.kraken_api_key.parentElement.hidden,true);
 await event('editExchangeCredentials');assert.equal(elements.exchangeEditor.hidden,false);assert.equal(elements.editExchangeCredentials['aria-expanded'],'true');
 elements.bitvavo_api_key.value='fictional-key';elements.bitvavo_api_secret.value='fictional-secret';
 const testing=event('testConnection');assert.equal(elements.connectionStatus.textContent,'Testing connection…');assert.equal(elements.testConnection.disabled,true);await testing;assert.equal(requests.at(-1).url,'/api/settings/test');assert.equal(requests.at(-1).body.bitvavo_api_key,'fictional-key');assert.equal(elements.connectionStatus.textContent,'Connected');
 const count=requests.length;elements.display_currency.value='USD';elements.timezone.value='Asia/Tokyo';elements.withdrawals_enabled.checked=true;elements.withdraw_threshold.value='1200';
 await event('settingsForm','submit');assert.equal(elements.review.open,true);assert.equal(requests.length,count,'Review must not save yet');
 const text=elements.reviewValues.children.map(x=>x.textContent).join(' ');assert.match(text,/LIVE|Dry run/);assert.match(text,/1200 EUR/);
 await event('cancel');assert.equal(elements.review.open,false);await event('confirm');assert.equal(requests.filter(r=>r.url==='/api/settings').length,0,'Cancelled review must not submit');assert.equal(requests.at(-1).body.event,'settings_save_cancelled');
 await event('settingsForm','submit');await event('confirm');assert.equal(requests.at(-1).url,'/api/settings');assert.equal(requests.at(-1).body.settings.withdrawals_enabled,true);assert.equal(requests.at(-1).body.settings.display_currency,'USD');assert.equal(requests.at(-1).body.settings.timezone,'Asia/Tokyo');
 await event('editDashboardCredentials');assert.equal(elements.dashboardEditor.hidden,false);
 elements.password.value='new-long-password';elements.passwordConfirm.value='different';const before=requests.length;await event('settingsForm','submit');assert.equal(requests.length,before);assert.match(elements.status.textContent,/do not match/);
 await event('editDashboardCredentials');assert.equal(elements.dashboardEditor.hidden,true);assert.equal(elements.password.value,'');
 await event('editExchangeCredentials');assert.equal(elements.exchangeEditor.hidden,true);assert.equal(elements.bitvavo_api_key.value,'');
 await event('settingsForm','submit');await event('confirm');const closedPayload=requests.at(-1).body;assert(!('username'in closedPayload));assert(!('password'in closedPayload));assert(!('bitvavo_api_key'in closedPayload.settings));assert(!('withdraw_address'in closedPayload.settings));
 vm.runInContext('initial.passwordChangeRequired=true',context);const beforeUpgrade=requests.length;await event('settingsForm','submit');assert.equal(requests.length,beforeUpgrade);assert.match(elements.status.textContent,/Open Change dashboard credentials/);vm.runInContext('initial.passwordChangeRequired=false',context);
 reject=true;await event('testConnection');assert.equal(elements.testConnection.disabled,false);assert.equal(elements.connectionStatus.textContent,'Connection failed');
 console.log('Settings UI passed: credentials, exchange fields, read-only test, review/cancel/save and validation');
})().catch(error=>{console.error(error);process.exitCode=1});

// Step two uses the actual server-rendered page without Dashboard Credentials.
const setupPage=execFileSync('python3',['-c',`import sys;from types import SimpleNamespace;sys.path.insert(0,'app');from configuration import DEFAULTS;from settings_ui import settings_page;print(settings_page(SimpleNamespace(public=lambda:dict(settings={**DEFAULTS,'timezone':'Pacific/Chatham'},username='operator',complete=False,passwordChangeRequired=False))))`],{cwd:root,encoding:'utf8'});
const setupElements={};
class SetupElement extends Element{append(...children){for(const child of children){child.parentElement=this;this.children.push(child);if(child.id)setupElements[child.id]=child}}}
for(const match of setupPage.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){const el=new SetupElement(match[1]);el.id=match[3];el.type=(match[2].match(/type="([^"]+)"/)||[])[1]||'';if(match[2].includes('data-setting'))el.dataset.setting='';setupElements[el.id]=el}
const setupRequests=[];vm.runInNewContext(setupPage.match(/<script>([\s\S]*?)<\/script>/)[1],{document:{getElementById:id=>setupElements[id],createElement:tag=>new SetupElement(tag),querySelectorAll:()=>Object.values(setupElements).filter(el=>'setting'in el.dataset)},location:{href:''},fetch:async(url,options)=>{setupRequests.push({url,body:JSON.parse(options.body)});return {ok:true,json:async()=>({})}}});
assert.equal(setupElements.timezone.value,'Pacific/Chatham');assert.equal(setupElements.timezone.children.at(-1).value,'Pacific/Chatham');
assert.equal(setupElements.dashboardCredentials,undefined);assert.equal(setupElements.username,undefined);
setupElements.settingsForm.listeners.submit({preventDefault(){}});assert.equal(setupElements.review.open,true);
assert.equal(setupRequests.length,0,'Step two reviews settings without editing account credentials');
console.log('First-run step two passed without credential fields');

// The dashboard owns a native modal and accepts messages only from its settings frame.
const dashboard=execFileSync('python3',['-c',`import ast;from pathlib import Path;m=ast.parse(Path('app/web_ui.py').read_text());print(next(ast.literal_eval(n.value) for n in m.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HTML' for t in n.targets)))`],{cwd:root,encoding:'utf8'});
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
