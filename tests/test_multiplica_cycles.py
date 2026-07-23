from datetime import date
import unittest

from flows.multiplica.cycles import CycleWindow, collection_windows


class CollectionWindowsTests(unittest.TestCase):
    def test_day_16_collects_current_and_previous(self):
        self.assertEqual(
            collection_windows(date(2026, 7, 16)),
            [
                CycleWindow(
                    date(2026, 7, 11),
                    date(2026, 8, 10),
                    date(2026, 7, 16),
                ),
                CycleWindow(
                    date(2026, 6, 11),
                    date(2026, 7, 10),
                    date(2026, 7, 10),
                ),
            ],
        )

    def test_day_23_collects_only_current(self):
        self.assertEqual(
            collection_windows(date(2026, 7, 23)),
            [
                CycleWindow(
                    date(2026, 7, 11),
                    date(2026, 8, 10),
                    date(2026, 7, 23),
                ),
            ],
        )
