"""Shared Parqet-compatible CSV conversion helpers."""
import csv
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO


def decimal_text(value):
    return format(value, "f")


def parqet_rows(api, start_ms, end_ms, quote_currency, withdrawals=None):
    """Build Parqet CSV rows for BTC transactions in (start_ms, end_ms]."""
    txs = api.account_history(start_ms, end_ms)
    rows = []
    for tx in txs:
        if tx.get("type") not in ("buy", "convert_buy"):
            continue
        if tx.get("receivedCurrency", "").upper() != "BTC":
            continue
        executed = datetime.fromisoformat(tx["executedAt"].replace("Z", "+00:00"))
        executed_ms = int(executed.timestamp() * 1000)
        if not (start_ms < executed_ms <= end_ms):
            continue
        shares = Decimal(tx.get("receivedAmount", "0"))
        if shares <= 0:
            continue
        gross = Decimal(tx.get("priceAmount", "0"))
        if gross <= 0:
            continue
        currency = tx.get("priceCurrency", quote_currency).upper()
        if currency != quote_currency:
            raise ValueError(f"Cannot export BTC trade in {currency}: configured QUOTE_CURRENCY is {quote_currency}")
        unit_price = gross / shares
        fee_currency = tx.get("feesCurrency", currency).upper()
        fee_amount = Decimal(tx.get("feesAmount", "0"))
        if fee_currency == currency:
            fee_quote = fee_amount
        elif fee_currency == "BTC":
            fee_quote = fee_amount * unit_price
        elif fee_amount == 0:
            fee_quote = Decimal(0)
        else:
            raise ValueError(f"Cannot convert trade fee from {fee_currency} to {currency}")
        rows.append({
            "datetime": tx["executedAt"],
            "type": "Buy",
            "identifier": "BTC",
            "assetType": "Crypto",
            "price": decimal_text(unit_price),
            "shares": decimal_text(shares),
            "amount": decimal_text(gross),
            "currency": currency,
            "fee": decimal_text(fee_quote),
            "tax": "0",
            "_timestamp": executed_ms,
        })

    if withdrawals is None:
        withdrawals = api.btc_withdrawals(start_ms=max(0, start_ms), end_ms=end_ms)
    for withdrawal in withdrawals:
        if withdrawal.get("symbol", "").upper() != "BTC" or withdrawal.get("status") != "completed":
            continue
        timestamp_ms = int(withdrawal["timestamp"])
        if not (start_ms < timestamp_ms <= end_ms):
            continue
        amount = Decimal(withdrawal.get("amount", "0"))
        fee = Decimal(withdrawal.get("fee", "0"))
        price = api.historical_price(f"BTC-{quote_currency}", timestamp_ms)
        if amount <= 0 or price <= 0:
            continue
        rows.append({
            "datetime": datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "type": "TransferOut",
            "identifier": "BTC",
            "assetType": "Crypto",
            "price": decimal_text(price),
            "shares": decimal_text(amount),
            "amount": decimal_text(amount * price),
            "currency": quote_currency,
            "fee": decimal_text(fee * price),
            "tax": "0",
            "_timestamp": timestamp_ms,
        })

    rows.sort(key=lambda row: (row["_timestamp"], row["type"]))
    for row in rows:
        row.pop("_timestamp")
    return rows


def parqet_csv(rows):
    output = StringIO(newline="")
    fields = ["datetime", "type", "identifier", "assetType", "price", "shares", "amount", "currency", "fee", "tax"]
    writer = csv.DictWriter(output, fieldnames=fields, delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()
