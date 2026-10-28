"""Exchange-configurable DCA bot. Starts in dry-run mode unless explicitly enabled."""
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo
import calendar
import threading

import requests
from activity import append_activity_records, read_activity_records, read_dashboard_snapshot, write_dashboard_snapshot
from exchange import create_exchange

LOG = logging.getLogger("bitvavo-dca")


def env_decimal(name, default=None):
    raw = os.getenv(name)
    if raw is None and default is None:
        raise ValueError(f"Missing required environment variable: {name}")
    value = Decimal(raw if raw is not None else str(default))
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return value


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


def latest_recorded_buy_time(base_asset):
    """Return the newest real buy of the configured asset from persistent UI activity."""
    buy_times = []
    for record in read_activity_records():
        if record.get("simulated"):
            continue
        row = record.get("row", {})
        if row.get("type") != "Buy" or str(row.get("identifier", "")).upper() != base_asset.upper():
            continue
        value = row.get("datetime")
        if not value:
            continue
        try:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            buy_times.append(timestamp.astimezone(timezone.utc))
        except (TypeError, ValueError):
            LOG.warning("Ignoring activity buy with invalid datetime: %r", value)
    return max(buy_times) if buy_times else None


def read_state(path, cycle_key):
    if not path.exists():
        return {"cycle": cycle_key, "spent": "0"}
    state = json.loads(path.read_text())
    if state.get("cycle") != cycle_key:
        # Reset dry-run simulated spending at the configured monthly boundary.
        fresh = {"cycle": cycle_key, "spent": "0"}
        if state.get("pending_order"):
            fresh["pending_order"] = state["pending_order"]
        if state.get("pending_withdrawal"):
            fresh["pending_withdrawal"] = state["pending_withdrawal"]
        if state.get("pending_withdrawal_context"):
            fresh["pending_withdrawal_context"] = state["pending_withdrawal_context"]
        if state.get("last_withdrawal"):
            fresh["last_withdrawal"] = state["last_withdrawal"]
        if "withdrawal_armed" in state:
            fresh["withdrawal_armed"] = state["withdrawal_armed"]
        return fresh
    return state


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2))
    temp.replace(path)


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
    quote = settings["quote"]
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
    }
    return append_activity_records([record])


def update_dashboard_snapshot(settings, quote_balance, price):
    records = read_activity_records()
    rows = [
        record["row"] for record in records
        if not record.get("simulated")
    ]
    bought = sum(
        (Decimal(row["shares"]) for row in rows if row.get("type") == "Buy"),
        Decimal(0),
    )
    withdrawn = sum(
        (Decimal(row["shares"]) for row in rows if row.get("type") == "TransferOut"),
        Decimal(0),
    )
    tracked_btc = bought - withdrawn
    previous = read_dashboard_snapshot()
    write_dashboard_snapshot({
        "exchange": settings["exchange"],
        "quote": settings["quote"],
        "trackedBtc": format(tracked_btc, "f"),
        "price": format(price, "f"),
        "value": format(tracked_btc * price, "f"),
        "quoteBalance": format(quote_balance, "f"),
        "withdrawThreshold": format(settings["withdraw_threshold"], "f"),
        "nextPurchaseAt": previous.get("nextPurchaseAt"),
        "nextPurchaseStatus": previous.get("nextPurchaseStatus", "Schedule pending"),
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    })


def update_dashboard_schedule(next_purchase_at, status):
    snapshot = read_dashboard_snapshot()
    snapshot["nextPurchaseAt"] = (
        next_purchase_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        if next_purchase_at is not None else None
    )
    snapshot["nextPurchaseStatus"] = status
    write_dashboard_snapshot(snapshot)


def persist_bot_buy(api, order, settings, event_id, fallback_timestamp_ms):
    records = api.normalize_bot_buy(
        order, settings["quote"], settings["base"], event_id, fallback_timestamp_ms
    )
    return records, append_activity_records(records)


