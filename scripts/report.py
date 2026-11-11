"""One dated report built only from the desk's saved files.

Nothing here fetches or decides: it reads the signal board, option chains, index
board, risk envelope, news run and paper ledger that the desk already wrote, and
lines them up for a session recap. Every number carries the timestamp of the file
it came from, so a stale section reads as stale rather than as current.
"""
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / 'workspace'
REPORTS = ROOT / 'reports'
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _files(workspace):
    out = {}
    for name, key in (('signals', 'signals.json'), ('envelope', 'envelope.json'),
                      ('index', 'index.json'), ('news', 'news.json'), ('scan', 'watchlist.json'),
                      ('portfolio', 'portfolio.json'), ('orders', 'orders.json'),
                      ('alerts', 'alerts.json'), ('risk', 'state/risk.json')):
        out[name] = _load(Path(workspace) / key)
    chains = []
    for path in sorted(Path(workspace).glob('options-*.json')):
        doc = _load(path)
        if doc and doc.get('rows'):
            chains.append(doc)
    out['chains'] = chains
    events = []
    log = Path(workspace) / 'state' / 'actions.jsonl'
    if log.is_file():
        for line in log.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    out['actions'] = events
    return out


def _clock(doc):
    value = (doc or {}).get('as_of')
    return str(value)[:16].replace('T', ' ') + ' IST' if value else 'no timestamp'


def _pct(value):
    return '%+.2f%%' % value if isinstance(value, (int, float)) else 'n/a'


def _headlines(doc):
    """Vendor items plus the desk RSS pass, newest first, publisher kept."""
    news = doc or {}
    feed = news.get('headlines') or {}
    rows = [dict(symbol=row.get('symbol'), title=row.get('title'), publisher=row.get('publisher'),
                 published=row.get('published_at'))
            for group in ('items', 'market') for row in (feed.get(group) or [])]
    rows += [dict(symbol=row.get('symbol') or row.get('ticker'),
                  title=row.get('headline') or row.get('title'),
                  publisher=row.get('source') or row.get('publisher'),
                  published=row.get('published') or row.get('published_at') or row.get('as_of'))
             for row in (news.get('items') or [])]
    rows.sort(key=lambda row: str(row.get('published') or ''), reverse=True)
    return rows


def _team(workspace):
    """Newest finalized specialist-team roll-up, read from the sealed session."""
    root = Path(workspace) / 'team'
    if not root.is_dir():
        return None
    newest = max((p for p in root.iterdir() if (p / 'report.json').is_file()),
                 key=lambda p: (p / 'report.json').stat().st_mtime, default=None)
    if not newest:
        return None
    doc = json.loads((newest / 'report.json').read_text(encoding='utf-8'))
    return dict(session=newest.name, **doc)


def build(workspace=WORKSPACE, now=None):
    files = _files(workspace)
    now = now or dt.datetime.now(IST)
    signals = files.get('signals') or {}
    rows = (signals.get('equity') or []) + (signals.get('index') or [])
    today = [e for e in (files.get('actions') or []) if str(e.get('time'))[:10] == now.date().isoformat()]
    sections = {
        'generated': now.isoformat(),
        'date': now.date().isoformat(),
        'paper_only': True,
        'execution_enabled': False,
        'capital': (files.get('portfolio') or {}).get('cash'),
        'realized_pnl': (files.get('portfolio') or {}).get('realized_pnl'),
        'open_positions': (files.get('portfolio') or {}).get('positions') or [],
        'envelope': files.get('envelope'),
        'signals_as_of': _clock(signals),
        'counts': signals.get('counts') or {},
        'actionable': signals.get('actionable'),
        'rows': sorted(rows, key=lambda r: -r.get('score', 0)),
        'buy': [r for r in rows if r.get('verdict') == 'BUY' and r.get('actionable')],
        'sell': [r for r in rows if r.get('verdict') == 'SELL'],
        'blocked': [r for r in rows if not r.get('actionable')],
        'hold': [r for r in rows if r.get('verdict') == 'HOLD'],
        'vix': signals.get('vix') or {},
        'options': files.get('chains') or [],
        'option_reads': signals.get('options') or [],
        'index_board': files.get('index'),
        'news': _headlines(files.get('news')),
        'news_as_of': _clock(files.get('news')),
        'scan': files.get('scan'),
        'orders': files.get('orders'),
        'alerts': (files.get('alerts') or {}).get('alerts') or [],
        'actions_today': today,
        'team': _team(workspace),
        'stale': [],
        'notes': [],
    }
    for name, doc in (('signal board', signals), ('risk envelope', files.get('envelope')),
                      ('index board', files.get('index'))):
        value = (doc or {}).get('as_of')
        if not value:
            sections['stale'].append('%s: no saved file' % name)
            continue
        try:
            age = (now - dt.datetime.fromisoformat(str(value))).total_seconds()
        except (TypeError, ValueError):
            continue
        if age > 7200:
            sections['stale'].append('%s is %s old' % (name, _human(age)))
    if not signals:
        sections['notes'].append('No signal board on file yet -- press Recalculate the signal board on the dashboard.')
    news_doc = files.get('news') or {}
    if str(news_doc.get('as_of'))[:10] != now.date().isoformat():
        sections['stale'].append('news run is from %s, not today' % (str(news_doc.get('as_of'))[:10] or 'never'))
    if not sections['news']:
        sections['notes'].append('The saved news run holds no headlines -- press Sweep dated headlines on the dashboard.')
    return sections


