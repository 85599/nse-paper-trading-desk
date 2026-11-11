"""Live detail for one symbol: fresh NSE bars, the desk's own indicators, and the option tape.

The saved workspace files answer a search instantly, but they only cover the 30 names
the signal board last read. This module goes to the same public NSE endpoints for
whatever the user typed -- RELIANCE, VEDL, NIFTY 50 or NIFTY 24000 CE -- computes the
identical indicator set `signals.py` uses, and caches the result per symbol. Nothing is
invented when a feed refuses: the section reports the failure text instead.
"""
import datetime as dt
import gzip
import json
import math
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor' / 'day-trader-skill-codex'
WORKSPACE = ROOT / 'workspace'
CACHE = WORKSPACE / 'state' / 'details'
CONFIG = ROOT / 'config'
DIRECTORY = CONFIG / 'instruments' / 'NSE.json.gz'
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))
sys.path.insert(0, str(ROOT / 'scripts'))

import announcements  # noqa: E402
import financials  # noqa: E402
import lookup  # noqa: E402
import signals  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CACHE_SECONDS = 900
PAISE = 100.0

SOURCE = ('NSE chart host (daily + 5m bars), NSE option-chain-v3 where a chain exists, '
          'cached Upstox instrument directory for lot and tick. Public endpoints; provider terms apply.')
NOTES = (
    'Detail is a read of the tape, not an order. A signal becomes a paper ticket only through '
    'the engine: entry, exit, budget, risk, then your own consent reply.',
    'Option geometry (lot, tick, premium for one lot, notional) comes from the cached instrument '
    'directory and is context: the desk is configured long-only cash and the engine has no option '
    'exit model, so no premium here is sized or ticketed.',
)


def slug(text):
    """Filesystem-safe cache key for a query."""
    cleaned = re.sub(r'[^A-Z0-9]+', '-', str(text or '').upper().strip())
    return cleaned.strip('-')[:48] or 'empty'


def path_for(text):
    return CACHE / ('%s.json' % slug(text))


def cached(text, now=None):
    """The stored detail doc while it is young enough to quote."""
    doc = lookup._load(path_for(text))
    if not doc:
        return None
    stamp, age, stale = lookup._stamp(doc)
    if stale or (age or 0) > CACHE_SECONDS:
        return None
    doc['cache_hit'] = True
    return doc


def _write(doc, key):
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / ('%s.json' % key)
    tmp = target.with_suffix('.part')
    tmp.write_text(json.dumps(doc, indent=2, default=str), encoding='utf-8')
    import os
    os.replace(tmp, target)
    return target


# ---------- instrument directory: names, ticks and lot sizes ----------

_DIRECTORY = None


def directory():
    """Parse the cached instrument directory once per process."""
    global _DIRECTORY
    if _DIRECTORY is not None:
        return _DIRECTORY
    out = {'equity': {}, 'contracts': {}}
    if not DIRECTORY.is_file():
        _DIRECTORY = out
        return out
    try:
        with gzip.open(DIRECTORY) as handle:
            rows = json.load(handle)
    except (OSError, ValueError):
        _DIRECTORY = out
        return out
    for row in rows if isinstance(rows, list) else []:
        segment = row.get('segment')
        symbol = str(row.get('trading_symbol') or '').strip().upper()
        if segment in ('NSE_EQ', 'NSE_COM') and row.get('instrument_type') == 'EQ' and symbol:
            out['equity'].setdefault(symbol, {
                'name': row.get('name') or row.get('short_name'),
                'isin': row.get('isin'),
                'tick_inr': (row.get('tick_size') or 0) / PAISE,
                'exchange': row.get('exchange'),
                'lot_size': row.get('lot_size'),
                'freeze_quantity': row.get('freeze_quantity'),
            })
        elif segment == 'NSE_FO' and row.get('instrument_type') in ('CE', 'PE'):
            expiry = row.get('expiry')
            try:
                when = dt.datetime.fromtimestamp(float(expiry) / 1000, IST).date().isoformat()
            except (TypeError, ValueError, OSError):
                continue
            underlying = str(row.get('asset_symbol') or '').upper()
            strike = row.get('strike_price')
            if not underlying or strike is None:
                continue
            out['contracts']['%s|%s|%s|%s' % (underlying, when, round(float(strike), 2),
                                              row.get('instrument_type'))] = {
                'trading_symbol': symbol,
                'lot_size': row.get('lot_size'),
                'minimum_lot': row.get('minimum_lot'),
                'tick_inr': (row.get('tick_size') or 0) / PAISE,
                'expiry': when,
            }
    _DIRECTORY = out
    return out


