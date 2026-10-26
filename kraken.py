"""Kraken Spot REST adapter used by the DCA bot."""
import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode

import requests

BASE_URL = "https://api.kraken.com/0"
LOG = logging.getLogger("bitvavo-dca.kraken")
_NONCE_LOCK = threading.Lock()
_LAST_NONCE = 0


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
    if value.startswith("X") and len(value) > 3:
        return value[1:]
    return value


class Kraken:
    exchange_name = "KRAKEN"
    supports_withdrawal_idempotency = False

    def __init__(self, quote_currency, withdraw_key=""):
        self.key = os.getenv("KRAKEN_API_KEY", "")
        self.secret = os.getenv("KRAKEN_API_SECRET", "")
        if not self.key or not self.secret:
            raise ValueError("Set KRAKEN_API_KEY and KRAKEN_API_SECRET for EXCHANGE=KRAKEN")
        try:
            self.secret_bytes = base64.b64decode(self.secret, validate=True)
        except Exception as exc:
            raise ValueError("KRAKEN_API_SECRET must be Kraken's base64-encoded API secret") from exc
        self.session = requests.Session()
        self.quote_currency = _canonical_asset(quote_currency)
        self.withdraw_key = withdraw_key
        self._assets = None
        self._pairs = None

    def _check_response(self, response):
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("error", [])
        if errors:
            raise KrakenAPIError("; ".join(errors))
        return payload.get("result", {})

    def public(self, method, params=None):
        response = self.session.get(
            f"{BASE_URL}/public/{method}", params=params or {}, timeout=20
        )
        return self._check_response(response)

    def private(self, method, params=None):
        path = f"/0/private/{method}"
        data = dict(params or {})
        nonce = _next_nonce()
        data["nonce"] = nonce
        postdata = urlencode(data)
        digest = hashlib.sha256((nonce + postdata).encode()).digest()
        signature = base64.b64encode(
            hmac.new(self.secret_bytes, path.encode() + digest, hashlib.sha512).digest()
        ).decode()
        response = self.session.post(
            f"https://api.kraken.com{path}",
            data=postdata,
            headers={
                "API-Key": self.key,
                "API-Sign": signature,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=20,
        )
        return self._check_response(response)

    def _asset_info(self):
        if self._assets is None:
            self._assets = self.public("Assets")
        return self._assets

    def _display_asset(self, code):
        code = str(code).upper()
        info = self._asset_info().get(code)
        if info:
            alias = info.get("altname", code)
            return "BTC" if alias.upper() == "XBT" else alias.upper()
        return _canonical_asset(code)

    def _internal_asset(self, symbol):
        wanted = _canonical_asset(symbol)
        for code, info in self._asset_info().items():
            alias = info.get("altname", code).upper()
            if _canonical_asset(code) == wanted or _canonical_asset(alias) == wanted:
                return code
        return symbol.upper()

    def _pair_info(self, symbol):
        base, quote = symbol.upper().replace("/", "-").split("-", 1)
        if self._pairs is None:
            self._pairs = self.public("AssetPairs", {"assetVersion": 1})
        for key, info in self._pairs.items():
            if _canonical_asset(info.get("base")) == _canonical_asset(base) and _canonical_asset(info.get("quote")) == _canonical_asset(quote):
                normalized = dict(info)
                normalized["_key"] = key
                normalized["_base"] = _canonical_asset(info.get("base"))
                normalized["_quote"] = _canonical_asset(info.get("quote"))
                normalized["market"] = f"{normalized['_base']}-{normalized['_quote']}"
                return normalized
        raise ValueError(f"Kraken market {symbol} does not exist")

    def market(self, symbol):
        pair = self._pair_info(symbol)
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
        result = self.public("Ticker", {"pair": pair.get("altname", pair["_key"])})
        ticker = next(iter(result.values()))
        return Decimal(ticker["c"][0])

    def estimate_taker_fee(self, symbol, quote_amount):
        pair = self._pair_info(symbol)
        result = self.private("TradeVolume", {"pair": pair["_key"]})
        fee_table = result.get("fees", {})
        fee_info = fee_table.get(pair["_key"])
        if fee_info is None and len(fee_table) == 1:
            fee_info = next(iter(fee_table.values()))
        if not isinstance(fee_info, dict) or "fee" not in fee_info:
            raise ValueError(f"Kraken did not return a taker fee for {symbol}")
        # Kraken returns percentage points (for example 0.2600 for 0.26%).
        rate = Decimal(str(fee_info["fee"])) / Decimal(100)
        return Decimal(str(quote_amount)) * rate

    def balance(self, currency):
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

    def _ledgers(self, start_s, end_s, ledger_type, asset=None):
        offset = 0
        entries = []
        while True:
            params = {
                "start": int(start_s),
                "end": int(end_s),
                "type": ledger_type,
                "ofs": offset,
            }
            if asset:
                params["asset"] = asset
            result = self.private("Ledgers", params)
            ledger = result.get("ledger", {})
            page = list(ledger.values())
            entries.extend(page)
            count = int(result.get("count", len(entries)))
            if len(page) < 50 or offset + len(page) >= count:
                return entries
            offset += len(page)

    def fiat_deposits(self, currency, start_ms, end_ms):
        internal = self._internal_asset(currency)
        entries = self._ledgers(start_ms // 1000, end_ms // 1000, "deposit", internal)
        deposits = []
        for entry in entries:
            if self._display_asset(entry.get("asset")) != _canonical_asset(currency):
                continue
            deposits.append({
                "symbol": _canonical_asset(currency),
                "amount": str(entry.get("amount", "0")),
                "status": "completed",
                "timestamp": int(float(entry.get("time", 0)) * 1000),
                "refid": entry.get("refid"),
            })
        return deposits

    def _pair_trade_history(self, pair, start_ms, end_ms):
        params = {
            "type": "all",
            "trades": "true",
            "pair": pair.get("altname", pair["_key"]),
            "start": int(start_ms // 1000),
            "end": int(end_ms // 1000),
            "limit": 100,
            "with_cursor": "true",
        }
        trades = []
        while True:
            result = self.private("TradesHistory", params)
            trades.extend(result.get("trades", {}).items())
            cursor = result.get("cursor", {})
            next_cursor = cursor.get("next") if isinstance(cursor, dict) else None
            if not next_cursor:
                break
            params = {"with_cursor": "true", "cursor": next_cursor, "limit": 100}
        return trades

    def account_history(self, start_ms, end_ms):
        pair = self._pair_info(f"BTC-{self.quote_currency}")
        rows = []
        for trade_id, trade in self._pair_trade_history(pair, start_ms, end_ms):
            if trade.get("type") != "buy":
                continue
            timestamp_ms = int(float(trade.get("time", 0)) * 1000)
            if not (start_ms < timestamp_ms <= end_ms):
                continue
            volume = Decimal(trade.get("vol", "0"))
            price = Decimal(trade.get("price", "0"))
            cost = Decimal(trade.get("cost", "0"))
            rows.append({
                "transactionId": trade_id,
                "executedAt": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(timestamp_ms / 1000)) + ".000Z",
                "type": "buy",
                "priceCurrency": self.quote_currency,
                "priceAmount": str(cost if cost > 0 else volume * price),
                "sentCurrency": self.quote_currency,
                "sentAmount": str(cost),
                "receivedCurrency": "BTC",
                "receivedAmount": str(volume),
                "feesCurrency": self.quote_currency,
                "feesAmount": str(trade.get("fee", "0")),
            })
        return rows

    def btc_withdrawals(self, start_ms=0, end_ms=None):
        params = {
            "asset": "XBT",
            "start": str(int(start_ms / 1000)),
            "end": str(int(end_ms / 1000)) if end_ms is not None else str(int(time.time())),
            "limit": 500,
            "cursor": "true",
        }
        result = self.private("WithdrawStatus", params)
        if isinstance(result, dict):
            # Kraken's paginated response may be wrapped instead of returned as
            # the plain list shown in the legacy endpoint examples.
            rows = next(
                (result[key] for key in ("withdrawals", "items", "data", "results")
                 if isinstance(result.get(key), list)),
                None,
            )
            if rows is None and result and all(
                isinstance(value, dict) and "status" in value and "time" in value
                for value in result.values()
            ):
                rows = list(result.values())
            if rows is None and not result:
                rows = []
            if rows is None:
                keys = ", ".join(sorted(result.keys())[:10])
                raise KrakenAPIError(
                    f"Unexpected Kraken withdrawal-history response object "
                    f"(keys: {keys or 'none'})"
                )
            result = rows
        if not isinstance(result, list):
            raise KrakenAPIError(
                f"Unexpected Kraken withdrawal-history response type: "
                f"{type(result).__name__}"
            )
        withdrawals = []
        for item in result:
            status = str(item.get("status", "")).lower()
            timestamp = int(float(item.get("time", 0)) * 1000)
            withdrawals.append({
                "timestamp": timestamp,
                "symbol": "BTC",
                "amount": str(item.get("amount", "0")),
                "fee": str(item.get("fee", "0")),
                "status": "completed" if status in ("success", "settled") else status,
                "txId": item.get("refid") or item.get("txid") or "",
            })
        return withdrawals

    def historical_price(self, market, timestamp_ms):
        pair = self._pair_info(market)
        timestamp_s = timestamp_ms // 1000
        for interval in (1, 60, 1440):
            result = self.public(
                "OHLC",
                {"pair": pair.get("altname", pair["_key"]), "interval": interval, "since": timestamp_s},
            )
            candles = next((value for key, value in result.items() if key != "last"), [])
            for candle in candles:
                candle_start = int(candle[0])
                if candle_start <= timestamp_s < candle_start + interval * 60:
                    return Decimal(str(candle[4]))
        raise ValueError(f"No historical Kraken {market} price available at {timestamp_ms}")

    def buy_base(self, symbol, amount, client_order_id):
        pair = self._pair_info(symbol)
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

    def get_order(self, symbol, client_order_id, exchange_order_id=None):
        if exchange_order_id:
            result = self.private("QueryOrders", {"txid": exchange_order_id})
            order = result.get(exchange_order_id)
            if order is not None:
                return order
            if len(result) == 1:
                return next(iter(result.values()))
            raise KrakenAPIError(f"Kraken could not find order {exchange_order_id}")

        # Compatibility for pending orders saved before the Kraken txid was persisted.
        userref = int(client_order_id.replace("-", "")[-8:], 16) % 2_147_483_647
        matches = []
        for method, bucket in (("ClosedOrders", "closed"), ("OpenOrders", "open")):
            try:
                result = self.private(method, {"userref": userref})
            except KrakenAPIError as exc:
                if "Permission denied" in str(exc):
                    continue
                raise
            orders = result.get(bucket, {})
            if isinstance(orders, dict):
                matches.extend(
                    order for order in orders.values()
                    if str(order.get("userref")) == str(userref)
                )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise KrakenAPIError(
                "Multiple Kraken orders share the saved user reference; refusing to guess which order to record"
            )
        raise KrakenAPIError("Kraken could not find the order with the saved user reference")

    def normalize_bot_buy(self, order, quote, base, event_id, fallback_timestamp_ms):
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
                "identifier": base,
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

    def withdraw_crypto(self, withdrawal):
        payload = {
            "asset": withdrawal["asset"],
            "key": withdrawal["key"],
            "amount": withdrawal["amount"],
        }
        result = self.private("Withdraw", payload)
        return {"id": result.get("refid"), "refid": result.get("refid")}