def _human(seconds):
    return '%d min' % round(seconds / 60) if seconds < 5400 else '%.1f h' % (seconds / 3600)


def markdown(sections):
    out = ['# Trading desk report -- %s' % sections['date'], '']
    out.append('_Generated %s IST. Paper environment: nothing in this file was sent to a broker._'
               % sections['generated'][:16].replace('T', ' '))
    out += ['', '## Risk envelope', '']
    env = sections.get('envelope') or {}
    if env:
        bars = env.get('bars') or {}
        loss = bars.get('loss_at_risk') or {}
        out += ['- Room per new trade: **Rs %s** | buy room Rs %s | trades left %s | orders left %s' % (
            env.get('risk_room_inr'), env.get('buy_room_inr'), env.get('trades_left'), env.get('orders_left')),
            '- Loss at risk today: Rs %s of Rs %s | open positions %s | cash Rs %s' % (
                loss.get('used'), loss.get('limit'), env.get('open_positions'), env.get('cash')),
            '- Entry window %s, flat by %s | last risk decision %s' % (
                env.get('entry_window'), env.get('flat_by'), env.get('last_risk_decision'))]
        for reason in (env.get('block_reasons') or [])[:5]:
            out.append('- Blocked: %s' % reason)
        out.append('- As of %s' % _clock(env))
    else:
        out.append('- No risk envelope on file yet -- the Journal page has the button that recomputes it.')
    out += ['', '## Signal board (%s)' % sections['signals_as_of'], '']
    counts = sections.get('counts') or {}
    out.append('Verdicts: **BUY %s / HOLD %s / SELL %s** | %s of %d clear of vetoes.' % (
        counts.get('BUY', 0), counts.get('HOLD', 0), counts.get('SELL', 0),
        sections.get('actionable') or 0,
        len(sections.get('buy', [])) + len(sections.get('hold', [])) + len(sections.get('sell', []))))
    out += ['', '| symbol | verdict | score | close | ADX | +DI | -DI | RSI | ATR% | MACDh | volx | realized vol |',
            '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for row in sections.get('rows', []):
        i = row.get('indicators') or {}
        out.append('| %s | **%s** | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
            row['symbol'], row['verdict'], row['score'], i.get('close'), i.get('adx'), i.get('di_plus'),
            i.get('di_minus'), i.get('rsi_14'), i.get('atr_pct'),
            (i.get('macd') or {}).get('histogram'), i.get('volume_x'), row.get('realized_vol_pct')))
    out.append('')
    if sections.get('blocked'):
        out.append('Vetoes:')
    for row in sections.get('blocked', []):
        for veto in row.get('vetoes') or []:
            out.append('- %s: %s' % (row['symbol'], veto))
    out += ['', '## Index and option tape', '']
    board_doc = sections.get('index_board') or {}
    for row in board_doc.get('indices') or []:
        orb = row.get('orb') or {}
        out.append('- **%s** %s | state %s | ORB %s-%s (%s%% wide) | day %s-%s | gap %s' % (
            row.get('symbol'), row.get('last'), row.get('state'), orb.get('low'), orb.get('high'),
            orb.get('width_pct'), row.get('day_low'), row.get('day_high'), _pct(row.get('gap_pct'))))
    futures = board_doc.get('futures') or {}
    if futures:
        out.append('- %s %s | premium %s pts (%s%%) as of %s' % (
            futures.get('symbol'), futures.get('last'), futures.get('premium_points'),
            futures.get('premium_pct'), _clock({'as_of': futures.get('as_of')})))
    breadth = board_doc.get('breadth') or {}
    if breadth:
        out.append('- Breadth from the saved scan: %s%% of %d symbols above MA20, %d above the 20-day high, '
                   'median RSI %s' % (breadth.get('above_ma20_pct'), breadth.get('symbols'),
                                      breadth.get('above_20d_high'), breadth.get('median_rsi')))
    for read in sections.get('option_reads') or []:
        out.append('- **%s %s** | underlying %s | ATM %s straddle %s (expected move %s%%) | PCR %s | '
                   'walls %s call / %s put | bias %s (%s)' % (
                       read.get('symbol'), read.get('expiry'), read.get('underlying'), read.get('atm_strike'),
                       read.get('atm_straddle'), read.get('expected_move_pct'), read.get('pcr_oi'),
                       read.get('resistance_oi'), read.get('support_oi'), read.get('verdict'), read.get('score')))
    if not (sections.get('option_reads') or []):
        out.append('- No saved option chain yet -- press Read the live option chain on the dashboard.')
    vix = sections.get('vix') or {}
    out.append('- India VIX is deliberately **not** published here: %s Substitutes used: %s.' % (
        vix.get('reason') or 'the chart host series reads ~100x the published index and no keyless '
                             'cross-check answered.',
        '; '.join(vix.get('substitutes') or []) or 'realized vol from the same daily bars'))
    out += ['', '## News and catalysts (%s)' % sections['news_as_of'], '']
    if sections.get('news'):
        for item in sections['news'][:12]:
            out.append('- %s -- %s%s (%s)' % (
                str(item.get('title'))[:120], item.get('symbol') or 'market',
                ' | %s' % item['publisher'][:28] if item.get('publisher') else '',
                item.get('published') or 'no timestamp'))
    else:
        out.append('- No headline in the saved news run; press Sweep dated headlines on the dashboard.')
    out += ['', '## Paper ledger', '']
    out.append('- Cash Rs %s | realized Rs %s | open positions %d' % (
        sections.get('capital'), sections.get('realized_pnl'), len(sections.get('open_positions') or [])))
    for position in sections.get('open_positions') or []:
        out.append('  - %s %s shares | stop %s | target %s' % (
            position.get('symbol'), position.get('shares'), position.get('stop'), position.get('target')))
    for alert in (sections.get('alerts') or [])[:8]:
        level = alert.get('price') or alert.get('at') or alert.get('time') or alert.get('above') \
            or alert.get('below') or '-'
        out.append('  - alert %s %s @ %s (%s)' % (alert.get('type'), alert.get('symbol'), level,
                                                  alert.get('note') or ''))
    orders = sections.get('orders') or {}
    if orders:
        out.append('  - last order file: %s' % json.dumps({k: orders.get(k) for k in
                                                           ('status', 'orders') if k in orders})[:240])
    out += ['', '## What the desk actually ran today', '']
    if sections.get('actions_today'):
        for event in sections['actions_today'][-15:]:
            out.append('- %s `%s` -> exit %s (%ss)' % (
                str(event.get('time'))[11:19], ' '.join(str(a) for a in (event.get('argv') or [])),
                event.get('returncode'), event.get('seconds')))
    else:
        out.append('- No dashboard-run commands recorded yet.')
    team = sections.get('team') or {}
    out += ['', '## Specialist team (%s)' % (team.get('session') or 'no session yet'), '']
    if team:
        orchestration = team.get('orchestration') or {}
        out.append('- %s | cutoff %s | verdict of the room: **%s** | %d reviews recorded from %d distinct '
                   'agent identities | live orders submitted %s' % (
                       team.get('symbol'), str(team.get('as_of'))[:16].replace('T', ' '),
                       team.get('status'), orchestration.get('received_reviews', 0),
                       orchestration.get('distinct_recorded_agent_ids', 0),
                       team.get('live_orders_submitted')))
        for role in ('scanner', 'news', 'analyst', 'entry', 'exit', 'timemachine', 'budget',
                     'risk', 'broker', 'alerts'):
            review = (team.get('reviews') or {}).get(role)
            if not review:
                out.append('  - %s -- no review recorded' % role)
                continue
            out.append('  - %s -- %s: %s' % (role, review.get('verdict'),
                                             str(review.get('summary'))[:200]))
        for label, keys in (('missing', team.get('missing_reviews')),
                            ('rejected as invalid', team.get('invalid_reviews')),
                            ('reviewer vetoes', team.get('vetoes')),
                            ('engine vetoes', team.get('engine_vetoes'))):
            if keys:
                out.append('- %s: %s' % (label, ', '.join(keys)))
        ticket = team.get('paper_ticket')
        out.append('- paper ticket from this room: %s' % (
            json.dumps(ticket)[:200] if ticket else 'none -- the room did not clear one'))
        out.append('- Agent identities are host-recorded provenance, not signed attestation. '
                   'The engine re-derives the numbers at finalize time, so a positive opinion '
                   'cannot override a numerical veto.')
    else:
        out.append('- No finalized team session on disk -- search a symbol on the dashboard and press '
                   'Seal a 10-specialist room for it.')
    if sections.get('stale'):
        out += ['', '## Freshness', ''] + ['- %s' % s for s in sections['stale']]
    if sections.get('notes'):
        out += ['', '## Notes', ''] + ['- %s' % s for s in sections['notes']]
    out += ['', '---', '',
            'A BUY or SELL here is a rule agreement on daily bars, not an order. Entry, exit, sizing and risk '
            'stages decide whether a paper ticket exists, and only your own reply starting with "yes" approves '
            'one. SELL never means a short sale: this desk is long-only cash. Option strikes are context '
            'only -- the lot and tick are on file from the instrument directory, but allow_options is false '
            'and the engine has no option exit model, so no premium becomes a ticket.',
            'Indicator formulas: ADX/DI and ATR use Wilder smoothing, RSI is Wilder 14 (same math as the '
            'scanner), MACD is 12/26/9, EMA20/50, 20-session high/low, volume vs the 20-session average.']
    return '\n'.join(out)


