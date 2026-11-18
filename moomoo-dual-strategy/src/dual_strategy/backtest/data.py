"""Offline, explicit epoch-ms adapter and fail-closed comparison coverage."""
import csv
import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from dual_strategy.data.calendar import NY, prior_sessions, session_bounds
from dual_strategy.data.ingest import CSV_FIELDS, IngestConvention, normalize_moomoo_response
from dual_strategy.data.validation import DataValidationError, validate_daily_history, validate_rth_bars
from dual_strategy.models import Bar, DailyBar
from dual_strategy.strategies.noise_area import compute_noise_area
from dual_strategy.strategies.orb import adv14, atr14, opening_range, opening_rvol


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sessions(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)
            if session_bounds(start + timedelta(days=i))]


def epoch_ms_utc(raw: str) -> datetime:
    # No magnitude guessing, local-time interpretation, or price-unit scaling.
    if not raw.isdigit():
        raise DataValidationError("end must be integer UTC epoch milliseconds")
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=int(raw))


def load_config(project: Path) -> dict:
    cfg = yaml.safe_load((project / 'config/backtest.yaml').read_text(encoding='utf-8'))
    if cfg['mode'] != 'SHADOW' or cfg['optimization_enabled'] is not False:
        raise ValueError('Only fixed-parameter SHADOW research is supported')
    if cfg['initial_nav'] != 50000 or cfg['lookback_sessions'] != 14:
        raise ValueError('This comparison requires independent 50000 NAV and 14 warmup sessions')
    d = cfg['data']
    if (d['timestamp_unit'], d['timestamp_semantics'], d['minutes']) != ('epoch_milliseconds_utc', 'end', 5):
        raise ValueError('Explicit five-minute end-labeled UTC epoch-ms input required')
    if any(d[key] != 1 for key in ('price_divisor', 'volume_divisor', 'turnover_divisor')):
        raise ValueError('Source numeric units must remain unchanged')
    if cfg['execution']['fill'] != 'next_bar_open' or not cfg['execution']['no_take_profit']:
        raise ValueError('Unsupported execution convention')
    if (cfg['execution']['fee_per_order'], cfg['execution']['sell_fee_per_share'],
            cfg['execution']['sell_fee_cap']) != (0.99, 0.000166, 8.30):
        raise ValueError('Fees must match the shared challenge_fee helper')
    if cfg['risk']['flatten_minutes_before_close'] != 5:
        raise ValueError('Calendar flatten proxy must be five minutes before close')
    if cfg['research']['annualized_ratio_min_sessions'] != 60:
        raise ValueError('Shared reporting requires 60 sessions for annualized ratios')
    return cfg


@dataclass
class Dataset:
    bars: dict[str, dict[date, tuple[Bar, ...]]]
    daily: dict[str, list[DailyBar]]
    dates: list[date]
    splits: dict[str, list[date]]
    coverage: dict
    noise_cache: dict = field(default_factory=dict)
    orb_cache: dict = field(default_factory=dict)

    def history(self, symbol: str, day: date):
        return {d: self.bars[symbol][d] for d in prior_sessions(day, 14)}

    def noise(self, day: date, index: int):
        key = (day, index)
        if key not in self.noise_cache:
            prefix = self.bars['SPY'][day][:index + 1]
            self.noise_cache[key] = compute_noise_area(
                prefix, self.history('SPY', day), at=prefix[-1].end,
                # Zero means no adjustment applied, NOT verified absence of dividends.
                symbol='SPY', vwap_method='turnover', minutes=5, cash_dividend=0)
        return self.noise_cache[key]

    def technical_candidates(self, day: date):
        """No Security objects or invented metadata. NOT full ORB eligibility."""
        if day not in self.orb_cache:
            result = []
            for symbol in sorted(s for s in self.bars if s != 'SPY'):
                first = self.bars[symbol][day][0]
                opening = opening_range([first], symbol=symbol, day=day, asof=first.end, minutes=5)
                daily = [b for b in self.daily[symbol] if b.session < day]
                atr = atr14(daily, symbol=symbol, day=day)
                adv = adv14(daily, symbol=symbol, day=day)
                rvol = opening_rvol(opening, self.history(symbol, day), symbol=symbol,
                                    day=day, asof=first.end, minutes=5)
                result.append(dict(symbol=symbol, opening=opening, atr=atr, adv=adv, rvol=rvol,
                                   eligible=opening.open > 5 and adv >= 1e6 and atr > .50 and rvol >= 1))
            self.orb_cache[day] = sorted(result, key=lambda x: (-x['rvol'], x['symbol']))
        return self.orb_cache[day]


