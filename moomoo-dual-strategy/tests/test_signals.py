import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from dual_strategy.data.calendar import NY, prior_sessions, session_bounds
from dual_strategy.data.ingest import IngestConvention, load_csv, normalize_moomoo_response
from dual_strategy.data.validation import DataValidationError, validate_rth_bars, validate_session_history
from dual_strategy.models import Bar, DailyBar, Security
from dual_strategy.strategies.noise_area import (
    NoiseAreaState, compute_noise_area, cumulative_vwap, noise_area_signal,
    sigma_slot, stateful_trailing_signal,
)
from dual_strategy.strategies.orb import adv14, atr14, orb_signal, prepare_candidate, rank_candidates

DAY = date(2026, 11, 17)


def tick(day=DAY, hour=10, minute=0):
    return datetime.combine(day, time(hour, minute), NY)


def session_bars(day=DAY, symbol="SPY", through=None, price=100.0, volume=100.0, minutes=1):
    opened, closed = session_bounds(day)
    end = through or closed
    step = timedelta(minutes=minutes)
    return [
        Bar(symbol, opened + i * step, price, price + 1, price - 1, price, volume, price * volume, minutes)
        for i in range(1, int((end - opened) / step) + 1)
    ]


def with_close(bar, price):
    return replace(bar, close=price, high=max(bar.high, price), low=min(bar.low, price))


def daily_bars(symbol="ABC", day=DAY):
    return [DailyBar(symbol, d, 100, 102, 98, 100, 1_000_000) for d in prior_sessions(day, 15)]


def orb_fixture(minutes=1):
    at = tick(hour=9, minute=36 if minutes == 1 else 40)
    bars = session_bars(symbol="ABC", through=at, volume=1500 if minutes == 1 else 7500, minutes=minutes)
    opening_count = 5 // minutes
    bars[opening_count - 1] = with_close(bars[opening_count - 1], 100.5)
    bars[-1] = with_close(bars[-1], 102)
    history = {}
    for i, d in enumerate(prior_sessions(DAY, 14), 1):
        history[d] = session_bars(d, "ABC", volume=i * 100 * minutes, minutes=minutes)
    security = Security("ABC", "STOCK", "Technology", 1_000_000_000, prior_sessions(DAY, 1)[0])
    return security, bars, daily_bars(), history, at


def noise_fixture(day=DAY, at=None):
    at = at or tick(day)
    history = {}
    for i, d in enumerate(prior_sessions(day, 14), 1):
        bars = session_bars(d)
        for j, bar in enumerate(bars):
            if bar.end.time() == time(10):
                bars[j] = with_close(bar, 100 + i)
            elif bar.end.time() == time(10, 30):
                bars[j] = with_close(bar, 101)
        history[d] = bars
    last_day = max(history)
    history[last_day][-1] = with_close(history[last_day][-1], 105)
    current = session_bars(day, through=at, price=110)
    current[-1] = with_close(current[-1], 120)
    return current, history, at