def _inline(escaped):
    """Turn the markdown subset used above into inline HTML."""
    parts = escaped.split('**')
    text = ''
    for index, part in enumerate(parts):
        text += '<b>%s</b>' % part if index % 2 else part
    code = text.split('`')
    return ''.join('<code>%s</code>' % part if index % 2 else part for index, part in enumerate(code))


def _render(text, title):
    lines = text.splitlines()
    body, index = [], 0
    while index < len(lines):
        line = lines[index]
        if line.startswith('|'):
            rows = []
            while index < len(lines) and lines[index].startswith('|'):
                cells = [html.escape(cell.strip()) for cell in lines[index].strip('|').split('|')]
                if not all(set(cell) <= set('-: ') for cell in cells):
                    rows.append(cells)
                index += 1
            head, *data = rows
            body.append('<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (
                ''.join('<th>%s</th>' % cell for cell in head),
                ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % _inline(c) for c in cells) for cells in data)))
            continue
        index += 1
        escaped = html.escape(line)
        if line.startswith('# '):
            body.append('<h1>%s</h1>' % escaped[2:])
        elif line.startswith('## '):
            body.append('<h2>%s</h2>' % escaped[3:])
        elif line.startswith('- '):
            body.append('<ul><li>%s</li></ul>' % _inline(escaped[2:]))
        elif line.startswith('---'):
            body.append('<hr>')
        elif line.strip():
            body.append('<p>%s</p>' % _inline(escaped))
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<title>Trading desk report %s</title><style>%s</style></head><body>%s</body></html>' % (
                html.escape(title),
                'body{font:14px/1.55 system-ui,sans-serif;max-width:1080px;margin:2rem auto;padding:0 1rem;'
                'color:#12181f}h1{font-size:1.5rem;margin:0}h2{font-size:1.05rem;margin:1.8rem 0 .5rem;'
                'border-bottom:1px solid #d7dee6;padding-bottom:.3rem;text-transform:uppercase;'
                'letter-spacing:.04em}table{border-collapse:collapse;width:100%;font-size:13px}'
                'td,th{border:1px solid #d7dee6;padding:.3rem .5rem;text-align:right;font-variant-numeric:'
                'tabular-nums}th:first-child,td:first-child{text-align:left}ul{margin:.2rem 0}'
                'li{margin:.15rem 0}code{background:#f2f5f8;padding:.1rem .3rem}hr{border:0;'
                'border-top:1px solid #d7dee6;margin:1.5rem 0}p:last-child{color:#5a6b7d}',
                '\n'.join(body)))


