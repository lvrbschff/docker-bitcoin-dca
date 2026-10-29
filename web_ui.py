"""Local read-only portfolio dashboard and on-demand Parqet export."""
import hmac
import html
import json
import logging
import os
import re
import requests
from datetime import datetime, timezone, timedelta
import calendar
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from secrets import token_urlsafe
from threading import RLock
import time
from concurrent.futures import ThreadPoolExecutor
import price_cache
from favicon_assets import FAVICON_ICO, FAVICON_PNG, APPLE_TOUCH_ICON_PNG, FAVICON_MASK_SVG
from analytics import report as build_report, totals_from_records
from activity import activity_page, activity_totals, parqet_interval_records, ledger_balance, withdrawal_records, read_report

from activity import bitcoin_activity_records, delete_activity_record, read_dashboard_snapshot
from reporting import parqet_aggregate_rows, parqet_csv


def withdrawal_forecast(settings, snapshot, monthly_deposit, now):
    """Project monthly deposits and purchases at a constant BTC price and order cost."""
    from decimal import ROUND_CEILING
    if not settings.get("withdrawals_enabled"):
        return None, "Automatic withdrawals disabled"
    if not settings.get("live"):
        return None, "Unavailable in simulation mode"
    try:
        monthly_deposit = Decimal(str(monthly_deposit))
        if not monthly_deposit.is_finite() or monthly_deposit < 0:
            return None, "Enter a non-negative monthly deposit"
        balance = Decimal(str(snapshot["btcBalance"]))
        price = Decimal(str(snapshot["price"]))
        threshold = Decimal(str(settings["withdraw_threshold"]))
        if price <= 0:
            return None, "Waiting for a market price"
        if balance * price >= threshold:
            return now.isoformat(), "Threshold reached; awaiting automatic withdrawal check"
        plan = snapshot["withdrawalForecastPlan"]
        amount = Decimal(plan["orderAmount"])
        cost = Decimal(plan["plannedCost"])
        budget = Decimal(plan["budget"])
        interval = max(60, int(plan["intervalSeconds"]))
        due = max(now, datetime.fromisoformat(snapshot["nextPurchaseAt"].replace("Z", "+00:00")))
        deposit = datetime.fromisoformat(plan["nextDepositAt"]).astimezone(ZoneInfo(settings.get("timezone", "UTC")))
        if amount <= 0 or cost <= 0 or budget < 0:
            return None, "Waiting for a purchase schedule"
        needed = int(((threshold / price - balance) / amount).to_integral_value(rounding=ROUND_CEILING))
        last_buy = None
        for _ in range(1200):
            slots = max(0, int((deposit.astimezone(timezone.utc) - due.astimezone(timezone.utc)).total_seconds() // interval) + 1)
            buys = min(int(budget // cost), slots)
            if needed <= buys:
                target = due + timedelta(seconds=(needed - 1) * interval)
                return target.isoformat(), "Estimate at the current price, fees and expected monthly deposits"
            if buys:
                last_buy = due + timedelta(seconds=(buys - 1) * interval)
                budget -= cost * buys
                needed -= buys
            if monthly_deposit == 0:
                return None, "Enter an expected monthly deposit to project further purchases"
            budget += monthly_deposit
            start = deposit
            year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
            deposit = start.replace(year=year, month=month,
                                   day=min(settings["deposit_day"], calendar.monthrange(year, month)[1]))
            possible = int(budget // cost)
            if possible:
                anchor = last_buy or start
                interval = max(60, int((deposit.astimezone(timezone.utc) - anchor.astimezone(timezone.utc)).total_seconds() / possible))
                due = max(start, anchor + timedelta(seconds=interval))
            else:
                due = deposit + timedelta(seconds=1)
        return None, "Estimate exceeds the 100-year forecast horizon"
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        return None, "Waiting for a purchase schedule"


LOG = logging.getLogger("bitcoin-dca.web")

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<title>Bitcoin DCA Bot Dashboard</title>
<rect x="1" y="1" width="62" height="62" rx="15" fill="#0b1020" stroke="#26324a" stroke-width="2"/>
<circle cx="32" cy="32" r="24" fill="#8bafff"/>
<g transform="rotate(14 32 32)" fill="none" stroke="#0b1020" stroke-width="4.5" stroke-linecap="square" stroke-linejoin="round">
<path d="M23 19v27M23 19h11c13 0 13 13 0 13H23m0 0h13c14 0 14 14 0 14H23"/>
<path d="M28 13v6m7-6v6M28 46v6m7-6v6"/>
</g>
</svg>"""

HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bitcoin DCA Bot Dashboard</title>
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="apple-touch-icon" sizes="180x180" href="/bitcoin-dca-icon-v3.png">
<link rel="mask-icon" href="/safari-pinned-tab-v3.svg" color="#8bafff">
<meta name="theme-color" content="#0b1020">
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2d;--line:#26324a;--text:#edf2ff;--muted:#9ba9c4;--accent:#8bafff;--green:#61d7ae;--red:#ff8f9e}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 15% 0%,#1b2a49 0,transparent 42%),var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
.dashboard-brand{display:flex;align-items:center;gap:12px;margin-bottom:6px;min-width:0}.dashboard-logo{display:block;width:48px;height:48px;flex:0 0 48px;border-radius:11px}.dashboard-brand h1{min-width:0;overflow-wrap:anywhere}
main{max-width:1180px;margin:0 auto;padding:36px 22px 64px}.top{display:flex;justify-content:space-between;gap:18px;align-items:center;margin-bottom:26px}.eyebrow{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}h1{font-size:30px;margin:4px 0}.sub{color:var(--muted);margin:0}
.bottom-actions{display:flex;justify-content:flex-end;margin-top:20px}.badge{border:1px solid var(--line);border-radius:999px;padding:8px 12px;color:var(--muted);font-size:12px}.top-actions{display:flex;flex-direction:column;align-items:flex-end;gap:10px}.top-actions form{margin:0}.withdraw-action{display:flex;flex-direction:column;align-items:flex-end;gap:4px;min-width:0}#withdrawMessage:empty{display:none}.top-actions .field{min-width:170px}.top-actions .field label{margin-bottom:5px}
.panel{background:linear-gradient(145deg,rgba(21,31,52,.96),rgba(15,22,39,.96));border:1px solid var(--line);border-radius:18px;padding:20px;margin:18px 0;box-shadow:0 14px 40px #0002}
.panel.activity-panel{margin:0;background:#0c1425a8;box-shadow:none}
.controls{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;align-items:end}.field label{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}select,input,button{font:inherit;color:var(--text);background:#0c1425;border:1px solid var(--line);border-radius:10px;padding:11px 12px;width:100%}button{width:auto;cursor:pointer;background:var(--accent);border-color:var(--accent);color:#081122;font-weight:700}button.secondary{background:transparent;color:var(--text);border-color:var(--line)}
.controls .field{width:100%;min-width:0;min-inline-size:0}
.controls input,.controls select,.controls>button{display:block;box-sizing:border-box;width:100%;max-width:100%;min-width:0;min-inline-size:0;height:46px;min-height:46px;line-height:1.25}
.controls input[type="datetime-local"]{appearance:none;-webkit-appearance:none;inline-size:100%;max-inline-size:100%;padding:10px 12px}
.controls input[type="datetime-local"]::-webkit-datetime-edit{min-width:0;padding:0}
.controls input[type="datetime-local"]::-webkit-datetime-edit-fields-wrapper{min-width:0;padding:0}
.controls input[type="datetime-local"]::-webkit-date-and-time-value{min-width:0;text-align:left}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.period-summary-grid{grid-template-columns:repeat(5,minmax(0,1fr))}.summary-secondary-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.card{padding:17px;border:1px solid var(--line);border-radius:14px;background:#0c1425a8}.card label{display:block;color:var(--muted);font-size:12px;margin-bottom:9px}.value{font-size:22px;font-weight:700;line-height:1.4;letter-spacing:-.02em}.small{font-size:12px;color:var(--muted);margin-top:4px}
h2{font-size:17px;margin:0 0 16px}.tablewrap{overflow:auto}table{border-collapse:collapse;width:100%;min-width:740px}th,td{text-align:left;padding:11px 10px;border-bottom:1px solid var(--line);white-space:nowrap}th{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}td.num{text-align:right;font-variant-numeric:tabular-nums}.positive{color:var(--green)}.negative{color:var(--red)}.notice{color:var(--muted);font-size:13px;margin-top:12px}.error{color:var(--red);margin:12px 0}.hidden{display:none}.activity-panel .activity-heading{justify-content:flex-end}
.activity-heading,.pagination,.settings-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.settings-heading h2{margin:0}.settings-heading .field{min-width:160px}.activity-heading h4{font-size:14px;font-weight:700;margin:0}.page-size{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px}.page-size select{width:auto;padding:7px 10px}.pagination{margin-top:12px}.pagination button{padding:8px 12px}.pagination .notice{margin:0;text-align:center}
main,.panel,.grid,.card,.field,.top>div{min-width:0}
main{width:100%}.top-actions{flex-shrink:0}.controls>*{min-width:0}
input,select{min-width:0;max-width:100%}.card label,.small,.notice,.error{overflow-wrap:anywhere}
.value{font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.time-value,.countdown-value{white-space:normal;overflow-wrap:anywhere}
.tablewrap{width:100%;max-width:100%;min-width:0;overscroll-behavior-x:contain;-webkit-overflow-scrolling:touch}
#activities td{padding:5px 10px}#activities button{padding:3px 9px;line-height:1.4;font-size:13px;border-radius:7px}
.page-jump{display:flex;align-items:center;gap:8px;margin:0;font-size:12px;color:var(--muted)}.page-jump input{width:76px;padding:7px 8px}.page-jump button{flex:none}
.pagination{flex-wrap:wrap}.pagination .notice{flex:1;min-width:100px}
@media(max-width:1100px){.period-summary-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.controls{grid-template-columns:repeat(3,minmax(0,1fr))}.controls>button{grid-column:1/-1}}
@media(max-width:850px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.top{align-items:flex-start}.top-actions .field{min-width:150px}}
@media(max-width:600px){
 main{padding:22px 12px 36px}.panel{padding:15px;margin:14px 0;border-radius:14px}
 .top{flex-direction:column;gap:18px;margin-bottom:20px}h1{font-size:26px}.sub{font-size:14px}
 .top-actions{width:100%;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center}
 .top-actions form,.top-actions .withdraw-action{grid-column:1/-1;justify-self:end}.top-actions .field{grid-column:1/-1;min-width:0}
 .controls{grid-template-columns:minmax(0,1fr)}.controls .field:first-child{grid-column:auto}
 input,select{font-size:16px}input,select,button{min-height:44px}
 .grid{gap:10px}.card{padding:13px}.card label{line-height:1.5;margin-bottom:7px}
 .value{font-size:19px;line-height:1.4}.time-card{grid-column:1/-1}
 .activity-heading,.settings-heading{align-items:flex-start;flex-wrap:wrap}
 .activity-heading h2{flex-basis:100%}.page-size{width:100%;justify-content:space-between}
 .page-size select{min-width:90px}.pagination{gap:10px}.pagination button{flex:1}
 .pagination .notice{order:3;flex-basis:100%;font-size:12px}
 .page-jump{order:4;flex-basis:100%;justify-content:center}.page-jump button{flex:none}
 th,td{padding:10px 8px;font-size:13px}.bottom-actions button{width:100%;white-space:normal}
}
@media(max-width:360px){.grid{grid-template-columns:minmax(0,1fr)}.time-card{grid-column:auto}}
.schedule-group{min-width:0}

.schedule-row-scroll{max-width:100%;overflow-x:auto;padding-bottom:4px}
.schedule-row-scroll:focus-visible{outline:2px solid var(--accent);outline-offset:4px}
.schedule-grid{grid-template-columns:repeat(4,minmax(220px,1fr))}
.schedule-grid .time-card{grid-column:auto}

.report-section{min-width:0}
.report-heading,.panel-heading{margin-bottom:18px}.report-heading h2,.panel-heading h2{font-size:21px;margin:0 0 5px}
.report-heading .sub{font-size:14px}.summary-subheading{font-size:17px;font-weight:700;line-height:1.4;margin:0 0 16px}
.period-summary-content,.schedule-group+.schedule-group,.summary-subheading.activity-subheading{
 margin-top:24px;padding-top:20px;border-top:1px solid var(--line)
}
.report-controls>.sub{margin-bottom:16px;font-size:14px}

@media(max-width:600px){.report-heading h2,.panel-heading h2{font-size:19px}}
sup{color:#fff}.footnote-ref{margin-left:3px}
.dashboard-footnotes{margin-top:30px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;line-height:1.7;overflow-wrap:anywhere}
.dashboard-footnotes h2{color:var(--muted);font-size:14px;margin:0 0 12px}.dashboard-footnotes ol{list-style:none;margin:0;padding:0}.dashboard-footnotes li{padding-left:0;margin-bottom:14px;scroll-margin-top:20px}.dashboard-footnotes strong{color:var(--muted);font-weight:600}.dashboard-footnotes p{margin:4px 0 0}
.export-feedback{margin:10px 0 0;text-align:right;color:var(--red);font-size:13px;line-height:1.6;overflow-wrap:anywhere}
.withdraw-feedback{max-width:min(320px,calc(100vw - 76px));margin:4px 0 0}.withdraw-feedback.success{color:var(--green)}\n@media(max-width:600px){.export-feedback{text-align:left}}

/* Shared title bars distinguish the three main panels from subsection headings. */
.panel>.panel-heading,.panel>.report-heading{
 margin:-20px -20px 20px;padding:10px 20px;
 background:linear-gradient(100deg,#223452,#192740);
 border-bottom:1px solid #354b70;border-radius:17px 17px 0 0;
}
.panel>.panel-heading h2,.panel>.report-heading h2{font-size:21px;font-weight:700;line-height:1.4;margin:0}
@media(max-width:600px){
 .panel>.panel-heading,.panel>.report-heading{margin:-15px -15px 20px;padding:8px 15px;border-radius:13px 13px 0 0}
 .panel>.panel-heading h2,.panel>.report-heading h2{font-size:19px}
}

/* Native disclosure controls keep panel contents and state intact while collapsed. */
details.panel>summary{list-style:none;cursor:pointer;user-select:none}
details.panel>summary::-webkit-details-marker{display:none}
details.panel>summary h2{display:flex;align-items:center;justify-content:space-between;gap:12px}
details.panel>summary h2::after{content:"+";font-size:22px;font-weight:400;color:var(--accent)}
details.panel[open]>summary h2::after{content:"−"}
details.panel:not([open])>summary{margin-bottom:-20px;border-bottom:0;border-radius:17px}
details.panel>summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
@media(max-width:600px){details.panel:not([open])>summary{margin-bottom:-15px;border-radius:13px}}

.compact-brand-bar{position:fixed;z-index:50;inset:0 0 auto;background:rgba(11,16,32,.96);border-bottom:1px solid var(--line);backdrop-filter:blur(12px);opacity:0;transform:translateY(-100%);visibility:hidden;transition:transform .22s ease,opacity .22s ease,visibility .22s}
.compact-brand-inner{max-width:1180px;margin:0 auto;padding:10px 22px;display:flex;align-items:center;gap:12px}
.compact-brand-inner img{display:block;width:36px;height:36px;border-radius:9px;flex-shrink:0}
.compact-brand-inner span{font-size:20px;font-weight:700;line-height:1.3}
body.compact-brand-visible .compact-brand-bar{opacity:1;transform:translateY(0);visibility:visible}
.top-actions{transition:opacity .2s ease,transform .2s ease,visibility .2s}
body.header-actions-hidden .top-actions{opacity:0;transform:translateY(-8px);visibility:hidden;pointer-events:none}
@media(max-width:600px){.compact-brand-inner{padding:9px 12px;gap:10px}.compact-brand-inner span{font-size:17px}}
@media(prefers-reduced-motion:reduce){.compact-brand-bar,.top-actions{transition:none}}

/* Compact header controls share one row and wrap only when space is limited. */
.top{align-items:flex-start;flex-wrap:wrap}
.top-actions{flex-direction:row;align-items:flex-end;justify-content:flex-end;flex-wrap:wrap;gap:8px;margin-left:auto}
.top-actions button,.top-actions select{padding:8px 10px;min-height:38px;font-size:13px;border-radius:9px}
.top-actions .field{min-width:130px}
.top-actions .field label{margin-bottom:4px;font-size:11px}
@media(max-width:600px){
 .top-actions{margin-left:0;justify-content:flex-start;align-items:flex-end;width:100%}
 .top-actions .field{min-width:120px}
 .top-actions button,.top-actions select{min-height:44px}
}

button{transition:background-color .16s ease,border-color .16s ease,box-shadow .16s ease,filter .16s ease,transform .16s ease}
button:disabled{cursor:not-allowed;filter:none;transform:none;box-shadow:none}
button:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
@media(hover:hover) and (pointer:fine){
 button:not(:disabled):hover{filter:brightness(1.13);border-color:var(--accent);box-shadow:0 0 8px #8bafff55,0 4px 20px #8bafff66;transform:translateY(-1px)}
 button:not(:disabled):active{transform:translateY(0);box-shadow:0 1px 5px #8bafff20}
}
@media(prefers-reduced-motion:reduce){button{transition:none}button:not(:disabled):hover{transform:none}}

.btc-balance-card{display:flex;flex-direction:column;align-items:flex-start}
.btc-balance-card .withdraw-action{align-items:flex-start;width:100%;margin-top:auto;padding-top:12px}
.btc-balance-card #manualWithdraw{font-size:12px;padding:6px 9px;border-radius:8px;min-height:32px}
.btc-balance-card .withdraw-feedback{max-width:100%;text-align:left}
@media(max-width:600px){.btc-balance-card #manualWithdraw{min-height:44px}}

.dashboard-footnotes li>strong{display:block;font-size:14px;font-weight:700;letter-spacing:.01em;color:#c2cde0;margin-bottom:10px}
.note-subsection{margin-top:20px}
.dashboard-footnotes .note-subheading{font-size:14px;font-weight:700;letter-spacing:.01em;color:#c2cde0;line-height:1.5;margin:0 0 10px}
</style>
</head>
<body>
<div class="compact-brand-bar" aria-hidden="true"><div class="compact-brand-inner"><img src="/bitcoin-dca-icon-v3.png" width="36" height="36" alt=""><span>Bitcoin DCA Bot Dashboard</span></div></div><main>
<header class="top"><div><div class="dashboard-brand"><img class="dashboard-logo" src="/bitcoin-dca-icon-v3.png" width="48" height="48" alt=""><h1>Bitcoin DCA Bot Dashboard</h1></div></div><div class="top-actions"><div class="field"><label for="displayCurrency">Display currency</label><select id="displayCurrency"><option value="EUR">Euro (EUR)</option><option value="USD">US Dollar (USD)</option></select></div><form method="post" action="/logout"><button type="submit">Log out</button></form></div></header>
<details open class="panel" aria-labelledby="configurationHeading">
<summary class="panel-heading"><h2 id="configurationHeading">Bot configuration</h2></summary>
<div class="grid">
<div class="card"><label>Exchange</label><div class="value" id="settingExchange">—</div></div>
<div class="card"><label>Quote currency</label><div class="value" id="settingQuoteCurrency">—</div></div>
<div class="card"><label>Trading mode</label><div class="value" id="tradingMode">—</div></div>
<div class="card"><label>Purchase amount</label><div class="value" id="buyAmount">—</div><div class="small" id="buyAmountNote"></div></div>
<div class="card"><label>Deposit day (day of month)</label><div class="value" id="depositDay">—</div></div>
<div class="card"><label>Automatic withdrawals</label><div class="value" id="withdrawEnabled">—</div></div>
<div class="card"><label>Withdrawal threshold</label><div class="value" id="threshold">—</div><div class="small" id="thresholdNote">Configured threshold</div></div>
<div class="card"><label>Withdrawal target</label><div class="value" id="withdrawalTarget">—</div></div>
</div>
</details>
<details open class="panel purchase-schedule" aria-labelledby="scheduleHeading">
<summary class="panel-heading"><h2 id="scheduleHeading">Schedule</h2></summary>
<section class="schedule-group" aria-labelledby="purchaseHeading">
<h3 id="purchaseHeading" class="summary-subheading">Purchase</h3>
<div class="schedule-row-scroll" role="region" aria-label="Purchase schedule cards" tabindex="0"><div class="grid schedule-grid">
<div class="card"><label>Current BTC price</label><div class="value" id="spot">—</div><div class="small" id="spotUpdated">Kraken public price page</div></div>
<div class="card"><label id="quoteBalanceLabel">Available fiat balance on exchange</label><div class="value" id="quoteBalance">—</div><div class="small" id="quoteBalanceUpdated">Waiting for account check</div></div>
<div class="card time-card"><label>Next purchase <span id="nextPurchaseStatus">Scheduled</span></label><div class="value time-value" id="nextPurchaseAt">—</div></div>
<div class="card time-card"><label>Time until next purchase</label><div class="value countdown-value" id="nextPurchaseCountdown">—</div></div>
</div></div>
</section>
<section class="schedule-group" aria-labelledby="withdrawalHeading">
<h3 id="withdrawalHeading" class="summary-subheading">Withdrawal</h3>
<div class="schedule-row-scroll" role="region" aria-label="Withdrawal schedule cards" tabindex="0"><div class="grid schedule-grid">
<div class="card btc-balance-card"><label>Current BTC balance</label><div class="value" id="exchangeBtcBalance">—</div><div class="small" id="exchangeBtcValue">—</div><div class="small" id="exchangeBtcUpdated">Waiting for account check</div><div class="withdraw-action"><button id="manualWithdraw" type="button" aria-describedby="withdrawal-note withdrawalFeedback">Withdraw BTC manually</button><p id="withdrawalFeedback" class="export-feedback withdraw-feedback hidden" role="alert"></p></div></div>
<div class="card"><div class="field"><label for="monthlyDeposit">Expected monthly deposit (<span id="monthlyDepositCurrency">EUR</span>)</label><input id="monthlyDeposit" type="number" min="0" step="0.01" inputmode="decimal" value="0"></div><div class="small">Forecast only · assumes deposits on the configured deposit day · saved in this browser</div></div>
<div class="card time-card"><label>Estimated automatic withdrawal date</label><div class="value time-value" id="estimatedWithdrawalAt">—</div><div class="small" id="estimatedWithdrawalStatus">Waiting for account check</div></div>
<div class="card time-card"><label>Time until automatic withdrawal</label><div class="value countdown-value" id="withdrawalCountdown">—</div><div class="small" id="withdrawalCountdownStatus">Estimate at the current BTC price and purchase interval</div></div>
</div></div>
</section>
</details>
<details open class="panel report-section report-controls" aria-labelledby="reportHeading">
<summary class="report-heading"><h2 id="reportHeading">Period Analysis</h2></summary>
<p class="sub">Choose a custom date range or an interval between withdrawals to review activity and prepare a Parqet export.</p>
<div class="controls">
<div class="field"><label for="interval">Date range</label><select id="interval"></select></div>
<div class="field"><label for="start">From</label><input id="start" type="datetime-local"></div>
<div class="field"><label for="end">To</label><input id="end" type="datetime-local"></div>
<button id="refresh">Load report</button>
</div><div id="error" class="error hidden"></div>
<div class="period-summary-content" role="region" aria-labelledby="periodSummaryHeading"><h3 id="periodSummaryHeading" class="summary-subheading">Selected period summary</h3><div class="grid period-summary-grid">
<div class="card"><label>BTC purchases</label><div class="value" id="buyCount">—</div><div class="small" id="buyBtc"></div></div>
<div class="card"><label>Purchase volume</label><div class="value" id="buyVolume">—</div><div class="small" id="avgPrice"></div></div>
<div class="card"><label>Trading fees</label><div class="value" id="tradeFees">—</div></div>
<div class="card"><label>Withdrawals</label><div class="value" id="withdrawBtc">—</div><div class="small" id="withdrawCount"></div></div>
<div class="card"><label>Withdrawal fees</label><div class="value" id="withdrawFees">—</div></div>
</div><div class="grid summary-secondary-grid" style="margin-top:14px">
<div class="card"><label>Profit / loss on period purchases</label><div class="value" id="profitLoss">—</div><div class="small" id="profitLossBasis">Current value of BTC bought in this period minus trading fees</div></div>
<div class="card"><label>Net BTC in period</label><div class="value" id="netBtc">—</div><div class="small">Purchases minus gross BTC withdrawals</div></div>
</div>
<h3 class="summary-subheading activity-subheading" id="activityHeading">Period activity</h3>
<section class="panel activity-panel" aria-labelledby="activityHeading"><div class="activity-heading"><div class="page-size"><label for="pageSize">Entries per page</label><select id="pageSize"><option>5</option><option selected>10</option><option>20</option><option>50</option><option>100</option><option>500</option><option>1000</option></select></div></div><div class="tablewrap"><table><thead><tr><th>Date</th><th>Type</th><th>BTC</th><th>Price</th><th>Value</th><th>Fee</th><th>Action</th></tr></thead><tbody id="activities"></tbody></table></div><div class="pagination"><button class="secondary" id="previousPage">Previous</button><span id="pageInfo" class="notice"></span><button class="secondary" id="nextPage">Next</button><form class="page-jump" id="pageJump" aria-label="Jump to activity page"><label for="pageNumber">Go to page</label><input id="pageNumber" type="number" min="1" step="1" value="1" required><button class="secondary" id="goToPage" type="submit">Go</button></form></div></section></div>
<div class="bottom-actions"><button id="download" aria-describedby="exportMessage">Download Parqet CSV</button></div>
<p id="exportMessage" class="export-feedback hidden" role="alert"></p>
</details>
<footer class="dashboard-footnotes" aria-labelledby="notesHeading">
<h2 id="notesHeading">Notes</h2>
<ol>
<li id="schedule-note"><strong>Schedule and withdrawals</strong><p>Purchase interval = time from the last purchase to the next deposit day ÷ purchases affordable with the current fiat balance, including estimated fees and a planning buffer. Without a previous purchase, planning starts from now. Next purchase = last purchase + interval; elapsed time reduces the wait. Open orders block further purchases. Balances reflect the last account check; the display BTC price comes from Kraken’s public converter pages.</p><p>The automatic withdrawal threshold uses all available exchange BTC valued in the purchase currency, including BTC acquired outside this bot. Remaining purchases = (threshold ÷ current BTC price − available BTC) ÷ BTC per purchase, rounded up. The forecast projects these purchases using the current budget, price and order costs. Expected monthly deposit adds assumed funds on each deposit day; unused funds carry over and intervals are recalculated for actual calendar months. This browser-saved setting affects only the forecast. The estimated date is the final required purchase; withdrawal follows an eligible bot check. If the threshold is already reached, the estimate is now. Countdown = estimated date − current time, with a minimum of zero. Estimates require automatic withdrawals and live trading; future prices, fees, deposits and execution may change them.</p><div id="withdrawal-note" class="note-subsection"><h3 class="note-subheading">Manual withdrawal</h3><p id="withdrawalDestination">Loading withdrawal configuration…</p><p id="withdrawMessage" role="status" aria-live="polite"></p><p>After confirmation, sends all available BTC minus the exchange withdrawal fee. Requires LIVE_TRADING; the automatic withdrawal setting and threshold do not apply. Set KRAKEN_WITHDRAW_KEY in .env for Kraken, or WITHDRAW_ADDRESS and WITHDRAW_NETWORK for Bitvavo. Withdrawals appear immediately in Period activity; only confirmed ones qualify for export.</p></div></li>
<li id="report-note"><strong>Period analysis and Parqet export</strong><p>Choose a custom range or an interval between withdrawals. Historical amounts are converted from their original currencies to the display currency. The EUR CSV exports only the complete cycle after the previous confirmed withdrawal through the last confirmed withdrawal in the selected range. It includes real purchase costs and trading fees, and the ending withdrawal’s net BTC after network fees. Later purchases, simulations and unconfirmed or failed withdrawals are excluded. A confirmed withdrawal and at least one real purchase in its cycle are required. Delete affects local history only; it does not reverse an exchange order.</p></li>
</ol>
</footer>
</main>
<script data-ui-scroll>
(()=>{
 const brand=document.querySelector(".top .dashboard-brand");
 if(!brand)return;
 let pending=false;
 function updateHeader(){
  document.body.classList.toggle("compact-brand-visible",window.scrollY>40);
  document.body.classList.toggle("header-actions-hidden",window.scrollY>40);
  pending=false;
 }
 window.addEventListener("scroll",()=>{if(!pending){pending=true;requestAnimationFrame(updateHeader)}},{passive:true});
 window.addEventListener("resize",updateHeader);
 updateHeader();
})();
</script>
<script>\nconst $=id=>document.getElementById(id);
let quote="EUR", displayCurrency="", fxRate=1, activityRows=[], currentPage=1, activityTotal=0, activityPages=1, activityRates={};
function localInput(ms){const d=new Date(ms);d.setMinutes(d.getMinutes()-d.getTimezoneOffset());return d.toISOString().slice(0,16)}
function parseInput(el){return new Date(el.value).getTime()}
function money(v,c){try{return new Intl.NumberFormat("en-GB",{style:"currency",currency:c,maximumFractionDigits:2}).format(Number(v||0))}catch(e){return Number(v||0).toFixed(2)+" "+c}}
function btc(v){return Number(v||0).toLocaleString("en-GB",{minimumFractionDigits:0,maximumFractionDigits:8})+" BTC"}
function dateLabel(ms){return new Date(ms).toLocaleString("en-GB")}
let exchangeBtcBalance=null, balanceDisplayPrice=null, balanceDisplayCurrency=null;
function renderExchangeBtcValue(){
 $("exchangeBtcValue").textContent=exchangeBtcBalance!==null&&balanceDisplayPrice>0&&balanceDisplayCurrency===displayCurrency?money(exchangeBtcBalance*balanceDisplayPrice,displayCurrency):"—";
}
let nextPurchaseTimestamp=null, estimatedWithdrawalTimestamp=null, scheduleClockOffset=0;
function renderCountdown(id, timestamp){
 const output=$(id);
 if(timestamp===null){output.textContent="—";return}
 let seconds=Math.max(0,Math.ceil((timestamp-Date.now()-scheduleClockOffset)/1000));
 if(!seconds){output.textContent="Due now";return}
 const days=Math.floor(seconds/86400);seconds%=86400;
 const hours=Math.floor(seconds/3600);seconds%=3600;
 const minutes=Math.floor(seconds/60),remaining=seconds%60;
 output.textContent=(days?days+"d ":"")+String(hours).padStart(2,"0")+"h "+String(minutes).padStart(2,"0")+"m "+String(remaining).padStart(2,"0")+"s";
}
function renderPurchaseCountdown(){
 renderCountdown("nextPurchaseCountdown",nextPurchaseTimestamp);
 renderCountdown("withdrawalCountdown",estimatedWithdrawalTimestamp);
 if(estimatedWithdrawalTimestamp===null)$("withdrawalCountdown").textContent="Not estimated";
}
async function refreshPurchaseSchedule(){
 try{
  const response=await fetch("/api/schedule?monthlyDeposit="+encodeURIComponent($("monthlyDeposit").value||"0"));
  if(response.status===401){window.location.reload();return}
  if(!response.ok)throw new Error("Schedule unavailable");
  const schedule=await response.json(),target=Date.parse(schedule.nextPurchaseAt),serverTime=Date.parse(schedule.serverTime);
  $("monthlyDepositCurrency").textContent=schedule.quote;
  $("quoteBalanceLabel").textContent="Available "+schedule.quote+" balance on exchange";
  const balanceKnown=schedule.quoteBalance!==null&&schedule.quoteBalance!==undefined&&Number.isFinite(Number(schedule.quoteBalance));
  $("quoteBalance").textContent=balanceKnown?money(schedule.quoteBalance,schedule.quote):"—";
  const checkedAt=Date.parse(schedule.balanceUpdatedAt);
  $("quoteBalanceUpdated").textContent=balanceKnown
   ?(Number.isFinite(checkedAt)?"Last checked "+new Date(checkedAt).toLocaleString("en-GB",{timeZone:schedule.timezone||"UTC"}):"Last account check")
   :"Waiting for account check";
  nextPurchaseTimestamp=Number.isFinite(target)?target:null;
  scheduleClockOffset=Number.isFinite(serverTime)?serverTime-Date.now():0;
  $("nextPurchaseAt").textContent=nextPurchaseTimestamp===null?"Not scheduled":new Date(nextPurchaseTimestamp).toLocaleString("en-GB",{timeZone:schedule.timezone||"UTC",timeZoneName:"short"});
  $("nextPurchaseStatus").textContent=schedule.nextPurchaseStatus||"Waiting for the bot schedule";
  exchangeBtcBalance=schedule.btcBalance!=null&&Number.isFinite(Number(schedule.btcBalance))?Number(schedule.btcBalance):null;
  $("exchangeBtcBalance").textContent=exchangeBtcBalance===null?"—":btc(exchangeBtcBalance);
  $("exchangeBtcUpdated").textContent=exchangeBtcBalance===null?"Waiting for account check":(Number.isFinite(checkedAt)?"Last checked "+new Date(checkedAt).toLocaleString("en-GB",{timeZone:schedule.timezone||"UTC"}):"Last account check");
  renderExchangeBtcValue();
  const withdrawalTarget=Date.parse(schedule.estimatedWithdrawalAt);
  estimatedWithdrawalTimestamp=Number.isFinite(withdrawalTarget)?withdrawalTarget:null;
  $("estimatedWithdrawalAt").textContent=estimatedWithdrawalTimestamp===null?"Not estimated":new Date(estimatedWithdrawalTimestamp).toLocaleString("en-GB",{timeZone:schedule.timezone||"UTC",timeZoneName:"short"});
  $("estimatedWithdrawalStatus").textContent=schedule.estimatedWithdrawalStatus||"Waiting for account check";
  $("withdrawalCountdownStatus").textContent=estimatedWithdrawalTimestamp===null?$("estimatedWithdrawalStatus").textContent:"Estimate at the current BTC price and purchase interval";
  renderPurchaseCountdown();
 }catch(e){$("nextPurchaseStatus").textContent="Could not refresh the bot schedule"}
}
const savedMonthlyDeposit=localStorage.getItem("bitcoinDcaMonthlyDeposit");
if(savedMonthlyDeposit!==null&&Number.isFinite(Number(savedMonthlyDeposit))&&Number(savedMonthlyDeposit)>=0)$("monthlyDeposit").value=savedMonthlyDeposit;
$("monthlyDeposit").addEventListener("change",()=>{
 const amount=Number($("monthlyDeposit").value);
 if(!Number.isFinite(amount)||amount<0){$("monthlyDeposit").reportValidity();return}
 localStorage.setItem("bitcoinDcaMonthlyDeposit",String(amount));refreshPurchaseSchedule();
});
setInterval(renderPurchaseCountdown,1000);
setInterval(refreshPurchaseSchedule,30000);
refreshPurchaseSchedule();

function showError(message){$("error").textContent=message||"";$("error").classList.toggle("hidden",!message)}
function selectedPeriod(){const start=parseInput($("start")),end=parseInput($("end"));if(!Number.isFinite(start)||!Number.isFinite(end)||start>=end)throw new Error("Please choose a valid date range.");return {start:start,end:end}}
function applySelectedInterval(){if(!$("interval").value)return;const p=$("interval").value.split(",").map(Number);$("start").value=localInput(p[0]);$("end").value=localInput(p[1])}
async function loadIntervals(){
 const response=await fetch("/api/withdrawals");if(!response.ok)throw new Error("Could not load withdrawal history.");
 const withdrawals=await response.json();const select=$("interval");select.innerHTML="";
 const custom=document.createElement("option");custom.value="";custom.textContent="Custom date range";select.appendChild(custom);
 for(let i=1;i<withdrawals.length;i++){const a=withdrawals[i-1],b=withdrawals[i],o=document.createElement("option");o.value=a.timestamp+","+b.timestamp;o.textContent=dateLabel(a.timestamp)+" → "+dateLabel(b.timestamp);select.appendChild(o)}
 const now=Date.now();if(withdrawals.length>=2){select.value=withdrawals[withdrawals.length-2].timestamp+","+withdrawals[withdrawals.length-1].timestamp;applySelectedInterval()}
 else {select.value="";$("start").value=localInput(withdrawals.length===1?withdrawals[0].timestamp:now-90*86400000);$("end").value=localInput(now)}
}
$("interval").addEventListener("change",()=>{applySelectedInterval();currentPage=1;loadAnalytics()});
function renderActivities(){
 const body=$("activities");body.innerHTML="";
 const pageSize=Number($("pageSize").value),total=activityTotal,totalPages=activityPages;
 currentPage=Math.min(Math.max(1,currentPage),totalPages);
 const start=(currentPage-1)*pageSize,end=Math.min(start+pageSize,total);
 for(const row of activityRows){
  const rate=Number.isFinite(activityRates[row.currency])?activityRates[row.currency]:1,currency=Number.isFinite(activityRates[row.currency])?displayCurrency:row.currency;
  const tr=document.createElement("tr"),type=row.simulated?"Simulated buy (dry run)":(row.type==="TransferOut"?"Withdrawal · "+(row.withdrawalStatus||"unverified"):row.type),vals=[dateLabel(Date.parse(row.datetime)),type,btc(row.shares),money(Number(row.price)*rate,currency)+"/BTC",money(Number(row.amount)*rate,currency),(row.simulated?"~":"")+money(Number(row.fee)*rate,currency)];
  vals.forEach((v,i)=>{const td=document.createElement("td");td.textContent=v;if(i>=2)td.className="num";tr.appendChild(td)});
  const action=document.createElement("td");
  if(row.type==="Buy"){const button=document.createElement("button");button.className="secondary";button.textContent="Delete";button.addEventListener("click",async()=>{if(!confirm("Delete this purchase from local bot history? This does not reverse an exchange order."))return;try{const response=await fetch("/api/activity/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({activityId:row.activityId})});const result=await response.json();if(!response.ok)throw new Error(result.error||"Could not delete purchase.");await loadAnalytics()}catch(e){showError(e.message)}});action.appendChild(button)}
  action.className="num";tr.appendChild(action);body.appendChild(tr)
 }
 if(!total){const tr=document.createElement("tr"),td=document.createElement("td");td.colSpan=7;td.textContent="No BTC activity in this period.";td.className="notice";tr.appendChild(td);body.appendChild(tr)}
 $("pageInfo").textContent=total?("Showing "+(start+1)+"–"+end+" of "+total+" · Page "+currentPage+" of "+totalPages):"No entries";
 $("previousPage").disabled=currentPage<=1;$("nextPage").disabled=currentPage>=totalPages;
 $("pageNumber").value=currentPage;$("pageNumber").max=totalPages;
 $("pageNumber").disabled=!total;$("goToPage").disabled=!total;
}
$("pageSize").addEventListener("change",()=>{currentPage=1;loadAnalytics()});
$("previousPage").addEventListener("click",()=>{if(currentPage>1){currentPage--;loadAnalytics()}});
$("nextPage").addEventListener("click",()=>{currentPage++;loadAnalytics()});
$("pageJump").addEventListener("submit",event=>{
 event.preventDefault();const page=Number($("pageNumber").value);
 if(!activityTotal||!Number.isInteger(page)||page<1||page>activityPages)return;
 if(page!==currentPage){currentPage=page;loadAnalytics()}
});
$("displayCurrency").addEventListener("change",()=>{displayCurrency=$("displayCurrency").value;renderExchangeBtcValue();localStorage.setItem("bitcoinDcaDisplayCurrency",displayCurrency);loadAnalytics()});
async function loadAnalytics(){
 try{showError("");const p=selectedPeriod();const response=await fetch("/api/analytics?start="+p.start+"&end="+p.end+"&page="+currentPage+"&limit="+$("pageSize").value);const d=await response.json();if(!response.ok)throw new Error(d.error||"Could not load the report.");quote=d.quote;
 if(!displayCurrency){const saved=localStorage.getItem("bitcoinDcaDisplayCurrency");displayCurrency=["EUR","USD"].includes(saved)?saved:(["EUR","USD"].includes(quote)?quote:"EUR")}
 $("displayCurrency").value=displayCurrency;
 const ex=d.exchange==="KRAKEN"?"Kraken":d.exchange==="BITVAVO"?"Bitvavo":d.exchange;document.title="Bitcoin DCA Bot Dashboard";
 let livePrice=Number((d.snapshot||{}).price||0),currencyRates={[quote]:1};
 const currencies=Object.keys(d.summaryByCurrency||{});
 try{
  const priceResponse=await fetch("/api/market-price?currency="+displayCurrency+"&currencies="+encodeURIComponent(currencies.join(",")));const market=await priceResponse.json();if(!priceResponse.ok)throw new Error(market.error||"Could not load current BTC price.");
  fxRate=Number(market.conversionRate);livePrice=Number(market.price);currencyRates=Object.fromEntries(Object.entries(market.conversionRates||{[quote]:fxRate})
   .map(([currency,rate])=>[currency,Number(rate)]).filter(([,rate])=>Number.isFinite(rate)&&rate>0));
  currencyRates[displayCurrency]=1;
  $("spotUpdated").textContent=(market.stale?"Cached Kraken price · fetched ":"Kraken public price page · fetched ")+new Date(Number(market.updatedAt)*1000).toLocaleTimeString("en-GB");
 }catch(priceError){
  displayCurrency=quote;fxRate=1;$("displayCurrency").value=quote;
  $("spotUpdated").textContent=livePrice>0?"Last price saved by the bot":"Live price unavailable";
  showError("Could not fetch a live BTC price. Showing saved activity and values in "+quote+" where available.");
 }
 balanceDisplayPrice=livePrice;balanceDisplayCurrency=displayCurrency;renderExchangeBtcValue();
 $("spot").textContent=livePrice>0?money(livePrice,displayCurrency)+"/BTC":"—";
 const converted={buyVolume:0,tradeFees:0,withdrawalFees:0};
 let moneyAvailable=true;
 for(const [currency,values] of Object.entries(d.summaryByCurrency||{})){
  if(!Number.isFinite(currencyRates[currency])){moneyAvailable=false;continue}
  for(const key of Object.keys(converted))converted[key]+=Number(values[key]||0)*currencyRates[currency];
 }
 if(!moneyAvailable)showError("A required currency conversion is unavailable; monetary totals are hidden.");
 if(livePrice>0&&moneyAvailable){
  const periodPurchaseValue=Number(d.summary.boughtBtc)*livePrice;
  const periodPurchaseCosts=converted.buyVolume+converted.tradeFees;
  const profitLoss=periodPurchaseValue-periodPurchaseCosts;
  $("profitLoss").textContent=money(profitLoss,displayCurrency);
  $("profitLoss").className="value "+(profitLoss>=0?"positive":"negative");
 }else{$("profitLoss").textContent="—";$("profitLoss").className="value"}
 $("settingExchange").textContent=ex;$("settingQuoteCurrency").textContent=quote;$("tradingMode").textContent=d.configuration.liveTrading?"Live":"Dry run";
 const amountSnapshot=d.snapshot||{};
 const effectiveMinimum=amountSnapshot.exchange===d.exchange&&amountSnapshot.quote===quote?Number((amountSnapshot.withdrawalForecastPlan||{}).orderAmount):NaN;
 $("buyAmount").textContent=Number(d.configuration.buyAmount)===0
  ?(Number.isFinite(effectiveMinimum)&&effectiveMinimum>0?btc(effectiveMinimum):"—")
  :btc(d.configuration.buyAmount);
 $("buyAmountNote").textContent=Number(d.configuration.buyAmount)===0?"(exchange minimum)"+(Number.isFinite(effectiveMinimum)&&effectiveMinimum>0?"":" · waiting for account check"):"";
 $("depositDay").textContent=String(d.configuration.depositDay);$("withdrawEnabled").textContent=d.configuration.withdrawalsEnabled?"Enabled":"Disabled";
 $("threshold").textContent=money(Number(d.configuration.withdrawalThreshold)*fxRate,displayCurrency);$("thresholdNote").textContent="Threshold in "+quote+", shown in "+displayCurrency;
 $("buyCount").textContent=d.summary.buyCount;$("buyBtc").textContent=btc(d.summary.boughtBtc);
 $("buyVolume").textContent=moneyAvailable?money(converted.buyVolume,displayCurrency):"—";$("avgPrice").textContent=moneyAvailable?"Average: "+money(Number(d.summary.boughtBtc)>0?converted.buyVolume/Number(d.summary.boughtBtc):0,displayCurrency)+"/BTC":"Conversion unavailable";
 $("tradeFees").textContent=moneyAvailable?money(converted.tradeFees,displayCurrency):"—";$("withdrawBtc").textContent=btc(d.summary.withdrawnBtc);$("withdrawCount").textContent=d.summary.withdrawalCount+" confirmed · "+(d.summary.pendingWithdrawalCount||0)+" pending";
 $("withdrawFees").textContent=moneyAvailable?money(converted.withdrawalFees,displayCurrency):"—";$("netBtc").textContent=btc(d.summary.netBtc);
 activityRates=currencyRates;activityRows=d.activities;activityTotal=d.pagination.total;activityPages=d.pagination.pages;currentPage=d.pagination.page;renderActivities();
 }catch(e){showError(e.message)}
}
$("refresh").addEventListener("click",()=>{currentPage=1;loadAnalytics()});
$("download").addEventListener("click",async()=>{
 const button=$("download"),message=$("exportMessage");
 message.textContent="";message.classList.add("hidden");
 button.disabled=true;button.textContent="Generating CSV…";
 try{
  const p=selectedPeriod(),response=await fetch("/api/export.csv?start="+p.start+"&end="+p.end);
  if(!response.ok){const d=await response.json();throw new Error(d.error||"Could not generate the CSV export.")}
  const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement("a");
  a.href=url;a.download="parqet_btc_"+new Date(p.start).toISOString().slice(0,10)+"_"+new Date(p.end).toISOString().slice(0,10)+".csv";
  a.click();URL.revokeObjectURL(url);
 }catch(e){message.textContent=e.message;message.classList.remove("hidden")}
 finally{button.disabled=false;button.textContent="Download Parqet CSV"}
});

async function withdrawalPost(path, body){
 const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-DCA-Action":"withdrawal"},body:JSON.stringify(body)});
 const result=await response.json();if(!response.ok)throw new Error(result.error||"Withdrawal request failed.");
 if(!result.jobId)return result;
 $("withdrawMessage").textContent="Request queued. Waiting for the account worker…";
 for(let i=0;i<600;i++){
  const check=await fetch("/api/withdrawal/jobs/"+encodeURIComponent(result.jobId));const job=await check.json();
  if(!check.ok)throw new Error(job.error||"Could not check the request. Check exchange status before trying again.");
  if(job.state==="failed")throw new Error(job.error);
  if(job.state==="completed")return job.result;
  await new Promise(resolve=>setTimeout(resolve,1000));
 }
 throw new Error("The request is still unresolved. Check exchange status before starting another withdrawal.");
}
async function loadWithdrawalConfiguration(){
 const response=await fetch("/api/withdrawal/config");const d=await response.json();
 if(!response.ok)throw new Error(d.error||"Could not load withdrawal configuration.");
 $("withdrawalDestination").textContent=d.destination;
 const target=d.target||"";
 $("withdrawalTarget").textContent=target?(d.exchange==="BITVAVO"&&target.length>22?target.slice(0,10)+"…"+target.slice(-8):target):"Not configured";
 $("withdrawalTarget").title=target;
 $("withdrawMessage").textContent=d.reason||"Manual withdrawals are available. Automatic withdrawals may remain disabled.";
 return d;
}
function showWithdrawalMessage(message,error=true){
 $("withdrawMessage").textContent=message;
 const feedback=$("withdrawalFeedback");
 feedback.textContent=message;feedback.classList.remove("hidden");
 feedback.classList.toggle("success",!error);
}
$("manualWithdraw").addEventListener("click",async()=>{
 const button=$("manualWithdraw");button.disabled=true;\n $("withdrawalFeedback").textContent="";$("withdrawalFeedback").classList.add("hidden");
 try{
 const configuration=await loadWithdrawalConfiguration();
 if(!configuration.enabled){showWithdrawalMessage(configuration.reason||"Manual withdrawal is not configured. See Notes.");return}
 const p=await withdrawalPost("/api/withdrawal/preview",{});
 const fee=p.feeBtc!==null&&p.feeBtc!==undefined?"Withdrawal fee: "+p.feeBtc+" BTC\\nNet sent: "+p.netBtc+" BTC":"Bitvavo deducts its withdrawal fee from this balance; the final fee is returned on submission.";
 if(!confirm("Confirm BTC withdrawal?\\n\\n"+p.destination+"\\nAvailable balance to withdraw: "+p.availableBtc+" BTC\\n"+fee+"\\n\\nThis transfer cannot be reversed. Submit withdrawal?"))return;
 $("withdrawMessage").textContent="Submitting withdrawal…";
 const result=await withdrawalPost("/api/withdrawal/confirm",{token:p.token});showWithdrawalMessage(result.message,false);
 $("interval").value="";$("end").value=localInput(Date.now()+60000);currentPage=1;await loadAnalytics();
 }catch(e){showWithdrawalMessage(e.message)}
 finally{button.disabled=false}
});
loadWithdrawalConfiguration().catch(e=>{$("withdrawMessage").textContent=e.message});

(async()=>{try{await loadIntervals();await loadAnalytics()}catch(e){showError(e.message)}})();
</script></body></html>
"""


LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bitcoin DCA Bot</title>
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="apple-touch-icon" sizes="180x180" href="/bitcoin-dca-icon-v3.png">
<link rel="mask-icon" href="/safari-pinned-tab-v3.svg" color="#8bafff">
<meta name="theme-color" content="#0b1020">
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2d;--line:#26324a;--text:#edf2ff;--muted:#9ba9c4;--accent:#8bafff;--red:#ff8f9e}
*{box-sizing:border-box}body{min-height:100vh;margin:0;display:grid;place-items:center;padding:22px;background:radial-gradient(ellipse at 15% 0%,#1b2a49 0,transparent 42%),var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
.card{width:min(100%,420px);padding:32px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,rgba(21,31,52,.98),rgba(15,22,39,.98));box-shadow:0 18px 48px #0005}
.eyebrow{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}h1{font-size:27px;margin:6px 0 4px}.sub{color:var(--muted);margin:0 0 24px}
.login-brand{display:flex;align-items:center;gap:12px;margin:10px 0}.login-brand img{display:block;flex:0 0 48px;border-radius:11px}.login-brand h1{min-width:0;margin:0;line-height:1.25}
label{display:block;color:var(--muted);font-size:12px;margin:15px 0 6px}input{width:100%;padding:12px 13px;color:var(--text);background:#0c1425;border:1px solid var(--line);border-radius:10px;font:inherit;outline:none}input:focus{border-color:var(--accent);box-shadow:0 0 0 3px #8bafff22}
button{width:100%;margin-top:22px;padding:12px;border:1px solid var(--accent);border-radius:10px;background:var(--accent);color:#081122;font:inherit;font-weight:700;cursor:pointer}
.error{min-height:20px;margin:12px 0 0;color:var(--red);font-size:13px}.foot{margin-top:20px;color:var(--muted);font-size:12px}
@media(max-width:480px){body{padding:16px;min-height:100svh}.card{padding:24px}input{font-size:16px}}

button{transition:background-color .16s ease,border-color .16s ease,box-shadow .16s ease,filter .16s ease,transform .16s ease}
button:disabled{cursor:not-allowed;filter:none;transform:none;box-shadow:none}
button:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
@media(hover:hover) and (pointer:fine){
 button:not(:disabled):hover{filter:brightness(1.13);border-color:var(--accent);box-shadow:0 0 8px #8bafff55,0 4px 20px #8bafff66;transform:translateY(-1px)}
 button:not(:disabled):active{transform:translateY(0);box-shadow:0 1px 5px #8bafff20}
}
@media(prefers-reduced-motion:reduce){button{transition:none}button:not(:disabled):hover{transform:none}}
</style>
</head>
<body><main class="card">

<div class="login-brand"><img src="/bitcoin-dca-icon-v3.png" width="48" height="48" alt=""><h1>Bitcoin DCA Bot Dashboard</h1></div>

<form method="post" action="/login">
<label for="username">Username</label>
<input id="username" name="username" type="text" autocomplete="username" required autofocus>
<label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Sign in</button>
</form>
<p class="error">{{ERROR}}</p>

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


def _public_market_price(display_currency, quote_currency, currencies=()):
    display_currency=display_currency.upper()
    quote_currency=quote_currency.upper()
    needed={display_currency,quote_currency,*currencies}
    if not needed <= {'EUR','USD'}:
        raise ValueError('Display and historical fiat currencies must be EUR or USD')
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures={c:executor.submit(price_cache.get_price,c,_kraken_public_btc_price) for c in needed}
        prices={c:f.result() for c,f in futures.items()}
    target=prices[display_currency][0]
    return {'currency':display_currency,'quoteCurrency':quote_currency,'price':str(target),
            'conversionRate':str(target/prices[quote_currency][0]),
            'conversionRates':{c:str(target/v[0]) for c,v in prices.items()},
            'source':'Kraken public conversion page','updatedAt':int(min(v[1] for v in prices.values())),
            'stale':any(v[2] for v in prices.values())}


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
    withdrawal_service = None
    worker = None

    def _send(self, status, body, content_type="application/json; charset=utf-8", filename=None, *, cache_control="no-store"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
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
        if parsed.path in ("/api/withdrawal/preview", "/api/withdrawal/confirm"):
            # A custom header plus JSON prevents cross-origin form submissions.
            if self.headers.get("X-DCA-Action") != "withdrawal" or self.headers.get("Content-Type") != "application/json":
                self._json(403, {"error": "Invalid withdrawal request"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("Invalid request body")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Invalid request body")
                session = SimpleCookie(self.headers.get("Cookie", ""))["dca_session"].value
                token = payload.get("token")
                if parsed.path.endswith("/confirm") and not isinstance(token, str):
                    raise ValueError("Confirmation token is required")
                result = self.withdrawal_service("preview" if parsed.path.endswith("/preview") else "confirm", session, token)
                self._json(202 if "jobId" in result else 200, result)
            except (ValueError, UnicodeDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception:
                LOG.exception("Manual withdrawal request failed")
                self._json(502, {"error": "Withdrawal could not be confirmed. Check exchange status and container logs before trying again."})
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
        if parsed.path == '/healthz':
            healthy=bool(self.worker and self.worker.healthy())
            self._json(200 if healthy else 503,{'healthy':healthy})
            return
        icon = {
            "/bitcoin-dca-icon-v3.png": (APPLE_TOUCH_ICON_PNG, "image/png"),
            "/safari-pinned-tab-v3.svg": (FAVICON_MASK_SVG, "image/svg+xml; charset=utf-8"),
            "/apple-touch-icon-precomposed.png": (APPLE_TOUCH_ICON_PNG, "image/png"),
            "/favicon.ico": (FAVICON_ICO, "image/x-icon"),
            "/favicon-32.png": (FAVICON_PNG, "image/png"),
            "/apple-touch-icon.png": (APPLE_TOUCH_ICON_PNG, "image/png"),
        }.get(parsed.path)
        if icon is not None:
            self._send(200, icon[0], icon[1], cache_control="public, max-age=86400")
            return
        if parsed.path == "/favicon.svg":
            self._send(200, FAVICON_SVG, "image/svg+xml; charset=utf-8", cache_control="public, max-age=86400")
            return
        if parsed.path == "/" and not self._has_valid_session():
            self._send(200, _login_page(), "text/html; charset=utf-8")
            return
        if not self._require_authentication():
            return
        if parsed.path == "/":
            self._send(200, HTML, "text/html; charset=utf-8")
            return
        if parsed.path.startswith('/api/withdrawal/jobs/'):
            session=SimpleCookie(self.headers.get('Cookie',''))['dca_session'].value
            try:
                self._json(200,self.worker.get_job(parsed.path.rsplit('/',1)[-1],session))
            except (ValueError,AttributeError) as exc:
                self._json(404,{'error':str(exc)})
            return
        if parsed.path == "/api/withdrawal/config":
            settings = self.settings
            kraken = settings["exchange"] == "KRAKEN"
            destination = ("Kraken saved withdrawal method: " + (settings.get("kraken_withdraw_key") or "not configured")) if kraken else ("Bitvavo address: " + (settings.get("withdraw_address") or "not configured") + "; network: " + settings.get("withdraw_network", "BTC"))
            missing = []
            if not settings["live"]:
                missing.append("enable LIVE_TRADING=true")
            if kraken:
                if not settings.get("kraken_withdraw_key"):
                    missing.append("set KRAKEN_WITHDRAW_KEY to your saved Bitcoin withdrawal method in .env")
            else:
                if not settings.get("withdraw_address"):
                    missing.append("set WITHDRAW_ADDRESS in .env")
                if not settings.get("withdraw_network"):
                    missing.append("set WITHDRAW_NETWORK in .env")
            if not self.withdrawal_service:
                missing.append("restart the bot to enable the withdrawal service")
            reason = "Manual withdrawal is unavailable: " + "; ".join(missing) + "." if missing else ""
            self._json(200, {"destination": destination, "enabled": not missing, "reason": reason,
                             "exchange": settings["exchange"],
                             "target": (settings.get("kraken_withdraw_key") if kraken else settings.get("withdraw_address")) or ""})
            return
        query = parse_qs(parsed.query)
        if parsed.path == "/api/market-price":
            currency = query.get("currency", ["EUR"])[0].upper()
            try:
                market = _public_market_price(currency, self.settings["quote"], query.get("currencies",[""])[0].split(",") if query.get("currencies",[""])[0] else ())
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
                balance_matches = (snapshot.get("quote") == self.settings["quote"]
                                   and snapshot.get("exchange") == self.settings["exchange"])
                forecast_at, forecast_status = withdrawal_forecast(
                    self.settings, snapshot if balance_matches else {},
                    query.get("monthlyDeposit", ["0"])[0], datetime.now(timezone.utc))
                self._json(200, {
                    "quote": self.settings["quote"],
                    "exchange": self.settings["exchange"],
                    "quoteBalance": snapshot.get("quoteBalance") if balance_matches else None,
                    "btcBalance": snapshot.get("btcBalance") if balance_matches else None,
                    "estimatedWithdrawalAt": forecast_at,
                    "estimatedWithdrawalStatus": forecast_status,
                    "balanceUpdatedAt": snapshot.get("updatedAt") if balance_matches else None,
                    "nextPurchaseAt": snapshot.get("nextPurchaseAt"),
                    "nextPurchaseStatus": snapshot.get("nextPurchaseStatus", "Schedule pending"),
                    "timezone": self.settings["timezone"],
                    "serverTime": datetime.now(timezone.utc).isoformat(),
                })
                return
            records = []
            if parsed.path == "/api/withdrawals":
                withdrawals = [
                    {
                        "timestamp": _row_timestamp_ms(record["row"]),
                        "txId": record.get("id", ""),
                    }
                    for record in withdrawal_records()
                    if record.get('withdrawalStatus')=='completed' 
                ]
                withdrawals.sort(key=lambda item: item["timestamp"])
                self._json(200, withdrawals)
                return
            if parsed.path in ("/api/analytics", "/api/export.csv"):
                start_ms, end_ms = _period(query)
                if parsed.path == "/api/export.csv":
                    selected_records=parqet_interval_records(start_ms,end_ms)
                    real_records = [record for record in selected_records if not record.get("simulated")]
                    if not any(record["row"].get("type") == "TransferOut" and record.get("withdrawalStatus")=="completed" for record in real_records):
                        raise ValueError(
                            "No confirmed bot-recorded BTC withdrawal was found in the selected period. "
                            "The Parqet export requires a purchase and a withdrawal. "
                            "Choose a period that includes a withdrawal, or export after your first withdrawal."
                        )
                    if not any(record["row"].get("type") == "Buy" for record in real_records):
                        raise ValueError(
                            "No real bot purchase was found since the previous confirmed withdrawal. "
                            "The Parqet export requires a real purchase and a withdrawal; dry-run purchases are excluded."
                        )
                    rows = parqet_aggregate_rows(
                        selected_records, self.settings["exchange"]
                    )
                    if not rows:
                        raise ValueError(
                            "The selected period must contain at least one real purchase "
                            "and one bot-recorded BTC withdrawal."
                        )
                    filename = "parqet_btc_" + str(start_ms) + "_" + str(end_ms) + ".csv"
                    self._send(200, parqet_csv(rows), "text/csv; charset=utf-8", filename)
                else:
                    page=int(query.get('page',['1'])[0]);limit=int(query.get('limit',['10'])[0])
                    page_records,total,page,totals,balance=read_report(start_ms,end_ms,page,limit)
                    snapshot=read_dashboard_snapshot()
                    snapshot['trackedBtc']=str(balance)
                    if snapshot.get('quote') not in (None,self.settings['quote']):snapshot['price']='0'
                    result=build_report(self.settings,totals,snapshot,page_records)
                    limit=max(1,min(1000,limit))
                    result['pagination']={'page':page,'pageSize':limit,'total':total,'pages':max(1,(total+limit-1)//limit)}
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
    records=bitcoin_activity_records(records)
    return build_report(settings,totals_from_records(records),snapshot,records)


def serve(settings, port, withdrawal_service=None, worker=None):
    handler = type("ConfiguredHandler", (Handler,), {"settings": settings, "withdrawal_service": staticmethod(withdrawal_service), "worker":worker})
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    LOG.info("Local portfolio web UI listening on container port %s.", port)
    server.serve_forever()


