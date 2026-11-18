"""Offline reproducibility and causality tests; never opens a broker/forward ledger."""
import copy
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from dual_strategy.backtest.data import Dataset, epoch_ms_utc, load_config, load_dataset
from dual_strategy.backtest.execution import Book, D, ResearchRisk, adverse_stop
from dual_strategy.backtest.runner import canonical, preserve_write, publish_comparison, research_archive
from dual_strategy.backtest.simulation import BENCHMARK, VARIANTS, noise_target, simulate
from dual_strategy.data.calendar import NY, flatten_time, prior_sessions, session_bounds
from dual_strategy.models import Bar, DailyBar
from dual_strategy.portfolio.ledger import challenge_fee
from dual_strategy.strategies.noise_area import NoiseAreaState, compute_noise_area, noise_area_signal, stateful_trailing_signal

PROJECT = Path(__file__).resolve().parents[1]
DAY = date(2026, 11, 17)


@pytest.fixture
def cfg():
    return load_config(PROJECT)


def bars(day, symbol, current=False):
    opened, closed = session_bounds(day)
    result = []
    for i in range(int((closed-opened).total_seconds()/300)):
        price = 100.0
        opening = price
        close = price
        if current:
            close = 100.2 if i == 0 else 102.0
            opening = 100 if i == 0 else 100.2 if i == 1 else 102
            if symbol == 'AAPL' and i >= 3:
                opening = close = 98.0  # gap through the actual-entry anchored stop
        volume = 200_000 if current else 100_000
        result.append(Bar(symbol, opened+(i+1)*timedelta(minutes=5), opening,
                          max(opening, close)+.1, min(opening, close)-.1, close,
                          volume, close*volume, minutes=5))
    return tuple(result)


def fixture_data(days=None):
    days = days or [DAY, date(2026, 11, 18), date(2026, 11, 19)]
    history_days = prior_sessions(days[0], 15)
    all_days = history_days + days
    intraday = {s: {d: bars(d, s, d in days) for d in all_days} for s in ('SPY', 'AAPL')}
    daily = {s: [DailyBar(s, d, 100, 105, 95, 100, 2_000_000) for d in all_days] for s in intraday}
    return Dataset(intraday, daily, days, dict(in_sample=days[:1], walk_forward=days[1:2], holdout=days[2:]), {})


def run(data, cfg, name='noise_target_40', scenario='normal'):
    variant = next(v for v in VARIANTS if v.name == name)
    return simulate(data, cfg, variant, scenario, cfg['scenarios'][scenario], 'deterministic-test')


def fills(result):
    return [e for e in result['events'] if e['kind'] == 'FILL']


def test_predeclared_split_and_adapter_preserve_real_units(cfg):
    assert cfg['optimization_enabled'] is False
    assert [len(v) for v in cfg['splits'].values()] == [8, 5, 6]
    assert cfg['splits']['in_sample'][0] == '2026-10-21'
    assert cfg['splits']['holdout'] == ['2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13', '2026-11-16']
    at = epoch_ms_utc('1790861700000')
    assert at.tzinfo == timezone.utc
    assert at.astimezone(NY).strftime('%H:%M') == '09:35'
    for bad in ('1790861700000.0', '2026-10-01', '-1'):
        with pytest.raises(ValueError):
            epoch_ms_utc(bad)
    data = load_dataset(PROJECT, cfg)
    first = data.bars['AAPL'][date(2026, 10, 1)][0]
    assert (first.open, first.volume, first.turnover) == (330, 1797698, 592674171.819)
    assert data.coverage['intraday_rows'] == 7722
    assert (data.coverage['intraday_sessions'], len(data.dates)) == (33, 19)
    assert data.bars['SPY'][date(2026, 10, 30)][0].end.utcoffset() == timedelta(hours=-4)
    assert data.bars['SPY'][date(2026, 11, 2)][0].end.utcoffset() == timedelta(hours=-5)
    altered = copy.deepcopy(cfg)
    altered['splits']['holdout'][0] = '2026-11-06'
    with pytest.raises(ValueError, match='Predeclared'):
        load_dataset(PROJECT, altered)


