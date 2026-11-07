"""v3.6 스킬 연동 (Q77-A): 도구 스킬 설치, 세트 → 스킬 내보내기(결정적·호환 프런트매터·고친 흔적 감지), 설치된 스킬 읽기."""
import os
import shutil
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
from engine import Library  # noqa: E402
import skills  # noqa: E402


def write(root, rel, text, mode="w"):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        f.write(text)
    return p


def fm(path):
    with open(path, encoding="utf-8") as f:
        t = f.read()
    return yaml.safe_load(t.split("---\n")[1]), t


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        os.environ["HOME"] = os.environ["USERPROFILE"] = self.home
        write(self.d, "sets/blog_post.md", "---\nid: blog_post\ntype: rule\nkind: set\ntitle: 블로그 글\nrules:\n  tone: 친근하게\n"
              "  image_style: ref:pic\n  palette: ref:secret\n---\n블로그 글을 쓸 때 드래그하는 세트입니다.\n")
        write(self.d, "image/pic.png", b"\x89PNG fake", "wb")
        write(self.d, "image/pic.meta.yaml", "id: pic\ntype: image\ntitle: 그림\nfile: pic.png\nspec: 그림 설명\n")
        write(self.d, "image/secret.png", b"\x89PNG s", "wb")
        write(self.d, "image/secret.meta.yaml", "id: secret\ntype: image\ntitle: 사내 팔레트\nfile: secret.png\nspec: 사내 색\nsensitivity: internal\n")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.d, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def test_tool_skill_install_is_idempotent(self):
        w = skills.install_tool_skill(self.d)
        self.assertIn(".claude/skills/assets-manager/SKILL.md", w)
        self.assertIn(".agents/skills/assets-manager/scripts/extract.py", w)
        self.assertEqual(skills.install_tool_skill(self.d), [])
        meta, _ = fm(os.path.join(self.d, ".claude/skills/assets-manager/SKILL.md"))
        self.assertEqual(meta["name"], "assets-manager")
        with open(os.path.join(self.d, ".gitignore"), encoding="utf-8") as f:
            self.assertIn(".claude/skills/assets-manager/", f.read())
        self.assertNotIn("assets-manager", " ".join(Library(self.d).nodes))  # 숨김 폴더는 노드가 아님

    def test_compile_is_portable_and_deterministic(self):
        L = Library(self.d)
        a, b = skills.compile_set(L, "blog_post"), skills.compile_set(Library(self.d), "blog_post")
        self.assertEqual(a["skill_md"], b["skill_md"])
        self.assertEqual(a["name"], "blog-post")
        meta = yaml.safe_load(a["skill_md"].split("---\n")[1])
        self.assertEqual(set(meta), {"name", "description", "metadata"})  # 공개 형식 키만
        self.assertLessEqual(len(meta["description"]), 1024)
        self.assertEqual(meta["metadata"]["set"], "blog_post")
        self.assertIn("references/pic.png", a["files"])
        self.assertNotIn("references/secret.png", a["files"])  # P8 내부 자료는 파일로 안 나감
        self.assertTrue(any("내부 자료" in w for w in a["warnings"]))
        self.assertIn("## 진행 방식", a["skill_md"])  # 게이트·자가 점검 포함
        self.assertTrue(a["skill_md"].rstrip().endswith("모두 지켰는가"))

    def test_export_edit_detection_and_remove(self):
        L = Library(self.d)
        r = skills.export(L, "blog_post", "personal", ["claude", "agents"])
        self.assertTrue(r["ok"])
        p = os.path.join(self.home, ".claude", "skills", "blog-post", "SKILL.md")
        self.assertTrue(os.path.exists(p))
        self.assertTrue(os.path.exists(os.path.join(self.home, ".agents", "skills", "blog-post", "references", "pic.png")))
        st = skills.status(L, "blog_post")
        self.assertEqual({(x["scope"], x["state"]) for x in st["installs"] if x["state"] != "absent"}, {("personal", "ok")})
        inst = skills.scan(L)
        one = next(s for s in inst if s["name"] == "blog-post")
        self.assertTrue(one["managed"])
        self.assertEqual(len(one["locations"]), 2)  # Claude용과 .agents용을 한 노드로
        g = skills.graph_part(L, inst)
        self.assertTrue(any(e["source"] == "blog_post" for e in g["edges"]))
        # 사용자가 직접 고치면 덮어쓰기 전에 멈춘다
        write(self.home, ".claude/skills/blog-post/SKILL.md", "---\nname: blog-post\ndescription: 내가 고침\n---\n고친 본문\n")
        r2 = skills.export(Library(self.d), "blog_post", "personal", ["claude"])
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["conflicts"][0]["state"], "edited")
        self.assertIn("고친 본문", r2["conflicts"][0]["diff"])
        self.assertEqual(skills.remove(L, "blog_post", "personal", ["claude"])["refused"], [os.path.dirname(p)])
        # 세트가 바뀌면 '다시 내보내기 필요'
        write(self.d, "sets/blog_post.md", "---\nid: blog_post\ntype: rule\nkind: set\ntitle: 블로그 글\nrules:\n  tone: 더 친근하게\n---\n")
        agents = next(s for s in skills.scan(Library(self.d)) if s["name"] == "blog-post")
        self.assertTrue(agents["stale"])
        self.assertTrue(skills.export(Library(self.d), "blog_post", "personal", ["claude"], overwrite=True)["ok"])
        res = skills.remove(Library(self.d), "blog_post", "personal", ["claude", "agents"])
        self.assertEqual(len(res["removed"]), 2)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "skills", "blog-post")))

    def test_foreign_skill_not_overwritten_and_scanned(self):
        write(self.home, ".claude/skills/blog-post/SKILL.md", "---\nname: blog-post\ndescription: 남의 스킬\n---\n본문\n")
        write(self.home, ".claude/skills/other/SKILL.md", "---\nname: other\ndescription: 다른 스킬\n---\n")
        r = skills.export(Library(self.d), "blog_post", "personal", ["claude"])
        self.assertEqual(r["conflicts"][0]["state"], "foreign")
        names = {s["name"]: s for s in skills.scan(Library(self.d))}
        self.assertFalse(names["other"]["managed"])
        self.assertEqual(names["other"]["description"], "다른 스킬")


if __name__ == "__main__":
    unittest.main()
