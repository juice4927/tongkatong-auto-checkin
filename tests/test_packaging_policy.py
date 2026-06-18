"""
Tests for default packaging dependency policy.
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIREMENTS = ROOT / "requirements.txt"
OCR_REQUIREMENTS = ROOT / "requirements-ocr.txt"
BUILD_SPECS = [
    ROOT / "tools" / "build" / "build.spec",
    ROOT / "tools" / "build" / "build_debug.spec",
]

HEAVY_OCR_PACKAGES = {
    "rapidocr_onnxruntime",
    "onnxruntime",
    "opencv-python",
    "cv2",
    "shapely",
}


def _normalized_requirements(path: Path) -> set[str]:
    packages = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for separator in ("==", ">=", "<=", "~=", ">", "<"):
            if separator in line:
                line = line.split(separator, 1)[0]
                break
        packages.add(line.lower())
    return packages


class TestPackagingPolicy(unittest.TestCase):
    def test_default_requirements_do_not_install_heavy_ocr_stack(self):
        packages = _normalized_requirements(DEFAULT_REQUIREMENTS)

        self.assertFalse(packages & HEAVY_OCR_PACKAGES)

    def test_ocr_stack_is_kept_as_optional_requirements(self):
        packages = _normalized_requirements(OCR_REQUIREMENTS)

        self.assertIn("rapidocr_onnxruntime", packages)

    def test_default_pyinstaller_specs_exclude_heavy_ocr_modules(self):
        required_excludes = {
            '"rapidocr_onnxruntime"',
            '"onnxruntime"',
            '"cv2"',
            '"numpy"',
            '"shapely"',
        }

        for spec_path in BUILD_SPECS:
            with self.subTest(spec=spec_path.name):
                content = spec_path.read_text(encoding="utf-8")
                for module in required_excludes:
                    self.assertIn(module, content)


if __name__ == "__main__":
    unittest.main()