def equity_identity(symbol):
    """Registry tick first (the desk's validated source), directory name second."""
    base = re.sub(r'\.(NS|BO)$', '', str(symbol).upper())
    import registry
    listed = directory()['equity'].get(base)
    record = dict(listed or {})
    record['symbol'] = base
    ticks = registry.tick_lookup()
    tick = ticks.get(base + '.NS')
    if tick:
        record['tick_inr'] = tick
        record['tick_source'] = 'workspace tick registry (config/security-master.csv)'
    elif record.get('tick_inr'):
        record['tick_source'] = 'Upstox instrument directory cache'
    # `record` always carries the symbol, so the miss has to be judged on what was found.
    if not listed and not tick:
        return record, ('neither the instrument directory nor the tick registry has a '
                        'cash-equity record for %s' % base)
    return record, None


def _nse_expiry(expiry):
    """Chain expiries read 09-Nov-2026; the directory stores ISO dates."""
    try:
        return dt.datetime.strptime(str(expiry), '%d-%b-%Y').date().isoformat()
    except (TypeError, ValueError):
        return str(expiry)


def contract_for(chain_symbol, expiry, strike, side):
    """Lot size and tick for one listed contract, matched on underlying/expiry/strike/side."""
    key = '%s|%s|%s|%s' % (str(chain_symbol).upper(), _nse_expiry(expiry), float(strike), side)
    found = directory()['contracts'].get(key)
    if found:
        return found, None
    return None, 'no contract in the cached instrument directory for %s' % key


def suggestions(name, limit=6):
    """Near names from the cached directory, for the case where NSE has no such scrip.

    NSE's own symbol search is fuzzy and capped, so a delisted or renamed name
    (TATAMOTORS after the demerger) simply returns nothing. A bare refusal reads
    like a desk failure; the closest live names do not.
    """
    equity = directory()['equity']
    upper = str(name or '').upper().strip()
    if not upper or upper in equity:
        return []
    stem = upper[:5]
    hits = [symbol for symbol, record in equity.items()
            if stem in symbol or stem in str(record.get('name') or '').upper().replace(' ', '')]
    return sorted(hits)[:limit]


def _is_index(picked):
    return (picked['kind'] == 'index'
            or (picked['kind'] == 'option' and picked.get('option_kind') == 'index'))


# ---------- bars ----------

def _partial(bars, as_of):
    clock = as_of.astimezone(IST)
    return (bool(bars)
            and bars[-1].time.astimezone(IST).date() == clock.date()
            and clock.time() < dt.time(15, 30))


def daily_bars(picked, as_of, days=100):
    """Daily OHLCV for the parsed target: equity through the provider, index through the chart host."""
    from india.nse import NSEProvider
    name = str(picked.get('symbol') or '').upper()
    if _is_index(picked):
        import indices
        symbol, token = indices.resolve(name)
        return indices.daily_candles(symbol, token, as_of, days=min(days, 120)), symbol
    provider = NSEProvider(as_of, days=min(days, 120))
    return provider.history(name, '1d'), name


def intraday_bars(picked, as_of, days=7):
    from india.nse import NSEProvider
    name = str(picked.get('symbol') or '').upper()
    if _is_index(picked):
        import indices
        symbol, token = indices.resolve(name)
        return indices.candles(symbol, token, 'Index', as_of, days=days), symbol
    provider = NSEProvider(as_of, days=days)
    return provider.history(name, '5m'), name


