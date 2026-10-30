"""Reconcile submitted withdrawals conservatively against exchange history."""
import time
import logging
from logging_utils import log_transition
from decimal import Decimal
from datetime import datetime, timezone
from activity import withdrawal_records, update_activity_record


LOG = logging.getLogger("bitcoin-dca.withdrawals")

def reconcile_withdrawals(api, settings, force=False):
    now=time.monotonic()
    previous=getattr(api,'_withdrawal_check_at',None)
    if isinstance(previous,(int,float)) and not force and now-previous<30: return
    api._withdrawal_check_at=now
    waiting=withdrawal_records(unresolved=True)
    waiting.sort(key=lambda r:r.get('withdrawalStatus')!='submitted')
    checked=0
    for record in waiting:
        if record.get('nextVerificationAt',0)>time.time() and not force:continue
        if checked>=4 and not force:break
        if record.get('exchange') not in ('',None,settings['exchange']): continue
        row=record['row']
        stamp=int(datetime.fromisoformat(row['datetime'].replace('Z','+00:00')).timestamp()*1000)
        checked+=1
        candidates=api.btc_withdrawals(max(0,stamp-300000),stamp+300000)
        identifier=record.get('withdrawalId') or record['id'].removeprefix('withdrawal:')
        matches=[]
        for candidate in candidates:
            identity=candidate.get('id') or candidate.get('refid')
            if identity and str(identity)==str(identifier):
                matches.append(candidate);continue
            if record.get('txId') and candidate.get('txId')==record['txId']:
                matches.append(candidate);continue
            if settings['exchange']!='BITVAVO' or not record.get('withdrawalAddress'): continue
            fee=Decimal(str(candidate.get('fee','0')))
            gross=Decimal(str(candidate.get('amount','0')))+fee
            if (candidate.get('address')==record['withdrawalAddress']
                    and abs(int(candidate.get('timestamp',0))-stamp)<=300000
                    and gross==Decimal(str(record.get('grossWithdrawalAmountBtc','-1')))):
                matches.append(candidate)
        if len(matches)!=1:
            legacy = record.get('withdrawalStatus') == 'legacy_unverified'
            overdue = not legacy and time.time() - stamp / 1000 >= 3600
            level = logging.DEBUG if legacy else logging.ERROR if overdue else logging.WARNING
            log_transition(LOG, api, 'withdrawal-verification:' + record['id'], 'overdue' if overdue else 'ambiguous', level,
                           'BTC withdrawal %s has no unique exchange-history match; verification deferred%s.',
                           identifier, '; unresolved for at least one hour, check exchange status' if overdue else '')
            record['nextVerificationAt']=time.time()+(86400 if record.get('withdrawalStatus')=='legacy_unverified' else 300)
            record['verificationNote']='No unique exchange-history match; not exportable.'
            update_activity_record(record)
            continue  # Never guess between indistinguishable withdrawals.
        found=matches[0]
        status=str(found.get('status','')).lower()
        if status in ('success','completed'): status='completed'
        elif status in ('failure','failed','canceled','cancelled'): status='failed'
        else: status='submitted'
        record.pop('nextVerificationAt',None)
        record.pop('verificationNote',None)
        record['exchange']=settings['exchange']
        previous_status = record.get('withdrawalStatus')
        record['withdrawalStatus']=status
        record['exchangeStatus']=str(found.get('status',''))
        record['txId']=found.get('txId','')
        record['lastVerifiedAt']=datetime.now(timezone.utc).isoformat()
        fee=Decimal(str(found.get('fee',record.get('networkFeeBtc','0'))))
        net=Decimal(str(found.get('amount',row['shares'])))
        record['networkFeeBtc']=str(fee)
        record['grossWithdrawalAmountBtc']=str(net+fee)
        record['netWithdrawalAmount']=True
        row['shares']=str(net)
        row['amount']=str(net*Decimal(row['price']))
        row['fee']=str(fee*Decimal(row['price']))
        update_activity_record(record)
        if previous_status != status:
            LOG.log(logging.WARNING if status == 'failed' else logging.INFO,
                    'BTC withdrawal %s status changed: %s -> %s (exchange status=%s)',
                    identifier, previous_status, status, record['exchangeStatus'])

