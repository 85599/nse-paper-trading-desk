"""Independent per-role reviewers that read one sealed artifact and answer for it.

A room is only honest if every role's verdict comes from something that actually
opened that role's own evidence. Each one reads a single sealed artifact (plus
``context.json`` for the settings the room was sealed under), re-checks the fields
that artifact carries, and returns a verdict the engine can bind. A checker can
confirm or downgrade what the artifact says, but it never upgrades a BLOCK into a
PASS -- the engine re-derives the numbers again at finalize, so an optimistic
claim with no evidence behind it is refused there instead.

Two roles are asked to read evidence the exchange does not put inside a candle
file, so they go and open it themselves: news reads NSE's own corporate-announcement
list for the scrip, analyst reads the issuer's regulation-33 XBRL instance the
exchange hosts. Both cite it with the exchange's own filing time, and both return
UNAVAILABLE rather than a pleasant guess when there is nothing admissible to cite.

Every verdict carries `code` (what was found) and `kind` (what kind of answer it
is), because "this role says no" and "this role had nothing to check" arrive as the
same two badges otherwise, and that distinction is the whole conclusion.
"""
import datetime as dt
import json
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = {'scanner': 'watchlist.json', 'news': 'news.json', 'analyst': 'metrics.json',
             'entry': 'entry.json', 'exit': 'plan.json', 'timemachine': 'timemachine.json',
             'budget': 'orders.json', 'risk': 'state/risk.json',
             'broker': 'state/broker.json', 'alerts': 'alerts.json'}
# The four kinds a verdict can belong to. KEYS is the set; the UI prints the label.
KIND_LABELS = {'confirmed': 'Checked its own evidence',
               'veto': 'Real veto',
               'nothing-to-check': 'Nothing to certify yet',
               'evidence-gap': 'No admissible evidence to read'}
# A budget/risk BLOCK listing only this reason is not a limit saying no -- it is
# the sizing stage finding nothing to size because the entry stage never fired.
NO_CANDIDATE = 'no_eligible_entry'


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _data(doc):
    return doc.get('data') if isinstance(doc.get('data'), dict) else {}


def _money(value):
    return '₹%s' % format(value, ',.2f') if isinstance(value, (int, float)) else 'null'


def _moment(text):
    try:
        stamp = dt.datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=dt.timezone(dt.timedelta(hours=5,
                                                                                      minutes=30)))


def _room(session):
    """The symbol and cutoff this room was sealed under, from its own context."""
    settings = (_read(session / 'context.json') or {}).get('settings') or {}
    return str(settings.get('symbol') or '').upper(), _moment(settings.get('as_of'))


def _source(url, published, accessed, cutoff):
    """One citation in the exact shape the engine admits, or None.

    ``_source_check`` at finalize wants https on an exchange/issuer host, a
    publication no later than the room's own cutoff, and an access time that is
    not before publication. Anything else is refused there, so it is not offered
    here either -- a reviewer cannot cite a page it could not have read by as_of.
    """
    host = (urllib.parse.urlsplit(str(url or '')).hostname or '').lower()
    admitted = any(host == domain or host.endswith('.' + domain)
                   for domain in ('nseindia.com', 'bseindia.com'))
    if not (admitted and str(url).startswith('https://')):
        return None
    if published is None or accessed is None or cutoff is None:
        return None
    if published > cutoff or accessed < published:
        return None
    return {'url': str(url), 'published_at': published.isoformat(),
            'accessed_at': accessed.isoformat()}


def _answer(verdict, code, kind, summary, evidence, **extra):
    draft = {'verdict': verdict, 'code': code, 'kind': kind, 'summary': summary,
             'evidence': list(evidence)}
    draft.update(extra)
    return draft


def _display(stamp):
    return stamp.strftime('%d-%b-%Y %H:%M IST') if stamp else 'time not stated'


