import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.core.config import ConfigManager
from src.core.automator import LoginTimeoutError, DeviceConnectionError
from src.core.scheduler import CheckinOrchestrator


class TestSchedulerResilience(unittest.TestCase):
    def test_daily_success_stats_counts_terminal_results(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / "config"
            cm = ConfigManager(config_dir=cfg_dir)
            cm.load()
            cm.config.notification.enabled = False

            automator = Mock()
            holiday_checker = Mock()
            orch = CheckinOrchestrator(automator, holiday_checker, cm)

            orch._record_terminal_result("上午签到", True, "签到成功", "2026-06-18 08:00:00")
            orch._record_terminal_result(
                "上午签退",
                False,
                "按钮定位失败",
                "2026-06-18 12:00:00",
                failure_code="button_not_found",
            )

            stats = orch.get_daily_success_stats()

            self.assertEqual(stats["total"], 2)
            self.assertEqual(stats["success"], 1)
            self.assertEqual(stats["failed"], 1)
            self.assertEqual(stats["success_rate"], 50.0)
            self.assertEqual(stats["failure_codes"], {"button_not_found": 1})

    def test_network_check_tries_multiple_targets(self):
        fake_socket = Mock()
        with patch(
            "src.core.scheduler.socket.create_connection",
            side_effect=[OSError("blocked"), fake_socket],
        ) as create_connection:
            ok = CheckinOrchestrator._check_network_connectivity(
                probes=[("8.8.8.8", 53), ("223.5.5.5", 53)],
                timeout=1,
            )

        self.assertTrue(ok)
        self.assertEqual(create_connection.call_count, 2)
        fake_socket.close.assert_called_once()

    def test_network_check_returns_false_when_all_targets_fail(self):
        with patch(
            "src.core.scheduler.socket.create_connection",
            side_effect=socket.timeout("timeout"),
        ) as create_connection:
            ok = CheckinOrchestrator._check_network_connectivity(
                probes=[("8.8.8.8", 53), ("223.5.5.5", 53)],
                timeout=1,
            )

        self.assertFalse(ok)
        self.assertEqual(create_connection.call_count, 2)

    @patch("time.sleep", return_value=None)
    @patch("src.core.scheduler.notify_checkin_result")
    @patch("src.utils.adb_helper.MuMuHelper.MUMU12_DEFAULT_PATHS", [])
    def test_gps_failure_skips_open_app_and_checkin(self, notify_mock, _sleep):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / "config"
            cm = ConfigManager(config_dir=cfg_dir)
            cm.load()
            cm.config.mumu.gps_latitude = 31.3191
            cm.config.mumu.gps_longitude = 120.5583
            cm.config.notification.enabled = False

            automator = Mock()
            automator.is_connected.return_value = True

            holiday_checker = Mock()
            holiday_checker.is_workday.return_value = True

            orch = CheckinOrchestrator(automator, holiday_checker, cm)
            orch._do_checkin(Mock(value="morning_signin"), "morning_signin")

            automator.open_app.assert_not_called()
            automator.do_checkin.assert_not_called()
            self.assertEqual(len(orch.get_daily_results()), 1)
            self.assertFalse(orch.get_daily_results()[0][1])
            self.assertIn("GPS虚拟定位设置失败", orch.get_daily_results()[0][2])
            notify_mock.assert_called_once()

    def test_gps_prefers_configured_mumu_path_over_default_paths(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / "config"
            cm = ConfigManager(config_dir=cfg_dir)
            cm.load()
            cm.config.mumu.gps_latitude = 31.3191
            cm.config.mumu.gps_longitude = 120.5583
            cm.config.mumu.mumu_exe_path = "E:/MuMuPlayer"

            automator = Mock()
            holiday_checker = Mock()
            orch = CheckinOrchestrator(automator, holiday_checker, cm)

            with (
                patch("src.utils.adb_helper.MuMuHelper.MUMU12_DEFAULT_PATHS", [Path("F:/MuMuPlayer")]),
                patch("src.core.scheduler.Path.exists", autospec=True) as path_exists_mock,
                patch("src.core.scheduler.subprocess.run") as subprocess_run_mock,
            ):
                path_exists_mock.side_effect = lambda path: str(path) in {
                    "E:\\MuMuPlayer\\nx_main\\MuMuManager.exe",
                    "F:\\MuMuPlayer\\nx_main\\MuMuManager.exe",
                }
                subprocess_run_mock.return_value.returncode = 0
                subprocess_run_mock.return_value.stderr = ""

                self.assertTrue(orch._setup_gps(cm.config))
                manager_path = Path(subprocess_run_mock.call_args.args[0][0])
                self.assertEqual(manager_path, Path("E:/MuMuPlayer/nx_main/MuMuManager.exe"))

    def test_gps_accepts_configured_mumu_manager_directory(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / "config"
            cm = ConfigManager(config_dir=cfg_dir)
            cm.load()
            cm.config.mumu.gps_latitude = 31.3191
            cm.config.mumu.gps_longitude = 120.5583
            cm.config.mumu.mumu_exe_path = "E:/MuMuPlayer/nx_main"

            automator = Mock()
            holiday_checker = Mock()
            orch = CheckinOrchestrator(automator, holiday_checker, cm)

            with (
                patch("src.utils.adb_helper.MuMuHelper.MUMU12_DEFAULT_PATHS", [Path("F:/MuMuPlayer")]),
                patch("src.core.scheduler.Path.exists", autospec=True) as path_exists_mock,
                patch("src.core.scheduler.subprocess.run") as subprocess_run_mock,
            ):
                path_exists_mock.side_effect = lambda path: str(path) in {
                    "E:\\MuMuPlayer\\nx_main\\MuMuManager.exe",
                    "F:\\MuMuPlayer\\nx_main\\MuMuManager.exe",
                }
                subprocess_run_mock.return_value.returncode = 0
                subprocess_run_mock.return_value.stderr = ""

                self.assertTrue(orch._setup_gps(cm.config))
                manager_path = Path(subprocess_run_mock.call_args.args[0][0])
                self.assertEqual(manager_path, Path("E:/MuMuPlayer/nx_main/MuMuManager.exe"))

    @patch("time.sleep", return_value=None)
    @patch("src.core.scheduler.notify_checkin_result")
    def test_login_timeout_is_not_retried(self, notify_mock, _sleep):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / "config"
            cm = ConfigManager(config_dir=cfg_dir)
            cm.load()
            cm.config.notification.enabled = False

            automator = Mock()
            automator.is_connected.return_value = True
            automator.open_app.side_effect = LoginTimeoutError("交建通登录超时")

            holiday_checker = Mock()
            holiday_checker.is_workday.return_value = True

            orch = CheckinOrchestrator(automator, holiday_checker, cm)
            orch._do_checkin(Mock(value="morning_signin"), "morning_signin")

            self.assertEqual(automator.open_app.call_count, 1)
            automator.do_checkin.assert_not_called()
            self.assertEqual(len(orch.get_daily_results()), 1)
            self.assertIn("登录失败：", orch.get_daily_results()[0][2])
            self.assertEqual(orch.get_last_result_meta()["failure_code"], "login_timeout")
            notify_mock.assert_called_once()

    @patch("time.sleep", return_value=None)
    @patch("src.core.scheduler.notify_checkin_result")
    def test_device_connect_failure_is_classified(self, notify_mock, _sleep):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / "config"
            cm = ConfigManager(config_dir=cfg_dir)
            cm.load()
            cm.config.notification.enabled = False

            automator = Mock()
            automator.is_connected.return_value = False
            automator.connect.side_effect = DeviceConnectionError(
                "无法连接设备 127.0.0.1:5555",
                failure_code="device_connect_failed",
            )

            holiday_checker = Mock()
            holiday_checker.is_workday.return_value = True

            orch = CheckinOrchestrator(automator, holiday_checker, cm)
            orch._do_checkin(Mock(value="morning_signin"), "morning_signin")

            self.assertGreaterEqual(automator.connect.call_count, 1)
            self.assertEqual(len(orch.get_daily_results()), 1)
            self.assertEqual(orch.get_last_result_meta()["failure_code"], "device_connect_failed")
            self.assertIn("设备连接失败：", orch.get_daily_results()[0][2])
            notify_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
