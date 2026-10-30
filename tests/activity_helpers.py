"""Record-list fixtures for report presentation tests; production aggregates in SQLite."""
from decimal import Decimal
from app.data.activity import bitcoin_activity_records, withdrawal_gross


def totals_from_records(records):
    groups={}
    for record in bitcoin_activity_records(records):
        row=record['row']
        status=record.get('withdrawalStatus','legacy_unverified') if row['type']=='TransferOut' else ''
        key=(row.get('currency',''),row['type'],bool(record.get('simulated')),status)
        g=groups.setdefault(key,dict(currency=key[0],kind=key[1],simulated=key[2],status=status,
                                    count=0,shares=Decimal(0),amount=Decimal(0),fee=Decimal(0),gross=Decimal(0)))
        g['count']+=1
        for name in ('shares','amount','fee'):g[name]+=Decimal(str(row.get(name,'0')))
        if row['type']=='TransferOut':g['gross']+=withdrawal_gross(record)
    return list(groups.values())

