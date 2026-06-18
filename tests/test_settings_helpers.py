import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.gui.widgets import settings


class TestSettingsHelpers(unittest.TestCase):
    def test_get_startup_target_points_to_src_main_in_dev(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            target = settings._get_startup_target()
        self.assertTrue(target.endswith(str(Path("src") / "main.py")))

    def test_get_startup_command_uses_python_in_dev(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            cmd = settings._get_startup_command()
        self.assertIn(settings._get_python_windowless_executable(), cmd)
        self.assertIn(str(Path("src") / "main.py"), cmd)


if __name__ == "__main__":
    unittest.main()
