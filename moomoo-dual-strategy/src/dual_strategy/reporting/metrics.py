from math import sqrt
from statistics import mean, stdev


def closed_trade_metrics(pnl: list[float]) -> dict:
    wins = [v for v in pnl if v > 0]
    losses = [v for v in pnl if v < 0]
    return {
        'closed_trade_count': len(pnl),
        'win_rate_pct': 100 * len(wins) / len(pnl) if pnl else None,
        'average_win': mean(wins) if wins else None,
        'average_loss': mean(losses) if losses else None,
        'expectancy_dollars': mean(pnl) if pnl else None,
        'profit_factor': sum(wins) / abs(sum(losses)) if losses else None,
    }


def performance(equity: list[dict], trades: list[dict], *, initial_nav: float = 50000) -> dict:
    high = initial_nav
    drawdowns = []
    for point in equity:
        high = max(high, point['nav'])
        drawdowns.append({'at': point['at'], 'drawdown_pct': 100 * (point['nav'] / high - 1)})
    daily = {}
    from datetime import datetime
    from zoneinfo import ZoneInfo
    for point in equity:
        if not point.get('observation', True):
            continue
        day = datetime.fromisoformat(point['at']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
        daily[day] = point['nav']
    values = [initial_nav] + list(daily.values())
    returns = [b / a - 1 for a, b in zip(values, values[1:]) if a > 0]
    r_values = [t['r_multiple'] for t in trades if t.get('r_multiple') is not None]
    sigma = stdev(returns) if len(returns) > 1 else 0
    downside = sqrt(mean([min(v, 0) ** 2 for v in returns])) if returns else 0
    meaningful = len(returns) >= 60
    return {
        **closed_trade_metrics([t['net_pnl'] for t in trades]),
        'sample_sessions': len(daily),
        'sharpe': mean(returns) / sigma * sqrt(252) if meaningful and sigma else None,
        'sortino': mean(returns) / downside * sqrt(252) if meaningful and downside else None,
        'ratio_note': 'Suppressed below 60 observed sessions; estimates assume zero risk-free rate',
        'max_drawdown_pct': abs(min((p['drawdown_pct'] for p in drawdowns), default=0)),
        'drawdown_curve': drawdowns,
        'worst_daily_return_pct': 100 * min(returns) if returns else None,
        'worst_daily_loss_dollars': min((b - a for a, b in zip(values, values[1:])), default=None),
        'expectancy_r': mean(r_values) if r_values else None,
        'r_sample_count': len(r_values),
        'profit_factor_note': 'Undefined without at least one losing trade',
    }
