"""Transactional activity ledger with indexed queries and one-time JSON migration."""
import json
import logging
import os
import sqlite3
from storage import write_json_atomic
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock

ACTIVITY_PATH = Path('/webui-data/activity.json')  # Legacy source; never overwritten.
SNAPSHOT_PATH = Path('/webui-data/dashboard.json')
_ACTIVITY_LOCK = RLock()


def _timestamp(row):
    value = datetime.fromisoformat(row['datetime'].replace('Z', '+00:00'))
    return int(value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp() * 1000)


def bitcoin_activity_records(records):
    return [r for r in records if str(r.get('row', {}).get('identifier', '')).upper() == 'BTC']


def withdrawal_gross(record):
    if record.get('withdrawalStatus') in ('failed', 'canceled', 'cancelled'):
        return Decimal(0)
    if record.get('grossWithdrawalAmountBtc') is not None:
        return Decimal(str(record['grossWithdrawalAmountBtc']))
    row = record['row']
    amount = Decimal(str(row.get('shares', '0')))
    if record.get('netWithdrawalAmount') or record.get('exchange')=='KRAKEN':
        fee = record.get('networkFeeBtc')
        if fee is None:
            price = Decimal(str(row.get('price', '0')))
            fee = Decimal(str(row.get('fee', '0'))) / price if price > 0 else Decimal(0)
        amount += Decimal(str(fee))
    return amount


class _DecimalSum:
    def __init__(self): self.value = Decimal(0)
    def step(self, value): self.value += Decimal(str(value or '0'))
    def finalize(self): return format(self.value, 'f')


def _values(record):
    row = record['row']
    return (record['id'], _timestamp(row), row.get('type', ''), row.get('identifier', '').upper(),
            int(bool(record.get('simulated'))), record.get('exchange', ''), row.get('currency', ''),
            row.get('shares', '0'), row.get('amount', '0'), row.get('fee', '0'),
            str(withdrawal_gross(record)) if row.get('type') == 'TransferOut' else '0',
            record.get('withdrawalStatus', 'legacy_unverified') if row.get('type') == 'TransferOut' else '',
            json.dumps(record, ensure_ascii=False))


_INSERT = 'INSERT OR IGNORE INTO activity VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)'