def _disclosures(symbol, cutoff):
    """Open NSE's own disclosure list for the scrip and keep only what it published
    by this room's cutoff. Returns (sources, newest item, error or None)."""
    try:
        import announcements
        doc = announcements.fetch(symbol, days=45, limit=12)
    except Exception as exc:                       # noqa: BLE001 -- reported, never hidden
        return [], None, str(exc)[:180] or exc.__class__.__name__
    read_at = _moment(doc.get('fetched_at')) or _moment(doc.get('as_of'))
    if read_at is None:
        return [], None, 'the disclosure list carried no exchange read time'
    sources = [row for row in (_source(item.get('url'), _moment(item.get('published_at')),
                                       read_at, cutoff)
                               for item in doc.get('items') or []) if row]
    newest = next((item for item in doc.get('items') or []
                   if _source(item.get('url'), _moment(item.get('published_at')),
                              read_at, cutoff)), None)
    return sources, newest, None


def _filed_figures(symbol, cutoff):
    """Read the issuer's regulation-33 XBRL instance the exchange hosts, newest
    filing published by the cutoff that actually carries a figure.

    Returns (detail, sources, error or None).
    """
    try:
        import financials
        doc = financials.fetch(symbol, quarters=2)
    except Exception as exc:                       # noqa: BLE001 -- reported, never hidden
        return None, [], str(exc)[:180] or exc.__class__.__name__
    read_at = _moment(doc.get('fetched_at')) or _moment(doc.get('_written_at'))
    for record in doc.get('filings') or []:
        filing = record.get('filing') or {}
        published, blocks = _moment(filing.get('published_at')), record.get('blocks') or []
        source = _source(filing.get('url'), published, read_at, cutoff)
        if not source or not blocks:
            continue
        figures = [row for block in blocks for row in block.get('figures') or []]
        if not figures:
            continue
        return {'source': source, 'period': filing.get('period_end_display'),
                'filed_display': filing.get('published_display') or _display(published),
                'kind': filing.get('filing_kind') or 'filing',
                'scope': record.get('scope') or filing.get('scope') or doc.get('scope'),
                'block': blocks[0], 'figures': figures,
                'fact_count': record.get('fact_count') or len(figures),
                'read_at': read_at}, [source], None
    return None, [], 'no filing NSE hosts for this issuer was both readable and published by %s' \
                     % _display(cutoff)


def scanner(session):
    d = _data(_read(session / ARTIFACTS['scanner']))
    analysis = d.get('analysis') or {}
    quality = analysis.get('data_quality') or {}
    tick = (d.get('tick_verification') or {}).get('status')
    complete = bool(quality.get('complete'))
    setup = analysis.get('status')
    reasons = []
    if not complete:
        reasons.append('the sealed candles are not marked complete')
    if tick != 'VERIFIED':
        reasons.append('the tick is %s, not VERIFIED for this symbol' % tick)
    if reasons:
        return _answer('BLOCK', 'SEALED_DATA_NOT_CERTIFIABLE', 'veto',
                       'The scanner cannot certify this room: %s.' % '; '.join(reasons),
                       ['data_quality.complete=%s' % complete,
                        'tick_verification=%s' % tick,
                        'analysis.status=%s' % setup])
    return _answer('PASS', 'CANDLES_COMPLETE_TICK_VERIFIED', 'confirmed',
                   'Candles are complete and the %s tick is exchange-verified; the sealed '
                   'window reads as %s for %s.' % (tick, setup, d.get('candidates') or []),
                   ['data_quality.complete=true', 'tick_verification=VERIFIED',
                    'analysis.status=%s' % setup, 'bars=%s' % analysis.get('bar_count')])


