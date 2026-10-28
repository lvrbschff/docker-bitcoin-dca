"""Local read-only portfolio dashboard and on-demand Parqet export."""
import hmac
import html
import json
import logging
import os
import re
import requests
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from secrets import token_urlsafe
from threading import RLock
import time

from activity import delete_activity_record, read_activity_records, read_dashboard_snapshot
from reporting import parqet_aggregate_rows, parqet_csv

LOG = logging.getLogger("bitvavo-dca.web")

HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bitcoin DCA · Portfolio</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2d;--line:#26324a;--text:#edf2ff;--muted:#9ba9c4;--accent:#8bafff;--green:#61d7ae;--red:#ff8f9e}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 15% 0%,#1b2a49 0,transparent 42%),var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1180px;margin:0 auto;padding:36px 22px 64px}.top{display:flex;justify-content:space-between;gap:18px;align-items:center;margin-bottom:26px}.eyebrow{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}h1{font-size:30px;margin:4px 0}.sub{color:var(--muted);margin:0}
.bottom-actions{display:flex;justify-content:flex-end;margin-top:18px}.badge{border:1px solid var(--line);border-radius:999px;padding:8px 12px;color:var(--muted);font-size:12px}.top-actions{display:flex;flex-direction:column;align-items:flex-end;gap:10px}.top-actions form{margin:0}.top-actions .field{min-width:170px}.top-actions .field label{margin-bottom:5px}
.panel{background:linear-gradient(145deg,rgba(21,31,52,.96),rgba(15,22,39,.96));border:1px solid var(--line);border-radius:18px;padding:20px;margin:18px 0;box-shadow:0 14px 40px #0002}
.controls{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr) minmax(0,1fr) auto;gap:12px;align-items:end}.field label{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}select,input,button{font:inherit;color:var(--text);background:#0c1425;border:1px solid var(--line);border-radius:10px;padding:11px 12px;width:100%}button{width:auto;cursor:pointer;background:var(--accent);border-color:var(--accent);color:#081122;font-weight:700}button.secondary{background:transparent;color:var(--text);border-color:var(--line)}button:hover{filter:brightness(1.08)}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.period-summary-grid{grid-template-columns:repeat(5,minmax(0,1fr))}.card{padding:17px;border:1px solid var(--line);border-radius:14px;background:#0c1425a8}.card label{display:block;color:var(--muted);font-size:12px;margin-bottom:9px}.value{font-size:22px;font-weight:700;letter-spacing:-.02em}.small{font-size:12px;color:var(--muted);margin-top:4px}
h2{font-size:17px;margin:0 0 16px}.tablewrap{overflow:auto}table{border-collapse:collapse;width:100%;min-width:740px}th,td{text-align:left;padding:11px 10px;border-bottom:1px solid var(--line);white-space:nowrap}th{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}td.num{text-align:right;font-variant-numeric:tabular-nums}.positive{color:var(--green)}.negative{color:var(--red)}.notice{color:var(--muted);font-size:13px;margin-top:12px}.error{color:var(--red);margin:12px 0}.hidden{display:none}.activity-heading,.pagination,.settings-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.settings-heading h2{margin:0}.settings-heading .field{min-width:160px}.activity-heading h2{margin:0}.page-size{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px}.page-size select{width:auto;padding:7px 10px}.pagination{margin-top:12px}.pagination button{padding:8px 12px}.pagination .notice{margin:0;text-align:center}
main,.panel,.grid,.card,.field,.top>div{min-width:0}
main{width:100%}.top-actions{flex-shrink:0}.controls>*{min-width:0}
input,select{min-width:0;max-width:100%}.card label,.small,.notice,.error{overflow-wrap:anywhere}
.value{font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.time-value{font-size:16px;line-height:1.5;letter-spacing:0;overflow-wrap:normal}
.date-point{display:flex;flex-wrap:wrap;gap:0 .35em}.date-point span{white-space:nowrap}
.countdown-value{font-size:clamp(16px,1.6vw,20px);white-space:nowrap;letter-spacing:0}
.tablewrap{width:100%;max-width:100%;min-width:0;overscroll-behavior-x:contain;-webkit-overflow-scrolling:touch}
.pagination{flex-wrap:wrap}.pagination .notice{flex:1;min-width:100px}
@media(max-width:1100px){.period-summary-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.controls{grid-template-columns:repeat(2,minmax(0,1fr))}.controls .field:first-child{grid-column:1/-1}}
@media(max-width:850px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.top{align-items:flex-start}.top-actions .field{min-width:150px}}
@media(max-width:600px){
 main{padding:22px 12px 36px}.panel{padding:15px;margin:14px 0;border-radius:14px}
 .top{flex-direction:column;gap:18px;margin-bottom:20px}h1{font-size:26px}.sub{font-size:14px}
 .top-actions{width:100%;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center}
 .top-actions form{grid-column:1/-1;justify-self:end}.top-actions .field{grid-column:1/-1;min-width:0}
 .controls{grid-template-columns:minmax(0,1fr)}.controls .field:first-child{grid-column:auto}
 input,select{font-size:16px}input,select,button{min-height:44px}
 .grid{gap:10px}.card{padding:13px}.card label{line-height:1.5;margin-bottom:7px}
 .value{font-size:19px;line-height:1.4}.time-card{grid-column:1/-1}
 .time-value{font-size:15px;line-height:1.65}.countdown-value{font-size:20px}
 .activity-heading,.settings-heading{align-items:flex-start;flex-wrap:wrap}
 .activity-heading h2{flex-basis:100%}.page-size{width:100%;justify-content:space-between}
 .page-size select{min-width:90px}.pagination{gap:10px}.pagination button{flex:1}
 .pagination .notice{order:3;flex-basis:100%;font-size:12px}
 th,td{padding:10px 8px;font-size:13px}.bottom-actions button{width:100%;white-space:normal}
}
@media(max-width:360px){.grid{grid-template-columns:minmax(0,1fr)}.time-card{grid-column:auto}}
.purchase-schedule{margin-top:24px;padding-top:20px;border-top:1px solid #354564}
.purchase-schedule h3{font-size:14px;color:var(--accent);margin:0 0 12px;font-weight:600}
.schedule-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
.schedule-grid .card{border-color:#354b70;background:#111f36}
.report-section{margin-top:38px;padding-top:28px;border-top:1px solid #354564;min-width:0}
.report-heading{margin-bottom:18px}.report-heading h2{font-size:21px;margin:0 0 5px}
.report-heading .sub{font-size:14px}.report-controls{border-color:#354b70;background:linear-gradient(145deg,#17243b,#111a2d)}
@media(max-width:600px){.schedule-grid{grid-template-columns:minmax(0,1fr)}.purchase-schedule{margin-top:20px;padding-top:17px}.report-section{margin-top:28px;padding-top:22px}.report-heading h2{font-size:19px}}
.footnote-ref{color:var(--accent);text-decoration:none;margin-left:3px}.footnote-ref:hover{text-decoration:underline}.footnote-ref:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px}
.dashboard-footnotes{margin-top:30px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;line-height:1.7;overflow-wrap:anywhere}
.dashboard-footnotes h2{font-size:14px;margin:0 0 12px}.dashboard-footnotes ol{margin:0;padding-left:20px}.dashboard-footnotes li{padding-left:4px;margin-bottom:14px;scroll-margin-top:20px}.dashboard-footnotes strong{color:var(--text);font-weight:600}.dashboard-footnotes p{margin:4px 0 0}
</style>
</head>
<body><main>
<header class="top"><div><h1>DCA Crypto Bot</h1><p class="sub">Portfolio overview, bot configuration and activity analysis, with optional Parqet CSV export</p></div><div class="top-actions"><form method="post" action="/logout"><button class="secondary" type="submit">Log out</button></form><div class="field"><label for="displayCurrency">Display currency</label><select id="displayCurrency"><option value="EUR">Euro (EUR)</option><option value="USD">US Dollar (USD)</option></select></div></div></header>
<section class="panel"><h2>Bot-tracked portfolio <a class="footnote-ref" href="#currency-note" aria-label="Read note 1 about display currency and prices"><sup>1</sup></a></h2><div class="grid">
<div class="card"><label>Bot-tracked BTC</label><div class="value" id="balance">—</div></div>
<div class="card"><label>Current BTC price</label><div class="value" id="spot">—</div><div class="small" id="spotUpdated">Kraken public price page</div></div>
<div class="card"><label>Value in fiat currency</label><div class="value" id="balanceValue">—</div></div>

</div></section>
<section class="panel">
<h2>Bot configuration</h2>
<div class="grid">
<div class="card"><label>Exchange</label><div class="value" id="settingExchange">—</div></div>
<div class="card"><label>Cryptocurrency</label><div class="value" id="baseAsset">—</div></div>
<div class="card"><label>Quote currency</label><div class="value" id="settingQuoteCurrency">—</div></div>
<div class="card"><label>Trading mode</label><div class="value" id="tradingMode">—</div></div>
<div class="card"><label>Purchase amount</label><div class="value" id="buyAmount">—</div></div>
<div class="card"><label>Deposit day</label><div class="value" id="depositDay">—</div></div>
<div class="card"><label>Automatic withdrawals</label><div class="value" id="withdrawEnabled">—</div></div>
<div class="card"><label>Withdrawal threshold</label><div class="value" id="threshold">—</div><div class="small" id="thresholdNote">Configured threshold</div></div>
</div><div class="purchase-schedule" aria-labelledby="scheduleHeading"><h3 id="scheduleHeading">Purchase schedule</h3><div class="grid schedule-grid">
<div class="card time-card"><label>Next purchase</label><div class="value time-value" id="nextPurchaseAt">—</div><div class="small" id="nextPurchaseStatus">Waiting for the bot schedule</div></div>
<div class="card time-card"><label>Time until next purchase</label><div class="value countdown-value" id="nextPurchaseCountdown">—</div><div class="small">Updates every second</div></div>
</div></div>
</section>
<section class="report-section" aria-labelledby="reportHeading">
<header class="report-heading"><h2 id="reportHeading">Period analysis <a class="footnote-ref" href="#report-note" aria-label="Read note 2 about reports, CSV exports and activity deletion"><sup>2</sup></a></h2><p class="sub">Choose a date range to review activity and prepare a Parqet export.</p></header>
<section class="panel report-controls">
<div class="controls">
<div class="field"><label for="interval">Withdrawal interval</label><select id="interval"></select></div>
<div class="field"><label for="start">From</label><input id="start" type="datetime-local"></div>
<div class="field"><label for="end">To</label><input id="end" type="datetime-local"></div>
<button id="refresh">Load report</button>
</div><div id="error" class="error hidden"></div>
</section>
<section class="panel"><h2>Selected period summary</h2><div class="grid period-summary-grid">
<div class="card"><label>BTC purchases</label><div class="value" id="buyCount">—</div><div class="small" id="buyBtc"></div></div>
<div class="card"><label>Purchase volume</label><div class="value" id="buyVolume">—</div><div class="small" id="avgPrice"></div></div>
<div class="card"><label>Trading fees</label><div class="value" id="tradeFees">—</div></div>
<div class="card"><label>Withdrawals</label><div class="value" id="withdrawBtc">—</div><div class="small" id="withdrawCount"></div></div>
<div class="card"><label>Profit / loss on period purchases</label><div class="value" id="profitLoss">—</div><div class="small" id="profitLossBasis">Current value of BTC bought in this period minus purchase costs and trading fees</div></div>
</div><div class="grid" style="margin-top:14px">
<div class="card"><label>Withdrawal fees</label><div class="value" id="withdrawFees">—</div></div>
<div class="card"><label>Net BTC in period</label><div class="value" id="netBtc">—</div><div class="small">Purchases minus withdrawals</div></div>
<div class="card time-card"><label>Date range</label><div class="value time-value" id="periodLabel">—</div></div>
<div class="card"><label>Last updated</label><div class="value" id="updated">—</div></div>
</div></section>
<section class="panel"><div class="activity-heading"><h2>Activity</h2><div class="page-size"><label for="pageSize">Entries per page</label><select id="pageSize"><option>10</option><option selected>20</option><option>50</option><option>100</option><option>500</option><option>1000</option></select></div></div><div class="tablewrap"><table><thead><tr><th>Date</th><th>Type</th><th>BTC</th><th>Price</th><th>Value</th><th>Fee</th><th>Action</th></tr></thead><tbody id="activities"></tbody></table></div><div class="pagination"><button class="secondary" id="previousPage">Previous</button><span id="pageInfo" class="notice"></span><button class="secondary" id="nextPage">Next</button></div></section>
<div class="bottom-actions"><button class="secondary" id="download">Download Parqet CSV</button></div>
</section>
<footer class="dashboard-footnotes" aria-labelledby="notesHeading">
<h2 id="notesHeading">Notes</h2>
<ol>
<li id="currency-note"><strong>Display currency and price data.</strong><p>Fiat values are converted to the selected display currency. The current BTC price is fetched from Kraken’s public BTC-to-fiat pages when the report loads. Parqet CSV exports use EUR.</p></li>
<li id="report-note"><strong>Period analysis, Parqet export and local activity history.</strong><p>These notes apply to the date filters in Period analysis, the Activity table and the Download Parqet CSV button. Choose an interval between bot-recorded BTC withdrawals or set a custom date range. The Parqet CSV consolidates the period into one purchase: all real purchase costs and trading fees in EUR, and net BTC received at withdrawals after network fees. The period must contain purchases and a bot-recorded withdrawal. Dry-run purchases are shown separately and excluded from the CSV. Delete removes a purchase from local history only; it cannot reverse an exchange order.</p></li>
<li id="local-access-note"><strong>Local access only.</strong><p>With the default Docker Compose port mapping, the dashboard is accessible only from the Docker host.</p></li>
</ol>
</footer>
</main>
<script>
const $=id=>document.getElementById(id);
let quote="EUR", displayCurrency="", fxRate=1, activityRows=[], currentPage=1;
function localInput(ms){const d=new Date(ms);d.setMinutes(d.getMinutes()-d.getTimezoneOffset());return d.toISOString().slice(0,16)}
function parseInput(el){return new Date(el.value).getTime()}
function money(v,c){try{return new Intl.NumberFormat("en-GB",{style:"currency",currency:c,maximumFractionDigits:2}).format(Number(v||0))}catch(e){return Number(v||0).toFixed(2)+" "+c}}
function btc(v){return Number(v||0).toLocaleString("en-GB",{minimumFractionDigits:0,maximumFractionDigits:8})+" BTC"}
function dateLabel(ms){return new Date(ms).toLocaleString("en-GB")}
function renderPeriodLabel(start,end){
 const points=[start,end].map((timestamp,index)=>{
  const point=document.createElement("time");
  point.className="date-point";point.dateTime=new Date(timestamp).toISOString();
  const date=document.createElement("span"),clock=document.createElement("span");
  date.textContent=(index?"To: ":"From: ")+new Date(timestamp).toLocaleDateString("en-GB");
  clock.textContent=new Date(timestamp).toLocaleTimeString("en-GB");
  point.append(date,clock);return point;
 });
 $("periodLabel").replaceChildren(...points);
}

let nextPurchaseTimestamp=null, scheduleClockOffset=0;
function renderPurchaseCountdown(){
 const output=$("nextPurchaseCountdown");
 if(nextPurchaseTimestamp===null){output.textContent="—";return}
 let seconds=Math.max(0,Math.ceil((nextPurchaseTimestamp-Date.now()-scheduleClockOffset)/1000));
 if(!seconds){output.textContent="Due now";return}
 const days=Math.floor(seconds/86400);seconds%=86400;
 const hours=Math.floor(seconds/3600);seconds%=3600;
 const minutes=Math.floor(seconds/60),remaining=seconds%60;
 output.textContent=(days?days+"d ":"")+String(hours).padStart(2,"0")+"h "+String(minutes).padStart(2,"0")+"m "+String(remaining).padStart(2,"0")+"s";
}
async function refreshPurchaseSchedule(){
 try{
  const response=await fetch("/api/schedule");
  if(response.status===401){window.location.reload();return}
  if(!response.ok)throw new Error("Schedule unavailable");
  const schedule=await response.json(),target=Date.parse(schedule.nextPurchaseAt),serverTime=Date.parse(schedule.serverTime);
  nextPurchaseTimestamp=Number.isFinite(target)?target:null;
  scheduleClockOffset=Number.isFinite(serverTime)?serverTime-Date.now():0;
  $("nextPurchaseAt").textContent=nextPurchaseTimestamp===null?"Not scheduled":new Date(nextPurchaseTimestamp).toLocaleString("en-GB",{timeZone:schedule.timezone||"UTC",timeZoneName:"short"});
  $("nextPurchaseStatus").textContent=schedule.nextPurchaseStatus||"Waiting for the bot schedule";
  renderPurchaseCountdown();
 }catch(e){$("nextPurchaseStatus").textContent="Could not refresh the bot schedule"}
}
setInterval(renderPurchaseCountdown,1000);
setInterval(refreshPurchaseSchedule,30000);
refreshPurchaseSchedule();

function showError(message){$("error").textContent=message||"";$("error").classList.toggle("hidden",!message)}
function selectedPeriod(){const start=parseInput($("start")),end=parseInput($("end"));if(!Number.isFinite(start)||!Number.isFinite(end)||start>=end)throw new Error("Please choose a valid date range.");return {start:start,end:end}}
async function loadIntervals(){
 const response=await fetch("/api/withdrawals");if(!response.ok)throw new Error("Could not load withdrawal history.");
 const withdrawals=await response.json();const select=$("interval");select.innerHTML="";
 const custom=document.createElement("option");custom.value="";custom.textContent="Custom date range";select.appendChild(custom);
 for(let i=1;i<withdrawals.length;i++){const a=withdrawals[i-1],b=withdrawals[i],o=document.createElement("option");o.value=a.timestamp+","+b.timestamp;o.textContent=dateLabel(a.timestamp)+" → "+dateLabel(b.timestamp);select.appendChild(o)}
 const now=Date.now();if(withdrawals.length>=2){select.value=withdrawals[withdrawals.length-2].timestamp+","+withdrawals[withdrawals.length-1].timestamp}
 else {select.value="";$("start").value=localInput(withdrawals.length===1?withdrawals[0].timestamp:now-90*86400000);$("end").value=localInput(now)}
}
$("interval").addEventListener("change",()=>{if(!$("interval").value)return;const p=$("interval").value.split(",").map(Number);$("start").value=localInput(p[0]);$("end").value=localInput(p[1]);loadAnalytics()});
function renderActivities(){
 const body=$("activities");body.innerHTML="";
 const pageSize=Number($("pageSize").value),total=activityRows.length,totalPages=Math.max(1,Math.ceil(total/pageSize));
 currentPage=Math.min(Math.max(1,currentPage),totalPages);
 const start=(currentPage-1)*pageSize,end=Math.min(start+pageSize,total);
 for(const row of activityRows.slice(start,end)){
  const tr=document.createElement("tr"),type=row.simulated?"Simulated buy (dry run)":(row.type==="TransferOut"?"Withdrawal":row.type),vals=[dateLabel(Date.parse(row.datetime)),type,btc(row.shares),money(Number(row.price)*fxRate,displayCurrency)+"/BTC",money(Number(row.amount)*fxRate,displayCurrency),(row.simulated?"~":"")+money(Number(row.fee)*fxRate,displayCurrency)];
  vals.forEach((v,i)=>{const td=document.createElement("td");td.textContent=v;if(i>=2)td.className="num";tr.appendChild(td)});
  const action=document.createElement("td");
  if(row.type==="Buy"){const button=document.createElement("button");button.className="secondary";button.textContent="Delete";button.addEventListener("click",async()=>{if(!confirm("Delete this purchase from local bot history? This does not reverse an exchange order."))return;try{const response=await fetch("/api/activity/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({activityId:row.activityId})});const result=await response.json();if(!response.ok)throw new Error(result.error||"Could not delete purchase.");await loadAnalytics()}catch(e){showError(e.message)}});action.appendChild(button)}
  action.className="num";tr.appendChild(action);body.appendChild(tr)
 }
 if(!total){const tr=document.createElement("tr"),td=document.createElement("td");td.colSpan=7;td.textContent="No BTC activity in this period.";td.className="notice";tr.appendChild(td);body.appendChild(tr)}
 $("pageInfo").textContent=total?("Showing "+(start+1)+"–"+end+" of "+total+" · Page "+currentPage+" of "+totalPages):"No entries";
 $("previousPage").disabled=currentPage<=1;$("nextPage").disabled=currentPage>=totalPages;
}
$("pageSize").addEventListener("change",()=>{currentPage=1;renderActivities()});
$("previousPage").addEventListener("click",()=>{if(currentPage>1){currentPage--;renderActivities()}});
$("nextPage").addEventListener("click",()=>{currentPage++;renderActivities()});
$("displayCurrency").addEventListener("change",()=>{displayCurrency=$("displayCurrency").value;localStorage.setItem("bitcoinDcaDisplayCurrency",displayCurrency);loadAnalytics()});
async function loadAnalytics(){
 try{showError("");const p=selectedPeriod();const response=await fetch("/api/analytics?start="+p.start+"&end="+p.end);const d=await response.json();if(!response.ok)throw new Error(d.error||"Could not load the report.");quote=d.quote;
 if(!displayCurrency){const saved=localStorage.getItem("bitcoinDcaDisplayCurrency");displayCurrency=["EUR","USD"].includes(saved)?saved:(["EUR","USD"].includes(quote)?quote:"EUR")}
 $("displayCurrency").value=displayCurrency;
 const ex=d.exchange==="KRAKEN"?"Kraken":d.exchange==="BITVAVO"?"Bitvavo":d.exchange;document.title=ex+" DCA · Crypto Bot";
 let livePrice=Number((d.snapshot||{}).price||0);
 try{
  const priceResponse=await fetch("/api/market-price?currency="+displayCurrency);const market=await priceResponse.json();if(!priceResponse.ok)throw new Error(market.error||"Could not load current BTC price.");
  fxRate=Number(market.conversionRate);livePrice=Number(market.price);
  $("spotUpdated").textContent="Kraken public price page · fetched "+new Date(Number(market.updatedAt)*1000).toLocaleTimeString("en-GB");
 }catch(priceError){
  displayCurrency=quote;fxRate=1;$("displayCurrency").value=quote;
  $("spotUpdated").textContent=livePrice>0?"Last price saved by the bot":"Live price unavailable";
  showError("Could not fetch a live BTC price. Showing saved activity and values in "+quote+" where available.");
 }
 $("balance").textContent=btc(d.snapshot.balance);$("spot").textContent=livePrice>0?money(livePrice,displayCurrency)+"/BTC":"—";
 const currentPortfolioValue=Number(d.snapshot.balance)*livePrice;
 $("balanceValue").textContent=livePrice>0?money(currentPortfolioValue,displayCurrency):"—";
 if(livePrice>0){
  const periodPurchaseValue=Number(d.summary.boughtBtc)*livePrice;
  const periodPurchaseCosts=(Number(d.summary.buyVolume)+Number(d.summary.tradeFees))*fxRate;
  const profitLoss=periodPurchaseValue-periodPurchaseCosts;
  $("profitLoss").textContent=money(profitLoss,displayCurrency);
  $("profitLoss").className="value "+(profitLoss>=0?"positive":"negative");
 }else{$("profitLoss").textContent="—";$("profitLoss").className="value"}
 $("settingExchange").textContent=ex;$("baseAsset").textContent=d.configuration.baseAsset;$("settingQuoteCurrency").textContent=quote;$("tradingMode").textContent=d.configuration.liveTrading?"Live":"Dry run";
 $("buyAmount").textContent=Number(d.configuration.buyAmount)===0?"Exchange minimum":btc(d.configuration.buyAmount);
 $("depositDay").textContent=String(d.configuration.depositDay);$("withdrawEnabled").textContent=d.configuration.withdrawalsEnabled?"Enabled":"Disabled";
 $("threshold").textContent=money(Number(d.configuration.withdrawalThreshold)*fxRate,displayCurrency);$("thresholdNote").textContent="Threshold in "+quote+", shown in "+displayCurrency;
 $("buyCount").textContent=d.summary.buyCount;$("buyBtc").textContent=btc(d.summary.boughtBtc);
 $("buyVolume").textContent=money(Number(d.summary.buyVolume)*fxRate,displayCurrency);$("avgPrice").textContent="Average: "+money(Number(d.summary.averagePrice)*fxRate,displayCurrency)+"/BTC";
 $("tradeFees").textContent=money(Number(d.summary.tradeFees)*fxRate,displayCurrency);$("withdrawBtc").textContent=btc(d.summary.withdrawnBtc);$("withdrawCount").textContent=d.summary.withdrawalCount+" withdrawals";
 $("withdrawFees").textContent=money(Number(d.summary.withdrawalFees)*fxRate,displayCurrency);$("netBtc").textContent=btc(d.summary.netBtc);
 renderPeriodLabel(p.start,p.end);$("updated").textContent=new Date(d.snapshot.updatedAt||Date.now()).toLocaleTimeString("en-GB");
 activityRows=d.activities.slice().reverse();currentPage=1;renderActivities();
 }catch(e){showError(e.message)}
}
$("refresh").addEventListener("click",loadAnalytics);
$("download").addEventListener("click",async()=>{try{showError("");const p=selectedPeriod();const response=await fetch("/api/export.csv?start="+p.start+"&end="+p.end);if(!response.ok){const d=await response.json();throw new Error(d.error||"Could not generate the CSV export.")}const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="parqet_crypto_"+new Date(p.start).toISOString().slice(0,10)+"_"+new Date(p.end).toISOString().slice(0,10)+".csv";a.click();URL.revokeObjectURL(url)}catch(e){showError(e.message)}});
(async()=>{try{await loadIntervals();await loadAnalytics()}catch(e){showError(e.message)}})();
</script></body></html>
"""


LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · DCA Crypto Bot</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2d;--line:#26324a;--text:#edf2ff;--muted:#9ba9c4;--accent:#8bafff;--red:#ff8f9e}
*{box-sizing:border-box}body{min-height:100vh;margin:0;display:grid;place-items:center;padding:22px;background:radial-gradient(ellipse at 15% 0%,#1b2a49 0,transparent 42%),var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
.card{width:min(100%,420px);padding:32px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,rgba(21,31,52,.98),rgba(15,22,39,.98));box-shadow:0 18px 48px #0005}
.eyebrow{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}h1{font-size:27px;margin:6px 0 4px}.sub{color:var(--muted);margin:0 0 24px}
label{display:block;color:var(--muted);font-size:12px;margin:15px 0 6px}input{width:100%;padding:12px 13px;color:var(--text);background:#0c1425;border:1px solid var(--line);border-radius:10px;font:inherit;outline:none}input:focus{border-color:var(--accent);box-shadow:0 0 0 3px #8bafff22}
button{width:100%;margin-top:22px;padding:12px;border:1px solid var(--accent);border-radius:10px;background:var(--accent);color:#081122;font:inherit;font-weight:700;cursor:pointer}button:hover{filter:brightness(1.08)}
.error{min-height:20px;margin:12px 0 0;color:var(--red);font-size:13px}.foot{margin-top:20px;color:var(--muted);font-size:12px}
@media(max-width:480px){body{padding:16px;min-height:100svh}.card{padding:24px}input{font-size:16px}}
</style>
</head>
<body><main class="card">
<div class="eyebrow">Local access only</div>
<h1>DCA Crypto Bot</h1>
<p class="sub">Sign in to view your portfolio and activity.</p>
<form method="post" action="/login">
<label for="username">Username</label>
<input id="username" name="username" type="text" autocomplete="username" required autofocus>
<label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Sign in</button>
</form>
<p class="error">{{ERROR}}</p>
<p class="foot">Your dashboard is available only on the Docker host.</p>
</main></body></html>
"""

SESSION_TTL_SECONDS = 12 * 60 * 60
_SESSION_LOCK = RLock()
_SESSIONS = {}


def _login_page(error=""):
    return LOGIN_HTML.replace("{{ERROR}}", html.escape(error))


class _PageTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data and data.strip():
            self.parts.append(data.strip())


def _kraken_public_btc_price(currency):
    currency = currency.upper()
    if currency not in ("EUR", "USD"):
        raise ValueError("Display currency and QUOTE_CURRENCY must be EUR or USD")
    url = f"https://www.kraken.com/convert/btc/{currency.lower()}"
    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; BitcoinDCA/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=15,
    )
    response.raise_for_status()
    parser = _PageTextParser()
    parser.feed(response.text)
    page_text = " ".join(" ".join(parser.parts).split())
    number = r"([0-9][0-9,]*(?:\.[0-9]+)?)"
    patterns = (
        rf"1\s*BTC\s*=\s*{number}\s*{currency}\b",
        rf"1(?:\.00)?\s*BTC\s*=\s*[$€]\s*{number}",
    )
    for pattern in patterns:
        match = re.search(pattern, page_text, flags=re.IGNORECASE)
        if match:
            try:
                price = Decimal(match.group(1).replace(",", ""))
            except InvalidOperation:
                continue
            if price.is_finite() and price > 0:
                return price
    raise RuntimeError(f"Could not find the BTC/{currency} price on Kraken’s public converter page")


def _public_market_price(display_currency, quote_currency):
    display_currency = display_currency.upper()
    quote_currency = quote_currency.upper()
    if display_currency not in ("EUR", "USD") or quote_currency not in ("EUR", "USD"):
        raise ValueError("Display currency and QUOTE_CURRENCY must be EUR or USD")
    prices = {
        currency: _kraken_public_btc_price(currency)
        for currency in ("EUR", "USD")
    }
    conversion = prices[display_currency] / prices[quote_currency]
    return {
        "currency": display_currency,
        "quoteCurrency": quote_currency,
        "price": str(prices[display_currency]),
        "conversionRate": str(conversion),
        "source": "Kraken public conversion page",
        "updatedAt": int(datetime.now(timezone.utc).timestamp()),
    }


def _period(query):
    try:
        start_ms = int(query["start"][0])
        end_ms = int(query["end"][0])
    except (KeyError, ValueError, IndexError):
        raise ValueError("start and end must be Unix timestamps in milliseconds")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if start_ms < 0 or end_ms <= start_ms or end_ms > now_ms + 60_000:
        raise ValueError("Invalid period; end must be after start and not in the future")
    return start_ms, end_ms



class Handler(BaseHTTPRequestHandler):
    settings = None

    def _send(self, status, body, content_type="application/json; charset=utf-8", filename=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _has_valid_session(self):
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            morsel = cookies.get("dca_session")
        except CookieError:
            return False
        if morsel is None:
            return False
        token = morsel.value
        now = time.monotonic()
        with _SESSION_LOCK:
            expires_at = _SESSIONS.get(token)
            if expires_at is None:
                return False
            if expires_at <= now:
                _SESSIONS.pop(token, None)
                return False
            return True

    def _require_authentication(self):
        if self._has_valid_session():
            return True
        self._json(401, {"error": "Authentication required"})
        return False

    def _handle_login(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValueError("Invalid login request")
            form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            username = form.get("username", [""])[0]
            password = form.get("password", [""])[0]
        except (ValueError, UnicodeDecodeError):
            self._send(400, _login_page("Please enter your username and password."), "text/html; charset=utf-8")
            return

        expected_username = (os.getenv("WEB_UI_USERNAME", "admin") or "admin").encode("utf-8")
        expected_password = (os.getenv("WEB_UI_PASSWORD", "admin") or "admin").encode("utf-8")
        username_matches = hmac.compare_digest(username.encode("utf-8"), expected_username)
        password_matches = hmac.compare_digest(password.encode("utf-8"), expected_password)
        if not (username_matches and password_matches):
            self._send(401, _login_page("The username or password is incorrect."), "text/html; charset=utf-8")
            return

        token = token_urlsafe(32)
        now = time.monotonic()
        with _SESSION_LOCK:
            expired = [key for key, expiry in _SESSIONS.items() if expiry <= now]
            for key in expired:
                _SESSIONS.pop(key, None)
            _SESSIONS[token] = now + SESSION_TTL_SECONDS
        self.send_response(303)
        self.send_header(
            "Set-Cookie",
            f"dca_session={token}; Path=/; Max-Age={SESSION_TTL_SECONDS}; HttpOnly; SameSite=Strict",
        )
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_logout(self):
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            morsel = cookies.get("dca_session")
        except CookieError:
            morsel = None
        if morsel is not None:
            with _SESSION_LOCK:
                _SESSIONS.pop(morsel.value, None)
        self.send_response(303)
        self.send_header("Set-Cookie", "dca_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            self._handle_login()
            return
        if parsed.path == "/logout":
            self._handle_logout()
            return
        if not self._require_authentication():
            return
        if parsed.path != "/api/activity/delete":
            self._json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValueError("Invalid request body")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            activity_id = payload.get("activityId") if isinstance(payload, dict) else None
            if not isinstance(activity_id, str) or not activity_id:
                raise ValueError("activityId is required")
            if not delete_activity_record(activity_id):
                self._json(404, {"error": "Purchase not found or cannot be deleted"})
                return
            self._json(200, {"deleted": True})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" and not self._has_valid_session():
            self._send(200, _login_page(), "text/html; charset=utf-8")
            return
        if not self._require_authentication():
            return
        if parsed.path == "/":
            self._send(200, HTML, "text/html; charset=utf-8")
            return
        query = parse_qs(parsed.query)
        if parsed.path == "/api/market-price":
            currency = query.get("currency", ["EUR"])[0].upper()
            try:
                market = _public_market_price(currency, self.settings["quote"])
                self._json(200, market)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._json(503, {"error": str(exc)})
            except Exception:
                LOG.exception("Kraken public price page fetch failed")
                self._json(502, {"error": "Could not fetch the current BTC price from Kraken’s public page. Check container internet access."})
            return
        try:
            if parsed.path == "/api/schedule":
                snapshot = read_dashboard_snapshot()
                self._json(200, {
                    "nextPurchaseAt": snapshot.get("nextPurchaseAt"),
                    "nextPurchaseStatus": snapshot.get("nextPurchaseStatus", "Schedule pending"),
                    "timezone": self.settings["timezone"],
                    "serverTime": datetime.now(timezone.utc).isoformat(),
                })
                return
            records = read_activity_records()
            if parsed.path == "/api/withdrawals":
                withdrawals = [
                    {
                        "timestamp": _row_timestamp_ms(record["row"]),
                        "txId": record.get("id", ""),
                    }
                    for record in records
                    if record["row"].get("type") == "TransferOut"
                ]
                withdrawals.sort(key=lambda item: item["timestamp"])
                self._json(200, withdrawals)
                return
            if parsed.path in ("/api/analytics", "/api/export.csv"):
                start_ms, end_ms = _period(query)
                selected_records = [
                    record for record in records
                    if start_ms < _row_timestamp_ms(record["row"]) <= end_ms
                ]
                if parsed.path == "/api/export.csv":
                    rows = parqet_aggregate_rows(
                        selected_records, self.settings["exchange"], self.settings["base"]
                    )
                    if not rows:
                        raise ValueError(
                            "The selected period must contain at least one real purchase "
                            "and one bot-recorded BTC withdrawal."
                        )
                    filename = "parqet_" + self.settings["base"].lower() + "_" + str(start_ms) + "_" + str(end_ms) + ".csv"
                    self._send(200, parqet_csv(rows), "text/csv; charset=utf-8", filename)
                else:
                    result = _analytics_from_rows(
                        self.settings, selected_records, read_dashboard_snapshot()
                    )
                    self._json(200, result)
                return
            self._json(404, {"error": "Not found"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            LOG.exception("Web UI request failed")
            self._json(502, {"error": "Stored bot activity could not be loaded. Check the container logs."})

    def log_message(self, fmt, *args):
        LOG.info("Web UI %s - %s", self.address_string(), fmt % args)


def _row_timestamp_ms(row):
    value = datetime.fromisoformat(row["datetime"].replace("Z", "+00:00"))
    return int(value.timestamp() * 1000)


def _analytics_from_rows(settings, records, snapshot):
    quote = settings["quote"]
    actual_rows = [record["row"] for record in records if not record.get("simulated")]
    simulated_rows = [record["row"] for record in records if record.get("simulated")]
    buys = [row for row in actual_rows if row["type"] == "Buy"]
    simulated_buys = [row for row in simulated_rows if row["type"] == "Buy"]
    withdrawals = [row for row in actual_rows if row["type"] == "TransferOut"]
    bought_btc = sum((Decimal(row["shares"]) for row in buys), Decimal(0))
    buy_volume = sum((Decimal(row["amount"]) for row in buys), Decimal(0))
    trade_fees = sum((Decimal(row["fee"]) for row in buys), Decimal(0))
    withdrawn_btc = sum((Decimal(row["shares"]) for row in withdrawals), Decimal(0))
    withdrawal_fees = sum((Decimal(row["fee"]) for row in withdrawals), Decimal(0))
    balance = Decimal(str(snapshot.get("trackedBtc", "0")))
    price = Decimal(str(snapshot.get("price", "0")))
    value = Decimal(str(snapshot.get("value", balance * price)))
    return {
        "quote": quote,
        "exchange": settings["exchange"],
        "configuration": {
            "liveTrading": bool(settings["live"]),
            "buyAmount": str(settings["buy_amount"]),
            "baseAsset": settings["base"],
            "depositDay": int(settings["deposit_day"]),
            "withdrawalsEnabled": bool(settings["withdrawals_enabled"]),
            "withdrawalThreshold": str(settings["withdraw_threshold"]),
        },
        "snapshot": {
            "balance": str(balance),
            "price": str(price),
            "value": str(value),
            "quoteBalance": str(snapshot.get("quoteBalance", "0")),
            "withdrawThreshold": str(snapshot.get("withdrawThreshold", settings["withdraw_threshold"])),
            "updatedAt": snapshot.get("updatedAt"),
        },
        "summary": {
            "buyCount": len(buys),
            "boughtBtc": str(bought_btc),
            "simulatedBuyCount": len(simulated_buys),
            "simulatedBtc": str(sum((Decimal(row["shares"]) for row in simulated_buys), Decimal(0))),
            "buyVolume": str(buy_volume),
            "tradeFees": str(trade_fees),
            "averagePrice": str(buy_volume / bought_btc if bought_btc else Decimal(0)),
            "withdrawalCount": len(withdrawals),
            "withdrawnBtc": str(withdrawn_btc),
            "withdrawalFees": str(withdrawal_fees),
            "netBtc": str(bought_btc - withdrawn_btc),
        },
        "activities": [
            {**record["row"], "activityId": record.get("id", ""), "simulated": bool(record.get("simulated"))}
            for record in records
        ],
    }


def serve(settings, port):
    handler = type("ConfiguredHandler", (Handler,), {"settings": settings})
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    LOG.info("Local portfolio web UI listening on container port %s.", port)
    server.serve_forever()