def persist_simulated_buy(api, settings, amount, price, quote_amount):
    timestamp_ms = int(time.time() * 1000)
    event_id = f"dry-run:{uuid.uuid4()}"
    row = {
        "datetime": utc_iso_from_ms(timestamp_ms),
        "type": "Buy",
        "identifier": settings["base"],
        "assetType": "Crypto",
        "price": format(price, "f"),
        "shares": format(amount, "f"),
        "amount": format(quote_amount, "f"),
        "currency": settings["quote"],
        "fee": format(api.estimate_taker_fee(f"{settings['base']}-{settings['quote']}", quote_amount), "f"),
        "tax": "0",
    }
    record = {"id": event_id, "simulated": True, "row": row}
    return append_activity_records([record])


def ceil_to_places(value, places):
    quantum = Decimal(1).scaleb(-int(places))
    return value.quantize(quantum, rounding=ROUND_CEILING)



def maybe_withdraw_btc(api, state, settings):
    if not settings["withdrawals_enabled"]:
        return
    if not settings["live"]:
        LOG.warning("Automatic withdrawal is enabled but LIVE_TRADING is false; no withdrawal will be sent.")
        return

    pending = state.get("pending_withdrawal")
    if pending:
        if not getattr(api, "supports_withdrawal_idempotency", False):
            LOG.error("A previous withdrawal request has an unknown outcome. It will not be retried automatically; verify its status with the exchange before taking further action.")
            return
        LOG.warning("Retrying unresolved withdrawal with the same exchange idempotency key.")
        try:
            result = api.withdraw_crypto(pending)
        except requests.RequestException:
            LOG.exception("Withdrawal outcome is uncertain; the same request will be retried next check.")
            return
        context = state.get("pending_withdrawal_context", {})
        persist_bot_withdrawal(api, pending, result, settings, context)
        state["last_withdrawal"] = {
            "id": result.get("id"),
            "amount": pending["amount"],
            "createdAt": result.get("createdAt"),
        }
        state["withdrawal_armed"] = False
        state.pop("pending_withdrawal", None)
        state.pop("pending_withdrawal_context", None)
        save_state(Path("/data/state.json"), state)
        LOG.info("BTC withdrawal submitted: amount=%s BTC, withdrawalId=%s", pending["amount"], result.get("id"))
        return

    available = api.balance("BTC")
    if available <= 0:
        if not state.get("withdrawal_armed", True):
            state["withdrawal_armed"] = True
            save_state(Path("/data/state.json"), state)
            LOG.info("BTC balance is below the withdrawal threshold again; automatic withdrawal is re-armed.")
        return
    price = api.price(f"BTC-{settings['quote']}")
    value = available * price
    if not state.get("withdrawal_armed", True):
        if value < settings["withdraw_threshold"]:
            state["withdrawal_armed"] = True
            save_state(Path("/data/state.json"), state)
            LOG.info("BTC balance is below the withdrawal threshold again; automatic withdrawal is re-armed.")
        else:
            LOG.info("Waiting for %s to reflect the previous withdrawal before another automatic withdrawal.", settings["exchange"])
        return
    if value < settings["withdraw_threshold"]:
        LOG.info("BTC withdrawal threshold not reached: available value %s %s, threshold %s %s.", value, settings["quote"], settings["withdraw_threshold"], settings["quote"])
        return

    amount = available.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    if amount <= 0:
        LOG.info("No withdrawable BTC remains after applying Bitcoin precision.")
        return
    idempotency_key = str(uuid.uuid4())
    withdrawal = api.withdrawal_request(amount, idempotency_key, settings)
    state["pending_withdrawal"] = withdrawal
    state["pending_withdrawal_context"] = {
        "price": format(price, "f"),
        "timestamp_ms": int(time.time() * 1000),
    }
    save_state(Path("/data/state.json"), state)
    try:
        result = api.withdraw_crypto(withdrawal)
    except requests.RequestException:
        LOG.exception("Withdrawal outcome is uncertain; check the exchange before taking further action.")
        if not getattr(api, "supports_withdrawal_idempotency", False):
            LOG.error("This exchange does not support idempotent withdrawal retries; the saved request will not be resent automatically.")
        return
    persist_bot_withdrawal(
        api, withdrawal, result, settings, state.get("pending_withdrawal_context", {})
    )
    state["last_withdrawal"] = {
        "id": result.get("id"),
        "amount": withdrawal["amount"],
        "createdAt": result.get("createdAt"),
    }
    state.pop("pending_withdrawal", None)
    state.pop("pending_withdrawal_context", None)
    state["withdrawal_armed"] = False
    save_state(Path("/data/state.json"), state)
    LOG.warning("BTC withdrawal submitted: amount=%s BTC (fee deducted from amount), value at submission=%s %s, withdrawalId=%s", withdrawal["amount"], value, settings["quote"], result.get("id"))


