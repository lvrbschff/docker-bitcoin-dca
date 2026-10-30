"""Bitvavo Bitcoin spot trading and withdrawal adapter."""
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode

import requests
from contextlib import contextmanager
from exchange_errors import DefinitiveRejection
from read_cache import ReadCache

BASE_URL = "https://api.bitvavo.com/v2"


class Bitvavo:
    def __init__(self, api_key=None, api_secret=None):
        self.key = api_key
        self.secret = api_secret
        if not self.key or not self.secret:
            raise ValueError("Configure the Bitvavo API key and secret in Settings")
        self.session = requests.Session()
        self.exchange_name = "BITVAVO"
        self.supports_withdrawal_idempotency = True
        self._reads = ReadCache()
        self._markets = {}
        self._fee_rate = None

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
        if method != "GET": self._reads.invalidate()
        response = self.session.request(
            method, BASE_URL + path, params=params, data=payload if payload else None,
            headers=headers, timeout=15,
        )
        try:
            payload = response.json()
        except ValueError:
            response.raise_for_status()
            if path=='/crypto/withdrawal' and response.status_code==201 and response.content==b'':
                return {}  # Accepted; match destination/time/gross amount in history later.
            raise
        code = payload.get('errorCode') if isinstance(payload,dict) else None
        safe = {102,200,201,202,203,204,205,206,210,211,212,213,215,216,217,219,
                231,300,301,302,303,304,305,306,402,403,405,406,408,409,410,411,413,414,434,435,436,437,438,439}
        if response.status_code < 500 and code in safe:
            self.invalidate_market()
            raise DefinitiveRejection(str(payload.get('error',code)))
        response.raise_for_status()
        return payload

    @contextmanager
    def balance_snapshot(self):
        with self._reads.cycle(): yield

    def invalidate_market(self):
        self._markets = {}
        self._fee_rate = None

    def market(self, symbol, refresh=False):
        cached = self._markets.get(symbol)
        if refresh or cached is None or time.monotonic()-cached[0]>=60:
            markets = self.request('GET','/markets',params={'market':symbol})
            if isinstance(markets,dict): markets=[markets]
            value=next((m for m in markets if m.get('market','').upper()==symbol),None)
            if value is None: raise ValueError(f'Bitvavo market {symbol} does not exist')
            self._markets[symbol]=(time.monotonic(),value)
        return self._markets[symbol][1]

    def price(self, symbol):
        ticker = self._reads.read(('price',symbol), lambda:self.request('GET',f'/{symbol}/ticker/price'))
        return Decimal(ticker['price'])

    def estimate_taker_fee(self, symbol, quote_amount):
        if self._fee_rate is None or time.monotonic()-self._fee_rate[0]>=300:
            account=self.request('GET','/account',private=True)
            self._fee_rate=(time.monotonic(),Decimal(str(account['fees']['taker'])))
        return Decimal(str(quote_amount))*self._fee_rate[1]

    def balance(self, currency):
        accounts=self._reads.read('balances',lambda:self.request('GET','/balance',private=True))
        return next((Decimal(a.get('available','0')) for a in accounts if a.get('symbol','').upper()==currency),Decimal(0))

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

    def withdraw_btc(self, withdrawal):
        if withdrawal.get("symbol") != "BTC":
            raise ValueError("Only Bitcoin withdrawals are supported")
        payload = dict(withdrawal)
        payload['asset'] = payload.pop('symbol')
        return self.request("POST", "/crypto/withdrawal", body=payload, private=True)

    def buy_btc(self, quote, amount, client_order_id):
        body = {
            "market": f"BTC-{quote.upper()}",
            "side": "buy",
            "orderType": "market",
            "amount": str(amount),
            "operatorId": 1,
            "clientOrderId": client_order_id,
        }
        return self.request("POST", "/order", body=body, private=True)

    def btc_withdrawals(self, start_ms=0, end_ms=None):
        params = {"symbol": "BTC", "start": start_ms, "limit": 1000}
        if end_ms is not None:
            params["end"] = end_ms
        return self.request("GET", "/withdrawalHistory", params=params, private=True)

    def get_order(self, symbol, client_order_id, exchange_order_id=None):
        return self.request("GET", "/order", params={"market": symbol, "clientOrderId": client_order_id}, private=True)

    def normalize_bot_buy(self, order, quote, event_id, fallback_timestamp_ms):
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
        elif fee_currency == "BTC":
            fee_quote = fee * price
        elif fee == 0:
            fee_quote = Decimal(0)
        else:
            raise ValueError(f"Cannot store Bitvavo trade fee in {fee_currency}; expected {quote} or BTC")
        timestamp_ms = int(order.get("updated") or order.get("created") or fallback_timestamp_ms)
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

