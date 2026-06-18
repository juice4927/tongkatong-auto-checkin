import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.utils.adb_helper import ADBHelper, MuMuHelper


class TestMuMuHelperPaths(unittest.TestCase):
    def test_find_mumu_adb_prefers_configured_install_dir(self):
        with tempfile.TemporaryDirectory() as td:
            configured = Path(td) / "E_MuMu"
            fallback = Path(td) / "F_MuMu"
            configured_adb = configured / "nx_device" / "12.0" / "shell" / "adb.exe"
            fallback_adb = fallback / "nx_device" / "12.0" / "shell" / "adb.exe"
            configured_adb.parent.mkdir(parents=True)
            fallback_adb.parent.mkdir(parents=True)
            configured_adb.write_text("", encoding="utf-8")
            fallback_adb.write_text("", encoding="utf-8")

            with patch.object(MuMuHelper, "MUMU12_DEFAULT_PATHS", [fallback]):
                self.assertEqual(MuMuHelper().find_mumu_adb(str(configured)), configured_adb)

    def test_find_mumu_adb_accepts_configured_exe_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "MuMuPlayer-12.0"
            manager = root / "nx_main" / "MuMuManager.exe"
            adb = root / "nx_device" / "12.0" / "shell" / "adb.exe"
            manager.parent.mkdir(parents=True)
            adb.parent.mkdir(parents=True)
            manager.write_text("", encoding="utf-8")
            adb.write_text("", encoding="utf-8")

            with patch.object(MuMuHelper, "MUMU12_DEFAULT_PATHS", []):
                self.assertEqual(MuMuHelper().find_mumu_adb(str(manager)), adb)

    def test_find_mumu_exe_accepts_nx_main_directory(self):
        with tempfile.TemporaryDirectory() as td:
            nx_main = Path(td) / "nx_main"
            nx_main.mkdir()
            manager = nx_main / "MuMuManager.exe"
            manager.write_text("", encoding="utf-8")

            with patch.object(MuMuHelper, "MUMU12_DEFAULT_PATHS", []):
                self.assertEqual(MuMuHelper().find_mumu_exe(str(nx_main)), manager)

    def test_launch_mumu_accepts_direct_manager_path(self):
        with tempfile.TemporaryDirectory() as td:
            manager = Path(td) / "MuMuManager.exe"
            manager.write_text("", encoding="utf-8")

            adb = Mock()
            adb.devices.return_value = [{"serial": "127.0.0.1:5555", "status": "device"}]
            helper = MuMuHelper(adb)

            with (
                patch.object(MuMuHelper, "MUMU12_DEFAULT_PATHS", []),
                patch("src.utils.adb_helper.subprocess.run") as run_mock,
            ):
                self.assertTrue(helper.launch_mumu(str(manager), wait_seconds=1))

            first_cmd = run_mock.call_args_list[0].args[0]
            self.assertEqual(Path(first_cmd[0]), manager)


class TestADBHelperDiagnostics(unittest.TestCase):
    def test_connect_reports_offline_device_with_suggestion(self):
        adb = ADBHelper("adb")

        def _fake_run(args, timeout=30):
            if args == ["connect", "127.0.0.1:5555"]:
                return True, "connected to 127.0.0.1:5555"
            if args == ["devices", "-l"]:
                return True, "List of devices attached\n127.0.0.1:5555 offline\n"
            return False, ""

        adb._run_command = _fake_run
        success, message = adb.connect("127.0.0.1", 5555)

        self.assertFalse(success)
        self.assertIn("offline", message)
        self.assertIn("重启", message)


if __name__ == "__main__":
    unittest.main()