def test_decimal_partial_fee_once_per_order_and_replay():
    at = session_bounds(DAY)[0]
    book = Book('orb', 'fee-test', 'hash', at)
    order = book.order('AAPL', 1, 10, at, 'test_entry', entry=True,
                       signal_bar_end=at, stop_distance=D('1'))
    book.fill(order, 4, D('100'), at, execution_bar_end=at+timedelta(minutes=5))
    book.fill(order, 6, D('101'), at+timedelta(minutes=5), execution_bar_end=at+timedelta(minutes=10))
    assert D(order['fee']) == D('.99')
    assert book.positions['AAPL'].average == D('100.6')
    assert book.positions['AAPL'].stop == D('99.6')
    sell = book.order('AAPL', -1, 10, at+timedelta(minutes=10), 'test_exit')
    book.fill(sell, 3, 102, at+timedelta(minutes=10))
    book.fill(sell, 7, 102, at+timedelta(minutes=15))
    assert D(sell['fee']) == D('.99') + D('.000166')*10
    assert book.fees == D('1.981660')
    assert book.cash == D('50012.018340')
    assert len(book.trades) == 1
    assert book.trades[0]['r_multiple'] == pytest.approx(1.201834)
    book.mark({'AAPL': 102}, at+timedelta(minutes=15))
    snapshot = book.verify()
    assert snapshot['nav'] == float(book.cash)
    assert len(snapshot['trades']) == 1
    assert snapshot['trades'][0]['net_pnl'] == pytest.approx(book.trades[0]['net_pnl'])
    assert snapshot['trades'][0]['r_multiple'] == pytest.approx(book.trades[0]['r_multiple'])
    with pytest.raises(ValueError, match='duplicate'):
        book.fill(sell, 1, 102, at)
    assert challenge_fee(60_000, -1) + challenge_fee(60_000, -1, 60_000) == D('9.29')
    book.ledger.close()


def test_reject_signal_bar_fill_and_stop_adverse_gap():
    at = session_bounds(DAY)[0]+timedelta(minutes=10)
    b = Book('orb', 'causal', 'hash', at)
    o = b.order('AAPL', 1, 10, at, 'entry', entry=True, signal_bar_end=at)
    with pytest.raises(ValueError, match='signal bar'):
        b.fill(o, 10, 100, at, execution_bar_end=at)
    b.ledger.close()
    bar = Bar('AAPL', at, 95, 96, 94, 95, 100, minutes=5)
    assert adverse_stop(bar, 1, D('99')) == D('95')
    assert adverse_stop(replace(bar, open=105, high=106, low=104, close=105), -1, D('101')) == D('105')


def test_next_open_delay_gap_exceeds_intended_risk_and_no_tp(cfg):
    data = fixture_data()
    normal = run(data, cfg, 'orb_technical_long')
    delayed = run(data, cfg, 'orb_technical_long', 'delayed_orders')
    entry = next(e for e in fills(normal) if e['payload']['reason'] == 'technical_breakout')
    late = next(e for e in fills(delayed) if e['payload']['reason'] == 'technical_breakout')
    signal = datetime.fromisoformat(entry['payload']['signal_at'])
    assert datetime.fromisoformat(entry['payload']['execution_bar_end']) == signal + timedelta(minutes=5)
    assert datetime.fromisoformat(late['at']) == datetime.fromisoformat(entry['at'])+timedelta(minutes=5)
    trade = normal['trades'][0]
    assert trade['exit_reason'] == 'stop_barrier_worst_open_or_stop'
    assert trade['r_multiple'] < -1  # 0.10 ATR cannot guarantee a maximum loss
    assert D(entry['payload']['stop_price']) == D(entry['payload']['price'])-D('1')
    assert normal['metrics']['ledger_replay_verified']
    assert all(not d['overnight'] for d in normal['daily'])


