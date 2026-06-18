import unittest
from datetime import datetime, timedelta
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.gui.main_window import MainWindow, UiState


def _build_fake_window():
    app_state = SimpleNamespace(keep_alive_enabled=True)
    config = SimpleNamespace(app_state=app_state)
    state = SimpleNamespace()
    state.config = config
    state._ui_state = UiState.CONNECTED
    state._pending_recover = False
    state._recover_show_error = True
    state._desired_running = True
    state._recovery_in_progress = False
    state._recovery_fail_count = 0
    state._recovery_next_retry_at = None
    state._recovery_started_at = None
    state._recovery_paused_until = None
    state._last_recovery_time = None
    state._last_recovery_result = "暂无"
    state._last_recovery_error = ""
    state._last_recovery_reason = ""
    state._last_recovery_action = ""
    state.is_running = False
    state.is_connected = False
    state.automator = None
    state.orchestrator = None
    state.connect_calls = []
    state.start_calls = []
    state.stop_calls = []

    class _DummyDashboard:
        def set_orchestrator(self, _orchestrator):
            return None

    state.dashboard_widget = _DummyDashboard()
    state._apply_ui_state = lambda: None
    def _mock_set_ui_state(x):
        state._ui_state = x
        state._apply_ui_state()
    state._set_ui_state = _mock_set_ui_state
    state._log = lambda *_args, **_kwargs: None
    state._connect = lambda show_error=True: state.connect_calls.append(show_error)
    state._start = lambda: state.start_calls.append(True)
    state._stop = lambda persist_auto_state=True: state.stop_calls.append(persist_auto_state)
    state._is_keep_alive_enabled = lambda: bool(getattr(state.config.app_state, "keep_alive_enabled", True))
    state._get_recovery_policy = lambda: {
        "base_backoff": 5,
        "max_backoff": 300,
        "max_failures": 20,
        "pause_minutes": 30,
        "quiet_enabled": False,
        "quiet_start": 0,
        "quiet_end": 0,
    }
    state._in_recovery_quiet_hours = lambda: False
    state._emit_recovery_event = lambda *args, **kwargs: None
    state._resolve_recovery_action = lambda reason: MainWindow._resolve_recovery_action(state, reason)
    return state


