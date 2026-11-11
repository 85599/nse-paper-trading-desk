"""One-command Indian day-trading desk on top of the day-trader-skill-codex engine.

Paper only. Nothing here places an order: the engine keeps live execution
disabled, and a ticket is never approved or filled unless you type your own
reply into this terminal.

    python scripts/desk.py doctor
    python scripts/desk.py premarket
    python scripts/desk.py setup RELIANCE.NS --stop or-mid --target 2R
    python scripts/desk.py journal
"""
import argparse
import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import registry

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor' / 'day-trader-skill-codex'
WORKSPACE = ROOT / 'workspace'
CONFIG = ROOT / 'config' / 'desk.json'
MASTER = CONFIG.parent / 'security-master.csv'
ORB_LIST = CONFIG.parent / 'watchlist-orb.txt'
DAILY_LIST = CONFIG.parent / 'universe-liquid.txt'
TOOLS = {'scanner': 'scan.py', 'entry': 'entry.py', 'exit': 'exit.py', 'timemachine': 'timemachine.py',
         'budget': 'size.py', 'risk': 'check.py', 'broker': 'ticket.py', 'alerts': 'alerts.py',
         'news': 'news.py', 'analyst': 'facts.py', 'pelosi': 'ptr.py'}
# Each vendor entrypoint declares its own accepted flags; passing an unsupported
# one would make argparse read the value as the positional command.
MARKET_ROLES = {'scanner', 'entry', 'exit', 'timemachine', 'risk', 'broker', 'alerts', 'news'}
MASTER_ROLES = {'scanner', 'entry', 'exit', 'risk', 'broker'}
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# Engine reason strings carry the rupee sign, which the legacy Windows console
# (cp1252) cannot encode. UTF-8 output with replacement keeps the desk readable.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace')


def tool_path(role):
    if not VENDOR.exists():
        raise SystemExit('Engine missing at %s -- clone jackson-kanchanapally/day-trader-skill-codex there' % VENDOR)
    return VENDOR / ('trader-%s' % role) / TOOLS[role]


def base_args(role, args, days=None, market=True):
    """Flags one vendor call needs; each role only accepts the flags it declares."""
    common = ['--workspace', str(WORKSPACE), '--config', str(CONFIG)]
    if market and role in MARKET_ROLES:
        common += ['--provider', args.provider, '--days', str(days or args.days)]
    if role in MASTER_ROLES and MASTER.exists():
        common += ['--security-master', str(MASTER)]
    if getattr(args, 'as_of', None):
        common += ['--as-of', args.as_of]
    for flag, attr in (('--capital', 'capital'), ('--risk', 'risk')):
        value = getattr(args, attr, None)
        if value is not None:
            common += [flag, str(value)]
    return common


