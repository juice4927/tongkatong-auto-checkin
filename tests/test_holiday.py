import unittest
from datetime import date, timedelta
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.core.holiday import HolidayChecker


def _first_weekend(start: date) -> date:
    d = start
    for _ in range(14):
        if d.weekday() >= 5:
            return d
        d += timedelta(days=1)
    return start


class TestHolidayChecker(unittest.TestCase):
    def test_extra_workdays_override_weekend(self):
        weekend = _first_weekend(date(2026, 1, 1))
        checker = HolidayChecker(extra_workdays=[weekend.strftime("%Y-%m-%d")])
        self.assertTrue(checker.is_workday(weekend))

    def test_extra_holidays_override_weekday(self):
        d = date(2026, 1, 1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        checker = HolidayChecker(extra_holidays=[d.strftime("%Y-%m-%d")])
        self.assertFalse(checker.is_workday(d))


if __name__ == "__main__":
    unittest.main()

