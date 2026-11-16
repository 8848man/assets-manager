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

    def test_version_mismatch_is_not_ready_with_reason(self):
        """Playwright가 기대하는 Chromium 리비전이 없으면 준비 안 됨 + 다시 받으라는 이유."""
        class Chromium:
            def launch(self, **kw):
                raise Exception("BrowserType.launch: Executable doesn't exist at C:\\x\\chromium-1248\\chrome.exe")

        class PW:
            chromium = Chromium()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        render_web._READY.clear()
        with patch("playwright.sync_api.sync_playwright", return_value=PW()), \
                patch.object(render_web, "_playwright_version", return_value="9.9.9"):
            render_web.set_enabled(self.root, True)
            st = render_web.status(self.root)
        self.assertTrue(st["enabled"])
        self.assertFalse(st["ready"])
        self.assertFalse(st["active"])
        self.assertIn("9.9.9", st["reason"])
        self.assertIn("다시 내려받", st["reason"])

    def test_ready_after_successful_launch_is_cached(self):
        calls = []

        class Browser:
            def close(self): pass

        class Chromium:
            def launch(self, **kw):
                calls.append(kw)
                return Browser()

        class PW:
            chromium = Chromium()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        render_web._READY.clear()
        with patch("playwright.sync_api.sync_playwright", return_value=PW()), \
                patch.object(render_web, "_playwright_version", return_value="9.9.8"):
            self.assertTrue(render_web.status(self.root)["ready"])
            self.assertTrue(render_web.status(self.root)["ready"])
        self.assertEqual(calls, [{"headless": True}])  # 추출과 같은 방식, 두 번째는 기억한 결과
        render_web._READY.clear()

    def test_disabled_extract_does_not_launch_browser(self):
        import extract
        html = os.path.join(self.root, "a.html")
        with open(html, "w", encoding="utf-8") as f:
            f.write("<html><body><h1>t</h1></body></html>")
        with patch.dict(os.environ, {"ASSETS_MANAGER_ROOT": self.root}), \
                patch.object(render_web, "_browser_ready", side_effect=AssertionError("launched")):
            out = extract.extract(html)
        self.assertNotIn("rendered", out)
        self.assertNotIn("render_error", out)


if __name__ == "__main__":
    unittest.main()