def window_stats(bars, partial=False):
    """Where the last close sits inside the fetched window, with the dates attached."""
    if not bars:
        return None
    highs = max(bars, key=lambda b: b.high)
    lows = min(bars, key=lambda b: b.low)
    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    last = closes[-1]
    # a forming bar has a fraction of a session behind it, so volume is read off
    # the last completed one -- the same bar the indicators use
    counted = bars[:-1] if (partial and len(bars) > 1) else bars
    avg20 = sum(volumes[-21:-1]) / 20 if len(volumes) > 21 else (sum(volumes) / len(volumes))
    return {
        'sessions': len(bars),
        'from': bars[0].time.astimezone(IST).date().isoformat(),
        'to': bars[-1].time.astimezone(IST).date().isoformat(),
        'window_high': round(highs.high, 2),
        'window_high_date': highs.time.astimezone(IST).date().isoformat(),
        'window_low': round(lows.low, 2),
        'window_low_date': lows.time.astimezone(IST).date().isoformat(),
        'pct_from_window_high': round((last / highs.high - 1) * 100, 2) if highs.high else None,
        'pct_from_window_low': round((last / lows.low - 1) * 100, 2) if lows.low else None,
        'avg_volume_20': int(avg20) if avg20 else None,
        'volume_bar_date': counted[-1].time.astimezone(IST).date().isoformat(),
        'last_volume': int(counted[-1].volume),
        'volume_x_20': round(counted[-1].volume / avg20, 2) if avg20 else None,
        'return_5_pct': round((last / closes[-6] - 1) * 100, 2) if len(closes) > 5 else None,
        'return_20_pct': round((last / closes[-21] - 1) * 100, 2) if len(closes) > 20 else None,
    }


def daily_section(picked, as_of, days, window):
    bars, symbol = daily_bars(picked, as_of, days)
    if len(bars) < 30:
        raise ValueError('only %d daily bars from NSE -- ADX, MACD and EMA50 need about 35' % len(bars))
    forming = _partial(bars, as_of)
    indicators = signals.indicators(bars, partial=forming)
    label, score, reasons, vetoes = signals.verdict(indicators, window=window, now=as_of)
    return {
        'symbol': symbol,
        'timeframe': '1d',
        'bar_as_of': bars[-1].time.astimezone(IST).isoformat(),
        'bar_forming': forming,
        'indicators': indicators,
        'verdict': label,
        'score': score,
        'reasons': reasons,
        'vetoes': vetoes,
        'actionable': not vetoes,
        'realized_vol_pct': signals.realized_volatility(bars),
        'window': window_stats(bars, partial=forming),
    }


def intraday_section(picked, as_of):
    import indices
    bars, symbol = intraday_bars(picked, as_of)
    if not bars:
        raise ValueError('no 5m bars returned')
    stats = indices.session_stats(bars, as_of)
    if not stats:
        raise ValueError('no 5m bar belongs to this session')
    stats['symbol'] = symbol
    stats['bars_today'] = len([b for b in bars
                               if b.time.astimezone(IST).date() == as_of.astimezone(IST).date()])
    stats['intraday_realized_vol_pct'] = signals.realized_volatility(bars, bars_per_year=252 * 48)
    return stats


# ---------- option chain ----------

def _fetch_chain(symbol, expiry=None, prefer=None):
    """Equity names get the Equity segment first; index names let optionchain choose."""
    import optionchain
    if prefer == 'equity':
        try:
            return optionchain.fetch(symbol, expiry=expiry, kind='Equity')
        except ValueError:
            return optionchain.fetch(symbol, expiry=expiry)
    return optionchain.fetch(symbol, expiry=expiry)


