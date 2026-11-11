"""The issuer's own numbers, read from the SEBI integrated filing that NSE hosts.

This is the desk's only source of *figures* rather than *prices*. Under regulation 33
every listed company files its quarterly results to the exchange as an XBRL instance,
and NSE publishes that instance on `nsearchives.nseindia.com` with the dissemination
timestamp. The desk downloads the file the exchange lists, walks its contexts, and
quotes each number with the period it was reported for and the scope it was reported
in. Nothing is estimated, interpolated or carried over from a previous quarter: a
concept the filing does not carry is reported as absent, and a filing the exchange
does not list is reported as a refusal.

It matters for one role in particular. The engine only admits a `news` or `analyst`
verdict against a source whose host is on the sealed room's trusted list and whose
publication time is verifiable, which is exactly what an exchange-hosted XBRL filing
supplies: the host is `nsearchives.nseindia.com`, and `creation_Date` from the
exchange's own index is the publication time.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import optionchain  # noqa: E402

WORKSPACE = ROOT / 'workspace'
STATE = WORKSPACE / 'state' / 'financials'
IST = optionchain.IST
TTL = 6 * 3600                      # a filing changes when the issuer files, not intraday
XBRli = '{http://www.xbrl.org/2003/instance}'
EXPLICIT = '{http://xbrl.org/2006/xbrldi}explicitMember'
TYPED = '{http://xbrl.org/2006/xbrldi}typedMember'
NEGATED = '{http://www.xbrl.org/2009/role/negated}negated'
INDEX_API = 'https://www.nseindia.com/api/integrated-filing-results'
SOURCE_PAGE = 'https://www.nseindia.com/companies-listing/corporate-filings-results'
CRORE = 10 ** 7

# Reported as filed. Concepts a given issuer does not carry are simply left out.
CONCEPTS = (
    ('RevenueFromOperations', 'Revenue from operations', 'money'),
    ('Income', 'Total income (revenue + other income)', 'money'),
    ('CostOfMaterialsConsumed', 'Cost of materials consumed', 'money'),
    ('EmployeeBenefitExpense', 'Employee benefit expense', 'money'),
    ('DepreciationDepletionAndAmortisationExpense', 'Depreciation and amortisation', 'money'),
    ('ProfitLossForPeriod', 'Net profit / loss for the period', 'money'),
    ('ComprehensiveIncomeForThePeriod', 'Comprehensive income for the period', 'money'),
    ('BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations',
     'Basic EPS, continuing and discontinued', 'per_share'),
    ('BasicEarningsLossPerShareFromContinuingOperations', 'Basic EPS, continuing operations', 'per_share'),
    ('DebtEquityRatio', 'Debt / equity, as filed', 'ratio'),
    ('Assets', 'Total assets', 'money'),
    ('CurrentAssets', 'Current assets', 'money'),
    ('Liabilities', 'Total liabilities', 'money'),
    ('BorrowingsCurrent', 'Borrowings, current', 'money'),
    ('BorrowingsNoncurrent', 'Borrowings, non-current', 'money'),
    ('PaidUpValueOfEquityShareCapital', 'Paid-up equity share capital', 'money'),
    ('FaceValueOfEquityShareCapital', 'Face value per equity share', 'per_share'),
)
CONCEPT_NAMES = {name: (name, label, kind) for name, label, kind in CONCEPTS}


class FinancialsError(RuntimeError):
    """The exchange did not answer, or answered with nothing usable."""


def slug(text):
    cleaned = re.sub(r'[^A-Z0-9]+', '-', str(text or '').upper().strip())
    return cleaned.strip('-')[:48] or 'empty'


def _ist(text):
    """`30-Oct-2026 21:32:55` -> aware IST datetime, or None."""
    try:
        stamp = dt.datetime.strptime(str(text).strip(), '%d-%b-%Y %H:%M:%S')
    except (TypeError, ValueError):
        return None
    return stamp.replace(tzinfo=IST)


def _ist_date(text):
    try:
        return dt.datetime.strptime(str(text).strip(), '%d-%b-%Y').date()
    except (TypeError, ValueError):
        return None


def _get(opener, url, referer=None, timeout=90):
    headers = dict(optionchain.HEADERS)
    if referer:
        headers['Referer'] = referer
    request = urllib.request.Request(url, headers=headers)
    with opener.open(request, timeout=timeout) as response:
        return response.read()


def filings(bare, scope='Standalone', now=None):
    """The exchange's own list of integrated financial filings for one scrip."""
    query = urllib.parse.urlencode({'index': 'equities', 'symbol': bare})
    opener = optionchain._opener()
    try:
        blob = _get(opener, INDEX_API + '?' + query, referer=SOURCE_PAGE)
    except Exception as exc:                       # noqa: BLE001 -- the refusal is the answer
        raise FinancialsError('NSE filing index refused or failed for %s: %s'
                              % (bare, str(exc)[:160])) from exc
    try:
        doc = json.loads(blob.decode('utf-8', 'replace'))
    except ValueError:
        raise FinancialsError('NSE returned something that is not the filing index for %s' % bare) from None
    rows = [row for row in (doc.get('data') or [])
            if str(row.get('type') or '').startswith('Integrated Filing- Financials')
            and str(row.get('xbrl') or '').lower().endswith('.xml')
            and (scope is None or str(row.get('consolidated') or '') == scope)]
    if not rows:
        raise FinancialsError('NSE lists no integrated financial filing in XBRL for %s (%s)'
                              % (bare, scope.lower()))
    # A revision supersedes the original for the same quarter; newest first either way.
    best = {}
    for row in rows:
        key = (str(row.get('qe_Date') or ''), str(row.get('consolidated') or ''))
        stamp = _ist(row.get('creation_Date')) or dt.datetime(1970, 1, 1, tzinfo=IST)
        ranked = (stamp, str(row.get('sub') or '') == 'Revision')
        if key not in best or ranked > best[key][0]:
            best[key] = (ranked, row)
    out = [value[1] for value in best.values()]
    out.sort(key=lambda row: _ist(row.get('creation_Date')) or dt.datetime(1970, 1, 1, tzinfo=IST),
             reverse=True)
    return out


