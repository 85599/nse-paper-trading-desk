"""Rule-based signals: indicators, a verdict with reasons, and the options tape.

Deterministic and inspectable -- every verdict lists the rules that fired and the
numbers behind them. This desk is long-only cash: SELL means "exit or stay out",
never a short sale. A signal is a lead, not an order; the engine's own entry,
exit, sizing and risk stages decide whether a paper ticket is even possible.
"""
import datetime as dt
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor' / 'day-trader-skill-codex'
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

from india.nse import NSEProvider  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CHAIN_TO_INDEX = {'NIFTY': 'NIFTY 50', 'BANKNIFTY': 'NIFTY BANK', 'FINNIFTY': 'NIFTY FIN SERVICE',
                  'MIDCPNIFTY': 'NIFTY MID SELECT', 'INDIAVIX': 'INDIA VIX'}
FORMULA_NOTE = ('ADX/DI and ATR use Wilder smoothing, RSI is Wilder 14 (the same math as the scanner), '
                'MACD is 12/26/9. Daily bars come from the same NSE chart host the scanner uses.')
VIX_NOTE = {
    'published': False,
    'reason': ('India VIX resolves on the chart host (scripcode 26011) but its series reads ~100x the '
               'published index value, and no keyless cross-check endpoint answered. Until the unit scale '
               'is verified this desk does not publish a VIX number.'),
    'substitutes': ['realized vol: annualized volatility measured from the same daily bars',
                    'expected move: the move the ATM straddle is actually pricing into expiry'],
}


def _wilder(values, period):
    """Wilder's smoothing: the first value is a simple average, then a running decay."""
    if len(values) < period:
        return []
    smoothed = [None] * (period - 1) + [sum(values[:period]) / period]
    for value in values[period:]:
        smoothed.append((smoothed[-1] * (period - 1) + value) / period)
    return smoothed


def ema(values, period):
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    out = [None] * (period - 1) + [sum(values[:period]) / period]
    for value in values[period:]:
        out.append(value * k + out[-1] * (1 - k))
    return out


def _true_range(bars):
    ranges = []
    for previous, current in zip(bars, bars[1:]):
        ranges.append(max(current.high - current.low,
                          abs(current.high - previous.close),
                          abs(current.low - previous.close)))
    return ranges


def atr(bars, period=14):
    smoothed = _wilder(_true_range(bars), period)
    return round(smoothed[-1], 4) if smoothed else None


def rsi(closes, period=14):
    gains, losses = [], []
    for previous, current in zip(closes, closes[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = _wilder(gains, period)
    avg_loss = _wilder(losses, period)
    if not avg_gain or not avg_loss:
        return None
    if avg_loss[-1] == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + avg_gain[-1] / avg_loss[-1]), 2)


def directional(bars, period=14):
    """+DI / -DI / ADX. Returns None until there are enough bars to warm up."""
    if len(bars) < period + 1:
        return None
    plus, minus, ranges = [], [], []
    for previous, current in zip(bars, bars[1:]):
        up = current.high - previous.high
        down = previous.low - current.low
        plus.append(up if (up > down and up > 0) else 0.0)
        minus.append(down if (down > up and down > 0) else 0.0)
        ranges.append(max(current.high - current.low,
                          abs(current.high - previous.close),
                          abs(current.low - previous.close)))
    smooth_plus = _wilder(plus, period)
    smooth_minus = _wilder(minus, period)
    smooth_range = _wilder(ranges, period)
    di_plus, di_minus, dx = [], [], []
    for index in range(len(smooth_range)):
        width = smooth_range[index]
        high = smooth_plus[index]
        low = smooth_minus[index]
        if width is None or high is None or low is None:
            continue
        di_plus.append(100.0 * high / width if width else 0.0)
        di_minus.append(100.0 * low / width if width else 0.0)
        total = high + low
        dx.append(100.0 * abs(high - low) / total if total else 0.0)
    if len(dx) < period:
        return None
    adx_line = _wilder(dx, period)
    if not adx_line:
        return None
    return {'adx': round(adx_line[-1], 1), 'di_plus': round(di_plus[-1], 1),
            'di_minus': round(di_minus[-1], 1),
            'adx_prev': round(adx_line[-2], 1) if len(adx_line) > 1 else None}


