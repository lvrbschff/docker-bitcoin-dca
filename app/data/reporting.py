"""Parqet-compatible aggregate CSV generation from locally recorded activity."""
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO

import requests

from app.data.activity import bitcoin_activity_records, cache_get_many, cache_put_many
from bisect import bisect_right
import time


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

    trade_dates = sorted({_parse_datetime(row).date() for row in usd_rows})
    keys = {day:f'fx:USD:EUR:{day.isoformat()}' for day in trade_dates}
    cached = cache_get_many(list(keys.values()))
    resolved = {day:Decimal(cached[keys[day]][0]['rate']) for day in trade_dates
                if keys[day] in cached and time.time()-cached[keys[day]][1]<86400}
    missing = [day for day in trade_dates if day not in resolved]
    if not missing: return resolved
    start_date = min(missing)-timedelta(days=7)
    response=requests.get(f'https://api.frankfurter.dev/v1/{start_date.isoformat()}..{max(missing).isoformat()}',
                          params={'base':'USD','symbols':'EUR'},timeout=15)
    response.raise_for_status()
    rates={datetime.strptime(day,'%Y-%m-%d').date():Decimal(str(value['EUR']))
           for day,value in response.json().get('rates',{}).items() if isinstance(value,dict) and 'EUR' in value}
    dates=sorted(rates)
    persisted={}
    for day in missing:
        index=bisect_right(dates,day)-1
        if index<0: raise ValueError(f'No historical USD/EUR rate available for {day}')
        rate=rates[dates[index]]
        if not rate.is_finite() or rate<=0: raise ValueError('Invalid historical USD/EUR rate')
        resolved[day]=rate
        persisted[keys[day]]={'rate':str(rate),'referenceDate':dates[index].isoformat()}
    cache_put_many(persisted)
    return resolved


def _purchase_cost_eur(row, rates):
    currency = row.get("currency", "").upper()
    cost = Decimal(str(row.get("amount", "0"))) + Decimal(str(row.get("fee", "0")))
    if currency == "EUR":
        return cost
    if currency != "USD":
        raise ValueError(f"Parqet export supports EUR or USD purchase records, got {currency or 'unknown'}")
    trade_date = _parse_datetime(row).date()
    if trade_date not in rates:
        dates=sorted(rates)
        index=bisect_right(dates,trade_date)-1
        if index<0: raise ValueError(f'No historical USD/EUR rate available for {trade_date}')
        rates[trade_date]=rates[dates[index]]
    return cost*rates[trade_date]


def _withdrawal_net_btc(record, exchange):
    row = record["row"]
    amount = Decimal(str(row.get("shares", "0")))
    if record.get("netWithdrawalAmount"):
        return amount

    # Older Bitvavo records stored the gross amount requested from the exchange.
    # Their fiat fee is converted back to BTC using the recorded withdrawal price.
    if record.get("exchange", exchange) == "BITVAVO":
        price = Decimal(str(row.get("price", "0")))
        fee_quote = Decimal(str(row.get("fee", "0")))
        if price > 0 and fee_quote > 0:
            amount -= fee_quote / price
    return max(amount, Decimal(0))


def parqet_aggregate_rows(records, exchange):
    """Consolidate the last completed withdrawal cycle, excluding later activity."""
    records = bitcoin_activity_records(records)
    exchange = str(exchange).upper()
    withdrawals = [
        record for record in records
        if not record.get("simulated")
        and record.get("row", {}).get("type") == "TransferOut"
        and record.get("withdrawalStatus") == "completed"
    ]
    if not withdrawals:
        return []
    timestamp = max(_parse_datetime(record["row"]) for record in withdrawals)
    ending = max(withdrawals, key=lambda record: _parse_datetime(record["row"]))
    cycle_exchange = ending.get('exchange') or exchange
    records = [record for record in records if (record.get('exchange') or exchange) == cycle_exchange]
    withdrawals = [record for record in withdrawals if (record.get('exchange') or exchange) == cycle_exchange]
    previous_times = [_parse_datetime(record["row"]) for record in withdrawals
                      if _parse_datetime(record["row"]) < timestamp]
    previous = max(previous_times) if previous_times else None
    purchases = [
        record["row"] for record in records
        if not record.get("simulated")
        and record.get("row", {}).get("type") == "Buy"
        and Decimal(str(record["row"].get("shares", "0"))) > 0
        and _parse_datetime(record["row"]) <= timestamp
        and (previous is None or _parse_datetime(record["row"]) > previous)
    ]
    withdrawals = [record for record in withdrawals if _parse_datetime(record["row"]) == timestamp]
    if not purchases:
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

