import unittest
from pathlib import Path

from src.utils.app_updater import (
    UpdateError,
    _build_self_update_bat_lines,
    parse_update_asset,
)


class TestAppUpdater(unittest.TestCase):
    def test_parse_update_asset_prefers_opensource_and_default_entries(self):
        manifest = {
            "version": "2.3.0",
            "assets": {
                "default": {
                    "file_name": "fallback.exe",
                    "url": "https://example.test/fallback.exe",
                    "sha256": "ABCDEF",
                    "size": 12,
                },
                "opensource": {
                    "file_name": "app.exe",
                    "url": "https://example.test/app.exe",
                    "sha256": "123456",
                    "size": 34,
                },
            },
            "delta": {
                "from_version": "2.2.9",
                "default": {
                    "url": "https://example.test/fallback.patch",
                    "sha256": "ABC",
                    "size": 1,
                },
                "opensource": {
                    "url": "https://example.test/app.patch",
                    "sha256": "DEF",
                    "size": 2,
                },
            },
        }

        asset = parse_update_asset(manifest, "opensource", "https://example.test/version.json")

        self.assertEqual(asset.version, "2.3.0")
        self.assertEqual(asset.file_name, "app.exe")
        self.assertEqual(asset.url, "https://example.test/app.exe")
        self.assertEqual(asset.sha256, "123456")
        self.assertEqual(asset.size, 34)
        self.assertEqual(asset.delta_from_version, "2.2.9")
        self.assertEqual(asset.delta_url, "https://example.test/app.patch")
        self.assertEqual(asset.delta_sha256, "def")
        self.assertEqual(asset.delta_size, 2)

    def test_parse_update_asset_rejects_removed_license_edition_keys(self):
        manifest = {
            "version": "2.3.0",
            "assets": {
                "licensed": {
                    "file_name": "licensed.exe",
                    "url": "https://example.test/licensed.exe",
                },
                "unlicensed": {
                    "file_name": "unlicensed.exe",
                    "url": "https://example.test/unlicensed.exe",
                },
            },
        }

        with self.assertRaisesRegex(UpdateError, "缺少可用下载项"):
            parse_update_asset(manifest, "opensource", "https://example.test/version.json")

    def test_self_update_bat_set_lines_are_closed(self):
        lines = _build_self_update_bat_lines(
            source_path=Path(r"C:\Temp\new app.exe"),
            target_path=Path(r"C:\Program Files\TongKaTong\tongkatong.exe"),
            state_path=Path(r"C:\Program Files\TongKaTong\update_state.json"),
            update_log_path=Path(r"C:\Program Files\TongKaTong\logs\update.log"),
            current_pid=1234,
            target_version="2.3.0",
            current_version="2.2.9",
        )

        set_lines = [line for line in lines if line.startswith('set "')]

        self.assertIn(r'set "source=C:\Temp\new app.exe"', set_lines)
        self.assertIn(r'set "target=C:\Program Files\TongKaTong\tongkatong.exe"', set_lines)
        self.assertTrue(set_lines)
        self.assertTrue(all(line.endswith('"') for line in set_lines))


if __name__ == "__main__":
    unittest.main()