def chain_section(picked, as_of, expiry=None, strike=None, side=None, chain_symbol=None,
                  index_row=None):
    symbol = str(chain_symbol or picked.get('chain_symbol') or '').upper()
    if not symbol:
        raise ValueError('nothing to look up in the chain')
    prefer = 'equity' if (picked.get('option_kind') == 'equity' or picked['kind'] == 'equity') else None
    doc = _fetch_chain(symbol, expiry=expiry, prefer=prefer)
    out = {'symbol': doc['symbol'], 'expiry': doc['expiry'], 'segment': doc.get('kind'),
           'underlying': doc['underlying'], 'atm_strike': doc['atm_strike'],
           'strikes': doc['strikes'], 'priced_strikes': doc['priced_strikes'],
           'pcr_oi': doc['pcr_oi'], 'pcr_chg_oi': doc.get('pcr_chg_oi'),
           'call_oi': doc['call_oi'], 'put_oi': doc['put_oi'],
           'call_oi_change': doc['call_oi_change'], 'put_oi_change': doc['put_oi_change'],
           'resistance_oi': doc['resistance_oi'], 'support_oi': doc['support_oi'],
           'resistance_buildup': doc['resistance_buildup'], 'support_buildup': doc['support_buildup'],
           'expiry_dates': doc.get('expiry_dates') or [], 'nse_timestamp': doc['timestamp'],
           'as_of': doc['as_of'], 'notes': doc['notes'],
           'context': signals.option_context(doc)}
    rows = doc['rows']
    out['bias'] = signals.option_read(doc, index_row)
    if strike is None:
        out['near_atm'] = [{
            'strike': r['strike'], 'CE': r['CE'], 'PE': r['PE'],
            'contract': (contract_for(doc['symbol'], doc['expiry'], r['strike'], 'CE')[0]
                         or contract_for(doc['symbol'], doc['expiry'], r['strike'], 'PE')[0]),
        } for r in (doc['near_atm'] or rows)[:9]]
        return out
    leg = next((r for r in rows if r.get('strike') == float(strike)), None)
    if leg is None:
        nearest = min(rows, key=lambda r: abs((r.get('strike') or 0) - float(strike)))
        raise ValueError('%s %s is not a listed strike for %s %s; nearest is %s'
                         % (symbol, strike, doc['expiry'], side or '', nearest.get('strike')))
    wanted = (side or 'CE').upper()
    other = 'PE' if wanted == 'CE' else 'CE'
    info, miss = contract_for(doc['symbol'], doc['expiry'], float(strike), wanted)
    underlying = doc['underlying']
    lot = (info or {}).get('lot_size')
    premium = (leg.get(wanted) or {}).get('ltp') or 0
    out['strike'] = float(strike)
    out['side'] = wanted
    out['legs'] = {key: leg.get(key) for key in ('CE', 'PE')}
    out['moneyness'] = (('ITM' if (float(strike) < underlying) == (wanted == 'CE') else 'OTM')
                        if underlying else None)
    out['distance_from_atm'] = (round(float(strike) - doc['atm_strike'], 2)
                                if doc.get('atm_strike') is not None else None)
    out['pct_from_underlying'] = (round((float(strike) / underlying - 1) * 100, 2)
                                  if underlying else None)
    out['contract'] = info
    out['contract_note'] = miss
    if lot and premium is not None:
        out['cash_for_one_lot'] = round(lot * premium, 2)
        out['notional_for_one_lot'] = (round(lot * underlying, 2) if underlying else None)
        out['tick_value_one_lot'] = (round(lot * (info or {}).get('tick_inr', 0), 2)
                                     if info else None)
    return out


def find_strike(picked, as_of, attempts=4, index_row=None):
    """Search the listed expiries for a strike instead of answering 'not on file'."""
    import optionchain
    symbol = str(picked.get('chain_symbol') or '').upper()
    strike, side = picked.get('strike'), picked.get('side')
    errors = []
    available = optionchain.expiries(symbol)
    if not available:
        raise ValueError('NSE lists no expiries for %s' % symbol)
    wanted = picked.get('expiry')
    order = ([wanted] if wanted else []) + [e for e in available if e != wanted]
    for expiry in order[:attempts if wanted else attempts]:
        try:
            return chain_section(picked, as_of, expiry=expiry, strike=strike, side=side,
                                 chain_symbol=symbol, index_row=index_row)
        except ValueError as exc:
            errors.append('%s: %s' % (expiry, str(exc)[:140]))
            if 'is not a listed strike' not in str(exc):
                break
    raise ValueError(' | '.join(errors) or 'strike not found')


# ---------- sizing hint ----------

