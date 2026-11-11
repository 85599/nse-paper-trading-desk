"""Which stocks sit inside a Nifty index, read from NSE's own constituent files.

NSE publishes one CSV per index under archives.nseindia.com/content/indices/. Those
files carry membership only -- symbol, company, industry, ISIN, series -- never a
price, so this module answers "what is in the index" and nothing else.

Nine of the indices traders ask for have no published file at all (the factor and
strategy screens, Midcap Select, Microcap 250, Total Market, Financial Services,
Private Bank). For those the desk says the file does not exist rather than copying a
constituent list from a third party: an invented universe would silently drive a scan
and every verdict that follows it.
"""
import csv
import datetime as dt
import gzip
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / 'workspace'
STORE = WORKSPACE / 'universes'
ARCHIVE = 'https://archives.nseindia.com/content/indices/'
DAILY_LIST = ROOT / 'config' / 'universe-liquid.txt'
ORB_LIST = ROOT / 'config' / 'watchlist-orb.txt'
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
# A constituent list changes at the monthly rebalance, not intraday, so a day of
# reuse is honest and keeps one click from hammering NSE.
MAX_AGE_SECONDS = 86400
HEADERS = {'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'),
           'Accept': 'text/csv,*/*'}

# label -> NSE file stem, in the order the desk shows them. A None slug means NSE
# publishes no constituent file, and the UI has to say that instead of scanning.
INDEXES = [
    ('Nifty 50', 'ind_nifty50list'),
    ('Nifty Next 50', 'ind_niftynext50list'),
    ('Nifty 100', 'ind_nifty100list'),
    ('Nifty 200', 'ind_nifty200list'),
    ('Nifty 500', 'ind_nifty500list'),
    ('Nifty Total Market', None),
    ('Nifty Midcap 50', 'ind_niftymidcap50list'),
    ('Nifty Midcap 100', 'ind_niftymidcap100list'),
    ('Nifty Midcap 150', 'ind_niftymidcap150list'),
    ('Nifty Midcap Select', None),
    ('Nifty Smallcap 50', 'ind_niftysmallcap50list'),
    ('Nifty Smallcap 100', 'ind_niftysmallcap100list'),
    ('Nifty Smallcap 250', 'ind_niftysmallcap250list'),
    ('Nifty Microcap 250', None),
    ('Nifty LargeMidcap 250', 'ind_niftylargemidcap250list'),
    ('Nifty MidSmallcap 400', 'ind_niftymidsmallcap400list'),
    ('Nifty Bank', 'ind_niftybanklist'),
    ('Nifty Financial Services', None),
    ('Nifty IT', 'ind_niftyitlist'),
    ('Nifty Auto', 'ind_niftyautolist'),
    ('Nifty FMCG', 'ind_niftyfmcglist'),
    ('Nifty Pharma', 'ind_niftypharmalist'),
    ('Nifty Metal', 'ind_niftymetallist'),
    ('Nifty Energy', 'ind_niftyenergylist'),
    ('Nifty Infra', 'ind_niftyinfralist'),
    ('Nifty PSU Bank', 'ind_niftypsubanklist'),
    ('Nifty Private Bank', None),
    ('Nifty Realty', 'ind_niftyrealtylist'),
    ('Nifty Media', 'ind_niftymedialist'),
    ('Nifty Commodities', 'ind_niftycommoditieslist'),
    ('Nifty Consumption', 'ind_niftyconsumptionlist'),
    ('Nifty MNC', 'ind_niftymnclist'),
    ('Nifty PSE', 'ind_niftypselist'),
    ('Nifty Service Sector', 'ind_niftyservicelist'),
    ('Nifty Low Volatility 50', None),
    ('Nifty Alpha 50', None),
    ('Nifty Momentum 30', None),
    ('Nifty Quality 30', None),
    ('Nifty Dividend Opportunities 50', 'ind_niftydivopp50list'),
]
NO_FILE = ('NSE publishes no constituent file for this index, so the desk will not '
           'guess its stocks. Search a stock inside it directly instead.')


