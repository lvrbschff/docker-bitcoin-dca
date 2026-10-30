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
 assert.equal(elements.settingsForm.hidden,false);assert.equal(elements.accountPanel.hidden,true);
 elements.exchange.value='BITVAVO';await event('exchange','change');assert.equal(elements.quote.value,'EUR');assert.equal(elements.quote.options[1].disabled,true);assert.equal(elements.kraken_api_key.parentElement.hidden,true);
 elements.bitvavo_api_key.value='fictional-key';elements.bitvavo_api_secret.value='fictional-secret';
 await event('testConnection');assert.equal(requests.at(-1).url,'/api/settings/test');assert.equal(requests.at(-1).body.bitvavo_api_key,'fictional-key');assert.equal(elements.status.textContent,'Connected');
 const count=requests.length;elements.withdrawals_enabled.checked=true;elements.withdraw_threshold.value='1200';
 await event('settingsForm','submit');assert.equal(elements.review.open,true);assert.equal(requests.length,count,'Review must not save yet');
 const text=elements.reviewValues.children.map(x=>x.textContent).join(' ');assert.match(text,/LIVE|Dry run/);assert.match(text,/1200 EUR/);
 await event('cancel');assert.equal(elements.review.open,false);await event('confirm');assert.equal(requests.length,count,'Cancelled review must not submit');
 await event('settingsForm','submit');await event('confirm');assert.equal(requests.at(-1).url,'/api/settings');assert.equal(requests.at(-1).body.settings.withdrawals_enabled,true);
 elements.password.value='new-long-password';elements.passwordConfirm.value='different';const before=requests.length;await event('settingsForm','submit');assert.equal(requests.length,before);assert.match(elements.status.textContent,/do not match/);
 reject=true;await event('testConnection');assert.equal(elements.testConnection.disabled,false);assert.equal(elements.status.textContent,'Connection failed');
 console.log('Settings UI passed: credentials, exchange fields, read-only test, review/cancel/save and validation');
})().catch(error=>{console.error(error);process.exitCode=1});
