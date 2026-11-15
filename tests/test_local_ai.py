"""v3.5 로컬 AI 도구 연결 (Q76-B): 찾기·설정·시험·실행·결과 확인. 실제 도구 대신 가짜 명령줄 도구를 쓴다."""
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
from engine import Library  # noqa: E402
import local_ai  # noqa: E402
import tidy  # noqa: E402

FAKE = r'''#!{py}
import os, re, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("9.9.9 (fake)"); sys.exit(0)
text = " ".join(args)
if "연결 시험" in text:
    print("OK"); sys.exit(0)
m = re.search(r"파일 (\S+) 를 읽고", text)
prompt = open(m.group(1), encoding="utf-8").read()
rid = re.search(r"req-[0-9A-Za-z-]+", prompt).group(0)
if os.environ.get("FAKE_NO_OUTPUT"):
    print("아무것도 안 씀"); sys.exit(0)
os.makedirs(f"inbox/{rid}/out", exist_ok=True)
open(f"inbox/{rid}/out/plan.yaml", "w", encoding="utf-8").write(f"request: {rid}\nsummary: 가짜 정리\nops: []\n")
print("done", rid)
'''


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


class LocalAiTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        write(self.d, "sets/s.md", "---\nid: s\ntype: rule\nkind: set\ntitle: 세트\nrules:\n  tone: 존댓말\n---\n")
        script = write(self.d, "_bin/fake-claude.py", FAKE.replace("{py}", sys.executable))
        if os.name == "nt":  # Windows는 shebang을 모른다: 실제 npm 설치처럼 .cmd로 감싼다 (local_ai가 cmd /c로 실행)
            self.tool = write(self.d, "_bin/fake-claude.cmd", f'@"{sys.executable}" "%~dp0fake-claude.py" %*\n')
        else:
            self.tool = script
            os.chmod(self.tool, os.stat(self.tool).st_mode | stat.S_IEXEC)
        os.environ.pop("FAKE_NO_OUTPUT", None)

    def tearDown(self):
        os.environ.pop("FAKE_NO_OUTPUT", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def connect(self):
        local_ai.update_config(self.d, {"tools": {"claude": {"custom_path": self.tool}, "codex": {"custom_path": os.path.join(self.d, "none")}}})
        return local_ai.status(self.d)

    def wait(self, jid):
        for _ in range(100):
            j = local_ai.get_job(self.d, jid)
            if j["state"] != "running":
                return j
            time.sleep(0.1)
        self.fail("작업이 끝나지 않음")

    def test_detect_status_and_test(self):
        st = self.connect()
        self.assertTrue(st["ready"])
        self.assertEqual(st["default"], "claude")
        c = next(t for t in st["tools"] if t["name"] == "claude")
        self.assertEqual(c["version"], "9.9.9 (fake)")
        self.assertFalse(next(t for t in st["tools"] if t["name"] == "codex")["found"])
        self.assertTrue(local_ai.test(self.d, "claude")["ok"])
        st = local_ai.update_config(self.d, {"tools": {"claude": {"enabled": False}}})
        self.assertFalse(st["ready"])

    def test_not_connected_raises_lookup(self):
        lib = Library(self.d)
        b = tidy.build(lib, "s")
        with self.assertRaises(LookupError):
            local_ai.run_for(lib, {"kind": "tidy", "request": b["request"]})

    def test_run_tidy_and_preview(self):
        self.connect()
        lib = Library(self.d)
        b = tidy.build(lib, "s")
        job = local_ai.run_for(lib, {"kind": "tidy", "request": b["request"]})
        self.assertNotIn('"', job["instruction"])
        j = self.wait(job["id"])
        self.assertEqual(j["state"], "done", j)
        self.assertTrue(j["result_found"])
        pv = tidy.preview(Library(self.d), b["request"])
        self.assertEqual(pv["summary"], "가짜 정리")
        with open(os.path.join(self.d, "inbox", b["request"], "prompt-local.md"), encoding="utf-8") as f:
            prompt = f.read()
        self.assertIn(f"inbox/{b['request']}/out/plan.yaml", prompt)  # 에이전트용 지시

    def test_missing_result_is_failure(self):
        self.connect()
        os.environ["FAKE_NO_OUTPUT"] = "1"
        lib = Library(self.d)
        b = tidy.build(lib, "s")
        j = self.wait(local_ai.run_for(lib, {"kind": "tidy", "request": b["request"]})["id"])
        self.assertEqual(j["state"], "failed")
        self.assertIn("결과 파일이 없습니다", j["error"])
        self.assertIn("아무것도 안 씀", j["log"])

    def test_codex_stdout_plan_is_saved_only_for_matching_request(self):
        rid = "req-20261115-a"
        expected = f"inbox/{rid}/out/plan.yaml"
        text = (f"noise\n```yaml\nrequest: {rid}\nsummary: 안전한 계획\nops: []\n"
                "inline_files:\n  web/example.meta.yaml: |\n    id: example\n    type: web\n```\n")
        self.assertTrue(local_ai._save_stdout_plan(self.d, text, expected, rid))
        with open(os.path.join(self.d, expected), encoding="utf-8") as f:
            self.assertIn("안전한 계획", f.read())
        with open(os.path.join(self.d, "inbox", rid, "out", "web", "example.meta.yaml"), encoding="utf-8") as f:
            self.assertIn("id: example", f.read())
        self.assertFalse(local_ai._save_stdout_plan(self.d, text, "sets/s.md", rid))
        self.assertFalse(local_ai._save_stdout_plan(self.d, text, expected, "req-other"))
        self.assertIn("--sandbox", local_ai.TOOLS["codex"]["args"])
        self.assertIn("read-only", local_ai.TOOLS["codex"]["args"])
        self.assertNotIn("--full-auto", local_ai.TOOLS["codex"]["args"])

    def test_codex_authoring_prepares_preview_request_with_source(self):
        write(self.d, "web/sample.html", "<html><title>Sample</title></html>")
        lib = Library(self.d)
        with patch.object(local_ai, "start", return_value={"id": "fake"}) as start:
            local_ai.run_for(lib, {"kind": "authoring", "files": ["web/sample.html"],
                                   "target": next(iter(lib.targets)), "tool": "codex"})
        root, kind, prompt, ref, tool, expect = start.call_args.args
        self.assertEqual((root, kind, tool), (self.d, "authoring", "codex"))
        self.assertEqual(expect, [f"inbox/{ref['request']}/out/plan.yaml"])
        self.assertTrue(os.path.isfile(prompt))
        self.assertTrue(os.path.isfile(os.path.join(self.d, "inbox", ref["request"], "src", "sample.html")))


if __name__ == "__main__":
    unittest.main()
