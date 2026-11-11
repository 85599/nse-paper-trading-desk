"""Specialist team sessions: sealed evidence, real reviewer identities, real votes.

A session is prepared by the engine (``python -m india team``), which seals the
candles and every numerical artifact. Nothing here votes on your behalf: the
verdict, summary and evidence come from a reviewer file that a real agent
produced, and the identity recorded in ``delegations.json`` is the id of the
agent that actually ran. The engine re-derives the numbers at finalize time, so
an optimistic opinion cannot buy a ticket.
"""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor' / 'day-trader-skill-codex'
WORKSPACE = ROOT / 'workspace'
TEAM_ROOT = WORKSPACE / 'team'
MASTER = ROOT / 'config' / 'security-master.csv'
CONFIG = ROOT / 'config' / 'desk.json'
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

ROLES = ('scanner', 'news', 'analyst', 'entry', 'exit', 'timemachine', 'budget',
         'risk', 'broker', 'alerts')
ARTIFACTS = {'scanner': 'watchlist.json', 'news': 'news.json', 'entry': 'entry.json',
             'exit': 'plan.json', 'timemachine': 'timemachine.json', 'budget': 'orders.json',
             'risk': 'state/risk.json', 'broker': 'state/broker.json', 'alerts': 'alerts.json',
             'analyst': 'metrics.json', 'pelosi': 'disclosures.json'}
# What each reviewer is actually asked to judge, in one line the UI can show.
CHARGE = {
    'scanner': 'Are the candles complete, is the detected setup real, and is the '
               'tick verified for this symbol? Do not accept an exchange-wide claim.',
    'news': 'Read dated market/issuer evidence yourself. PASS or WARN only with an '
            'https nseindia.com / bseindia.com / issuer source you actually opened '
            'before the cutoff; otherwise UNAVAILABLE.',
    'analyst': 'Do the numbers in the sealed artifact support the claim being made? The filed '
               'figures are readable from the issuer\'s own regulation-33 XBRL instance that NSE '
               'hosts on nsearchives.nseindia.com, with the exchange\'s own filing time; cite that '
               'and its access time, or the verdict is UNAVAILABLE.',
    'entry': 'Is the trigger a completed-bar event with a valid signal time, or is it '
             'a forming/stale candle being anticipated?',
    'exit': 'Are the stop and target tick-rounded, structurally placed, and is the '
            'reward/risk geometry honest after costs?',
    'timemachine': 'Does the seeded resampling and prior-session replay support the '
                    'setup, or is the history/scenario evidence insufficient?',
    'budget': 'Recompute the cash, position, order, daily-loss and open-risk room from '
              'the sealed portfolio. A BLOCK here is a real limit, not an opinion.',
    'risk': 'Independently re-check the exact order levels, quantity and cost-inclusive '
            'loss against the desk limits. You may BLOCK; you may not authorize execution.',
    'broker': 'Is the draft ticket internally consistent and clearly unsubmitted? Confirm '
              'execution_enabled is false and that any edit invalidates it.',
    'alerts': 'Are the stop/target/flat-by alert definitions bound to the exact order and '
              'free of invented monitoring?',
}


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + '\n',
                    encoding='utf-8')


def _run(argv, timeout=600):
    done = subprocess.run([sys.executable] + [str(v) for v in argv], cwd=str(VENDOR),
                          capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout)
    text = (done.stdout or '').strip()
    if not text:
        return done.returncode, {'status': 'UNAVAILABLE',
                                 'error': (done.stderr or 'no output').strip()[-600:]}
    try:
        return done.returncode, json.loads(text) if text else None
    except ValueError:
        start = next((i for i, line in enumerate(text.splitlines()) if line.startswith('{')), None)
        if start is None:
            return done.returncode, {'status': 'UNAVAILABLE',
                                     'error': (done.stderr or text)[-600:]}
        try:
            return done.returncode, json.loads('\n'.join(text.splitlines()[start:]))
        except ValueError:
            return done.returncode, {'status': 'UNAVAILABLE', 'error': text[-600:]}


def _now():
    return dt.datetime.now(IST)


