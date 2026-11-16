"""v3.8 `am` 콘솔 명령, 명령줄 연결, 터미널 세션, 권한 파일, 변경 감지 (Q79-A·Q80-A)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")
sys.path.insert(0, CORE)
from engine import Library, library_signature  # noqa: E402
import console  # noqa: E402
import store  # noqa: E402
import terminal  # noqa: E402

CLI = os.path.join(CORE, "cli.py")


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        write(self.d, "principles/global.md", "---\nid: global\ntype: rule\nkind: principle\ntitle: 내 전역 원칙\nrules:\n  forbidden: 과장 표현\n---\n본문\n")
        write(self.d, "principles/plain.md", "---\nid: plain\ntype: rule\nkind: principle\ntitle: 쉬운 말\nrules:\n  audience_level: 초등학생\n---\n")
        write(self.d, "sets/blog.md", "---\nid: blog\ntype: rule\nkind: set\ntitle: 블로그\ninherits: [global]\nrules:\n  tone: 친근한 존댓말\n---\n블로그 본문\n")
        write(self.d, "sets/sub.md", "---\nid: sub\ntype: rule\nkind: set\ntitle: 하위\ninherits: [blog]\nrules: {}\n---\n")
        Library(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def am(self, line, actor="user"):
        return console.run_line(self.d, line, actor)

    def test_set_unset_and_undo(self):
        before = read(self.d, "sets/blog.md")
        r = self.am("am set blog tone 차분한 문체")
        self.assertEqual(r["code"], 0, r["output"])
        self.assertEqual(r["changed"], ["blog"])
        self.assertEqual(Library(self.d).nodes["blog"]["rules"]["tone"]["value"], "차분한 문체")
        self.assertIn("블로그 본문", read(self.d, "sets/blog.md"))  # 본문은 그대로
        self.assertEqual(self.am("unset blog tone")["code"], 0)
        self.assertNotIn("tone", Library(self.d).nodes["blog"]["rules"])
        self.am("undo")
        self.am("undo")
        self.assertEqual(read(self.d, "sets/blog.md"), before)
        self.assertIn("되돌릴", self.am("undo")["output"])

    @unittest.skipUnless(shutil.which("git"), "git 없음")
    def test_console_commits_split_by_source(self):
        """명령 전 변경은 이름 없이 '폴더 변경 감지', 명령이 만든 변경은 '콘솔: <명령>'으로 따로 (ISSUE-032·033)."""
        store.backup_sync(Library(self.d))  # 첫 기록
        write(self.d, "principles/plain.md", "---\nid: plain\ntype: rule\nkind: principle\ntitle: 쉬운 말\nrules:\n  audience_level: 중학생\n---\n")
        r = self.am("am set blog tone 차분한 문체")
        self.assertEqual(r["code"], 0, r["output"])
        log = subprocess.run(["git", "log", "-2", "--format=%s|%b<END>"], cwd=self.d, capture_output=True,
                             text=True, encoding="utf-8").stdout.split("<END>")
        after, before = log[0].strip(), log[1].strip()
        self.assertTrue(after.startswith("콘솔: set blog tone"), after)
        self.assertIn("sets/blog.md", after)
        self.assertIn("history/drops.jsonl", after)  # 명령 기록도 같은 커밋
        self.assertNotIn("plain", after.split("|")[0])
        self.assertTrue(before.startswith("규칙 수정: plain"), before)  # 사람이 바꾼 것에는 콘솔 이름이 없다
        self.assertIn("출처: 폴더 변경 감지 (콘솔 명령 전 확인)", before)
        self.assertNotIn("sets/blog.md", before)

    def test_inherit_use_and_cycle_rejected(self):
        self.assertEqual(self.am("use blog plain")["code"], 0)
        L = Library(self.d)
        self.assertEqual(L.nodes["blog"]["uses"], ["plain"])
        entries = {e["key"]: e for e in L.assemble("blog")["entries"]}
        self.assertEqual((entries["audience_level"]["value"], entries["audience_level"]["source"]), ("초등학생", "plain"))
        r = self.am("use blog sub")  # 세트는 부품이 아니다
        self.assertEqual(r["code"], 2)
        r = self.am("inherit blog sub")  # blog ← sub ← blog : 순환
        self.assertEqual(r["code"], 2, r["output"])
        self.assertEqual(Library(self.d).nodes["blog"]["inherits"], ["global"])
        self.assertEqual(self.am("unuse blog plain")["code"], 0)
        self.assertNotIn("uses", read(self.d, "sets/blog.md"))

    def test_new_rename_and_history(self):
        r = self.am("new set memo 메모 앱 만들기", actor="ai-tab")
        self.assertEqual(r["code"], 0, r["output"])
        L = Library(self.d)
        self.assertEqual(L.nodes["memo"]["inherits"], ["global"])  # 세트는 기본으로 전역 원칙을 상속
        self.assertEqual(self.am("new set memo 중복")["code"], 2)
        self.am("rename memo 메모 앱")
        self.assertEqual(Library(self.d).nodes["memo"]["title"], "메모 앱")
        hist = [h for h in store.read_history(self.d) if h.get("kind") == "console"]
        self.assertEqual(hist[0]["actor"], "ai-tab")
        self.assertEqual(hist[0]["changed"], ["memo"])
        self.am("undo")
        self.am("undo")
        self.assertFalse(os.path.exists(os.path.join(self.d, "sets/memo.md")))

    def test_show_ls_errors(self):
        out = self.am("show sub")["output"]
        self.assertIn("_defaults → global → blog → sub", out)
        self.assertIn("tone", out)
        self.assertIn("blog", self.am("ls sets")["output"])
        self.assertEqual(self.am("bogus")["code"], 2)
        self.assertEqual(self.am("set nope tone x")["code"], 2)
        self.assertEqual(self.am('set blog tone "따옴표')["code"], 2)

    def test_cli_routes_to_console(self):
        env = dict(os.environ, AM_ACTOR="ai-tab", PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, CLI, "--root", self.d, "set", "blog", "tone", "명령줄"], capture_output=True, text=True,
                           encoding="utf-8", env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(Library(self.d).nodes["blog"]["rules"]["tone"]["value"], "명령줄")
        p = subprocess.run([sys.executable, CLI, "--root", self.d, "standard", "블로그"], capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("작업 지침", p.stdout)

    def test_signature_changes_only_with_files(self):
        a = library_signature(self.d)
        write(self.d, "index.md", "x")       # 앱이 만드는 파일은 무시
        write(self.d, "history/drops.jsonl", "{}\n")
        self.assertEqual(library_signature(self.d), a)
        time.sleep(0.01)
        self.am("set blog tone 바뀜")
        self.assertNotEqual(library_signature(self.d), a)


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        terminal.close_all()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_permissions_merge_keeps_user_entries(self):
        write(self.d, ".claude/settings.json", json.dumps({"permissions": {"allow": ["Read(./x)"]}, "model": "opus"}))
        r = terminal.ensure_permissions(self.d)
        self.assertTrue(r["ok"])
        cfg = json.loads(read(self.d, ".claude/settings.json"))
        self.assertEqual(cfg["model"], "opus")
        self.assertIn("Read(./x)", cfg["permissions"]["allow"])
        self.assertIn("Bash(am *)", cfg["permissions"]["allow"])
        self.assertIn("Edit(./sets/**)", cfg["permissions"]["deny"])
        self.assertEqual(terminal.ensure_permissions(self.d)["added"], [])  # 두 번째는 그대로
        write(self.d, ".claude/settings.json", "{ broken")
        self.assertFalse(terminal.ensure_permissions(self.d)["ok"])
        self.assertEqual(read(self.d, ".claude/settings.json"), "{ broken")

    def test_shims(self):
        d = terminal.write_shims(self.d)
        self.assertIn("cli.py", read(self.d, ".assets/bin/am"))
        self.assertTrue(os.path.isfile(os.path.join(d, "am.cmd")))

    @unittest.skipIf(os.name == "nt", "POSIX pty")
    def test_session_echo_env_and_limit(self):
        r = terminal.start(self.d, "test", cmd=["sh", "-c", 'echo "actor=$AM_ACTOR"; read a; echo "got:$a"'])
        self.assertTrue(r.get("ok"), r)
        s = terminal.get(r["session"]["id"])
        busy = terminal.start(self.d, "test", cmd=["sh", "-c", "sleep 5"])
        self.assertIn("busy", busy)  # AI 탭은 기본 1개
        out = s.read(0, 3)
        self.assertIn("actor=ai-tab", out["data"])
        s.write("한글\n")
        deadline = time.time() + 5
        text = out["data"]
        nxt = out["next"]
        while time.time() < deadline and "got:한글" not in text:
            o = s.read(nxt, 1)
            text += o["data"]
            nxt = o["next"]
        self.assertIn("got:한글", text)
        for _ in range(50):
            if not s.read(nxt)["alive"]:
                break
            time.sleep(0.1)
        self.assertFalse(s.read(nxt)["alive"])
        terminal.close(r["session"]["id"])
        self.assertEqual(terminal.list_sessions(), [])

    def test_unknown_tool(self):
        if not terminal.available()["ok"]:
            self.skipTest("pty 없음")
        self.assertIn("error", terminal.start(self.d, "nope"))


if __name__ == "__main__":
    unittest.main()