def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None
    fast_line = ema(closes, fast)
    slow_line = ema(closes, slow)
    line = [f - s if f is not None and s is not None else None
            for f, s in zip(fast_line, slow_line)]
    usable = [v for v in line if v is not None]
    signal_line = ema(usable, signal)
    if not signal_line:
        return None
    return {'macd': round(usable[-1], 4), 'signal': round(signal_line[-1], 4),
            'histogram': round(usable[-1] - signal_line[-1], 4),
            'histogram_prev': round(usable[-2] - signal_line[-2], 4)
            if len(usable) > 1 and len(signal_line) > 1 else None}


def indicators(bars, partial=False):
    """Everything the verdict needs, from one OHLCV series (daily or 5m bars).

    partial=True marks the newest bar as still forming, so volume comparisons are
    taken from the last completed session instead of a fraction of today's.
    """
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    volumes = [b.volume for b in bars]
    completed = volumes[:-1] if partial and len(volumes) > 1 else volumes
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    atr_value = atr(bars)
    window = min(20, len(highs) - 1)
    last = closes[-1]
    base = sum(completed[-21:-1]) / 20 if len(completed) > 21 else 0
    spike = (round(completed[-1] / base, 2) if base else None)
    # the spike has to sit on the same bar as the price move it confirms
    spike_up = bool(spike and spike >= 1.5 and len(closes) > 2
                    and closes[-1 if not partial else -2] >= closes[-2 if not partial else -3])
    return {
        'close': round(last, 2),
        'rsi_14': rsi(closes),
        'atr_14': atr_value,
        'atr_pct': round(atr_value / last * 100, 2) if atr_value else None,
        'ema20': round(ema20[-1], 2) if ema20 else None,
        'ema50': round(ema50[-1], 2) if ema50 else None,
        'high_20': round(max(highs[-window - 1:-1]), 2) if len(highs) > window else None,
        'low_20': round(min(lows[-window - 1:-1]), 2) if len(lows) > window else None,
        'volume_x': spike,
        'volume_spike': spike_up,
        'volume_x_forming': (round(volumes[-1] / base, 2) if base and partial else None),
        'change_pct': round((last / closes[-2] - 1) * 100, 2) if len(closes) > 1 else None,
        'run_5_pct': round((last / closes[-6] - 1) * 100, 2) if len(closes) > 5 else None,
    } | (directional(bars) or {}) | {'macd': macd(closes)}


def _inside_window(window, now):
    try:
        start_text, end_text = str(window).replace(' IST', '').split('-')
        start = dt.time(*[int(p) for p in start_text.split(':')])
        end = dt.time(*[int(p) for p in end_text.split(':')])
        clock = now.astimezone(IST).time()
        return start <= clock <= end
    except (ValueError, AttributeError, TypeError):
        return True