def call(role, argv, timeout=300):
    """Run one vendor tool. Returns (exit code, parsed JSON report)."""
    command = [sys.executable, str(tool_path(role))] + [str(v) for v in argv]
    environment = {**dict(os.environ), 'PYTHONIOENCODING': 'utf-8'}
    try:
        done = subprocess.run(command, cwd=str(VENDOR), capture_output=True, text=True, encoding='utf-8',
                              errors='replace', env=environment, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 1, {'status': 'UNAVAILABLE', 'error': 'timed out after %ds' % timeout}
    text = (done.stdout or '').strip()
    if not text:
        return done.returncode, {'status': 'UNAVAILABLE', 'error': (done.stderr or 'no output').strip()[-500:]}
    try:
        return done.returncode, json.loads(text)
    except ValueError:
        lines = text.splitlines()
        start = next((i for i, line in enumerate(lines) if line.startswith('{')), None)
        if start is not None:
            try:
                return done.returncode, json.loads('\n'.join(lines[start:]))
            except ValueError:
                pass
        return done.returncode, {'status': 'UNAVAILABLE', 'error': text[-500:]}


def watch_poll(role, argv, timeout):
    """Read the poller's reports as one line per check.

    The engine prints a whole JSON document for every poll, and a pretty one for
    the check that ends the watch. Printed straight into a dashboard panel that is
    five minutes of braces with no clock in them, so the desk keeps the fields you
    act on -- bar time, status, why -- and drops the rest. Anything that is not a
    report stays visible, because that is usually a crash.
    """
    command = [sys.executable, str(tool_path(role))] + [str(v) for v in argv]
    environment = {**dict(os.environ), 'PYTHONIOENCODING': 'utf-8'}
    proc = subprocess.Popen(command, cwd=str(VENDOR), env=environment, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
    decoder = json.JSONDecoder()
    deadline = time.monotonic() + timeout
    buffer, noise, checks, last = '', [], 0, {}
    while True:
        if time.monotonic() > deadline:
            proc.kill()
            noise.append('the desk stopped polling after %ds' % timeout)
            break
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        buffer += line
        while buffer.strip():
            stripped = buffer.lstrip()
            if stripped[0] != '{':
                cut = stripped.find('\n')
                if cut < 0:
                    break
                noise.append(stripped[:cut].strip())
                buffer = stripped[cut + 1:]
                continue
            try:
                doc, size = decoder.raw_decode(stripped)
            except ValueError:
                break
            buffer = stripped[size:]
            if isinstance(doc, dict):
                checks += 1
                last = doc
                print('  %s %-9s %s' % (str(doc.get('now') or '')[11:19],
                                        doc.get('status') or 'report',
                                        str(doc.get('reason') or doc.get('error') or '')[:110]),
                      flush=True)
    proc.wait()
    for row in noise[-6:]:
        if row:
            print('  %s' % row[:200])
    return checks, last


def header(title):
    print('\n== %s ==' % title)


def money(value):
    try:
        return 'INR %s' % '{:,.2f}'.format(float(value))
    except (TypeError, ValueError):
        return 'n/a'


def num(value, dp=2):
    try:
        return '%.*f' % (dp, float(value))
    except (TypeError, ValueError):
        return 'n/a'


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def as_of_arg(args):
    """--as-of as an aware IST datetime; naive input is read as IST, like the engine."""
    if not getattr(args, 'as_of', None):
        return None
    value = dt.datetime.fromisoformat(args.as_of)
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


def failure(doc):
    return isinstance(doc, dict) and doc.get('status') == 'UNAVAILABLE' and 'error' in doc


def unavailable(doc, label):
    print('%s unavailable -- %s' % (label, str(doc.get('error', 'no reason given'))[:400]))
    return 1


def tick_for(symbol, args):
    override = getattr(args, 'tick', None)
    if override:
        return float(override)
    return registry.ensure(symbol)


def symbols_in(args, default_list):
    if getattr(args, 'symbols', None):
        return [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    path = Path(args.list) if getattr(args, 'list', None) else default_list
    if not path.exists():
        raise SystemExit('Symbol list missing: %s' % path)
    return registry.read_symbol_file(path)


def teamflow_roles():
    import teamflow
    return teamflow.ROLES


def daily_universe(symbols=None, limit=None):
    """Equities to read next: saved-scan setup matches first, then the rest of the scan."""
    if symbols:
        names = [s.strip().upper() for s in symbols.split(',') if s.strip()]
    else:
        scan = read_json(WORKSPACE / 'watchlist.json')
        names = []
        for row in (scan.get('hits') or []) + (scan.get('results') or []):
            symbol = str(row.get('symbol') or '').upper()
            if symbol.endswith('.NS') and symbol not in names:
                names.append(symbol)
        if not names:
            names = [s.upper() for s in registry.read_symbol_file(DAILY_LIST) if s.upper().endswith('.NS')]
    return names if limit is None else names[:limit]


# ---------- environment, clock ----------

def cmd_doctor(args):
    header('environment')
    print('python    : %s (%s)' % (sys.version.split()[0], sys.executable))
    print('project   : %s' % ROOT)
    print('engine    : %s' % (VENDOR if VENDOR.exists() else 'MISSING -- clone day-trader-skill-codex into vendor/'))
    print('workspace : %s' % WORKSPACE)
    print('node      : %s' % (shutil.which('node') or 'not found (only the dashboard build needs Node 20.19+/22.12+)'))
    print('platform  : the engine is tested on macOS/Linux; india/job.py needs fcntl, so this desk polls in the '
          'foreground instead of installing a background job')

    header('desk configuration')
    if not CONFIG.exists():
        print('config missing: %s' % CONFIG)
        return 1
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    print('capital %s | risk per trade %s | entries %s-%s IST | flatten %s' %
          (money(cfg['budget']), money(cfg['max_loss_per_trade_inr']), cfg['no_entries_before'],
           cfg['no_entries_after'], cfg['flat_by']))
    print('daily loss cap %s | max %s trades/day | max %s orders/day | max position %s%% | cash buffer %s%%' %
          (money(cfg['max_daily_loss_inr']), cfg['max_trades_per_day'], cfg['max_orders_per_day'],
           cfg['max_position_pct'], cfg['cash_buffer_pct']))
    print('paper %s | broker %s | options/margin/short/fractional disabled | whole shares only' %
          (cfg['paper'], cfg['broker']))

    header('tick registry')
    master = registry.tick_lookup(MASTER)
    print('%d symbols with dated INR ticks' % len(master))
    print(registry.staleness(MASTER) or 'registry is fresh')
    for name, path in (('orb list', ORB_LIST), ('daily list', DAILY_LIST)):
        listed = registry.read_symbol_file(path) if path.exists() else []
        missing = [s for s in listed if s not in master]
        print('%-10s %3d symbols, %d without ticks' % (name, len(listed), len(missing)))
        for symbol in missing:
            print('            no tick: %s' % symbol)

    header('market clock')
    rc, clock = call('alerts', base_args('alerts', args, market=False) + ['clock'])
    if failure(clock):
        return unavailable(clock, 'clock')
    print('%s | %s IST | trading day %s | session %s-%s | entries %s-%s IST | flatten %s' % (
        clock.get('date'), clock.get('now_ist'), 'yes' if clock.get('open') else 'no',
        clock.get('opens'), clock.get('closes'), cfg['no_entries_before'], cfg['no_entries_after'], cfg['flat_by']))
    print('calendar coverage is 2026 only; for 2027 supply a sourced --calendar file')

    header('candle feed')
    # The probe scans into a throwaway workspace: writing it to the real one
    # would replace the saved daily scan the dashboard's Opportunities view reads.
    probe = WORKSPACE / '.probe'
    probe.mkdir(parents=True, exist_ok=True)
    argv = ['--workspace', str(probe), '--config', str(CONFIG),
            '--provider', args.provider, '--days', '3']
    if MASTER.exists():
        argv += ['--security-master', str(MASTER)]
    if getattr(args, 'as_of', None):
        argv += ['--as-of', args.as_of]
    rc, doc = call('scanner', argv + ['orb', '--symbols', 'TCS.NS', '--or-minutes', 15], timeout=120)
    if failure(doc):
        print('NSE candles unavailable -- %s' % str(doc.get('error'))[:200])
        print('   NSE public charting is experimental, can be delayed or rate limited. For a licensed feed set '
              'UPSTOX_ANALYTICS_TOKEN in your own environment (never in chat) and pass --provider upstox.')
        return 1
    rows = doc.get('results') or doc.get('hits', [])
    opening = (rows[0].get('opening_range') or {}) if rows else {}
    print('NSE candles ok: %s scanned, %s failed | TCS opening range %s/%s bars, complete %s' %
          (doc.get('scanned'), len(doc.get('failed', [])), opening.get('bars'), opening.get('of'),
           opening.get('complete')))
    print('source: %s' % doc.get('source'))
    print('probed in %s -- your saved scan in workspace/ is untouched' % probe)

    header('paper ledger')
    rc, doc = call('broker', base_args('broker', args, market=False) + ['portfolio'])
    if failure(doc):
        print('no ledger yet -- the first risk check creates %s' % (WORKSPACE / 'state' / 'paper.sqlite'))
        return 0
    print('cash %s | initial capital %s | open positions %d | realized %s' %
          (money(doc.get('cash')), money(doc.get('initial_capital')), len(doc.get('positions', [])),
           money(doc.get('realized_pnl'))))
    return 0


def cmd_status(args):
    header('desk status')
    rc, clock = call('alerts', base_args('alerts', args, market=False) + ['clock'])
    cfg = json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else {}
    if not failure(clock):
        print('%s %s IST | market %s | entries %s-%s | flatten %s' % (
            clock.get('date'), clock.get('now_ist'), 'open' if clock.get('open') else 'closed',
            clock.get('no_entries_before') or cfg.get('no_entries_before'),
            clock.get('no_entries_after') or cfg.get('no_entries_after'),
            clock.get('flat_by') or cfg.get('flat_by')))
    stale = registry.staleness(MASTER)
    if stale:
        print('WARNING %s' % stale)
    rc, pf = call('broker', base_args('broker', args, market=False) + ['portfolio'])
    if not failure(pf):
        print('cash %s | realized %s' % (money(pf.get('cash')), money(pf.get('realized_pnl'))))
        for position in pf.get('positions', []):
            print('  %-16s %6s shares | plan stop %s target %s' % (
                position.get('symbol') or position.get('ticker'), position.get('shares'),
                position.get('stop'), position.get('target')))
    rc, alerts = call('alerts', base_args('alerts', args, market=False) + ['list'])
    if not failure(alerts):
        print('%d alert rules persisted' % len(alerts.get('alerts', [])))
    rc, risk = call('risk', base_args('risk', args), timeout=180)
    if not failure(risk) and risk.get('today'):
        print('today: %s' % json.dumps(risk['today'])[:240])


# ---------- scanning ----------

def cmd_scan(args):
    index = getattr(args, 'index', None)
    if index:
        import universe
        label, _ = universe.resolve(index)
        if label is None:
            print('No Nifty index matches "%s". The desk knows %d of them by NSE\'s own '
                  'constituent files -- run `desk.py universe --list` for the list.'
                  % (index, len(universe.labels())))
            return 1
        names = universe.symbols(label)
        if not names:
            doc = universe.constituents(label)
            print('%s: %s' % (label, doc.get('note') or 'no constituents came back'))
            return 1
    else:
        names = symbols_in(args, ORB_LIST if args.mode == 'orb' else DAILY_LIST)
    days = args.days or (120 if args.mode == 'daily' else 5)
    argv = base_args('scanner', args, days=days) + [args.mode, '--symbols', ','.join(names)]
    if args.limit:
        argv += ['--limit', args.limit]
    if args.mode == 'orb':
        argv += ['--or-minutes', args.or_minutes]
        # The engine writes every scan to watchlist.json, and that file is the daily
        # snapshot: `signals`, the Opportunities board and a team seal's scanner
        # artifact all read it. An opening-range pass has no business overwriting a
        # daily setup scan, so it keeps its own file and the board stays truthful.
        argv += ['--out', str(WORKSPACE / 'watchlist-orb.json')]
        if args.min_volume_x:
            argv += ['--min-volume-x', args.min_volume_x]
        if args.at:
            argv += ['--at', args.at]
    header('%s scan | %d symbols%s%s' % (args.mode, len(names),
                                         ' | %s' % index if index else '',
                                         ' | limit %d' % args.limit if args.limit else ''))
    rc, doc = call('scanner', argv, timeout=args.timeout)
    if failure(doc):
        return unavailable(doc, 'scanner')
    print('as of %s | source %s' % (doc.get('as_of'), doc.get('source')))
    print('scanned %s of %s | failures %d' % (doc.get('scanned'), doc.get('universe'), len(doc.get('failed', []))))
    hits = doc.get('hits', [])
    if args.mode == 'orb':
        rows = doc.get('results') or hits

        def last_close(row):
            value = row.get('last')
            return value.get('close') if isinstance(value, dict) else (row.get('price') or value)

        def gap(row):
            high, close = (row.get('opening_range') or {}).get('high'), last_close(row)
            return (high - close) / high if high and close else None

        set_ranges = [row for row in rows if (row.get('opening_range') or {}).get('complete')]
        print('triggered %d | ranges set %d | still forming %d' %
              (len(hits), len(set_ranges), len(rows) - len(set_ranges)))
        if not set_ranges:
            print('No opening range is complete yet -- needs %d minutes of completed bars after 09:15.'
                  % args.or_minutes)
        print('%-16s %9s %9s %8s %7s %8s %-10s %s' % ('symbol', 'or-high', 'or-low', 'last', 'width%',
                                                      'to-break', 'state', 'note'))
        for row in sorted(rows, key=lambda r: (r.get('status') != 'triggered',
                                               gap(r) if gap(r) is not None else 9.0)):
            opening = row.get('opening_range') or {}
            last, high, low = last_close(row), opening.get('high'), opening.get('low')
            width = 100 * (high - low) / last if high and low and last else None
            distance = gap(row)
            print('%-16s %9s %9s %8s %7s %8s %-10s %s' % (
                row.get('symbol'), high, low, last,
                '%.2f' % width if width is not None else '-',
                'breakout' if row.get('status') == 'triggered' else
                ('%+.2f%%' % (100 * distance) if distance is not None else '-'),
                row.get('status'), str(row.get('reason'))[:58]))
        print('\nAn entry needs a completed close above the opening-range high, so the desk')
        print('watches the break on completed bars. Search the symbol on the dashboard and use')
        print('the Read next buttons -- nothing here needs a terminal.')
    else:
        if not hits:
            print('No daily setups matched.')
        for row in hits:
            print('%-16s %10s %-32s rsi %-6s volx %-6s high20 %-10s ma20 %s' % (
                row.get('symbol'), row.get('price'), ','.join(row.get('setups', [])),
                round(row['rsi'], 1) if row.get('rsi') is not None else '-',
                round(row['volume_x'], 2) if row.get('volume_x') is not None else '-',
                row.get('high20'), round(row['ma20'], 2) if row.get('ma20') is not None else '-'))
        dates = ', '.join(sorted({str(row.get('as_of'))[:10] for row in hits})) or 'none'
        print('\nA daily match is a lead, not an eligible entry. Candle dates: %s' % dates)
    for row in doc.get('failed', [])[:8]:
        print('  FAILED %-16s %s' % (row.get('symbol'), str(row.get('reason'))[:90]))
    snapshot = 'watchlist-orb.json' if args.mode == 'orb' else 'watchlist.json'
    if len(doc.get('failed', [])) > 8:
        print('  ... %d more failures kept in %s' % (len(doc['failed']) - 8, WORKSPACE / snapshot))
    return 0


def cmd_universe(args):
    import universe
    if args.list:
        print('%d Nifty indices, by the constituent file NSE actually publishes:'
              % len(universe.INDEXES))
        for label, slug in universe.INDEXES:
            print('  %-34s %s' % (label, slug + '.csv' if slug else 'no published file'))
        print('\nA factor or strategy screen with no file is refused rather than copied '
              'from a third-party list.')
        return 0
    label, _ = universe.resolve(args.index)
    if label is None:
        print('No Nifty index matches "%s". Run `desk.py universe --list` for the %d the desk knows.'
              % (args.index, len(universe.labels())))
        return 1
    doc = universe.constituents(label, refresh=args.refresh)
    if doc.get('status') != 'OK':
        print('%s: %s' % (label, doc.get('note')))
        return 1
    header('%s constituents | %d stocks | %d already on the desk scan lists | %s' %
           (label, doc['count'], doc['on_desk'], doc['as_of']))
    if args.symbols_only:
        print(','.join(row['symbol'] for row in doc['rows']))
        return 0
    for row in doc['rows']:
        print('  %-16s %-4s %-30s %s' % (row['symbol'], 'on' if row['on_desk'] else '--',
                                         row['name'][:30], row['industry'][:34]))
    print('\nSaved to %s' % (universe.STORE / (doc['slug'] + '.json')))
    print('Membership only: NSE lists no prices in this file. To get verdicts for these '
          'stocks, scan the index: desk.py scan daily --index "%s"' % label)
    return 0


def cmd_premarket(args):
    rc = cmd_scan(argparse.Namespace(mode='daily', list=None, symbols=None, limit=args.limit, days=120,
                                     or_minutes=15, min_volume_x=None, at=None, timeout=args.timeout,
                                     provider=args.provider, as_of=args.as_of, tick=None))
    stale = registry.staleness(MASTER)
    if stale:
        print('\nWARNING %s' % stale)
    print('\nThe break is only knowable after 09:30 IST, on a completed bar. Both passes are')
    print('buttons on the dashboard: search the symbol, then Read the live daily bars and')
    print('indicators, then Run the paper chain.')
    return rc


def cmd_watch(args):
    tick = tick_for(args.symbol, args)
    argv = base_args('entry', args) + ['watch', args.symbol, '--tick', tick, '--setup', args.setup,
                              '--interval', args.interval, '--or-minutes', args.or_minutes,
                              '--every', args.every, '--max-checks', args.max_checks, '--write']
    if args.level:
        argv += ['--level', args.level]
    if args.min_volume_x:
        argv += ['--min-volume-x', args.min_volume_x]
    header('watching %s for a completed-bar %s entry | every %ss, stops after %s checks or the first trigger' %
           (args.symbol.upper(), args.setup, args.every, args.max_checks))
    print('tick INR %.2f | a trigger writes %s and runs the chain in this same pass' %
          (tick, WORKSPACE / 'entry.json'))
    checks, _last = watch_poll('entry', argv, args.every * args.max_checks + 120)
    # entry.json outlives the run, so a watch that never reported must not be allowed to
    # chain off the signal the previous symbol left in that file.
    entry = read_json(WORKSPACE / 'entry.json')
    wanted = str(args.symbol).upper().split('.')[0]
    got = str(entry.get('symbol') or '').upper().split('.')[0]
    if not checks:
        print('\nThe watch returned no completed-bar report at all, so nothing is confirmed and '
              'no chain runs.')
        return 0
    if got != wanted:
        print('\nThis watch run reported %s checks but wrote nothing for %s (the file holds %s), '
              'so no chain runs on another symbol\'s bar.'
              % (checks, args.symbol.upper(), got or 'nothing'))
        return 0
    if entry.get('status') != 'triggered':
        print('\nNo trigger inside the poll window; entry.json still holds %s.' %
              (entry.get('status') or 'nothing'))
        return 0
    if not entry.get('eligible'):
        print('\nThe signal this chain inherited is %s minutes old (%s), and the engine wants the '
              'chain inside %s minutes of that bar closing. A watch pass on a symbol that has '
              'already broken returns on its very first check, so pressing it again does not '
              'renew anything -- the desk will not dress an old bar up as a fresh signal. Watch the '
              'next bar on a row that is still waiting instead.'
              % (num(entry.get('minutes_since'), 1), str(entry.get('reason') or '')[:64],
                 1 if args.interval == '1m' else 5))
        return 0
    if args.no_chain:
        print('\nNext: press Run the paper chain for %s on the dashboard to size these levels'
              % args.symbol.upper())
        print('(stop %s, target %s) into a paper ticket.' % (args.stop, args.target))
        return 0
    # The risk stage only passes for a signal confirmed within the last bar step,
    # so the whole chain has to run in the seconds right after the trigger.
    header('trigger seen -- running the chain immediately')
    chain = argparse.Namespace(**vars(args))
    chain.symbol = args.symbol.upper()
    chain.index = 1
    chain.manual_entry = None
    return cmd_setup(chain)


# ---------- the plan chain ----------

def print_sizing(doc):
    """Render a budget report. False means no order was proposed."""
    if not doc.get('orders'):
        print('No order proposed -- %s' % json.dumps({k: doc.get(k) for k in
                                                      ('status', 'reason', 'cash', 'available', 'notes')
                                                      if k in doc}))
        return False
    for row in doc['orders']:
        print('%s %s %s shares | stop %s target %s | %s' % (
            str(row.get('side')).upper(), row.get('symbol'), row.get('qty'),
            row.get('stop_loss_stop_price'), row.get('take_profit_limit_price'), str(row.get('why'))[:70]))
    print('status %s | whole shares after cash buffer, order cap and concentration limits' % doc.get('status'))
    return True


def cmd_setup(args):
    symbol = args.symbol.upper()
    tick = tick_for(symbol, args)
    # entry/exit/risk/broker declare the same flags; the sizing tool declares neither
    # the feed nor the metadata ones, so it gets its own list.
    argv = base_args('entry', args)
    header('entry check | %s | tick INR %.2f' % (symbol, tick))
    entry_argv = argv + ['check', symbol, '--tick', tick, '--setup', args.setup, '--interval', args.interval,
                         '--or-minutes', args.or_minutes, '--write']
    if args.level:
        entry_argv += ['--level', args.level]
    if args.min_volume_x:
        entry_argv += ['--min-volume-x', args.min_volume_x]
    rc, entry = call('entry', entry_argv)
    if failure(entry):
        return unavailable(entry, 'entry')
    opening = entry.get('opening_range') or {}
    print('status %s | eligible %s | price %s at %s' % (entry.get('status'), entry.get('eligible'),
                                                        entry.get('price'), entry.get('time') or entry.get('bar_start')))
    if opening:
        print('opening range %s - %s (%s min, %s)' % (opening.get('low'), opening.get('high'),
                                                     opening.get('minutes'), entry.get('date')))
    if entry.get('reason'):
        print('reason: %s' % entry['reason'])
    if entry.get('missing_bars'):
        print('missing bars: %s' % str(entry['missing_bars'])[:160])

    manual = args.manual_entry is not None
    if not manual and entry.get('status') != 'triggered':
        print('\nNo triggered entry, so nothing was planned, sized, risk-checked or ticketed.')
        print('Next: watch %s from the dashboard so the desk confirms the break on a completed bar.' % symbol)
        print('Or, if you already took the fill in the sim, plan it on the dashboard at its actual price.')
        return 0

    header('exit plan')
    plan_argv = argv + ['plan', '--tick', tick, '--stop', args.stop, '--target', args.target, '--write']
    if manual:
        plan_argv += ['--symbol', symbol, '--entry', args.manual_entry]
        if opening:
            plan_argv += ['--or-high', opening.get('high'), '--or-low', opening.get('low'), '--setup', args.setup,
                          '--interval', args.interval, '--or-minutes', args.or_minutes]
        print('planning from your manual entry %s (no triggered signal required)' % args.manual_entry)
    if args.flat_by:
        plan_argv += ['--flat-by', args.flat_by]
    rc, plan = call('exit', plan_argv)
    if failure(plan):
        return unavailable(plan, 'exit plan')
    print('entry %s | stop %s (%s) | target %s (%s | %sR)' % (
        num(plan.get('entry')), num(plan.get('stop')), plan.get('stop_label') or plan.get('stop_rule'),
        num(plan.get('target')), plan.get('target_spec'), num(plan.get('target_r'), 2)))
    print('risk per share %s | reward/risk %s | flatten by %s | tick %s' % (
        num(plan.get('risk_per_share')), num(plan.get('reward_risk')), plan.get('flat_by'), plan.get('tick')))
    if (plan.get('tick_verification') or {}).get('status') != 'VERIFIED':
        print('tick verification: %s' % json.dumps(plan.get('tick_verification')))

    header('position sizing')
    rc, sized = call('budget', base_args('budget', args, market=False) + ['--write'])
    if failure(sized):
        return unavailable(sized, 'budget')
    if not print_sizing(sized):
        return 0

    header('risk check')
    rc, risk = call('risk', argv)
    if failure(risk):
        return unavailable(risk, 'risk')
    print('%s %s IST | pass %s | block %s | cash after %s' % (
        risk.get('date'), risk.get('now_ist'), risk.get('pass'), risk.get('block'), money(risk.get('cash_after'))))
    for row in risk.get('results', []):
        print('  [%s] %s %s %s | stop %s | max loss %s | net R:R %s | tick %s' % (
            row.get('verdict'), row.get('symbol'), row.get('side'), row.get('amount'), row.get('stop'),
            money(row.get('max_loss')), num(row.get('net_reward_risk')),
            (row.get('tick_verification') or {}).get('status')))
        for reason in row.get('reasons', []):
            print('      reason: %s' % reason)
        for warning in row.get('warnings', []):
            print('      warn : %s' % warning)

    header('paper ticket')
    rc, ticket = call('broker', argv + ['show', '--index', args.index])
    if failure(ticket):
        return unavailable(ticket, 'ticket')
    order = ticket.get('order', {})
    if not ticket.get('ready'):
        print('BLOCKED | %s %s %s shares | max loss %s' % (
            str(order.get('side')).upper(), order.get('symbol'), order.get('qty'), money(ticket.get('max_loss'))))
        for reason in ticket.get('reasons', []):
            print('      reason: %s' % reason)
        return 1
    print('READY_PAPER_TICKET | %s %s %s shares | max loss at stop (cost inclusive) %s' % (
        order.get('side'), order.get('symbol'), order.get('qty'), money(ticket.get('max_loss'))))
    print('fingerprint %s' % ticket.get('fingerprint'))
    print('execution_enabled %s' % ticket.get('execution_enabled'))
    print('\nNothing was approved or recorded. If you want this local paper position, type your own reply:')
    print('  type "yes %s" into this ticket\'s approval box on the dashboard.' % str(ticket.get('fingerprint'))[:12])
    print('  The desk will never type that reply for you, and approval is paper-only.')
    return 0


def cmd_risk(args):
    header('risk check')
    rc, risk = call('risk', base_args('risk', args))
    if failure(risk):
        return unavailable(risk, 'risk')
    print('%s %s IST | pass %s | block %s' % (risk.get('date'), risk.get('now_ist'), risk.get('pass'), risk.get('block')))
    for row in risk.get('results', []):
        print('  [%s] %s %s %s | stop %s | max loss %s | net R:R %s' % (
            row.get('verdict'), row.get('symbol'), row.get('side'), row.get('amount'), row.get('stop'),
            money(row.get('max_loss')), row.get('net_reward_risk')))
        for reason in row.get('reasons', []):
            print('      reason: %s' % reason)
    return 1 if risk.get('block') else 0


def cmd_size(args):
    header('sizing from %s' % (WORKSPACE / 'plan.json'))
    rc, doc = call('budget', base_args('budget', args, market=False) + ['--write'])
    if failure(doc):
        return unavailable(doc, 'budget')
    print_sizing(doc)
    return 0


def cmd_ticket(args):
    argv = base_args('broker', args) + ['show', '--index', args.index or 1]
    rc, ticket = call('broker', argv)
    header('paper ticket')
    if failure(ticket):
        return unavailable(ticket, 'ticket')
    order = ticket.get('order', {})
    print('%s | %s %s %s shares | max loss %s | tick %s' % (
        ticket.get('status'), order.get('side'), order.get('symbol'), order.get('qty'),
        money(ticket.get('max_loss')), (ticket.get('tick_verification') or {}).get('status')))
    print('fingerprint %s' % ticket.get('fingerprint'))
    if ticket.get('reasons'):
        for reason in ticket['reasons']:
            print('      reason: %s' % reason)
    print('\nApproving records a local simulated fill only, and needs your own reply text.')
    return 0 if ticket.get('ready') else 1


def cmd_approve(args):
    argv = base_args('broker', args) + ['approve', '--reply', args.reply]
    if args.index:
        argv += ['--index', args.index]
    rc, doc = call('broker', argv)
    header('approval')
    if failure(doc):
        return unavailable(doc, 'approve')
    print(json.dumps(doc, indent=2)[:1000])
    print('\nThat is a LOCAL SIMULATED event, not a broker acknowledgement. Next: record the simulated price '
          'from the dashboard, or leave it as an approved ticket.')
    return 0


def cmd_decline(args):
    argv = base_args('broker', args) + ['decline', '--reply', args.reply]
    if args.index:
        argv += ['--index', args.index]
    rc, doc = call('broker', argv)
    header('decline')
    print(json.dumps(doc, indent=2)[:600])
    return 0


def cmd_fill(args):
    argv = base_args('broker', args) + ['fill', '--fingerprint', args.fingerprint, '--qty', args.qty, '--price', args.price,
                              '--fill-id', args.fill_id]
    rc, doc = call('broker', argv)
    header('simulated fill')
    if failure(doc):
        return unavailable(doc, 'fill')
    print(json.dumps(doc, indent=2)[:900])
    print('\nSimulation only: execution_enabled is false and no order left this machine.')
    return 0


def cmd_flatten(args):
    symbol = args.symbol.upper()
    argv = base_args('broker', args, market=False) + ['flatten', '--symbol', symbol, '--tick', tick_for(symbol, args), '--write']
    if args.qty:
        argv += ['--qty', args.qty]
    rc, doc = call('broker', argv)
    header('flatten proposal')
    if failure(doc):
        return unavailable(doc, 'flatten')
    for row in doc.get('orders', []):
        print('sell %s %s -- %s' % (row.get('qty'), row.get('symbol'), row.get('why')))
    print('Now re-run the exact order through risk, then approve it yourself:')
    print('  the Journal page carries both controls -- the risk check button and this ticket\'s approve box.')
    return 0


def cmd_sim(args):
    symbol = args.symbol.upper()
    argv = base_args('timemachine', args, days=args.days or 28) + ['run', '--write', '--replays', args.replays,
                                                                   '--seed', args.seed]
    header('time machine | %s | rule from %s' % (symbol, WORKSPACE / 'plan.json'))
    tick_for(symbol, args)
    rc, doc = call('timemachine', argv, timeout=args.timeout)
    if failure(doc):
        return unavailable(doc, 'timemachine')
    print('status %s' % doc.get('status'))
    if doc.get('reason'):
        print('reason %s' % doc['reason'])
        if doc.get('prior_sessions') is not None:
            print('  %s prior complete sessions available, %s needed for sampled scenarios. The free NSE feed caps a '
                  'five-minute request near 28 calendar days, so run this with --csv of authorized longer history '
                  'or --provider upstox to get the simulation.' % (doc['prior_sessions'], doc.get('minimum_sessions')))
    history = doc.get('history') or {}
    if history:
        print('replay       %s sessions | %s triggered | %s trades | net P&L %s (costs included, next-bar-open fills)'
              % (history.get('sessions'), history.get('triggered'), len(history.get('trades', [])),
                 money(history.get('net_pnl'))))
        for trade in history.get('trades', [])[-8:]:
            print('  %s entry %-9s stop %-9s target %-9s %3s shares -> %-9s %s' % (
                trade.get('date'), trade.get('entry'), trade.get('stop'), trade.get('target'),
                trade.get('qty'), trade.get('exit'), money(trade.get('net_pnl'))))
    if doc.get('mc'):
        paths = doc['mc']
        print('simulation   %s' % json.dumps({k: v for k, v in paths.items() if k != 'paths'})[:600])
    for limitation in doc.get('limitations', []):
        print('  note: %s' % limitation)
    print('Replays use only prior sessions known at the plan cutoff. This is not a forecast.')
    return 0


# ---------- alerts, news, ledger ----------

def cmd_alerts(args):
    header('alerts')
    if args.action == 'list':
        rc, doc = call('alerts', base_args('alerts', args, market=False) + ['list'])
        rules = doc.get('alerts', []) if not failure(doc) else []
        if not rules:
            print('No alert rules stored yet. The desk can build them from the sealed plan on the Alerts panel.')
        for row in rules:
            print('  %-24s %-13s %s' % (row.get('id'), row.get('type'),
                                        ' '.join('%s=%s' % (k, v) for k, v in row.items()
                                                 if k not in ('id', 'type', 'label'))))
        return 0
    if args.action == 'reset':
        if not args.id:
            print('Supply --id ALERT_ID')
            return 1
        rc, doc = call('alerts', base_args('alerts', args, market=False) + ['reset', args.id])
        print(json.dumps(doc)[:300])
        return 0
    if args.action == 'check':
        rc, doc = call('alerts', base_args('alerts', args) + ['check'], timeout=240)
        if failure(doc):
            return unavailable(doc, 'alerts check')
        print('%d rules rung | %d data errors' % (len(doc.get('rings', [])), len(doc.get('errors', []))))
        for row in doc.get('rings', []):
            print('  RING %s' % json.dumps(row)[:300])
        for row in doc.get('errors', [])[:5]:
            print('  ERROR %s' % json.dumps(row)[:200])
        print('Each rule rings once until rearmed; nothing runs in the background and nothing is delivered.')
        return 0

    argv = base_args('alerts', args, market=False) + ['add']
    if args.from_plan:
        argv += ['--from-plan']
    elif args.opening_bell:
        argv += ['--opening-bell']
    elif args.symbol and args.above is not None:
        argv += ['--symbol', args.symbol.upper(), '--above', args.above]
    elif args.symbol and args.below is not None:
        argv += ['--symbol', args.symbol.upper(), '--below', args.below]
    elif args.at:
        argv += ['--at', args.at] + (['--symbol', args.symbol.upper()] if args.symbol else [])
    else:
        print('Choose exactly one: --from-plan | --symbol S --above PRICE | --symbol S --below PRICE | '
              '--at HH:MM | --opening-bell')
        return 1
    if args.label:
        argv += ['--label', args.label]
    if args.id:
        argv += ['--id', args.id]
    if args.rearm:
        argv += ['--rearm']
    rc, doc = call('alerts', argv)
    if failure(doc):
        return unavailable(doc, 'alerts add')
    for row in doc.get('added', []):
        print('added %s' % json.dumps(row))
    print('The desk checks them on its own cadence; the Alerts panel shows the next check and every firing.')
    return 0


def cmd_news(args):
    """Two passes: the vendor's completed-candle moves, then dated Indian headlines by RSS."""
    import newsfeed
    tickers = symbols_in(args, ORB_LIST)[:args.limit or 15]
    feed_symbols = daily_universe(args.symbols, args.rss_limit)
    argv = base_args('news', args) + ['--tickers'] + tickers
    if args.hours:
        argv += ['--hours', args.hours]
    if args.no_moves:
        argv += ['--no-moves']
    header('headlines | %d ticker(s), %d RSS symbol(s)' % (len(tickers), len(feed_symbols)))
    rc, doc = call('news', argv, timeout=args.timeout)
    if failure(doc):
        print('vendor pass unavailable -- %s' % str(doc.get('error', 'no reason'))[:240])
        print('Its feeds are US-shaped (Yahoo RSS, SEC EDGAR); the RSS pass below reads Indian queries.')
        doc = dict(created_by='desk news', as_of=dt.datetime.now(IST).isoformat(),
                   currency='INR', paper=True, execution_enabled=False, items=[], errors=[], moves=[],
                   vendor_error=str(doc.get('error', ''))[:300])
    print('%d dated vendor items | %d source errors' % (len(doc.get('items', [])), len(doc.get('errors', []))))
    for row in doc.get('moves', [])[:15]:
        print('  move %-16s %s at %s (%s since previous completed candle)' % (
            row.get('symbol'), row.get('price'), str(row.get('as_of'))[11:16],
            '%+.2f%%' % row['pct'] if row.get('pct') is not None else 'n/a'))
    if not feed_symbols:
        print('\nNo symbols for the RSS sweep -- press Run the daily setup scan on the dashboard first.')
        doc['headlines'] = None
    else:
        try:
            feed = newsfeed.gather(feed_symbols, hours=args.hours or 36)
        except (OSError, ValueError) as exc:
            print('\nRSS sweep failed -- %s' % str(exc)[:240])
            feed = None
        if feed is None:
            doc['headlines'] = None
        else:
            newsfeed.print_headlines(feed)
            doc['headlines'] = feed
    (WORKSPACE / 'news.json').write_text(json.dumps(doc, indent=2, default=str), encoding='utf-8')
    print('\nsaved %s' % (WORKSPACE / 'news.json'))
    print('Headlines are leads, not verified facts; open the publisher link before acting on one.')
    return 0


def cmd_journal(args):
    argv = base_args('broker', args, market=False)
    header('paper journal')
    rc, pf = call('broker', argv + ['portfolio'])
    if failure(pf):
        return unavailable(pf, 'portfolio')
    print('cash %s | initial %s | realized %s | positions %d' % (
        money(pf.get('cash')), money(pf.get('initial_capital')), money(pf.get('realized_pnl')),
        len(pf.get('positions', []))))
    for row in pf.get('positions', []):
        print('  %-16s %6s shares | avg %10s | stop %10s | target %10s' % (
            row.get('symbol') or row.get('ticker'), row.get('shares'), row.get('average_cost'),
            row.get('stop'), row.get('target')))
    rc, log = call('broker', argv + ['log'])
    if failure(log):
        return unavailable(log, 'ledger log')
    events = log.get('orders', []) if isinstance(log, dict) else log
    counts = {}
    for event in events:
        key = str(event.get('status') or event.get('event') or 'unknown')
        counts[key] = counts.get(key, 0) + 1
    print('\n%d ledger events: %s' % (len(events), ', '.join('%s=%d' % kv for kv in sorted(counts.items())) or 'none'))
    for event in events[-10:]:
        print('  %s %-10s %s' % (str(event.get('at'))[:19], event.get('status') or event.get('event'),
                                 json.dumps({k: v for k, v in event.items()
                                             if k in ('symbol', 'ticker', 'qty', 'price', 'profit', 'fingerprint')})[:220]))
    print('\nAuthoritative ledger: %s' % (WORKSPACE / 'state' / 'paper.sqlite'))
    print('Every event is a local simulation: placed/filled here never means a broker acknowledgement.')
    return 0


# ---------- index board ----------

def cmd_index(args):
    import indices
    names = ([s.strip().upper() for s in args.symbols.split(',') if s.strip()]
             if args.symbols else list(indices.DEFAULT_INDEXES))
    header('index board')
    doc = indices.board(names=names, daily_scan=read_json(WORKSPACE / 'watchlist.json'))
    if not doc['indices']:
        print('No index candles came back -- %s' % (doc['failures'] or 'empty response'))
        return 1
    indices.print_board(doc)
    (WORKSPACE / 'index.json').write_text(json.dumps(doc, indent=2, default=str), encoding='utf-8')
    print('\nsaved %s' % (WORKSPACE / 'index.json'))
    print(doc['option_premiums'])
    print('Context only: index option legs are not sized, risked or ticketed by this desk.')
    return 0


# ---------- option chain ----------

def cmd_options(args):
    import optionchain
    header('option chain | %s' % args.symbol.upper())
    try:
        doc = optionchain.fetch(args.symbol, expiry=args.expiry, kind=args.kind)
    except ValueError as exc:
        print('unavailable: %s' % exc)
        return 1
    optionchain.print_chain(doc, strikes=args.strikes)
    (WORKSPACE / 'options.json').write_text(json.dumps(doc, indent=2, default=str), encoding='utf-8')
    # Keep one file per underlying so the signals layer can read several expiries at once.
    (WORKSPACE / ('options-%s.json' % doc['symbol'])).write_text(
        json.dumps(doc, indent=2, default=str), encoding='utf-8')
    print('\nsaved %s' % (WORKSPACE / 'options.json'))
    print('expiries on file: %s' % ', '.join(doc['expiry_dates'][:6]))
    return 0


# ---------- signals ----------

def cmd_signals(args):
    import signals
    if not 30 <= args.days <= 120:
        print('--days must be 30-120: ADX, MACD and EMA50 need about 35 daily bars to warm up.')
        return 1
    if not CONFIG.exists():
        print('config missing: %s' % CONFIG)
        return 1
    if args.provider != 'nse':
        print('note: signals always reads the NSE chart host; --provider %s is ignored here' % args.provider)
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    scan = read_json(WORKSPACE / 'watchlist.json')
    meta = {}
    for row in scan.get('hits') or []:
        symbol = str(row.get('symbol') or '').upper()
        if symbol:
            meta[symbol] = {'setups': row.get('setups') or [], 'scan_as_of': row.get('as_of')}
    names = daily_universe(args.symbols)
    if not names:
        print('No .NS equities to read -- press Run the daily setup scan on the dashboard first.')
        return 1
    limit = None if args.all else args.limit
    index_names = ([s.strip() for s in args.index.split(',') if s.strip()] if args.index
                   else ['NIFTY 50', 'NIFTY BANK'])
    chains = [doc for path in sorted(WORKSPACE.glob('options-*.json'))
              if (doc := read_json(path)).get('atm_strike')]
    header('signals | %d equities%s, %s daily bars, %d saved chain(s)' % (
        len(names), '' if limit is None else ' (top %d)' % limit, args.days, len(chains)))
    doc = signals.build(names, as_of=as_of_arg(args), days=args.days,
                        window='%s-%s' % (cfg.get('no_entries_before'), cfg.get('no_entries_after')),
                        index_names=index_names, chains=chains,
                        index_board=read_json(WORKSPACE / 'index.json'), limit=limit, meta=meta)
    if not doc['equity'] and not doc['index']:
        print('Nothing came back -- failures: %s' % json.dumps(doc['failures'][:4])[:400])
        return 1
    signals.print_signals(doc)
    (WORKSPACE / 'signals.json').write_text(json.dumps(doc, indent=2, default=str), encoding='utf-8')
    print('\nsaved %s' % (WORKSPACE / 'signals.json'))
    print('Refresh the desk lists first for newer inputs: scan daily, index, options.')
    return 0


# ---------- risk envelope ----------

# ---------- live per-symbol detail ----------

def cmd_detail(args):
    import detail
    header('detail | %s' % args.symbol)
    doc = detail.run(args.symbol, as_of=as_of_arg(args), days=args.days,
                     refresh=args.refresh, with_chain=not args.no_chain)
    detail.print_detail(doc)
    if doc.get('cache_hit'):
        print('\ncache read from %s -- add --refresh to re-read NSE'
              % str(doc.get('fetched_at'))[:16])
    return 0 if doc.get('answered') else 1


def cmd_filings(args):
    import announcements
    return announcements.main([args.symbol, '--days', str(args.days),
                               '--limit', str(args.limit)]
                              + (['--refresh'] if args.refresh else []))


def cmd_financials(args):
    import financials
    return financials.main([args.symbol, '--scope', args.scope, '--quarters', str(args.quarters)]
                           + (['--json'] if args.json else [])
                           + (['--refresh'] if args.refresh else []))


def cmd_envelope(args):
    import envelope
    if not CONFIG.exists():
        print('config missing: %s' % CONFIG)
        return 1
    rc, pf = call('broker', base_args('broker', args, market=False) + ['portfolio'])
    if failure(pf):
        return unavailable(pf, 'portfolio')
    header('risk envelope')
    doc = envelope.build(json.loads(CONFIG.read_text(encoding='utf-8')), pf,
                         read_json(WORKSPACE / 'state' / 'risk.json'),
                         read_json(WORKSPACE / 'entry.json'))
    envelope.print_envelope(doc)
    (WORKSPACE / 'envelope.json').write_text(json.dumps(doc, indent=2, default=str), encoding='utf-8')
    print('\nsaved %s' % (WORKSPACE / 'envelope.json'))
    print('Computed from saved files only -- this command never writes a risk decision.')
    return 0


def cmd_report(args):
    import report
    if args.symbol:
        doc = report.build_symbol(args.symbol, WORKSPACE)
        header('symbol report | %s' % doc['query'])
        print(report.symbol_markdown(doc))
        for path in report.write_symbol(doc, WORKSPACE, html_too=not args.no_html):
            print('\nwrote %s' % path)
        print('Market detail is a live NSE read (or its cache); the rest is saved desk state. '
              'Nothing here places an order.')
        return 0
    sections = report.build(WORKSPACE)
    header('desk report | %s' % sections['date'])
    report.print_report(sections)
    for path in report.write(sections, WORKSPACE, html_too=not args.no_html):
        print('\nwrote %s' % path)
    print('Assembled from saved workspace files only -- this command makes no request and decides nothing.')
    return 0


# ---------- specialist team ----------
def cmd_team(args):
    import teamflow
    return teamflow.prepare(args.symbol, tick=args.tick, days=args.days,
                            simulations=args.simulations, as_of=as_of_arg(args))


def cmd_team_dispatch(args):
    import teamflow
    return teamflow.dispatch(args.session, args.role, args.agent_id)


def cmd_team_record(args):
    import teamflow
    return teamflow.record(args.session, args.role, args.agent_id, args.review)


def cmd_team_report(args):
    import teamflow
    return teamflow.rollup(args.session, finalize=not args.show_only)


def cmd_team_review(args):
    import teamflow
    return teamflow.run_room(args.symbol, tick=args.tick, days=args.days,
                             simulations=args.simulations, as_of=as_of_arg(args))


def cmd_team_finish(args):
    import teamflow
    return teamflow.finish_room(args.session)


# ---------- dashboard ----------
def cmd_dashboard(args):
    dashboard = VENDOR / 'dashboard'
    if not (dashboard / 'dist' / 'index.html').exists():
        header('building dashboard (needs Node)')
        for command in (['npm', 'ci', '--prefix', 'dashboard'], ['npm', 'run', 'build', '--prefix', 'dashboard']):
            print('  ' + ' '.join(command))
            done = subprocess.run(command, cwd=str(VENDOR), shell=sys.platform == 'win32')
            if done.returncode:
                print('Build failed (%s). Node 20.19+/22.12+ is required; the desk itself does not need it.'
                      % done.returncode)
                return done.returncode
    header('serving dashboard')
    if socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect_ex(('127.0.0.1', args.port)) == 0:
        print('Port %d already has a listener. Something else owns it -- rerun with --port %d. '
              'Nothing here kills other processes.' % (args.port, args.port + 13))
        return 1
    print('Open http://127.0.0.1:%d (loopback only, Ctrl-C stops)' % args.port)
    done = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'serve.py'),
                           '--workspace', str(WORKSPACE),
                           '--sessions', str(WORKSPACE / 'team'), '--port', str(args.port)],
                          cwd=str(VENDOR))
    return done.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description='Indian day-trading desk (paper only, no order execution)')
    parser.add_argument('--provider', default='nse', choices=['nse', 'yahoo', 'upstox'],
                        help='candle source; nse is the public website feed, upstox reads UPSTOX_ANALYTICS_TOKEN '
                             'from your own environment')
    parser.add_argument('--as-of', help='ISO timestamp for a historical replay; naive times are read as IST')
    sub = parser.add_subparsers(dest='command', required=True)

    def add_market(item, days=5, timeout=300):
        item.add_argument('--days', type=int, default=days, help='calendar-day lookback')
        item.add_argument('--timeout', type=int, default=timeout)

    sub.add_parser('doctor', help='check environment, config, tick registry, feed and ledger').set_defaults(func=cmd_doctor)
    status = sub.add_parser('status', help='clock, open positions, alerts, today risk')
    add_market(status)
    status.set_defaults(func=cmd_status)

    scan = sub.add_parser('scan', help='daily setup scan or opening-range scan over the desk lists')
    scan.add_argument('mode', choices=['daily', 'orb'])
    scan.add_argument('--list', help='symbol file; default is the matching config list')
    scan.add_argument('--symbols', help='comma-separated symbols instead of the list')
    scan.add_argument('--index', help='scan one Nifty index\'s own constituents (e.g. NIFTY IT)')
    scan.add_argument('--limit', type=int)
    scan.add_argument('--or-minutes', type=int, default=15)
    scan.add_argument('--min-volume-x', type=float)
    scan.add_argument('--at', help='IST HH:MM opening-range cutoff for a historical read')
    add_market(scan, days=None, timeout=1200)
    scan.set_defaults(func=cmd_scan)

    uni = sub.add_parser('universe', help='which stocks NSE itself lists inside a Nifty index')
    uni.add_argument('index', nargs='?')
    uni.add_argument('--list', action='store_true', help='print every index the desk can resolve')
    uni.add_argument('--refresh', action='store_true', help='refetch instead of using the cached file')
    uni.add_argument('--symbols-only', action='store_true', help='print one comma-separated list')
    uni.set_defaults(func=cmd_universe)

    pre = sub.add_parser('premarket', help='daily setup scan plus clock and registry warnings')
    pre.add_argument('--limit', type=int)
    add_market(pre, days=None, timeout=1200)
    pre.set_defaults(func=cmd_premarket)

    watch = sub.add_parser('watch', help='poll one symbol for a completed-bar entry, then run the chain')
    watch.add_argument('symbol')
    watch.add_argument('--setup', default='orb', choices=['orb', 'level'])
    watch.add_argument('--level', type=float)
    watch.add_argument('--interval', default='5m', choices=['1m', '5m'])
    watch.add_argument('--or-minutes', type=int, default=15)
    watch.add_argument('--min-volume-x', type=float)
    watch.add_argument('--every', type=int, default=30)
    watch.add_argument('--max-checks', type=int, default=120)
    watch.add_argument('--stop', default='or-mid')
    watch.add_argument('--target', default='2R')
    watch.add_argument('--flat-by')
    watch.add_argument('--tick', type=float)
    watch.add_argument('--capital', type=float)
    watch.add_argument('--risk', type=float)
    watch.add_argument('--no-chain', action='store_true', help='stop after the trigger; do not size or risk it')
    add_market(watch, days=3)
    watch.set_defaults(func=cmd_watch)

    setup = sub.add_parser('setup', help='entry check, then plan, sizing, risk and paper ticket')
    setup.add_argument('symbol')
    setup.add_argument('--setup', default='orb', choices=['orb', 'level'])
    setup.add_argument('--level', type=float)
    setup.add_argument('--interval', default='5m', choices=['1m', '5m'])
    setup.add_argument('--or-minutes', type=int, default=15)
    setup.add_argument('--min-volume-x', type=float)
    setup.add_argument('--stop', default='or-mid', help='or-mid, or-low, atr:K, pct:X, price:X')
    setup.add_argument('--target', default='2R', help='1R, 2R, price:X')
    setup.add_argument('--flat-by')
    setup.add_argument('--manual-entry', type=float, help='plan a fill you already took')
    setup.add_argument('--tick', type=float, help='override the registry tick')
    setup.add_argument('--index', type=int, default=1)
    setup.add_argument('--capital', type=float, help='override config budget for this run')
    setup.add_argument('--risk', type=float, help='override config per-trade loss limit for this run')
    add_market(setup, days=3)
    setup.set_defaults(func=cmd_setup)

    for name, handler, market in (('risk', cmd_risk, True), ('size', cmd_size, False), ('ticket', cmd_ticket, True)):
        item = sub.add_parser(name, help='re-run that single stage')
        item.add_argument('--index', type=int)
        item.add_argument('--capital', type=float)
        item.add_argument('--risk', type=float)
        add_market(item, days=3)
        item.set_defaults(func=handler)

    approve = sub.add_parser('approve', help='record your own consent for the ticket just shown')
    approve.add_argument('--reply', required=True, help='your words, beginning with an unambiguous yes')
    approve.add_argument('--index', type=int)
    approve.set_defaults(func=cmd_approve)

    decline = sub.add_parser('decline', help='record that you passed on the ticket')
    decline.add_argument('--reply', default='no')
    decline.add_argument('--index', type=int)
    decline.set_defaults(func=cmd_decline)

    fill = sub.add_parser('fill', help='record a simulated full fill for an approved ticket')
    fill.add_argument('--fingerprint', required=True)
    fill.add_argument('--qty', type=int, required=True)
    fill.add_argument('--price', type=float, required=True)
    fill.add_argument('--fill-id', required=True)
    fill.set_defaults(func=cmd_fill)

    flatten = sub.add_parser('flatten', help='propose closing a paper position, then re-run risk')
    flatten.add_argument('symbol')
    flatten.add_argument('--qty', type=int)
    flatten.add_argument('--tick', type=float)
    flatten.set_defaults(func=cmd_flatten)

    sim = sub.add_parser('sim', help='replay the configured rule on prior sessions and simulate remaining paths')
    sim.add_argument('symbol')
    sim.add_argument('--replays', type=int, default=10000)
    sim.add_argument('--seed', type=int, default=19)
    sim.add_argument('--tick', type=float)
    add_market(sim, days=28, timeout=600)
    sim.set_defaults(func=cmd_sim)

    alerts = sub.add_parser('alerts', help='persisted stop/target/level/time rules, once per event')
    alerts.add_argument('action', choices=['add', 'check', 'list', 'reset'])
    alerts.add_argument('--symbol')
    alerts.add_argument('--from-plan', action='store_true')
    alerts.add_argument('--above', type=float)
    alerts.add_argument('--below', type=float)
    alerts.add_argument('--at', help='IST HH:MM')
    alerts.add_argument('--opening-bell', action='store_true')
    alerts.add_argument('--label')
    alerts.add_argument('--id')
    alerts.add_argument('--rearm', action='store_true')
    add_market(alerts, days=3)
    alerts.set_defaults(func=cmd_alerts)

    news = sub.add_parser('news', help='dated headline and move checks for the desk lists')
    news.add_argument('--symbols')
    news.add_argument('--list')
    news.add_argument('--limit', type=int, help='symbols to check; default 15')
    news.add_argument('--rss-limit', type=int, default=12, help='how many symbols get an RSS headline sweep')
    news.add_argument('--hours', type=float)
    news.add_argument('--no-moves', action='store_true')
    add_market(news, days=3, timeout=600)
    news.set_defaults(func=cmd_news)

    envelope = sub.add_parser('envelope', help='live risk envelope: limits, usage and room left, from saved files')
    envelope.set_defaults(func=cmd_envelope)

    index = sub.add_parser('index', help='live NIFTY/BANKNIFTY spot ranges, futures premium and list breadth')
    index.add_argument('--symbols', help='comma-separated NSE index names; default the four broad indices')
    index.set_defaults(func=cmd_index)

    options = sub.add_parser('options', help='live NSE option chain: premiums, IV, OI, PCR and OI walls')
    options.add_argument('symbol', nargs='?', default='NIFTY',
                         help='NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY or an equity like RELIANCE')
    options.add_argument('--expiry', help='e.g. 03-Nov-2026; default the nearest listed expiry')
    options.add_argument('--kind', choices=('Indices', 'Equity'),
                         help='chain segment; default tries Indices then Equity')
    options.add_argument('--strikes', type=int, default=9, help='near-ATM rows to print')
    options.set_defaults(func=cmd_options)

    sig = sub.add_parser('signals', help='ADX/DI/MACD/RSI/ATR verdicts for stocks and indices, plus the expiry tape')
    sig.add_argument('--symbols', help='comma-separated .NS equities; default the saved daily scan, setups first')
    sig.add_argument('--limit', type=int, default=30, help='how many equities to read (default 30)')
    sig.add_argument('--all', action='store_true', help='read the whole saved universe (several minutes)')
    sig.add_argument('--days', type=int, default=100, help='daily-bar lookback, 30-120')
    sig.add_argument('--index', help='comma-separated NSE index names; default NIFTY 50 and NIFTY BANK')
    sig.set_defaults(func=cmd_signals)

    det = sub.add_parser('detail',
                         help='live per-symbol detail: daily + 5m bars, indicators, option chain, lot and tick')
    det.add_argument('symbol', help='RELIANCE, NIFTY 50, NIFTY 24000 CE, BANKNIFTY 09-Nov-2026 55800 PE')
    det.add_argument('--days', type=int, default=100, help='daily-bar lookback, 30-120')
    det.add_argument('--refresh', action='store_true', help='ignore the cache and re-read NSE')
    det.add_argument('--no-chain', action='store_true', help='skip the option chain for a plain stock')
    det.set_defaults(func=cmd_detail)

    fil = sub.add_parser('filings',
                         help='dated NSE corporate announcements -- the exchange primary source')
    fil.add_argument('symbol')
    fil.add_argument('--days', type=int, default=45)
    fil.add_argument('--limit', type=int, default=10)
    fil.add_argument('--refresh', action='store_true')
    fil.set_defaults(func=cmd_filings)

    num = sub.add_parser('financials',
                         help="the issuer's own regulation-33 numbers, read from the XBRL instance NSE hosts")
    num.add_argument('symbol', help='RELIANCE, M&M, UNIPARTS (NSE scrip name; .NS is ignored)')
    num.add_argument('--scope', default='Standalone', choices=('Standalone', 'Consolidated'))
    num.add_argument('--quarters', type=int, default=2, help='how many filed quarters to read (default 2)')
    num.add_argument('--refresh', action='store_true', help='ignore the 6-hour filing cache')
    num.add_argument('--json', action='store_true', help='print the whole document instead of the table')
    num.set_defaults(func=cmd_financials)

    rep = sub.add_parser('report', help='dated session report (markdown + HTML) from saved files only')
    rep.add_argument('--symbol', help='write a one-symbol report instead: RELIANCE, NIFTY 24000 CE')
    rep.add_argument('--no-html', action='store_true', help='write only the markdown file')
    rep.set_defaults(func=cmd_report)

    journal = sub.add_parser('journal', help='paper ledger: cash, positions, events')
    journal.set_defaults(func=cmd_journal)

    team = sub.add_parser('team', help='seal a specialist-team evidence session for one symbol')
    team.add_argument('symbol')
    team.add_argument('--tick', type=float)
    team.add_argument('--days', type=int, default=60)
    team.add_argument('--simulations', type=int, default=2000)
    team.add_argument('--as-of', help='seal a labelled replay room at a past IST instant, e.g. '
                                      '2026-11-05T10:41:00 -- the engine seals only the bars that '
                                      'had already completed by then')
    team.set_defaults(func=cmd_team)

    trev = sub.add_parser('team-review',
                          help='seal a room and run all ten specialists on it in one pass')
    trev.add_argument('symbol')
    trev.add_argument('--tick', type=float)
    trev.add_argument('--days', type=int, default=60)
    trev.add_argument('--simulations', type=int, default=2000)
    trev.add_argument('--as-of', help='run a labelled replay room at a past IST instant')
    trev.set_defaults(func=cmd_team_review)

    tfin = sub.add_parser('team-finish',
                          help='run the ten specialists on an ALREADY sealed room and finalize it')
    tfin.add_argument('session')
    tfin.set_defaults(func=cmd_team_finish)

    tdis = sub.add_parser('team-dispatch',
                          help='bind a role to the agent identity that was actually given it')
    tdis.add_argument('session')
    tdis.add_argument('role', choices=list(teamflow_roles()))
    tdis.add_argument('--agent-id', required=True)
    tdis.set_defaults(func=cmd_team_dispatch)

    trec = sub.add_parser('team-record',
                          help='save and bind one reviewer file written by that agent')
    trec.add_argument('session')
    trec.add_argument('role', choices=list(teamflow_roles()))
    trec.add_argument('--agent-id', required=True)
    trec.add_argument('--review', required=True, help='JSON with verdict, summary, evidence')
    trec.set_defaults(func=cmd_team_record)

    trep = sub.add_parser('team-report', help='finalize and print the team roll-up')
    trep.add_argument('session', nargs='?')
    trep.add_argument('--show-only', action='store_true', help='read the saved report.json, do not recompute')
    trep.set_defaults(func=cmd_team_report)

    dash = sub.add_parser('dashboard', help='build (first time) and serve the local React dashboard')
    dash.add_argument('--port', type=int, default=8788)
    dash.set_defaults(func=cmd_dashboard)

    args = parser.parse_args(argv)
    if not hasattr(args, 'days'):
        args.days = 5
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    return args.func(args) or 0


if __name__ == '__main__':
    raise SystemExit(main())