def portfolio_snapshot(as_of=None):
    """Team-format paper portfolio straight from the local ledger.

    The engine refuses a stale snapshot, so this is written at dispatch time and
    the positions are marked from the saved price files rather than assumed.
    """
    as_of = as_of or _now()
    rc, doc = _run(['trader-broker/ticket.py', '--workspace', WORKSPACE, '--config', CONFIG,
                    '--json', 'portfolio'], timeout=180)
    book = doc if rc == 0 and isinstance(doc, dict) else {}
    prices = read_json(WORKSPACE / 'prices.json')
    marks = {}
    for symbol, row in (prices.get('quotes') or {}).items():
        value = row.get('close') or row.get('ltp') if isinstance(row, dict) else row
        if isinstance(value, (int, float)) and value > 0:
            marks[symbol.upper()] = float(value)
    positions = []
    for row in book.get('positions') or []:
        symbol = str(row.get('symbol') or row.get('ticker') or '').upper()
        qty = row.get('shares') or row.get('qty') or 0
        cost = float(row.get('average_cost') or row.get('avg') or 0)
        stop = float(row.get('stop') or 0)
        mark = marks.get(symbol, cost)
        if not symbol or qty <= 0 or mark <= 0:
            continue
        if stop <= 0 or stop >= mark:
            # An untriggered stop below mark is an engine requirement; report it
            # instead of inventing a level that would pass the check.
            print('  note: %s has no stop below its mark (%s); it is carried as '
                  'closed risk only' % (symbol, stop or 'missing'))
            continue
        positions.append({'symbol': symbol, 'qty': int(qty), 'mark': round(mark, 2),
                          'stop': round(stop, 2)})
    cash = float(book.get('cash') or 0)
    equity = cash + sum(p['qty'] * p['mark'] for p in positions)
    rc, log = _run(['trader-broker/ticket.py', '--workspace', WORKSPACE, '--config', CONFIG,
                    '--json', 'log'], timeout=180)
    events = (log or {}).get('orders', []) if isinstance(log, dict) else []
    today = as_of.date().isoformat()
    todays = [e for e in events if str(e.get('at') or e.get('time') or '')[:10] == today]
    realized = sum(float(e.get('profit') or 0) for e in todays)
    trades = len({str(e.get('fingerprint') or e.get('symbol') or i)
                  for i, e in enumerate(todays) if e.get('status') == 'filled'})
    return {'as_of': as_of.isoformat(), 'cash': cash, 'equity': equity,
            'positions': positions, 'realized_pnl_today': round(realized, 2),
            'trades_today': trades, 'orders_today': len(todays)}


def team_limits():
    cfg = read_json(CONFIG)
    return {'cash_buffer_fraction': float(cfg.get('cash_buffer_pct', 10)) / 100,
            'max_position_fraction': float(cfg.get('max_position_pct', 75)) / 100,
            'max_order_value': float(cfg.get('max_order_inr', 100000)),
            'max_total_risk': float(cfg.get('max_daily_loss_inr', 1500)),
            'max_daily_loss': float(cfg.get('max_daily_loss_inr', 1500)),
            'max_trades_per_day': int(cfg.get('max_trades_per_day', 3)),
            'max_orders_per_day': int(cfg.get('max_orders_per_day', 10)),
            'min_net_rr': float(cfg.get('min_reward_risk', 1))}


def _team_argv(symbol, tick, days, cfg, inputs, session, as_of, simulations):
    argv = ['-m', 'india', 'team', symbol, '--provider', 'nse', '--tick', tick,
            '--days', days, '--capital', cfg.get('budget', 100000),
            '--risk', cfg.get('max_loss_per_trade_inr', 500),
            '--rr', 2, '--simulations', simulations,
            '--security-master', MASTER, '--portfolio', inputs / 'portfolio.json',
            '--team-limits', inputs / 'limits.json', '--team-dir', session]
    if as_of:
        argv += ['--as-of', as_of.isoformat()]
    return argv


