"""Keyless Indian headlines from Google News RSS, one query per symbol plus a market sweep.

The vendored news skill reads US sources (Yahoo finance RSS, SEC EDGAR) and returns
zero items for NSE symbols -- those endpoints answer with empty channels. This reads a
public RSS search instead and keeps every publisher and publication timestamp exactly
as the feed gave them. Headlines are leads to check, not verified facts.
"""
import datetime as dt
import email.utils
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / 'workspace'
FEED = 'https://news.google.com/rss/search'
USER_AGENT = 'Mozilla/5.0 (compatible; day-trader-desk/1.0; local paper research)'
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

MARKET_QUERIES = (
    ('market open, Nifty and Sensex', 'Nifty Sensex stock market today'),
    ('NSE corporate announcements', 'NSE stock results dividend board meeting announcement'),
)
# Price widgets repeat the ticker and carry no event; they are noise in a news list.
WIDGET = re.compile(r'\b(share price|stock price|market price|live price|price target|pre-market)\b', re.I)
BARE = re.compile(r'\.NS$|\.BO$', re.I)

NOTES = (
    'Titles and publishers are copied from the feed. Nothing here is confirmed against '
    'the original filing or exchange record -- open the link before acting.',
    'Links are Google News redirect URLs, not the publisher URL, because that is what the RSS feed supplies.',
    'This is not complete exchange coverage and it is not a substitute for NSE announcements.',
)


def _text(node, tag):
    child = node.find(tag)
    return (child.text or '').strip() if child is not None and child.text else ''


def _parse(body, symbol, hours, now):
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise ValueError('feed is not valid XML') from None
    rows = []
    for node in root.findall('.//item'):
        published = _text(node, 'pubDate')
        when = None
        try:
            when = email.utils.parsedate_to_datetime(published)
        except (TypeError, ValueError):
            when = None
        if when is None:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        age = (now - when.astimezone(IST)).total_seconds()
        if age < -300 or age > hours * 3600:
            continue
        title = _text(node, 'title')
        if not title:
            continue
        rows.append(dict(symbol=symbol, title=title, publisher=_text(node, 'source'),
                         link=_text(node, 'link'), published_at=when.astimezone(IST).isoformat(),
                         age_hours=round(age / 3600, 1), widget=bool(WIDGET.search(title))))
    return rows


def _fetch(query):
    url = FEED + '?' + urllib.parse.urlencode({'q': query, 'hl': 'en-IN', 'gl': 'IN', 'ceid': 'IN:en'})
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/rss+xml'})
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()


def collect(symbols, hours=36, per_query=8, sleep=0.25, now=None):
    now = now or dt.datetime.now(IST)
    items, errors = [], []
    queries = [(BARE.sub('', symbol).strip().upper() + ' NSE', symbol) for symbol in symbols]
    queries += [(text, None) for _, text in MARKET_QUERIES]
    for index, (query, symbol) in enumerate(queries):
        if index:
            time.sleep(sleep)
        try:
            rows = _parse(_fetch(query), symbol, hours, now)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append(dict(query=query, error=str(getattr(exc, 'reason', exc))[:160] or type(exc).__name__))
            continue
        rows.sort(key=lambda row: row['published_at'], reverse=True)
        # A price-widget title is only noise when the query has something else to show.
        useful = [row for row in rows if not row['widget']]
        rows = (useful or rows if symbol else useful)[:per_query]
        for row in rows:
            row['query'] = query
        items += rows
    market = [row for row in items if row['symbol'] is None]
    company = [row for row in items if row['symbol']]
    return dict(items=company, market=market, errors=errors)


def mark_new(items, workspace=WORKSPACE):
    """Flag lines the desk has not shown before, then remember them."""
    path = Path(workspace) / 'state' / 'news-seen.json'
    try:
        seen = set(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError):
        seen = set()
    fresh = 0
    for row in items:
        key = re.sub(r'[^a-z0-9]', '', row['title'].lower())[:120]
        row['seen_before'] = key in seen
        if not row['seen_before']:
            fresh += 1
            seen.add(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(seen)[-3000:]), encoding='utf-8')
    return fresh


def gather(symbols, hours=36, per_query=8, workspace=WORKSPACE):
    found = collect(symbols, hours=hours, per_query=per_query)
    fresh = mark_new(found['items'] + found['market'], workspace)
    return dict(created_by='desk newsfeed', as_of=dt.datetime.now(IST).isoformat(),
                provider='Google News RSS search (keyless); publisher and pubDate kept as supplied',
                paper_only=True, execution_enabled=False, window_hours=hours,
                universe=len(symbols), symbols=list(symbols),
                new_items=fresh, items=found['items'], market=found['market'],
                errors=found['errors'], notes=list(NOTES))


def print_headlines(doc):
    def line(row, label):
        print('  %-16s %-5s %s  %-15s %s' % ((row['symbol'] or 'market'), label,
                                             str(row['published_at'])[5:16],
                                             (row['publisher'] or '?')[:15], row['title'][:88]))
    if doc['items']:
        print('\nsymbol headlines | newest %d per symbol' % len(doc['items']))
        for row in doc['items']:
            line(row, 'NEW' if not row['seen_before'] else 'seen')
    else:
        print('\nsymbol headlines | none inside the %dh window' % doc['window_hours'])
    if doc['market']:
        print('\nmarket headlines')
        for row in doc['market']:
            line(row, 'NEW' if not row['seen_before'] else 'seen')
    for row in doc['errors']:
        print('  ERROR %s: %s' % (row['query'], row['error']))
    print('\n%d new, %d already shown, %d query errors' % (
        doc['new_items'], len(doc['items']) + len(doc['market']) - doc['new_items'], len(doc['errors'])))
    for note in doc['notes']:
        print('  - ' + note)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols', default='', help='Comma list like RELIANCE.NS,SBIN.NS')
    parser.add_argument('--hours', type=int, default=36)
    parser.add_argument('--out', default=str(WORKSPACE / 'headlines.json'))
    args = parser.parse_args(argv)
    names = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    if not names:
        parser.exit(1, 'Pass --symbols RELIANCE.NS,SBIN.NS (the desk command uses your saved lists)\n')
    doc = gather(names, hours=args.hours)
    print_headlines(doc)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, default=str), encoding='utf-8')
    print('\nwrote %s' % path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