def verdict(row, window=None, now=None):
    """Score the setup, then apply vetoes. Returns (label, score, reasons, vetoes)."""
    score = 0
    reasons = []
    close, ema20, ema50 = row.get('close'), row.get('ema20'), row.get('ema50')
    if close and ema20 and ema50:
        if close > ema20 > ema50:
            score += 2
            reasons.append('trend: price above EMA20 above EMA50 (+2)')
        elif close < ema20 < ema50:
            score -= 2
            reasons.append('trend: price below EMA20 below EMA50 (-2)')
        else:
            reasons.append('trend: EMAs crossed over, no clean structure (0)')
    adx, di_plus, di_minus = row.get('adx'), row.get('di_plus'), row.get('di_minus')
    adx_prev = row.get('adx_prev')
    if adx is not None and di_plus is not None:
        if adx >= 25 and di_plus > di_minus:
            score += 2
            reasons.append('ADX %s with +DI %s over -DI %s: trending up (+2)' % (adx, di_plus, di_minus))
            if adx_prev is not None and adx - adx_prev >= 1:
                score += 1
                reasons.append('ADX climbing %s to %s with +DI on top: the up-trend is strengthening (+1)'
                               % (adx_prev, adx))
            elif adx_prev is not None and adx_prev - adx >= 1:
                reasons.append('ADX easing %s to %s: the up-move is maturing (0)' % (adx_prev, adx))
        elif adx >= 25 and di_minus > di_plus:
            score -= 2
            reasons.append('ADX %s with -DI %s over +DI %s: trending down (-2)' % (adx, di_minus, di_plus))
            if adx_prev is not None and adx - adx_prev >= 1:
                score -= 1
                reasons.append('ADX climbing %s to %s with -DI on top: the down-trend is strengthening, '
                               'so a long entry is against it (-1)' % (adx_prev, adx))
            elif adx_prev is not None and adx_prev - adx >= 1:
                reasons.append('ADX easing %s to %s: the down-move may be exhausting (0)'
                               % (adx_prev, adx))
        elif adx < 18:
            reasons.append('ADX %s under 18: no trend to follow (0)' % adx)
        else:
            reasons.append('ADX %s is transitional -- direction not established (0)' % adx)
    macd_row = row.get('macd') or {}
    if macd_row.get('histogram') is not None:
        if macd_row['histogram'] > 0 and (macd_row.get('histogram_prev') or 0) <= macd_row['histogram']:
            score += 1
            reasons.append('MACD histogram %s and widening (+1)' % macd_row['histogram'])
        elif macd_row['histogram'] > 0:
            reasons.append('MACD histogram positive but narrowing (0)')
        else:
            score -= 1
            reasons.append('MACD histogram %s (negative) (-1)' % macd_row['histogram'])
    rsi_value = row.get('rsi_14')
    if rsi_value is not None:
        if rsi_value >= 60:
            score += 1
            reasons.append('RSI %s shows demand (above 60) (+1)' % rsi_value)
        elif rsi_value <= 40:
            score -= 1
            reasons.append('RSI %s shows supply (below 40) (-1)' % rsi_value)
    high20, low20 = row.get('high_20'), row.get('low_20')
    if close and high20 and close >= high20:
        score += 2
        reasons.append('close %s is at or above the 20-bar high %s (+2)' % (close, high20))
    elif close and low20 and close <= low20:
        score -= 2
        reasons.append('close %s is at or below the 20-bar low %s (-2)' % (close, low20))
    volume_x = row.get('volume_x')
    if row.get('volume_spike'):
        score += 1
        reasons.append('volume %sx the 20-session average on an up session (+1)' % volume_x)
    elif volume_x and volume_x >= 1.5:
        reasons.append('volume %sx the average but the bar closed lower -- distribution, not demand (0)' % volume_x)

    label = 'BUY' if score >= 4 else 'SELL' if score <= -4 else 'HOLD'
    if not reasons:
        reasons.append('no rule fired -- indicators are flat or history is too short (0)')
    vetoes = []
    if label == 'BUY' and rsi_value is not None and rsi_value >= 80:
        vetoes.append('RSI %s is blow-off territory: no fresh entry here' % rsi_value)
    if label == 'BUY' and window and now and not _inside_window(window, now):
        vetoes.append('outside the desk entry window %s IST' % window)
    if row.get('atr_pct') is not None and row['atr_pct'] > 6:
        vetoes.append('daily ATR is %s%% of price -- a ₹500 risk cap cannot size this' % row['atr_pct'])
    if label == 'SELL':
        reasons.append('SELL here means exit or stay out: this desk does not short')
    return label, score, reasons, vetoes


def realized_volatility(bars, bars_per_year=252):
    """Annualized realized volatility of closes -- a measured number, not a quote."""
    closes = [b.close for b in bars if b.close]
    returns = [math.log(c / p) for p, c in zip(closes, closes[1:]) if p > 0 and c > 0]
    if len(returns) < 20:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return round(math.sqrt(variance) * math.sqrt(bars_per_year) * 100, 2)


