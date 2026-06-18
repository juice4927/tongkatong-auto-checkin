import os
import unittest
from pathlib import Path
from unittest.mock import patch
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestVelopackSigningConfig(unittest.TestCase):
    def test_pack_command_includes_signtool_params_from_env(self):
        from tools.build import build

        with patch.dict(os.environ, {"VELOPACK_SIGN_PARAMS": "/fd sha256 /a"}, clear=False):
            cmd = build.build_velopack_pack_command(
                "vpk",
                "2.3.0",
                Path("app"),
                Path("out"),
                Path("notes.md"),
            )

        self.assertIn("--signParams", cmd)
        self.assertIn("/fd sha256 /a", cmd)

    def test_sign_params_and_template_are_mutually_exclusive(self):
        from tools.build import build

        with patch.dict(
            os.environ,
            {
                "VELOPACK_SIGN_PARAMS": "/fd sha256 /a",
                "VELOPACK_SIGN_TEMPLATE": "signtool sign {{file}}",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "只能配置一个"):
                build.signing_args_from_env()


class TestVelopackInstallerConfig(unittest.TestCase):
    def test_pack_command_includes_msi_and_install_location_from_env(self):
        from tools.build import build

        with patch.dict(
            os.environ,
            {
                "VELOPACK_MSI": "true",
                "VELOPACK_INST_LOCATION": "PerMachine",
            },
            clear=False,
        ):
            cmd = build.build_velopack_pack_command(
                "vpk",
                "2.3.0",
                Path("app"),
                Path("out"),
                Path("notes.md"),
            )

        self.assertIn("--msi", cmd)
        self.assertIn("true", cmd)
        self.assertIn("--instLocation", cmd)
        self.assertIn("PerMachine", cmd)

    def test_invalid_install_location_is_rejected(self):
        from tools.build import build

        with patch.dict(os.environ, {"VELOPACK_INST_LOCATION": "Somewhere"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "PerUser、PerMachine 或 Either"):
                build.installer_args_from_env()


if __name__ == "__main__":
    unittest.main()
