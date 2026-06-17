import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestImports(unittest.TestCase):
    def test_import_core(self):
        import src.core.config
        import src.core.scheduler
        import src.core.automator
        import src.core.xml_parser
        import src.core.navigator
        import src.core.button_finder
        import src.core.checkin_verifier
        import src.core.failure_codes

    def test_import_gui(self):
        import src.gui.main_window
        import src.gui.widgets.settings
        import src.gui.widgets.time_config
        import src.gui.workers

    def test_main_entrypoint_has_no_license_gate(self):
        source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")

        self.assertNotIn("src.utils.license", source)
        self.assertNotIn("_check_license", source)
        self.assertNotIn("license.dat", source)
        self.assertFalse((ROOT / "src" / "utils" / "license.py").exists())
        self.assertFalse((ROOT / "tools" / "keygen" / "keygen.py").exists())


if __name__ == "__main__":
    unittest.main()