def _contexts(root):
    """contextRef -> the period it reports, plus who reports it. No context, no claim."""
    found = {}
    for context in root.findall(XBRli + 'context'):
        period = context.find(XBRli + 'Period') or context.find(XBRli + 'period')
        span = {'instant': None, 'from': None, 'to': None}
        if period is not None:
            instant = period.find(XBRli + 'Instant') or period.find(XBRli + 'instant')
            start = period.find(XBRli + 'StartDate') or period.find(XBRli + 'startDate')
            end = period.find(XBRli + 'EndDate') or period.find(XBRli + 'endDate')
            if instant is not None and instant.text:
                span['instant'] = instant.text.strip()
            else:
                span['from'] = (start.text.strip() if start is not None and start.text else None)
                span['to'] = (end.text.strip() if end is not None and end.text else None)
        entity = context.find(XBRli + 'Entity') or context.find(XBRli + 'entity')
        identifier = None
        if entity is not None:
            node = entity.find(XBRli + 'Identifier') or entity.find(XBRli + 'identifier')
            identifier = (node.text or '').strip() if node is not None else None
        dims = []
        for member in context.iter(EXPLICIT):
            name = member.find(XBRli + 'Dimension') or member.find(XBRli + 'dimension')
            value = member.find(XBRli + 'Member') or member.find(XBRli + 'member')
            dims.append('%s=%s' % ((name.text if name is not None else '?'),
                                   (value.text if value is not None else '?')))
        for member in context.iter(TYPED):
            element = member.find(XBRli + 'TypedElement') or member.find(XBRli + 'typedElement')
            if element is None:
                continue
            href = element.get('{http://www.w3.org/1999/xlink}href') or ''
            value = element.find(XBRli + 'value')
            dims.append('%s=%s' % (href.split(':')[-1], (value.text or '').strip() if value is not None else '?'))
        found[context.get('id')] = dict(span, identifier=identifier, dimensions=dims)
    return found


def _facts(root, contexts):
    """Each curated concept, straight off the filing, with its context attached."""
    rows = []
    for element in root:                      # direct children only: facts, not tuple members
        name = element.tag.split('}')[-1]
        known = CONCEPT_NAMES.get(name)
        if not known or not (element.text or '').strip():
            continue
        raw = element.text.strip()
        try:
            value = float(raw.replace(',', ''))
        except ValueError:
            continue
        if (element.get(NEGATED) or '').lower() == 'true':
            value = -value
        context = contexts.get(element.get('contextRef')) or {}
        unit = element.get('unitRef') or ''
        kind = known[2]
        if unit.upper().endswith('PERSHARE'):
            kind = 'per_share'
        elif unit and not unit.upper().startswith(('INR', 'ISO4217:INR')) and kind == 'money':
            kind = 'ratio' if unit.lower() in ('pure', 'ratio') else kind
        rows.append({'concept': name, 'label': known[1], 'kind': kind,
                     'value_as_filed': raw, 'value': value, 'unit': unit,
                     'period_from': context.get('from'), 'period_to': context.get('to'),
                     'instant': context.get('instant'),
                     'dimensions': context.get('dimensions') or [],
                     'identifier': context.get('identifier')})
    return rows