def option_context(chain):
    """Premium geometry from one chain doc (scripts/optionchain.fetch)."""
    if not isinstance(chain, dict) or not chain.get('atm_strike'):
        return None
    atm = chain['atm_strike']
    row = next((r for r in chain.get('rows') or [] if r.get('strike') == atm), None)
    straddle = round(row['CE']['ltp'] + row['PE']['ltp'], 2) if row else None
    return {
        'symbol': chain.get('symbol'), 'expiry': chain.get('expiry'),
        'underlying': chain.get('underlying'), 'atm_strike': atm,
        'pcr_oi': chain.get('pcr_oi'), 'resistance_oi': chain.get('resistance_oi'),
        'support_oi': chain.get('support_oi'),
        'resistance_buildup': chain.get('resistance_buildup'),
        'support_buildup': chain.get('support_buildup'),
        'call_oi': chain.get('call_oi'), 'put_oi': chain.get('put_oi'),
        'call_oi_change': chain.get('call_oi_change'), 'put_oi_change': chain.get('put_oi_change'),
        'atm_straddle': straddle,
        'expected_move_pct': (round(straddle / chain['underlying'] * 100, 2)
                              if straddle and chain.get('underlying') else None),
        'atm_iv_call': row['CE']['iv'] if row else None,
        'atm_iv_put': row['PE']['iv'] if row else None,
        'timestamp': chain.get('timestamp'),
        'chain_as_of': chain.get('as_of'),
    }


def option_read(chain, index_row=None):
    """Bias for an expiry, from the daily index tape plus the chain's OI geometry.

    Read-only. No option leg here is sized, risked or ticketed by this desk.
    """
    ctx = option_context(chain)
    if not ctx:
        return None
    underlying = ctx['underlying']
    reasons = []
    for key, label in (('resistance_oi', 'call OI wall'), ('support_oi', 'put OI wall')):
        strike = ctx.get(key)
        if strike and underlying:
            reasons.append('%s at %s sits %s the underlying (%+.2f%% away)' % (
                label, '{:,.0f}'.format(strike), 'above' if strike > underlying else 'below',
                (strike / underlying - 1) * 100))
    for key, label in (('resistance_buildup', 'today call OI added at'), ('support_buildup', 'today put OI added at')):
        strike = ctx.get(key)
        if strike and underlying and strike != ctx.get('resistance_oi') and strike != ctx.get('support_oi'):
            reasons.append('%s %s' % (label, '{:,.0f}'.format(strike)))
    pcr = ctx.get('pcr_oi')
    if pcr is not None:
        reasons.append('PCR by open interest %s -- %s' % (
            pcr, 'put side heavier' if pcr >= 1.3 else 'call side heavier' if pcr <= 0.7 else 'balanced'))
    if ctx.get('expected_move_pct'):
        reasons.append('ATM straddle of %s prices a %s%% move into %s' % (
            ctx['atm_straddle'], ctx['expected_move_pct'], ctx['expiry']))
    if ctx.get('call_oi_change') is not None and ctx.get('put_oi_change') is not None:
        reasons.append('OI change today: calls %+d, puts %+d' % (ctx['call_oi_change'], ctx['put_oi_change']))
    if index_row:
        daily_atr = index_row['indicators'].get('atr_pct')
        if daily_atr and ctx.get('expected_move_pct'):
            reasons.append('measured daily ATR is %s%%, so the straddle is %s a one-day range' % (
                daily_atr, 'roughly' if abs(ctx['expected_move_pct'] - daily_atr) <= daily_atr * 0.35
                           else 'more than' if ctx['expected_move_pct'] > daily_atr else 'less than'))

    vetoes = list(index_row['vetoes']) if index_row else []
    vetoes.append('context only: the desk is configured long-only cash (allow_options false) and the '
                  'engine has no option exit or Greeks model, so no premium can become a ticket')
    return ctx | {
        'kind': 'option',
        'index': index_row['symbol'] if index_row else None,
        'index_indicators': index_row['indicators'] if index_row else None,
        'index_realized_vol_pct': index_row.get('realized_vol_pct') if index_row else None,
        'verdict': index_row['verdict'] if index_row else 'HOLD',
        'score': index_row['score'] if index_row else 0,
        'reasons': reasons,
        'vetoes': vetoes,
        'actionable': False,
        'tradeable': False,
    }