def defer_order_check(state, state_path, settings):
    delay = settings["order_status_check_seconds"]
    state["pending_order"]["nextStatusCheckAt"] = int(time.time() * 1000) + delay * 1000
    save_state(state_path, state)
    return delay


def reconcile_pending_order(api, state, state_path, settings, check_interval):
    pending = state["pending_order"]
    symbol = f"{settings['base']}-{settings['quote']}"
    delay = settings["order_status_check_seconds"]
    update_dashboard_schedule(None, "Waiting for confirmation that the previous order has ended")
    if pending.get("exchange", settings["exchange"]) != settings["exchange"] or pending.get("symbol", symbol) != symbol:
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
        LOG.info(
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
    settings["first_purchase_done"] = True
    settings["startup_last_buy_at"] = None
    LOG.info(
        "Confirmed %s order %s ended (status=%s); saved %s activity record(s). Recalculating the purchase schedule.",
        settings["exchange"], pending.get("exchangeOrderId") or pending["clientOrderId"], status, added,
    )
    return None


def run_once(api, settings, check_interval, schedule_only=False):
    quote, base = settings["quote"], settings["base"]
    symbol = f"{base}-{quote}"
    state_path = Path("/data/state.json")
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
        LOG.error("Automatic BTC withdrawal check failed: %s", exc)
    # Use the current available quote balance as the DCA budget. This includes
    # funds already on the exchange, even if they were deposited before this cycle.
    balance = api.balance(quote)
    balance_after_simulated_spend = (
        max(Decimal(0), balance - spent) if not settings["live"] else balance
    )
    market = api.market(symbol)
    price = api.price(symbol)
    update_dashboard_snapshot(settings, balance, price)
    if balance_after_simulated_spend <= 0:
        update_dashboard_schedule(None, "Waiting for available quote-currency funds")
        simulated_spent = spent if not settings["live"] else Decimal(0)
        LOG.info(
            "No available %s balance for DCA purchases (balance=%s, simulated spend=%s).",
            quote, balance, simulated_spent,
        )
        return check_interval
    if market.get("status") != "trading":
        update_dashboard_schedule(None, f"Waiting for market status: {market.get('status')}")
        LOG.info("Market %s is %s; waiting.", symbol, market.get("status"))
        return check_interval
    min_quote = Decimal(market["minOrderInQuoteAsset"])
    min_base = Decimal(market["minOrderInBaseAsset"])
    quantity_decimals = int(market["quantityDecimals"])
    notional_decimals = int(market["notionalDecimals"])
    order_amount = ceil_to_places(max(settings["buy_amount"], min_base, min_quote / price), quantity_decimals)
    order_quote = ceil_to_places(order_amount * price, notional_decimals)
    spendable = balance_after_simulated_spend
    available = balance if settings["live"] else balance_after_simulated_spend
    affordable_budget = min(spendable, available)
    possible_buys = int(affordable_budget // order_quote)
    if possible_buys < 1:
        update_dashboard_schedule(None, "Waiting until the balance covers an order")
        LOG.info("Waiting: available balance %s %s; minimum order %s %s.", affordable_budget, quote, order_quote, quote)
        return check_interval

    seconds_left = max(60, (next_deposit - now).total_seconds())
    interval_seconds = max(60, int(seconds_left / possible_buys))
    LOG.info(
        "Recalculated DCA schedule: balance=%s %s; spendable=%s %s; "
        "price=%s %s/%s; estimated order=%s %s (%s %s); "
        "next deposit day=%s; amount of possible buys until next deposit=%s; "
        "purchase interval=%s minutes.",
        balance, quote, affordable_budget, quote, price, quote, base,
        order_amount, base, order_quote, quote, next_deposit.isoformat(),
        possible_buys, int(interval_seconds / 60),
    )
    startup_last_buy = settings.get("startup_last_buy_at")
    if startup_last_buy:
        elapsed = max(0, (now.astimezone(timezone.utc) - startup_last_buy).total_seconds())
        remaining_seconds = max(0, int(interval_seconds - elapsed))
        if remaining_seconds > 0:
            next_purchase_at = now + timedelta(seconds=remaining_seconds)
            update_dashboard_schedule(next_purchase_at, "Scheduled")
            LOG.info(
                "Aligning first purchase after restart to the previous DCA cadence: "
                "last recorded buy=%s; elapsed=%s minutes of %s-minute interval; "
                "next purchase in about %s minutes at %s local time.",
                startup_last_buy.isoformat(timespec="seconds"),
                int(elapsed / 60), int(interval_seconds / 60),
                int(remaining_seconds / 60),
                next_purchase_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
            )
            return remaining_seconds
        update_dashboard_schedule(now, "Due now")
        LOG.info(
            "Previous DCA interval has elapsed since the last recorded buy at %s; "
            "the next purchase is due now.",
            startup_last_buy.isoformat(timespec="seconds"),
        )
    elif not settings["first_purchase_done"]:
        update_dashboard_schedule(now, "Due now")
        LOG.info(
            "No previous real %s purchase is recorded; the first purchase follows "
            "the configured startup delay of %s seconds.",
            base, settings["first_buy_delay_seconds"],
        )
    else:
        last_order = state.get("last_order_at")
        if last_order:
            elapsed = (now - datetime.fromisoformat(last_order)).total_seconds()
            if elapsed < interval_seconds:
                remaining_seconds = max(0, int(interval_seconds - elapsed))
                next_purchase_at = now + timedelta(seconds=remaining_seconds)
                update_dashboard_schedule(next_purchase_at, "Scheduled")
                LOG.info(
                    "Next purchase in about %s minutes, estimated around %s local time "
                    "(recomputed from %s %s available and %s possible buys).",
                    int(remaining_seconds / 60),
                    next_purchase_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
                    affordable_budget, quote, possible_buys,
                )
                return remaining_seconds

    update_dashboard_schedule(now, "Due now")
    if schedule_only:
        # If the computed time has already arrived while recording the last order,
        # wake the normal loop immediately instead of submitting from this pass.
        return 1

    if settings["live"]:
        if available < order_quote:
            update_dashboard_schedule(None, "Waiting until the balance covers an order")
            LOG.info("Waiting for sufficient available balance: %s %s; minimum order %s %s.", available, quote, order_quote, quote)
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
            result = api.buy_base(symbol, order_amount, pending["clientOrderId"])
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
            order_amount, base, order_quote, quote, pending.get("exchangeOrderId"), wait,
        )
        return wait
    else:
        LOG.info("DRY RUN: would market-buy %s %s (estimated %s %s at %s %s/%s); saved as simulated activity.", order_amount, base, order_quote, quote, price, quote, base)
        added = persist_simulated_buy(api, settings, order_amount, price, order_quote)
        LOG.info("Saved %s simulated purchase record(s) for the web UI; excluded from the Parqet CSV export.", added)

    settings["first_purchase_done"] = True
    settings["startup_last_buy_at"] = None
    # Debit budget for dry-run too, to make its monthly schedule representative.
    state["spent"] = str(spent + order_quote)
    state["last_order_at"] = datetime.now(timezone.utc).isoformat()
    state["last_order_quote"] = str(order_quote)
    state.pop("pending_order", None)
    save_state(state_path, state)
    # Refresh balance, market minimum and price after recording the purchase,
    # then return the newly calculated delay to the bot loop.
    return run_once(api, settings, check_interval, schedule_only=True)


def bot_loop(api, settings, interval):
    update_dashboard_schedule(None, "Waiting for startup delay")
    if settings["first_buy_delay_seconds"] > 0:
        LOG.info(
            "Waiting %s seconds after container startup before checking purchase history or calculating the first order.",
            settings["first_buy_delay_seconds"],
        )
        time.sleep(settings["first_buy_delay_seconds"])

    last_buy = latest_recorded_buy_time(settings["base"])
    if last_buy is not None:
        settings["startup_last_buy_at"] = last_buy
        LOG.info(
            "Found previous real %s purchase in persistent activity at %s; "
            "the first purchase after startup will be aligned to the recalculated DCA interval.",
            settings["base"], last_buy.isoformat(timespec="seconds"),
        )
    else:
        LOG.info(
            "No previous real %s purchase is recorded in persistent activity; "
            "the first purchase will be checked now.",
            settings["base"],
        )
    while True:
        next_wait = interval
        try:
            calculated_wait = run_once(api, settings, interval)
            if calculated_wait is not None:
                next_wait = max(1, int(calculated_wait))
        except requests.RequestException as exc:
            LOG.error("%s request failed: %s", settings["exchange"], exc)
            update_dashboard_schedule(None, "Exchange request failed; waiting for the next retry")
        except Exception:
            LOG.exception("Cycle failed")
            update_dashboard_schedule(None, "Cycle failed; waiting for the next retry")
        time.sleep(next_wait)


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s")
    deposit_day = int(os.getenv("DEPOSIT_DAY", "1"))
    if not 1 <= deposit_day <= 31:
        raise ValueError("DEPOSIT_DAY must be between 1 and 31")
    settings = {
        "exchange": os.getenv("EXCHANGE", "KRAKEN").upper(),
        "quote": os.getenv("QUOTE_CURRENCY", "EUR").upper(),
        "base": os.getenv("BASE_ASSET", "BTC").upper(),
        "buy_amount": env_decimal("BUY_AMOUNT", 0),
        "deposit_day": deposit_day,
        "first_buy_delay_seconds": int(os.getenv("FIRST_BUY_DELAY_SECONDS", "60")),
        "first_purchase_done": False,
        "order_status_check_seconds": int(os.getenv("ORDER_STATUS_CHECK_SECONDS", "30")),
        "timezone": os.getenv("TIMEZONE", "Europe/Berlin"),
        "live": os.getenv("LIVE_TRADING", "false").lower() == "true",
        "withdrawals_enabled": os.getenv("WITHDRAWALS_ENABLED", "false").lower() == "true",
        "withdraw_threshold": env_decimal("WITHDRAW_THRESHOLD_QUOTE", 1000),
        "withdraw_address": os.getenv("WITHDRAW_ADDRESS", "").strip(),
        "withdraw_network": os.getenv("WITHDRAW_NETWORK", "BTC").upper(),
        "kraken_withdraw_key": os.getenv("KRAKEN_WITHDRAW_KEY", "").strip(),
    }
    if settings["first_buy_delay_seconds"] < 0:
        raise ValueError("FIRST_BUY_DELAY_SECONDS must be zero or greater")
    if settings["order_status_check_seconds"] < 1:
        raise ValueError("ORDER_STATUS_CHECK_SECONDS must be greater than zero")
    api = create_exchange(settings)
    if settings["withdrawals_enabled"]:
        if settings["base"] != "BTC":
            raise ValueError("Automatic withdrawals currently support BASE_ASSET=BTC only")
        api.validate_withdrawal_settings(settings)
        if settings["withdraw_threshold"] <= 0:
            raise ValueError("WITHDRAW_THRESHOLD_QUOTE must be greater than zero")
    interval = max(60, int(os.getenv("CHECK_INTERVAL_SECONDS", "300")))
    buy_amount_mode = "exchange minimum will be used" if settings["buy_amount"] == 0 else "fixed requested amount"
    LOG.info("Starting on %s: %s; live trading=%s; quote currency=%s; BUY_AMOUNT=%s %s (%s); deposit day=%s; first-buy delay=%ss; timezone=%s; check interval=%ss; order status check delay=%ss", settings["exchange"], f"{settings['base']}-{settings['quote']}", settings["live"], settings["quote"], settings["buy_amount"], settings["base"], buy_amount_mode, deposit_day, settings["first_buy_delay_seconds"], settings["timezone"], interval, settings["order_status_check_seconds"])

    if os.getenv("WEB_UI_ENABLED", "true").lower() == "true":
        port = int(os.getenv("WEB_UI_PORT", "6890"))
        if not 1 <= port <= 65535:
            raise ValueError("WEB_UI_PORT must be between 1 and 65535")
        threading.Thread(target=bot_loop, args=(api, settings, interval), daemon=True).start()
        from web_ui import serve
        serve(settings, port)
    else:
        bot_loop(api, settings, interval)


if __name__ == "__main__":
    main()
