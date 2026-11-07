"""v3.7 스킬을 라이브러리에 보관·연결 (Q78-A), 작업 → 표준 찾기, AI가 Assets Manager를 부르게 하기."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
from engine import Library  # noqa: E402
import skills  # noqa: E402
import standards  # noqa: E402
import drops  # noqa: E402
import tidy  # noqa: E402

CLI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core", "cli.py")


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


class StandardsTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        os.environ["HOME"] = os.environ["USERPROFILE"] = self.home
        write(self.d, "principles/global.md", "---\nid: global\ntype: rule\nkind: principle\ntitle: 내 전역 원칙\nrules:\n  forbidden: 과장 표현\n---\n")
        write(self.d, "sets/blog.md", "---\nid: blog\ntype: rule\nkind: set\ntitle: 네이버 블로그 포스팅\ninherits: [global]\n"
              "rules:\n  tone: 친근한 존댓말\n---\n네이버 블로그 글을 쓸 때 쓰는 세트입니다.\n")
        write(self.d, "sets/dev.md", "---\nid: dev\ntype: rule\nkind: set\ntitle: 앱 개발 착수\ninherits: [global]\n"
              "rules:\n  output_format: 설계 제안서\n---\n개발을 시작할 때 쓰는 세트입니다.\n")
        write(self.d, "skills/pdf-tools/SKILL.md", "---\nname: pdf-tools\ndescription: PDF를 나누고 합칠 때 쓰는 절차\n---\n1. pdftk로 나눈다\n")
        write(self.d, "skills/pdf-tools/scripts/split.py", "print('x')\n")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.d, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def test_skill_node_attach_and_assemble(self):
        L = Library(self.d)
        sk = L.nodes["pdf-tools"]
        self.assertEqual((sk["type"], sk["kind"]), ("skill", "skill"))
        self.assertIn("scripts/split.py", sk["files"])
        self.assertFalse(any(i["level"] == "error" for i in L.issues), L.issues)
        skills.attach(L, "pdf-tools", "global")
        L = Library(self.d)
        self.assertEqual(L.nodes["global"]["uses"], ["pdf-tools"])
        self.assertFalse([i for i in L.issues if "uses" in i["message"]])
        self.assertEqual(L.skills_of("blog"), ["pdf-tools"])  # 전역에 붙이면 상속으로 모든 세트에
        self.assertEqual(set(L.descendants("pdf-tools")), {"global", "blog", "dev"})
        agent = L.package("blog", "codex", write=False)["prompt"]
        self.assertIn("## 함께 쓰는 스킬", agent)
        self.assertIn(os.path.join(self.d, "skills", "pdf-tools", "SKILL.md"), agent)
        self.assertNotIn("pdftk", agent)  # 본문은 넣지 않는다 (필요할 때 읽게)
        self.assertTrue(agent.rstrip().endswith("모두 지켰는가"))
        chat = L.package("blog", "chatgpt_web", write=True)
        self.assertIn("pdf-tools.SKILL.md", chat["prompt"])
        self.assertTrue(any(f.endswith("pdf-tools.SKILL.md") and os.path.exists(f) for f in chat["files"]))
        edges = {(e["source"], e["target"], e["label"]) for e in L.graph()["edges"]}
        self.assertIn(("global", "pdf-tools", "스킬"), edges)
        skills.attach(L, "pdf-tools", "global", detach=True)
        self.assertEqual(Library(self.d).skills_of("blog"), [])

    def test_find_standard_and_cli(self):
        L = Library(self.d)
        self.assertEqual(standards.find(L, "네이버 블로그 여름 휴가 글")[0]["id"], "blog")
        self.assertEqual(standards.find(L, "새 앱 개발 설계")[0]["id"], "dev")
        r = standards.standard(L, "오늘 저녁 메뉴 추천")
        self.assertTrue(r["found"] and r["fallback"])
        self.assertEqual(r["set"], "global")
        a, b = standards.standard(L, "블로그 글"), standards.standard(Library(self.d), "블로그 글")
        self.assertEqual(a["text"], b["text"])  # 결정적
        p = subprocess.run([sys.executable, CLI, "--root", self.d, "standard", "블로그 글 써줘"], capture_output=True, text=True,
                           encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("네이버 블로그 포스팅", p.stdout)
        self.assertIn("## 진행 방식", p.stdout)

    def test_import_installed_skill_disables_original(self):
        src = write(self.home, ".claude/skills/tone-guide/SKILL.md", "---\nname: tone-guide\ndescription: 말투 지침\n---\n존댓말\n")
        write(self.home, ".claude/skills/tone-guide/examples.md", "예시\n")
        res = skills.import_skill(Library(self.d), os.path.dirname(src), attach_to="blog")
        self.assertTrue(res["disabled_original"])
        self.assertFalse(os.path.exists(src))
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(src), skills.DISABLED)))
        L = Library(self.d)
        self.assertEqual(L.nodes["tone-guide"]["origin"]["origin"], os.path.dirname(src))
        self.assertIn("examples.md", L.nodes["tone-guide"]["files"])
        self.assertIn("tone-guide", L.nodes["blog"]["uses"])
        self.assertNotIn("tone-guide", {s["name"] for s in skills.scan(L)})  # 원래 것은 꺼져서 두 번 안 보임
        skills.restore_original(L, "tone-guide")
        self.assertTrue(os.path.exists(src))

    def test_drop_skill_folder_on_node(self):
        sd = os.path.join(self.home, "drop", "writing-rules")
        write(sd, "SKILL.md", "---\nname: writing-rules\ndescription: 글쓰기 규칙\n---\n짧게\n")
        L = Library(self.d)
        st = drops.stage(L, "global", [sd], [], True)
        self.assertEqual(st["options"][0]["id"], "skill_import")
        r = drops.commit(Library(self.d), st["sid"], "skill_import", {"disable_original": False})
        self.assertEqual(r["created"], ["skills/writing-rules"])
        self.assertTrue(os.path.exists(os.path.join(sd, "SKILL.md")))  # 끄지 않음 선택
        self.assertIn("writing-rules", Library(self.d).nodes["global"]["uses"])

    def test_caller_personal_skill_and_rule_lines(self):
        root = self.d
        write(self.home, ".claude/CLAUDE.md", "# 내 지침\n기존 내용\n")
        st = skills.caller_apply(root, "rule_claude", "install")
        st = skills.caller_apply(root, "rule_claude", "install")  # 두 번 해도 한 번만
        with open(os.path.join(self.home, ".claude", "CLAUDE.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text.count(skills.RULE_START), 1)
        self.assertIn("기존 내용", text)
        self.assertTrue(os.path.exists(os.path.join(self.home, ".claude", "CLAUDE.md.assets-manager.bak")))
        skills.caller_apply(root, "skill_claude", "install")
        with open(os.path.join(self.home, ".claude", "skills", "assets-manager", "SKILL.md"), encoding="utf-8") as f:
            sk = f.read()
        self.assertIn(skills.CLI, sk)
        self.assertNotIn("{CLI}", sk)
        st = skills.caller_status(root)
        self.assertTrue(all(i["installed"] for i in st["items"] if i["id"] in ("rule_claude", "skill_claude")))
        skills.caller_apply(root, "rule_claude", "remove")
        skills.caller_apply(root, "skill_claude", "remove")
        with open(os.path.join(self.home, ".claude", "CLAUDE.md"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "# 내 지침\n기존 내용")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "skills", "assets-manager")))

    def test_tidy_goal_skill_to_principle(self):
        L = Library(self.d)
        skills.attach(L, "pdf-tools", "blog")
        b = tidy.build(Library(self.d), "blog", "chatgpt_web", {"kind": "skill_to_principle", "skill": "pdf-tools"})
        self.assertIn("스킬을 원칙으로 풀기", b["prompt"])
        self.assertIn("pdftk", b["prompt"])
        self.assertIn("pdf-tools", b["scope"])


if __name__ == "__main__":
    unittest.main()


class SkillTrashTests(unittest.TestCase):
    def test_delete_skill_moves_folder(self):
        import store
        d = tempfile.mkdtemp()
        try:
            write(d, "skills/x/SKILL.md", "---\nname: x\ndescription: d\n---\nb\n")
            write(d, "skills/x/ref/a.md", "a\n")
            r = store.delete_node(Library(d), "x")
            self.assertEqual(sorted(r["files"]), ["skills/x/SKILL.md", "skills/x/ref/a.md"])
            self.assertFalse(os.path.exists(os.path.join(d, "skills", "x")))
            store.restore_trash(d, r["trash_id"])
            self.assertIn("x", Library(d).nodes)
        finally:
            shutil.rmtree(d, ignore_errors=True)