def prepare(symbol, tick=None, days=60, simulations=2000, as_of=None):
    """Seal one team session for a symbol under workspace/team/.

    `as_of` seals a labelled replay: the engine reads the exchange's 5-minute bars
    up to that instant and writes `historical_replay: true` into the session
    provenance, so a room whose first-fill trigger has already printed can still be
    reviewed by all ten specialists without anyone pretending it is live.
    """
    import registry
    symbol = symbol.upper()
    if '.' not in symbol:
        symbol += '.NS'
    if not tick:
        tick = registry.ensure(symbol)
    cfg = read_json(CONFIG)
    now = as_of or _now()
    stamp = now.strftime('%Y%m%d-%H%M%S')
    session = TEAM_ROOT / ('%s-%s' % (symbol.split('.')[0].lower(), stamp))
    # The engine refuses a directory that already has anything in it, so the
    # portfolio and limit inputs the session consumes live outside it.
    inputs = WORKSPACE / 'state' / 'team-inputs' / session.name
    inputs.mkdir(parents=True, exist_ok=True)
    snapshot = portfolio_snapshot(now)
    _write_json(inputs / 'portfolio.json', snapshot)
    _write_json(inputs / 'limits.json', team_limits())
    print('== team session %s ==' % session.name)
    print('symbol     : %s (tick %s, %d days, %s scenario paths)'
          % (symbol, tick, days, f'{simulations:,}'))
    if as_of:
        print('as of      : %s -- REPLAY room. The engine seals only the bars that had '
              'already completed by that instant and marks historical_replay=true in the '
              'session provenance.' % as_of.isoformat())
    print('portfolio  : cash ₹%s, %d open position(s), ₹%s realized today'
          % (f"{snapshot['cash']:,.0f}", len(snapshot['positions']),
             f"{snapshot['realized_pnl_today']:,.0f}"))
    argv = _team_argv(symbol, tick, days, cfg, inputs, session, as_of, simulations)
    rc, doc = _run(argv, timeout=900)
    # NSE only serves about a month of 5-minute candles, so a 60-day request can
    # ask for a window the website simply does not have. Shorten it and say so
    # rather than pretending the history exists.
    for shorter in [d for d in (45, 40, 35, 28) if d < days]:
        if not (rc and isinstance(doc, dict) and 'No OHLCV data' in str(doc.get('error', ''))):
            break
        print('NSE has no 5m candles that far back (%d days) -- asking for %d' % (days, shorter))
        days = shorter
        rc, doc = _run(_team_argv(symbol, tick, days, cfg, inputs, session, as_of, simulations),
                       timeout=900)
    if rc or not isinstance(doc, dict) or doc.get('status') not in ('PENDING_AGENT_REVIEWS',):
        print('Engine refused the session -- %s' % json.dumps(doc)[:900])
        return 1
    readiness = doc['preflight']
    print('\npreflight  : %s' % readiness['status'])
    print('           : dispatch specialists = %s' % readiness['should_dispatch_specialists'])
    if readiness['analysis_status'] == 'PAPER_SETUP':
        print('           : the sealed window holds a completed-bar setup, so entry, exit,')
        print('           : budget, risk, broker and alerts each have real numbers to check.')
    elif readiness['status'] != 'READY':
        print('           : NOT_READY here means the sealed window holds no completed-bar setup.')
        print('           : The ten specialists can still review the sealed evidence -- a room')
        print('           : that ends BLOCKED with no ticket is the correct answer, not a failure.')
    print('snapshot   : %s' % doc['snapshot_id'][:16])
    print('analysis   : %s | market_open=%s strategy_open=%s'
          % (readiness['analysis_status'], readiness['market_open'], readiness['strategy_open']))
    for issue in readiness['issues']:
        print('  issue    : %s -- %s' % (issue['code'], issue['action']))
    print('\nSealed artifacts one reviewer each (role | artifact | what it must judge):')
    for role in ROLES:
        print('  %-11s %-20s %s' % (role, ARTIFACTS.get(role, 'plan.json'), CHARGE[role]))
    print('\nNext: ten separate agent identities have to be dispatched and each one records')
    print('      its own verdict for %s. The desk never votes for you.' % session.name)
    print('Reviewers must read the sealed artifact themselves; a review without its own')
    print('evidence is refused by the engine at finalize time.')
    _write_json(session / 'preflight-summary.json',
                {'session': session.name, 'prepared_at': _now().isoformat(),
                 'snapshot_id': doc['snapshot_id'], 'symbol': symbol, 'as_of': doc['as_of'],
                 'historical_replay': bool(as_of),
                 'replay_requested_as_of': as_of.isoformat() if as_of else None,
                 'readiness': readiness})
    return 0


def session_dir(name):
    path = (TEAM_ROOT / name).resolve()
    if path.parent != TEAM_ROOT.resolve() or not (path / 'context.json').is_file():
        raise SystemExit('Unknown team session: %s (see workspace/team/)' % name)
    return path


