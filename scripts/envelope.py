"""Risk envelope: what the desk's limits allow right now, computed from saved files.

Reads only -- config, the paper portfolio and the last risk decision. It never
calls the risk stage, because that would write a fresh decision into
workspace/state/risk.json and the consent chain keys off that file.
"""
import datetime as dt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct(used, limit):
    if not limit:
        return None
    return round(min(100.0, max(0.0, used / limit * 100)), 1)


def build(cfg, portfolio, risk_state, entry=None, as_of=None):
    """cfg: config/desk.json, portfolio: broker portfolio doc, risk_state: state/risk.json."""
    as_of = as_of or dt.datetime.now(IST)
    capital = _num(cfg.get('budget'), 0.0)
    per_trade = _num(cfg.get('max_loss_per_trade_inr'), 0.0)
    daily_loss = _num(cfg.get('max_daily_loss_inr'), 0.0)
    max_trades = int(cfg.get('max_trades_per_day') or 0)
    max_orders = int(cfg.get('max_orders_per_day') or 0)
    max_position_pct = _num(cfg.get('max_position_pct'), 75.0)
    cash_buffer_pct = _num(cfg.get('cash_buffer_pct'), 10.0)

    cash = _num(portfolio.get('cash'), capital)
    realized = _num(portfolio.get('realized_pnl'), 0.0)
    positions = portfolio.get('positions') or []
    cost_basis = sum((_num(p.get('shares'), 0.0) * _num(p.get('average_cost'), 0.0)) for p in positions)
    max_position_inr = round(capital * max_position_pct / 100.0, 2)
    buffer_inr = round(capital * cash_buffer_pct / 100.0, 2)

    today = (risk_state or {}).get('today') or {}
    state_date = str((risk_state or {}).get('date') or '')
    counters_stale = state_date != as_of.strftime('%Y-%m-%d')
    orders_used = int(today.get('orders_placed') or 0)
    trades_used = int(today.get('entries_placed') or 0)
    loss_at_risk = _num(today.get('loss_at_risk'), 0.0)

    daily_remaining = daily_loss - max(0.0, loss_at_risk) - max(0.0, -realized)
    risk_room = max(0.0, min(per_trade, daily_remaining))
    spendable = max(0.0, cash - buffer_inr)
    position_room = max(0.0, max_position_inr - cost_basis)
    buy_room = min(spendable, position_room)

    state_warnings = [str(w) for w in ((risk_state or {}).get('warnings') or [])]
    results = [r for r in ((risk_state or {}).get('results') or []) if isinstance(r, dict)]
    top = results[0] if results else None
    reasons = [str(r) for r in ((top or {}).get('reasons') or [])]
    warnings = [str(w) for w in ((top or {}).get('warnings') or [])]

    def bar(used, limit):
        return {'used': round(used, 2), 'limit': round(limit, 2), 'pct': _pct(used, limit)}

    pending = None
    if isinstance(entry, dict) and entry.get('symbol'):
        pending = {'symbol': entry.get('symbol'), 'status': entry.get('status'),
                   'eligible': bool(entry.get('eligible'))}
    elif top:
        pending = {'symbol': top.get('symbol'), 'side': top.get('side'), 'amount': top.get('amount'),
                   'verdict': top.get('verdict'), 'max_loss': _num(top.get('max_loss')),
                   'reward_risk': _num(top.get('reward_risk')),
                   'tick_verification': top.get('tick_verification')}

    decision = str((top or {}).get('verdict') or ('PASS' if (risk_state or {}).get('pass') else
                                                  'BLOCKED' if risk_state else 'none'))
    envelope = {
        'as_of': as_of.isoformat(),
        'currency': 'INR',
        'paper': bool(cfg.get('paper', True)),
        'execution_enabled': False,
        'entry_window': '%s-%s IST' % (cfg.get('no_entries_before'), cfg.get('no_entries_after')),
        'flat_by': cfg.get('flat_by'),
        'bars': {
            'loss_at_risk': bar(loss_at_risk, daily_loss),
            'trades': bar(trades_used, max_trades),
            'orders': bar(orders_used, max_orders),
            'position_cost': bar(cost_basis, max_position_inr),
            'cash': bar(max(0.0, capital - cash), capital),
        },
        'cash': round(cash, 2),
        'realized_pnl': round(realized, 2),
        'open_positions': len(positions),
        'cost_basis': round(cost_basis, 2),
        'risk_room_inr': round(risk_room, 2),
        'buy_room_inr': round(buy_room, 2),
        'trades_left': max(0, max_trades - trades_used),
        'orders_left': max(0, max_orders - orders_used),
        'counters_stale': counters_stale,
        'counters_date': state_date or None,
        'last_risk_decision': decision,
        'risk_state_at': (risk_state or {}).get('created_at'),
        'block_reasons': reasons if decision.startswith('BLOCK') else [],
        'warnings': state_warnings + warnings,
        'pending': pending,
    }
    return envelope


def print_envelope(doc):
    print('as of %s | paper %s | no live execution' % (doc['as_of'], doc['paper']))
    print('entry window %s | flatten %s' % (doc['entry_window'], doc['flat_by']))
    for key in ('loss_at_risk', 'trades', 'orders', 'position_cost', 'cash'):
        row = doc['bars'][key]
        width = int((row['pct'] or 0) / 4)
        print('  %-14s %5s%% |%-25s| used %s / %s' % (
            key, row['pct'], '#' * width + '.' * (25 - width), row['used'], row['limit']))
    print('  room now: risk %s | buy %s | trades left %s | orders left %s' % (
        doc['risk_room_inr'], doc['buy_room_inr'], doc['trades_left'], doc['orders_left']))
    if doc['counters_stale']:
        print('  NOTE counters come from the last risk run (%s); re-run risk or a scan for today\'s numbers'
              % (doc['counters_date'] or 'never'))
    decision = doc['last_risk_decision']
    if decision != 'none':
        print('  last risk decision on file: %s' % decision)
        for reason in doc['block_reasons'][:6]:
            print('    blocked: %s' % reason)
        for warning in doc['warnings'][:4]:
            print('    warning: %s' % warning)
    if doc.get('pending'):
        row = doc['pending']
        # entry.json gives a signal row; the risk results row has a verdict instead.
        if row.get('status'):
            print('  pending signal: %s (%s, eligible %s)' % (row['symbol'], row['status'], row['eligible']))
        else:
            amount = _num(row.get('amount'))
            print('  pending order on file: %s %s%s(verdict %s)' % (
                row['symbol'], (row.get('side') or '') + ' ',
                'INR %.2f ' % amount if amount is not None else 'no size recorded ',
                row.get('verdict') or 'no verdict'))
