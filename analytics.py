"""Exact per-currency summaries; unlike currencies are never silently added."""
from decimal import Decimal
from activity import bitcoin_activity_records, withdrawal_gross


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


def report(settings, groups, snapshot, records):
    summary=dict(buyCount=0,boughtBtc=Decimal(0),simulatedBuyCount=0,simulatedBtc=Decimal(0),
                 withdrawalCount=0,withdrawnBtc=Decimal(0),grossWithdrawnBtc=Decimal(0),pendingWithdrawalCount=0)
    money={}
    for g in groups:
        if g['simulated']:
            if g['kind']=='Buy':
                summary['simulatedBuyCount']+=g['count'];summary['simulatedBtc']+=Decimal(g['shares'])
            continue
        currency=g['currency']
        values=money.setdefault(currency,dict(buyVolume=Decimal(0),tradeFees=Decimal(0),withdrawalFees=Decimal(0)))
        if g['kind']=='Buy':
            summary['buyCount']+=g['count'];summary['boughtBtc']+=Decimal(g['shares'])
            values['buyVolume']+=Decimal(g['amount']);values['tradeFees']+=Decimal(g['fee'])
        elif g['kind']=='TransferOut':
            summary['grossWithdrawnBtc']+=Decimal(g['gross'])
            if g['status']=='completed':
                summary['withdrawalCount']+=g['count'];summary['withdrawnBtc']+=Decimal(g['shares'])
                values['withdrawalFees']+=Decimal(g['fee'])
            elif g['status'] not in ('failed','canceled','cancelled'):
                summary['pendingWithdrawalCount']+=g['count']
    summary['netBtc']=summary['boughtBtc']-summary['grossWithdrawnBtc']
    same=not money or set(money)=={settings['quote']}
    values=money.get(settings['quote'],{})
    for field in ('buyVolume','tradeFees','withdrawalFees'):
        summary[field]=str(values.get(field,Decimal(0))) if same else None
    summary['averagePrice']=str(Decimal(summary['buyVolume'])/summary['boughtBtc'] if summary['boughtBtc'] else 0) if same else None
    summary={k:str(v) if isinstance(v,Decimal) else v for k,v in summary.items()}
    balance=Decimal(str(snapshot.get('trackedBtc','0')))
    price=Decimal(str(snapshot.get('price','0')))
    return {'quote':settings['quote'],'exchange':settings['exchange'],
            'configuration':{'liveTrading':bool(settings['live']),'buyAmount':str(settings['buy_amount']),
                             'depositDay':int(settings['deposit_day']),'withdrawalsEnabled':bool(settings['withdrawals_enabled']),
                             'withdrawalThreshold':str(settings['withdraw_threshold'])},
            'snapshot':{'exchange':snapshot.get('exchange'),'quote':snapshot.get('quote'),
                        'withdrawalForecastPlan':snapshot.get('withdrawalForecastPlan'),
                        'balance':str(balance),'price':str(price),'value':str(balance*price),
                        'quoteBalance':str(snapshot.get('quoteBalance','0')),'updatedAt':snapshot.get('updatedAt'),
                        'withdrawThreshold':str(settings['withdraw_threshold'])},
            'summary':summary,'summaryByCurrency':{c:{k:str(v) for k,v in m.items()} for c,m in money.items()},
            'activities':[{**r['row'],'activityId':r['id'],'simulated':bool(r.get('simulated')),
                           'exchange':r.get('exchange',''),'withdrawalStatus':r.get('withdrawalStatus')} for r in records]}