def _row(kind, symbol, bars, as_of, window, extra=None):
    clock = as_of.astimezone(IST)
    partial = bars[-1].time.astimezone(IST).date() == clock.date() and clock.time() < dt.time(15, 30)
    i = indicators(bars, partial=partial)
    label, score, reasons, vetoes = verdict(i, window=window, now=as_of)
    return {'kind': kind, 'symbol': symbol, 'timeframe': '1d', 'bars': len(bars),
            'bar_as_of': bars[-1].time.astimezone(IST).isoformat(),
            'bar_forming': partial,
            'indicators': i, 'verdict': label, 'score': score, 'reasons': reasons,
            'vetoes': vetoes, 'actionable': not vetoes,
            'realized_vol_pct': realized_volatility(bars)} | (extra or {})


def index_rows(names, as_of, days=120, window=None):
    import indices
    rows, failures = [], []
    for name in names:
        try:
            symbol, token = indices.resolve(name)
            bars = indices.daily_candles(symbol, token, as_of, days=days)
            rows.append(_row('index', symbol, bars, as_of, window))
        except (ValueError, KeyError, TypeError) as exc:
            failures.append({'symbol': name, 'error': str(exc)[:200]})
        time.sleep(0.4)
    return rows, failures


def build(universe, as_of=None, days=100, window=None, index_names=('NIFTY 50', 'NIFTY BANK'),
          chains=None, index_board=None, sleep=0.25, limit=None, meta=None):
    """Equity and index verdicts from daily NSE bars, plus the expiry tape from saved chains."""
    as_of = (as_of or dt.datetime.now(IST)).astimezone(IST)
    names = list(universe or [])
    if limit:
        names = names[:limit]
    provider = NSEProvider(as_of, days)
    equity, failures = [], []
    for symbol in names:
        try:
            bars = provider.history(symbol, '1d')
            if len(bars) < 30:
                raise ValueError('only %d daily bars, not enough to warm ADX/MACD' % len(bars))
            equity.append(_row('equity', symbol, bars, as_of, window, (meta or {}).get(symbol.upper())))
        except (ValueError, KeyError, TypeError) as exc:
            failures.append({'symbol': symbol, 'error': str(exc)[:200]})
        time.sleep(sleep)
    equity.sort(key=lambda r: -r['score'])

    index_list, index_failures = index_rows(index_names, as_of, days=days, window=window)
    failures.extend(index_failures)
    by_index = {str(r['symbol']).upper(): r for r in index_list}
    options = []
    for chain in (chains or []):
        name = str(chain.get('symbol') or '').upper()
        target = CHAIN_TO_INDEX.get(name, name)
        match = by_index.get(target) or by_index.get(name)
        read = option_read(chain, match)
        if read:
            options.append(read)

    scored = equity + index_list
    counts = {}
    for row in scored:
        counts[row['verdict']] = counts.get(row['verdict'], 0) + 1
    return {
        'created_by': 'desk signals', 'as_of': as_of.isoformat(),
        'source': 'NSE chart host daily bars + NSE option-chain-v3 (public website; provider terms apply)',
        'currency': 'INR', 'paper_only': True, 'execution_enabled': False,
        'universe': len(names), 'entry_window': window,
        'equity': equity, 'index': index_list, 'options': options,
        'counts': counts, 'actionable': sum(1 for r in scored if r['actionable']),
        'orb_context': {
            'as_of': (index_board or {}).get('as_of'),
            'indices': [{k: row.get(k) for k in
                         ('symbol', 'last', 'state', 'orb', 'gap_pct', 'range_pct', 'as_of')}
                        for row in (index_board or {}).get('indices', []) if row.get('orb')],
            'futures': (index_board or {}).get('futures'),
            'breadth': (index_board or {}).get('breadth'),
        },
        'vix': VIX_NOTE,
        'failures': failures,
        'notes': [FORMULA_NOTE,
                  'A signal is a lead, not an order. BUY means the rules agree with a long cash entry; '
                  'SELL means exit or stay out -- this desk never shorts; HOLD means the rules do not line up.'],
    }


