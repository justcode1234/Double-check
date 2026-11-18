"""Supervised Portfolio B (SPY Noise-Area) signal computation on live data.

Fetches 5-minute SPY bars from the authenticated Moomoo CLI (autype 0, one
consistent price basis), assembles exactly 14 prior complete sessions plus
today's closed bars, and computes the baseline long-only Noise-Area signal at
the latest closed half-hour decision tick. Prints signal + metrics; places NO
orders here. 5-minute VWAP is a documented deviation from preferred 1-minute.
"""
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dual_strategy.data.calendar import NY
from dual_strategy.execution.moomoo_cli import default_cli_path
from dual_strategy.models import Bar
from dual_strategy.strategies.noise_area import noise_area_signal

PROJECT = Path(__file__).resolve().parents[1]
SYMBOL = 'US.SPY'


def _parse(rows):
    bars = []
    for row in rows:
        end_dt = datetime.fromtimestamp(row['time_key'] / 1000, timezone.utc).astimezone(NY)
        o = float(row.get('open_price', row.get('open')))
        h = float(row.get('high_price', row.get('high')))
        l = float(row.get('low_price', row.get('low')))
        c = float(row.get('close_price', row.get('close')))
        bars.append(Bar(SYMBOL, end_dt, o, h, l, c, float(row['volume']), float(row['turnover']), 5))
    return bars


def _run(cli, args):
    r = subprocess.run([cli, *args, '--format', 'json'], capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(f'CLI failed: {(r.stderr or r.stdout)[:250]}')
    data = json.loads(r.stdout)
    return data.get('data', {}).get('kline_list', data.get('kline_list', []))


def fetch_history(cli, today):
    # Two <=1000-bar batches covering the 14 prior complete sessions (CLI caps bars/call).
    ranges = [((today - timedelta(days=9)).isoformat(), today.isoformat()),
              ((today - timedelta(days=20)).isoformat(), (today - timedelta(days=11)).isoformat())]
    seen, bars = set(), []
    for start, end in ranges:
        for row in _run(cli, ['history-kline', SYMBOL, '--ktype', '6', '--autype', '0',
                              '--start', start, '--end', end, '--num', '370']):
            if row['time_key'] not in seen:
                seen.add(row['time_key'])
                bars.extend(_parse([row]))
    return bars


def fetch_today(cli, today):
    rows = _run(cli, ['kline', SYMBOL, '--ktype', '6', '--autype', '0', '--num', '80'])
    return [b for b in _parse(rows) if b.end.date() == today]


def get_latest_signal(cli=None, today=None):
    cli = cli or default_cli_path()
    today = today or datetime.now(timezone.utc).astimezone(NY).date()
    sessions = defaultdict(list)
    for b in fetch_history(cli, today):
        sessions[b.end.date()].append(b)
    today_bars = sorted(fetch_today(cli, today), key=lambda b: b.end)
    for day in sessions:
        sessions[day].sort(key=lambda b: b.end)
    prior = sorted(d for d in sessions if d < today)
    if len(prior) < 14:
        return {'error': f'need 14 prior sessions, have {len(prior)}', 'prior': [str(d) for d in prior]}
    history = {d: sessions[d] for d in prior[-14:]}
    ticks = [b.end for b in today_bars if b.end.minute in (0, 30) and b.end.hour >= 10]
    if not ticks:
        return {'error': 'no closed decision tick today yet', 'today_bars': len(today_bars)}
    at = max(ticks)
    today_prefix = [b for b in today_bars if b.end <= at]
    sig = noise_area_signal(today_prefix, history, at=at, symbol=SYMBOL, shorts_enabled=False,
                            vwap_method='turnover', minutes=5)
    return {'at': at.isoformat(), 'today_open': today_prefix[0].open,
            'prev_close': history[max(history)][-1].close,
            'prior_sessions': [str(d) for d in sorted(history)],
            'today_bars_used': len(today_prefix),
            'target_side': sig.target_side, 'reason': sig.reason,
            'metrics': sig.metadata}


def main():
    print(json.dumps(get_latest_signal(), default=str))


if __name__ == '__main__':
    main()