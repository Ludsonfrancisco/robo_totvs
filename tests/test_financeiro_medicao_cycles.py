from datetime import date, datetime
import unittest

from flows.financeiro_medicao import window_for


class FinanceiroMedicaoCycleTests(unittest.TestCase):
    def test_current_cycle_in_july(self):
        window = window_for(date(2026, 7, 30))

        self.assertEqual(window.cycle_id, "2026-07-11--2026-08-10")
        self.assertEqual(window.query_start, date(2026, 7, 11))
        self.assertEqual(window.query_end, date(2026, 7, 30))
        self.assertEqual(window.mode, "current")

    def test_current_cycle_in_august_before_close(self):
        window = window_for(date(2026, 8, 5))

        self.assertEqual(window.cycle_id, "2026-07-11--2026-08-10")
        self.assertEqual(window.query_start, date(2026, 7, 11))
        self.assertEqual(window.query_end, date(2026, 8, 5))
        self.assertEqual(window.mode, "current")

    def test_first_day_keeps_previous_month_cycle(self):
        window = window_for(date(2026, 8, 1))

        self.assertEqual(window.cycle_start, date(2026, 7, 11))
        self.assertEqual(window.cycle_close, date(2026, 8, 10))
        self.assertEqual(window.query_end, date(2026, 8, 1))
        self.assertEqual(window.mode, "current")

    def test_tenth_day_keeps_previous_month_cycle(self):
        window = window_for(date(2026, 8, 10))

        self.assertEqual(window.cycle_start, date(2026, 7, 11))
        self.assertEqual(window.cycle_close, date(2026, 8, 10))
        self.assertEqual(window.query_end, date(2026, 8, 10))
        self.assertEqual(window.mode, "current")

    def test_closing_day_finalizes_previous_cycle(self):
        window = window_for(date(2026, 8, 11))

        self.assertEqual(window.cycle_id, "2026-07-11--2026-08-10")
        self.assertEqual(window.query_start, date(2026, 7, 11))
        self.assertEqual(window.query_end, date(2026, 8, 10))
        self.assertEqual(window.mode, "final")

    def test_day_after_close_starts_new_cycle(self):
        window = window_for(date(2026, 8, 12))

        self.assertEqual(window.cycle_id, "2026-08-11--2026-09-10")
        self.assertEqual(window.query_start, date(2026, 8, 11))
        self.assertEqual(window.query_end, date(2026, 8, 12))
        self.assertEqual(window.mode, "current")

    def test_year_boundary_uses_previous_december_cycle(self):
        window = window_for(date(2027, 1, 5))

        self.assertEqual(window.cycle_id, "2026-12-11--2027-01-10")
        self.assertEqual(window.cycle_close, date(2027, 1, 10))
        self.assertEqual(window.query_start, date(2026, 12, 11))
        self.assertEqual(window.query_end, date(2027, 1, 5))
        self.assertEqual(window.mode, "current")

    def test_datetime_is_normalized_to_plain_dates(self):
        window = window_for(datetime(2026, 7, 30, 23, 59))

        self.assertIs(type(window.query_end), date)
        self.assertEqual(window.query_end, date(2026, 7, 30))


if __name__ == "__main__":
    unittest.main()