def _period_kind(span_from, span_to):
    if not span_from or not span_to:
        return 'As at %s' % span_to
    try:
        start = dt.date.fromisoformat(span_from)
        end = dt.date.fromisoformat(span_to)
    except ValueError:
        return 'Period %s to %s' % (span_from, span_to)
    days = (end - start).days + 1
    label = ('Quarter' if 85 <= days <= 97 else 'Half-year' if 175 <= days <= 190
             else 'Nine months' if 265 <= days <= 280 else 'Full year' if 358 <= days <= 372
             else '%d-day period' % days)
    return '%s ended %s (as filed %s to %s)' % (label, end.isoformat(), span_from, span_to)


def _display(row):
    if row['kind'] == 'money':
        return 'Rs %s cr' % format(row['value'] / CRORE, ',.2f')
    if row['kind'] == 'per_share':
        return 'Rs %s' % format(row['value'], ',.2f')
    return format(row['value'], ',.4f').rstrip('0').rstrip('.')


def parse(blob, row):
    """Turn one filing instance into blocks the desk can show without editorialising."""
    try:
        root = ET.fromstring(blob)
    except ET.ParseError:
        raise FinancialsError('the XBRL instance could not be parsed') from None
    contexts = _contexts(root)
    facts = _facts(root, contexts)
    if not facts:
        raise FinancialsError('the filing carries none of the concepts the desk reports')
    groups, order = {}, []
    for item in facts:
        if item['instant']:
            key, title = ('instant', item['instant']), 'As at %s' % item['instant']
        elif item['period_from'] and item['period_to']:
            key = ('span', item['period_from'], item['period_to'])
            title = _period_kind(item['period_from'], item['period_to'])
        else:
            key, title = ('unstated', item['concept']), 'Period not stated in the filing'
        if key not in groups:
            groups[key] = {'title': title, 'figures': []}
            order.append(key)
        groups[key]['figures'].append(dict(item, display=_display(item)))
    identifiers = sorted({item['identifier'] for item in facts if item['identifier']})
    dimensions = sorted({text for item in facts for text in item['dimensions']})
    return {
        'blocks': [groups[key] for key in order],
        'identifier': identifiers[0] if len(identifiers) == 1 else identifiers,
        'dimensions': dimensions[:8],
        'fact_count': len(facts),
    }


def collect(bare, scope='Standalone', quarters=2, now=None):
    """Latest filed figures for one scrip, with the exchange's own provenance."""
    now = now or dt.datetime.now(IST)
    rows = filings(bare, scope=scope)
    chosen = rows[:max(1, int(quarters))]
    opener = optionchain._opener()
    fetched = []
    for row in chosen:
        link = str(row.get('xbrl') or '')
        if link.startswith('/'):
            link = 'https://nsearchives.nseindia.com' + link
        try:
            blob = _get(opener, link, timeout=120)
        except Exception as exc:                   # noqa: BLE001 -- reported, never guessed
            fetched.append({'filing': _provenance(row, link),
                            'absent': 'download refused or failed: %s' % str(exc)[:160]})
            continue
        try:
            doc = parse(blob, row)
        except FinancialsError as exc:
            fetched.append({'filing': _provenance(row, link), 'absent': str(exc)})
            continue
        doc['filing'] = _provenance(row, link)
        fetched.append(doc)
    usable = [item for item in fetched if item.get('blocks')]
    if not usable:
        raise FinancialsError('NSE listed %s filing(s) for %s and none carried a readable figure (%s)'
                              % (len(fetched), bare, usable and 'ok' or fetched[0].get('absent', 'unreadable')))
    return {
        'symbol': bare,
        'scope': scope,
        'source': 'NSE integrated filing in SEBI XBRL, hosted on nsearchives.nseindia.com',
        'source_page': SOURCE_PAGE,
        'as_of': usable[0]['filing'].get('period_end_display'),
        'fetched_at': now.isoformat(),
        'filings': fetched,
        'latest': usable[0],
        'note': ('Every figure is the issuer\'s own regulation-33 filing, quoted with the period and '
                 'scope it was filed in, and converted to crore for legibility only. The desk reads '
                 'the instance the exchange publishes; it estimates nothing and carries nothing over '
                 'from an earlier quarter.'),
    }


