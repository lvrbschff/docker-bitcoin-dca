"""Kraken Bitcoin spot trading and withdrawal adapter."""
import base64
import hashlib
import hmac
import logging
import threading
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from decimal import Decimal
from urllib.parse import urlencode

import requests
from exchange_errors import DefinitiveRejection
from read_cache import ReadCache

BASE_URL = "https://api.kraken.com/0"
LOG = logging.getLogger("bitcoin-dca.kraken")
_NONCE_LOCK = threading.Lock()
_LAST_NONCE = 0
_HTTP_IDLE_RESET_SECONDS = 60
_BALANCE_SNAPSHOT_MAX_AGE_SECONDS = 5
_READ_ONLY_PRIVATE_METHODS = frozenset({
    "Balance", "BalanceEx", "TradeVolume", "Ledgers", "TradesHistory",
    "WithdrawStatus", "WithdrawInfo", "OpenOrders", "ClosedOrders", "QueryOrders",
})


class KrakenAPIError(requests.RequestException):
    pass


def _next_nonce():
    global _LAST_NONCE
    with _NONCE_LOCK:
        _LAST_NONCE = max(int(time.time() * 1000), _LAST_NONCE + 1)
        return str(_LAST_NONCE)


def _canonical_asset(value):
    value = str(value or "").upper()
    if value in ("XBT", "XXBT", "BTC"):
        return "BTC"
    if value.startswith("Z") and len(value) == 4:
        return value[1:]
    return value


