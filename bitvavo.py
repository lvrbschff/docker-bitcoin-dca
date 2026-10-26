"""Bitvavo Spot API adapter implementing the bot exchange interface."""
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode

import requests

BASE_URL = "https://api.bitvavo.com/v2"


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

    def estimate_taker_fee(self, symbol, quote_amount):
        account = self.request("GET", "/account", private=True)
        rate = Decimal(str(account["fees"]["taker"]))
        return Decimal(str(quote_amount)) * rate

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

    def validate_withdrawal_settings(self, settings):
        if not settings.get("withdraw_address"):
            raise ValueError("WITHDRAW_ADDRESS is required for Bitvavo automatic withdrawals")
        if not settings.get("withdraw_network"):
            raise ValueError("WITHDRAW_NETWORK is required for Bitvavo automatic withdrawals")

    def withdrawal_request(self, amount, idempotency_key, settings):
        return {
            "symbol": "BTC",
            "network": settings["withdraw_network"],
            "address": settings["withdraw_address"],
            "amount": format(amount, "f"),
            "deductFeeFromAmount": True,
            "idempotencyKey": idempotency_key,
        }

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

    def normalize_bot_buy(self, order, quote, base, event_id, fallback_timestamp_ms):
        filled = Decimal(str(order.get("filledAmount", "0")))
        quote_amount = Decimal(str(order.get("filledAmountQuote", "0")))
        fills = order.get("fills") or []
        if quote_amount <= 0 and fills:
            quote_amount = sum(
                (Decimal(str(fill.get("amount", "0"))) * Decimal(str(fill.get("price", "0"))) for fill in fills),
                Decimal(0),
            )
        if filled <= 0 and fills:
            filled = sum((Decimal(str(fill.get("amount", "0"))) for fill in fills), Decimal(0))
        if filled <= 0 or quote_amount <= 0:
            return []
        price = quote_amount / filled
        fee = Decimal(str(order.get("feePaid", "0")))
        fee_currency = str(order.get("feeCurrency", quote)).upper()
        if fee == 0 and fills:
            fill_fees = [fill for fill in fills if fill.get("fee") is not None]
            if fill_fees:
                fee = sum((Decimal(str(fill["fee"])) for fill in fill_fees), Decimal(0))
                fee_currency = str(fill_fees[0].get("feeCurrency", fee_currency)).upper()
        if fee_currency == quote:
            fee_quote = fee
        elif fee_currency == base:
            fee_quote = fee * price
        elif fee == 0:
            fee_quote = Decimal(0)
        else:
            raise ValueError(f"Cannot store Bitvavo trade fee in {fee_currency}; expected {quote} or {base}")
        timestamp_ms = int(order.get("updated") or order.get("created") or fallback_timestamp_ms)
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