def load_dataset(project: Path, cfg: dict) -> Dataset:
    spec = cfg['data']
    paths = {k: (project / spec[k]).resolve() for k in ('intraday', 'daily')}
    for path in paths.values():
        if not path.is_relative_to((project / 'data-cache/normalized').resolve()):
            raise ValueError('Research input must remain under data-cache/normalized')
    with paths['intraday'].open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise DataValidationError('Missing/duplicate CSV columns')
        rows = list(reader)
    for row in rows:
        if None in row or row['minutes'] != '5':
            raise DataValidationError('Invalid CSV row or minutes')
        row['end'] = epoch_ms_utc(row['end'])
    end = date.fromisoformat(spec['end'])
    result = normalize_moomoo_response(
        rows, convention=IngestConvention(1, 'end', 5, 'UTC', 1, 1, spec['adjustment']),
        retrieved_at=session_bounds(end)[1], source='recorded_normalized_csv_epoch_ms_adapter',
        field_map=CSV_FIELDS, symbol_map=spec['symbols'])
    bars = {s: {} for s in spec['symbols'].values()}
    for bar in result.bars:
        bars[bar.symbol].setdefault(bar.end.astimezone(NY).date(), []).append(bar)
    expected = sessions(date.fromisoformat(spec['intraday_start']), end)
    for symbol, history in bars.items():
        if sorted(history) != expected:
            raise DataValidationError(f'{symbol}: unequal or incomplete session coverage')
        for day, values in history.items():
            history[day] = validate_rth_bars(values, symbol=symbol, day=day,
                                            asof=session_bounds(day)[1], minutes=5)
    daily = {s: [] for s in bars}
    with paths['daily'].open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise DataValidationError('Missing/duplicate daily columns')
        for row in reader:
            if None in row:
                raise DataValidationError('Invalid daily CSV row')
            symbol = spec['symbols'][row['symbol']]
            daily[symbol].append(DailyBar(symbol, date.fromisoformat(row['session']),
                                         **{k: float(row[k]) for k in ('open', 'high', 'low', 'close', 'volume')}))
    expected_daily = sessions(date.fromisoformat(spec['daily_start']), end)
    for symbol, values in daily.items():
        values.sort(key=lambda b: b.session)
        if [b.session for b in values] != expected_daily:
            raise DataValidationError(f'{symbol}: duplicate/missing/non-session daily data')
    dates = expected[cfg['lookback_sessions']:]
    splits = {name: [date.fromisoformat(d) for d in cfg['splits'][name]]
              for name in ('in_sample', 'walk_forward', 'holdout')}
    if [d for group in splits.values() for d in group] != dates:
        raise DataValidationError('Predeclared splits must cover all usable dates exactly, in order')
    for symbol in bars:
        for day in dates:
            validate_daily_history([b for b in daily[symbol] if b.session < day],
                                   symbol=symbol, day=day, count=15)
    # Report cross-source differences without fabricating adjustments/replacing units.
    discrepancies = []
    for symbol, history in bars.items():
        daily_map = {b.session: b for b in daily[symbol]}
        for day, values in history.items():
            db = daily_map[day]
            if abs(values[0].open / db.open - 1) > .001 or abs(values[-1].close / db.close - 1) > .001:
                discrepancies.append(dict(symbol=symbol, day=str(day), daily_open=db.open,
                                          intraday_open=values[0].open, daily_close=db.close,
                                          intraday_close=values[-1].close))
    coverage = dict(intraday_rows=len(rows), intraday_sessions=len(expected), daily_sessions=len(expected_daily),
                    warmup_sessions=14, usable_sessions=len(dates), dates=[str(d) for d in dates],
                    split_counts={k: len(v) for k, v in splits.items()}, calendar='XNYS',
                    timezone='America/New_York', timestamps='UTC epoch milliseconds -> NY closed ends',
                    units='All prices, volume and turnover unchanged; divisors exactly 1',
                    source_hashes={k: sha(p) for k, p in paths.items()},
                    daily_intraday_discrepancies_over_10bps=discrepancies,
                    corporate_actions_verified=False, pit_universe_verified=False)
    return Dataset(bars, daily, dates, splits, coverage)
