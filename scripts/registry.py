"""Build and verify dated INR security metadata (tick sizes) for the trading desk.

Two sources are combined, and both are recorded per symbol:
  1. Upstox public NSE instrument directory (static file, no account) -- tick_size
     is expressed in PAISE there, so it is divided by 100 to rupees explicitly.
  2. Observed price granularity of NSE website five-minute candles -- the greatest
     common divisor of every OHLC value in paise.

The engine blocks a real paper ticket when metadata is missing or disagrees with
the tick it was given, so this registry is what makes `desk.py setup` runnable
without hand-typing tick sizes. It is a metadata check, not an authentication of
either source; refresh it before relying on it (the desk warns when it goes stale).
"""
import argparse
import datetime as dt
import difflib
import gzip
import json
import math
import os
import sys
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'config'
CACHE = CONFIG / 'instruments'
INSTRUMENTS_URL = 'https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz'
PAISE_PER_INR = 100
DIRECTORY_MAX_AGE_DAYS = 7
METADATA_WINDOW_DAYS = 30

sys.path.insert(0, str(ROOT / 'vendor' / 'day-trader-skill-codex'))


def read_symbol_file(path):
    symbols = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.split('#')[0].strip()
        if line:
            symbols.append(line.split('|')[0].strip().upper())
    return list(dict.fromkeys(symbols))


def download_directory(url=INSTRUMENTS_URL, force=False):
    """Cache the instrument directory locally so scans do not refetch 2 MB."""
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / 'NSE.json.gz'
    fresh = target.exists() and (dt.datetime.now() - dt.datetime.fromtimestamp(target.stat().st_mtime)).days < DIRECTORY_MAX_AGE_DAYS
    if target.exists() and fresh and not force:
        return target, False
    tmp = target.with_suffix('.part')
    request = urllib.request.Request(url, headers={'User-Agent': 'trading-desk/1.0'})
    with urllib.request.urlopen(request, timeout=60) as response, open(tmp, 'wb') as sink:
        sink.write(response.read())
    os.replace(tmp, target)
    return target, True


def equity_records(path):
    """Map NSE trading symbol to its cash-equity directory record."""
    with gzip.open(path) as handle:
        rows = json.load(handle)
    found = {}
    for row in rows:
        if row.get('segment') != 'NSE_EQ' or row.get('instrument_type') != 'EQ':
            continue
        symbol = str(row.get('trading_symbol') or '').strip().upper()
        if symbol:
            found.setdefault(symbol, []).append(row)
    return found


def observed_granularity(data, symbol):
    """GCD of completed-candle OHLC values in paise, and the bar count."""
    try:
        bars = data.history(symbol, '5m')
    except Exception as exc:
        return None, 0, str(exc)
    values = [int(round(v * PAISE_PER_INR)) for bar in bars for v in (bar.open, bar.high, bar.low, bar.close)]
    if not values:
        return None, 0, 'no completed bars'
    divisor = 0
    for value in values:
        divisor = math.gcd(divisor, value)
    return divisor, len(bars), None