class Kraken:
    exchange_name = "KRAKEN"
    supports_withdrawal_idempotency = False

    def __init__(self, quote_currency, withdraw_key="", api_key=None, api_secret=None):
        self.key = api_key
        self.secret = api_secret
        if not self.key or not self.secret:
            raise ValueError("Configure the Kraken API key and secret in Settings")
        try:
            self.secret_bytes = base64.b64decode(self.secret, validate=True)
        except Exception as exc:
            raise ValueError("KRAKEN_API_SECRET must be Kraken's base64-encoded API secret") from exc
        self.session = requests.Session()
        self._last_http_activity = None
        self._balance_scope_depth = 0
        self._balance_cache = None
        self._balance_cache_at = None
        self.quote_currency = _canonical_asset(quote_currency)
        self.withdraw_key = withdraw_key
        self._pairs = None
        self._pairs_at = 0
        self._fee_rates = {}
        self._reads = ReadCache()

    def _check_response(self, response):
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("error", [])
        if errors:
            message = "; ".join(errors)
            definitive = ("EOrder:Insufficient funds", "EOrder:Order minimum", "EOrder:Cost minimum",
                          "EOrder:Invalid", "EOrder:Tick size", "EAPI:Invalid key", "EAPI:Invalid signature",
                          "EGeneral:Permission denied", "EGeneral:Invalid arguments", "EFunding:Insufficient funds",
                          "EOrder:Orders limit exceeded", "EOrder:Rate limit exceeded",
                          "EOrder:Domain rate limit exceeded", "EAPI:Rate limit exceeded",
                          "EFunding:Max fee exceeded")
            if not payload.get("result") and all(str(e).startswith(definitive) for e in errors):
                self.invalidate_market()
                raise DefinitiveRejection(message)
            raise KrakenAPIError(message)
        return payload.get("result", {})

    def _reset_http_session(self):
        """Discard pooled connections; the next request establishes a fresh one."""
        self.session.close()
        self.session = requests.Session()
        self._last_http_activity = None
        self._balance_cache = None
        self._balance_cache_at = None

    def _request_result(self, endpoint, send_request, retry_safe):
        started = time.monotonic()
        last_activity = self._last_http_activity
        if last_activity is not None and started - last_activity >= _HTTP_IDLE_RESET_SECONDS:
            LOG.debug("Kraken HTTP connection pool renewed after %.1fs idle.", started - last_activity)
            self._reset_http_session()
        # A submitted action may change funds even if its response is lost.
        if not retry_safe:
            self._balance_cache = None
            self._balance_cache_at = None
            self._reads.invalidate()
        attempts = 3 if retry_safe else 1
        for attempt in range(1, attempts + 1):
            attempt_started = time.monotonic()
            try:
                result = self._check_response(send_request())
                if attempt > 1:
                    LOG.info(
                        "Kraken %s recovered on read attempt %s/%s after %.1fs; "
                        "the retry used a fresh HTTP connection.",
                        endpoint, attempt, attempts, time.monotonic() - started,
                    )
                LOG.debug("Kraken %s completed in %.3fs (attempt %s/%s).",
                          endpoint, time.monotonic() - attempt_started, attempt, attempts)
                return result
            except (requests.Timeout, requests.ConnectionError) as exc:
                elapsed = time.monotonic() - attempt_started
                self._reset_http_session()
                if attempt == attempts:
                    raise KrakenAPIError(
                        f"{endpoint} failed after {attempts} attempt(s) "
                        f"({time.monotonic() - started:.1f}s elapsed): {exc}"
                    ) from exc
                delay = attempt
                LOG.warning(
                    "Kraken %s: %s after %.1fs on read attempt %s/%s; "
                    "retrying in %ss with a fresh HTTP connection.",
                    endpoint, type(exc).__name__, elapsed, attempt, attempts, delay,
                )
                time.sleep(delay)
            finally:
                self._last_http_activity = time.monotonic()

    @contextmanager
    def balance_snapshot(self):
        """Reuse BalanceEx briefly within a bot cycle, never across cycles."""
        if self._balance_scope_depth == 0:
            self._balance_cache = None
            self._balance_cache_at = None
        self._balance_scope_depth += 1
        try:
            with self._reads.cycle():
                yield
        finally:
            self._balance_scope_depth -= 1
            if self._balance_scope_depth == 0:
                self._balance_cache = None
                self._balance_cache_at = None

    def public(self, method, params=None):
        return self._request_result(
            f"public/{method}",
            lambda: self.session.get(
                f"{BASE_URL}/public/{method}", params=params or {}, timeout=(10, 30)
            ),
            retry_safe=True,
        )

    def private(self, method, params=None):
        path = f"/0/private/{method}"

        def send_request():
            # A retry of a read operation needs a fresh nonce and signature.
            data = dict(params or {})
            nonce = _next_nonce()
            data["nonce"] = nonce
            postdata = urlencode(data)
            digest = hashlib.sha256((nonce + postdata).encode()).digest()
            signature = base64.b64encode(
                hmac.new(self.secret_bytes, path.encode() + digest, hashlib.sha512).digest()
            ).decode()
            return self.session.post(
                f"https://api.kraken.com{path}",
                data=postdata,
                headers={
                    "API-Key": self.key,
                    "API-Sign": signature,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=(10, 30),
            )

        return self._request_result(
            f"private/{method}", send_request,
            retry_safe=method in _READ_ONLY_PRIVATE_METHODS,
        )

    def invalidate_market(self):
        self._pairs = None
        self._pairs_at = 0
        self._fee_rates = {}

    def _pair_info(self, symbol, refresh=False):
        bitcoin, quote = symbol.upper().replace("/", "-").split("-", 1)
        if bitcoin != "BTC":
            raise ValueError("Only Bitcoin markets are supported")
        if refresh or self._pairs is None or time.monotonic() - getattr(self, '_pairs_at', 0) >= 60:
            self._pairs = self.public("AssetPairs", {"pair": "XBT" + quote, "assetVersion": 1})
            self._pairs_at = time.monotonic()
        for key, info in self._pairs.items():
            if _canonical_asset(info.get("base")) == "BTC" and _canonical_asset(info.get("quote")) == _canonical_asset(quote):
                normalized = dict(info)
                normalized["_key"] = key
                normalized["_quote"] = _canonical_asset(info.get("quote"))
                normalized["market"] = f"BTC-{normalized['_quote']}"
                return normalized
        raise ValueError(f"Kraken market {symbol} does not exist")

    def market(self, symbol, refresh=False):
        pair = self._pair_info(symbol, refresh=refresh)
        return {
            "market": pair["market"],
            "status": "trading" if pair.get("status") == "online" else pair.get("status", "unknown"),
            "minOrderInBaseAsset": pair.get("ordermin", "0"),
            "minOrderInQuoteAsset": pair.get("costmin", "0"),
            "quantityDecimals": pair.get("lot_decimals", 8),
            "notionalDecimals": pair.get("cost_decimals", pair.get("pair_decimals", 2)),
            "pairDecimals": pair.get("pair_decimals", 2),
            "pair": pair.get("altname", pair["_key"]),
        }

    def price(self, symbol):
        pair = self._pair_info(symbol)
        result = self._reads.read(('price',symbol), lambda: self.public("Ticker", {"pair": pair.get("altname", pair["_key"])}))
        ticker = next(iter(result.values()))
        return Decimal(ticker["c"][0])

    def estimate_taker_fee(self, symbol, quote_amount):
        cached = self._fee_rates.get(symbol)
        if cached is None or time.monotonic() - cached[0] >= 300:
            pair = self._pair_info(symbol)
            result = self.private("TradeVolume", {"pair": pair["_key"]})
            table = result.get("fees", {})
            info = table.get(pair["_key"])
            if info is None and len(table)==1: info=next(iter(table.values()))
            if not isinstance(info,dict) or 'fee' not in info:
                raise ValueError(f"Kraken did not return a taker fee for {symbol}")
            rate=Decimal(str(info['fee']))/100
            self._fee_rates[symbol]=(time.monotonic(),rate)
        return Decimal(str(quote_amount))*self._fee_rates[symbol][1]

    def balance(self, currency):
        if self._balance_scope_depth:
            if (self._balance_cache is None or self._balance_cache_at is None
                    or time.monotonic() - self._balance_cache_at >= _BALANCE_SNAPSHOT_MAX_AGE_SECONDS):
                self._balance_cache = self.private("BalanceEx")
                self._balance_cache_at = time.monotonic()
            balances = self._balance_cache
        else:
            balances = self.private("BalanceEx")
        wanted = _canonical_asset(currency)
        for code, info in balances.items():
            if _canonical_asset(code) == wanted:
                item = info if isinstance(info, dict) else {"balance": info}
                return max(
                    Decimal(0),
                    Decimal(str(item.get("balance", "0")))
                    + Decimal(str(item.get("credit", "0")))
                    - Decimal(str(item.get("credit_used", "0")))
                    - Decimal(str(item.get("hold_trade", "0"))),
                )
        return Decimal(0)

    def btc_withdrawals(self, start_ms=0, end_ms=None):
        params = {
            "asset": "XBT",
            "start": str(int(start_ms / 1000)),
            "end": str(int(end_ms / 1000)) if end_ms is not None else str(int(time.time())),
            "limit": 500,
            "cursor": "true",
        }
        result = []
        cursors = set()
        for _ in range(100):
            page = self.private("WithdrawStatus", dict(params))
            cursor = page.get('cursor') if isinstance(page, dict) else None
            rows = page
            if isinstance(page, dict):
                rows = next((page[key] for key in ('withdrawals', 'items', 'data', 'results')
                             if isinstance(page.get(key), list)), None)
                if rows is None and page and all(isinstance(value, dict) and 'status' in value and 'time' in value for value in page.values()):
                    rows = list(page.values())
                if not page: rows = []
            if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
                raise KrakenAPIError('Unexpected Kraken withdrawal-history response')
            result.extend(rows)
            if not cursor:
                if len(rows) >= params['limit'] and not (isinstance(page, dict) and 'cursor' in page):
                    raise KrakenAPIError('Complete Kraken withdrawal history could not be verified: missing cursor')
                break
            if not isinstance(cursor, str) or cursor in cursors:
                raise KrakenAPIError('Kraken withdrawal-history cursor did not advance')
            cursors.add(cursor)
            params['cursor'] = cursor
        else:
            raise KrakenAPIError('Kraken withdrawal-history page limit reached')
        withdrawals = []
        for item in result:
            status = str(item.get("status", "")).lower()
            timestamp = int(float(item.get("time", 0)) * 1000)
            withdrawals.append({
                "timestamp": timestamp,
                "symbol": "BTC",
                "amount": str(item.get("amount", "0")),
                "fee": str(item.get("fee", "0")),
                "status": "completed" if status == "success" else ("failed" if status == "failure" else status),
                "id": item.get("refid") or "",
                "txId": item.get("txid") or "",
            })
        return withdrawals

    def buy_btc(self, quote, amount, client_order_id):
        pair = self._pair_info(f"BTC-{quote.upper()}")
        userref = int(client_order_id.replace("-", "")[-8:], 16) % 2_147_483_647
        result = self.private("AddOrder", {
            "pair": pair.get("altname", pair["_key"]),
            "type": "buy",
            "ordertype": "market",
            "volume": format(amount, "f"),
            "userref": userref,
        })
        ids = result.get("txid", [])
        return {"status": "submitted", "orderId": ids[0] if ids else None, "userref": userref}

    def open_orders(self):
        result = self.private('OpenOrders', {})
        if not isinstance(result, dict) or not isinstance(result.get('open'), dict):
            raise ValueError('Kraken open orders could not be verified.')
        return list(result['open'].values())

    def _find_order(self, userref, exchange_order_id=None):
        matches = []
        for method, bucket in (("ClosedOrders", "closed"), ("OpenOrders", "open")):
            try:
                result = self.private(method, {"userref": userref})
            except (KrakenAPIError,DefinitiveRejection) as exc:
                if "Permission denied" in str(exc):
                    continue
                raise
            orders = result.get(bucket, {})
            if not isinstance(orders, dict):
                continue
            for order_id, order in orders.items():
                if exchange_order_id:
                    if str(order_id) == str(exchange_order_id):
                        matches.append(order)
                elif str(order.get("userref")) == str(userref):
                    matches.append(order)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise KrakenAPIError(
                "Multiple Kraken orders match the saved reference; refusing to guess which order to record"
            )
        return None

    def get_order(self, symbol, client_order_id, exchange_order_id=None):
        userref = int(client_order_id.replace("-", "")[-8:], 16) % 2_147_483_647
        if exchange_order_id:
            result = self.private("QueryOrders", {"txid": exchange_order_id})
            order = result.get(exchange_order_id)
            if order is not None:
                return order
            order = self._find_order(userref, exchange_order_id)
            if order is not None:
                return order
            raise KrakenAPIError(f"Kraken has not returned order {exchange_order_id} yet")

        # Compatibility for pending orders saved before the Kraken txid was persisted.
        order = self._find_order(userref)
        if order is not None:
            return order
        raise KrakenAPIError("Kraken could not find the order with the saved user reference")
    def normalize_bot_buy(self, order, quote, event_id, fallback_timestamp_ms):
        filled = Decimal(str(order.get("vol_exec", "0")))
        quote_amount = Decimal(str(order.get("cost", "0")))
        if filled <= 0:
            return []
        if quote_amount <= 0:
            quote_amount = filled * Decimal(str(order.get("price", "0")))
        if quote_amount <= 0:
            return []
        price = quote_amount / filled
        fee_quote = Decimal(str(order.get("fee", "0")))
        timestamp_seconds = order.get("closetm") or order.get("opentm")
        timestamp_ms = int(float(timestamp_seconds) * 1000) if timestamp_seconds else fallback_timestamp_ms
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return [{
            "id": f"buy:{event_id}",
            "row": {
                "datetime": timestamp,
                "type": "Buy",
                "identifier": "BTC",
                "assetType": "Crypto",
                "price": format(price, "f"),
                "shares": format(filled, "f"),
                "amount": format(quote_amount, "f"),
                "currency": quote,
                "fee": format(fee_quote, "f"),
                "tax": "0",
            },
        }]

    def validate_withdrawal_settings(self, settings):
        if not self.withdraw_key:
            raise ValueError("KRAKEN_WITHDRAW_KEY is required for Kraken automatic withdrawals")

    def withdrawal_request(self, amount, idempotency_key, settings):
        if not self.withdraw_key:
            raise ValueError("KRAKEN_WITHDRAW_KEY is required when automatic withdrawals are enabled")
        info = self.private("WithdrawInfo", {
            "asset": "XBT",
            "key": self.withdraw_key,
            "amount": format(amount, "f"),
        })
        fee = Decimal(str(info.get("fee", "0")))
        net_amount = amount - fee
        if net_amount <= 0:
            raise ValueError("Kraken withdrawal fee is greater than or equal to the available BTC balance")
        return {
            "asset": "XBT",
            "key": self.withdraw_key,
            "amount": format(net_amount, "f"),
            "fee": format(fee, "f"),
        }

    def withdraw_btc(self, withdrawal):
        if withdrawal.get("asset") != "XBT":
            raise ValueError("Only Bitcoin withdrawals are supported")
        payload = {
            "asset": withdrawal["asset"],
            "key": withdrawal["key"],
            "amount": withdrawal["amount"],
        }
        result = self.private("Withdraw", payload)
        return {"id": result.get("refid"), "refid": result.get("refid")}