def html_page(sections):
    return _render(markdown(sections), sections['date'])


# ---------- one symbol, one report ----------

def _team_for(workspace, symbol):
    """Specialist rooms already sealed for this symbol, with their recorded outcome."""
    root = Path(workspace) / 'team'
    if not root.is_dir():
        return []
    wanted = re.sub(r'\.(NS|BO)$', '', str(symbol).upper()).replace(' ', '')
    rows = []
    for folder in sorted(root.iterdir()):
        doc = _load(folder / 'report.json')
        if not doc:
            continue
        listed = re.sub(r'\.(NS|BO)$', '', str(doc.get('symbol') or '').upper()).replace(' ', '')
        if listed != wanted and wanted not in folder.name.upper().replace('-', ''):
            continue
        orchestration = doc.get('orchestration') or {}
        rows.append({'session': folder.name, 'symbol': doc.get('symbol'), 'status': doc.get('status'),
                     'as_of': doc.get('as_of'),
                     'reviews': '%s of %s' % (orchestration.get('received_reviews'),
                                              orchestration.get('required_agents')),
                     'unavailable_reviews': len(doc.get('unavailable_reviews') or []),
                     'vetoes': (doc.get('engine_vetoes') or [])[:4],
                     'paper_ticket': doc.get('paper_ticket'),
                     'live_orders': doc.get('live_orders_submitted')})
    return rows


def _saved(workspace, function, picked):
    """One saved-state section from the search layer, with its refusal text kept."""
    result = function(picked, Path(workspace))
    payload = result[0]
    if payload is None:
        return {'found': False, 'reason': result[1]}
    return {'found': True, 'payload': payload, 'data_as_of': result[1],
            'age_seconds': result[2], 'stale': result[3]}


