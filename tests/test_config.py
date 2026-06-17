import unittest
import tempfile
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.core.config import ConfigManager, RandomDelayConfig, MuMuConfig, AppStateConfig, CheckinEntry


class TestConfig(unittest.TestCase):
    def test_deep_merge(self):
        cm = ConfigManager(config_dir=Path(tempfile.gettempdir()) / "dummy")
        base = {"a": {"b": 1, "c": 2}, "x": 1}
        override = {"a": {"c": 3}, "y": 2}
        merged = cm._deep_merge(base, override)
        self.assertEqual(merged["a"]["b"], 1)
        self.assertEqual(merged["a"]["c"], 3)
        self.assertEqual(merged["x"], 1)
        self.assertEqual(merged["y"], 2)

    def test_random_delay_validation(self):
        with self.assertRaises(Exception):
            RandomDelayConfig(min_seconds=5, max_seconds=1)

    def test_mumu_port_validation(self):
        with self.assertRaises(Exception):
            MuMuConfig(port=70000)

    def test_load_with_notification_verify_tls_default(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td)
            (cfg_dir / "default.json").write_text(json.dumps({"notification": {"enabled": False, "webhook": ""}}), encoding="utf-8")
            cm = ConfigManager(config_dir=cfg_dir)
            cfg = cm.load()
            self.assertTrue(cfg.notification.verify_tls)

    def test_keep_alive_enabled_backward_compatible_default_true(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td)
            # 模拟旧配置：没有 app_state.keep_alive_enabled
            (cfg_dir / "default.json").write_text(
                json.dumps({"app_state": {"auto_connect": False, "auto_start": False}}),
                encoding="utf-8",
            )
            (cfg_dir / "user_config.json").write_text(
                json.dumps({"app_state": {"auto_connect": True}}),
                encoding="utf-8",
            )
            cm = ConfigManager(config_dir=cfg_dir)
            cfg = cm.load()
            self.assertTrue(cfg.app_state.keep_alive_enabled)

    def test_recovery_policy_validation(self):
        with self.assertRaises(Exception):
            AppStateConfig(
                recovery_base_backoff_seconds=30,
                recovery_max_backoff_seconds=10,
            )
        with self.assertRaises(Exception):
            AppStateConfig(
                recovery_quiet_hours_enabled=True,
                recovery_quiet_start_hour=2,
                recovery_quiet_end_hour=2,
            )


class TestConfigAtomicWrite(unittest.TestCase):
    """测试配置原子写入"""

    def test_save_creates_valid_json(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td)
            (cfg_dir / "default.json").write_text("{}", encoding="utf-8")
            cm = ConfigManager(config_dir=cfg_dir)
            cfg = cm.load()
            cm.save()
            # 验证配置文件存在且为有效 JSON
            self.assertTrue(cm.config_file.exists())
            data = json.loads(cm.config_file.read_text(encoding="utf-8"))
            self.assertIsInstance(data, dict)

    def test_save_no_temp_file_left(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td)
            (cfg_dir / "default.json").write_text("{}", encoding="utf-8")
            cm = ConfigManager(config_dir=cfg_dir)
            cfg = cm.load()
            cm.save()
            # 验证没有临时文件残留
            temp_files = list(cfg_dir.glob("*.tmp"))
            self.assertEqual(len(temp_files), 0)


class TestConfigJsonCorruptionFallback(unittest.TestCase):
    """测试 JSON 损坏降级"""

    def test_corrupted_user_config_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td)
            (cfg_dir / "default.json").write_text("{}", encoding="utf-8")
            # 写入损坏的 JSON
            (cfg_dir / "user_config.json").write_text("{invalid json", encoding="utf-8")
            cm = ConfigManager(config_dir=cfg_dir)
            cfg = cm.load()
            # 应能正常加载，使用默认值
            self.assertIsNotNone(cfg)

    def test_corrupted_user_config_is_backed_up(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td)
            (cfg_dir / "default.json").write_text("{}", encoding="utf-8")
            (cfg_dir / "user_config.json").write_text("{invalid json", encoding="utf-8")
            cm = ConfigManager(config_dir=cfg_dir)
            cm.load()
            # 验证备份文件存在
            backup = cfg_dir / "user_config.json.bak"
            self.assertTrue(backup.exists())


class TestGpsCoordinateValidation(unittest.TestCase):
    """测试 GPS 坐标校验"""

    def test_valid_latitude(self):
        MuMuConfig(gps_latitude=45.0)
        MuMuConfig(gps_latitude=-90.0)
        MuMuConfig(gps_latitude=90.0)
        MuMuConfig(gps_latitude=0.0)

    def test_invalid_latitude(self):
        with self.assertRaises(Exception):
            MuMuConfig(gps_latitude=91.0)
        with self.assertRaises(Exception):
            MuMuConfig(gps_latitude=-91.0)

    def test_valid_longitude(self):
        MuMuConfig(gps_longitude=120.0)
        MuMuConfig(gps_longitude=-180.0)
        MuMuConfig(gps_longitude=180.0)
        MuMuConfig(gps_longitude=0.0)

    def test_invalid_longitude(self):
        with self.assertRaises(Exception):
            MuMuConfig(gps_longitude=181.0)
        with self.assertRaises(Exception):
            MuMuConfig(gps_longitude=-181.0)


class TestGetCheckinTimes(unittest.TestCase):
    """测试 get_checkin_times() 方法"""

    def test_returns_dict_of_checkin_times(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td)
            (cfg_dir / "default.json").write_text("{}", encoding="utf-8")
            cm = ConfigManager(config_dir=cfg_dir)
            cm.load()
            times = cm.get_checkin_times()
            # 应返回 dict，包含 4 个打卡时间配置
            self.assertIsInstance(times, dict)
            self.assertEqual(len(times), 4)
            for key, t in times.items():
                self.assertIsInstance(key, str)
                self.assertIsInstance(t.time_range, tuple)


class TestCheckinEntryValidation(unittest.TestCase):
    """测试 CheckinEntry 校验"""

    def test_valid_time_range(self):
        entry = CheckinEntry(time_range=["07:00", "09:00"])
        self.assertEqual(entry.time_range, ["07:00", "09:00"])

    def test_invalid_time_range_format(self):
        with self.assertRaises(Exception):
            CheckinEntry(time_range=["invalid", "09:00"])

    def test_invalid_time_range_order(self):
        with self.assertRaises(Exception):
            CheckinEntry(time_range=["09:00", "07:00"])


class TestDefaultConfig(unittest.TestCase):
    def test_get_default_config_ignores_user_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td)
            (cfg_dir / "default.json").write_text(
                json.dumps({
                    "app": {"package_name": "com.demo.default"},
                    "checkin": {
                        "morning_signin": {"enabled": True, "time_range": ["07:00", "07:30"], "label": "上午签到"}
                    },
                }),
                encoding="utf-8",
            )
            (cfg_dir / "user_config.json").write_text(
                json.dumps({
                    "app": {"package_name": "com.demo.user"},
                    "random_delay": {"min_seconds": 9, "max_seconds": 12},
                }),
                encoding="utf-8",
            )

            cm = ConfigManager(config_dir=cfg_dir)
            default_cfg = cm.get_default_config()
            user_cfg = cm.load()

            self.assertEqual(default_cfg.app.package_name, "com.demo.default")
            self.assertEqual(user_cfg.app.package_name, "com.demo.user")
            self.assertEqual(default_cfg.random_delay.min_seconds, 1)
            self.assertEqual(default_cfg.checkin["morning_signin"]["time_range"], ["07:00", "07:30"])


if __name__ == "__main__":
    unittest.main()
