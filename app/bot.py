"""Bitcoin DCA bot for Bitvavo and Kraken. Starts in dry-run mode unless explicitly enabled."""
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from zoneinfo import ZoneInfo
import calendar
import threading
from functools import wraps
from exchange_errors import DefinitiveRejection, UncertainWithdrawalError
from logging_utils import log_transition, clear_transition
from withdrawals import reconcile_withdrawals

BOT_LOCK = threading.RLock()

def serialized(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with BOT_LOCK:
            return function(*args, **kwargs)
    return locked

import requests
from activity import append_activity_records, read_dashboard_snapshot, write_dashboard_snapshot, ledger_balance, withdrawal_records, write_json_atomic, get_activity_record, latest_real_buy
from storage import STATE_PATH, prepare_storage

LOG = logging.getLogger("bitcoin-dca")


def cycle_dates(now, deposit_day):
    """Return this deposit cycle's key and next deposit instant in configured local time."""
    def deposit_date(year, month):
        day = min(deposit_day, calendar.monthrange(year, month)[1])
        return datetime(year, month, day, tzinfo=now.tzinfo)

    this_month = deposit_date(now.year, now.month)
    if now >= this_month:
        start = this_month
        if now.month == 12:
            next_deposit = deposit_date(now.year + 1, 1)
        else:
            next_deposit = deposit_date(now.year, now.month + 1)
    else:
        if now.month == 1:
            start = deposit_date(now.year - 1, 12)
        else:
            start = deposit_date(now.year, now.month - 1)
        next_deposit = this_month
    return start.strftime("%Y-%m-%d"), start, next_deposit


def local_now(timezone_name):
    return datetime.now(ZoneInfo(timezone_name))


def latest_recorded_buy_time(exchange=None):
    """Return the newest real Bitcoin buy from persistent UI activity."""
    return latest_real_buy(exchange) if exchange else latest_real_buy()


def read_state(path, cycle_key):
    if not path.exists():
        return {"cycle": cycle_key, "spent": "0"}
    state = json.loads(path.read_text())
    if state.get("cycle") != cycle_key:
        # Reset dry-run simulated spending at the configured monthly boundary.
        fresh = {"cycle": cycle_key, "spent": "0"}
        if state.get("configuration_account"):
            fresh["configuration_account"] = state["configuration_account"]
        if "configuration_live" in state:
            fresh["configuration_live"] = state["configuration_live"]
        if state.get("pending_order"):
            fresh["pending_order"] = state["pending_order"]
        if state.get("pending_withdrawal"):
            fresh["pending_withdrawal"] = state["pending_withdrawal"]
        if state.get("pending_withdrawal_context"):
            fresh["pending_withdrawal_context"] = state["pending_withdrawal_context"]
        if state.get("last_withdrawal"):
            fresh["last_withdrawal"] = state["last_withdrawal"]
        if state.get("last_order_at"):
            fresh["last_order_at"] = state["last_order_at"]
        if "withdrawal_armed" in state:
            fresh["withdrawal_armed"] = state["withdrawal_armed"]
        return fresh
    return state


def save_state(path, state):
    write_json_atomic(path, state)


def utc_iso_from_ms(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalized_event_time(value, fallback_ms):
    if value is None:
        return utc_iso_from_ms(fallback_ms)
    if isinstance(value, (int, float)) or str(value).isdigit():
        numeric = int(value)
        return utc_iso_from_ms(numeric if numeric > 10**11 else numeric * 1000)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def persist_bot_withdrawal(api, withdrawal, result, settings, context):
    quote = context.get("quote", settings["quote"])
    amount = Decimal(str(withdrawal.get("amount", "0")))
    fee_btc = Decimal(str(withdrawal.get("fee", result.get("fee", "0"))))
    # Kraken's request amount is already net of its fee; Bitvavo's request amount
    # is the gross debit and its fee is deducted from that amount before sending.
    if settings["exchange"] == "BITVAVO":
        net_amount = max(amount - fee_btc, Decimal(0))
        gross_amount = amount
    else:
        net_amount = amount
        gross_amount = amount + fee_btc
    price = Decimal(str(context.get("price", "0")))
    if price <= 0:
        price = api.price(f"BTC-{quote}")
    timestamp_ms = int(context.get("timestamp_ms", int(time.time() * 1000)))
    timestamp = normalized_event_time(result.get("createdAt"), timestamp_ms)
    event_id = result.get("id") or result.get("refid") or withdrawal.get("idempotencyKey") or f"{timestamp_ms}-{amount}"
    row = {
        "datetime": timestamp,
        "type": "TransferOut",
        "identifier": "BTC",
        "assetType": "Crypto",
        "price": format(price, "f"),
        "shares": format(net_amount, "f"),
        "amount": format(net_amount * price, "f"),
        "currency": quote,
        "fee": format(fee_btc * price, "f"),
        "tax": "0",
    }
    record = {
        "id": f"withdrawal:{event_id}",
        "row": row,
        "netWithdrawalAmount": True,
        "networkFeeBtc": format(fee_btc, "f"),
        "grossWithdrawalAmountBtc": format(gross_amount, "f"),
        "manual": bool(context.get("manual")),
        "exchange": settings["exchange"],
        "withdrawalStatus": "submitted",
        "withdrawalId": result.get("id") or result.get("refid"),
        "withdrawalAddress": withdrawal.get("address"),
        "withdrawalNetwork": withdrawal.get("network"),
    }
    context["record_id"] = record["id"]
    return append_activity_records([record])


def update_dashboard_snapshot(settings, quote_balance, price, btc_balance=None):
    tracked_btc = ledger_balance()
    previous = read_dashboard_snapshot()
    write_dashboard_snapshot({
        "exchange": settings["exchange"],
        "quote": settings["quote"],
        "trackedBtc": format(tracked_btc, "f"),
        "btcBalance": str(btc_balance) if btc_balance is not None else None,
        "price": format(price, "f"),
        "value": format(tracked_btc * price, "f"),
        "quoteBalance": format(quote_balance, "f"),
        "withdrawThreshold": format(settings["withdraw_threshold"], "f"),
        "nextPurchaseAt": previous.get("nextPurchaseAt"),
        "nextPurchaseStatus": previous.get("nextPurchaseStatus", "Schedule pending"),
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    })


def update_dashboard_schedule(next_purchase_at, status, forecast_plan=None):
    snapshot = read_dashboard_snapshot()
    snapshot["nextPurchaseAt"] = (
        next_purchase_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        if next_purchase_at is not None else None
    )
    snapshot["nextPurchaseStatus"] = status
    snapshot["withdrawalForecastPlan"] = forecast_plan
    write_dashboard_snapshot(snapshot)


def persist_bot_buy(api, order, settings, event_id, fallback_timestamp_ms):
    records = api.normalize_bot_buy(
        order, settings["quote"], event_id, fallback_timestamp_ms
    )
    for record in records:
        record["exchange"] = settings["exchange"]
        record["purchaseStatus"] = "confirmed"
    return records, append_activity_records(records)


def persist_simulated_buy(api, settings, amount, price, quote_amount, estimated_fee=None):
    timestamp_ms = int(time.time() * 1000)
    event_id = f"dry-run:{uuid.uuid4()}"
    row = {
        "datetime": utc_iso_from_ms(timestamp_ms),
        "type": "Buy",
        "identifier": "BTC",
        "assetType": "Crypto",
        "price": format(price, "f"),
        "shares": format(amount, "f"),
        "amount": format(quote_amount, "f"),
        "currency": settings["quote"],
        "fee": format(estimated_fee if estimated_fee is not None else api.estimate_taker_fee(f"BTC-{settings['quote']}", quote_amount), "f"),
        "tax": "0",
    }
    record = {"id": event_id, "simulated": True, "exchange": settings["exchange"], "row": row}
    return append_activity_records([record])


def ceil_to_places(value, places):
    quantum = Decimal(1).scaleb(-int(places))
    return value.quantize(quantum, rounding=ROUND_CEILING)


def maybe_withdraw_btc(api, state, settings, manual_request=None):
    if manual_request is None and settings['live']:
        reconcile_withdrawals(api, settings)
        last_record = get_activity_record(state.get('last_withdrawal',{}).get('activityId',''))
        if last_record and last_record.get('withdrawalStatus')=='failed' and not state.get('withdrawal_armed',True):
            state['withdrawal_armed']=True
            save_state(STATE_PATH,state)
            return
        if not state.get('pending_withdrawal') and any(r.get('exchange')==settings['exchange'] and r.get('withdrawalStatus')=='submitted'
               for r in withdrawal_records(unresolved=True)):
            return
    if not settings["withdrawals_enabled"] and manual_request is None and not state.get("pending_withdrawal"):
        return
    if not settings["live"]:
        log_transition(LOG, api, "withdrawal-wait", "dry-run", logging.WARNING, "Automatic withdrawal is enabled but LIVE_TRADING is false; no withdrawal will be sent.")
        return

    pending = state.get("pending_withdrawal")
    if pending:
        bitcoin_request = (pending.get("symbol") == "BTC" if settings["exchange"] == "BITVAVO"
                           else pending.get("asset") == "XBT")
        if not bitcoin_request:
            raise ValueError("Saved withdrawal is not a Bitcoin request for the selected exchange; check its status before continuing")
        context = state.get("pending_withdrawal_context", {})
        if context.get("exchange", settings["exchange"]) != settings["exchange"]:
            raise ValueError("Saved withdrawal belongs to another exchange; resolve it before switching exchanges")
        result = context.get("result")
        if result is None:
            if not getattr(api, "supports_withdrawal_idempotency", False):
                log_transition(LOG, api, "withdrawal-unknown", str(pending), logging.ERROR, "A previous withdrawal request has an unknown outcome. Verify its status with the exchange; it will not be resent.")
                return
            try:
                result = api.withdraw_btc(pending)
            except DefinitiveRejection:
                state.pop("pending_withdrawal", None)
                state.pop("pending_withdrawal_context", None)
                save_state(STATE_PATH, state)
                raise
            except requests.RequestException:
                LOG.warning("Withdrawal outcome is uncertain; the same idempotent request will be retried next check.")
                LOG.debug("Idempotent withdrawal retry failure", exc_info=True)
                return
            context["result"] = result
            state["pending_withdrawal_context"] = context
            save_state(STATE_PATH, state)
        persist_bot_withdrawal(api, pending, result, settings, context)
        state["last_withdrawal"] = {
            "id": result.get("id"),
            "amount": pending["amount"],
            "activityId": context.get('record_id'),
            "balanceBefore": context.get("balanceBefore", pending["amount"]),
            "createdAt": result.get("createdAt"),
        }
        state["withdrawal_armed"] = False
        state.pop("pending_withdrawal", None)
        state.pop("pending_withdrawal_context", None)
        save_state(STATE_PATH, state)
        LOG.info("BTC withdrawal submitted: amount=%s BTC, withdrawalId=%s", pending["amount"], result.get("id"))
        return

    available = api.balance("BTC")
    if available <= 0:
        if not state.get("withdrawal_armed", True):
            state["withdrawal_armed"] = True
            save_state(STATE_PATH, state)
            LOG.info("BTC balance is below the withdrawal threshold again; automatic withdrawal is re-armed.")
        return
    price = api.price(f"BTC-{settings['quote']}")
    value = available * price
    if manual_request is None and not state.get("withdrawal_armed", True):
        if value < settings["withdraw_threshold"]:
            state["withdrawal_armed"] = True
            save_state(STATE_PATH, state)
            LOG.info("BTC balance is below the withdrawal threshold again; automatic withdrawal is re-armed.")
        else:
            log_transition(LOG, api, "withdrawal-wait", "balance-not-updated", logging.INFO, "Waiting for %s to reflect the previous withdrawal before another automatic withdrawal.", settings["exchange"])
        return
    if manual_request is None and value < settings["withdraw_threshold"]:
        LOG.debug("BTC withdrawal threshold not reached: available value %s %s, threshold %s %s.", value, settings["quote"], settings["withdraw_threshold"], settings["quote"])
        return

    amount = available.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    if amount <= 0:
        log_transition(LOG, api, "withdrawal-wait", "empty", logging.INFO, "No withdrawable BTC remains after applying Bitcoin precision.")
        return
    idempotency_key = str(uuid.uuid4())
    withdrawal = manual_request or api.withdrawal_request(amount, idempotency_key, settings)
    state["pending_withdrawal"] = withdrawal
    state["pending_withdrawal_context"] = {
        "exchange": settings["exchange"],
        "quote": settings["quote"],
        "price": format(price, "f"),
        "timestamp_ms": int(time.time() * 1000),
        "manual": manual_request is not None,
        "balanceBefore": format(available, "f"),
    }
    save_state(STATE_PATH, state)
    try:
        result = api.withdraw_btc(withdrawal)
    except DefinitiveRejection:
        state.pop('pending_withdrawal',None)
        state.pop('pending_withdrawal_context',None)
        save_state(STATE_PATH,state)
        raise
    except requests.RequestException:
        LOG.exception("Withdrawal outcome is uncertain; check the exchange before taking further action.")
        if not getattr(api, "supports_withdrawal_idempotency", False):
            LOG.error("This exchange does not support idempotent withdrawal retries; the saved request will not be resent automatically.")
        return
    state["pending_withdrawal_context"]["result"] = result
    save_state(STATE_PATH, state)
    persist_bot_withdrawal(
        api, withdrawal, result, settings, state.get("pending_withdrawal_context", {})
    )
    state["last_withdrawal"] = {
        "id": result.get("id"),
        "amount": withdrawal["amount"],
        "activityId": state.get('pending_withdrawal_context',{}).get('record_id'),
        "createdAt": result.get("createdAt"),
        "balanceBefore": format(available, "f"),
    }
    state.pop("pending_withdrawal", None)
    state.pop("pending_withdrawal_context", None)
    state["withdrawal_armed"] = False
    save_state(STATE_PATH, state)
    LOG.info("BTC withdrawal submitted: amount=%s BTC (fee deducted from amount), value at submission=%s %s, withdrawalId=%s", withdrawal["amount"], value, settings["quote"], result.get("id"))


def defer_order_check(state, state_path, settings):
    delay = settings["order_status_check_seconds"]
    state["pending_order"]["nextStatusCheckAt"] = int(time.time() * 1000) + delay * 1000
    save_state(state_path, state)
    return delay


def reconcile_pending_order(api, state, state_path, settings, check_interval):
    pending = state["pending_order"]
    symbol = f"BTC-{settings['quote']}"
    delay = settings["order_status_check_seconds"]
    update_dashboard_schedule(None, "Waiting for confirmation that the previous order has ended")
    if pending.get("exchange", settings["exchange"]) != settings["exchange"] or pending.get("symbol") != symbol:
        LOG.error("Pending order belongs to a different exchange or market; refusing another purchase.")
        return delay
    due_ms = int(pending.get("nextStatusCheckAt", int(pending.get("submittedAt", 0)) + delay * 1000))
    remaining_ms = due_ms - int(time.time() * 1000)
    if remaining_ms > 0:
        return max(1, (remaining_ms + 999) // 1000)

    try:
        order = api.get_order(symbol, pending["clientOrderId"], pending.get("exchangeOrderId"))
    except requests.RequestException as exc:
        LOG.warning(
            "%s order %s could not be confirmed: %s. No new order will be sent; checking again in %ss.",
            settings["exchange"], pending.get("exchangeOrderId") or pending["clientOrderId"], exc, delay,
        )
        return defer_order_check(state, state_path, settings)

    status = str(order.get("status", "")).lower()
    if status not in ("closed", "filled", "canceled", "cancelled", "expired", "rejected"):
        log_transition(LOG, api, "order-wait", (pending["clientOrderId"], status), logging.INFO,
            "%s order %s exists but has not ended (status=%s); no new purchase; checking again in %ss.",
            settings["exchange"], pending.get("exchangeOrderId") or pending["clientOrderId"], status or "unknown", delay,
        )
        return defer_order_check(state, state_path, settings)

    records, added = persist_bot_buy(
        api, order, settings, pending["clientOrderId"], int(pending.get("submittedAt", 0))
    )
    if not records:
        filled_raw = order.get("vol_exec", order.get("filledAmount"))
        filled = Decimal(str(filled_raw)) if filled_raw is not None else None
        if filled is None or filled > 0 or status == "filled":
            LOG.warning("Order %s ended but its fill details are incomplete; keeping it pending.", pending["clientOrderId"])
            return defer_order_check(state, state_path, settings)
        state.pop("pending_order", None)
        save_state(state_path, state)
        LOG.warning("Order %s ended without a fill (status=%s); no purchase recorded.", pending["clientOrderId"], status)
        return check_interval

    order_cost = sum(
        (Decimal(record["row"]["amount"]) + Decimal(record["row"].get("fee", "0")) for record in records),
        Decimal(0),
    )
    state["spent"] = str(Decimal(state.get("spent", "0")) + order_cost)
    state["last_order_at"] = max(
        datetime.fromisoformat(record["row"]["datetime"].replace("Z", "+00:00")) for record in records
    ).isoformat()
    state["last_order_quote"] = str(order_cost)
    state.pop("pending_order", None)
    save_state(state_path, state)
    settings["startup_last_buy_at"] = None
    LOG.info(
        "Confirmed %s order %s ended (status=%s); saved %s activity record(s). Recalculating the purchase schedule.",
        settings["exchange"], pending.get("exchangeOrderId") or pending["clientOrderId"], status, added,
    )
    return None


@serialized
def run_once(api, settings, check_interval, schedule_only=False):
    # Optional adapter scope: share balance reads within this cycle only.
    # The adapter invalidates it whenever an order or withdrawal is submitted.
    snapshot_scope = getattr(type(api), "balance_snapshot", None)
    if snapshot_scope is None:
        return _run_once(api, settings, check_interval, schedule_only)
    with snapshot_scope(api):
        return _run_once(api, settings, check_interval, schedule_only)


def _run_once(api, settings, check_interval, schedule_only=False, cycle_data=None):
    quote = settings["quote"]
    symbol = f"BTC-{quote}"
    state_path = STATE_PATH
    now = local_now(settings["timezone"])
    cycle_key, _cycle_start, next_deposit = cycle_dates(now, settings["deposit_day"])
    state = read_state(state_path, cycle_key)
    state["cycle"] = cycle_key
    spent = Decimal(state.get("spent", "0"))
    pending = state.get("pending_order")
    if settings["live"] and pending:
        wait = reconcile_pending_order(api, state, state_path, settings, check_interval)
        if wait is not None:
            return wait
        return run_once(api, settings, check_interval, schedule_only=True)
    try:
        maybe_withdraw_btc(api, state, settings)
    except requests.RequestException as exc:
        LOG.warning("Automatic BTC withdrawal check failed; will retry: %s", exc)
    # Use the current available quote balance as the DCA budget. This includes
    # funds already on the exchange, even if they were deposited before this cycle.
    try:
        exchange_btc = cycle_data['btc_balance'] if cycle_data else api.balance("BTC")
    except requests.RequestException as exc:
        LOG.warning("Could not refresh the dashboard BTC balance: %s", exc)
        LOG.debug("Dashboard balance refresh failure", exc_info=True)
        exchange_btc = None
    balance = cycle_data['balance'] if cycle_data else api.balance(quote)
    balance_after_simulated_spend = (
        max(Decimal(0), balance - spent) if not settings["live"] else balance
    )
    if balance_after_simulated_spend <= 0:
        update_dashboard_snapshot(settings,balance,Decimal(str(read_dashboard_snapshot().get('price','0'))),exchange_btc)
        update_dashboard_schedule(None,'Waiting for available quote-currency funds')
        log_transition(LOG, api, 'purchase-wait', 'no-funds', logging.INFO, 'Waiting for available %s funds.', quote)
        return check_interval
    # Fee lookup precedes the fresh market-rule check; it is cached by the adapter.
    fee_rate = cycle_data['fee_rate'] if cycle_data else api.estimate_taker_fee(symbol, Decimal(1))
    price = cycle_data['price'] if cycle_data else api.price(symbol)
    market = cycle_data['market'] if cycle_data else api.market(symbol, refresh=True)
    cycle_data = {'balance':balance,'price':price,'market':market,'fee_rate':fee_rate,'btc_balance':exchange_btc}
    update_dashboard_snapshot(settings, balance, price, exchange_btc)
    if market.get("status") != "trading":
        update_dashboard_schedule(None, f"Waiting for market status: {market.get('status')}")
        log_transition(LOG, api, "purchase-wait", "market-" + str(market.get("status")), logging.INFO, "Market %s is %s; waiting.", symbol, market.get("status"))
        return check_interval
    min_quote = Decimal(market["minOrderInQuoteAsset"])
    min_btc = Decimal(market["minOrderInBaseAsset"])
    quantity_decimals = int(market["quantityDecimals"])
    notional_decimals = int(market["notionalDecimals"])
    order_amount = ceil_to_places(max(settings["buy_amount"], min_btc, min_quote / price), quantity_decimals)
    order_quote = ceil_to_places(order_amount * price, notional_decimals)
    spendable = balance_after_simulated_spend
    available = balance if settings["live"] else balance_after_simulated_spend
    affordable_budget = min(spendable, available)
    estimated_fee = order_quote * fee_rate
    # 0.5% estimation buffer protects the budget, not the execution price of a market order.
    planned_cost = ceil_to_places((order_quote + estimated_fee) * Decimal('1.005'), notional_decimals)
    possible_buys = int(affordable_budget // planned_cost)
    if possible_buys < 1:
        update_dashboard_schedule(None, "Waiting until the balance covers an order")
        log_transition(LOG, api, "purchase-wait", "insufficient-funds", logging.INFO, "Waiting: available balance %s %s; minimum order %s %s.", affordable_budget, quote, order_quote, quote)
        return check_interval

    last_buy = settings.get('startup_last_buy_at')
    if last_buy is None and state.get('last_order_at'):
        last_buy = datetime.fromisoformat(state['last_order_at'])
    if last_buy is not None:
        last_buy = last_buy.replace(tzinfo=last_buy.tzinfo or timezone.utc).astimezone(timezone.utc)
    clear_transition(api, "purchase-wait")
    interval_anchor = last_buy if last_buy is not None else now.astimezone(timezone.utc)
    seconds_to_deposit = max(60, (next_deposit.astimezone(timezone.utc) - interval_anchor).total_seconds())
    interval_seconds = max(60, int(seconds_to_deposit / possible_buys))
    LOG.info(
        "Recalculated DCA schedule: balance=%s %s; spendable=%s %s; "
        "price=%s %s/%s; estimated order=%s %s (%s %s); "
        "next deposit day=%s; amount of possible buys until next deposit=%s; "
        "purchase interval=%s minutes.",
        balance, quote, affordable_budget, quote, price, quote, "BTC",
        order_amount, "BTC", order_quote, quote, next_deposit.isoformat(),
        possible_buys, int(interval_seconds / 60),
    )
    fingerprint = ['last-purchase-interval-v2',str(affordable_budget),str(planned_cost),next_deposit.isoformat(),
                   last_buy.isoformat() if last_buy else None]
    previous_plan = state.get('schedule',{})
    if previous_plan.get('fingerprint')==fingerprint:
        due = datetime.fromisoformat(previous_plan['dueAt'])
    else:
        due = interval_anchor + timedelta(seconds=interval_seconds) if last_buy else now
    state['schedule']={'fingerprint':fingerprint,'dueAt':due.isoformat()}
    save_state(state_path,state)
    remaining=max(0,int((due.astimezone(timezone.utc)-now.astimezone(timezone.utc)).total_seconds()))
    update_dashboard_schedule(due, 'Scheduled' if remaining else 'Due now', {
        "orderAmount": str(order_amount), "plannedCost": str(planned_cost),
        "budget": str(affordable_budget), "intervalSeconds": interval_seconds,
        "nextDepositAt": next_deposit.isoformat(),
    })
    startup_alignment = settings.pop('startup_schedule_log_pending', False)
    log_label = 'Restart alignment' if startup_alignment else 'Purchase schedule'
    local_zone = ZoneInfo(settings['timezone'])
    if last_buy:
        anchor = last_buy.replace(tzinfo=last_buy.tzinfo or timezone.utc)
        elapsed = max(0, int((now.astimezone(timezone.utc) - anchor.astimezone(timezone.utc)).total_seconds()))
        LOG.info(
            "%s: last purchase=%s; elapsed since purchase=%.1f minutes%s; "
            "calculated purchase interval=%.1f minutes; next purchase=%s; remaining wait=%.1f minutes; %s.",
            log_label, anchor.astimezone(local_zone).isoformat(timespec='seconds'), elapsed / 60,
            ' (including startup delay)' if startup_alignment else '', interval_seconds / 60,
            due.astimezone(local_zone).isoformat(timespec='seconds'), remaining / 60,
            'saved due time retained for unchanged inputs' if previous_plan.get('fingerprint') == fingerprint
            else 'due time calculated from last purchase',
        )
    else:
        LOG.info("%s: no previous purchase time available; next purchase=%s; remaining wait=%.1f minutes; first purchase due now.",
                 log_label, due.astimezone(local_zone).isoformat(timespec='seconds'), remaining / 60)
    if remaining: return remaining

    if schedule_only:
        # If the computed time has already arrived while recording the last order,
        # wake the normal loop immediately instead of submitting from this pass.
        return 1

    if settings["live"]:
        if available < planned_cost:
            update_dashboard_schedule(None, "Waiting until the balance covers an order")
            log_transition(LOG, api, "purchase-wait", "insufficient-funds", logging.INFO, "Waiting for sufficient available balance: %s %s; minimum order %s %s.", available, quote, order_quote, quote)
            return check_interval
        pending = {
            "clientOrderId": str(uuid.uuid4()),
            "exchange": settings["exchange"],
            "symbol": symbol,
            "amountQuote": str(order_quote),
            "amountBase": str(order_amount),
            "submittedAt": int(time.time() * 1000),
        }
        state["pending_order"] = pending
        save_state(state_path, state)
        update_dashboard_schedule(None, "Submitting the order and waiting for its status check")
        try:
            result = api.buy_btc(quote, order_amount, pending["clientOrderId"])
        except DefinitiveRejection as exc:
            state.pop('pending_order',None)
            state['last_rejection']={'action':'buy','reason':str(exc),'at':utc_iso_from_ms(int(time.time()*1000))}
            save_state(state_path,state)
            LOG.warning('Order rejected by %s: %s; no pending order remains.',settings['exchange'],exc)
            return check_interval
        except requests.RequestException as exc:
            LOG.warning(
                "%s order submission has an uncertain outcome: %s. It will not be resent; checking the saved reference in %ss.",
                settings["exchange"], exc, settings["order_status_check_seconds"],
            )
            return defer_order_check(state, state_path, settings)
        pending["exchangeOrderId"] = result.get("orderId")
        wait = defer_order_check(state, state_path, settings)
        LOG.info(
            "Order submitted: %s %s (estimated %s %s); orderId=%s. Checking its status in %ss; no new purchase until it has ended.",
            order_amount, "BTC", order_quote, quote, pending.get("exchangeOrderId"), wait,
        )
        return wait
    else:
        LOG.info("DRY RUN: would market-buy %s %s (estimated %s %s at %s %s/%s); saved as simulated activity.", order_amount, "BTC", order_quote, quote, price, quote, "BTC")
        added = persist_simulated_buy(api, settings, order_amount, price, order_quote, estimated_fee)
        LOG.info("Saved %s simulated purchase record(s) for the web UI; excluded from the Parqet CSV export.", added)

    settings["startup_last_buy_at"] = None
    # Debit budget for dry-run too, to make its monthly schedule representative.
    state["spent"] = str(spent + order_quote + estimated_fee)
    state["last_order_at"] = datetime.now(timezone.utc).isoformat()
    state["last_order_quote"] = str(order_quote)
    state.pop("pending_order", None)
    save_state(state_path, state)
    # Reuse this cycle's data for pure schedule recomputation after a simulation.
    return _run_once(api, settings, check_interval, schedule_only=True, cycle_data=cycle_data)


def manual_withdrawal_service(api, settings):
    previews = {}

    @serialized
    def handle(action, session, token=None):
        if not settings["live"]:
            raise ValueError("Manual Bitcoin withdrawals require LIVE_TRADING=true.")
        api.validate_withdrawal_settings(settings)
        cycle, _, _ = cycle_dates(local_now(settings["timezone"]), settings["deposit_day"])
        state = read_state(STATE_PATH, cycle)
        if (state.get("pending_order") or state.get("pending_withdrawal") or
            any(r.get('exchange')==settings['exchange'] and r.get('withdrawalStatus')=='submitted'
                for r in withdrawal_records(unresolved=True))):
            raise ValueError("An order or withdrawal is unresolved. Check the exchange before continuing.")
        available = api.balance("BTC").quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        if available <= 0:
            raise ValueError("No BTC is available to withdraw.")
        last = state.get("last_withdrawal", {})
        last_record=get_activity_record(last.get('activityId',''))
        failed=last_record and last_record.get('withdrawalStatus')=='failed'
        if not failed and not state.get("withdrawal_armed", True) and available >= Decimal(last.get("balanceBefore", last.get("amount", "0"))):
            raise ValueError("Waiting for the exchange balance to reflect the previous withdrawal.")
        fingerprint = json.dumps(last, sort_keys=True)
        now = time.monotonic()
        for key in list(previews):
            if previews[key]["expires"] <= now:
                previews.pop(key)
        if action == "preview":
            request = api.withdrawal_request(available, str(uuid.uuid4()), settings)
            key = str(uuid.uuid4())
            previews[key] = {"session": session, "expires": time.monotonic() + 120, "request": request,
                             "available": available, "fingerprint": fingerprint}
            return {"token": key, "availableBtc": format(available, "f"),
                    "netBtc": request["amount"] if settings["exchange"] == "KRAKEN" else None,
                    "feeBtc": request.get("fee"), "destination": withdrawal_destination(settings)}
        preview = previews.get(token)
        if not preview or preview["session"] != session:
            raise ValueError("Confirmation expired or already used. Start a new withdrawal preview.")
        previews.pop(token)
        if preview["available"] != available or preview["fingerprint"] != fingerprint:
            raise ValueError("The balance or withdrawal state changed. Review a new preview.")
        maybe_withdraw_btc(api, state, settings, manual_request=preview["request"])
        if state.get("pending_withdrawal"):
            raise UncertainWithdrawalError("Withdrawal outcome is uncertain. Check the exchange; do not submit again.")
        return {"submitted": True, "message": "Withdrawal submitted and recorded in Activity. It will be included in Parqet exports after exchange confirmation."}
    return handle


def withdrawal_destination(settings):
    if settings["exchange"] == "KRAKEN":
        return "Kraken saved withdrawal method: " + (settings.get("kraken_withdraw_key") or "not configured")
    return "Bitvavo address: " + (settings.get("withdraw_address") or "not configured") + "; network: " + settings.get("withdraw_network", "BTC")


def main():
    os.umask(0o077)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s")
    prepare_storage()
    from runtime import Runtime
    from worker import supervise
    from web_ui import serve
    runtime = Runtime()
    port = int(os.getenv("WEB_UI_PORT", "6890"))
    if not 1 <= port <= 65535:
        raise ValueError("WEB_UI_PORT must be between 1 and 65535")
    runtime.worker.start()
    threading.Thread(target=supervise, args=(runtime.worker,), daemon=True).start()
    serve(runtime.settings, port, runtime.worker.submit, worker=runtime.worker, runtime=runtime)


if __name__ == "__main__":
    main()