def build_symbol(text, workspace=WORKSPACE, refresh=False, now=None):
    import detail
    import lookup
    doc = detail.run(text, refresh=refresh, workspace=workspace)
    picked = doc['parsed']
    now = now or dt.datetime.now(IST)
    functions = (('signal board', lookup._signals), ('daily scan', lookup._scan),
                 ('news', lookup._news), ('paper ledger', lookup._ledger),
                 ('risk envelope', lookup._envelope))
    saved = {name: _saved(workspace, function, picked) for name, function in functions}
    return {
        'query': doc['query'], 'kind': picked['kind'], 'parsed': picked,
        'symbol': picked.get('symbol') or doc['query'], 'date': now.date().isoformat(),
        'generated': now.isoformat(), 'market_as_of': doc['as_of'],
        'fetched_at': doc.get('fetched_at'), 'cache_hit': doc.get('cache_hit'),
        'source': doc['source'], 'directory_cached': doc.get('directory_cached'),
        'identity': doc.get('identity'), 'detail': doc,
        'sections': doc['sections'], 'absent': doc['absent'],
        'sizing': doc.get('sizing'), 'sizing_note': doc.get('sizing_note'),
        'notes': doc.get('notes'), 'saved': saved, 'team': _team_for(workspace, picked.get('symbol')),
        'paper_only': True, 'execution_enabled': False,
    }


def _v(value, decimals=None):
    if value is None or value == '':
        return 'n/a'
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    if isinstance(value, (int, float)) and decimals is not None:
        return ('{:,.%df}' % decimals).format(value)
    if isinstance(value, (int, float)):
        return '{:,.2f}'.format(value).rstrip('0').rstrip('.') if isinstance(value, float) else '{:,}'.format(value)
    return str(value)


def _table(rows):
    out = ['| item | value |', '| --- | --- |']
    for label, value in rows:
        out.append('| %s | %s |' % (label, value))
    return out