class BarValidationTests(unittest.TestCase):
    def setUp(self):
        self.at = tick()
        self.bars = session_bars(through=self.at)

    def validate(self, bars, **kwargs):
        return validate_rth_bars(bars, symbol="SPY", day=DAY, asof=self.at, through=self.at, **kwargs)

    def test_closed_timestamp_and_unsorted_input(self):
        result = self.validate(list(reversed(self.bars)))
        self.assertEqual(result[0].end.time(), time(9, 31))
        self.assertEqual(result[0].start.time(), time(9, 30))
        self.assertEqual(result[-1].end, self.at)
        self.assertEqual(len(result), 30)

    def test_duplicate_missing_wrong_symbol_interval_future_and_open_labels(self):
        variants = [
            self.bars + [self.bars[0]], self.bars[:10] + self.bars[11:],
            [replace(self.bars[0], symbol="QQQ")] + self.bars[1:],
            [replace(self.bars[0], minutes=5)] + self.bars[1:],
            self.bars + [replace(self.bars[-1], end=self.at + timedelta(minutes=1))],
            [replace(b, end=b.end - timedelta(minutes=1)) for b in self.bars],
        ]
        for bars in variants:
            with self.subTest(length=len(bars)), self.assertRaises(DataValidationError):
                self.validate(bars)

    def test_duplicate_same_instant_in_different_timezones(self):
        with self.assertRaisesRegex(DataValidationError, "Duplicate"):
            self.validate(self.bars + [replace(self.bars[0], end=self.bars[0].end.astimezone(timezone.utc))])

    def test_full_history_requires_close_not_only_slot(self):
        current, history, at = noise_fixture()
        history[min(history)] = history[min(history)][:30]
        with self.assertRaisesRegex(DataValidationError, "Missing"):
            validate_session_history(history, symbol="SPY", day=DAY, asof=at)

    def test_early_close_full_session_has_210_minutes(self):
        day = date(2026, 11, 27)
        bars = session_bars(day)
        self.assertEqual(len(bars), 210)
        self.assertEqual(len(validate_rth_bars(bars, symbol="SPY", day=day, asof=tick(day, 13))), 210)

    def test_naive_asof_unclosed_through_and_weekend(self):
        with self.assertRaises(ValueError):
            validate_rth_bars(self.bars, symbol="SPY", day=DAY, asof=datetime(2026, 11, 17, 10))
        with self.assertRaises(DataValidationError):
            validate_rth_bars(self.bars, symbol="SPY", day=DAY, asof=self.at)
        with self.assertRaises(DataValidationError):
            validate_rth_bars(self.bars, symbol="SPY", day=date(2026, 11, 21), asof=self.at)
        with self.assertRaises(DataValidationError):
            validate_rth_bars(self.bars, symbol="SPY", day=DAY, asof=self.at, through=self.at - timedelta(seconds=1))


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.row = {"code": "US.SPY", "time_key": "2026-11-17 09:30:00", "open": 100_000,
                    "high": 102_000, "low": 99_000, "close": 101_000, "volume": 100, "turnover": 1_010_000}
        self.convention = IngestConvention(1000, "start", 1, "America/New_York", 100, adjustment="unadjusted")

    def normalize(self, response=None, **kwargs):
        options = dict(convention=self.convention, retrieved_at=tick(), source="recorded:test", symbol_map={"US.SPY": "SPY"})
        options.update(kwargs)
        return normalize_moomoo_response([self.row] if response is None else response, **options)

    def test_explicit_scales_start_to_closed_end_and_provenance(self):
        result = self.normalize()
        bar = result.bars[0]
        self.assertEqual((bar.symbol, bar.open, bar.close, bar.turnover), ("SPY", 100, 101, 10100))
        self.assertEqual(bar.end, tick(hour=9, minute=31))
        self.assertEqual(result.provenance.row_count, 1)
        self.assertEqual(len(result.provenance.raw_sha256), 64)
        self.assertEqual(result.provenance.convention.adjustment, "unadjusted")
        self.assertEqual(result.provenance.as_dict()["retrieved_at"], tick().isoformat())
        self.assertEqual(result.provenance.raw_sha256, self.normalize().provenance.raw_sha256)

    def test_explicit_envelope_and_no_implicit_symbol_rewrite(self):
        result = self.normalize({"data": [self.row]}, rows_key="data", symbol_map=None)
        self.assertEqual(result.bars[0].symbol, "US.SPY")
        with self.assertRaises(DataValidationError):
            self.normalize({"data": [self.row]})
        with self.assertRaises(DataValidationError):
            self.normalize((0, [self.row]))

    def test_end_labels_and_offsets_not_shifted(self):
        row = {**self.row, "time_key": "2026-11-17T14:31:00+00:00"}
        result = self.normalize([row], convention=replace(self.convention, timestamp_semantics="end", source_timezone=None))
        self.assertEqual(result.bars[0].end, tick(hour=9, minute=31))

    def test_reject_ambiguous_unknown_scale_time_and_future(self):
        for changes in ({"price_scale": 0}, {"price_scale": float("nan")}, {"timestamp_semantics": "unknown"}, {"minutes": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.convention, **changes)
        for convention in (replace(self.convention, source_timezone=None), replace(self.convention, turnover_scale=None)):
            with self.subTest(convention=convention), self.assertRaises(DataValidationError):
                self.normalize(convention=convention)
        for raw in ("2026-11-01 01:30:00", "2026-03-08 02:30:00", "2026-11-17 10:00:00", 1794933000):
            with self.subTest(raw=raw), self.assertRaises(DataValidationError):
                self.normalize([{**self.row, "time_key": raw}])

    def test_reject_duplicate_empty_and_invalid_ohlc(self):
        for rows in ([self.row, self.row], [], [{**self.row, "high": 1}], [{**self.row, "volume": 0}]):
            with self.subTest(rows=rows), self.assertRaises(DataValidationError):
                self.normalize(rows)

    def test_csv_explicit_columns_and_original_hash(self):
        raw = b"symbol,end,open,high,low,close,volume,turnover\nSPY,2026-11-17T09:31:00-05:00,100,102,99,101,100,10100\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bars.csv"
            path.write_bytes(raw)
            result = load_csv(path, convention=IngestConvention(1, "end", 1, turnover_scale=1), retrieved_at=tick())
            self.assertEqual(result.bars[0].close, 101)
            self.assertEqual(result.provenance.raw_sha256, hashlib.sha256(raw).hexdigest())
            self.assertEqual(result.provenance.source, str(path.resolve()))
            path.write_text("symbol,symbol\nSPY,SPY\n", encoding="utf-8")
            with self.assertRaises(DataValidationError):
                load_csv(path, convention=self.convention, retrieved_at=tick())


class ORBTests(unittest.TestCase):
    def setUp(self):
        self.security, self.bars, self.daily, self.history, self.at = orb_fixture()

    def signal(self, **kwargs):
        args = dict(security=self.security, today_bars=self.bars, daily_history=self.daily,
                    opening_history=self.history, at=self.at, selected_symbols=["ABC"])
        args.update(kwargs)
        return orb_signal(**args)

    def test_atr_uses_14_true_ranges_and_15_prior_closes(self):
        daily = [DailyBar("ABC", d, 100 + i, 100.5 + i, 99.5 + i, 100 + i, 1_000_000 + 100 * i)
                 for i, d in enumerate(prior_sessions(DAY, 15))]
        # Every gap-adjusted TR = 1.5 rather than the high-low range of 1.
        self.assertAlmostEqual(atr14(daily, symbol="ABC", day=DAY), 1.5)
        self.assertEqual(adv14(daily, symbol="ABC", day=DAY), 1_000_750)
        with self.assertRaises(DataValidationError):
            atr14(daily[1:], symbol="ABC", day=DAY)

    def test_hand_calculated_rvol_and_stop_not_target(self):
        candidate = prepare_candidate(self.security, self.bars, self.daily, self.history, at=self.at)
        # Current 5*1500=7500; prior mean 500*(1+...+14)/14=3750.
        self.assertEqual(candidate.rvol, 2)
        self.assertEqual(candidate.atr, 4)
        signal = self.signal()
        self.assertEqual(signal.target_side, 1)
        self.assertAlmostEqual(signal.stop_distance, 0.4)
        self.assertEqual(signal.metadata["stop_anchor"], "actual_executed_entry")
        self.assertIsNone(signal.metadata["take_profit"])
        self.assertNotIn("stop_price", signal.metadata)

    def test_latest_closed_bar_high_breakout_does_not_require_close_or_vwap(self):
        bars = self.bars.copy()
        bars[-1] = replace(bars[-1], close=100, turnover=1_000_000)
        signal = self.signal(today_bars=bars)
        self.assertEqual(signal.target_side, 1)
        self.assertIn("delayed_not_resting_stop", signal.metadata["entry_interpretation"])

    def test_never_breaks_out_at_0935_or_on_equality(self):
        self.assertEqual(self.signal(today_bars=self.bars[:5], at=tick(hour=9, minute=35)).target_side, 0)
        bars = self.bars.copy()
        bars[-1] = replace(bars[-1], high=101, close=101)
        self.assertEqual(self.signal(today_bars=bars).reason, "no_breakout")

    def test_doji_red_candle_and_explicit_verified_short(self):
        bars = self.bars.copy()
        bars[4] = replace(bars[4], close=100)
        self.assertEqual(self.signal(today_bars=bars).reason, "opening_doji")
        bars[4] = replace(bars[4], close=99.5)
        bars[-1] = replace(bars[-1], low=98, close=98)
        self.assertEqual(self.signal(today_bars=bars).target_side, 0)
        self.assertEqual(self.signal(today_bars=bars, shorts_enabled=True).target_side, 0)
        self.assertEqual(self.signal(today_bars=bars, shorts_enabled=True, short_verified=True).target_side, -1)

    def test_universe_boundaries_and_point_in_time_metadata(self):
        for security, reason in (
            (replace(self.security, kind="ETF"), "individual_stocks_only"),
            (replace(self.security, tradable=False), "not_tradable"),
            (replace(self.security, market_cap=999_999_999), "market_cap_below_1b"),
            (replace(self.security, asof=DAY), "invalid_data"),
            (replace(self.security, asof=DAY + timedelta(days=1)), "invalid_data"),
        ):
            with self.subTest(reason=reason):
                result = self.signal(security=security)
                self.assertEqual(result.target_side, 0)
                self.assertIn(reason, result.reason)
        bars = self.bars.copy()
        bars[0] = replace(bars[0], open=5, low=5)
        self.assertIn("opening_price_not_above_5", self.signal(today_bars=bars).reason)
        self.assertIn("adv14_below_1m", self.signal(daily_history=[replace(b, volume=999_999) for b in self.daily]).reason)
        small_atr = [replace(b, high=100.25, low=99.75) for b in self.daily]
        self.assertIn("atr14_not_above_0.50", self.signal(daily_history=small_atr).reason)
        self.assertEqual(self.signal().target_side, 1)  # exact ADV=1m and market cap=1b qualify

    def test_rvol_minimum_inclusive_and_top8_required(self):
        low = [replace(b, volume=b.volume / 4) for b in self.bars]
        equal = [replace(b, volume=b.volume / 2) for b in self.bars]
        self.assertIn("rvol_below_1", self.signal(today_bars=low).reason)
        self.assertEqual(self.signal(today_bars=equal).target_side, 1)
        self.assertEqual(self.signal(selected_symbols=[]).reason, "not_top8_selected")
        self.assertIn("invalid_data", self.signal(selected_symbols=[str(i) for i in range(9)]).reason)

    def test_ranking_deterministic_top8_with_ineligible_removed(self):
        candidate = prepare_candidate(self.security, self.bars, self.daily, self.history, at=self.at)
        candidates = [replace(candidate, symbol=f"S{i:02}", rvol=3 if i < 2 else 2) for i in range(10)]
        ranked = rank_candidates(list(reversed(candidates)))
        self.assertEqual([c.symbol for c in ranked], [f"S{i:02}" for i in range(8)])
        candidates[0] = replace(candidates[0], eligible=False)
        self.assertNotIn("S00", [c.symbol for c in rank_candidates(candidates)])
        with self.assertRaises(DataValidationError):
            rank_candidates([candidate, candidate])

    def test_missing_future_duplicate_history_and_zero_rvol_denominator(self):
        cases = [
            {**self.history, DAY: self.bars},
            {d: b for d, b in self.history.items() if d != min(self.history)},
            {d: [replace(b, volume=0, turnover=0) for b in bars] for d, bars in self.history.items()},
        ]
        for history in cases:
            with self.subTest(sessions=len(history)):
                signal = self.signal(opening_history=history)
                self.assertEqual(signal.target_side, 0)
                self.assertIn("invalid_data", signal.reason)
        for daily in (self.daily + [self.daily[0]], self.daily + [replace(self.daily[-1], session=DAY)], self.daily[:5] + self.daily[6:]):
            with self.subTest(daily_count=len(daily)):
                self.assertIn("invalid_data", self.signal(daily_history=daily).reason)
        self.assertIn("invalid_data", self.signal(today_bars=self.bars[1:]).reason)

    def test_five_minute_bars_are_explicit_and_supported(self):
        security, bars, daily, history, at = orb_fixture(minutes=5)
        result = orb_signal(security, bars, daily, history, at=at, selected_symbols=["ABC"], minutes=5)
        self.assertEqual(result.target_side, 1)
        self.assertEqual(result.metadata["rvol"], 2)

    def test_naive_decision_and_flatten_window(self):
        with self.assertRaises(ValueError):
            self.signal(at=self.at.replace(tzinfo=None))
        self.assertEqual(self.signal(at=tick(hour=15, minute=55)).reason, "flatten_window_no_entry")
        self.assertEqual(self.signal(at=tick(date(2026, 11, 27), 12, 55)).reason, "flatten_window_no_entry")


class NoiseAreaTests(unittest.TestCase):
    def setUp(self):
        self.current, self.history, self.at = noise_fixture()

    def signal(self, **kwargs):
        options = dict(today_bars=self.current, history=self.history, at=self.at)
        options.update(kwargs)
        return noise_area_signal(**options)

    def test_hand_calculated_sigma_gap_bands_turnover_vwap_and_long(self):
        result = compute_noise_area(self.current, self.history, at=self.at)
        # abs returns .01 ... .14 => .075; gap up from 105 prior close to 110.
        self.assertAlmostEqual(result.sigma, 0.075)
        self.assertAlmostEqual(result.upper, 118.25)
        self.assertAlmostEqual(result.lower, 97.125)
        self.assertEqual(result.vwap, 110)
        self.assertEqual(result.previous_close, 105)
        signal = self.signal()
        self.assertEqual(signal.target_side, 1)
        self.assertTrue(signal.metadata["actionable"])
        self.assertEqual(signal.metadata["interpretation"], "target_position")
        self.assertIsNone(signal.stop_distance)

    def test_gap_down_uses_previous_close_for_upper_and_today_open_for_lower(self):
        current = session_bars(through=self.at, price=90)
        result = compute_noise_area(current, self.history, at=self.at)
        self.assertAlmostEqual(result.upper, 105 * 1.075)
        self.assertAlmostEqual(result.lower, 90 * 0.925)

    def test_strict_band_and_vwap_equality_flat(self):
        metrics = compute_noise_area(self.current, self.history, at=self.at)
        for price in (metrics.upper, metrics.lower, 110):
            bars = self.current.copy()
            bars[-1] = with_close(bars[-1], price)
            with self.subTest(price=price):
                result = self.signal(today_bars=bars, shorts_enabled=True, short_verified=True)
                self.assertEqual(result.target_side, 0)
                self.assertTrue(result.metadata["actionable"])
        bars = [replace(b, turnover=120 * b.volume) for b in self.current]
        self.assertEqual(self.signal(today_bars=bars).target_side, 0)

    def test_short_requires_both_opt_in_and_verification(self):
        current = self.current.copy()
        current[-1] = with_close(current[-1], 95)
        for enabled, verified, expected in ((False, False, 0), (True, False, 0), (False, True, 0), (True, True, -1)):
            with self.subTest(enabled=enabled, verified=verified):
                self.assertEqual(self.signal(today_bars=current, shorts_enabled=enabled, short_verified=verified).target_side, expected)

    def test_vwap_hand_calculated_proxy_is_explicit_and_not_mixed(self):
        bars = session_bars(through=tick(hour=9, minute=32), price=10)
        bars[0] = replace(bars[0], high=13, low=9, close=11, volume=2, turnover=20)
        bars[1] = replace(bars[1], high=15, low=9, close=12, volume=1, turnover=12)
        self.assertAlmostEqual(cumulative_vwap(bars), 32 / 3)
        self.assertAlmostEqual(cumulative_vwap(bars, method="hlc3"), (11 * 2 + 12) / 3)
        missing = [replace(b, turnover=None) for b in bars]
        with self.assertRaisesRegex(DataValidationError, "Missing turnover"):
            cumulative_vwap(missing)
        self.assertAlmostEqual(cumulative_vwap(missing, method="hlc3"), 34 / 3)
        with self.assertRaises(DataValidationError):
            cumulative_vwap(bars[1:])

    def test_missing_turnover_zero_volume_and_invalid_methods_fail_closed(self):
        no_turnover = [replace(b, turnover=None) for b in self.current]
        self.assertIn("invalid_data", self.signal(today_bars=no_turnover).reason)
        proxy = self.signal(today_bars=no_turnover, vwap_method="hlc3")
        self.assertEqual(proxy.target_side, 1)
        self.assertEqual(proxy.metadata["vwap_method"], "hlc3")
        zero = [replace(b, volume=0, turnover=0) for b in self.current]
        self.assertIn("invalid_data", self.signal(today_bars=zero).reason)
        bad = self.current.copy()
        bad[0] = replace(bad[0], volume=0)
        self.assertIn("invalid_data", self.signal(today_bars=bad).reason)
        self.assertIn("invalid_data", self.signal(vwap_method="unknown").reason)

    def test_only_closed_scheduled_ticks_and_spy(self):
        for at in (tick(hour=9, minute=59), tick() + timedelta(seconds=1), tick(hour=10, minute=1), tick(hour=16)):
            with self.subTest(at=at):
                signal = self.signal(at=at)
                self.assertEqual(signal.reason, "not_decision_tick")
                self.assertFalse(signal.metadata["actionable"])
        self.assertIn("invalid_data", self.signal(symbol="QQQ").reason)
        with self.assertRaises(ValueError):
            self.signal(at=self.at.replace(tzinfo=None))

    def test_reject_missing_history_gaps_and_lookahead(self):
        history_gap = dict(self.history)
        first = min(history_gap)
        history_gap[first] = history_gap[first][:35] + history_gap[first][36:]
        history_missing = {d: b for d, b in self.history.items() if d != first}
        for history in (history_gap, history_missing, {**self.history, DAY: self.current}):
            with self.subTest(count=len(history)):
                result = self.signal(history=history)
                self.assertEqual(result.target_side, 0)
                self.assertIn("invalid_data", result.reason)
                self.assertFalse(result.metadata["actionable"])
        for current in (self.current[1:], self.current + [replace(self.current[-1], end=self.at + timedelta(minutes=1))]):
            self.assertIn("invalid_data", self.signal(today_bars=current).reason)

    def test_exactly_immediate_14_no_older_session_substitution(self):
        extra_day = prior_sessions(DAY, 15)[0]
        history = {**self.history, extra_day: session_bars(extra_day, price=500)}
        self.assertAlmostEqual(sigma_slot(history, at=self.at), 0.075)
        del history[min(self.history)]
        with self.assertRaises(DataValidationError):
            sigma_slot(history, at=self.at)

    def test_previous_half_day_missing_later_slot_is_rejected_not_skipped(self):
        day = date(2026, 11, 30)
        current, history, at = noise_fixture(day, tick(day, 13, 30))
        result = noise_area_signal(current, history, at=at)
        self.assertIn("has no closed bar", result.reason)
        self.assertEqual(result.target_side, 0)
        current, history, at = noise_fixture(day, tick(day, 12, 30))
        self.assertTrue(noise_area_signal(current, history, at=at).metadata["actionable"])

    def test_early_close_stops_ticks_before_1300(self):
        day = date(2026, 11, 27)
        current, history, at = noise_fixture(day, tick(day, 12, 30))
        self.assertTrue(noise_area_signal(current, history, at=at).metadata["actionable"])
        self.assertEqual(noise_area_signal(current, history, at=tick(day, 13)).reason, "not_decision_tick")

    def test_stateful_ablation_ratchets_and_differs_from_baseline(self):
        first, state = stateful_trailing_signal(self.current, self.history, at=self.at, state=NoiseAreaState())
        self.assertEqual(first.target_side, 1)
        self.assertAlmostEqual(state.trailing_stop, 118.25)
        later = tick(hour=10, minute=30)
        current = session_bars(through=later, price=110)
        current[29] = self.current[-1]
        current[-1] = with_close(current[-1], 115)
        baseline = noise_area_signal(current, self.history, at=later)
        ablation, state = stateful_trailing_signal(current, self.history, at=later, state=state)
        self.assertEqual(baseline.target_side, 1)  # new upper=111.1, VWAP=110
        self.assertEqual(ablation.target_side, 0)  # old ratcheted stop=118.25
        self.assertEqual(ablation.reason, "trailing_exit")
        self.assertEqual(state.side, 0)
        self.assertIn("ablation", ablation.metadata["interpretation"])

    def test_ablation_no_same_tick_reversal_and_rejects_stale_state(self):
        state = NoiseAreaState(1, 118.25, tick(hour=9, minute=30))
        current = self.current.copy()
        current[-1] = with_close(current[-1], 95)
        signal, next_state = stateful_trailing_signal(current, self.history, at=self.at, state=state, shorts_enabled=True, short_verified=True)
        self.assertEqual(signal.target_side, 0)
        self.assertEqual(next_state.side, 0)
        stale = NoiseAreaState(1, 118.25, self.at)
        signal, unchanged = stateful_trailing_signal(self.current, self.history, at=self.at, state=stale)
        self.assertIn("invalid_data", signal.reason)
        self.assertEqual(unchanged, stale)
        overnight = replace(state, last_at=tick(prior_sessions(DAY, 1)[0]))
        signal, unchanged = stateful_trailing_signal(self.current, self.history, at=self.at, state=overnight)
        self.assertIn("overnight", signal.reason)
        self.assertEqual(unchanged, overnight)

    def test_stateful_short_holds_then_closes_if_verification_removed(self):
        current = self.current.copy()
        current[-1] = with_close(current[-1], 95)
        signal, state = stateful_trailing_signal(
            current, self.history, at=self.at, state=NoiseAreaState(),
            shorts_enabled=True, short_verified=True,
        )
        self.assertEqual(signal.target_side, -1)
        self.assertAlmostEqual(state.trailing_stop, 97.125)
        later = tick(hour=10, minute=30)
        current = session_bars(through=later, price=110)
        current[-1] = with_close(current[-1], 90)
        hold, held = stateful_trailing_signal(
            current, self.history, at=later, state=state,
            shorts_enabled=True, short_verified=True,
        )
        self.assertEqual(hold.target_side, -1)
        self.assertAlmostEqual(held.trailing_stop, 97.125)
        exit_signal, flat = stateful_trailing_signal(
            current, self.history, at=later, state=state, shorts_enabled=True,
        )
        self.assertEqual(exit_signal.target_side, 0)
        self.assertEqual(flat.side, 0)

    def test_utc_decision_timestamp_has_identical_exchange_signal(self):
        self.assertEqual(self.signal(), self.signal(at=self.at.astimezone(timezone.utc)))

    def test_deterministic_repeated_calls(self):
        self.assertEqual(self.signal(), self.signal())


if __name__ == "__main__":
    unittest.main()