def news(session):
    d = _data(_read(session / ARTIFACTS['news']))
    items, sources = d.get('items') or [], d.get('sources') or []
    if sources:
        return _answer('WARN', 'SEALED_SOURCES_READ', 'confirmed',
                       'The room sealed %d dated source(s); the reviewer read them and found no '
                       'material item that changes the setup.' % len(sources),
                       ['items=%d' % len(items), 'sources=%d' % len(sources)],
                       sources=sources)
    symbol, cutoff = _room(session)
    if not symbol or cutoff is None:
        return _answer('UNAVAILABLE', 'NO_SEALED_CUTOFF', 'evidence-gap',
                       'The sealed context carries no symbol and as-of for this room, so nothing '
                       'the desk reads can be dated against its cutoff.',
                       ['context symbol=%s' % (symbol or 'missing'),
                        'as_of=%s' % ('missing' if cutoff is None else cutoff.isoformat())])
    live, newest, failure = _disclosures(symbol, cutoff)
    if failure:
        return _answer('UNAVAILABLE', 'EXCHANGE_LIST_UNREADABLE', 'evidence-gap',
                       'The desk went and read NSE\'s own disclosure list for %s and could not '
                       'open it: %s. A PASS or WARN here would be a citation nobody opened.'
                       % (symbol, failure),
                       ['sealed sources=0', 'nse corporate-announcements=unavailable',
                        'error=%s' % failure])
    if not live:
        return _answer('UNAVAILABLE', 'NO_DISCLOSURE_BEFORE_CUTOFF', 'evidence-gap',
                       'NSE listed no disclosure for %s that was published and readable before '
                       'this room\'s cutoff of %s, so there is no dated source to cite. Absence '
                       'from the list is reported, not read as a clean bill.'
                       % (symbol, _display(cutoff)),
                       ['sealed sources=0', 'items in the desk read=0',
                        'cutoff=%s' % cutoff.isoformat()])
    host = urllib.parse.urlsplit(newest['url']).netloc
    return _answer('WARN', 'EXCHANGE_DISCLOSURE_LIST_READ', 'confirmed',
                   'Read NSE\'s own disclosure list for %s before this cutoff: %d filing(s), '
                   'newest "%s" on %s at %s. It is a dated disclosure, not a price claim.'
                   % (symbol, len(live), str(newest.get('category') or 'Announcement')[:48],
                      host, newest.get('published_display') or _display(cutoff)),
                   ['sources=%d read by this reviewer' % len(live),
                    'newest=%s' % (newest.get('published_at') or ''),
                    'category=%s' % (newest.get('category') or 'Announcement'),
                    'sealed sources=0'],
                   sources=live)


def analyst(session):
    d = _data(_read(session / ARTIFACTS['analyst']))
    items, sources = d.get('items') or [], d.get('sources') or []
    if sources:
        return _answer('WARN', 'SEALED_FILED_FIGURES_READ', 'confirmed',
                       'The analyst opened the sealed filed-figure source and read %d numeric '
                       'item(s) from it.' % len(items),
                       ['items=%d' % len(items), 'sources=%d' % len(sources)],
                       sources=sources)
    symbol, cutoff = _room(session)
    if not symbol or cutoff is None:
        return _answer('UNAVAILABLE', 'NO_SEALED_CUTOFF', 'evidence-gap',
                       'The sealed context carries no symbol and as-of for this room, so no '
                       'filing can be dated against its cutoff.',
                       ['context symbol=%s' % (symbol or 'missing'),
                        'as_of=%s' % ('missing' if cutoff is None else cutoff.isoformat())])
    detail, live, failure = _filed_figures(symbol, cutoff)
    if failure:
        return _answer('UNAVAILABLE', 'NO_FILED_FIGURE_BEFORE_CUTOFF', 'evidence-gap',
                       'The analyst looked for the issuer\'s own regulation-33 XBRL instance on '
                       'the exchange archive for %s and found none it could quote by this '
                       'cutoff: %s' % (symbol, failure),
                       ['sealed sources=0', 'cutoff=%s' % cutoff.isoformat(),
                        'filed figures=%s' % failure],
                       )
    quoted = '; '.join('%s %s' % (row['label'], row['display'])
                       for row in detail['figures'][:3])
    return _answer('WARN', 'FILED_FIGURES_READ', 'confirmed',
                   'The issuer\'s own regulation-33 filing for the period ended %s (%s, filed %s) '
                   'reports %s -- %d figures read straight off the instance NSE hosts, with its '
                   'filing time cited.' % (detail['period'] or '?', detail['scope'] or 'Standalone',
                                           detail['filed_display'], quoted, detail['fact_count']),
                   ['filed=%s' % detail['filed_display'],
                    'period_ended=%s' % (detail['period'] or '?'),
                    'figures read=%d' % detail['fact_count'],
                    'sealed sources=0'],
                   sources=live)