def symbol_markdown(doc):
    daily = doc['sections'].get('daily') or {}
    indicators = daily.get('indicators') or {}
    macd = indicators.get('macd') or {}
    window = daily.get('window') or {}
    identity = doc.get('identity') or {}
    out = ['# %s -- desk detail report' % doc['query'], '']
    market = 'Market data read %s IST%s' % (
        str(doc['market_as_of'])[:16].replace('T', ' '),
        ' (from the cache fetched %s IST)' % str(doc.get('fetched_at'))[:16].replace('T', ' ')
        if doc.get('cache_hit') else '')
    out.append('_Generated %s IST. Paper desk: nothing in this file was sent to a broker. %s._'
               % (str(doc['generated'])[:16].replace('T', ' '), market))
    out.append('')
    out.append('## What it is')
    parsed_bits = ['%s %s' % (key, value) for key, value in
                   (('kind', doc['kind']), ('strike', doc['parsed'].get('strike')),
                    ('side', doc['parsed'].get('side')), ('expiry', doc['parsed'].get('expiry')),
                    ('underlying', doc['parsed'].get('underlying'))) if value not in (None, '')]
    out += _table([
        ('search read as', '; '.join(parsed_bits)),
        ('symbol', _v(identity.get('symbol'))),
        ('name', _v(identity.get('name'))),
        ('ISIN', _v(identity.get('isin'))),
        ('tick size', 'Rs %s' % _v(identity.get('tick_inr'))),
        ('cash lot', _v(identity.get('lot_size'))),
    ]) + ['']

    out += ['', '## Verdict on the daily tape']
    if not daily:
        out += ['_%s_ -- no daily bars came back, so there is no verdict to print.'
                % _v(doc['absent'].get('daily')), '']
    else:
        out += _table([
            ('verdict', '**%s** (score %s)' % (daily['verdict'], daily['score'])),
            ('last close', _v(indicators.get('close'))),
            ('session change', '%s%%' % _v(indicators.get('change_pct'))),
            ('clear of vetoes', _v(daily.get('actionable'))),
            ('bar still forming', _v(daily.get('bar_forming'))),
            ('realized volatility', '%s%% annualized' % _v(daily.get('realized_vol_pct'), 1)),
        ])
        out += ['', 'Rules that fired:']
        out += ['- %s' % reason for reason in (daily.get('reasons') or [])]
        if daily.get('vetoes'):
            out += ['', 'Vetoes that stop a ticket:']
            out += ['- %s' % veto for veto in daily['vetoes']]
        out += ['']

        out += ['', '## Indicators (daily bars)']
        out += _table([
            ('EMA20 / EMA50', '%s / %s' % (_v(indicators.get('ema20')), _v(indicators.get('ema50')))),
            ('20-bar high / low', '%s / %s' % (_v(indicators.get('high_20')), _v(indicators.get('low_20')))),
            ('ADX / +DI / -DI', '%s / %s / %s' % (_v(indicators.get('adx'), 1),
                                                   _v(indicators.get('di_plus'), 1),
                                                   _v(indicators.get('di_minus'), 1))),
            ('RSI(14)', _v(indicators.get('rsi_14'), 1)),
            ('ATR(14)', '%s (%s%% of price)' % (_v(indicators.get('atr_14')), _v(indicators.get('atr_pct')))),
            ('MACD histogram', '%s (previous %s)' % (_v(macd.get('histogram')), _v(macd.get('histogram_prev')))),
            ('volume vs 20-session average', '%sx' % _v(indicators.get('volume_x'))),
            ('sessions read', '%s (%s to %s)' % (_v(window.get('sessions')), _v(window.get('from')),
                                                 _v(window.get('to')))),
            ('window high / low', '%s (%s) / %s (%s)' % (
                _v(window.get('window_high')), _v(window.get('window_high_date')),
                _v(window.get('window_low')), _v(window.get('window_low_date')))),
            ('distance from window high / low', '%s%% / %s%%' % (
                _v(window.get('pct_from_window_high')), _v(window.get('pct_from_window_low')))),
            ('5-session / 20-session return', '%s%% / %s%%' % (_v(window.get('return_5_pct')),
                                                                _v(window.get('return_20_pct')))),
            ('average volume (20)', _v(window.get('avg_volume_20'))),
            ('volume on the last completed session', '%s on %s (%sx)' % (
                _v(window.get('last_volume')), _v(window.get('volume_bar_date')),
                _v(window.get('volume_x_20')))),
        ]) + ['']

    intraday = doc['sections'].get('intraday')
    out += ['', '## Today on the 5-minute tape']
    if not intraday:
        out += ['_%s_' % _v(doc['absent'].get('intraday')), '']
    else:
        orb = intraday.get('orb') or {}
        out += _table([
            ('bars today', _v(intraday.get('bars_today'))),
            ('open / last', '%s / %s' % (_v(intraday.get('open')), _v(intraday.get('last')))),
            ('day high / low', '%s / %s' % (_v(intraday.get('day_high')), _v(intraday.get('day_low')))),
            ('day range', '%s%%' % _v(intraday.get('range_pct'))),
            ('previous close / gap', '%s / %s%%' % (_v(intraday.get('prev_close')),
                                                    _v(intraday.get('gap_pct')))),
            ('15-minute opening range', '%s - %s (mid %s, width %s%%)' % (
                _v(orb.get('high')), _v(orb.get('low')), _v(orb.get('mid')), _v(orb.get('width_pct')))),
            ('price vs the opening range', '%s (%s up / %s down to break)' % (
                _v(intraday.get('state')), _v(intraday.get('to_break_up')),
                _v(intraday.get('to_break_down')))),
            ('intraday realized volatility', '%s%% annualized' % _v(
                intraday.get('intraday_realized_vol_pct'), 1)),
        ]) + ['']

    option = doc['sections'].get('option')
    chain = doc['sections'].get('chain')
    out += ['', '## Option market']
    if option:
        leg = (option.get('legs') or {}).get(option['side']) or {}
        other = (option.get('legs') or {}).get('PE' if option['side'] == 'CE' else 'CE') or {}
        contract = option.get('contract') or {}
        out += _table([
            ('contract', '%s %s %s, %s' % (option['symbol'], _v(option['strike']),
                                           option['side'], option['expiry'])),
            ('moneyness', '%s, %s%% from the underlying' % (
                _v(option['moneyness']), _v(option.get('pct_from_underlying')))),
            ('underlying / ATM strike', '%s / %s' % (_v(option['underlying']), _v(option['atm_strike']))),
            ('queried leg: ltp / IV', '%s / %s' % (_v(leg.get('ltp')), _v(leg.get('iv'), 1))),
            ('queried leg: OI / change / volume', '%s / %s / %s' % (
                _v(leg.get('oi')), _v(leg.get('chg_oi')), _v(leg.get('volume')))),
            ('other side: ltp / IV / OI', '%s / %s / %s' % (
                _v(other.get('ltp')), _v(other.get('iv'), 1), _v(other.get('oi')))),
            ('lot size / tick', '%s / Rs %s' % (_v(contract.get('lot_size')), _v(contract.get('tick_inr')))),
            ('one lot costs', 'Rs %s premium on Rs %s notional; Rs %s per tick' % (
                _v(option.get('cash_for_one_lot')), _v(option.get('notional_for_one_lot')),
                _v(option.get('tick_value_one_lot')))),
            ('chain breadth', '%s of %s strikes priced' % (_v(option['priced_strikes']),
                                                           _v(option['strikes']))),
            ('PCR by OI', _v(option['pcr_oi'])),
            ('OI walls (call / put)', '%s / %s' % (_v(option['resistance_oi']), _v(option['support_oi']))),
            ('OI added today (call / put)', '%s / %s' % (_v(option['call_oi_change']),
                                                          _v(option['put_oi_change']))),
            ('expiries listed', ', '.join(option.get('expiry_dates') or []) or 'n/a'),
        ])
        bias = option.get('bias') or {}
        if bias.get('reasons'):
            out += ['', 'Bias read from the chain (%s on the underlying, score %s):' % (
                _v(bias.get('verdict')), _v(bias.get('score')))]
            out += ['- %s' % reason for reason in bias['reasons']]
        for veto in (bias.get('vetoes') or [])[:3]:
            out += ['- veto: %s' % veto]
        if option.get('contract_note'):
            out += ['_', option['contract_note'], '_']
        out += ['']
    elif chain:
        out += _table([
            ('chain', '%s %s | NSE chain type %s' % (
                chain['symbol'], chain['expiry'], _v(chain.get('segment')))),
            ('underlying / ATM', '%s / %s' % (_v(chain['underlying']), _v(chain['atm_strike']))),
            ('PCR by OI', _v(chain['pcr_oi'])),
            ('OI walls (call / put)', '%s / %s' % (_v(chain['resistance_oi']), _v(chain['support_oi']))),
            ('expiries listed', ', '.join(chain.get('expiry_dates') or []) or 'n/a'),
        ])
        out += ['', '| strike | CE ltp | CE IV | CE OI | PE ltp | PE IV | PE OI |',
                '| --- | --- | --- | --- | --- | --- | --- |']
        for row in (chain.get('near_atm') or [])[:9]:
            call, put = row['CE'], row['PE']
            out.append('| %s%s | %s | %s | %s | %s | %s | %s |' % (
                _v(row['strike']), ' (ATM)' if row['strike'] == chain['atm_strike'] else '',
                _v(call['ltp'], 2), _v(call['iv'], 1), _v(call['oi']),
                _v(put['ltp'], 2), _v(put['iv'], 1), _v(put['oi'])))
        out += ['']
    else:
        out += ['_%s_' % _v(doc['absent'].get('option') or doc['absent'].get('chain')), '']

    filings = doc['sections'].get('announcements')
    out += ['', '## What the issuer filed with the exchange']
    if filings:
        out += ['_%s -- last %s days, %d disclosures, fetched %s%s:_' % (
            filings['source'], _v(filings['days']), filings['count_in_window'],
            str(filings['fetched_at'])[:16], ' (cache)' if filings.get('cache_hit') else '')]
        out += _table([
            (row['published_display'], '%s -- [%s](%s) -- %s' % (
                row['category'].replace('|', '/'), row['attachment_size'] or 'filing',
                row['url'], row['text'].replace('|', '/')[:170]))
            for row in filings['items']]) + ['']
        out += ['_%s_' % filings['note'], '']
    else:
        out += ['_%s_' % _v(doc['absent'].get('announcements')
                            or 'no exchange-disclosure read in this detail document'), '']

    filed = doc['sections'].get('financials')
    out += ['', '## The numbers the issuer filed with the exchange']
    if filed:
        latest = filed['latest']
        prov = latest['filing']
        out += ['_%s -- %s for the period ended **%s**, filed %s (%s, %s) by the issuer on the '
                'exchange archive. Fetched %s%s:_' % (
                    filed['source'], _v(prov.get('scope') or filed['scope']),
                    _v(prov.get('period_end_display')), _v(prov.get('published_display')),
                    _v(prov.get('filing_kind')), _v(prov.get('audited')),
                    str(_v(filed.get('fetched_at')))[:16],
                    ' (cache)' if filed.get('cache_hit') else '')]
        out += ['_[downloaded instance](%s) -- host `%s`, %s figures read._' % (
            prov['url'], prov['host'], _v(latest.get('fact_count')))]
        out += ['']
        for block in latest['blocks']:
            out += ['**%s**' % block['title']]
            out += ['| figure | as filed | value as filed |', '| --- | --- | --- |']
            for row in block['figures']:
                out += ['| %s | %s | %s %s |' % (
                    row['label'], row['display'], _v(row.get('value_as_filed')),
                    _v(row.get('unit')))]
            out += ['']
        out += ['_%s_' % filed['note'], '']
    else:
        out += ['_%s_' % _v(doc['absent'].get('financials')
                            or 'no regulation-33 filing read in this detail document'), '']

    out += ['', '## Sizing under the desk limits']
    sizing = doc.get('sizing')
    if sizing:
        out += _table([
            ('stop rule used', sizing['stop_rule']),
            ('risk cap', 'Rs %s' % _v(sizing['risk_cap_inr'])),
            ('cash on file', 'Rs %s' % _v(sizing['cash_inr'])),
            ('shares by risk cap / by cash', '%s / %s' % (_v(sizing['shares_by_risk_cap']),
                                                           _v(sizing['shares_by_cash']))),
            ('position that fits', '%s shares, Rs %s' % (_v(sizing['shares']),
                                                          _v(sizing['position_value_inr']))),
            ('loss if the ATR stop is hit', 'Rs %s' % _v(sizing['risk_at_stop_inr'])),
            ('applies to', sizing.get('applies_to') or 'this symbol'),
        ])
        out += ['', '_%s_' % sizing['note']]
    else:
        out += ['_%s_' % _v(doc.get('sizing_note') or 'no sizing: the daily indicators are missing')]
    out += ['']

    out += ['', '## The desk\'s own files on this symbol']
    for label, section in doc['saved'].items():
        if not section['found']:
            out += ['- **%s**: %s' % (label, section['reason'])]
            continue
        payload = section['payload']
        if label == 'signal board':
            out += ['- **signal board** (%s, %ss old%s): %s, score %s, %d setups matched' % (
                payload.get('symbol'), _v(section['age_seconds']),
                ', STALE' if section['stale'] else '', payload['verdict'], payload['score'],
                len(payload.get('setups') or []))]
        elif label == 'daily scan':
            out += ['- **daily scan** (%s): setups %s | price %s | rsi %s | ma20 %s | high20 %s' % (
                _v(section['data_as_of'])[:16],
                ', '.join(payload.get('setups') or []) or 'none', _v(payload.get('price')),
                _v(payload.get('rsi'), 1), _v(payload.get('ma20')), _v(payload.get('high20')))]
        elif label == 'news':
            out += ['- **news** (%s):' % _v(section['data_as_of'])[:16]]
            for item in (payload.get('items') or [])[:6]:
                out.append('  - %s -- %s (%s)' % (
                    _v(item.get('published_at'))[:16],
                    str(item.get('title') or item.get('headline') or '')[:110],
                    _v(item.get('publisher'))))
            out.append('  - _%s_' % payload.get('note'))
        elif label == 'paper ledger':
            positions = payload.get('position') or []
            out += ['- **paper ledger**: %s | cash Rs %s | realized Rs %s' % (
                '; '.join('%s x%s at stop %s target %s' % (p.get('symbol'), p.get('shares'),
                                                           p.get('stop'), p.get('target'))
                          for p in positions) or 'no open position',
                _v(payload.get('cash')), _v(payload.get('realized_pnl')))]
            for alert in (payload.get('alerts') or [])[:4]:
                out.append('  - alert %s %s @ %s (%s)' % (_v(alert.get('type')), _v(alert.get('symbol')),
                                                           _v(alert.get('price')), _v(alert.get('note'))))
        elif label == 'risk envelope':
            loss = payload.get('loss_at_risk') or {}
            out += ['- **risk envelope**: room Rs %s | buy room Rs %s | trades left %s | '
                    'loss used Rs %s of Rs %s | last decision %s' % (
                        _v(payload.get('risk_room_inr')), _v(payload.get('buy_room_inr')),
                        _v(payload.get('trades_left')), _v(loss.get('used')), _v(loss.get('limit')),
                        _v(payload.get('last_risk_decision')))]
    out.append('')

    out += ['', '## Specialist-team rooms for this symbol']
    if not doc['team']:
        out += ['_None sealed yet. From the signal board row, "Seal a 10-specialist evidence '
                'session" writes one._']
    for room in doc['team']:
        out += ['- `%s` -- status **%s**, %s reviews recorded, %s returned UNAVAILABLE, '
                'paper ticket %s, live orders %s (%s)' % (
                    room['session'], _v(room['status']), room['reviews'],
                    _v(room['unavailable_reviews']),
                    'none' if not room['paper_ticket'] else 'raised',
                    _v(room['live_orders']), _v(room['as_of'])[:16])]
        for veto in room['vetoes'][:3]:
            out.append('  - veto: %s' % (veto if isinstance(veto, str) else json.dumps(veto)[:160]))
    out.append('')

    out += ['', '## Sources and limits']
    out += ['- market data: %s' % doc['source']]
    if doc.get('directory_cached'):
        out.append('- instrument directory (lot and tick) cached locally on %s; ask the desk to '
                    'refresh it if any tick looks stale' % doc['directory_cached'])
    for note in doc.get('notes') or []:
        out.append('- %s' % note)
    if doc.get('absent'):
        for label, why in doc['absent'].items():
            out.append('- unavailable -- %s: %s' % (label, why))
    return '\n'.join(out)


def symbol_html(doc):
    return _render(symbol_markdown(doc), '%s desk detail %s' % (doc['query'], doc['date']))


def write_symbol(doc, workspace=WORKSPACE, html_too=True):
    import detail
    REPORTS.mkdir(parents=True, exist_ok=True)
    stem = 'symbol-%s-%s' % (detail.slug(doc['query']), doc['date'])
    path = REPORTS / ('%s.md' % stem)
    path.write_text(symbol_markdown(doc), encoding='utf-8')
    made = [path]
    if html_too:
        other = REPORTS / ('%s.html' % stem)
        other.write_text(symbol_html(doc), encoding='utf-8')
        made.append(other)
    return made


def write(sections, workspace=WORKSPACE, html_too=True):
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / ('report-%s.md' % sections['date'])
    path.write_text(markdown(sections), encoding='utf-8')
    made = [path]
    if html_too:
        other = REPORTS / ('report-%s.html' % sections['date'])
        other.write_text(html_page(sections), encoding='utf-8')
        made.append(other)
    return made


def print_report(sections):
    print(markdown(sections))


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    data = build()
    print_report(data)
    for path in write(data):
        print('\nwrote %s' % path)
