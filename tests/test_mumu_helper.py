import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.utils.adb_helper import MuMuHelper


class TestMuMuHelperPaths(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