def sizing(daily, as_of, workspace=WORKSPACE):
    """What the desk's own cash and ₹500 cap allow, from the ATR stop the exit stage uses."""
    indicators = (daily or {}).get('indicators') or {}
    close, atr_value = indicators.get('close'), indicators.get('atr_14')
    if not close or not atr_value:
        return None
    config = lookup._load(CONFIG / 'desk.json') or {}
    portfolio = lookup._load(Path(workspace) / 'portfolio.json') or {}
    cash = portfolio.get('cash')
    cap = config.get('max_loss_per_trade_inr') or config.get('risk_per_trade_inr') or 500
    stop_distance = round(atr_value, 2)
    by_risk = int(math.floor(cap / stop_distance)) if stop_distance else 0
    by_cash = int(math.floor(cash / close)) if cash else 0
    shares = min(by_risk, by_cash)
    return {
        'stop_rule': 'ATR(14) = %s' % stop_distance,
        'risk_cap_inr': cap,
        'cash_inr': cash,
        'shares_by_risk_cap': by_risk,
        'shares_by_cash': by_cash,
        'shares': shares if shares > 0 else 0,
        'position_value_inr': round(shares * close, 2) if shares > 0 else 0,
        'risk_at_stop_inr': round(shares * stop_distance, 2) if shares > 0 else 0,
        'note': 'Indicative only -- the engine budget and risk stages decide the real ticket.',
    }


# ---------- assemble ----------

def run(text, as_of=None, days=100, refresh=False, with_chain=True, workspace=WORKSPACE,
        window=None):
    picked = lookup.parse(text)
    key = slug(text)
    if not refresh:
        stored = cached(text)
        if stored:
            return stored
    as_of = (as_of or dt.datetime.now(IST)).astimezone(IST)
    if window is None:
        config = lookup._load(CONFIG / 'desk.json') or {}
        window = '%s-%s' % (config.get('no_entries_before'), config.get('no_entries_after'))
    doc = {'query': str(text or ''), 'parsed': picked, 'as_of': as_of.isoformat(),
           'paper_only': True, 'execution_enabled': False, 'source': SOURCE,
           'directory_cached': (dt.date.fromtimestamp(DIRECTORY.stat().st_mtime).isoformat()
                                 if DIRECTORY.is_file() else None),
           'fetched_at': dt.datetime.now(IST).isoformat(), 'cache_hit': False,
           'sections': {}, 'absent': {}, 'notes': list(NOTES)}
    if picked['kind'] == 'empty':
        doc['note'] = 'Type a symbol (RELIANCE), an index (NIFTY 50) or a strike (NIFTY 24000 CE).'
        return doc

    target_symbol = picked.get('symbol') or text
    if picked['kind'] == 'index' or (picked['kind'] == 'option'
                                     and picked.get('option_kind') == 'index'):
        doc['identity'] = {'symbol': target_symbol, 'exchange': 'NSE', 'kind': 'index'}
    else:
        identity, miss = equity_identity(target_symbol)
        if miss:
            doc['absent']['identity'] = miss
        identity['kind'] = picked['kind']
        doc['identity'] = identity

    try:
        doc['sections']['daily'] = daily_section(picked, as_of, days, window)
    except Exception as exc:            # noqa: BLE001 -- any feed failure is reported, never guessed
        doc['absent']['daily'] = '%s: %s' % (exc.__class__.__name__, str(exc)[:200])

    try:
        doc['sections']['intraday'] = intraday_section(picked, as_of)
    except Exception as exc:            # noqa: BLE001
        doc['absent']['intraday'] = '%s: %s' % (exc.__class__.__name__, str(exc)[:200])

    index_row = doc['sections'].get('daily')
    if picked['kind'] == 'option':
        try:
            doc['sections']['option'] = find_strike(picked, as_of, index_row=index_row)
        except Exception as exc:        # noqa: BLE001
            doc['absent']['option'] = str(exc)[:300]
    elif with_chain:
        try:
            doc['sections']['chain'] = chain_section(picked, as_of, index_row=index_row)
        except Exception as exc:        # noqa: BLE001
            doc['absent']['chain'] = ('NSE lists no option chain for %s (%s)'
                                      % (picked.get('chain_symbol'), str(exc)[:140]))

    cash_underlying = (picked['kind'] == 'equity'
                       or (picked['kind'] == 'option' and picked.get('option_kind') == 'equity'))
    if cash_underlying:
        try:
            doc['sections']['announcements'] = announcements.fetch(
                target_symbol, days=45, limit=8, refresh=refresh, now=as_of)
        except Exception as exc:        # noqa: BLE001 -- a refused archive is reported, never guessed
            doc['absent']['announcements'] = str(exc)[:220]
        try:
            doc['sections']['financials'] = financials.fetch(
                target_symbol, quarters=1, refresh=refresh, now=as_of)
        except Exception as exc:        # noqa: BLE001 -- the exchange refusing to have a filing is the answer
            doc['absent']['financials'] = str(exc)[:220]
    doc['sizing'] = (sizing(doc['sections'].get('daily'), as_of, workspace)
                     if cash_underlying else None)
    if doc['sizing'] and picked['kind'] == 'option':
        doc['sizing']['applies_to'] = ('the %s cash equity this option is written on, not the premium'
                                       % target_symbol)
    if not cash_underlying:
        doc['sizing_note'] = ('no cash sizing -- %s is read as an %s; this desk sizes whole-share NSE cash '
                              'equity and never an index level or an option premium'
                              % (target_symbol, picked['kind']))
    if not doc['sections'] and picked['kind'] == 'equity':
        bare = re.sub(r'\.(NS|BO)$', '', str(target_symbol).upper())
        near = suggestions(bare)
        if near:
            doc['suggestions'] = near
            doc['absent']['suggest'] = ('NSE lists no cash-equity scrip named %s -- it is either delisted '
                                        'or renamed. Closest names on file: %s.'
                                        % (bare, ', '.join(near)))
    doc['answered'] = bool(doc['sections'])
    if doc['answered']:
        _write(doc, key)
    return doc


