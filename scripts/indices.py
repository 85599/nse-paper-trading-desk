"""NSE index board: spot candles, opening range, futures basis and list breadth.

Read-only market context. Nothing here can size or ticket an options position --
the engine's risk model is cash-segment equity only, and this chart host serves no
option candles; premiums come from `scripts/optionchain.py` instead.
"""
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor' / 'day-trader-skill-codex'
sys.path.insert(0, str(VENDOR))

from india.data import validate  # noqa: E402  (reuse upstream candle validation)
from india.nse import NSEProvider  # noqa: E402

BASE = 'https://charting.nseindia.com'
OFFSET = 19800
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
HEADERS = {'User-Agent': 'day-trader-skill/1.0', 'Accept': 'application/json', 'Referer': BASE + '/'}
DEFAULT_INDEXES = ('NIFTY 50', 'NIFTY BANK', 'NIFTY FIN SERVICE', 'NIFTY IT')
MONTHS = {m: i for i, m in enumerate(
    ('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'), 1)}


def _get(path, params):
    url = BASE + path + '?' + urllib.parse.urlencode(params)
    last = None
    for attempt in range(2):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=20) as response:
                doc = json.load(response)
            if not isinstance(doc, dict) or doc.get('status') is not True:
                raise ValueError('NSE chart returned an unsuccessful response')
            return doc
        except urllib.error.HTTPError as exc:
            last = 'HTTP %d' % exc.code
            if exc.code not in (429, 500, 502, 503, 504) or attempt:
                break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            last = 'network or non-JSON response'
        time.sleep(1)
    raise ValueError('NSE index request failed: %s' % last)


def resolve(name):
    """Look up an index scripcode instead of hardcoding it."""
    rows = _get('/v1/exchanges/symbolsDynamic', {'symbol': name}).get('data') or []
    match = next((r for r in rows if r.get('type') == 'Index' and r.get('symbol') == name
                  and str(r.get('scripcode', '')).isdigit()), None)
    if not match:
        raise ValueError('NSE could not resolve index %s' % name)
    return name, str(match['scripcode'])