def print_signals(doc):
    print('as of %s | %s' % (doc['as_of'], doc['source']))
    line = '  %-18s %10s %6s %6s %6s %6s %6s %9s %6s %6s %s'
    columns = ('symbol', 'close', 'ADX', 'DI+', 'DI-', 'RSI', 'ATR%', 'MACDh', 'volx', 'rv%', 'verdict')
    for kind, title in (('index', 'indices'), ('equity', 'equities')):
        rows = doc.get(kind) or []
        print('\n%s (%d%s)' % (title, len(rows),
                               ', universe %d' % doc['universe'] if kind == 'equity' else ''))
        if not rows:
            print('  none')
            continue
        print(line % columns)
        for row in rows:
            i = row['indicators']
            print(line % (
                row['symbol'], i.get('close'), i.get('adx'), i.get('di_plus'), i.get('di_minus'),
                i.get('rsi_14'), i.get('atr_pct'), (i.get('macd') or {}).get('histogram'),
                i.get('volume_x'), row.get('realized_vol_pct'),
                '%s (%d)%s' % (row['verdict'], row['score'], '' if row['actionable'] else ' BLOCKED')))
            for veto in row['vetoes']:
                print('  %-18s veto: %s' % ('', veto))
    counts = doc.get('counts') or {}
    print('\nverdicts: %s | %d of %d clear of vetoes' % (
        ' '.join('%s %d' % (k, counts[k]) for k in ('BUY', 'HOLD', 'SELL') if counts.get(k)) or 'none',
        doc.get('actionable', 0), len(doc.get('equity') or []) + len(doc.get('index') or [])))
    if any(r.get('bar_forming') for r in (doc.get('equity') or []) + (doc.get('index') or [])):
        print("  (today's daily bar is still forming; the close is live but volume uses the last completed session)")
    for row in doc.get('options') or []:
        print('\noptions tape  %s %s | underlying %s | ATM %s straddle %s (expected move %s%%)' % (
            row['symbol'], row['expiry'], row['underlying'], row['atm_strike'],
            row['atm_straddle'], row['expected_move_pct']))
        print('              PCR %s | walls %s call / %s put | bias %s (%d) | iv c/p %s/%s' % (
            row['pcr_oi'], row['resistance_oi'], row['support_oi'], row['verdict'], row['score'],
            row['atm_iv_call'], row['atm_iv_put']))
        for reason in row['reasons']:
            print('              - %s' % reason)
        for veto in row['vetoes']:
            print('              veto: %s' % veto)
    orb = doc.get('orb_context') or {}
    if orb.get('indices'):
        print('\nintraday   %s' % ' | '.join(
            '%s %s %s (gap %s%%, range %s%%)' % (r['symbol'], r['last'], r['state'],
                                                 r['gap_pct'], r['range_pct'])
            for r in orb['indices'] if r.get('last')))
    if orb.get('futures'):
        f = orb['futures']
        print('           %s %s | premium %s pts (%s%%)' % (
            f['symbol'], f['last'], f['premium_points'], f['premium_pct']))
    vix = doc.get('vix') or {}
    if vix.get('published') is False:
        print('\nvix        not published -- %s' % vix['reason'])
        print('           using %s' % '; '.join(vix.get('substitutes', [])))
    if doc.get('failures'):
        print('unavailable: %s' % '; '.join('%s (%s)' % (f['symbol'], f['error']) for f in doc['failures'][:6]))
    if len(doc.get('failures') or []) > 6:
        print('             ... %d more kept in the JSON' % (len(doc['failures']) - 6))
    for note in doc.get('notes', []):
        print('\n%s' % note)
    print('The desk still has to pass entry, exit, sizing and risk before a ticket exists.')