def _fmt(value, width=11, decimals=2):
    if value is None:
        return ' ' * (width - 3) + 'n/a'
    if isinstance(value, (int, float)):
        return ('{:%d,.%df}' % (width, decimals)).format(value)
    return str(value).rjust(width)


def print_detail(doc):
    picked = doc['parsed']
    print('detail %s -> %s (%s)' % (doc['query'], picked['kind'], doc.get('as_of')))
    identity = doc.get('identity') or {}
    if identity:
        print('  %-8s %s | %s | tick Rs %s | lot %s | %s' % (
            'symbol:', identity.get('symbol') or '-', identity.get('name') or identity.get('kind') or '-',
            identity.get('tick_inr') if identity.get('tick_inr') is not None else 'n/a',
            (identity.get('lot_size') if identity.get('lot_size') is not None else 'n/a'),
            identity.get('tick_source') or identity.get('exchange') or '-'))
    for label, note in doc['absent'].items():
        print('  unavailable %-9s %s' % (label + ':', note[:150]))
    daily = doc['sections'].get('daily')
    if daily:
        i = daily['indicators']
        print('\nDAILY  %s | %s bars | bar as of %s%s' % (
            daily['symbol'], (daily.get('window') or {}).get('sessions'), daily['bar_as_of'][:16],
            ' (today\'s bar still forming)' if daily['bar_forming'] else ''))
        print('  %s %s (score %d)%s' % (daily['symbol'], daily['verdict'], daily['score'],
                                        '' if daily['actionable'] else ' -- blocked'))
        print('  close %s  chg %s%%  ADX %s  +DI %s  -DI %s  RSI %s  ATR %s (%s%%)' % (
            _fmt(i.get('close')), _fmt(i.get('change_pct'), 6), _fmt(i.get('adx'), 5, 1),
            _fmt(i.get('di_plus'), 5, 1), _fmt(i.get('di_minus'), 5, 1), _fmt(i.get('rsi_14'), 5, 1),
            _fmt(i.get('atr_14'), 7), i.get('atr_pct')))
        macd = i.get('macd') or {}
        print('  EMA20 %s  EMA50 %s  20d high %s  20d low %s  MACDh %s  vol x %s  rv %s%%' % (
            _fmt(i.get('ema20')), _fmt(i.get('ema50')), _fmt(i.get('high_20')), _fmt(i.get('low_20')),
            _fmt(macd.get('histogram'), 8, 2), _fmt(i.get('volume_x'), 5),
            _fmt(daily.get('realized_vol_pct'), 5, 1)))
        window = daily.get('window') or {}
        if window:
            print('  window %s..%s: high %s (%s) %s%% away | low %s (%s) %s%% away | avg vol %s' % (
                window.get('from'), window.get('to'), _fmt(window.get('window_high')),
                window.get('window_high_date'), window.get('pct_from_window_high'),
                _fmt(window.get('window_low')), window.get('window_low_date'),
                window.get('pct_from_window_low'), _fmt(window.get('avg_volume_20'), 12, 0)))
        for reason in daily['reasons']:
            print('    - %s' % reason)
        for veto in daily['vetoes']:
            print('    ! %s' % veto)
    intraday = doc['sections'].get('intraday')
    if intraday:
        print('\nINTRADAY 5m  %s | %s bars today | last %s at %s' % (
            intraday.get('symbol'), intraday.get('bars_today'), intraday.get('last'),
            str(intraday.get('as_of'))[:16]))
        print('  open %s  high %s  low %s  range %s%%  prev close %s  gap %s%%' % (
            _fmt(intraday.get('open'), 9), _fmt(intraday.get('day_high'), 9),
            _fmt(intraday.get('day_low'), 9), _fmt(intraday.get('range_pct'), 6),
            _fmt(intraday.get('prev_close'), 9), _fmt(intraday.get('gap_pct'), 6)))
        orb = intraday.get('orb')
        if orb:
            print('  15m opening range %s-%s (mid %s, width %s%%) -- price is %s, %s up / %s down to break' % (
                orb['high'], orb['low'], orb['mid'], orb['width_pct'], intraday.get('state'),
                intraday.get('to_break_up'), intraday.get('to_break_down')))
    option = doc['sections'].get('option')
    if option:
        leg = (option.get('legs') or {}).get(option['side']) or {}
        print('\nOPTION  %s %s %s %s | underlying %s | ATM %s | %s%% from spot' % (
            option['symbol'], '{:,.0f}'.format(option['strike']), option['side'],
            option['moneyness'], option['underlying'], option['atm_strike'],
            option.get('pct_from_underlying')))
        print('  queried leg: ltp %s  iv %s  oi %s  chg oi %s  volume %s  bid/ask %s/%s' % (
            leg.get('ltp'), leg.get('iv'), leg.get('oi'), leg.get('chg_oi'), leg.get('volume'),
            leg.get('bid'), leg.get('ask')))
        other = (option.get('legs') or {}).get('PE' if option['side'] == 'CE' else 'CE') or {}
        print('  other side : ltp %s  iv %s  oi %s  chg oi %s' % (
            other.get('ltp'), other.get('iv'), other.get('oi'), other.get('chg_oi')))
        contract = option.get('contract') or {}
        if contract:
            print('  contract: %s | lot %s | tick Rs %s | 1 lot costs Rs %s premium on Rs %s notional' % (
                contract.get('trading_symbol'), contract.get('lot_size'), contract.get('tick_inr'),
                option.get('cash_for_one_lot'), option.get('notional_for_one_lot')))
        else:
            print('  contract: %s' % option.get('contract_note'))
        print('  chain: PCR %s | walls %s call / %s put | %s/%s strikes priced | expiries %s' % (
            option['pcr_oi'], option['resistance_oi'], option['support_oi'],
            option['priced_strikes'], option['strikes'], ', '.join(option['expiry_dates'][:4])))
        bias = option.get('bias') or {}
        if bias:
            print('  underlying bias: %s (%s) -- %s' % (
                bias.get('index'), bias.get('verdict'), bias.get('score')))
            for reason in (bias.get('reasons') or [])[:5]:
                print('    - %s' % reason)
            for veto in (bias.get('vetoes') or [])[:2]:
                print('    ! %s' % veto)
    chain = doc['sections'].get('chain')
    if chain:
        print('\nCHAIN  %s %s | NSE type %s | underlying %s | ATM %s' % (
            chain['symbol'], chain['expiry'], chain.get('segment'), chain['underlying'],
            chain['atm_strike']))
        print('  PCR by OI %s | OI %s calls / %s puts | today %s call / %s put | walls %s/%s' % (
            chain['pcr_oi'], chain['call_oi'], chain['put_oi'], chain['call_oi_change'],
            chain['put_oi_change'], chain['resistance_oi'], chain['support_oi']))
        print('  %10s %9s %7s %10s | %9s %7s %10s' % (
            'strike', 'CE ltp', 'CE iv', 'CE oi', 'PE ltp', 'PE iv', 'PE oi'))
        for row in chain.get('near_atm') or []:
            call, put = row['CE'], row['PE']
            print('  %10s %9s %7s %10s | %9s %7s %10s%s' % (
                '{:,.0f}'.format(row['strike']), call['ltp'], call['iv'], call['oi'],
                put['ltp'], put['iv'], put['oi'],
                '  <= ATM' if row['strike'] == chain['atm_strike'] else ''))
    filings = doc['sections'].get('announcements')
    if filings:
        print('\nFILINGS  %s | %s' % (filings['symbol'], filings['source']))
        print('  %d disclosures in the last %s days (fetched %s%s)'
              % (filings['count_in_window'], filings['days'], filings['fetched_at'],
                 ', cached' if filings.get('cache_hit') else ''))
        for row in filings['items']:
            print('  %-21s %-30s %s' % (row['published_display'], row['category'][:30], row['url']))
            print('  %-21s %s' % ('', row['text'][:110]))
    filed = doc['sections'].get('financials')
    if filed:
        latest = filed['latest']
        prov = latest['filing']
        print('\nFILED NUMBERS  %s -- %s | %s' % (
            filed['symbol'], prov.get('scope') or filed['scope'], filed['source']))
        print('  period ended %s | filed %s (%s, %s) | %d figures read | fetched %s%s' % (
            prov.get('period_end_display') or 'not stated',
            prov.get('published_display') or 'time not stated',
            prov.get('filing_kind') or 'filing', prov.get('audited') or 'audit status not stated',
            latest.get('fact_count', 0), str(filed.get('fetched_at'))[:16],
            ' (cache)' if filed.get('cache_hit') else ''))
        print('  instance     %s' % prov['url'])
        for block in latest['blocks']:
            print('  %s' % block['title'])
            for row in block['figures']:
                print('    %-38s %-14s (as filed %s %s)' % (
                    row['label'], row['display'], row['value_as_filed'], row['unit'] or 'no unit stated'))
    sizing = doc.get('sizing')
    if sizing:
        print('\nSIZING  %s | %s shares fits a Rs %s cap and Rs %s cash | position Rs %s' % (
            sizing['stop_rule'], sizing['shares'], sizing['risk_cap_inr'], sizing['cash_inr'],
            sizing['position_value_inr']))
        print('  %s' % sizing['note'])
        if sizing.get('applies_to'):
            print('  applies to %s' % sizing['applies_to'])
    if doc.get('sizing_note'):
        print('\nSIZING  %s' % doc['sizing_note'])
    if not doc['sections']:
        print('\nnothing came back for %s -- every source above refused.' % doc['query'])
    for note in doc.get('notes') or []:
        print('\n%s' % note)


if __name__ == '__main__':
    import argparse
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Live per-symbol detail (paper desk, read-only)')
    parser.add_argument('symbol', help='RELIANCE, NIFTY 50, NIFTY 24000 CE, BANKNIFTY 03-Dec-2026 55800 PE')
    parser.add_argument('--days', type=int, default=100)
    parser.add_argument('--refresh', action='store_true', help='ignore the cache and re-fetch')
    parser.add_argument('--no-chain', action='store_true', help='skip the option chain for equities')
    args = parser.parse_args()
    print_detail(run(args.symbol, days=args.days, refresh=args.refresh, with_chain=not args.no_chain))