def entry(session):
    d = _data(_read(session / ARTIFACTS['entry']))
    status, complete, stale = d.get('status'), d.get('data_complete'), d.get('stale')
    if status == 'TRIGGERED' and complete and not stale:
        return _answer('PASS', 'COMPLETED_BAR_TRIGGER', 'confirmed',
                       'The trigger is a completed-bar event: signal_time %s confirmed at %s, '
                       'data complete and fresh.' % (d.get('signal_time'),
                                                     d.get('confirmation_time')),
                       ['status=TRIGGERED', 'data_complete=%s' % complete,
                        'stale=%s' % stale, 'signal_time=%s' % d.get('signal_time')])
    return _answer('BLOCK', 'NO_COMPLETED_BAR_TRIGGER', 'veto',
                   'Nothing printed in this window: entry status is %s (data_complete=%s, '
                   'stale=%s), so there is no completed-bar event to act on. Every later stage '
                   'of this room has nothing to size from.' % (status, complete, stale),
                   ['status=%s' % status, 'data_complete=%s' % complete,
                    'stale=%s' % stale, 'signal_time=%s' % d.get('signal_time')])


def exit(session):
    d = _data(_read(session / ARTIFACTS['exit']))
    plan = d.get('plan')
    if not isinstance(plan, dict) or not plan:
        return _answer('UNAVAILABLE', 'NO_PLAN_YET', 'nothing-to-check',
                       'plan.json holds no plan because the entry stage never triggered, so there '
                       'is no entry, stop or target level for this role to certify. This is not a '
                       'veto about a trade -- there is no trade on the table.',
                       ['plan=null', 'stop_method=%s' % d.get('stop_method'),
                        'target_multiple=%s' % d.get('target_multiple')])
    entry_, stop, target = plan.get('entry'), plan.get('stop'), plan.get('target')
    ordered = isinstance(entry_, (int, float)) and isinstance(stop, (int, float)) and \
        isinstance(target, (int, float)) and stop < entry_ < target
    if not ordered:
        return _answer('BLOCK', 'INVALID_LEVEL_GEOMETRY', 'veto',
                       'The sealed levels are not a valid long geometry (stop %s < entry %s '
                       '< target %s failed).' % (stop, entry_, target),
                       ['entry=%s' % entry_, 'stop=%s' % stop, 'target=%s' % target])
    return _answer('PASS', 'LEVELS_ORDERED', 'confirmed',
                   'Levels are structurally placed and ordered: entry %s, stop %s, target %s '
                   'via %s at %sR.' % (_money(entry_), _money(stop), _money(target),
                                       d.get('stop_method'), d.get('target_multiple')),
                   ['entry=%s' % entry_, 'stop=%s' % stop, 'target=%s' % target,
                    'stop_method=%s' % d.get('stop_method')])


def timemachine(session):
    d = _data(_read(session / ARTIFACTS['timemachine']))
    status, reason = d.get('status'), d.get('reason')
    if status != 'AVAILABLE':
        # NO_PLAN is the replay stage saying it has nothing to simulate, which is the
        # entry stage's answer, not a shortage of history. Anything else is data.
        missing = reason == 'NO_PLAN' or status == 'NO_PLAN'
        return _answer('UNAVAILABLE', 'NO_PLAN_TO_SIMULATE' if missing else 'REPLAY_DATA_SHORT',
                       'nothing-to-check' if missing else 'evidence-gap',
                       'The seeded resampling returned %s (%s): %s' % (
                           status, reason,
                           'there is no plan to replay, because the entry stage never '
                           'triggered -- not a shortage of history.' if missing else
                           'no replay or scenario evidence supports this setup, so this role '
                           'has nothing to report.'),
                       ['status=%s' % status, 'sims=%s' % d.get('sims'), 'reason=%s' % reason])
    return _answer('PASS', 'SCENARIOS_AVAILABLE', 'confirmed',
                   'The seeded scenario paths (%s, seed %s) completed and are reported as '
                   'scenarios, not predictions.' % (d.get('sims'), d.get('seed')),
                   ['status=AVAILABLE', 'sims=%s' % d.get('sims'), 'seed=%s' % d.get('seed')])


