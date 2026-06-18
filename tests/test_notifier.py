import unittest
from unittest.mock import patch
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.utils.notifier import send_serverchan


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data


class TestNotifier(unittest.TestCase):
    def test_send_success(self):
        with patch("src.utils.notifier.requests.post") as post:
            post.return_value = _Resp(200, {"code": 0})
            ok = send_serverchan("SCTxxxx", "t", "d", verify_tls=True)
            self.assertTrue(ok)

    def test_send_http_error(self):
        with patch("src.utils.notifier.requests.post") as post, patch("src.utils.notifier.time.sleep") as slp:
            slp.return_value = None
            post.return_value = _Resp(500, {"code": 0}, "oops")
            ok = send_serverchan("SCTxxxx", "t", "d", verify_tls=True)
            self.assertFalse(ok)

    def test_send_non_json(self):
        with patch("src.utils.notifier.requests.post") as post:
            post.return_value = _Resp(200, ValueError("bad json"), "not-json")
            ok = send_serverchan("SCTxxxx", "t", "d", verify_tls=True)
            self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