def labels():
    return [label for label, _ in INDEXES]


def resolve(query):
    """Match what the user typed to one index label, tolerantly."""
    wanted = ' '.join(str(query or '').upper().split()).replace('NIFTY ', 'NIFTY')
    for label, slug in INDEXES:
        if ' '.join(label.upper().split()).replace('NIFTY ', 'NIFTY') == wanted:
            return label, slug
    return None, None


def _read_list(path):
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.split('#')[0].strip()
        if line:
            out.add(line.split('|')[0].strip().upper())
    return out


def _download(slug):
    request = urllib.request.Request(ARCHIVE + slug + '.csv', headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    if body[:2] == b'\x1f\x8b':
        body = gzip.decompress(body)
    text = body.decode('utf-8-sig', errors='replace')
    if text.lstrip()[:1] == '<':
        raise ValueError('NSE served an HTML page instead of the constituent file')
    return list(csv.DictReader(io.StringIO(text)))


def _rows(records, on_desk):
    rows = []
    for record in records:
        bare = str(record.get('Symbol') or '').strip().upper()
        if not bare or not str(record.get('ISIN Code') or '').strip():
            continue
        symbol = bare if '.' in bare else bare + '.NS'
        rows.append(dict(symbol=symbol, bare=bare,
                         name=str(record.get('Company Name') or '').strip(),
                         industry=str(record.get('Industry') or '').strip(),
                         series=str(record.get('Series') or '').strip(),
                         isin=str(record.get('ISIN Code') or '').strip(),
                         on_desk=symbol in on_desk or bare in on_desk))
    return rows


def cached(label):
    _, slug = resolve(label)
    path = STORE / (slug + '.json')
    if not slug or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return None


def age_seconds(doc):
    if not doc or not doc.get('as_of'):
        return None
    try:
        stamp = dt.datetime.fromisoformat(str(doc['as_of']))
    except ValueError:
        return None
    return max(0, int((dt.datetime.now(IST) - stamp).total_seconds()))


def constituents(label, refresh=False):
    """NSE's own list for one index, cached under workspace/universes/."""
    label, slug = resolve(label)
    if label is None:
        return {'status': 'NO_FILE', 'index': str(label or ''), 'note': NO_FILE}
    if not slug:
        return {'status': 'NO_FILE', 'index': label, 'note': NO_FILE}
    path = STORE / (slug + '.json')
    doc = cached(label)
    if doc and not refresh and (age_seconds(doc) or 0) < MAX_AGE_SECONDS:
        return dict(doc, from_cache=True)
    on_desk = _read_list(DAILY_LIST) | _read_list(ORB_LIST)
    try:
        rows = _rows(_download(slug), on_desk)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {'status': 'NO_FILE', 'index': label, 'note': NO_FILE}
        return {'status': 'UNAVAILABLE', 'index': label,
                'note': 'NSE answered HTTP %d for the %s constituent file.' % (exc.code, label)}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {'status': 'UNAVAILABLE', 'index': label,
                'note': 'The %s constituent file could not be read: %s' % (label, str(exc)[:120])}
    if not rows:
        return {'status': 'UNAVAILABLE', 'index': label,
                'note': 'NSE returned the %s file but it held no constituents.' % label}
    doc = {'status': 'OK', 'index': label, 'slug': slug, 'source': ARCHIVE + slug + '.csv',
           'as_of': dt.datetime.now(IST).isoformat(), 'count': len(rows),
           'on_desk': sum(1 for row in rows if row['on_desk']), 'rows': rows,
           'note': 'Membership only: NSE lists no prices in this file, so every number '
                   'here comes from the desk scan, not from it.'}
    STORE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1), encoding='utf-8')
    return dict(doc, from_cache=False)


def symbols(label, refresh=False):
    doc = constituents(label, refresh=refresh)
    return [row['symbol'] for row in doc.get('rows') or []] if doc.get('status') == 'OK' else []
