"""Optional price-level DCA weighting using 200 completed UTC daily closes."""
from datetime import datetime, timezone
from decimal import Decimal, DecimalException
import requests
from activity import cache_get_many, cache_put_many

BASE_URL = 'https://api.kraken.com/0/public/'
DAY_SECONDS = 86400


def tier_factor(z):
    if z <= Decimal('-2'): return Decimal('2')
    if z <= Decimal('-1'): return Decimal('1.5')
    if z <= Decimal('-.5'): return Decimal('1.25')
    if z < Decimal('.5'): return Decimal('1')
    if z < Decimal('1'): return Decimal('.8')
    if z < Decimal('2'): return Decimal('.67')
    return Decimal('.5')


def _public(endpoint, quote, **params):
    response = requests.get(BASE_URL + endpoint, params={'pair': 'XBT' + quote, **params}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get('error') or not isinstance(payload.get('result'), dict):
        raise ValueError('Invalid Kraken public market data')
    rows = [value for key, value in payload['result'].items() if key != 'last']
    if len(rows) != 1: raise ValueError('Missing Kraken market pair')
    return rows[0]


def _closes(rows, day):
    if not isinstance(rows, list): raise ValueError('Missing daily prices')
    closed = {}
    for row in rows:
        stamp = int(row[0])
        if stamp >= day: continue  # The unfinished day must never enter the indicator.
        price = Decimal(str(row[4]))
        if stamp % DAY_SECONDS or not price.is_finite() or price <= 0 or stamp in closed:
            raise ValueError('Invalid daily prices')
        closed[stamp] = price
    expected = [day - offset * DAY_SECONDS for offset in range(200, 0, -1)]
    if any(stamp not in closed for stamp in expected):
        raise ValueError('200 consecutive completed daily prices are required')
    return [closed[stamp] for stamp in expected]


def evaluate(settings, current_price=None, now=None):
    """Return JSON-safe diagnostics; unusable data falls back to ordinary DCA."""
    enabled = bool(settings.get('adaptive_dca_enabled', False))
    strength = Decimal(str(settings.get('adaptive_dca_strength', '1')))
    result = {'enabled': enabled, 'strength': str(strength), 'factor': '1',
              'status': 'disabled' if not enabled else 'neutral', 'quote': settings['quote']}
    if not enabled: return result
    try:
        now = now or datetime.now(timezone.utc)
        day = int(now.timestamp()) // DAY_SECONDS * DAY_SECONDS
        key = 'adaptive:daily:' + settings['quote']
        cached = cache_get_many([key]).get(key)
        saved = cached[0] if cached else None
        closes = None
        if isinstance(saved, dict) and saved.get('day') == day:
            try: closes = _closes(saved.get('rows'), day)
            except (ValueError, TypeError, KeyError, IndexError, DecimalException): pass
        if closes is None:
            rows = _public('OHLC', settings['quote'], interval=1440)
            closes = _closes(rows, day)
            cache_put_many({key: {'day': day, 'rows': [
                [day - (200-index)*DAY_SECONDS, None, None, None, str(value)]
                for index, value in enumerate(closes)]}})
        if current_price is None:
            ticker = _public('Ticker', settings['quote'])
            current_price = ticker['c'][0]
        price = Decimal(str(current_price))
        if not price.is_finite() or price <= 0: raise ValueError('Invalid reference price')
        average = sum(closes) / Decimal(200)
        sigma = (sum((value-average)**2 for value in closes) / Decimal(200)).sqrt()
        if sigma <= 0: raise ValueError('Daily price standard deviation is zero')
        z = (price-average) / sigma
        tier = tier_factor(z)
        factor = tier ** strength
        result.update(status='active' if strength else 'neutral', factor=str(factor), tierFactor=str(tier),
                      mean=str(average), standardDeviation=str(sigma), zScore=str(z),
                      referencePrice=str(price), throughDate=datetime.fromtimestamp(day-DAY_SECONDS, timezone.utc).date().isoformat())
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError, DecimalException, OverflowError, OSError):
        result.update(status='unavailable', reason='Reference prices unavailable; using the base purchase amount')
    return result
