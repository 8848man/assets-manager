"""v3.10 (Q82-A): 이미지 키는 원본(+비슷한 원본)만 ImageGen 참조로, 스타일 글·점검·요소 없이."""
import os
import shutil
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
from engine import Library, IMAGE_CHECK  # noqa: E402
from test_engine import image_ref, rule, write  # noqa: E402


def similar(root, nid, target, spec="", file=True):
    if file:
        image_ref(root, nid, spec=spec, color="blue")
    with open(os.path.join(root, "image", f"{nid}.meta.yaml"), "a", encoding="utf-8") as f:
        if not file:
            f.write(f"id: {nid}\ntype: image\ntitle: {nid} 제목\nfile: {nid}.png\n")
        f.write(f"relations:\n  - {{type: similar_to, target: {target}}}\n")


class ImageGenTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "image"))
        image_ref(self.root, "ill", spec="굵은 외곽선 낙서풍")
        with open(os.path.join(self.root, "image", "ill.meta.yaml"), "a", encoding="utf-8") as f:
            f.write("check: [외곽선이 굵은가]\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def lib(self):
        return Library(self.root)

    def pkg(self, target="chatgpt_web", **kw):
        return self.lib().package("a", target, write=False, **kw)

    def test_no_style_text_for_image_keys(self):
        rule(self.root, "a.md", "a", None, "  image_style: ref:ill\n  character_style: 점 눈, 눈썹으로 표정\n  tone: 친근\n")
        p = self.pkg()["prompt"]
        rules = p[p.index("## 규칙"):p.index("## 첨부 레퍼런스")]
        self.assertNotIn("화풍", rules)
        self.assertNotIn("캐릭터", rules)
        self.assertIn("톤과 말투", rules)
        self.assertNotIn("굵은 외곽선 낙서풍", p)        # spec
        self.assertNotIn("선 굵기", p)                    # 옛 ref_instruction
        self.assertNotIn("외곽선이 굵은가", p)            # 레퍼런스 check
        self.assertNotIn("## 우선순위", p)                # 캐릭터 > 화풍 문장
        self.assertIn("## 이미지 생성 (ImageGen)", p)
        self.assertIn("  - [1] ill.png — 화풍", p)
        self.assertIn("  - 캐릭터: 점 눈, 눈썹으로 표정", p)
        self.assertIn(f"- [ ] {IMAGE_CHECK}", p)
        self.assertLess(p.index("## 첨부 레퍼런스"), p.index("## 이미지 생성"))
        self.assertLess(p.index("## 이미지 생성"), p.index("## 출력 전 자가 점검"))

    def test_request_form_for_non_imagegen_targets(self):
        rule(self.root, "a.md", "a", None, "  image_style: ref:ill\n  composition: 왼쪽에서 오른쪽\n")
        for t in ("claude_desktop", "codex"):
            p = self.pkg(t)
            self.assertFalse(p["image_gen"])
            pr = p["prompt"]
            self.assertIn("## 이미지 생성 (ImageGen 요청서)", pr)
            self.assertIn("- 참조 이미지로 첨부: [1] ill.png (화풍)", pr)
            self.assertIn("- 원본과 다르게 할 점: 배치와 구도: 왼쪽에서 오른쪽", pr)
            self.assertNotIn("## 이미지 생성 (ImageGen)\n", pr)
        self.assertTrue(self.pkg("chatgpt_web")["image_gen"])

    def test_similar_images_both_directions(self):
        similar(self.root, "alt-b", "ill")                 # alt-b → ill
        image_ref(self.root, "alt-a", color="green")
        with open(os.path.join(self.root, "image", "ill.meta.yaml"), "a", encoding="utf-8") as f:
            f.write("relations:\n  - {type: similar_to, target: alt-a}\n")  # ill → alt-a
        similar(self.root, "ghost", "ill", file=False)     # 원본 없는 비슷한 노드는 빠짐
        rule(self.root, "a.md", "a", None, "  image_style: ref:ill\n  character_style: ref:alt-a\n")
        p = self.pkg()
        pr = p["prompt"]
        self.assertNotIn("ghost", pr)
        # alt-a는 캐릭터 원본으로 이미 붙었으므로 비슷한 원본으로 다시 붙지 않는다
        self.assertEqual(sorted(os.path.basename(f) for f in p["files"][1:]), ["alt-a.png", "alt-b.png", "ill.png"])
        self.assertIn("— 화풍 · 비슷한 원본", pr)

    def test_missing_original_is_dropped_with_warning(self):
        write(self.root, "image/nofile.meta.yaml", "id: nofile\ntype: image\ntitle: 글만\nfile: nofile.png\nspec: 점 눈 캐릭터\n")
        rule(self.root, "a.md", "a", None, "  character_style: ref:nofile\n")
        p = self.pkg()
        self.assertNotIn("점 눈 캐릭터", p["prompt"])
        self.assertTrue(any("원본 없음" in w and "이미지 생성에서 빠졌" in w for w in p["warnings"]))
        self.assertEqual(len(p["files"]), 1)

    def test_trim_drops_similar_and_never_converts_images(self):
        similar(self.root, "alt", "ill")
        image_ref(self.root, "chr", color="yellow", spec="캐릭터 스펙")
        rule(self.root, "a.md", "a", None, "  image_style: ref:ill\n  character_style: ref:chr\n")
        self.lib()
        path = os.path.join(self.root, "_defaults", "targets.yaml")
        with open(path, encoding="utf-8") as f:
            d = yaml.safe_load(f)
        d["targets"]["chatgpt_web"]["max_files"] = 3
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(d, f, allow_unicode=True)
        p = self.pkg(trim=True)
        self.assertEqual(p["violations"], [])
        names = [os.path.basename(f) for f in p["files"]]
        self.assertNotIn("contact-sheet.png", names)
        self.assertNotIn("alt.png", names)
        self.assertIn("chr.png", names)
        self.assertIn("ill.png", names)
        self.assertNotIn("캐릭터 스펙", p["prompt"])
        self.assertTrue(any("비슷한 원본" in l for l in p["log"]))

    def test_no_image_keys_no_section(self):
        rule(self.root, "a.md", "a", None, "  chart_style: ref:ill\n  tone: 친근\n")
        p = self.pkg()["prompt"]
        self.assertNotIn("## 이미지 생성", p)
        self.assertNotIn(IMAGE_CHECK, p)

    def test_old_defaults_fallback_and_custom_flag(self):
        rule(self.root, "a.md", "a", None, "  image_style: ref:ill\n  mascot_sheet: ref:ill\n")
        L = self.lib()
        for t in L.targets.values():
            t.pop("image_gen", None)
        L.keys = {k: dict(v, image=False) if isinstance(v, dict) else v for k, v in L.keys.items()}
        self.assertIn("image_style", L.image_keys)   # 표시가 하나도 없으면 기본 목록
        self.assertTrue(L.image_gen("chatgpt_web"))
        self.assertFalse(L.image_gen("codex"))
        L.keys["mascot_sheet"] = {"label": "마스코트", "image": True}
        self.assertEqual(L.image_keys, {"mascot_sheet"})

    def test_deterministic(self):
        similar(self.root, "alt", "ill")
        rule(self.root, "a.md", "a", None, "  image_style: ref:ill\n  character_style: 점 눈\n")
        self.assertEqual(self.pkg()["prompt"], self.pkg()["prompt"])
        self.assertEqual(self.pkg("codex")["prompt"], self.pkg("codex")["prompt"])


if __name__ == "__main__":
    unittest.main()
