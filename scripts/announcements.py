"""NSE corporate announcements -- dated issuer filings, no API key.

The same cookie handshake the option chain needs also unlocks
/api/corporate-announcements, which returns the exchange's own disclosure list
with the filing PDF hosted on nsearchives.nseindia.com. That is the only source
in this desk that satisfies the news specialist's bar (an nseindia.com /
bseindia.com / issuer page actually opened before the cutoff), so it is read
here rather than paraphrased from a news aggregator.
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import optionchain

WORKSPACE = ROOT / 'workspace'
STATE = WORKSPACE / 'state' / 'announcements'
IST = optionchain.IST
TTL = 900
SOURCE = 'NSE corporate announcements (www.nseindia.com, exchange disclosure archive)'
PLATFORM = 'https://www.nseindia.com/static/companies-list/corporate-announcements'


class AnnouncementError(RuntimeError):
    pass


def slug(text):
    cleaned = re.sub(r'[^A-Z0-9]+', '-', str(text or '').upper().strip())
    return cleaned.strip('-')[:48] or 'empty'


def _bare(symbol):
    return re.sub(r'\.(NS|BO)$', '', str(symbol or '').upper()).strip()


def _stamp(value):
    for fmt in ('%d-%b-%Y %H:%M:%S', '%d-%b-%Y'):
        try:
            return dt.datetime.strptime(str(value).strip(), fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def _get(opener, path, params):
    url = optionchain.BASE + path + '?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers=optionchain.HEADERS)
    with opener.open(request, timeout=30) as response:
        body = response.read()
    if body[:2] == b'\x1f\x8b':
        import gzip
        body = gzip.decompress(body)
    return json.loads(body)


def _rows(bare, tries=2):
    last = None
    for attempt in range(tries):
        try:
            opener = optionchain._opener()
            doc = _get(opener, '/api/corporate-announcements',
                       {'index': 'equities', 'symbol': bare})
            if not isinstance(doc, list):
                raise AnnouncementError('NSE returned a %s instead of a list'
                                        % type(doc).__name__)
            return doc
        except AnnouncementError:
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError,
                ValueError, json.JSONDecodeError) as exc:
            last = exc
            if attempt + 1 < tries:
                time.sleep(1.5)
    raise AnnouncementError('NSE corporate announcements unavailable: %s'
                            % str(last)[:160])


def _item(row):
    published = _stamp(row.get('an_dt') or row.get('exchdisstime'))
    url = str(row.get('attchmntFile') or '')
    text = str(row.get('attchmntText') or '').strip()
    if not url or not text or published is None:
        return None
    return {'published_at': published.isoformat(),
            'published_display': published.strftime('%d-%b-%Y %H:%M IST'),
            'category': str(row.get('desc') or '').strip() or 'Announcement',
            'text': re.sub(r'\s+', ' ', text)[:400],
            'url': url,
            'host': urllib.parse.urlparse(url).netloc,
            'attachment_size': str(row.get('fileSize') or '').strip() or None}


def collect(symbol, days=45, limit=12, now=None):
    """Dated filings for one cash-equity symbol, newest first."""
    bare = _bare(symbol)
    if not bare:
        raise AnnouncementError('no symbol to look up')
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    floor = now - dt.timedelta(days=days)
    items, skipped = [], 0
    for row in _rows(bare):
        if str(row.get('symbol') or '').strip().upper().rstrip('+') != bare:
            continue
        item = _item(row)
        if item is None:
            skipped += 1
            continue
        published = dt.datetime.fromisoformat(item['published_at'])
        if published < floor:
            continue
        items.append(item)
    items.sort(key=lambda r: r['published_at'], reverse=True)
    return {'symbol': bare, 'source': SOURCE, 'source_page': PLATFORM,
            'as_of': now.isoformat(), 'days': days, 'requested_limit': limit,
            'fetched_at': dt.datetime.now(IST).isoformat(),
            'items': items[:limit], 'count_in_window': len(items),
            'unreadable_rows': skipped,
            'note': ('Exchange disclosure list only. Nothing here is parsed into numbers; '
                     'the filing text and PDF link are the evidence.')}


def cached(symbol, now=None):
    path = STATE / (slug(symbol) + '.json')
    if not path.is_file():
        return None
    try:
        with open(path, encoding='utf-8') as handle:
            doc = json.load(handle)
    except (ValueError, OSError):
        return None
    stamp = doc.get('fetched_at')
    if not stamp:
        return None
    try:
        age = dt.datetime.now(IST) - dt.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if age.total_seconds() > TTL:
        return None
    doc['cache_hit'] = True
    return doc


def store(doc):
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / (slug(doc['symbol']) + '.json')
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def fetch(symbol, days=45, limit=12, refresh=False, now=None):
    if not refresh:
        stored = cached(symbol, now)
        if stored:
            return stored
    doc = collect(symbol, days=days, limit=limit, now=now)
    doc['cache_hit'] = False
    store(doc)
    return doc


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description='NSE issuer announcements (read-only)')
    parser.add_argument('symbol')
    parser.add_argument('--days', type=int, default=45)
    parser.add_argument('--limit', type=int, default=12)
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args(argv)
    doc = fetch(args.symbol, days=args.days, limit=args.limit, refresh=args.refresh)
    print('\n== announcements | %s ==' % doc['symbol'])
    print('source: %s' % doc['source'])
    print('window: last %d days | %d filings in it | fetched %s%s'
          % (doc['days'], doc['count_in_window'], doc['fetched_at'],
             ' (cached)' if doc.get('cache_hit') else ''))
    if not doc['items']:
        print('  NSE listed no disclosure for %s in the last %d days.'
              % (doc['symbol'], doc['days']))
    for row in doc['items']:
        print('  %-22s %-34s %s' % (row['published_display'], row['category'][:34], row['url']))
        print('  %-22s %s' % ('', row['text'][:120]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