def _agents(path):
    manifest = read_json(path / 'delegations.json')
    agents = manifest.get('agents')
    if not isinstance(agents, dict):
        raise SystemExit('delegations.json is damaged -- prepare a new session')
    return manifest, agents


def dispatch(name, role, agent_id):
    """Record that this exact agent identity was handed this role."""
    if role not in ROLES:
        raise SystemExit('Unknown role %s -- use one of %s' % (role, ', '.join(ROLES)))
    path = session_dir(name)
    manifest, agents = _agents(path)
    if agents.get(role) and agents[role] != agent_id:
        raise SystemExit('%s is already bound to %s' % (role, agents[role]))
    if agent_id in agents.values():
        raise SystemExit('Identity %s is already used by another role' % agent_id)
    agents[role] = agent_id
    _write_json(path / 'delegations.json', manifest)
    for state in ('dispatched', 'running'):
        rc, _ = _run(['-m', 'dashboard', 'activity', '--session', path, '--role', role,
                      '--agent-id', agent_id, '--state', state], timeout=120)
        if rc:
            print('activity %s refused' % state)
    print('dispatched %s -> %s' % (role, agent_id))
    return 0


def record(name, role, agent_id, review_file):
    """Bind and save one reviewer's own verdict, then log completion."""
    if role not in ROLES:
        raise SystemExit('Unknown role %s' % role)
    path = session_dir(name)
    draft = read_json(Path(review_file))
    if not draft:
        raise SystemExit('Reviewer file missing or unreadable: %s' % review_file)
    for field in ('verdict', 'summary'):
        if not str(draft.get(field) or '').strip():
            raise SystemExit('Reviewer file needs a %s' % field)
    if not isinstance(draft.get('evidence'), list) or not draft['evidence']:
        raise SystemExit('Reviewer file needs a non-empty evidence list -- no evidence, no vote')
    rc, artifact = _run(['-m', 'india.team', 'evaluate', '--session', path, '--role', role],
                        timeout=180)
    if rc or not isinstance(artifact, dict):
        print('Engine refused to open the artifact -- %s' % json.dumps(artifact)[:500])
        return 1
    doc = {'schema_version': 1, 'role': role, 'snapshot_id': artifact['snapshot_id'],
           'as_of': artifact['as_of'], 'artifact_sha256': artifact['artifact_sha256'],
           'agent_id': agent_id, 'verdict': str(draft['verdict']).strip().upper(),
           'summary': str(draft['summary']).strip(),
           'evidence': draft['evidence'], 'execution_enabled': False,
           'reviewed_at': _now().isoformat()}
    if draft.get('sources'):
        doc['sources'] = draft['sources']
    if draft.get('recommendation'):
        doc['recommendation'] = str(draft['recommendation']).strip()
    # What kind of answer this is, so the desk can tell a veto from an empty
    # evidence file instead of printing both as the same badge.
    if draft.get('code'):
        doc['reason_code'] = str(draft['code']).strip().upper()
    if draft.get('kind'):
        doc['reason_kind'] = str(draft['kind']).strip()
    manifest, agents = _agents(path)
    if agents.get(role) != agent_id:
        raise SystemExit('Dispatch %s with this identity before recording its review' % role)
    _write_json(path / 'reviews' / (role + '.json'), doc)
    rc, _ = _run(['-m', 'dashboard', 'activity', '--session', path, '--role', role,
                  '--agent-id', agent_id, '--state', 'completed'], timeout=120)
    if rc:
        print('The engine refused this completion -- its review is invalid, so it stays open.')
        return 1
    print('%s: %s recorded from %s' % (role, doc['verdict'], agent_id))
    return 0


def _kinds(report):
    """Group the recorded reviews by the kind of answer each one says it gave.

    A BLOCK and an UNAVAILABLE used to read identically on screen, which made every
    quiet room look broken. The kind is written by the role that made the call, so
    this groups the reviewers' own claims instead of guessing afterwards; a room
    sealed before the kinds existed falls back to its verdict.
    """
    groups = {}
    for role, review in (report.get('reviews') or {}).items():
        verdict = review.get('verdict')
        kind = str(review.get('reason_kind') or (
            'veto' if verdict == 'BLOCK' else 'confirmed'
            if verdict in ('PASS', 'WARN') else 'evidence-gap'))
        groups.setdefault(kind, []).append(role)
    return groups


