"""Local read-only portfolio dashboard and on-demand Parqet export."""
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from bot import Bitvavo
from reporting import parqet_csv, parqet_rows

LOG = logging.getLogger("bitvavo-dca.web")

HTML = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bitvavo DCA · Portfolio</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2d;--line:#26324a;--text:#edf2ff;--muted:#9ba9c4;--accent:#8bafff;--green:#61d7ae;--red:#ff8f9e}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 15% 0%,#1b2a49 0,transparent 42%),var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1180px;margin:0 auto;padding:36px 22px 64px}.top{display:flex;justify-content:space-between;gap:18px;align-items:center;margin-bottom:26px}.eyebrow{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}h1{font-size:30px;margin:4px 0}.sub{color:var(--muted);margin:0}
.badge{border:1px solid var(--line);border-radius:999px;padding:8px 12px;color:var(--muted);font-size:12px}
.panel{background:linear-gradient(145deg,rgba(21,31,52,.96),rgba(15,22,39,.96));border:1px solid var(--line);border-radius:18px;padding:20px;margin:18px 0;box-shadow:0 14px 40px #0002}
.controls{display:grid;grid-template-columns:2fr 1fr 1fr auto auto;gap:12px;align-items:end}.field label{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}select,input,button{font:inherit;color:var(--text);background:#0c1425;border:1px solid var(--line);border-radius:10px;padding:11px 12px;width:100%}button{width:auto;cursor:pointer;background:var(--accent);border-color:var(--accent);color:#081122;font-weight:700}button.secondary{background:transparent;color:var(--text);border-color:var(--line)}button:hover{filter:brightness(1.08)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card{padding:17px;border:1px solid var(--line);border-radius:14px;background:#0c1425a8}.card label{display:block;color:var(--muted);font-size:12px;margin-bottom:9px}.value{font-size:22px;font-weight:700;letter-spacing:-.02em}.small{font-size:12px;color:var(--muted);margin-top:4px}
h2{font-size:17px;margin:0 0 16px}.tablewrap{overflow:auto}table{border-collapse:collapse;width:100%;min-width:740px}th,td{text-align:left;padding:11px 10px;border-bottom:1px solid var(--line);white-space:nowrap}th{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}td.num{text-align:right;font-variant-numeric:tabular-nums}.positive{color:var(--green)}.negative{color:var(--red)}.notice{color:var(--muted);font-size:13px;margin-top:12px}.error{color:var(--red);margin:12px 0}.hidden{display:none}
@media(max-width:850px){.controls{grid-template-columns:1fr 1fr}.controls .field:first-child{grid-column:span 2}.grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:520px){main{padding:24px 14px}.top{align-items:flex-start;flex-direction:column}h1{font-size:25px}.controls{grid-template-columns:1fr}.controls .field:first-child{grid-column:auto}.grid{gap:9px}.card{padding:13px}.value{font-size:18px}}
</style>
</head>
<body><main>
<header class="top"><div><div class="eyebrow">Bitvavo · DCA</div><h1>Bitcoin-Übersicht</h1><p class="sub">Käufe, Gebühren, Auszahlungen und Parqet-Export</p></div><div class="badge">Nur lokal erreichbar</div></header>
<section class="panel">
<div class="controls">
<div class="field"><label for="interval">Auszahlungsintervall</label><select id="interval"></select></div>
<div class="field"><label for="start">Von</label><input id="start" type="datetime-local"></div>
<div class="field"><label for="end">Bis</label><input id="end" type="datetime-local"></div>
<button id="refresh">Auswertung laden</button><button class="secondary" id="download">CSV herunterladen</button>
</div><div id="error" class="error hidden"></div><div class="notice">Wähle ein Intervall zwischen zwei abgeschlossenen BTC-Auszahlungen oder lege den Zeitraum manuell fest.</div>
</section>
<section class="panel"><h2>Bestand auf Bitvavo</h2><div class="grid">
<div class="card"><label>Verfügbarer BTC-Bestand</label><div class="value" id="balance">—</div></div>
<div class="card"><label>Aktueller BTC-Kurs</label><div class="value" id="spot">—</div></div>
<div class="card"><label>Gegenwert des Bestands</label><div class="value" id="balanceValue">—</div></div>
<div class="card"><label>Auszahlungsziel</label><div class="value" id="threshold">—</div><div class="small">Schwelle aus Compose</div></div>
</div></section>
<section class="panel"><h2>Auswertung des gewählten Zeitraums</h2><div class="grid">
<div class="card"><label>BTC-Käufe</label><div class="value" id="buyCount">—</div><div class="small" id="buyBtc"></div></div>
<div class="card"><label>Kaufvolumen</label><div class="value" id="buyVolume">—</div><div class="small" id="avgPrice"></div></div>
<div class="card"><label>Handelsgebühren</label><div class="value" id="tradeFees">—</div></div>
<div class="card"><label>Auszahlungen</label><div class="value" id="withdrawBtc">—</div><div class="small" id="withdrawCount"></div></div>
</div><div class="grid" style="margin-top:14px">
<div class="card"><label>Auszahlungsgebühren</label><div class="value" id="withdrawFees">—</div></div>
<div class="card"><label>Netto-BTC im Zeitraum</label><div class="value" id="netBtc">—</div><div class="small">Käufe minus Auszahlungen</div></div>
<div class="card"><label>Zeitraum</label><div class="value" id="periodLabel">—</div></div>
<div class="card"><label>Aktualisiert</label><div class="value" id="updated">—</div></div>
</div></section>
<section class="panel"><h2>Aktivitäten</h2><div class="tablewrap"><table><thead><tr><th>Datum</th><th>Typ</th><th>BTC</th><th>Kurs</th><th>Wert</th><th>Gebühr</th></tr></thead><tbody id="activities"></tbody></table></div></section>
</main>
<script>
const $=id=>document.getElementById(id);
let quote="EUR", dataCurrency="EUR";
function localInput(ms){const d=new Date(ms);d.setMinutes(d.getMinutes()-d.getTimezoneOffset());return d.toISOString().slice(0,16)}
function parseInput(el){return new Date(el.value).getTime()}
function money(v,c){try{return new Intl.NumberFormat("de-DE",{style:"currency",currency:c,maximumFractionDigits:2}).format(Number(v||0))}catch(e){return Number(v||0).toFixed(2)+" "+c}}
function btc(v){return Number(v||0).toLocaleString("de-DE",{minimumFractionDigits:0,maximumFractionDigits:8})+" BTC"}
function dateLabel(ms){return new Date(ms).toLocaleString("de-DE")}
function showError(message){$("error").textContent=message||"";$("error").classList.toggle("hidden",!message)}
function selectedPeriod(){const start=parseInput($("start")),end=parseInput($("end"));if(!Number.isFinite(start)||!Number.isFinite(end)||start>=end)throw new Error("Bitte einen gültigen Zeitraum wählen.");return {start:start,end:end}}
async function loadIntervals(){
 const response=await fetch("/api/withdrawals");if(!response.ok)throw new Error("Auszahlungsverlauf konnte nicht geladen werden.");
 const withdrawals=await response.json();const select=$("interval");select.innerHTML="";
 const custom=document.createElement("option");custom.value="";custom.textContent="Manueller Zeitraum";select.appendChild(custom);
 for(let i=1;i<withdrawals.length;i++){const a=withdrawals[i-1],b=withdrawals[i],o=document.createElement("option");o.value=a.timestamp+","+b.timestamp;o.textContent=dateLabel(a.timestamp)+" → "+dateLabel(b.timestamp);select.appendChild(o)}
 const now=Date.now();if(withdrawals.length>=2){select.value=withdrawals[withdrawals.length-2].timestamp+","+withdrawals[withdrawals.length-1].timestamp}
 else {select.value="";$("start").value=localInput(withdrawals.length===1?withdrawals[0].timestamp:now-90*86400000);$("end").value=localInput(now)}
}
$("interval").addEventListener("change",()=>{if(!$("interval").value)return;const p=$("interval").value.split(",").map(Number);$("start").value=localInput(p[0]);$("end").value=localInput(p[1]);loadAnalytics()});
async function loadAnalytics(){
 try{showError("");const p=selectedPeriod();const response=await fetch("/api/analytics?start="+p.start+"&end="+p.end);const d=await response.json();if(!response.ok)throw new Error(d.error||"Auswertung fehlgeschlagen.");quote=d.quote;dataCurrency=quote;
 $("balance").textContent=btc(d.snapshot.balance);$("spot").textContent=money(d.snapshot.price,quote)+"/BTC";$("balanceValue").textContent=money(d.snapshot.value,quote);
 $("threshold").textContent=money(d.snapshot.withdrawThreshold,quote);
 $("buyCount").textContent=d.summary.buyCount;$("buyBtc").textContent=btc(d.summary.boughtBtc);
 $("buyVolume").textContent=money(d.summary.buyVolume,quote);$("avgPrice").textContent="Durchschnitt: "+money(d.summary.averagePrice,quote)+"/BTC";
 $("tradeFees").textContent=money(d.summary.tradeFees,quote);$("withdrawBtc").textContent=btc(d.summary.withdrawnBtc);$("withdrawCount").textContent=d.summary.withdrawalCount+" abgeschlossene Auszahlungen";
 $("withdrawFees").textContent=money(d.summary.withdrawalFees,quote);$("netBtc").textContent=btc(d.summary.netBtc);
 $("periodLabel").textContent=new Date(p.start).toLocaleDateString("de-DE")+" – "+new Date(p.end).toLocaleDateString("de-DE");$("updated").textContent=new Date().toLocaleTimeString("de-DE");
 const body=$("activities");body.innerHTML="";
 for(const row of d.activities.slice().reverse()){const tr=document.createElement("tr"),vals=[dateLabel(Date.parse(row.datetime)),row.type,btc(row.shares),money(row.price,quote)+"/BTC",money(row.amount,quote),money(row.fee,quote)];vals.forEach((v,i)=>{const td=document.createElement("td");td.textContent=v;if(i>=2)td.className="num";tr.appendChild(td)});body.appendChild(tr)}
 if(!d.activities.length){const tr=document.createElement("tr"),td=document.createElement("td");td.colSpan=6;td.textContent="Keine BTC-Aktivitäten in diesem Zeitraum.";td.className="notice";tr.appendChild(td);body.appendChild(tr)}
 }catch(e){showError(e.message)}
}
$("refresh").addEventListener("click",loadAnalytics);
$("download").addEventListener("click",async()=>{try{showError("");const p=selectedPeriod();const response=await fetch("/api/export.csv?start="+p.start+"&end="+p.end);if(!response.ok){const d=await response.json();throw new Error(d.error||"CSV-Export fehlgeschlagen.")}const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="parqet_btc_"+new Date(p.start).toISOString().slice(0,10)+"_"+new Date(p.end).toISOString().slice(0,10)+".csv";a.click();URL.revokeObjectURL(url)}catch(e){showError(e.message)}});
(async()=>{try{await loadIntervals();await loadAnalytics()}catch(e){showError(e.message)}})();
</script></body></html>
"""


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

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, HTML, "text/html; charset=utf-8")
            return
        query = parse_qs(parsed.query)
        try:
            api = Bitvavo()
            if parsed.path == "/api/withdrawals":
                now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                rows = api.btc_withdrawals(start_ms=0, end_ms=now_ms)
                withdrawals = [
                    {"timestamp": int(row["timestamp"]), "txId": row.get("txId", "")}
                    for row in rows
                    if row.get("symbol", "").upper() == "BTC" and row.get("status") == "completed"
                ]
                withdrawals.sort(key=lambda item: item["timestamp"])
                self._json(200, withdrawals)
                return
            if parsed.path in ("/api/analytics", "/api/export.csv"):
                start_ms, end_ms = _period(query)
                rows = parqet_rows(api, start_ms, end_ms, self.settings["quote"])
                if parsed.path == "/api/export.csv":
                    filename = "parqet_btc_" + str(start_ms) + "_" + str(end_ms) + ".csv"
                    self._send(200, parqet_csv(rows), "text/csv; charset=utf-8", filename)
                else:
                    result = _analytics_from_rows(api, self.settings, rows)
                    self._json(200, result)
                return
            self._json(404, {"error": "Not found"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            LOG.exception("Web UI request failed")
            self._json(502, {"error": "Bitvavo data could not be loaded. Check the container logs."})

    def log_message(self, fmt, *args):
        LOG.info("Web UI %s - %s", self.address_string(), fmt % args)


def _analytics_from_rows(api, settings, rows):
    quote = settings["quote"]
    buys = [row for row in rows if row["type"] == "Buy"]
    withdrawals = [row for row in rows if row["type"] == "TransferOut"]
    bought_btc = sum((Decimal(row["shares"]) for row in buys), Decimal(0))
    buy_volume = sum((Decimal(row["amount"]) for row in buys), Decimal(0))
    trade_fees = sum((Decimal(row["fee"]) for row in buys), Decimal(0))
    withdrawn_btc = sum((Decimal(row["shares"]) for row in withdrawals), Decimal(0))
    withdrawal_fees = sum((Decimal(row["fee"]) for row in withdrawals), Decimal(0))
    price = api.price("BTC-" + quote)
    balance = api.balance("BTC")
    return {
        "quote": quote,
        "snapshot": {
            "balance": str(balance),
            "price": str(price),
            "value": str(balance * price),
            "withdrawThreshold": str(settings["withdraw_threshold"]),
        },
        "summary": {
            "buyCount": len(buys),
            "boughtBtc": str(bought_btc),
            "buyVolume": str(buy_volume),
            "tradeFees": str(trade_fees),
            "averagePrice": str(buy_volume / bought_btc if bought_btc else Decimal(0)),
            "withdrawalCount": len(withdrawals),
            "withdrawnBtc": str(withdrawn_btc),
            "withdrawalFees": str(withdrawal_fees),
            "netBtc": str(bought_btc - withdrawn_btc),
        },
        "activities": rows,
    }


def serve(settings, port):
    handler = type("ConfiguredHandler", (Handler,), {"settings": settings})
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    LOG.info("Local portfolio web UI listening on container port %s.", port)
    server.serve_forever()
