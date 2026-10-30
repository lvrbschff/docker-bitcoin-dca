"""Per-currency public-price cache with bounded, explicitly marked stale fallback."""
import time
from threading import Lock

_values = {}
_locks = {'EUR':Lock(),'USD':Lock()}


def get_price(currency, loader):
    with _locks[currency]:
        value=_values.get(currency)
        now=time.time()
        if value and now-value[1]<30: return (*value,False)
        try:
            result=loader(currency)
            value=(result,time.time())
            _values[currency]=value
            return (*value,False)
        except Exception:
            if value and now-value[1]<=300: return (*value,True)
            raise
