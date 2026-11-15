"""Q88: Chromium은 사용자가 설정 버튼을 누르기 전에는 설치하지 않는다."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
import render_web  # noqa: E402


class RenderWebTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_off_by_default_and_explicit_toggle(self):
        with patch.object(render_web, "_browser_ready", return_value=True):
            self.assertFalse(render_web.status(self.root)["active"])
            self.assertTrue(render_web.set_enabled(self.root, True)["active"])
            self.assertFalse(render_web.set_enabled(self.root, False)["active"])


if __name__ == "__main__":
    unittest.main()
