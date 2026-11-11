"""NSE option chain (index and equity) from the public website, standard library only.

The chain endpoint needs a cookie handshake: a plain GET to the API answers
403/404 until the session has visited /option-chain and kept the `nsit`/`bm_sz`
cookies. That is the whole trick, and it is why the chart host -- which has no
option candles at all -- is not used here.

Context only. Nothing in this file sizes, risks or tickets an option leg: the
engine's model is whole-share cash-segment equity, with no lot size or notional.
"""
import datetime as dt
import http.cookiejar
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://www.nseindia.com'
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9,hi;q=0.8',
    'Referer': BASE + '/option-chain',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'Connection': 'keep-alive',
}
NOTES = ('NSE publishes no IV for strikes with no computed volatility; those arrive as 0 and are '
         'kept as 0 rather than estimated. Context only: option legs are not sized, risked or '
         'ticketed by this desk -- it is configured long-only cash (allow_options false) and the '
         'engine has no option exit or Greeks model. Lot size and tick come from the cached '
         'instrument directory and are printed by `detail` for geometry, not for an order.')


def _opener():
    """A cookie-keeping opener warmed up on the option-chain page."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    request = urllib.request.Request(BASE + '/option-chain', headers=HEADERS)
    with opener.open(request, timeout=25) as response:
        response.read()
    if not [c for c in jar if c.name in ('nsit', 'bm_sz')]:
        raise ValueError('NSE gave no session cookies; the chain API will refuse to answer')
    time.sleep(random.uniform(0.4, 0.9))
    return opener


def _json(opener, path, params=None):
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    request = urllib.request.Request(url, headers=HEADERS)
    with opener.open(request, timeout=25) as response:
        body = response.read()
    if body[:2] == b'\x1f\x8b':
        import gzip
        body = gzip.decompress(body)
    if body[:1] == b'<':
        raise ValueError('NSE served an HTML block page instead of JSON')
    doc = json.loads(body)
    if not isinstance(doc, dict):
        raise ValueError('NSE returned a non-object response')
    return doc


def _with_retry(opener_for, path, params, attempts=3):
    """Re-handshake on bot-protection statuses; NSE blocks intermittently, not permanently."""
    last = None
    opener = None
    for attempt in range(attempts):
        try:
            opener = opener or opener_for()
            return _json(opener, path, params)
        except urllib.error.HTTPError as exc:
            last = 'HTTP %d' % exc.code
            if exc.code not in (401, 403, 429, 500, 502, 503, 504) or attempt == attempts - 1:
                break
            opener = None
            time.sleep(1.5 * (attempt + 1) + random.uniform(0, 1))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError,
                json.JSONDecodeError) as exc:
            last = str(exc) or exc.__class__.__name__
            opener = None
            if attempt == attempts - 1:
                break
            time.sleep(1.5 * (attempt + 1))
    raise ValueError('NSE option chain request failed: %s' % str(last)[:160])


def expiries(symbol):
    doc = _with_retry(_opener, '/api/option-chain-contract-info', {'symbol': symbol.upper()})
    return [str(e) for e in (doc.get('expiryDates') or [])]


def _number(value, default=0.0):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return default


def _leg(record, side):
    row = record.get(side) or {}
    return {'ltp': _number(row.get('lastPrice')), 'iv': _number(row.get('impliedVolatility')),
            'oi': int(_number(row.get('openInterest'))), 'chg_oi': int(_number(row.get('changeinOpenInterest'))),
            'volume': int(_number(row.get('totalTradedVolume'))), 'chg': _number(row.get('change')),
            'bid': _number(row.get('bidprice1') or row.get('bidprice')), 'ask': _number(row.get('askprice1')),
            'underlying': _number(row.get('underlyingValue'), None)}


def summarize(records, underlying, timestamp, expiry, symbol):
    data = [r for r in (records.get('data') or []) if isinstance(r, dict)]
    rows = []
    for record in data:
        strike = _number(record.get('strikePrice'), None)
        if strike is None:
            continue
        rows.append({'strike': strike, 'CE': _leg(record, 'CE'), 'PE': _leg(record, 'PE')})
    rows.sort(key=lambda r: r['strike'])
    spot = _number(underlying, None) or next((r['CE']['underlying'] for r in rows if r['CE']['underlying']), None)
    priced = [r for r in rows if r['CE']['ltp'] or r['PE']['ltp']]
    call_oi = sum(r['CE']['oi'] for r in rows)
    put_oi = sum(r['PE']['oi'] for r in rows)
    call_chg = sum(r['CE']['chg_oi'] for r in rows)
    put_chg = sum(r['PE']['chg_oi'] for r in rows)

    def peak(key, side):
        candidates = [r for r in priced if r[side][key] > 0]
        return max(candidates, key=lambda r: r[side][key])['strike'] if candidates else None

    atm = min(rows, key=lambda r: abs(r['strike'] - spot))['strike'] if spot and rows else None
    step = rows[1]['strike'] - rows[0]['strike'] if len(rows) > 1 else 50

    def window(center, bands=4):
        if center is None:
            return []
        return [r for r in priced if abs(r['strike'] - center) <= bands * step]

    return {'symbol': symbol, 'expiry': expiry, 'timestamp': timestamp,
            'underlying': spot, 'atm_strike': atm, 'strikes': len(rows), 'priced_strikes': len(priced),
            'call_oi': call_oi, 'put_oi': put_oi,
            'pcr_oi': round(put_oi / call_oi, 2) if call_oi else None,
            # A ratio of two OI *changes* is only meaningful while both sides are adding;
            # a negative denominator would flip the sign and read as the opposite sentiment.
            'pcr_chg_oi': (round(put_chg / call_chg, 2)
                           if call_chg > 0 and put_chg > 0 else None),
            'call_oi_change': call_chg, 'put_oi_change': put_chg,
            'resistance_oi': peak('oi', 'CE'), 'support_oi': peak('oi', 'PE'),
            'resistance_buildup': peak('chg_oi', 'CE'), 'support_buildup': peak('chg_oi', 'PE'),
            'near_atm': window(atm), 'rows': rows}


def fetch(symbol='NIFTY', expiry=None, kind=None):
    """One chain: expiries, then the v3 records for the chosen (or nearest) expiry."""
    symbol = symbol.upper().strip()
    kinds = [kind] if kind else ['Indices', 'Equity']
    errors = []
    for attempt_kind in kinds:
        try:
            available = expiries(symbol)
            if not available:
                raise ValueError('NSE listed no expiries for %s' % symbol)
            chosen = expiry or available[0]
            if expiry and expiry not in available:
                raise ValueError('Expiry %s is not listed for %s; try one of %s'
                                 % (expiry, symbol, ', '.join(available[:4])))
            doc = _with_retry(_opener, '/api/option-chain-v3',
                              {'type': attempt_kind, 'symbol': symbol, 'expiry': chosen})
            records = doc.get('records') or {}
            if not records.get('data'):
                raise ValueError('NSE returned no chain rows for %s %s' % (symbol, chosen))
            out = summarize(records, records.get('underlyingValue'), records.get('timestamp'), chosen, symbol)
            out.update({'source': 'NSE option-chain-v3 (public website; provider terms apply)',
                        'kind': attempt_kind, 'as_of': dt.datetime.now(IST).isoformat(),
                        'nse_timestamp_ist': records.get('timestamp'),
                        'expiry_dates': available, 'paper_only': True, 'notes': NOTES})
            return out
        except (ValueError, KeyError, TypeError) as exc:
            errors.append('%s: %s' % (attempt_kind, str(exc)[:180]))
    raise ValueError(' | '.join(errors) or 'NSE option chain failed for %s' % symbol)


def _fmt(value, width=9, decimals=2):
    if value is None:
        return ' ' * (width - 3) + 'n/a'
    return ('{:%d,.%df}' % (width, decimals)).format(value) if isinstance(value, (int, float)) else str(value).rjust(width)


def print_chain(doc, strikes=9):
    print('%s %s | underlying %s | NSE timestamp %s' % (
        doc['symbol'], doc['expiry'], _fmt(doc['underlying'], 11), doc['timestamp']))
    print('  strikes %s (priced %s) | PCR by OI %s | by today\'s OI change %s' % (
        doc['strikes'], doc['priced_strikes'], doc['pcr_oi'],
        'n/a (one side unwound)' if doc['pcr_chg_oi'] is None else doc['pcr_chg_oi']))
    print('  OI resistance %s | OI support %s | today\'s buildup %s call / %s put' % (
        _fmt(doc['resistance_oi'], 10, 0), _fmt(doc['support_oi'], 10, 0),
        _fmt(doc['call_oi_change'], 10, 0), _fmt(doc['put_oi_change'], 10, 0)))
    rows = doc['near_atm'] or doc['rows']
    step = max(1, len(rows) // strikes)
    rows = rows[::step][:strikes] if len(rows) > strikes else rows
    print('\n  %10s | %8s %7s %10s %10s | %8s %7s %10s %10s' % (
        'strike', 'CE ltp', 'CE iv', 'CE oi', 'CE dOI', 'PE ltp', 'PE iv', 'PE oi', 'PE dOI'))
    for row in rows:
        call, put = row['CE'], row['PE']
        mark = '  <= ATM' if doc['atm_strike'] is not None and row['strike'] == doc['atm_strike'] else ''
        print('  %10s | %8s %7s %10s %10s | %8s %7s %10s %10s%s' % (
            _fmt(row['strike'], 10, 0), _fmt(call['ltp'], 8), _fmt(call['iv'], 7, 1),
            _fmt(call['oi'], 10, 0), _fmt(call['chg_oi'], 10, 0), _fmt(put['ltp'], 8),
            _fmt(put['iv'], 7, 1), _fmt(put['oi'], 10, 0), _fmt(put['chg_oi'], 10, 0), mark))
    print('\n  %s' % doc['notes'])
