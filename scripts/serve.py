"""Desk-owned dashboard server: the upstream handler plus a few desk routes.

The vendored engine stays untouched. Read routes serve workspace JSON snapshots
written by the matching `python scripts/desk.py` command, and refuse them when
they are older than their own budget. The one write route, POST /api/action, runs
a fixed allowlist of desk commands as subprocesses on loopback only -- it never
builds a shell line, never accepts an arbitrary executable, and never types your
approval reply for you.
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor' / 'day-trader-skill-codex'
sys.path.insert(0, str(VENDOR))
sys.path.insert(0, str(ROOT / 'scripts'))

from dashboard.__main__ import handler  # noqa: E402
from dashboard.journal import paper_journal  # noqa: E402
import lookup  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MAX_AGE_SECONDS = 900
# Daily-bar verdicts age slowly and a whole-universe pass costs minutes, so the
# signal board gets a wider budget -- but it must still be from this session.
ROUTES = {
    '/api/envelope': dict(filename='envelope.json', label='risk envelope', command='envelope',
                          max_age=MAX_AGE_SECONDS),
    '/api/options': dict(filename='options.json', label='option chain', command='options',
                         max_age=MAX_AGE_SECONDS),
    '/api/signals': dict(filename='signals.json', label='signal board', command='signals',
                         max_age=7200, same_day=True, button='Recalculate the signal board'),
    # Headlines are a daily sweep, so the budget is a working day, not 15 minutes.
    '/api/news': dict(filename='news.json', label='headline feed', command='news', max_age=21600,
                      button='Collect dated headlines'),
}
SYMBOL_RE = re.compile(r'^[A-Z0-9&._-]{1,24}$')
# A strike query is one argument with spaces in it: "NIFTY 24000 CE". It still goes to
# argv as a single element, never through a shell.
QUERY_RE = re.compile(r'^[A-Z0-9&._ -]{1,40}$')
# A team session is a directory stamp the desk itself wrote (m&m-20261105-104100).
# Only a bare name is admitted -- no separator, no dot-dot -- and teamflow resolves
# it under workspace/team/ and refuses anything that is not a sealed room.
SESSION_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._&-]{0,63}$')
REPLY_RE = re.compile(r'^[A-Za-z0-9 ,.;:\'"_-]{1,160}$')
DESK = str(ROOT / 'scripts' / 'desk.py')
MAX_LOG = 200

# name -> (argv template, timeout, needs symbol, needs reply)
COMMANDS = {
    'signals': (['signals', '--limit', '{limit}'], 1500, False, False),
    # The desk's own market stage is allowed 1200s, so the wrapper budget has to sit above
    # it. An outer timeout shorter than the inner one only guarantees that the server gives
    # up while the engine is still fetching, and the message then says the desk was left
    # running -- which is true and worse than waiting.
    'scan daily': (['scan', 'daily'], 1500, False, False),
    'scan orb': (['scan', 'orb'], 1500, False, False),
    # Which stocks NSE itself lists inside a Nifty index, and a daily scan of exactly
    # those stocks. The list is membership only -- NSE publishes no price in it.
    'universe': (['universe', '{index}'], 180, False, False),
    'scan index': (['scan', 'daily', '--index', '{index}'], 1500, False, False),
    'index': (['index'], 300, False, False),
    'envelope': (['envelope'], 180, False, False),
    'journal': (['journal'], 180, False, False),
    'news': (['news'], 600, False, False),
    'report': (['report'], 240, False, False),
    'status': (['status'], 240, False, False),
    'options': (['options', '{symbol}'], 240, True, False),
    # Live per-symbol read from NSE (bars, indicators, chain, lot and tick), plus the
    # written one-symbol report. Both only read the market; neither places an order.
    'detail': (['detail', '{symbol}', '--refresh'], 240, True, False),
    'symbol report': (['report', '--symbol', '{symbol}'], 300, True, False),
    'setup': (['setup', '{symbol}'], 420, True, False),
    # The risk stage only accepts a signal inside one bar step of its close, so the
    # pass that has to make the call is the one that caught the bar. This polls on a
    # 20s beat for five minutes -- short enough that the dashboard is never locked
    # for a whole session, long enough to cover a bar close and NSE's lag.
    'watch': (['watch', '{symbol}', '--every', '20', '--max-checks', '15'], 480, True, False),
    # Sealing a team session writes immutable evidence only. It starts no agent
    # and clears no trade: the reviews still have to be recorded by real agents.
    'team': (['team', '{symbol}'], 1200, True, False),
    # One pass that seals the room and puts every role's own deterministic checker
    # against its own sealed artifact, so a searched stock stops sitting at 0/10
    # PENDING. It records verdicts the engine re-derives at finalize; it never
    # books a ticket -- that still needs your own "yes".
    'team review': (['team-review', '{symbol}'], 1500, True, False),
    'team report': (['team-report'], 300, False, False),
    # The fix for a room that was sealed but never reviewed: it puts the ten
    # checkers on the artifacts that are ALREADY on disk instead of sealing a
    # second room, so an Observatory stuck at 0/10 has one honest way forward.
    'team finish': (['team-finish', '{session}'], 600, False, False),
    'ticket': (['ticket'], 180, False, False),
    'risk': (['risk'], 300, False, False),
    'flatten': (['flatten', '{symbol}'], 300, True, False),
    'approve': (['approve', '--reply', '{reply}'], 180, False, True),
    'decline': (['decline', '--reply', '{reply}'], 180, False, True),
}
BUSY = threading.Lock()
LIMIT_DEFAULT = 30


def _risk_cap():
    """The per-trade loss limit the engine itself enforces, read once at import."""
    try:
        cfg = json.loads((ROOT / 'config' / 'desk.json').read_text(encoding='utf-8'))
        return float(cfg.get('max_loss_per_trade_inr') or 0)
    except (OSError, ValueError, TypeError):
        return 0.0


RISK_CAP = _risk_cap()
# Only a run that builds a ticket can be re-budgeted, and only downwards: costs are
# added after sizing, so a slightly smaller risk budget is how a cost-inclusive
# block clears. It is not a way to trade bigger than the cap you set.
RISKABLE = frozenset(('setup', 'watch'))


class Stale(ValueError):
    """A snapshot the desk will not show as current, plus the one click that fixes it."""

    def __init__(self, message, fix):
        super().__init__(message)
        self.fix = fix


def saved_state(workspace, filename, label, command, max_age=MAX_AGE_SECONDS, same_day=False,
                button=None):
    """A workspace JSON snapshot, refused when it is too old or not from today.

    A refusal names the button that fixes it, never a shell line: the dashboard is
    the only place a client can act on it, and every command here is already on the
    frozen allowlist.
    """
    fix = {'command': command, 'label': button or ('Refresh the %s' % label)}
    path = Path(workspace) / filename
    if not path.is_file():
        raise Stale('No %s on file yet.' % label, fix)
    doc = json.loads(path.read_text(encoding='utf-8'))
    try:
        written = dt.datetime.fromisoformat(str(doc.get('as_of')))
    except (TypeError, ValueError):
        raise Stale('%s has an unreadable timestamp.' % _cap(label), fix) from None
    if written.tzinfo is None:
        written = written.replace(tzinfo=IST)
    now = dt.datetime.now(IST)
    age = (now - written).total_seconds()
    if age < 0:
        raise Stale('%s is future-dated.' % _cap(label), fix)
    if age > max_age:
        raise Stale('%s is %s old, so the desk will not show it as current.' % (
            _cap(label), _human(age)), fix)
    if same_day and written.date() != now.date():
        raise Stale('%s is from %s, not this session.' % (
            _cap(label), written.date().isoformat()), fix)
    doc['age_seconds'] = int(age)
    return doc


def _human(seconds):
    return '%d min' % round(seconds / 60) if seconds < 5400 else '%.1f h' % (seconds / 3600)


def _cap(text):
    return text[0].upper() + text[1:]


def _load(workspace, name):
    try:
        return json.loads((Path(workspace) / name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


ATTEMPTS = 'state/ticket-attempts.json'
MAX_ATTEMPTS = 200


def _ticket_row(row, checked_at, command):
    """One risk engine verdict, in the flat shape the journal renders."""
    return {
        'symbol': row.get('symbol'), 'side': row.get('side'), 'qty': row.get('amount'),
        'entry': row.get('entry_ref'), 'entry_from': row.get('entry_ref_from'),
        'stop': row.get('stop'), 'target': row.get('target'),
        'risk_per_share': row.get('risk_per_share'), 'max_loss': row.get('max_loss'),
        'reward_risk': row.get('reward_risk'),
        'net_reward_risk': row.get('net_reward_risk'), 'value_inr': row.get('value_inr'),
        'verdict': row.get('verdict'), 'reasons': row.get('reasons') or [],
        'warnings': row.get('warnings') or [], 'info': row.get('info') or [],
        'tick_status': (row.get('tick_verification') or {}).get('status'),
        'fingerprint': row.get('fingerprint'), 'checked_at': checked_at,
        'command': command, 'stage': 'risk',
    }


def _argv_symbol(event):
    """The symbol a desk run was pointed at, from the argv the action log kept."""
    argv = [str(part) for part in event.get('argv') or []]
    return argv[1] if len(argv) > 1 and not argv[1].startswith('--') else None


REASON_RE = re.compile(r'^\s*reason: (.+)$', re.MULTILINE)
SAVED_ORDER_RE = re.compile(r'\b[A-Z0-9&._-]+\.(?:NS|BO)\b')


def _run_row(event):
    """A chain run that stopped before the risk engine, in the engine's own words.

    A setup that finds no triggered entry writes no risk verdict, so it would
    vanish from the journal and read as a broken panel. This keeps the run with the
    reason the entry stage printed, which is the answer the desk actually gave.
    """
    output = str(event.get('output') or '')
    reasons = REASON_RE.findall(output)
    if 'No triggered entry' in output:
        verdict, note = 'NO ENTRY', 'Nothing was planned, sized, risk-checked or ticketed.'
    elif 'no eligible' in output.lower() or 'not eligible' in output.lower():
        verdict, note = 'NO SETUP', 'The desk found no setup to size on this bar.'
    else:
        verdict, note = 'NOT SIZED', 'The chain stopped before an order was sized.'
    found = re.search(r'\| ([A-Z0-9&._-]+) \|', output)
    symbol = _argv_symbol(event) or (found.group(1) if found else None)
    range_line = next((line.strip() for line in output.splitlines()
                       if line.strip().startswith('opening range')), '')
    return {
        'symbol': symbol, 'verdict': verdict, 'reasons': reasons[:3], 'note': note,
        'warnings': [], 'info': [range_line] if range_line else [],
        'checked_at': event.get('time'), 'command': event.get('command'), 'stage': 'entry',
        'qty': None, 'side': None, 'entry': None, 'entry_from': None, 'stop': None,
        'target': None, 'max_loss': None, 'value_inr': None, 'reward_risk': None,
        'net_reward_risk': None, 'risk_per_share': None, 'tick_status': None,
        'fingerprint': None,
    }


def _key(attempt):
    return '%s|%s|%s' % (attempt.get('checked_at'), attempt.get('symbol'),
                         attempt.get('fingerprint'))


def _read_attempts(workspace):
    path = Path(workspace) / ATTEMPTS
    try:
        attempts = json.loads(path.read_text(encoding='utf-8')).get('attempts') or []
    except (OSError, ValueError, AttributeError):
        return []
    for attempt in attempts:
        # Rows logged before the entry-stage split can only be risk verdicts.
        if isinstance(attempt, dict):
            attempt.setdefault('stage', 'risk' if attempt.get('fingerprint') else 'entry')
    return [a for a in attempts if isinstance(a, dict)]


def _moment(text):
    try:
        stamp = dt.datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return stamp.replace(tzinfo=IST) if stamp.tzinfo is None else stamp


def record_attempts(workspace, event=None):
    """Keep every ticket attempt the desk makes, not just the newest one.

    state/risk.json is overwritten by the engine on every check, so sizing a
    second symbol erased the first one's verdict and left the journal stuck on
    whichever stock was touched last. This appends each run instead, keyed by its
    timestamp and order fingerprint so the same check is never counted twice.
    """
    risk = _load(workspace, 'state/risk.json')
    checked_at = risk.get('created_at') or risk.get('as_of')
    created = _moment(checked_at)
    finished, seconds = _moment((event or {}).get('time')), float((event or {}).get('seconds') or 0)
    # A risk file written during this run belongs to it; an older one is another
    # symbol's verdict and must not be credited to the run that just finished.
    fresh = created is not None and (finished is None or created >= finished
                                     - dt.timedelta(seconds=seconds + 5))
    rows = [_ticket_row(row, checked_at, (event or {}).get('command'))
            for row in (risk.get('results') or [] if fresh else [])]
    if event is not None and not rows and event.get('command') in ('setup', 'watch') \
            and not event.get('returncode'):
        rows = [_run_row(event)]
    if not rows:
        return
    attempts = _read_attempts(workspace)
    seen = {_key(a) for a in attempts}
    added = False
    for attempt in rows:
        if _key(attempt) not in seen:
            seen.add(_key(attempt))
            attempts.append(attempt)
            added = True
    if not added:
        return
    path = Path(workspace) / ATTEMPTS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'attempts': attempts[-MAX_ATTEMPTS:],
                                'updated_at': dt.datetime.now(IST).isoformat()},
                               default=str), encoding='utf-8')


def ticket_queue(workspace):
    """Every ticket the desk actually tried, newest first, and why each one is where
    it is.

    An empty ledger is a fact, not a bug -- so the journal shows the attempts that
    never became fills, kept across symbols by the append-only log above rather
    than read from the single check the engine leaves on file.
    """
    record_attempts(workspace)
    attempts = _read_attempts(workspace)
    attempts.sort(key=lambda a: str(a.get('checked_at') or ''), reverse=True)
    today = dt.datetime.now(IST).date().isoformat()
    todays = [a for a in attempts if str(a.get('checked_at') or '').startswith(today)]
    try:
        lines = (Path(workspace) / 'state' / 'actions.jsonl').read_text(
            encoding='utf-8').splitlines()[-200:]
    except OSError:
        lines = []
    decisions = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        command = event.get('command')
        if command not in ('approve', 'decline', 'ticket', 'flatten'):
            continue
        output = str(event.get('output') or '')
        # desk.py puts the symbol in argv[1]; approve, decline and ticket work off the
        # saved order instead, so the symbol has to come from what the engine printed.
        symbol = _argv_symbol(event) or next(iter(SAVED_ORDER_RE.findall(output)), None)
        decisions.append({
            'time': event.get('time'), 'command': command, 'symbol': symbol,
            'returncode': event.get('returncode'), 'seconds': event.get('seconds'),
            'outcome': ('READY, waiting for your yes' if 'READY:' in output or
                        'waiting for your yes' in output else
                        'BLOCKED' if 'BLOCK' in output else
                        'nothing to approve' if 'no ticket' in output.lower() else 'run complete'),
        })
    return {
        'tickets': attempts,
        'ticket_counts': {
            'pass': sum(1 for a in attempts if a.get('verdict') == 'PASS'),
            'block': sum(1 for a in attempts if a.get('stage') == 'risk'
                         and a.get('verdict') != 'PASS'),
            'unsized': sum(1 for a in attempts if a.get('stage') != 'risk'),
            'today': len(todays), 'saved': len(attempts),
            'symbols': len({a.get('symbol') for a in attempts}),
        },
        'decisions': decisions[-25:],
    }


def build(root, workspace):
    base = handler(root, workspace)
    workspace = Path(workspace)
    log_path = workspace / 'state' / 'actions.jsonl'

    def logged(event):
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            lines = log_path.read_text(encoding='utf-8').splitlines()[-MAX_LOG:] if log_path.is_file() else []
            lines.append(json.dumps(event, default=str))
            log_path.write_text('\n'.join(lines[-MAX_LOG:]) + '\n', encoding='utf-8')
        except OSError:
            pass

    def run(command, symbol=None, reply=None, limit=LIMIT_DEFAULT, risk=None, session=None,
            index=None):
        argv_template, timeout, wants_symbol, wants_reply = COMMANDS[command]
        argv = [sys.executable, DESK]
        for part in argv_template:
            if part == '{symbol}':
                wanted = str(symbol or '').upper()
                spaced = command in ('detail', 'symbol report') and QUERY_RE.fullmatch(wanted)
                if not (SYMBOL_RE.fullmatch(wanted) or spaced):
                    raise ValueError('A symbol like RELIANCE, NIFTY 50 or NIFTY 24000 CE is required')
                argv.append(wanted)
            elif part == '{reply}':
                # Your consent text travels verbatim as one argument; the desk never writes it.
                if not reply or not REPLY_RE.fullmatch(reply):
                    raise ValueError('Approval needs your own reply text starting with "yes"')
                argv.append(reply)
            elif part == '{limit}':
                argv.append(str(max(1, min(200, int(limit)))))
            elif part == '{session}':
                wanted = str(session or '').strip()
                if not SESSION_RE.fullmatch(wanted) or Path(wanted).name != wanted:
                    raise ValueError('A sealed session name like ptc-20261109-115153 is required')
                argv.append(wanted)
            elif part == '{index}':
                # A Nifty index name ("NIFTY IT"), one argv element, never a shell string.
                wanted = str(index or '').strip()
                if not QUERY_RE.fullmatch(wanted.upper()):
                    raise ValueError('An index like NIFTY IT or NIFTY MIDCAP 150 is required')
                argv.append(wanted)
            else:
                argv.append(part)
        used_risk = None
        if risk not in (None, ''):
            if command not in RISKABLE:
                raise ValueError('%s does not take a risk budget' % command)
            try:
                value = float(risk)
            except (TypeError, ValueError):
                raise ValueError('Risk budget must be a number of rupees')
            if not 0 < value <= RISK_CAP:
                raise ValueError(
                    'Risk budget has to be between 1 and the cap you set, Rs %s. '
                    'This control only tightens the limit -- the dashboard cannot '
                    'raise it, and the engine would refuse the ticket anyway.'
                    % '{:,.0f}'.format(RISK_CAP))
            used_risk = value
            argv += ['--risk', '%g' % value]
        started = dt.datetime.now(IST)
        done = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=timeout)
        finished = dt.datetime.now(IST)
        event = {'time': finished.isoformat(), 'command': command, 'argv': argv[2:],
                 'returncode': done.returncode, 'seconds': round((finished - started).total_seconds(), 1),
                 'risk_cap': RISK_CAP, 'risk_used': used_risk,
                 'output': (done.stdout or '')[-20000:]}
        if done.stderr and done.returncode:
            event['output'] += '\nstderr: ' + done.stderr[-2000:]
        logged(event)
        # Capture what this run decided before the next run overwrites it. Idempotent,
        # so this is safe after every command, not only the ones that size a ticket.
        record_attempts(workspace, event)
        return event

    class DeskHandler(base):
        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == '/api/limits':
                # The limits the engine actually enforces, read straight from the
                # desk config, so no panel ever asks you to retype numbers that are
                # already fixed on this machine.
                if not self.local_only():
                    return
                cfg = _load(ROOT, 'config/desk.json')
                return self.respond(200, {
                    'capital_inr': cfg.get('budget'),
                    'max_loss_per_trade_inr': cfg.get('max_loss_per_trade_inr'),
                    'max_daily_loss_inr': cfg.get('max_daily_loss_inr'),
                    'max_order_value_inr': cfg.get('max_order_inr'),
                    'max_trades_per_day': cfg.get('max_trades_per_day'),
                    'entry_window': '%s to %s' % (cfg.get('no_entries_before'),
                                                  cfg.get('no_entries_after')),
                    'flat_by': cfg.get('flat_by'),
                    'paper': cfg.get('paper'),
                    'execution_enabled': not cfg.get('paper', True),
                })
            if url.path == '/api/universe':
                # The picker list, or one index's cached constituent file. A GET never
                # asks NSE for anything: fetching is the `universe` command's job, so a
                # missing or stale cache refuses with that command instead of silently
                # reaching the network from a read.
                if not self.local_only():
                    return
                import universe as desk_universe
                wanted = (parse_qs(url.query).get('index') or [''])[0]
                if not wanted:
                    return self.respond(200, {'indexes': [
                        dict(label=label, published=bool(slug)) for label, slug in desk_universe.INDEXES]})
                label, slug = desk_universe.resolve(wanted)
                if label is None:
                    return self.respond(400, dict(error='No Nifty index matches "%s".' % wanted))
                if not slug:
                    return self.respond(200, dict(status='NO_FILE', index=label, note=desk_universe.NO_FILE))
                doc = desk_universe.cached(label)
                if not doc:
                    return self.respond(409, dict(
                        error='%s has no constituent list on this machine yet.' % label,
                        fix=dict(command='universe', label='Find the stocks in ' + label)))
                age = desk_universe.age_seconds(doc)
                if age and age > desk_universe.MAX_AGE_SECONDS:
                    return self.respond(409, dict(
                        error='%s is %s old, so the desk will not show it as current.'
                              % (label, _human(age)),
                        fix=dict(command='universe', label='Refresh ' + label), doc=doc))
                return self.respond(200, dict(doc, age=age))
            if url.path == '/api/journal':
                # The vendor ledger view plus the desk's own ticket history.
                if not self.local_only():
                    return
                try:
                    doc = paper_journal(workspace)
                except (ValueError, OSError, TypeError, KeyError):
                    doc = {'status': 'UNREADABLE',
                           'note': 'The local paper ledger could not be read.'}
                try:
                    # Either half can fail on its own; a damaged ticket history must not
                    # blank out a ledger that reads fine.
                    doc.update(ticket_queue(workspace))
                except (ValueError, OSError, TypeError, KeyError):
                    doc['tickets_note'] = ('The ticket history on disk could not be read, so no '
                                           'attempt is listed. The ledger above is unaffected.')
                return self.respond(200, doc)
            if url.path == '/api/lookup':
                if not self.local_only():
                    return
                params = parse_qs(url.query)
                query = params.get('q', [''])[0]
                flags = {key: params.get(key, [default])[0] in ('1', 'true', 'yes')
                         for key, default in (('refresh', '0'), ('fetch', '1'))}
                try:
                    return self.respond(200, lookup.run(query, workspace, **flags))
                except (ValueError, OSError, TypeError) as exc:
                    return self.respond(409, dict(error=str(exc)[:300]))
            if url.path == '/api/actions':
                if not self.local_only():
                    return
                try:
                    lines = log_path.read_text(encoding='utf-8').splitlines()[-40:]
                except OSError:
                    lines = []
                events = []
                for line in reversed(lines):
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        continue
                return self.respond(200, dict(events=events, running=BUSY.locked(),
                                              commands=sorted(COMMANDS)))
            route = ROUTES.get(url.path)
            if route:
                if not self.local_only():
                    return
                try:
                    return self.respond(200, saved_state(workspace, route['filename'], route['label'],
                                                         route['command'], max_age=route['max_age'],
                                                         same_day=route.get('same_day', False),
                                                         button=route.get('button')))
                except Stale as exc:
                    return self.respond(409, dict(error=str(exc)[:300], fix=exc.fix))
                except (ValueError, OSError, TypeError) as exc:
                    return self.respond(409, dict(error=str(exc)[:300]))
            return base.do_GET(self)

        def do_POST(self):
            url = urlsplit(self.path)
            if url.path != '/api/action':
                return self.respond(404, dict(error='Unknown action'))
            if not self.local_only():
                return
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                return self.respond(400, dict(error='Bad Content-Length'))
            if length > 4096:
                return self.respond(431, dict(error='Request too large'))
            try:
                body = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
            except (ValueError, UnicodeDecodeError):
                return self.respond(400, dict(error='Body must be JSON'))
            command = str(body.get('command') or '')
            if command not in COMMANDS:
                return self.respond(400, dict(error='Unknown command. Allowed: %s'
                                              % ', '.join(sorted(COMMANDS))))
            if not BUSY.acquire(blocking=False):
                return self.respond(409, dict(error='A desk command is already running; wait for it'))
            try:
                event = run(command, symbol=body.get('symbol'), reply=body.get('reply'),
                            limit=body.get('limit') or LIMIT_DEFAULT, risk=body.get('risk'),
                            session=body.get('session'), index=body.get('index'))
            except subprocess.TimeoutExpired:
                event = {'time': dt.datetime.now(IST).isoformat(), 'command': command,
                         'returncode': None, 'output': 'timed out -- the desk process was left running'}
                logged(event)
            except (ValueError, OSError) as exc:
                return self.respond(400, dict(error=str(exc)[:300]))
            finally:
                BUSY.release()
            return self.respond(200 if event.get('returncode') == 0 else 409, event)

        def local_only(self):
            host = self.headers.get('Host', '')
            allowed = ('127.0.0.1:%d' % self.server.server_port, 'localhost:%d' % self.server.server_port)
            if host not in allowed:
                self.respond(403, dict(error='Local dashboard only'))
                return False
            return True

    return DeskHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', default=str(ROOT / 'workspace'))
    parser.add_argument('--sessions', default=str(ROOT / 'workspace' / 'team'))
    parser.add_argument('--port', type=int, default=8788)
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), build(Path(args.sessions).resolve(), workspace))
    print('Desk dashboard: http://127.0.0.1:%s' % server.server_port, flush=True)
    print('Workspace ' + str(workspace), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