def test_future_data_cannot_change_earlier_decisions_or_fills(cfg):
    first = fixture_data()
    changed = fixture_data()
    at = session_bounds(DAY)[0]+timedelta(minutes=65)
    for s in changed.bars:
        changed.bars[s][DAY] = tuple(replace(b, open=b.open*2, high=b.high*2, low=b.low*2,
                                                  close=b.close*2, turnover=b.turnover*2)
                                    if b.start >= at else b for b in changed.bars[s][DAY])
        changed.daily[s] = [replace(b, high=999, close=500) if b.session >= DAY else b for b in changed.daily[s]]
    a, b = run(first, cfg), run(changed, cfg)
    def prefix(result):
        return [e for e in result['events'] if datetime.fromisoformat(e['at']) < at]
    assert prefix(a) == prefix(b)
    # A future historical bar accidentally supplied to the engine still fails closed.
    with pytest.raises(ValueError):
        changed.noise(DAY, 4)  # 09:55 is not a scheduled tick


def test_deterministic_repeat_and_partial_fills_accounting(cfg):
    data = fixture_data()
    a = run(data, cfg, scenario='partial_fills')
    b = run(data, cfg, scenario='partial_fills')
    assert canonical(a) == canonical(b)
    assert any(len(o['fills']) == 2 for o in a['orders'])
    for order in a['orders']:
        if order['filled']:
            assert D(order['fee']) == challenge_fee(order['filled'], order['side'])
    assert sum(t['net_pnl'] for t in a['trades']) == pytest.approx(a['metrics']['net_pnl'])
    assert a['metrics']['sharpe'] is None and a['metrics']['sortino'] is None
    assert a['metrics']['r_sample_count'] == 0


def test_strict_ORB_never_constructs_security_or_places_orders(cfg, monkeypatch):
    import dual_strategy.models as models
    def forbidden(*args, **kwargs):
        raise AssertionError('No fabricated Security permitted')
    monkeypatch.setattr(models, 'Security', forbidden)
    result = run(fixture_data(), cfg, 'orb_strict_constrained')
    assert result['status'] == 'BLOCKED_SETUP'
    assert result['orders'] == result['trades'] == []
    assert result['metrics']['nav'] == 50000
    assert all(e['payload']['no_Security_metadata_fabricated'] for e in result['events'] if e['kind'] == 'BLOCKED_SETUP')


def test_risk_ladder_own_peak_daily_baseline_and_terminal_persist(cfg):
    a, b = ResearchRisk(cfg['risk']), ResearchRisk(cfg['risk'])
    a.begin(DAY, D(50000))
    b.begin(DAY, D(50000))
    a.update(D(49000))
    assert a.reduced and a.factor == D('.5') and a.daily_locked
    assert not b.reduced and not b.daily_locked
    a.update(D(49200))
    assert a.reduced  # 1.6% retains half exposure
    a.update(D(49300))
    assert not a.reduced
    a.update(D(48250))
    assert a.halted
    a.begin(date(2026, 11, 18), D(49000))
    assert a.halted and a.day_start == D(49000) and a.peak == D(50000)
    a.update(D(47500))
    assert a.terminal
    a.begin(date(2026, 11, 19), D(51000))
    assert a.terminal and a.halted


@pytest.mark.parametrize('scenario', ['stale_data', 'disconnection', 'sentinel_failure'])
def test_outages_block_entries_mark_held_exposure_and_adverse_recovery(cfg, scenario):
    result = run(fixture_data(), cfg, scenario=scenario)
    assert result['metrics']['outage_exposed_intervals'] > 0
    assert any(e['payload'].get('phase') == 'hypothetical_outage_adverse_valuation' for e in result['events'])
    assert any(e['payload']['reason'] == 'outage_adverse_recovery' for e in fills(result))
    for event in fills(result):
        if event['payload']['reason'] == 'noise_target':
            clock = datetime.fromisoformat(event['at']).strftime('%H:%M')
            assert not '10:30' <= clock < '11:00'
    assert all(not d['overnight'] for d in result['daily'])


