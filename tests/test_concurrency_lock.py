import unittest
import threading
import time
import tempfile
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.core.scheduler import CheckinOrchestrator
from src.core.automator import CheckinResult, CheckinAction
from src.core.config import ConfigManager


class _HolidayAlwaysWorkday:
    def is_workday(self, *_args, **_kwargs):
        return True


class _FakeAutomator:
    def __init__(self, timeline):
        self._connected = False
        self._timeline = timeline
        self._lock = threading.Lock()

    def is_connected(self):
        return self._connected

    def connect(self):
        self._connected = True
        return True

    def open_app(self, package_name: str, notify_config=None):
        return True

    def do_checkin(self, action):
        with self._lock:
            start = time.time()
            self._timeline.append(("enter", action.value, start))
            time.sleep(0.15)
            end = time.time()
            self._timeline.append(("exit", action.value, end))

        return CheckinResult(
            success=True,
            action=action,
            message="ok",
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        )


class TestConcurrencyLock(unittest.TestCase):
    def test_do_checkin_serialized(self):
        timeline = []
        auto = _FakeAutomator(timeline)
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / "config"
            cm = ConfigManager(config_dir=cfg_dir)
            cm.load()
            hc = _HolidayAlwaysWorkday()
            orch = CheckinOrchestrator(auto, hc, cm)

            t1 = threading.Thread(target=lambda: orch._do_checkin(CheckinAction.MORNING_SIGNIN, "morning_signin"))
            t2 = threading.Thread(target=lambda: orch._do_checkin(CheckinAction.MORNING_SIGNOUT, "morning_signout"))
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

        enters = [x for x in timeline if x[0] == "enter"]
        exits = [x for x in timeline if x[0] == "exit"]
        self.assertEqual(len(enters), 2)
        self.assertEqual(len(exits), 2)

        first_enter = enters[0][2]
        first_exit = exits[0][2]
        second_enter = enters[1][2]
        self.assertGreaterEqual(second_enter, first_exit)
        self.assertLess(first_enter, first_exit)

    def test_same_job_reentrant_skip(self):
        timeline = []
        auto = _FakeAutomator(timeline)
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / "config"
            cm = ConfigManager(config_dir=cfg_dir)
            cm.load()
            hc = _HolidayAlwaysWorkday()
            orch = CheckinOrchestrator(auto, hc, cm)

            t1 = threading.Thread(target=lambda: orch._do_checkin(CheckinAction.MORNING_SIGNIN, "morning_signin"))
            t2 = threading.Thread(target=lambda: orch._do_checkin(CheckinAction.MORNING_SIGNIN, "morning_signin"))
            t1.start()
            time.sleep(0.02)
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

        enters = [x for x in timeline if x[0] == "enter"]
        self.assertEqual(len(enters), 1)

    def test_reentrant_and_other_job_still_no_overlap(self):
        timeline = []
        auto = _FakeAutomator(timeline)
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / "config"
            cm = ConfigManager(config_dir=cfg_dir)
            cm.load()
            hc = _HolidayAlwaysWorkday()
            orch = CheckinOrchestrator(auto, hc, cm)

            t1 = threading.Thread(target=lambda: orch._do_checkin(CheckinAction.MORNING_SIGNIN, "morning_signin"))
            t2 = threading.Thread(target=lambda: orch._do_checkin(CheckinAction.MORNING_SIGNIN, "morning_signin"))
            t3 = threading.Thread(target=lambda: orch._do_checkin(CheckinAction.AFTERNOON_SIGNIN, "afternoon_signin"))
            t1.start()
            time.sleep(0.02)
            t2.start()
            time.sleep(0.02)
            t3.start()
            t1.join(timeout=5)
            t2.join(timeout=5)
            t3.join(timeout=5)

        enters = [x for x in timeline if x[0] == "enter"]
        exits = [x for x in timeline if x[0] == "exit"]
        self.assertEqual(len(enters), 2)
        self.assertEqual(len(exits), 2)
        self.assertGreaterEqual(enters[1][2], exits[0][2])


if __name__ == "__main__":
    unittest.main()