def _provenance(row, link):
    published = _ist(row.get('creation_Date'))
    qe = _ist_date(row.get('qe_Date'))
    return {
        'url': link,
        'host': urllib.parse.urlsplit(link).netloc,
        'published_at': published.isoformat() if published else None,
        'published_display': str(row.get('creation_Date') or ''),
        'period_end': qe.isoformat() if qe else None,
        'period_end_display': str(row.get('qe_Date') or ''),
        'audited': row.get('audited'),
        'filing_kind': row.get('type_Sub') or row.get('sub'),
        'scope': row.get('consolidated'),
        'type': row.get('type'),
        'size_bytes': row.get('xbrlFileSize'),
    }


def cached(bare, scope='Standalone'):
    path = STATE / ('%s-%s.json' % (slug(bare), slug(scope)))
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    written = doc.get('_written_at')
    try:
        age = (dt.datetime.now(IST) - dt.datetime.fromisoformat(written)).total_seconds()
    except (TypeError, ValueError):
        return doc
    doc['age_seconds'] = int(age)
    doc['cache_hit'] = True
    return doc


def store(doc):
    STATE.mkdir(parents=True, exist_ok=True)
    payload = dict(doc, _written_at=dt.datetime.now(IST).isoformat())
    path = STATE / ('%s-%s.json' % (slug(doc['symbol']), slug(doc['scope'])))
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
    os.replace(temp, path)
    return path


def fetch(bare, scope='Standalone', quarters=2, refresh=False, now=None):
    """Cached read first: one company's filed numbers do not change between candles."""
    text = re.sub(r'\.(NS|BO)$', '', str(bare or '').upper().strip())
    if not text:
        raise FinancialsError('no symbol given')
    if not refresh:
        doc = cached(text, scope)
        if doc:
            return doc
    doc = collect(text, scope=scope, quarters=quarters, now=now)
    store(doc)
    doc['cache_hit'] = False
    return doc


def print_financials(doc):
    latest = doc['latest']
    filing = latest['filing']
    print('%s -- %s filing, period ended %s' % (doc['symbol'], filing.get('scope') or doc['scope'],
                                                filing.get('period_end_display') or '?'))
    print('  source   %s' % filing['host'])
    print('  filed    %s (%s, %s)' % (filing.get('published_display') or 'time not stated',
                                      filing.get('filing_kind') or 'filing',
                                      filing.get('audited') or 'audit status not stated'))
    print('  link     %s' % filing['url'])
    print('  fetched  %s%s' % (doc.get('fetched_at'), ' (cache)' if doc.get('cache_hit') else ''))
    for block in latest['blocks']:
        print('\n  %s' % block['title'])
        for row in block['figures']:
            print('    %-38s %-14s (as filed %s %s)' % (
                row['label'], row['display'], row['value_as_filed'], row['unit'] or 'no unit stated'))
    for item in doc['filings'][1:]:
        if item.get('blocks'):
            prior = item['filing']
            print('\n  previous filing: period ended %s, filed %s -- %d figures read'
                  % (prior.get('period_end_display'), prior.get('published_display'),
                     item.get('fact_count', 0)))
        else:
            print('\n  previous filing: %s' % item.get('absent'))
    print('\n  %s' % doc['note'])


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Issuer-filed figures from the exchange XBRL instance')
    parser.add_argument('symbol', help='RELIANCE, M&M, UNIPARTS (the NSE scrip name; .NS is ignored)')
    parser.add_argument('--scope', default='Standalone', choices=('Standalone', 'Consolidated'))
    parser.add_argument('--quarters', type=int, default=2, help='how many filed quarters to read')
    parser.add_argument('--refresh', action='store_true', help='ignore the 6-hour cache')
    parser.add_argument('--json', action='store_true', help='print the document instead of the table')
    args = parser.parse_args(argv)
    try:
        doc = fetch(args.symbol, scope=args.scope, quarters=args.quarters, refresh=args.refresh)
    except FinancialsError as exc:
        print('No filed numbers: %s' % exc)
        return 1
    if args.json:
        print(json.dumps(doc, indent=2, default=str))
    else:
        print_financials(doc)
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