def test_missed_tick_and_forced_weekend_carry_explicit_failure(cfg):
    result = run(fixture_data(), cfg, scenario='missed_tick')
    assert any(e['kind'] == 'MISSED_DECISION_TICK' for e in result['events'])
    data = fixture_data([date(2026, 11, 20), date(2026, 11, 23), date(2026, 11, 24)])
    failed = run(data, cfg, scenario='overnight_holiday_failure')
    assert failed['metrics']['overnight_failure_count'] > 0
    assert failed['failures'] and not failed['release_validated']
    assert any(e['payload']['reason'] == 'overnight_gap_recovery' for e in fills(failed))
    assert sum(d['net_pnl'] for d in failed['daily']) == pytest.approx(failed['metrics']['net_pnl'])


def test_early_close_preclose_uses_quote_not_last_bar_close(cfg):
    days = [date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 27)]
    data = fixture_data(days)
    final = days[-1]
    assert session_bounds(date(2026, 11, 26)) is None
    assert flatten_time(final).strftime('%H:%M') == '12:55'
    # Alter the final close dramatically. The preclose exit must not consume it.
    original = data.bars['SPY'][final]
    # Ensure a valid day-three breakout above previous sessions' 102 close/band.
    old = (original[0],) + tuple(replace(b, open=104, high=104.1, low=103.9, close=104,
                                        turnover=104*b.volume) for b in original[1:])
    data.bars['SPY'][final] = old[:-1]+(replace(old[-1], high=999, close=999, turnover=999*old[-1].volume),)
    result = run(data, cfg)
    exits = [e for e in fills(result) if e['payload']['reason'] == 'scheduled_preclose_quote_proxy' and e['at'][:10] == str(final)]
    assert exits
    assert exits[-1]['at'] == flatten_time(final).isoformat()
    assert D(exits[-1]['payload']['reference_price']) == D(old[-1].open)
    assert all(not d['overnight'] for d in result['daily'])


def test_target_and_ratchet_match_engine_long_only_metrics(cfg):
    data = fixture_data()
    index = 5
    prefix = data.bars['SPY'][DAY][:index+1]
    at = prefix[-1].end
    history = data.history('SPY', DAY)
    engine = noise_area_signal(prefix, history, at=at, minutes=5)
    target_variant = next(v for v in VARIANTS if v.name == 'noise_target_40')
    target, _, _ = noise_target(data.noise(DAY, index), target_variant)
    assert target == engine.target_side
    ratchet = next(v for v in VARIANTS if v.name == 'noise_ratcheting_40')
    engine_signal, state = stateful_trailing_signal(prefix, history, at=at, state=NoiseAreaState(), minutes=5)
    target, stop, _ = noise_target(data.noise(DAY, index), ratchet)
    assert (target, stop) == (engine_signal.target_side, state.trailing_stop)
    assert next(v for v in VARIANTS if v.name == 'noise_author_vol').author_sizing


def test_benchmark_overnight_full_exposure_fees_and_final_liquidation(cfg):
    data = fixture_data()
    result = simulate(data, cfg, BENCHMARK, 'normal', cfg['scenarios']['normal'], 'benchmark-test')
    assert result['metrics']['order_count'] == 2
    assert result['daily'][0]['overnight']
    assert not result['daily'][-1]['overnight']
    assert result['metrics']['max_observed_gross_NAV_fraction'] > .95
    assert sum(d['net_pnl'] for d in result['daily']) == pytest.approx(result['metrics']['net_pnl'])
    assert result['metrics']['ledger_replay_verified']
    assert len(result['trades']) == 1


def test_archive_and_artifacts_are_immutable_and_replayable(cfg, tmp_path):
    run_result = run(fixture_data(), cfg)
    path = tmp_path / 'backtest-test.sqlite'
    research_archive(path, 'hash', [run_result])
    before = path.read_bytes()
    research_archive(path, 'hash', [run_result])
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM backtest_events').fetchone()[0] == len(run_result['events'])
        restored = json.loads(db.execute('SELECT result_json FROM backtest_runs').fetchone()[0])
        assert restored == run_result
    with pytest.raises(FileExistsError):
        research_archive(path, 'different-hash', [run_result])
    artifact = tmp_path / 'backtest-test.json'
    preserve_write(artifact, 'external data')
    preserve_write(artifact, 'external data')
    with pytest.raises(FileExistsError):
        preserve_write(artifact, 'replacement')
    assert artifact.read_text() == 'external data'