def conclusion(report):
    """One paragraph that says what this room actually concluded, and why."""
    groups = _kinds(report)
    vetoes = sorted(groups.get('veto', []))
    waiting = sorted(groups.get('nothing-to-check', []))
    gaps = sorted(groups.get('evidence-gap', []))
    ticket = report.get('paper_ticket')
    if ticket:
        return ('CONCLUSION: the room agrees a draft paper ticket exists (%s shares at %s). '
                'Nothing is booked until you reply yes.'
                % (ticket.get('qty'), ticket.get('entry')))
    parts = []
    if vetoes:
        parts.append('real veto(s) from %s' % ', '.join(vetoes))
    if waiting:
        parts.append('%d role(s) had nothing to certify yet (%s), because an earlier stage in '
                     'this room produced no plan, ticket or order' % (len(waiting),
                                                                      ', '.join(waiting)))
    if gaps:
        parts.append('%d role(s) found no admissible dated evidence to read (%s)'
                     % (len(gaps), ', '.join(gaps)))
    if not parts:
        return 'CONCLUSION: every role answered from its own sealed evidence and none vetoed.'
    return 'CONCLUSION: %s.' % '; '.join(parts)


def rollup(name=None, finalize=True):
    """Print the sealed roll-up; finalize re-derives the numbers itself."""
    paths = [session_dir(name)] if name else sorted(
        (p for p in TEAM_ROOT.glob('*') if (p / 'context.json').is_file()),
        key=lambda p: p.stat().st_mtime, reverse=True) if TEAM_ROOT.is_dir() else []
    if not paths:
        print('No team sessions yet -- search a symbol on the dashboard and press Seal a 10-specialist room for it.')
        return 1
    for path in paths[:1] if name else paths[:3]:
        rc, report = _run(['-m', 'india.team', 'finalize', '--session', path], timeout=300) \
            if finalize else (0, read_json(path / 'report.json'))
        if rc or not isinstance(report, dict) or 'status' not in report:
            print('%s: cannot finalize -- %s' % (path.name, json.dumps(report)[:400]))
            continue
        context = read_json(path / 'context.json')
        print('\n== %s ==' % path.name)
        print('%s | as of %s | %s' % (report['symbol'], str(report['as_of'])[:19],
                                      report['status']))
        print('reviews: %d/%d recorded, %d distinct agent ids'
              % (report['orchestration']['received_reviews'],
                 report['orchestration']['required_agents'],
                 report['orchestration']['distinct_recorded_agent_ids']))
        for role in ROLES:
            review = report['reviews'].get(role)
            print('  %-11s %-11s %s' % (role, review['verdict'] if review else 'missing',
                                         (review['summary'] if review else '')[:110]))
        print(conclusion(report))
        for label, keys in (('missing', report['missing_reviews']),
                            ('invalid', report['invalid_reviews']),
                            ('unavailable', report['unavailable_reviews']),
                            ('engine vetoes', report['engine_vetoes'])):
            if keys:
                print('  %-16s %s' % (label + ':', ', '.join(keys)))
        if report['vetoes']:
            print('  reviewer vetoes: %s' % ', '.join(report['vetoes']))
        ticket = report.get('paper_ticket')
        print('\npaper ticket: %s' % (json.dumps(ticket) if ticket else
                                      'none -- status is ' + report['status']))
        print('live orders submitted: %d | execution_enabled: %s'
              % (report['live_orders_submitted'], report['execution_enabled']))
        if not ticket:
            reasons = (report['budget'] or {}).get('reasons') or []
            if reasons:
                print('budget reasons: %s' % ', '.join(reasons))
        print('dashboard: Observatory / Specialist team / Review evidence read this session.')
    return 0


def report_markdown(name=None):
    """Latest finalized session as text rows the desk report can print."""
    path = session_dir(name) if name else next(iter(sorted(
        (p for p in TEAM_ROOT.glob('*') if (p / 'context.json').is_file()),
        key=lambda p: p.stat().st_mtime, reverse=True)), None) if TEAM_ROOT.is_dir() else None
    if not path:
        return None
    doc = read_json(path / 'report.json')
    if not doc:
        rc, doc = _run(['-m', 'india.team', 'finalize', '--session', path], timeout=300)
    if not isinstance(doc, dict) or not doc.get('status'):
        return None
    return {'session': path.name, 'symbol': doc['symbol'], 'as_of': doc['as_of'],
            'status': doc['status'],
            'reviews': {role: {'verdict': review['verdict'], 'summary': review['summary'],
                               'agent_id': review['agent_id']}
                        for role, review in (doc.get('reviews') or {}).items()},
            'missing': doc['missing_reviews'], 'invalid': doc['invalid_reviews'],
            'vetoes': doc['vetoes'], 'paper_ticket': doc.get('paper_ticket'),
            'execution_enabled': False, 'source': str(path)}


