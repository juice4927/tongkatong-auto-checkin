import unittest
from pathlib import Path
from unittest.mock import patch
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestVelopackUpdater(unittest.TestCase):
    def test_check_for_updates_explains_non_velopack_install(self):
        from src.utils import velopack_updater

        with patch.object(
            velopack_updater,
            "create_update_manager",
            side_effect=RuntimeError("This application is not properly installed: Could not auto-locate app manifest"),
        ):
            with self.assertRaisesRegex(RuntimeError, "请先下载新版安装包完成一次安装"):
                velopack_updater.check_for_updates("https://github.com/juice4927/tongkatong-auto-checkin")


if __name__ == "__main__":
    unittest.main()
