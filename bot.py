"""Budget-capped Bitvavo DCA bot. Starts in dry-run mode unless explicitly enabled."""
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import calendar

import requests

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

    def get_order(self, symbol, client_order_id):
        return self.request("GET", "/order", params={"market": symbol, "clientOrderId": client_order_id}, private=True)


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


def run_once(api, settings):
    quote, base = settings["quote"], settings["base"]
    symbol = f"{base}-{quote}"
    state_path = Path("/data/state.json")
    now = local_now(settings["timezone"])
    cycle_key, cycle_start, next_deposit = cycle_dates(now, settings["deposit_day"])
    state = read_state(state_path, cycle_key)
    state["cycle"] = cycle_key
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
        LOG.info("Reconciled previous Bitvavo order %s; status=%s. Next interval will be recalculated.", pending["clientOrderId"], result.get("status", "found"))
        return
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


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s")
    deposit_day = int(os.getenv("DEPOSIT_DAY", "1"))
    if not 1 <= deposit_day <= 31:
        raise ValueError("DEPOSIT_DAY must be between 1 and 31")
    settings = {
        "quote": os.getenv("QUOTE_CURRENCY", "EUR").upper(),
        "base": os.getenv("BASE_ASSET", "BTC").upper(),
        "buy_amount": env_decimal("BUY_AMOUNT", 0),
        "deposit_day": deposit_day,
        "timezone": os.getenv("TIMEZONE", "Europe/Berlin"),
        "live": os.getenv("LIVE_TRADING", "false").lower() == "true",
        "fee_buffer": env_decimal("FEE_BUFFER_PERCENT", 1),
    }
    interval = max(60, int(os.getenv("CHECK_INTERVAL_SECONDS", "300")))
    api = Bitvavo()
    LOG.info("Starting %s; live trading=%s; quote currency=%s; requested buy amount=%s %s; deposit day=%s; timezone=%s; check interval=%ss", f"{settings['base']}-{settings['quote']}", settings["live"], settings["quote"], settings["buy_amount"], settings["base"], deposit_day, settings["timezone"], interval)
    while True:
        try:
            run_once(api, settings)
        except requests.RequestException as exc:
            LOG.error("Bitvavo request failed: %s", exc)
        except Exception:
            LOG.exception("Cycle failed")
        time.sleep(interval)


if __name__ == "__main__":
    main()