class TestRecoveryFlow(unittest.TestCase):
    def test_reset_worker_slot_clears_deleted_worker_reference(self):
        fake = _build_fake_window()

        class _DeletedWorker:
            def isRunning(self):
                raise RuntimeError("wrapped C/C++ object of type QThread has been deleted")

        fake._conn_worker = _DeletedWorker()

        MainWindow._reset_worker_slot(fake, "_conn_worker")

        self.assertIsNone(fake._conn_worker)

    def test_failed_recovery_backoff_increases(self):
        fake = _build_fake_window()

        MainWindow._trigger_recovery(fake, "设备未连接", show_error=False)
        self.assertTrue(fake._recovery_in_progress)
        self.assertEqual(len(fake.connect_calls), 1)

        MainWindow._mark_recovery_failed(fake, "连接失败")
        self.assertFalse(fake._recovery_in_progress)
        self.assertEqual(fake._recovery_fail_count, 1)
        self.assertEqual(fake._last_recovery_result, "失败")
        self.assertEqual(fake._last_recovery_error, "连接失败")

        delta1 = (fake._recovery_next_retry_at - datetime.now()).total_seconds()
        self.assertGreaterEqual(delta1, 4)
        self.assertLessEqual(delta1, 6)

        fake._recovery_in_progress = True
        fake._recovery_started_at = datetime.now()
        MainWindow._mark_recovery_failed(fake, "二次失败")
        self.assertEqual(fake._recovery_fail_count, 2)
        delta2 = (fake._recovery_next_retry_at - datetime.now()).total_seconds()
        self.assertGreaterEqual(delta2, 9)
        self.assertLessEqual(delta2, 11)

    def test_success_recovery_resets_state(self):
        fake = _build_fake_window()
        fake._pending_recover = True
        fake._recovery_in_progress = True
        fake._recovery_fail_count = 3
        fake._recovery_next_retry_at = datetime.now() + timedelta(seconds=60)
        fake._recovery_started_at = datetime.now() - timedelta(seconds=2)
        fake._last_recovery_error = "历史错误"

        MainWindow._mark_recovery_succeeded(fake)
        self.assertFalse(fake._recovery_in_progress)
        self.assertFalse(fake._pending_recover)
        self.assertEqual(fake._recovery_fail_count, 0)
        self.assertIsNone(fake._recovery_next_retry_at)
        self.assertEqual(fake._last_recovery_result, "成功")
        self.assertEqual(fake._last_recovery_error, "")
        self.assertIsNotNone(fake._last_recovery_time)

        snapshot = MainWindow.get_guard_status_snapshot(fake)
        self.assertEqual(snapshot["recovery_fail_count"], 0)
        self.assertEqual(snapshot["last_recovery_result"], "成功")

    def test_trigger_recovery_guard_conditions(self):
        fake = _build_fake_window()

        fake.config.app_state.keep_alive_enabled = False
        MainWindow._trigger_recovery(fake, "设备未连接", show_error=False)
        self.assertEqual(fake.connect_calls, [])
        self.assertFalse(fake._recovery_in_progress)

    def test_failures_reach_threshold_pause(self):
        fake = _build_fake_window()
        fake._get_recovery_policy = lambda: {
            "base_backoff": 1,
            "max_backoff": 10,
            "max_failures": 2,
            "pause_minutes": 1,
            "quiet_enabled": False,
            "quiet_start": 0,
            "quiet_end": 0,
        }
        fake._recovery_in_progress = True
        fake._recovery_started_at = datetime.now()
        MainWindow._mark_recovery_failed(fake, "一次失败")
        self.assertEqual(fake._recovery_fail_count, 1)
        self.assertIsNone(fake._recovery_paused_until)

        fake._recovery_in_progress = True
        fake._recovery_started_at = datetime.now()
        MainWindow._mark_recovery_failed(fake, "二次失败")
        self.assertEqual(fake._recovery_fail_count, 2)
        self.assertIsNotNone(fake._recovery_paused_until)
        self.assertEqual(fake._recovery_next_retry_at, fake._recovery_paused_until)

    def test_quiet_hours_skip_recovery(self):
        fake = _build_fake_window()
        fake._in_recovery_quiet_hours = lambda: True
        MainWindow._trigger_recovery(fake, "设备未连接", show_error=False)
        self.assertEqual(fake.connect_calls, [])
        self.assertFalse(fake._recovery_in_progress)

        fake.config.app_state.keep_alive_enabled = True
        fake._recovery_next_retry_at = datetime.now() + timedelta(seconds=20)
        MainWindow._trigger_recovery(fake, "设备未连接", show_error=False)
        self.assertEqual(fake.connect_calls, [])
        self.assertFalse(fake._recovery_in_progress)

    def test_trigger_recovery_restart_scheduler_when_connected(self):
        fake = _build_fake_window()
        fake.is_connected = True
        fake.is_running = False
        MainWindow._trigger_recovery(fake, "调度未运行", show_error=False)
        self.assertEqual(fake.start_calls, [True])
        self.assertEqual(fake.connect_calls, [])
        self.assertEqual(fake._last_recovery_action, "restart_scheduler")

    def test_trigger_recovery_reconnects_device_when_disconnected(self):
        fake = _build_fake_window()
        fake.is_connected = False
        fake.is_running = False
        MainWindow._trigger_recovery(fake, "设备无响应", show_error=False)
        self.assertEqual(fake.start_calls, [])
        self.assertEqual(fake.connect_calls, [False])
        self.assertEqual(fake._last_recovery_action, "reset_device_session")


if __name__ == "__main__":
    unittest.main()
