"""Parqet-compatible aggregate CSV generation from locally recorded activity."""
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO

import requests

from activity import bitcoin_activity_records


def decimal_text(value):
    return format(value, "f")


def _parse_datetime(row):
    value = datetime.fromisoformat(row["datetime"].replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _usd_eur_rates(rows):
    """Fetch one daily historical USD/EUR time series for the selected purchases."""
    usd_rows = [row for row in rows if row.get("currency", "").upper() == "USD"]
    if not usd_rows:
        return {}

    trade_dates = [_parse_datetime(row).date() for row in usd_rows]
    start_date = min(trade_dates) - timedelta(days=7)
    end_date = max(trade_dates)
    response = requests.get(
        f"https://api.frankfurter.dev/v1/{start_date.isoformat()}..{end_date.isoformat()}",
        params={"base": "USD", "symbols": "EUR"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    raw_rates = payload.get("rates", {})
    rates = {
        datetime.strptime(day, "%Y-%m-%d").date(): Decimal(str(value["EUR"]))
        for day, value in raw_rates.items()
        if isinstance(value, dict) and value.get("EUR") is not None
    }
    if not rates:
        raise ValueError("Could not load historical USD/EUR rates for the selected purchases")
    return rates


def _purchase_cost_eur(row, rates):
    currency = row.get("currency", "").upper()
    cost = Decimal(str(row.get("amount", "0"))) + Decimal(str(row.get("fee", "0")))
    if currency == "EUR":
        return cost
    if currency != "USD":
        raise ValueError(f"Parqet export supports EUR or USD purchase records, got {currency or 'unknown'}")
    trade_date = _parse_datetime(row).date()
    applicable = [day for day in rates if day <= trade_date]
    if not applicable:
        raise ValueError(f"No historical USD/EUR rate available for {trade_date.isoformat()}")
    return cost * rates[max(applicable)]


def _withdrawal_net_btc(record, exchange):
    row = record["row"]
    amount = Decimal(str(row.get("shares", "0")))
    if record.get("netWithdrawalAmount"):
        return amount

    # Older Bitvavo records stored the gross amount requested from the exchange.
    # Their fiat fee is converted back to BTC using the recorded withdrawal price.
    if exchange == "BITVAVO":
        price = Decimal(str(row.get("price", "0")))
        fee_quote = Decimal(str(row.get("fee", "0")))
        if price > 0 and fee_quote > 0:
            amount -= fee_quote / price
    return max(amount, Decimal(0))


def parqet_aggregate_rows(records, exchange):
    """Represent a selected withdrawal period as one consolidated Parqet Buy row.

    The purchase cost is all real buys plus their trading fees, converted to EUR.
    The quantity is the net BTC that arrived at the configured withdrawal address.
    """
    records = bitcoin_activity_records(records)
    exchange = str(exchange).upper()
    purchases = [
        record["row"] for record in records
        if not record.get("simulated")
        and record.get("row", {}).get("type") == "Buy"
        and Decimal(str(record["row"].get("shares", "0"))) > 0
    ]
    withdrawals = [
        record for record in records
        if not record.get("simulated")
        and record.get("row", {}).get("type") == "TransferOut"
    ]
    if not purchases or not withdrawals:
        return []

    rates = _usd_eur_rates(purchases)
    total_cost_eur = sum(
        (_purchase_cost_eur(row, rates) for row in purchases), Decimal(0)
    )
    net_withdrawn_btc = sum(
        (_withdrawal_net_btc(record, exchange) for record in withdrawals),
        Decimal(0),
    )
    if total_cost_eur <= 0 or net_withdrawn_btc <= 0:
        return []

    latest_withdrawal = max(withdrawals, key=lambda record: _parse_datetime(record["row"]))
    timestamp = _parse_datetime(latest_withdrawal["row"])
    unit_price = total_cost_eur / net_withdrawn_btc
    return [{
        "datetime": timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "type": "Buy",
        "identifier": "BTC",
        "assetType": "Crypto",
        "price": decimal_text(unit_price),
        "shares": decimal_text(net_withdrawn_btc),
        "amount": decimal_text(total_cost_eur),
        "currency": "EUR",
        # Fees are included in amount and unit price, so a separate fee would double-count them.
        "fee": "0",
        "tax": "0",
    }]


def parqet_csv(rows):
    output = StringIO(newline="")
    fields = ["datetime", "type", "identifier", "assetType", "price", "shares", "amount", "currency", "fee", "tax"]
    writer = csv.DictWriter(output, fieldnames=fields, delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()
