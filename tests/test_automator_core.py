import unittest
import time
from pathlib import Path
import sys
from unittest.mock import Mock, PropertyMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.automator import (
    DeviceSession, CheckinResult, CheckinAction,
    UIAutomator2Impl, AlreadyCheckedInError, GpsLocationError, DeviceConnectionError
)


class TestDeviceSession(unittest.TestCase):
    """测试 DeviceSession TTL 逻辑"""

    def test_session_not_expired_within_ttl(self):
        """会话在 TTL 内不应过期"""
        mock_device = object()
        session = DeviceSession(mock_device, ttl_seconds=300)
        self.assertFalse(session.is_expired())

    def test_session_expired_after_ttl(self):
        """会话超过 TTL 后应过期"""
        mock_device = object()
        session = DeviceSession(mock_device, ttl_seconds=0)
        time.sleep(0.01)
        self.assertTrue(session.is_expired())

    def test_session_default_ttl(self):
        """默认 TTL 应为 300 秒"""
        mock_device = object()
        session = DeviceSession(mock_device)
        self.assertEqual(session.ttl, 300)


class TestAlreadyCheckedInRanges(unittest.TestCase):
    """测试 _ALREADY_CHECKED_IN_RANGES 时间范围判断"""

    def setUp(self):
        self.ranges = UIAutomator2Impl._ALREADY_CHECKED_IN_RANGES

    def test_morning_signin_range(self):
        """上午签到：05:00-08:00"""
        checker = self.ranges[(True, True)]
        self.assertTrue(checker(5, 0))
        self.assertTrue(checker(7, 59))
        self.assertFalse(checker(4, 59))
        self.assertFalse(checker(8, 0))

    def test_morning_signout_range(self):
        """上午签退：11:30-13:00"""
        checker = self.ranges[(True, False)]
        self.assertTrue(checker(11, 30))
        self.assertTrue(checker(13, 0))
        self.assertFalse(checker(11, 29))
        self.assertFalse(checker(13, 1))

    def test_afternoon_signin_range(self):
        """下午签到：12:00-13:30"""
        checker = self.ranges[(False, True)]
        self.assertTrue(checker(12, 0))
        self.assertTrue(checker(13, 30))
        self.assertFalse(checker(11, 59))
        self.assertFalse(checker(13, 31))

    def test_afternoon_signout_range(self):
        """下午签退：17:00-次日04:00"""
        checker = self.ranges[(False, False)]
        self.assertTrue(checker(17, 0))
        self.assertTrue(checker(23, 59))
        self.assertTrue(checker(0, 0))
        self.assertTrue(checker(3, 59))
        self.assertFalse(checker(16, 59))
        self.assertFalse(checker(4, 0))


class TestCheckinResult(unittest.TestCase):
    """测试 CheckinResult 数据类"""

    def test_create_success_result(self):
        result = CheckinResult(
            success=True,
            action=CheckinAction.MORNING_SIGNIN,
            message="打卡成功",
            timestamp="2026-04-13 08:30:00"
        )
        self.assertTrue(result.success)
        self.assertEqual(result.action, CheckinAction.MORNING_SIGNIN)

    def test_create_result_with_screenshot(self):
        result = CheckinResult(
            success=True,
            action=CheckinAction.MORNING_SIGNIN,
            message="打卡成功",
            timestamp="2026-04-13 08:30:00",
            screenshot_path="/path/to/screenshot.png"
        )
        self.assertEqual(result.screenshot_path, "/path/to/screenshot.png")

    def test_create_result_without_screenshot(self):
        result = CheckinResult(
            success=False,
            action=CheckinAction.AFTERNOON_SIGNOUT,
            message="打卡失败",
            timestamp="2026-04-13 18:00:00"
        )
        self.assertIsNone(result.screenshot_path)


class TestGpsLocationError(unittest.TestCase):
    """测试 GpsLocationError 异常类"""

    def test_exception_message(self):
        err = GpsLocationError("打卡失败: 超出距离")
        self.assertEqual(str(err), "打卡失败: 超出距离")

    def test_exception_is_exception(self):
        self.assertTrue(issubclass(GpsLocationError, Exception))


class TestAlreadyCheckedInError(unittest.TestCase):
    """测试 AlreadyCheckedInError 异常类"""

    def test_error_with_checkin_time(self):
        err = AlreadyCheckedInError("已打卡", in_correct_slot=True, checkin_time="08:30")
        self.assertTrue(err.in_correct_slot)
        self.assertEqual(err.checkin_time, "08:30")

    def test_error_without_checkin_time(self):
        err = AlreadyCheckedInError("已打卡")
        self.assertTrue(err.in_correct_slot)
        self.assertIsNone(err.checkin_time)


class TestDeviceConnectionClassification(unittest.TestCase):
    def test_connection_error_keeps_failure_code(self):
        err = DeviceConnectionError("无法连接设备", failure_code="device_connect_failed")
        self.assertEqual(err.failure_code, "device_connect_failed")

    def test_is_connected_marks_unresponsive_device(self):
        automator = UIAutomator2Impl()
        automator._connected = True
        automator.device = Mock()
        type(automator._device).device_info = PropertyMock(side_effect=RuntimeError("offline"))

        self.assertFalse(automator.is_connected())
        self.assertEqual(automator._last_connection_failure_code, "device_unresponsive")

    def test_connect_uses_configured_adb_path_for_uiautomator2(self):
        adb_path = r"E:\Program Files\Netease\MuMuPlayer-12.0\nx_main\adb.exe"

        fake_device = Mock()
        type(fake_device).device_info = PropertyMock(return_value={"brand": "MuMu", "model": "12"})
        fake_device.info = Mock()

        with (
            patch("src.core.automator.ADBHelper") as adb_helper_cls,
            patch("src.core.automator.u2.connect", return_value=fake_device) as u2_connect,
        ):
            helper_mock = adb_helper_cls.return_value
            helper_mock.connect.return_value = (True, "connected to 127.0.0.1:5555")
            helper_mock.devices.return_value = [
                {"serial": "127.0.0.1:5555", "status": "device"}
            ]
            helper_mock._run_command.return_value = (True, "ok")

            automator = UIAutomator2Impl(adb_path=adb_path)
            self.assertTrue(automator.connect())

            helper_mock.connect.assert_any_call("127.0.0.1", 5555)
            u2_connect.assert_any_call("127.0.0.1:5555")


if __name__ == "__main__":
    unittest.main()