def _limit_answer(stage, status, reasons, order, wording):
    """Sort a sizing/risk answer into 'a limit said no' and 'there was nothing to size'.

    Both arrive as BLOCK, which is exactly why every room read as a wall of vetoes.
    The engine's ``no_eligible_entry`` reason means the entry stage never triggered,
    so this stage had no candidate to test -- it is not the desk's cash, order or
    daily-loss limit refusing.
    """
    rows, summary = wording['rows'], wording['summary']
    if status == 'PASS' and order:
        return _answer('PASS', stage + '_WITHIN_LIMITS', 'confirmed', summary['pass'],
                       rows['pass'])
    if set(reasons or []) == {NO_CANDIDATE}:
        return _answer('BLOCK', 'NO_CANDIDATE_TO_SIZE', 'nothing-to-check',
                       summary['no_candidate'], rows['block'] + ['kind=no candidate, not a limit'])
    return _answer('BLOCK', 'LIMIT_OR_GEOMETRY_VETO', 'veto', summary['limit'] % (
        ', '.join(reasons) if reasons else 'no admissible order'),
        rows['block'] + ['kind=real limit or geometry'])


def budget(session):
    d = _data(_read(session / ARTIFACTS['budget']))
    order = d.get('order') or {}
    cash = _money(d.get('available_cash_after_buffer'))
    room = _money(d.get('remaining_risk_budget'))
    return _limit_answer(
        'budget', d.get('status'), d.get('reasons'), order,
        {'summary': {
            'pass': 'Sizing recomputes inside the sealed cash, order and daily-loss room: '
                    '%s shares at %s, available cash after buffer %s.'
                    % (order.get('qty'), _money(order.get('entry')), cash),
            'no_candidate': 'The budget stage has nothing to size because the entry stage never '
                            'triggered (no_eligible_entry). No cash, order or daily-loss limit '
                            'was reached -- %s of cash and %s of risk room were still free.'
                            % (cash, room),
            'limit': 'The budget stage blocks a ticket for this bar: %s.'},
         'rows': {'pass': ['status=PASS', 'qty=%s' % order.get('qty'),
                           'cash_after_buffer=%s' % cash, 'risk_room=%s' % room],
                  'block': ['status=%s' % d.get('status'),
                            'reasons=%s' % (d.get('reasons') or ['none']),
                            'cash_after_buffer=%s' % cash, 'risk_room=%s' % room]}})


def risk(session):
    d = _data(_read(session / ARTIFACTS['risk']))
    order = d.get('order') or {}
    reasons = d.get('reasons') or []
    stamp = _moment(d.get('checks_as_of'))
    stamp = stamp.isoformat() if stamp else 'no readable time in the artifact'
    rows = ['status=%s' % d.get('status'), 'reasons=%s' % (reasons or ['no order']),
            'order=%s' % ('present' if order else 'null'), 'checks_as_of=%s' % stamp]
    return _limit_answer(
        'risk', d.get('status'), reasons, order,
        {'summary': {
            'pass': 'Re-checked the exact order (%s at %s, stop %s) against the sealed '
                    'limits; no veto.' % (order.get('qty'), _money(order.get('entry')),
                                          _money(order.get('stop'))),
            'no_candidate': 'The risk stage has no order to veto: the only reason recorded is '
                            'no_eligible_entry, which is the entry stage never triggering, not a '
                            'limit breach. Checked at %s.' % stamp,
            'limit': 'The risk veto stands -- no order may be sized from this bar because %s.'},
         'rows': {'pass': ['status=PASS',
                           'order_fingerprint=%s' % str(d.get('order_fingerprint'))[:12],
                           'checks_as_of=%s' % stamp],
                  'block': rows}})