def _newest_room(symbol):
    """The session directory prepare() just wrote for this symbol, by stamp."""
    stem = symbol.split('.')[0].lower()
    rooms = sorted((p for p in TEAM_ROOT.glob(stem + '-*') if (p / 'context.json').is_file()),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return rooms[0].name if rooms else None


def run_specialists(name):
    """Dispatch, check and record every role that has not been reviewed yet.

    A role that already holds a review is left exactly as it is -- overwriting a
    real agent's verdict with a desk checker's would erase who actually looked.
    A role dispatched to some other identity but never recorded cannot be voted
    for by the desk either, so it is reported instead of faked. If a checker
    throws, the role records an honest UNAVAILABLE naming the failure rather than
    leaving the room stuck at 9/10 forever.
    """
    import reviewers
    path = session_dir(name)
    drafts = WORKSPACE / 'state' / 'review-drafts' / name
    drafts.mkdir(parents=True, exist_ok=True)
    manifest, agents = _agents(path)
    print('\n== running the ten specialists on %s ==' % name)
    recorded = skipped = refused = 0
    for role in ROLES:
        bound = agents.get(role)
        if (path / 'reviews' / (role + '.json')).is_file():
            print('%s: already recorded by %s -- left untouched' % (role, bound))
            skipped += 1
            continue
        if bound:
            print('%s: dispatched to %s but never recorded -- the desk will not vote '
                  'under another reviewer identity' % (role, bound))
            refused += 1
            continue
        agent_id = 'desk-checker-%s' % role
        dispatch(name, role, agent_id)
        try:
            draft = reviewers.review(role, path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            draft = {'verdict': 'UNAVAILABLE', 'code': 'ARTIFACT_UNREADABLE',
                     'kind': 'evidence-gap',
                     'summary': 'The %s checker could not read its sealed artifact, so this role '
                                'has no evidence to answer with: %s' % (role, str(exc)[:160]),
                     'evidence': ['artifact=%s' % reviewers.ARTIFACTS[role],
                                  'checker error=%s' % str(exc)[:160]]}
        file = drafts / (role + '.json')
        file.write_text(json.dumps(draft, indent=2, allow_nan=False, default=str) + '\n',
                        encoding='utf-8')
        if record(name, role, agent_id, file):
            refused += 1
            continue
        recorded += 1
    print('\n%d recorded, %d already on file, %d refused (of %d roles).'
          % (recorded, skipped, refused, len(ROLES)))
    return recorded, skipped, refused


def finish_room(name):
    """Put the ten checkers on an ALREADY sealed room and finalize it.

    A seal that nobody followed up leaves a room at 0/10 PENDING in the
    Observatory forever -- that is what a searched stock looked like whenever the
    tab was talking to an older server. This completes the room that is already on
    disk, using its own sealed artifacts, instead of sealing a second one.
    """
    session_dir(name)
    run_specialists(name)
    return rollup(name, finalize=True)


def run_room(symbol, tick=None, days=60, simulations=2000, as_of=None):
    """Seal a room and put all ten specialists to work on it in one command.

    Sealing writes evidence only, which is why a searched stock used to sit at
    0/10 PENDING until someone dispatched the roles by hand. This chains the
    whole pass: seal, bind each role to its own desk-checker identity, run that
    role's deterministic checker against **only** its own sealed artifact, record
    the verdict, then finalize. The desk still never votes for the user -- a
    checker reports what the sealed evidence says, the engine re-derives every
    number again at finalize, and a ticket is booked only after the user's own
    "yes".
    """
    symbol = symbol.upper()
    if '.' not in symbol:
        symbol += '.NS'
    if prepare(symbol, tick=tick, days=days, simulations=simulations, as_of=as_of):
        print('Room could not be sealed for %s -- no reviews were started.' % symbol)
        return 1
    name = _newest_room(symbol)
    if not name:
        print('The sealed room could not be located under workspace/team/.')
        return 1
    run_specialists(name)
    return rollup(name, finalize=True)
