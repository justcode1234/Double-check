import unittest
from datetime import date, datetime, time, timedelta, timezone

from dual_strategy.data.calendar import NY, decision_times, flatten_time, next_session_open, prior_sessions, session_bounds


class CalendarTests(unittest.TestCase):
    def test_thanksgiving_2026_is_closed(self):
        day = date(2026, 11, 26)
        self.assertIsNone(session_bounds(day))
        self.assertIsNone(flatten_time(day))
        self.assertEqual(decision_times(day), [])

    def test_black_friday_early_close(self):
        day = date(2026, 11, 27)
        opened, closed = session_bounds(day)
        self.assertEqual(opened, datetime(2026, 11, 27, 9, 30, tzinfo=NY))
        self.assertEqual(closed, datetime(2026, 11, 27, 13, tzinfo=NY))
        self.assertEqual(flatten_time(day), datetime(2026, 11, 27, 12, 55, tzinfo=NY))
        self.assertEqual([t.time() for t in decision_times(day)], [time(10), time(10, 30), time(11), time(11, 30), time(12), time(12, 30)])
        self.assertTrue(all(t < closed for t in decision_times(day)))

    def test_normal_session_ticks_and_flatten(self):
        day = date(2026, 11, 17)
        ticks = decision_times(day)
        self.assertEqual(len(ticks), 12)
        self.assertEqual(ticks[0].time(), time(10))
        self.assertEqual(ticks[-1].time(), time(15, 30))
        self.assertTrue(all(t.tzinfo.key == "America/New_York" for t in ticks))
        self.assertEqual(flatten_time(day).time(), time(15, 55))

    def test_dst_changes_utc_not_exchange_wall_clock(self):
        for before, after, first_utc, second_utc in [
            (date(2026, 3, 6), date(2026, 3, 9), time(14, 30), time(13, 30)),
            (date(2026, 10, 30), date(2026, 11, 2), time(13, 30), time(14, 30)),
        ]:
            with self.subTest(before=before):
                opening_before = session_bounds(before)[0]
                opening_after = session_bounds(after)[0]
                self.assertEqual(opening_before.time(), time(9, 30))
                self.assertEqual(opening_after.time(), time(9, 30))
                self.assertEqual(opening_before.astimezone(timezone.utc).time(), first_utc)
                self.assertEqual(opening_after.astimezone(timezone.utc).time(), second_utc)
                self.assertEqual(session_bounds(after)[1] - opening_after, timedelta(hours=6, minutes=30))

    def test_prior_sessions_skip_holiday_weekend_and_today(self):
        self.assertEqual(prior_sessions(date(2026, 11, 30), 8), [date(2026, 11, d) for d in (17, 18, 19, 20, 23, 24, 25, 27)])
        self.assertEqual(prior_sessions(date(2026, 11, 26), 2), [date(2026, 11, 24), date(2026, 11, 25)])
        self.assertEqual(prior_sessions(date(2026, 11, 17), 0), [])

    def test_next_open_before_open_after_close_and_on_holiday(self):
        for at, day in [
            (datetime(2026, 11, 25, 8, tzinfo=NY), 25),
            (datetime(2026, 11, 25, 16, tzinfo=NY), 27),
            (datetime(2026, 11, 26, 10, tzinfo=NY), 27),
            (datetime(2026, 11, 27, 13, tzinfo=NY), 30),
        ]:
            with self.subTest(at=at):
                self.assertEqual(next_session_open(at), datetime(2026, 11, day, 9, 30, tzinfo=NY))

    def test_naive_timestamps_are_rejected_everywhere(self):
        naive = datetime(2026, 11, 17, 10)
        for function in (session_bounds, decision_times, flatten_time):
            with self.subTest(function=function.__name__), self.assertRaises(ValueError):
                function(naive)
        with self.assertRaises(ValueError):
            prior_sessions(naive, 1)

    def test_datetime_day_uses_new_york_not_utc_date(self):
        # 01:00 UTC Saturday is still Friday in New York.
        self.assertEqual(session_bounds(datetime(2026, 11, 28, 1, tzinfo=timezone.utc)), session_bounds(date(2026, 11, 27)))

    def test_out_of_coverage_and_invalid_counts_fail(self):
        for day in (date(1800, 1, 1), date(2200, 1, 1)):
            with self.subTest(day=day), self.assertRaisesRegex(ValueError, "coverage"):
                session_bounds(day)
        for count in (-1, 1.5, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                prior_sessions(date(2026, 11, 17), count)
        with self.assertRaisesRegex(ValueError, "coverage"):
            prior_sessions(date(2026, 11, 17), 100_000)


if __name__ == "__main__":
    unittest.main()