def candles(symbol, token, symbol_type, as_of, days=7):
    start = (as_of - dt.timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    doc = _get('/v1/charts/symbolHistoricalData', {
        'token': token, 'fromDate': int(start.timestamp()) + OFFSET,
        'toDate': int(as_of.timestamp()) + OFFSET, 'symbol': symbol,
        'symbolType': symbol_type, 'chartType': 'I', 'timeInterval': 5})
    return NSEProvider.parse(doc, '5m', as_of)


def daily_candles(symbol, token, as_of, days=120):
    """Daily index bars over a long lookback, split into the host's 28-day windows."""
    start = (as_of - dt.timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    bars, cursor = [], start
    while cursor <= as_of:
        end = min(as_of, cursor + dt.timedelta(days=28) - dt.timedelta(seconds=1))
        doc = _get('/v1/charts/symbolHistoricalData', {
            'token': token, 'fromDate': int(cursor.timestamp()) + OFFSET,
            'toDate': int(end.timestamp()) + OFFSET, 'symbol': symbol,
            'symbolType': 'Index', 'chartType': 'D', 'timeInterval': 1})
        bars.extend(b for b in NSEProvider.parse(doc, '1d', end) if cursor <= b.time <= end)
        cursor = end + dt.timedelta(seconds=1)
    return validate(bars)


def front_month(futures):
    """Nearest monthly futures contract, read from the expiry in its symbol."""
    def key(row):
        match = re.search(r'(\d{2})([A-Z]{3})FUT$', row['symbol'].upper())
        return (2000 + int(match.group(1))) * 100 + MONTHS.get(match.group(2), 99) if match else 999999
    dated = [row for row in futures if key(row) < 999999]
    return min(dated, key=key) if dated else None


def session_stats(bars, as_of):
    """Opening range and day levels from completed 5m bars of one session."""
    today = as_of.date()
    day = [b for b in bars if b.time.astimezone(IST).date() == today]
    opening = [b for b in day if b.time.astimezone(IST).time() < dt.time(9, 30)]
    prior = [b for b in bars if b.time.astimezone(IST).date() < today]
    previous = [b for b in prior if b.time.astimezone(IST).date() == (prior[-1].time.astimezone(IST).date()
                                                                       if prior else today)]
    if not day:
        return None
    last = day[-1]
    stats = {
        'bars': len(day),
        'open': day[0].open,
        'last': last.close,
        'as_of': last.time.astimezone(IST).isoformat(),
        'day_high': max(b.high for b in day),
        'day_low': min(b.low for b in day),
        'range_pct': round((max(b.high for b in day) - min(b.low for b in day)) / last.close * 100, 2),
    }
    if previous:
        stats['prev_close'] = previous[-1].close
        stats['prev_high'] = max(b.high for b in previous)
        stats['prev_low'] = min(b.low for b in previous)
        stats['gap_pct'] = round((stats['open'] / previous[-1].close - 1) * 100, 2)
    if len(opening) >= 3:
        high, low = max(b.high for b in opening), min(b.low for b in opening)
        stats['orb'] = {'high': round(high, 2), 'low': round(low, 2), 'mid': round((high + low) / 2, 2),
                        'width_pct': round((high - low) / low * 100, 2)}
        stats['state'] = ('above' if last.close > high else 'below' if last.close < low else 'inside')
        stats['to_break_up'] = round(high - last.close, 2) if last.close <= high else 0.0
        stats['to_break_down'] = round(last.close - low, 2) if last.close >= low else 0.0
    return stats


def breadth(daily_scan):
    """Regime read from the saved daily scan, not from new requests."""
    if not isinstance(daily_scan, dict) or daily_scan.get('mode') != 'daily':
        return None
    rows = [r for r in (daily_scan.get('results') or []) if isinstance(r, dict) and r.get('price')]
    if not rows:
        return None
    above_ma20 = sum(1 for r in rows if r.get('ma20') and r['price'] > r['ma20'])
    above_high20 = sum(1 for r in rows if r.get('high20') and r['price'] > r['high20'])
    strong = sum(1 for r in rows if isinstance(r.get('rsi'), (int, float)) and r['rsi'] >= 50)
    rsis = [r['rsi'] for r in rows if isinstance(r.get('rsi'), (int, float))]
    return {
        'as_of': daily_scan.get('as_of'), 'symbols': len(rows),
        'above_ma20': above_ma20, 'above_ma20_pct': round(above_ma20 / len(rows) * 100, 1),
        'above_20d_high': above_high20, 'rsi_median_50_plus': strong,
        'median_rsi': round(sorted(rsis)[len(rsis) // 2], 1) if rsis else None,
        'setup_matches': len(daily_scan.get('hits') or []),
    }


def board(as_of=None, names=DEFAULT_INDEXES, daily_scan=None, include_futures=True):
    as_of = as_of or dt.datetime.now(IST)
    out = {'source': 'NSE experimental website adapter; provider terms apply',
           'as_of': as_of.isoformat(), 'paper_only': True,
           'option_premiums': 'no option candles on this host; the chain panel reads them live from NSE',
           'indices': [], 'failures': []}
    spot_last = None
    for name in names:
        try:
            symbol, token = resolve(name)
            stats = session_stats(candles(symbol, token, 'Index', as_of), as_of)
            if stats:
                stats['symbol'] = symbol
                out['indices'].append(stats)
                if name == 'NIFTY 50':
                    spot_last = stats['last']
            else:
                out['failures'].append({'symbol': name, 'error': 'no candles for today yet'})
        except (ValueError, KeyError, TypeError) as exc:
            out['failures'].append({'symbol': name, 'error': str(exc)[:200]})
        time.sleep(0.4)
    if include_futures and spot_last:
        try:
            rows = _get('/v1/exchanges/symbolsDynamic', {'symbol': 'NIFTY', 'segment': 'FO'}).get('data') or []
            futures = [r for r in rows if r.get('type') == 'Futures'
                       and str(r.get('scripcode', '')).isdigit() and r['symbol'].startswith('NIFTY')]
            pick = front_month(futures)
            if pick:
                basis = session_stats(candles(pick['symbol'], str(pick['scripcode']), 'Futures', as_of, days=3), as_of)
                if basis:
                    out['futures'] = {'symbol': pick['symbol'], 'last': basis['last'],
                                      'spot_last': spot_last,
                                      'premium_points': round(basis['last'] - spot_last, 2),
                                      'premium_pct': round((basis['last'] / spot_last - 1) * 100, 3),
                                      'as_of': basis['as_of']}
        except (ValueError, KeyError, TypeError) as exc:
            out['failures'].append({'symbol': 'NIFTY futures', 'error': str(exc)[:200]})
    scan_breadth = breadth(daily_scan)
    if scan_breadth:
        out['breadth'] = scan_breadth
    return out


def print_board(doc):
    print('as of %s | %s' % (doc['as_of'], doc['source']))
    for row in doc['indices']:
        print('%-20s %10s  chg %s%%  day %s-%s  orb %s-%s  %s' % (
            row['symbol'], '{:,.2f}'.format(row['last']),
            '{:+.2f}'.format((row['last'] / row.get('prev_close', row['last']) - 1) * 100) if row.get('prev_close') else 'n/a',
            '{:,.2f}'.format(row['day_low']), '{:,.2f}'.format(row['day_high']),
            '{:,.2f}'.format(row['orb']['low']) if row.get('orb') else 'n/a',
            '{:,.2f}'.format(row['orb']['high']) if row.get('orb') else 'n/a',
            row.get('state', 'no range yet')))
        if row.get('orb'):
            print('%-20s to break up %s | down %s | gap open %s%% | prev close %s' % (
                '', '{:,.2f}'.format(row['to_break_up']), '{:,.2f}'.format(row['to_break_down']),
                row.get('gap_pct', 'n/a'),
                '{:,.2f}'.format(row['prev_close']) if row.get('prev_close') else 'n/a'))
    futures = doc.get('futures')
    if futures:
        print('\nfutures  %-20s %10s | premium %s pts (%s%%) as of %s' % (
            futures['symbol'], '{:,.2f}'.format(futures['last']),
            futures['premium_points'], futures['premium_pct'], futures['as_of']))
    breadth_row = doc.get('breadth')
    if breadth_row:
        print('breadth  %d scanned | %s%% above MA20 | %d above 20d high | median RSI %s | %d setup matches' % (
            breadth_row['symbols'], breadth_row['above_ma20_pct'], breadth_row['above_20d_high'],
            breadth_row['median_rsi'], breadth_row['setup_matches']))
    if doc.get('failures'):
        print('\nunavailable:')
        for row in doc['failures']:
            print('  %-20s %s' % (row['symbol'], row['error']))
