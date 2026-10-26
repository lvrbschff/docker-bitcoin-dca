"""Exchange-configurable DCA bot. Starts in dry-run mode unless explicitly enabled."""
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import calendar
import threading

import requests
from reporting import parqet_csv, parqet_rows

BASE_URL = "https://api.bitvavo.com/v2"
LOG = logging.getLogger("bitvavo-dca")


def env_decimal(name, default=None):
    raw = os.getenv(name)
    if raw is None and default is None:
        raise ValueError(f"Missing required environment variable: {name}")
    value = Decimal(raw if raw is not None else str(default))
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return value


class Bitvavo:
    def __init__(self):
        self.key = os.getenv("BITVAVO_API_KEY", "")
        self.secret = os.getenv("BITVAVO_API_SECRET", "")
        if not self.key or not self.secret:
            raise ValueError("Set BITVAVO_API_KEY and BITVAVO_API_SECRET")
        self.session = requests.Session()
        self.exchange_name = "BITVAVO"
        self.supports_withdrawal_idempotency = True

    def request(self, method, path, params=None, body=None, private=False):
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"Content-Type": "application/json"}
        signed_path = path + ("?" + urlencode(params) if params else "")
        if private:
            timestamp = str(int(time.time() * 1000))
            message = timestamp + method + signed_path + payload
            signature = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
            headers.update({
                "Bitvavo-Access-Key": self.key,
                "Bitvavo-Access-Timestamp": timestamp,
                "Bitvavo-Access-Signature": signature,
                "Bitvavo-Access-Window": "10000",
            })
        response = self.session.request(
            method, BASE_URL + path, params=params, data=payload if payload else None,
            headers=headers, timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def market(self, symbol):
        markets = self.request("GET", "/markets")
        for market in markets:
            if market.get("market", "").upper() == symbol:
                return market
        raise ValueError(f"Bitvavo market {symbol} does not exist")

    def price(self, symbol):
        ticker = self.request("GET", f"/{symbol}/ticker/price")
        return Decimal(ticker["price"])

    def balance(self, currency):
        accounts = self.request("GET", "/balance", private=True)
        for account in accounts:
            if account.get("symbol", "").upper() == currency:
                return Decimal(account.get("available", "0"))
        return Decimal("0")

    def fiat_deposits(self, currency, start_ms, end_ms):
        return self.request(
            "GET", "/depositHistory",
            params={"symbol": currency, "start": start_ms, "end": end_ms, "limit": 1000},
            private=True,
        )

    def withdraw_crypto(self, withdrawal):
        return self.request("POST", "/crypto/withdrawal", body=withdrawal, private=True)

    def buy_base(self, symbol, amount, client_order_id):
        body = {
            "market": symbol,
            "side": "buy",
            "orderType": "market",
            "amount": str(amount),
            "operatorId": 1,
            "clientOrderId": client_order_id,
        }
        return self.request("POST", "/order", body=body, private=True)

    def account_history(self, start_ms, end_ms):
        items = []
        for transaction_type in ("buy", "convert_buy"):
            page = 1
            while True:
                result = self.request(
                    "GET", "/account/history",
                    params={"fromDate": start_ms, "toDate": end_ms, "page": page, "maxItems": 100, "type": transaction_type},
                    private=True,
                )
                items.extend(result.get("items", []))
                if page >= int(result.get("totalPages", 1)):
                    break
                page += 1
        return items

    def btc_withdrawals(self, start_ms=0, end_ms=None):
        params = {"symbol": "BTC", "start": start_ms, "limit": 1000}
        if end_ms is not None:
            params["end"] = end_ms
        return self.request("GET", "/withdrawalHistory", params=params, private=True)

    def historical_price(self, market, timestamp_ms):
        interval_ms = 60_000
        start_ms = timestamp_ms - timestamp_ms % interval_ms
        candles = self.request(
            "GET", f"/{market}/candles",
            params={"interval": "1m", "start": start_ms, "end": start_ms + interval_ms, "limit": 2},
        )
        if not candles:
            raise ValueError(f"No historical {market} price available for withdrawal timestamp {timestamp_ms}")
        return Decimal(candles[0][4])

    def get_order(self, symbol, client_order_id):
        return self.request("GET", "/order", params={"market": symbol, "clientOrderId": client_order_id}, private=True)


def create_exchange(settings):
    exchange = settings["exchange"]
    if exchange == "BITVAVO":
        return Bitvavo()
    if exchange == "KRAKEN":
        from kraken import Kraken
        return Kraken(settings["quote"], settings.get("kraken_withdraw_key", ""))
    raise ValueError("EXCHANGE must be BITVAVO or KRAKEN")


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


def read_state(path, cycle_key):
    if not path.exists():
        return {"cycle": cycle_key, "spent": "0"}
    state = json.loads(path.read_text())
    if state.get("cycle") != cycle_key:
        # A new configured deposit cycle starts a fresh allowance.
        fresh = {"cycle": cycle_key, "spent": "0"}
        if state.get("pending_order"):
            fresh["pending_order"] = state["pending_order"]
        if state.get("parqet_export"):
            fresh["parqet_export"] = state["parqet_export"]
        if state.get("pending_withdrawal"):
            fresh["pending_withdrawal"] = state["pending_withdrawal"]
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


def ceil_to_places(value, places):
    quantum = Decimal(1).scaleb(-int(places))
    return value.quantize(quantum, rounding=ROUND_CEILING)



def csv_decimal(value):
    return format(value, "f")


def export_parqet_cycle(api, state, settings):
    """Export BTC buys since the previous completed BTC withdrawal."""
    tracker = state.get("parqet_export")
    now_ms = int(time.time() * 1000)
    start_ms = int(tracker.get("last_timestamp", 0)) if tracker else 0
    withdrawals = api.btc_withdrawals(start_ms=max(0, start_ms), end_ms=now_ms)
    completed = [
        item for item in withdrawals
        if item.get("symbol", "").upper() == "BTC"
        and item.get("status") == "completed"
        and item.get("txId")
    ]
    if tracker is None:
        latest = max((int(item["timestamp"]) for item in completed), default=0)
        state["parqet_export"] = {
            "initialized": True,
            "last_timestamp": latest,
            "exported_ids": [item["txId"] for item in completed],
        }
        save_state(Path("/data/state.json"), state)
        LOG.info("Initialized Parqet export tracking at the latest existing completed BTC withdrawal; future withdrawals will create CSV exports.")
        return

    exported_ids = set(tracker.get("exported_ids", []))
    pending = sorted(
        (item for item in completed if item["txId"] not in exported_ids),
        key=lambda item: (int(item["timestamp"]), item["txId"]),
    )
    period_start_ms = start_ms
    for withdrawal in pending:
        withdrawal_ms = int(withdrawal["timestamp"])
        rows = parqet_rows(
            api, period_start_ms, withdrawal_ms, settings["quote"], withdrawals=[withdrawal]
        )
        export_dir = Path("/exports")
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(withdrawal_ms / 1000, timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe_id = "".join(char for char in withdrawal["txId"] if char.isalnum())[:16]
        output = export_dir / f"parqet_btc_{stamp}_{safe_id}.csv"
        temporary = output.with_suffix(".csv.tmp")
        temporary.write_text(parqet_csv(rows), encoding="utf-8")
        temporary.replace(output)

        exported_ids.add(withdrawal["txId"])
        tracker["exported_ids"] = sorted(exported_ids)
        tracker["last_timestamp"] = max(int(tracker.get("last_timestamp", 0)), withdrawal_ms)
        period_start_ms = withdrawal_ms
        state["parqet_export"] = tracker
        save_state(Path("/data/state.json"), state)
        LOG.info("Created Parqet CSV %s with %s BTC buys and the completed withdrawal; trade and withdrawal fees included.", output, sum(row["type"] == "Buy" for row in rows))



def maybe_withdraw_btc(api, state, settings):
    if not settings["withdrawals_enabled"]:
        return
    if not settings["live"]:
        LOG.warning("Automatic withdrawal is enabled but LIVE_TRADING is false; no withdrawal will be sent.")
        return

    pending = state.get("pending_withdrawal")
    if pending:
        if not getattr(api, "supports_withdrawal_idempotency", False):
            LOG.error("A previous Kraken withdrawal request has an unknown outcome. It will not be retried automatically; verify it in Kraken and clear pending_withdrawal only after confirming its status.")
            return
        LOG.warning("Retrying unresolved withdrawal with the same exchange idempotency key.")
        try:
            result = api.withdraw_crypto(pending)
        except requests.RequestException:
            LOG.exception("Withdrawal outcome is uncertain; the same request will be retried next check.")
            return
        state["last_withdrawal"] = {
            "id": result.get("id"),
            "amount": pending["amount"],
            "createdAt": result.get("createdAt"),
        }
        state["withdrawal_armed"] = False
        state.pop("pending_withdrawal", None)
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
    withdrawal = api.withdrawal_request(amount, idempotency_key, settings) if hasattr(api, "withdrawal_request") else {
        "symbol": "BTC", "network": settings["withdraw_network"], "address": settings["withdraw_address"],
        "amount": format(amount, "f"), "deductFeeFromAmount": True, "idempotencyKey": idempotency_key,
    }
    state["pending_withdrawal"] = withdrawal
    save_state(Path("/data/state.json"), state)
    try:
        result = api.withdraw_crypto(withdrawal)
    except requests.RequestException:
        LOG.exception("Withdrawal outcome is uncertain; check the exchange before taking further action.")
        if not getattr(api, "supports_withdrawal_idempotency", False):
            LOG.error("This exchange does not support idempotent withdrawal retries; the saved request will not be resent automatically.")
        return
    state["last_withdrawal"] = {
        "id": result.get("id"),
        "amount": withdrawal["amount"],
        "createdAt": result.get("createdAt"),
    }
    state.pop("pending_withdrawal", None)
    state["withdrawal_armed"] = False
    save_state(Path("/data/state.json"), state)
    LOG.warning("BTC withdrawal submitted: amount=%s BTC (fee deducted from amount), value at submission=%s %s, withdrawalId=%s", withdrawal["amount"], value, settings["quote"], result.get("id"))


def run_once(api, settings):
    quote, base = settings["quote"], settings["base"]
    symbol = f"{base}-{quote}"
    state_path = Path("/data/state.json")
    now = local_now(settings["timezone"])
    cycle_key, cycle_start, next_deposit = cycle_dates(now, settings["deposit_day"])
    state = read_state(state_path, cycle_key)
    state["cycle"] = cycle_key
    try:
        export_parqet_cycle(api, state, settings)
    except Exception:
        LOG.exception("Parqet export failed; continuing with the DCA cycle")
    spent = Decimal(state.get("spent", "0"))
    pending = state.get("pending_order")
    if settings["live"] and pending:
        try:
            result = api.get_order(symbol, pending["clientOrderId"])
        except requests.RequestException as exc:
            LOG.error("Previous order %s is unresolved; refusing another order until it can be checked: %s", pending["clientOrderId"], exc)
            return
        order_quote = Decimal(pending["amountQuote"])
        state["spent"] = str(spent + order_quote)
        state["last_order_at"] = datetime.now(timezone.utc).isoformat()
        state["last_order_quote"] = str(order_quote)
        state.pop("pending_order", None)
        save_state(state_path, state)
        LOG.info("Reconciled previous %s order %s; status=%s. Next interval will be recalculated.", settings["exchange"], pending["clientOrderId"], result.get("status", "found"))
        return
    try:
        maybe_withdraw_btc(api, state, settings)
    except requests.RequestException as exc:
        LOG.error("Automatic BTC withdrawal check failed: %s", exc)
    deposits = api.fiat_deposits(
        quote,
        int(cycle_start.timestamp() * 1000),
        int(now.timestamp() * 1000),
    )
    deposited = sum(
        (Decimal(item.get("amount", "0")) for item in deposits
         if item.get("symbol", "").upper() == quote and item.get("status") == "completed"),
        Decimal(0),
    )
    remaining = max(Decimal(0), deposited - spent)
    if remaining <= 0:
        LOG.info("No unspent completed %s deposit remains in this cycle (deposited=%s, spent=%s).", quote, deposited, spent)
        return

    market = api.market(symbol)
    if market.get("status") != "trading":
        LOG.info("Market %s is %s; waiting.", symbol, market.get("status"))
        return
    price = api.price(symbol)
    min_quote = Decimal(market["minOrderInQuoteAsset"])
    min_base = Decimal(market["minOrderInBaseAsset"])
    quantity_decimals = int(market["quantityDecimals"])
    notional_decimals = int(market["notionalDecimals"])
    order_amount = ceil_to_places(max(settings["buy_amount"], min_base, min_quote / price), quantity_decimals)
    order_quote = ceil_to_places(order_amount * price, notional_decimals)
    reserve = deposited * settings["fee_buffer"] / Decimal(100)
    spendable = max(Decimal(0), remaining - reserve)
    available = api.balance(quote) if settings["live"] else spendable
    affordable_budget = min(spendable, available)
    possible_buys = int(affordable_budget // order_quote)
    if possible_buys < 1:
        LOG.info("Waiting: available monthly funds %s %s; minimum order %s %s.", affordable_budget, quote, order_quote, quote)
        return

    seconds_left = max(60, (next_deposit - now).total_seconds())
    interval_seconds = max(60, int(seconds_left / possible_buys))
    last_order = state.get("last_order_at")
    if last_order:
        elapsed = (now - datetime.fromisoformat(last_order)).total_seconds()
        if elapsed < interval_seconds:
            LOG.info("Next purchase in about %s minutes (recomputed from %s %s available and %s remaining monthly buys).", int((interval_seconds - elapsed) / 60), affordable_budget, quote, possible_buys)
            return
    else:
        LOG.info("First purchase is due now; next interval will be recalculated from current funds and minimum order.")

    if settings["live"]:
        if available < order_quote:
            LOG.info("Waiting for deposit: available %s %s; minimum order %s %s.", available, quote, order_quote, quote)
            return
        pending = {"clientOrderId": str(uuid.uuid4()), "amountQuote": str(order_quote), "amountBase": str(order_amount)}
        state["pending_order"] = pending
        save_state(state_path, state)
        result = api.buy_base(symbol, order_amount, pending["clientOrderId"])
        status = result.get("status", "submitted")
        LOG.info("Order submitted: %s %s (estimated %s %s); status=%s orderId=%s", order_amount, base, order_quote, quote, status, result.get("orderId"))
    else:
        LOG.info("DRY RUN: would market-buy %s %s (estimated %s %s at %s %s/%s).", order_amount, base, order_quote, quote, price, quote, base)

    # Debit budget for dry-run too, to make its monthly schedule representative.
    state["spent"] = str(spent + order_quote)
    state["last_order_at"] = datetime.now(timezone.utc).isoformat()
    state["last_order_quote"] = str(order_quote)
    state.pop("pending_order", None)
    save_state(state_path, state)


def bot_loop(api, settings, interval):
    while True:
        try:
            run_once(api, settings)
        except requests.RequestException as exc:
            LOG.error("%s request failed: %s", settings["exchange"], exc)
        except Exception:
            LOG.exception("Cycle failed")
        time.sleep(interval)


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s")
    deposit_day = int(os.getenv("DEPOSIT_DAY", "1"))
    if not 1 <= deposit_day <= 31:
        raise ValueError("DEPOSIT_DAY must be between 1 and 31")
    settings = {
        "exchange": os.getenv("EXCHANGE", "BITVAVO").upper(),
        "quote": os.getenv("QUOTE_CURRENCY", "EUR").upper(),
        "base": os.getenv("BASE_ASSET", "BTC").upper(),
        "buy_amount": env_decimal("BUY_AMOUNT", 0),
        "deposit_day": deposit_day,
        "timezone": os.getenv("TIMEZONE", "Europe/Berlin"),
        "live": os.getenv("LIVE_TRADING", "false").lower() == "true",
        "fee_buffer": env_decimal("FEE_BUFFER_PERCENT", 1),
        "withdrawals_enabled": os.getenv("WITHDRAWALS_ENABLED", "false").lower() == "true",
        "withdraw_threshold": env_decimal("WITHDRAW_THRESHOLD_QUOTE", 1000),
        "withdraw_address": os.getenv("WITHDRAW_ADDRESS", "").strip(),
        "withdraw_network": os.getenv("WITHDRAW_NETWORK", "BTC").upper(),
        "kraken_withdraw_key": os.getenv("KRAKEN_WITHDRAW_KEY", "").strip(),
    }
    if settings["exchange"] not in ("BITVAVO", "KRAKEN"):
        raise ValueError("EXCHANGE must be BITVAVO or KRAKEN")
    if settings["withdrawals_enabled"]:
        if settings["base"] != "BTC":
            raise ValueError("Automatic withdrawals currently support BASE_ASSET=BTC only")
        if settings["exchange"] == "BITVAVO":
            if not settings["withdraw_address"]:
                raise ValueError("WITHDRAW_ADDRESS is required for Bitvavo automatic withdrawals")
            if not settings["withdraw_network"]:
                raise ValueError("WITHDRAW_NETWORK is required for Bitvavo automatic withdrawals")
        elif not settings["kraken_withdraw_key"]:
            raise ValueError("KRAKEN_WITHDRAW_KEY is required for Kraken automatic withdrawals")
        if settings["withdraw_threshold"] <= 0:
            raise ValueError("WITHDRAW_THRESHOLD_QUOTE must be greater than zero")
    interval = max(60, int(os.getenv("CHECK_INTERVAL_SECONDS", "300")))
    api = create_exchange(settings)
    LOG.info("Starting on %s: %s; live trading=%s; quote currency=%s; requested buy amount=%s %s; deposit day=%s; timezone=%s; check interval=%ss", settings["exchange"], f"{settings['base']}-{settings['quote']}", settings["live"], settings["quote"], settings["buy_amount"], settings["base"], deposit_day, settings["timezone"], interval)

    if os.getenv("WEB_UI_ENABLED", "true").lower() == "true":
        port = int(os.getenv("WEB_UI_PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError("WEB_UI_PORT must be between 1 and 65535")
        threading.Thread(target=bot_loop, args=(api, settings, interval), daemon=True).start()
        from web_ui import serve
        serve(settings, port)
    else:
        bot_loop(api, settings, interval)


if __name__ == "__main__":
    main()
