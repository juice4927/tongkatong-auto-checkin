import unittest
import random
from datetime import datetime
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.core.random_time import parse_time, generate_random_time


class TestRandomTime(unittest.TestCase):
    def test_parse_time(self):
        t = parse_time("08:30")
        self.assertEqual(t.hour, 8)
        self.assertEqual(t.minute, 30)

    def test_generate_random_time_in_range(self):
        random.seed(12345)
        dt = generate_random_time("08:30", "09:00", random_seconds=(0, 0))
        self.assertIsInstance(dt, datetime)
        self.assertGreaterEqual((dt.hour, dt.minute), (8, 30))
        self.assertLessEqual((dt.hour, dt.minute), (9, 0))

    def test_generate_random_time_invalid_range(self):
        with self.assertRaises(ValueError):
            generate_random_time("09:00", "08:30", random_seconds=(0, 0))


if __name__ == "__main__":
    unittest.main()