@contextmanager
def _db():
    path = ACTIVITY_PATH.with_suffix('.sqlite3')
    path.parent.mkdir(parents=True, exist_ok=True)
    with _ACTIVITY_LOCK:
        connection = sqlite3.connect(path, timeout=30)
        connection.create_aggregate('decimal_sum', 1, _DecimalSum)
        try:
            os.chmod(path, 0o600)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=FULL')
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS activity(
                    id TEXT PRIMARY KEY, timestamp INTEGER NOT NULL, kind TEXT, asset TEXT,
                    simulated INTEGER, exchange TEXT, currency TEXT, shares TEXT, amount TEXT,
                    fee TEXT, gross TEXT, status TEXT, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS activity_period ON activity(asset,timestamp DESC,id);
                CREATE INDEX IF NOT EXISTS activity_type ON activity(kind,status,timestamp);
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY,value TEXT,updated REAL);
            ''')
            if not connection.execute("SELECT 1 FROM metadata WHERE key='json_migrated'").fetchone():
                source = ACTIVITY_PATH if ACTIVITY_PATH.exists() else None
                if source:
                    payload = json.loads(source.read_text(encoding='utf-8'))
                    records = payload.get('records', []) if isinstance(payload, dict) else payload
                    if not isinstance(records, list): raise ValueError('Invalid legacy activity list')
                    snapshot = read_dashboard_snapshot()
                    for i, record in enumerate(records):
                        if not isinstance(record, dict) or not isinstance(record.get('row'), dict): continue
                        record = dict(record)
                        record.setdefault('id', f'legacy:{i}')
                        record.setdefault('exchange', '')
                        if snapshot.get('exchange'):record.setdefault('legacyExchangeHint',snapshot['exchange'])
                        if record['row'].get('type') == 'TransferOut':
                            record.setdefault('withdrawalStatus', 'legacy_unverified')
                        connection.execute(_INSERT, _values(record))
                connection.execute("INSERT INTO metadata VALUES('json_migrated','1')")
                connection.commit()
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def append_activity_records(records):
    with _db() as db:
        before = db.total_changes
        db.executemany(_INSERT, [_values(r) for r in records])
        return db.total_changes - before


def update_activity_record(record):
    values = _values(record)
    with _db() as db:
        db.execute('UPDATE activity SET timestamp=?,kind=?,asset=?,simulated=?,exchange=?,currency=?,shares=?,amount=?,fee=?,gross=?,status=?,payload=? WHERE id=?', (*values[1:], values[0]))


def read_activity_records():
    with _db() as db:
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM activity ORDER BY timestamp,id')]


def parqet_interval_records(start, end):
    """Return the complete cycle ending at the last selected confirmed withdrawal."""
    with _db() as db:
        db.execute('BEGIN')
        completed = "asset='BTC' AND kind='TransferOut' AND simulated=0 AND status='completed'"
        ending = db.execute('SELECT timestamp,exchange FROM activity WHERE '+completed+' AND timestamp>? AND timestamp<=? ORDER BY timestamp DESC,id DESC LIMIT 1', (start,end)).fetchone()
        if ending is None:
            return []
        latest, exchange = ending
        previous = db.execute('SELECT max(timestamp) FROM activity WHERE '+completed+' AND exchange=? AND timestamp<?', (exchange,latest)).fetchone()[0]
        lower = previous if previous is not None else -1
        return [json.loads(r[0]) for r in db.execute(
            "SELECT payload FROM activity WHERE asset='BTC' AND exchange=? AND timestamp>? AND timestamp<=? ORDER BY timestamp,id",
            (exchange,lower,latest))]


def _totals(db, start, end):
    rows = db.execute("SELECT currency,kind,simulated,status,count(*),decimal_sum(shares),decimal_sum(amount),decimal_sum(fee),decimal_sum(gross) FROM activity WHERE asset='BTC' AND timestamp>? AND timestamp<=? GROUP BY currency,kind,simulated,status", (start,end)).fetchall()
    return [dict(zip(('currency','kind','simulated','status','count','shares','amount','fee','gross'),r)) for r in rows]


def activity_totals(start=0, end=2**63-1):
    with _db() as db:return _totals(db,start,end)


def _balance(groups):
    total=Decimal(0)
    for group in groups:
        if group['simulated']:continue
        if group['kind']=='Buy':total+=Decimal(group['shares'])
        elif group['kind']=='TransferOut':total-=Decimal(group['gross'])
    return total


def ledger_balance():
    return _balance(activity_totals())


def read_report(start,end,page=1,limit=20):
    """Rows, totals and global balance from one consistent SQLite read transaction."""
    limit=max(1,min(1000,int(limit)))
    with _db() as db:
        db.execute('BEGIN')
        where="asset='BTC' AND timestamp>? AND timestamp<=?"
        total=db.execute('SELECT count(*) FROM activity WHERE '+where,(start,end)).fetchone()[0]
        page=max(1,min(int(page),max(1,(total+limit-1)//limit)))
        rows=db.execute('SELECT payload FROM activity WHERE '+where+' ORDER BY timestamp DESC,id DESC LIMIT ? OFFSET ?', (start,end,limit,(page-1)*limit)).fetchall()
        return ([json.loads(r[0]) for r in rows],total,page,_totals(db,start,end),_balance(_totals(db,0,2**63-1)))


def withdrawal_records(unresolved=False):
    query = "SELECT payload FROM activity WHERE asset='BTC' AND kind='TransferOut' AND simulated=0"
    if unresolved: query += " AND status NOT IN ('completed','failed','canceled','cancelled')"
    query += ' ORDER BY timestamp,id'
    with _db() as db: return [json.loads(r[0]) for r in db.execute(query)]


def read_dashboard_snapshot():
    with _ACTIVITY_LOCK:
        source = SNAPSHOT_PATH if SNAPSHOT_PATH.exists() else None
        if not source: return {}
        try:value = json.loads(source.read_text(encoding='utf-8'))
        except (ValueError,OSError):
            logging.getLogger('bitcoin-dca.activity').warning('Derived dashboard snapshot could not be read; rebuilding from the ledger.')
            return {}
        return value if isinstance(value,dict) else {}


def write_dashboard_snapshot(snapshot):
    with _ACTIVITY_LOCK: write_json_atomic(SNAPSHOT_PATH,snapshot)




def cache_get_many(keys):
    if not keys: return {}
    with _db() as db:
        return {key:(json.loads(value),updated) for key,value,updated in db.execute(
            'SELECT key,value,updated FROM cache WHERE key IN ('+','.join('?' for _ in keys)+')',tuple(keys))}


def cache_put_many(values):
    with _db() as db:
        db.executemany('INSERT OR REPLACE INTO cache VALUES(?,?,?)',
                       [(key,json.dumps(value),time.time()) for key,value in values.items()])


def get_activity_record(activity_id):
    with _db() as db:
        row=db.execute('SELECT payload FROM activity WHERE id=?',(activity_id,)).fetchone()
        return json.loads(row[0]) if row else None


def latest_real_buy(exchange=None):
    with _db() as db:
        row=db.execute("SELECT timestamp FROM activity WHERE asset='BTC' AND kind='Buy' AND simulated=0" + (" AND exchange=?" if exchange else "") + " ORDER BY timestamp DESC LIMIT 1", (exchange,) if exchange else ()).fetchone()
        return datetime.fromtimestamp(row[0]/1000,timezone.utc) if row else None