def broker(session):
    d = _data(_read(session / ARTIFACTS['broker']))
    status, order = d.get('status'), d.get('order')
    if d.get('submitted') or d.get('filled'):
        return _answer('BLOCK', 'EXECUTION_CLAIMED', 'veto',
                       'A broker artifact that claims a submitted or filled order is refused; '
                       'this desk is paper-only.',
                       ['submitted=%s' % d.get('submitted'), 'filled=%s' % d.get('filled')])
    if not order:
        return _answer('BLOCK', 'NO_TICKET_YET', 'nothing-to-check',
                       'There is no draft ticket to review (status %s, order null) because the '
                       'sizing stage had no candidate, so there is nothing to certify as '
                       'internally consistent. Any edit would invalidate a ticket that does not '
                       'exist yet.' % status,
                       ['status=%s' % status, 'order=null',
                        'execution_enabled=%s' % d.get('execution_enabled', False)])
    return _answer('PASS', 'DRAFT_TICKET_CONSISTENT', 'confirmed',
                   'The draft paper ticket is internally consistent and clearly unsubmitted '
                   '(%s shares at %s, submitted=false, filled=false). Any edit invalidates '
                   'it.' % (order.get('qty'), _money(order.get('entry'))),
                   ['status=%s' % status, 'submitted=%s' % d.get('submitted'),
                    'filled=%s' % d.get('filled'),
                    'fingerprint=%s' % str(d.get('order_fingerprint'))[:12]])


def alerts(session):
    d = _data(_read(session / ARTIFACTS['alerts']))
    items = d.get('alerts') or []
    if not items:
        return _answer('UNAVAILABLE', 'NO_ORDER_BOUND_ALERTS', 'nothing-to-check',
                       'No alert definition exists because no ticket exists (alerts=%d), so there '
                       'is nothing to certify as monitored. This is the entry stage\'s answer '
                       'travelling down the room, not a defect in the alerting path.' % len(items),
                       ['alerts=0', 'monitoring_started=%s' % d.get('monitoring_started'),
                        'deduplication_scope=%s' % d.get('deduplication_scope')])
    kinds = sorted({a.get('kind') for a in items if isinstance(a, dict)})
    return _answer('PASS', 'ALERTS_BOUND', 'confirmed',
                   '%d alert definition(s) bound to the order fingerprint (%s); these are '
                   'definitions only -- no scheduler was started.'
                   % (len(items), ', '.join(str(k) for k in kinds)),
                   ['alerts=%d' % len(items), 'kinds=%s' % kinds,
                    'monitoring_started=%s' % d.get('monitoring_started')])


REVIEWERS = {'scanner': scanner, 'news': news, 'analyst': analyst, 'entry': entry,
             'exit': exit, 'timemachine': timemachine, 'budget': budget, 'risk': risk,
             'broker': broker, 'alerts': alerts}


def review(role, session):
    """Run one role's checker against its own sealed artifact only."""
    session = Path(session)
    checker = REVIEWERS.get(role)
    if checker is None:
        raise KeyError('No reviewer for role %s' % role)
    draft = checker(session)
    draft.setdefault('evidence', ['the reviewer read %s' % ARTIFACTS[role]])
    if not str(draft.get('summary') or '').strip():
        draft['summary'] = 'Reviewed %s.' % ARTIFACTS[role]
    draft['verdict'] = str(draft['verdict']).strip().upper()
    # A verdict without its kind is the wall of badges this desk used to show: the
    # reader could not tell a veto from an empty evidence file.
    if not draft.get('kind'):
        draft['kind'] = {'PASS': 'confirmed', 'WARN': 'confirmed'}.get(
            draft['verdict'], 'veto' if draft['verdict'] == 'BLOCK' else 'evidence-gap')
    draft.setdefault('code', 'NOT_CLASSIFIED')
    return draft