def test_public_summary_matches_completed_report_and_preserves_old_runs(cfg, tmp_path):
    public = tmp_path / 'backtest_comparison.json'
    previous = None
    for scenario in ('normal', 'stressed_slippage'):
        result = run(fixture_data(), cfg, scenario=scenario)
        report = tmp_path / f'backtest_{scenario}.json'
        comparison = dict(comparison_report=str(report), release_validated=False,
                          mode='SHADOW / HYPOTHETICAL FILLS', result=result)
        content = canonical(comparison) + '\n'
        preserve_write(report, content)
        publish_comparison(public, comparison)
        assert public.read_bytes() == report.read_bytes()
        assert json.loads(public.read_text()) == comparison
        publish_comparison(public, comparison)
        assert public.read_bytes() == report.read_bytes()
        if previous:
            assert previous[0].read_bytes() == previous[1]
        previous = (report, report.read_bytes())
    before = public.read_bytes()
    with pytest.raises(ValueError, match='immutable'):
        publish_comparison(public, comparison | {'release_validated': True})
    with pytest.raises(FileNotFoundError):
        publish_comparison(public, comparison | {'comparison_report': str(tmp_path / 'missing.json')})
    assert public.read_bytes() == before
    assert list(tmp_path.glob('*.tmp')) == []


@pytest.mark.parametrize('field,value', [('net_pnl_decimal', '999'), ('r_multiple', 123), ('qty', 999)])
def test_book_verification_checks_flat_to_flat_episode_fields(field, value):
    at = session_bounds(DAY)[0]
    book = Book('orb', 'verify-trades', 'test', at)
    try:
        entry = book.order('AAPL', 1, 10, at, 'entry', stop_distance=D('1'))
        book.fill(entry, 10, 100, at)
        exit_order = book.order('AAPL', -1, 10, at + timedelta(minutes=5), 'exit')
        book.fill(exit_order, 10, 101, at + timedelta(minutes=5))
        book.verify()
        book.trades[0][field] = value
        with pytest.raises(AssertionError, match='trade replay'):
            book.verify()
    finally:
        book.ledger.close()


def test_dividend_api_integration_does_not_invent_cash_actions():
    data = fixture_data()
    prefix = data.bars['SPY'][DAY][:6]
    at = prefix[-1].end
    inputs = dict(at=at, minutes=5)
    unadjusted = compute_noise_area(prefix, data.history('SPY', DAY), cash_dividend=0, **inputs)
    adjusted = compute_noise_area(prefix, data.history('SPY', DAY), cash_dividend=2, **inputs)
    assert data.noise(DAY, 5) == unadjusted
    assert adjusted.previous_close == unadjusted.previous_close - 2
    assert adjusted.sigma == unadjusted.sigma
    assert adjusted.vwap == unadjusted.vwap
    variant = next(v for v in VARIANTS if v.name == 'noise_target_40')
    signal = noise_area_signal(prefix, data.history('SPY', DAY), cash_dividend=2, **inputs)
    assert noise_target(adjusted, variant)[0] == signal.target_side
    assert data.noise(DAY, 5).previous_close == unadjusted.previous_close


def test_execution_bar_volume_and_close_cannot_size_its_open_fill(cfg):
    data, changed = fixture_data(), fixture_data()
    index = 6  # 10:00 open, after the first Noise decision at 10:00.
    original = changed.bars['SPY'][DAY]
    b = original[index]
    changed.bars['SPY'][DAY] = original[:index] + (
        replace(b, volume=0, turnover=0, high=999, close=999),
    ) + original[index+1:]
    first, second = run(data, cfg), run(changed, cfg)
    entry_a = next(e for e in fills(first) if e['payload']['reason'] == 'noise_target')
    entry_b = next(e for e in fills(second) if e['payload']['reason'] == 'noise_target')
    assert entry_a == entry_b
