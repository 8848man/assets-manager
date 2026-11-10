"""코어 엔진 테스트. 실행: python -m unittest discover -s app/tests"""
import os
import shutil
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
from engine import Library  # noqa: E402
from PIL import Image  # noqa: E402


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(text).lstrip())


def rule(root, rel, nid, inherits=None, rules="", extra=""):
    inh = f"inherits: [{', '.join(inherits)}]\n" if inherits is not None else ""
    write(root, rel, f"---\nid: {nid}\ntype: rule\nkind: set\ntitle: {nid}\n{inh}rules:\n{rules}{extra}---\n본문\n")


def image_ref(root, nid, spec="스펙", sensitivity="public", size=(40, 30), color="red"):
    Image.new("RGB", size, color).save(os.path.join(root, "image", f"{nid}.png"))
    write(root, f"image/{nid}.meta.yaml", f"""
        id: {nid}
        type: image
        title: {nid} 제목
        file: {nid}.png
        summary: 요약
        spec: "{spec}"
        sensitivity: {sensitivity}
    """)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "image"))
        # _defaults는 앱 번들에서 자동 복사된다 (Q17-B)

    def tearDown(self):
        shutil.rmtree(self.root)

    def lib(self):
        return Library(self.root)

    def test_defaults_copied_and_implicit_parent(self):
        rule(self.root, "sets/a.md", "a", None, "  tone: 반말\n")
        L = self.lib()
        self.assertTrue(os.path.exists(os.path.join(self.root, "_defaults", "keys.yaml")))
        self.assertEqual(L.linearize("a"), ["_defaults", "a"])
        e = {x["key"]: x for x in L.assemble("a")["entries"]}
        self.assertEqual(e["tone"]["value"], "반말")
        self.assertEqual(e["tone"]["overridden"][0]["source"], "_defaults")
        self.assertTrue(e["language"]["from_defaults"])  # 기본값 출처 표시 (Q16)

    def test_multiple_inheritance_later_parent_wins(self):
        rule(self.root, "p/doc.md", "doc", ["_defaults"], "  tone: 문서톤\n  length: 길게\n")
        rule(self.root, "p/web.md", "web", ["_defaults"], "  tone: 웹톤\n")
        rule(self.root, "sets/r.md", "r", ["doc", "web"], "  structure: 결론 먼저\n")
        L = self.lib()
        self.assertEqual(L.linearize("r"), ["_defaults", "doc", "web", "r"])
        e = {x["key"]: x for x in L.assemble("r")["entries"]}
        self.assertEqual((e["tone"]["value"], e["tone"]["source"]), ("웹톤", "web"))
        self.assertEqual(e["length"]["source"], "doc")
        self.assertEqual(sorted(L.descendants("doc")), ["r"])

    def test_cycle_detected_and_not_assembled(self):
        rule(self.root, "a.md", "a", ["b"], "  tone: x\n")
        rule(self.root, "b.md", "b", ["a"], "  tone: y\n")
        L = self.lib()
        self.assertIn("a", L.cyclic)
        self.assertTrue(any("순환" in i["message"] for i in L.issues))
        with self.assertRaises(Exception):
            L.assemble("a")

    def test_alias_warning(self):
        rule(self.root, "a.md", "a", None, "  말투: 반말\n")
        L = self.lib()
        w = [i for i in L.issues if i.get("key") == "말투"]
        self.assertEqual(w[0]["suggest"], "tone")

    def test_ref_note_overrides_template_and_paths(self):
        image_ref(self.root, "g")
        rule(self.root, "a.md", "a", None,
             "  chart_style: ref:g\n  palette:\n    value: ref:g\n    note: 특별지시 [{n}] {file}\n")
        L = self.lib()
        p = L.package("a", "codex")
        self.assertIn("특별지시 [1] g.png", p["prompt"])
        self.assertIn("첨부 [2] g.png의 형태", p["prompt"])
        self.assertIn(os.path.join(self.root, "image", "g.png"), p["prompt"])  # Codex는 경로 포함
        q = L.package("a", "chatgpt_web")
        self.assertNotIn("파일 경로", q["prompt"])
        self.assertIn("파일 경로", q["clipboard"])  # 클립보드는 항상 경로 포함
        self.assertEqual(len(q["files"]), 3)

    def test_deterministic(self):
        image_ref(self.root, "g")
        rule(self.root, "a.md", "a", None, "  chart_style: ref:g\n")
        a = self.lib().package("a", "claude_desktop")["prompt"]
        b = self.lib().package("a", "claude_desktop")["prompt"]
        self.assertEqual(a, b)

    def _many_refs(self, specs):
        keys = ["chart_style", "image_style", "palette", "writing_sample", "animation"]
        lines = ""
        for i, (k, spec) in enumerate(zip(keys, specs)):
            image_ref(self.root, f"r{i}", spec=spec)
            lines += f"  {k}: ref:r{i}\n"
        return keys[: len(specs)], lines

    def _limit(self, target, **kw):
        path = os.path.join(self.root, "_defaults", "targets.yaml")
        import yaml
        with open(path, encoding="utf-8") as f:
            d = yaml.safe_load(f)
        d["targets"][target].update(kw)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(d, f, allow_unicode=True)

    def test_trim_step1_spec_replacement_by_priority(self):
        _, lines = self._many_refs(["스펙0", "스펙1", "스펙2"])
        rule(self.root, "a.md", "a", None, lines)
        self.lib()  # 기본값 복사
        self._limit("chatgpt_web", max_files=3)
        L = self.lib()
        p = L.package("a", "chatgpt_web")
        self.assertTrue(p["can_trim"])
        t = L.package("a", "chatgpt_web", trim=True)
        self.assertEqual(t["violations"], [])
        modes = {e["key"]: e["mode"] for e in t["entries"]}
        self.assertEqual(modes["palette"], "text")  # optional이 먼저 텍스트로
        self.assertEqual(modes["chart_style"], "attach")
        self.assertIn("스펙2", t["prompt"])

    def test_trim_step2_contact_sheet_then_step3_drop(self):
        _, lines = self._many_refs(["", "", "", ""])  # spec 없음 → 1단계 불가
        rule(self.root, "a.md", "a", None, lines)
        self.lib()
        self._limit("chatgpt_web", max_files=2)
        L = self.lib()
        t = L.package("a", "chatgpt_web", trim=True)
        self.assertEqual(t["violations"], [])
        self.assertTrue(any("컨택트 시트" in x for x in t["log"]))
        self.assertTrue(os.path.exists(t["files"][1]) and t["files"][1].endswith("contact-sheet.png"))
        self._limit("chatgpt_web", max_files=1)
        L = self.lib()
        t = L.package("a", "chatgpt_web", trim=True)
        self.assertEqual(t["violations"], [])
        self.assertTrue(any("3단계" in x for x in t["log"]))
        required_kept = [e for e in t["entries"] if e["priority"] == "required" and e["mode"] == "drop"]
        self.assertEqual(required_kept, [])

    def test_sensitivity_computed_from_package(self):
        image_ref(self.root, "secret", spec="비밀 스펙", sensitivity="internal")
        rule(self.root, "a.md", "a", None, "  chart_style: ref:secret\n")
        L = self.lib()
        p = L.package("a", "chatgpt_web")
        self.assertEqual(p["sensitive"], ["secret"])
        self.assertTrue(p["needs_strip"])
        s = L.package("a", "chatgpt_web", strip_sensitive=True)
        self.assertEqual(len(s["files"]), 1)
        self.assertIn("비밀 스펙", s["prompt"])
        c = L.package("a", "codex")
        self.assertFalse(c["needs_strip"])  # Codex는 허용

    def test_change_detection_and_impact(self):
        rule(self.root, "g.md", "g", None, "  tone: A\n")
        rule(self.root, "s.md", "s", ["g"], "  length: 짧게\n")
        L = self.lib()
        self.assertEqual({c["status"] for c in L.changes()}, {"new"})
        L.approve()
        self.assertEqual(L.changes(), [])
        rule(self.root, "g.md", "g", None, "  tone: B\n")
        L = self.lib()
        changed = {c["id"]: c for c in L.changes()}
        self.assertEqual(set(changed), {"g", "s"})  # 부모 변경이 자식에 전파
        self.assertIn("+- **톤과 말투**: B", changed["s"]["diff"])
        self.assertEqual(L.descendants("g"), ["s"])

    def test_empty_reference_excluded_with_warning(self):
        write(self.root, "writing/w.txt", "")
        write(self.root, "writing/w.meta.yaml", "id: w\ntype: writing\ntitle: 빈글\nfile: w.txt\n")
        rule(self.root, "a.md", "a", None, "  writing_sample: ref:w\n")
        L = self.lib()
        p = L.package("a", "claude_desktop")
        self.assertEqual(len(p["files"]), 1)
        self.assertTrue(p["warnings"])

    def test_index_and_graph(self):
        image_ref(self.root, "g")
        rule(self.root, "a.md", "a", None, "  chart_style: ref:g\n")
        L = self.lib()
        L.build_index()
        g = L.graph()
        kinds = {e["kind"] for e in g["edges"]}
        self.assertEqual(kinds, {"inherits", "key"})
        self.assertTrue(os.path.exists(os.path.join(self.root, "index.md")))


if __name__ == "__main__":
    unittest.main()