def build(symbols, provider='nse', days=5, quiet=False, workers=6, merge=False):
    from india.data import IST
    from workflow.data import MarketData

    directory, fetched = download_directory()
    records = equity_records(directory)
    published_at = dt.datetime.now(IST).isoformat(timespec='seconds')
    effective_from = dt.date.today() - dt.timedelta(days=1)
    effective_to = dt.date.today() + dt.timedelta(days=METADATA_WINDOW_DAYS)
    thread_state = threading.local()

    def candles(symbol):
        """One provider per worker thread; MarketData is not shared across threads."""
        if not hasattr(thread_state, 'data'):
            thread_state.data = MarketData(dt.datetime.now(IST), provider=provider, days=days)
        return observed_granularity(thread_state.data, symbol)

    def resolve(symbol):
        base = symbol[:-3] if symbol.endswith('.NS') else symbol
        matches = records.get(base.upper(), [])
        if len(matches) != 1:
            return None, (symbol, 'instrument directory holds %d cash-equity records' % len(matches)), None
        record = matches[0]
        raw_tick = record.get('tick_size')
        # Directory tick_size is paise; convert explicitly rather than guessing.
        if not isinstance(raw_tick, (int, float)) or isinstance(raw_tick, bool) or raw_tick <= 0:
            return None, (symbol, 'directory: missing or non-positive tick_size'), None
        if abs(raw_tick - round(raw_tick)) > 1e-9:
            return None, (symbol, 'directory: tick_size %s is not whole paise' % raw_tick), None
        paise = int(round(raw_tick))
        granularity, bars, error = candles(symbol)
        if granularity is None:
            return None, (symbol, 'NSE candles unavailable: %s' % error), None
        if granularity % paise == 0:
            tick_paise, basis = paise, 'directory'
        elif paise % granularity == 0 and granularity >= 1:
            # The directory is coarser than prices the exchange actually printed,
            # so its record is stale. Adopt the finer observed grid and say so.
            tick_paise, basis = granularity, 'observed NSE price grid; directory tick was coarser'
        else:
            return None, (symbol, 'directory tick %d paise and observed grid %d paise do not divide' % (paise, granularity)), None
        return {
            'symbol': symbol,
            'tick_size': '%.2f' % (tick_paise / PAISE_PER_INR),
            'currency': 'INR',
            'tick_unit': 'INR',
            'effective_from': effective_from.isoformat(),
            'effective_to': effective_to.isoformat(),
            'published_at': published_at,
            'source': INSTRUMENTS_URL,
            'tick_basis': basis,
            'directory_raw_tick': '%g' % raw_tick,
            'directory_raw_unit': 'paise',
            'directory_isin': record.get('isin', ''),
            'observed_granularity_inr': '%.2f' % (granularity / PAISE_PER_INR),
            'candle_source': 'https://charting.nseindia.com (5m, %s bars)' % bars,
        }, None, (symbol, tick_paise, paise) if basis != 'directory' else None

    import csv
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        resolved = list(pool.map(resolve, symbols))
    rows, problems, adjusted = [], [], []
    for row, problem, note in resolved:
        if row:
            rows.append(row)
            if not quiet:
                print('  ok  %-22s tick INR %s  [%s]' % (row['symbol'], row['tick_size'], row['tick_basis']))
        else:
            problems.append(problem)
        if note:
            adjusted.append(note)

    output = CONFIG / 'security-master.csv'
    header = ['symbol', 'tick_size', 'currency', 'tick_unit', 'effective_from', 'effective_to', 'published_at',
              'source', 'tick_basis', 'directory_raw_tick', 'directory_raw_unit', 'directory_isin',
              'observed_granularity_inr', 'candle_source']
    written = rows
    if merge and output.exists():
        # A single-symbol resolve must not throw away the desk universe already on file.
        resolved = {row['symbol'].upper() for row in rows}
        with open(output, newline='', encoding='utf-8') as handle:
            written = rows + [row for row in csv.DictReader(handle) if row['symbol'].upper() not in resolved]
    with open(output, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(written)
    return output, rows, problems, adjusted, fetched


def _unknown(symbol, problems):
    """Why one symbol has no verified tick, phrased so it can be acted on.

    An exchange directory that holds no cash-equity record under this name almost
    always means the listing was renamed or demerged, not that the file needs a
    rebuild -- so the message names the closest listings actually on file.
    """
    reason = problems[0][1] if problems and len(problems[0]) > 1 else 'no cash-equity record'
    base = symbol[:-3] if symbol.endswith('.NS') else symbol
    try:
        directory, _ = download_directory()
        names = sorted(equity_records(directory))
    except Exception:
        names = sorted(tick_lookup())
    near = [name for name in difflib.get_close_matches(base, names, n=6, cutoff=0.6)]
    near += [name for name in names if name.startswith(base[:5]) and name not in near][:3]
    text = 'No verified tick for %s: %s.' % (symbol, reason)
    if not names:
        return text + ' The desk instrument directory could not be read -- rebuild it from the dashboard.'
    if near and 'cash-equity record' in reason:
        return text + (" NSE's cash-equity list has no stock under that exact name today, so it has"
                       " probably been renamed, demerged or delisted. The closest listings on file: %s."
                       % ', '.join(near))
    return text + ' Try the metadata check again from the dashboard, or pick a symbol that is listed.'


def ensure(symbol, provider='nse', days=5):
    """Verified INR tick for one symbol, resolving it from the exchange directory and
    the observed price grid when the registry has never seen it. A user searching an
    off-listed stock gets a real metadata check, not a demand to edit a config file."""
    symbol = symbol.upper()
    if '.' not in symbol:
        symbol += '.NS'
    master = tick_lookup()
    if symbol in master:
        return master[symbol]
    _, rows, problems, _, _ = build([symbol], provider=provider, days=days, quiet=True, merge=True)
    if not rows:
        raise SystemExit(_unknown(symbol, problems))
    row = rows[0]
    print('registry: %s was not on file -- verified tick INR %s [%s] from the instrument '
          'directory and added it to config/security-master.csv'
          % (row['symbol'], row['tick_size'], row['tick_basis']))
    return float(row['tick_size'])


def tick_lookup(master_path=CONFIG / 'security-master.csv'):
    """symbol -> INR tick, from the generated registry."""
    import csv
    if not Path(master_path).exists():
        return {}
    with open(master_path, newline='', encoding='utf-8') as handle:
        return {row['symbol'].upper(): float(row['tick_size']) for row in csv.DictReader(handle)}


def staleness(master_path=CONFIG / 'security-master.csv', today=None, max_age_days=7):
    import csv
    if not Path(master_path).exists():
        return 'registry missing -- the desk instrument directory has never been built'
    with open(master_path, newline='', encoding='utf-8') as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return 'registry empty -- ask the desk to rebuild its instrument directory'
    today = today or dt.date.today()
    expired = [row['symbol'] for row in rows if row['effective_to'] < today.isoformat()]
    if expired:
        return ('tick metadata past effective_to for %d symbols -- the desk instrument '
                'directory needs a rebuild' % len(expired))
    published = min(row['published_at'][:10] for row in rows)
    age = (today - dt.date.fromisoformat(published)).days
    if age > max_age_days:
        return 'tick registry is %d days old (published %s) -- rebuild it before sizing real paper tickets' % (age, published)
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description='Tick/security-master registry (paper only)')
    parser.add_argument('command', choices=['build', 'verify', 'show'])
    parser.add_argument('--symbols', help='Comma-separated symbols; default is both desk lists')
    parser.add_argument('--provider', default='nse', choices=['nse', 'yahoo', 'upstox'])
    parser.add_argument('--days', type=int, default=5)
    parser.add_argument('--force-download', action='store_true')
    args = parser.parse_args(argv)

    if args.command == 'show':
        master = tick_lookup()
        print(json.dumps(master, indent=2))
        return 0

    symbols = []
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    else:
        for name in ('watchlist-orb.txt', 'universe-liquid.txt'):
            path = CONFIG / name
            if path.exists():
                symbols.extend(read_symbol_file(path))
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        print('No symbols to resolve. Create config/watchlist-orb.txt first.')
        return 1

    if args.force_download:
        download_directory(force=True)

    if args.command == 'verify':
        master = tick_lookup()
        missing = [s for s in symbols if s not in master]
        print('%d symbols in desk lists, %d in registry, %d missing ticks' % (len(symbols), len(symbols) - len(missing), len(missing)))
        for symbol in missing:
            print('  MISSING %s' % symbol)
        note = staleness()
        if note:
            print(note)
        return 1 if missing else 0

    print('Resolving %d symbols against the NSE cash-equity directory and live candles...' % len(symbols))
    output, rows, problems, adjusted, fetched = build(symbols, args.provider, args.days)
    print('\nWrote %s (%d symbols)%s' % (output, len(rows), ' [directory freshly downloaded]' if fetched else ''))
    for symbol, reason in problems:
        print('  SKIP %-22s %s' % (symbol, reason))
    if problems:
        print('\nRemove or correct the skipped symbols in config/*.txt, then rebuild.')
    if adjusted:
        print('\n%d symbols use the finer tick seen in live NSE prices instead of the directory value;' % len(adjusted)
              + ' reconcile against the exchange tick-size circular before trusting them:')
        for symbol, adopted, claimed in adjusted:
            print('  ADJUSTED %-22s adopted INR %.2f  directory said INR %.2f' % (symbol, adopted / PAISE_PER_INR, claimed / PAISE_PER_INR))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
