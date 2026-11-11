"""Answer a search box: the desk's saved state plus a live read for anything it misses.

The saved-file sections are instant and carry their own timestamps. When a symbol is
not in them -- any NSE stock, an index, or a call/put strike -- `detail` fetches fresh
NSE bars and the option chain for it and the `detail` section reports that, with the
failure text kept when a feed refuses. Nothing is invented either way.
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / 'workspace'
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
STALE_AFTER = 900

# NSE option-chain symbols and the index names the chart host uses for them.
INDEX_ALIASES = {'NIFTY': 'NIFTY 50', 'NIFTY50': 'NIFTY 50', 'NIFTY 50': 'NIFTY 50',
                 'BANKNIFTY': 'NIFTY BANK', 'NIFTYBANK': 'NIFTY BANK', 'NIFTY BANK': 'NIFTY BANK',
                 'FINNIFTY': 'NIFTY FIN SERVICE', 'NIFTYFIN': 'NIFTY FIN SERVICE',
                 'NIFTY FIN SERVICE': 'NIFTY FIN SERVICE', 'MIDCPNIFTY': 'NIFTY MID SELECT',
                 # NIFTY IT is on the desk's own index board but has no published chain, so
                 # it maps to itself and stays out of CHAIN_FOR_INDEX.
                 'NIFTY IT': 'NIFTY IT',
                 'SENSEX': 'SENSEX'}
SIDES = {'CE': 'CE', 'CALL': 'CE', 'C': 'CE', 'PE': 'PE', 'PUT': 'PE', 'P': 'PE'}
# The index names the NSE chain endpoint publishes, keyed by the chart host's name. An
# index the desk boards but NSE does not publish a chain for (NIFTY IT) is deliberately
# absent, so no button is offered that would only be able to fail.
CHAIN_FOR_INDEX = {'NIFTY 50': 'NIFTY', 'NIFTY BANK': 'BANKNIFTY',
                   'NIFTY FIN SERVICE': 'FINNIFTY', 'NIFTY MID SELECT': 'MIDCPNIFTY',
                   'SENSEX': 'SENSEX'}


def parse(text):
    """Classify a search string: an option strike, an index, or an equity symbol."""
    raw = ' '.join(str(text or '').upper().split())
    if not raw:
        return {'kind': 'empty', 'query': ''}
    stamp = re.search(r'\b\d{1,2}-[A-Z]{3}-\d{4}\b', raw)
    if stamp:
        # "NIFTY 09-NOV-2026 23800 CALL" is as natural as the trailing-expiry form.
        token = stamp.group(0)
        raw = ' '.join(raw.replace(token, ' ').split()) + ' ' + token
    option = re.fullmatch(
        r'([A-Z0-9 &.-]*?)\s*(\d+(?:\.\d{1,2})?)\s*(CE|CALL|C|PE|PUT|P)(?:\s+(\d{1,2}-[A-Z]{3}-\d{4}))?',
        raw)
    if option and option.group(2):
        base = option.group(1).strip() or 'NIFTY'
        chain_symbol = base.replace(' ', '')
        index_name = INDEX_ALIASES.get(base) or INDEX_ALIASES.get(chain_symbol)
        return {'kind': 'option', 'query': raw, 'underlying': base, 'chain_symbol': chain_symbol,
                'option_kind': 'index' if index_name else 'equity',
                'symbol': index_name or (base if '.' in base else base + '.NS'),
                'bare': base.split('.')[0],
                'strike': float(option.group(2)), 'side': SIDES[option.group(3)],
                'expiry': _expiry_text(option.group(4)) if option.group(4) else None}
    if raw in INDEX_ALIASES or re.fullmatch(r'(NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY|SENSEX|INDIA ?VIX)', raw):
        key = INDEX_ALIASES.get(raw, 'INDIA VIX' if 'VIX' in raw else raw)
        return {'kind': 'index', 'query': raw, 'symbol': key, 'chain_symbol': raw.replace(' ', '')}
    symbol = raw if '.' in raw else raw + '.NS'
    return {'kind': 'equity', 'query': raw, 'symbol': symbol,
            'bare': raw.split('.')[0], 'chain_symbol': raw.split('.')[0]}


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _stamp(doc):
    """(iso timestamp, age seconds, stale flag) for one saved file."""
    value = doc.get('as_of') or doc.get('created_at') or doc.get('timestamp') if isinstance(doc, dict) else None
    if not value:
        return None, None, True
    try:
        when = dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return str(value), None, True
    if when.tzinfo is None:
        when = when.replace(tzinfo=IST)
    age = (dt.datetime.now(IST) - when).total_seconds()
    return when.astimezone(IST).isoformat(), (int(age) if age >= 0 else None), age < 0 or age > STALE_AFTER


def _signals(picked, workspace):
    doc = _load(Path(workspace) / 'signals.json')
    if not doc:
        return None, 'no signal board on file yet -- press "Recalculate the signal board" below'
    wanted = {str(picked.get('symbol') or '').upper(), str(picked.get('symbol') or '').upper().replace(' ', '')}
    key = picked.get('chain_symbol') or picked.get('bare')
    wanted.add(str(key or '').upper())
    for row in (doc.get('equity') or []) + (doc.get('index') or []):
        if str(row['symbol']).upper() in wanted or str(row['symbol']).upper().replace(' ', '') in wanted:
            return {'source': 'workspace/signals.json', 'kind': row['kind'], 'symbol': row['symbol'],
                    'verdict': row['verdict'], 'score': row['score'], 'actionable': row['actionable'],
                    'bar_forming': row.get('bar_forming'), 'indicators': row['indicators'],
                    'reasons': row['reasons'], 'vetoes': row['vetoes'],
                    'setups': row.get('setups') or [], 'realized_vol_pct': row.get('realized_vol_pct')}, *_stamp(doc)
    return None, 'no daily-bar verdict for this symbol in the saved signal board'


def _scan(picked, workspace):
    doc = _load(Path(workspace) / 'watchlist.json')
    if not doc:
        return None, 'no saved scan on file yet -- press "Run the daily setup scan" below'
    symbol = str(picked.get('symbol') or '').upper()
    for row in (doc.get('hits') or []) + (doc.get('results') or []):
        if str(row.get('symbol') or '').upper() == symbol:
            return {'source': 'workspace/watchlist.json', 'symbol': symbol,
                    'setups': row.get('setups') or [], 'price': row.get('price'),
                    'rsi': row.get('rsi'), 'ma20': row.get('ma20'), 'high20': row.get('high20'),
                    'volume_x': row.get('volume_x'), 'run_pct': row.get('run_pct'),
                    'candle_as_of': row.get('as_of')}, *_stamp(doc)
    return None, '%s is not in the saved scan universe' % symbol


def _chain_rows(workspace):
    """Every saved chain, newest per underlying+expiry, so a search can pick one."""
    found = {}
    for path in sorted(Path(workspace).glob('options-*.json')) + [Path(workspace) / 'options.json']:
        doc = _load(path)
        if not doc or not doc.get('rows'):
            continue
        key = (str(doc.get('symbol') or '').upper(), doc.get('expiry'))
        stamp = str(doc.get('as_of') or '')
        if key not in found or stamp > str(found[key].get('as_of') or ''):
            found[key] = doc
    return list(found.values())


def _expiry_text(value):
    """NSE spells expiries 23-Nov-2026; the search string is uppercased, so restore it."""
    try:
        return dt.datetime.strptime(str(value), '%d-%b-%Y').strftime('%d-%b-%Y')
    except ValueError:
        return str(value)


def _option(picked, workspace):
    strike = picked.get('strike')
    if strike is None:
        return None, 'not an option query'
    wanted_side = picked['side']
    base = str(picked.get('chain_symbol') or '').upper().replace(' ', '')
    chains = [c for c in _chain_rows(workspace)
              if str(c.get('symbol') or '').upper().replace(' ', '') == base]
    if picked.get('expiry'):
        wanted = str(picked['expiry']).upper()
        matching = [c for c in chains if str(c.get('expiry') or '').upper() == wanted]
        if not matching:
            # Answering a named expiry from a different one would just be wrong.
            return None, ('no saved chain for %s at %s -- "Read the live option chain" below asks NSE for it'
                          % (picked.get('chain_symbol'), _expiry_text(picked['expiry'])))
        chains = matching
    if not chains:
        return None, ('no saved chain for %s on disk -- "Read the live option chain" below fetches one'
                      % picked.get('chain_symbol'))
    chain = max(chains, key=lambda c: str(c.get('as_of') or ''))
    row = next((r for r in chain['rows'] if r.get('strike') == strike), None)
    if row is None:
        nearest = min(chain['rows'], key=lambda r: abs((r.get('strike') or 0) - strike))
        return None, ('%s %s is not a listed strike; nearest is %s'
                      % (base, strike, nearest.get('strike')))
    underlying = chain.get('underlying')
    legs = {}
    for side in ('CE', 'PE'):
        leg = row.get(side) or {}
        legs[side] = {'ltp': leg.get('ltp'), 'iv': leg.get('iv'), 'oi': leg.get('oi'),
                      'chg_oi': leg.get('chg_oi'), 'volume': leg.get('volume'),
                      'chg': leg.get('chg'), 'bid': leg.get('bid'), 'ask': leg.get('ask')}
    atm = chain.get('atm_strike')
    moneyness = ('ITM' if (strike < underlying) == (wanted_side == 'CE') else 'OTM') if underlying else None
    priced = sum(1 for r in chain['rows'] if (r.get('CE') or {}).get('ltp') or (r.get('PE') or {}).get('ltp'))
    return {'source': 'NSE option-chain-v3 snapshot (public website; provider terms apply)',
            'symbol': chain.get('symbol'), 'expiry': chain.get('expiry'), 'strike': strike,
            'side': wanted_side, 'moneyness': moneyness, 'underlying': underlying,
            'atm_strike': atm, 'distance_from_atm': round(strike - atm, 2) if atm is not None else None,
            'legs': legs, 'pcr_oi': chain.get('pcr_oi'), 'resistance_oi': chain.get('resistance_oi'),
            'support_oi': chain.get('support_oi'), 'strikes': chain.get('strikes'),
            'priced_strikes': priced, 'expiry_dates': chain.get('expiry_dates') or [],
            'nse_timestamp': chain.get('timestamp')}, *_stamp(chain)


def _news(picked, workspace):
    doc = _load(Path(workspace) / 'news.json')
    if not doc:
        return None, 'no headline run on file yet -- press "Sweep dated headlines" below'
    feed = doc.get('headlines') or {}
    rows = (list(feed.get('items') or []) + list(feed.get('market') or []) + list(doc.get('items') or []))
    symbols = {str(picked.get(key) or '').upper()
               for key in ('symbol', 'bare', 'chain_symbol')}
    words = {str(picked.get(key) or '').upper() for key in ('chain_symbol', 'bare')}
    words |= {re.sub(r'\.(NS|BO)$', '', str(picked.get('symbol') or '').upper())}
    words.discard('')
    hits = []
    for row in rows:
        symbol = str(row.get('symbol') or row.get('ticker') or '').upper()
        title = str(row.get('title') or row.get('headline') or '').upper()
        if symbol in symbols or any(word and word in title for word in words):
            hits.append(dict(symbol=symbol or 'market', title=row.get('title') or row.get('headline'),
                             publisher=row.get('publisher') or row.get('source'),
                             published_at=row.get('published_at') or row.get('published'),
                             link=row.get('link')))
    hits.sort(key=lambda row: str(row.get('published_at') or ''), reverse=True)
    if not hits:
        return None, ('%d headline(s) at %s, none about this symbol -- press "Sweep dated headlines" below'
                      % (len(rows), (_stamp(doc)[0] or 'unknown time')))
    return {'source': 'workspace/news.json (vendor items + desk RSS pass)',
            'note': 'Headlines are leads, not verified facts. Open the publisher link before acting.',
            'items': hits[:12]}, *_stamp(doc)


def _stamp_now():
    """Ledger files are mirrors of the SQLite journal; stamp them at read time."""
    return dt.datetime.now(IST).isoformat(), 0, False


def _ledger(picked, workspace):
    symbol = str(picked.get('symbol') or '').upper()
    out = {}
    portfolio = _load(Path(workspace) / 'portfolio.json')
    if portfolio:
        out['position'] = [p for p in (portfolio.get('positions') or [])
                           if str(p.get('symbol') or '').upper() == symbol]
        out['cash'] = portfolio.get('cash')
        out['realized_pnl'] = portfolio.get('realized_pnl')
    alerts = _load(Path(workspace) / 'alerts.json')
    if alerts:
        out['alerts'] = [a for a in (alerts.get('alerts') or [])
                         if str(a.get('symbol') or '').upper() == symbol]
    for name, key in (('entry', 'entry.json'), ('plan', 'plan.json'), ('orders', 'orders.json')):
        doc = _load(Path(workspace) / key)
        if not doc:
            continue
        text = json.dumps(doc).upper()
        if symbol in text:
            out[name] = {'file': key, 'summary': {k: doc.get(k) for k in
                                                  ('symbol', 'status', 'setup', 'signal_bar_time',
                                                   'shares', 'entry', 'stop', 'target', 'risk_inr')
                                                  if k in doc}}
    out['symbol'] = symbol
    out['source'] = 'workspace ledger files (read at request time)'
    matched = bool(out.get('position') or out.get('alerts')
                   or any(k in out for k in ('entry', 'plan', 'orders')))
    if not matched:
        return None, 'no open position, alert or pending plan for %s' % symbol
    return out, *_stamp_now()


def _envelope(picked, workspace):
    doc = _load(Path(workspace) / 'envelope.json')
    if not doc:
        return None, 'no risk envelope on file -- the envelope panel refreshes itself from the desk ledger'
    bars = doc.get('bars') or {}
    return {'source': 'workspace/envelope.json',
            'risk_room_inr': doc.get('risk_room_inr'), 'buy_room_inr': doc.get('buy_room_inr'),
            'trades_left': doc.get('trades_left'), 'orders_left': doc.get('orders_left'),
            'loss_at_risk': (bars.get('loss_at_risk') or {}),
            'cash': doc.get('cash'), 'open_positions': doc.get('open_positions'),
            'entry_window': doc.get('entry_window'), 'last_risk_decision': doc.get('last_risk_decision'),
            'block_reasons': doc.get('block_reasons') or [],
            'counters_stale': doc.get('counters_stale'), 'counters_date': doc.get('counters_date')}, *_stamp(doc)


SECTION_FUNCS = ('signals', 'scan', 'option', 'ledger', 'news', 'envelope')


def _detail(picked, workspace, text, refresh=False):
    """Live NSE read for the searched symbol: bars, indicators, chain, lot and tick."""
    try:
        import detail
    except (ImportError, SyntaxError) as exc:
        return None, 'live detail unavailable: %s' % str(exc)[:160]
    try:
        doc = detail.run(text, refresh=refresh, workspace=workspace)
    except Exception as exc:  # noqa: BLE001 -- a feed failure is a note, never a guess
        return None, 'live detail failed: %s: %s' % (exc.__class__.__name__, str(exc)[:180])
    if not doc.get('sections') and not doc.get('suggestions'):
        return None, 'NSE gave no bars and no chain for %s (%s)' % (
            text, json.dumps(doc.get('absent') or {}, default=str)[:220])
    payload = {key: doc[key] for key in ('identity', 'sections', 'absent', 'sizing', 'sizing_note',
                                         'suggestions', 'source', 'directory_cached', 'cache_hit')
               if key in doc}
    payload['query'] = doc['query']
    payload['kind'] = picked['kind']
    payload['fetched_at'] = doc.get('fetched_at') or doc.get('as_of')
    return payload, doc.get('as_of'), 0, False


def run(text, workspace=WORKSPACE, fetch=True, refresh=False):
    picked = parse(text)
    doc = {'query': str(text or ''), 'parsed': picked, 'sections': {}, 'absent': {},
           'as_of': dt.datetime.now(IST).isoformat(), 'paper_only': True,
           'source': ('saved desk workspace files, plus one live NSE read for the searched symbol'
                      if fetch else 'saved desk workspace files only -- no request was made')}
    if picked['kind'] == 'empty':
        doc['note'] = 'Type a symbol (RELIANCE), an index (NIFTY 50) or a strike (NIFTY 24200 CE).'
        return doc
    runners = {'signals': _signals, 'scan': _scan, 'ledger': _ledger, 'news': _news, 'envelope': _envelope}
    if picked['kind'] == 'option':
        runners['option'] = _option
    for name in SECTION_FUNCS:
        function = runners.get(name)
        if function is None:
            continue
        result = function(picked, Path(workspace))
        payload = result[0]
        if payload is None:
            doc['absent'][name] = result[1]
            continue
        payload['data_as_of'], payload['age_seconds'], payload['stale'] = result[1], result[2], result[3]
        doc['sections'][name] = payload
    if fetch:
        result = _detail(picked, Path(workspace), text, refresh=refresh)
        payload = result[0]
        if payload is None:
            doc['absent']['detail'] = result[1]
        else:
            payload['data_as_of'], payload['age_seconds'], payload['stale'] = result[1], result[2], result[3]
            doc['sections']['detail'] = payload
    # The envelope is desk-wide context; it alone must not make an unknown symbol look answered.
    doc['answered'] = bool(set(doc['sections']) - {'envelope'})
    if not doc['answered']:
        doc['unknown'] = ('Neither the saved desk state nor the live NSE pass has anything on %s. '
                          'Sections above are desk-wide context only.' % (picked.get('symbol') or text))
    doc['next'] = _next_steps(picked, doc)
    return doc


def _chain_arg(picked):
    """The symbol the NSE chain endpoint accepts for this search, or None if it has no chain."""
    if picked['kind'] == 'index' or picked.get('option_kind') == 'index':
        return CHAIN_FOR_INDEX.get(picked.get('symbol'))
    return picked.get('chain_symbol') or picked.get('bare')


def _next_steps(picked, doc):
    """What to read next, as dashboard controls rather than shell text.

    Each step names a command from the server's own frozen allowlist, so a click runs
    it; no terminal is needed and no browser-side string ever becomes an argv. `why`
    says what the read adds, and a step with `search` is a symbol chip, not a command.
    """
    steps = []
    symbol = picked.get('symbol') or picked.get('chain_symbol') or doc['query']
    near = (doc['sections'].get('detail') or {}).get('suggestions') or []
    if near:
        return [{'label': name, 'search': name,
                 'why': 'NSE has no scrip by that name; this is a live one it does list.'}
                for name in near[:3]]

    def add(label, command, why, for_symbol=None):
        step = {'label': label, 'command': command, 'why': why}
        if for_symbol:
            step['symbol'] = for_symbol
        steps.append(step)

    if picked['kind'] == 'option':
        if _chain_arg(picked):
            add('Read the live option chain', 'options',
                'Premiums, IV, open interest and the OI change at the nearest expiry, with the '
                'call and put walls and the PCR.',
                _chain_arg(picked))
        add('Recalculate the signal board', 'signals',
            'Daily-bar ADX, +DI/-DI, MACD, RSI and ATR verdicts for the saved universe, '
            'including the underlying index.')
    elif picked['kind'] == 'equity':
        if 'signals' not in doc['sections'] and 'detail' not in doc['sections']:
            add('Read the live daily bars and indicators', 'detail',
                'One NSE pass for %s: ADX, +DI/-DI, MACD, RSI, ATR, today\'s 5m tape and the '
                'opening range, with the BUY / HOLD / SELL verdict computed on them.' % symbol,
                doc['query'])
        if 'scan' not in doc['sections']:
            add('Run the daily setup scan', 'scan daily',
                'Scores the desk universe and is the file the opportunities board reads; %s '
                'appears in it once it has run.' % symbol)
        add('Run the paper chain for %s' % symbol, 'setup',
            'Completed-bar entry, tick-rounded exit plan, sizing under the Rs 500 and Rs 1 lakh '
            'caps, the risk check and a ticket. It stops at the ticket: only your own reply '
            'approves it, and nothing is ever sent to a broker.')
    else:
        add('Read the live index board', 'index',
            'NIFTY 50, BANKNIFTY, FINNIFTY and NIFTY IT: opening ranges, day levels, futures '
            'premium and how wide the advance is.')
        if _chain_arg(picked):
            add('Read the option chain', 'options',
                'The chain NSE publishes for this index at the nearest expiry, with the OI '
                'walls. NIFTY IT has no published chain, so no button appears for it.',
                _chain_arg(picked))
        add('Run the 10 specialists on %s' % symbol, 'team review',
            'Freezes today\'s bars, costs and limits as immutable evidence, then puts each '
            'role\'s own checker on its own sealed artifact and finalizes the room. It starts '
            'no live order, approves nothing and books no ticket -- only your own reply does '
            'that, and a room with nothing to take ends BLOCKED, which is a real answer.',
            symbol)
    if 'detail' not in doc['sections'] and picked['kind'] != 'equity':
        add('Fetch the live NSE read', 'detail',
            'Bars, today\'s tape, the exact strike with its lot and tick, and the filings the '
            'issuer lodged with the exchange.', doc['query'])
    if 'news' not in doc['sections']:
        add('Sweep dated headlines', 'news',
            'The desk headline feeds with their own publication stamps, deduplicated per day.')
    add('Write the one-symbol report', 'symbol report',
        'Markdown plus HTML in reports/, carrying every number above with the timestamp of the '
        'file it came from.', symbol or doc['query'])
    return steps


def print_result(doc):
    picked = doc['parsed']
    print('search %s -> %s' % (doc['query'], picked['kind']))
    if not doc.get('answered'):
        print('  %s' % doc.get('unknown', 'Nothing in the saved desk state matched.'))
        for name, why in doc['absent'].items():
            if name != 'envelope':
                print('    %-9s %s' % (name + ':', why))
        for step in doc.get('next', []):
            _print_step(step, '  ')
        return
    for name, payload in doc['sections'].items():
        age = '%ss' % payload['age_seconds'] if payload.get('age_seconds') is not None else 'undated'
        print('\n%s  (%s%s)' % (name.upper(), age, ', STALE' if payload.get('stale') else ''))
        if name == 'signals':
            i = payload['indicators']
            print('  %s %s (score %d)%s' % (payload['symbol'], payload['verdict'], payload['score'],
                                             '' if payload['actionable'] else ' -- blocked'))
            print('  ADX %s  +DI %s  -DI %s  RSI %s  ATR%% %s  MACDh %s  volx %s  rv %s%%' % (
                i.get('adx'), i.get('di_plus'), i.get('di_minus'), i.get('rsi_14'), i.get('atr_pct'),
                (i.get('macd') or {}).get('histogram'), i.get('volume_x'), payload.get('realized_vol_pct')))
            for reason in payload['reasons']:
                print('    - %s' % reason)
            for veto in payload['vetoes']:
                print('    ! %s' % veto)
        elif name == 'option':
            leg = payload['legs'][payload['side']]
            other = payload['legs']['PE' if payload['side'] == 'CE' else 'CE']
            print('  %s %s %s %s | underlying %s | ATM %s | %s%% from ATM' % (
                payload['symbol'], '{:,.0f}'.format(payload['strike']), payload['side'],
                payload['moneyness'], payload['underlying'], payload['atm_strike'],
                round((payload['strike'] / payload['underlying'] - 1) * 100, 2) if payload['underlying'] else 'n/a'))
            print('  queried leg: ltp %s  IV %s  OI %s  chg OI %s  volume %s  bid/ask %s/%s' % (
                leg['ltp'], leg['iv'], leg['oi'], leg['chg_oi'], leg['volume'], leg['bid'], leg['ask']))
            print('  other side : ltp %s  IV %s  OI %s  chg OI %s' % (
                other['ltp'], other['iv'], other['oi'], other['chg_oi']))
            print('  chain: PCR %s | walls %s call / %s put | %s/%s strikes priced | expiries %s' % (
                payload['pcr_oi'], payload['resistance_oi'], payload['support_oi'],
                payload['priced_strikes'], payload['strikes'],
                ', '.join(payload['expiry_dates'][:4])))
        elif name == 'scan':
            print('  %s | setups %s | price %s | rsi %s | ma20 %s | high20 %s | volx %s' % (
                payload['symbol'], ','.join(payload['setups']) or 'none', payload['price'],
                round(payload['rsi'], 1) if payload.get('rsi') is not None else '-',
                payload['ma20'], payload['high20'],
                round(payload['volume_x'], 2) if payload.get('volume_x') else '-'))
            print('  candle date %s' % str(payload.get('candle_as_of'))[:10])
        elif name == 'ledger':
            for position in payload.get('position') or []:
                print('  open: %s shares %s | stop %s target %s' % (
                    position.get('symbol'), position.get('shares'),
                    position.get('stop'), position.get('target')))
            if not payload.get('position'):
                print('  no open position; cash %s | realized %s' % (
                    payload.get('cash'), payload.get('realized_pnl')))
            for alert in payload.get('alerts') or []:
                print('  alert %s %s @ %s (%s)' % (alert.get('type'), alert.get('symbol'),
                                                   alert.get('price'), alert.get('note')))
            for name2, value in payload.items():
                if name2 in ('entry', 'plan', 'orders'):
                    print('  %s: %s' % (name2, json.dumps(value['summary'])[:160]))
        elif name == 'news':
            for item in payload['items'][:6]:
                print('  - %-14s %s  %s' % (str(item.get('publisher') or '?')[:14],
                                            str(item.get('published_at') or '')[:16],
                                            str(item.get('headline') or item.get('title'))[:88]))
        elif name == 'envelope':
            print('  risk room %s | buy room %s | trades left %s | orders left %s' % (
                payload.get('risk_room_inr'), payload.get('buy_room_inr'),
                payload.get('trades_left'), payload.get('orders_left')))
            loss = payload.get('loss_at_risk') or {}
            print('  daily loss used %s of %s | open positions %s | last risk decision %s%s' % (
                loss.get('used'), loss.get('limit'), payload.get('open_positions'),
                payload.get('last_risk_decision'),
                ' (counters from %s)' % payload.get('counters_date') if payload.get('counters_stale') else ''))
            for reason in (payload.get('block_reasons') or [])[:3]:
                print('    ! %s' % reason)
        elif name == 'detail':
            import detail
            detail.print_detail({'query': doc['query'], 'parsed': picked,
                                 'as_of': payload.get('data_as_of') or doc['as_of'],
                                 'identity': payload.get('identity'), 'sections': payload['sections'],
                                 'absent': payload.get('absent') or {}, 'sizing': payload.get('sizing'),
                                 'sizing_note': payload.get('sizing_note'), 'notes': [],
                                 'cache_hit': payload.get('cache_hit'),
                                 'fetched_at': payload.get('fetched_at')})
    for name, why in doc['absent'].items():
        print('\nnot available: %s -- %s' % (name, why))
    for step in doc.get('next') or []:
        _print_step(step, '')


def _print_step(step, indent):
    """Terminal view of a clickable step: the desk names the read, not a shell line."""
    target = step.get('search') or step.get('symbol') or ''
    print('%s> %s%s\n%s    %s' % (indent, step['label'], ' (%s)' % target if target else '',
                                  indent, step['why']))


if __name__ == '__main__':
    import argparse
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Search answer: saved desk state + live NSE detail')
    parser.add_argument('query', nargs='*', help='RELIANCE, NIFTY 50, NIFTY 24000 CE')
    parser.add_argument('--refresh', action='store_true', help='re-read NSE instead of the 15-minute cache')
    parser.add_argument('--no-fetch', action='store_true', help='saved files only, no request at all')
    args = parser.parse_args()
    text = ' '.join(args.query) or 'NIFTY'
    print_result(run(text, fetch=not args.no_fetch, refresh=args.refresh))
