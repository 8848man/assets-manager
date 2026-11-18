"""v3.9 구성 요소 분리 (Q81-A): 메타 읽기, 조립·렌더(원본 + 요소), #part 참조, 덜어내기, 요청 왕복, 사람 수정."""
import os
import shutil
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
from engine import Library  # noqa: E402
import components  # noqa: E402
import drops  # noqa: E402
import folder_requests as freq  # noqa: E402
from test_engine import rule, write  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

COMPS = """
components:
  - name: character
    part: character
    title: 점눈 캐릭터
    box: [10, 10, 60, 80]
    spec: 2등신, 점 눈 2개
    check: [눈이 점 2개인가]
  - part: shape
    title: 블록
    box: [100, 60, 80, 40]
    spec: 둥근 모서리 블록
  - part: composition
    title: 왼쪽에서 오른쪽
    regions:
      - { label: 캐릭터, box: [10, 10, 60, 80] }
      - { label: 블록, box: [100, 60, 80, 40] }
    flow: [[40, 50], [140, 80]]
    spec: 왼쪽에서 오른쪽으로 흐름
"""


def picture(root, nid, comps=COMPS):
    os.makedirs(os.path.join(root, "image"), exist_ok=True)
    im = Image.new("RGB", (200, 120), "white")
    d = ImageDraw.Draw(im)
    d.ellipse((20, 15, 60, 55), outline="black", width=3)
    d.rectangle((100, 60, 180, 100), fill=(250, 220, 150), outline="black", width=3)
    im.save(os.path.join(root, "image", f"{nid}.png"))
    write(root, f"image/{nid}.meta.yaml",
          f"id: {nid}\ntype: image\ntitle: 삽화\nfile: {nid}.png\nspec: 낙서풍 삽화\n" + comps)


class ComponentsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def lib(self):
        return Library(self.root)

    def test_load_normalizes(self):
        picture(self.root, "ill")
        n = self.lib().nodes["ill"]
        self.assertEqual([c["name"] for c in n["components"]], ["character", "shape", "composition"])
        self.assertEqual(n["components"][0]["file"], ".assets/components/ill/ill.character.png")
        self.assertEqual(n["components"][2]["flow"], [[40, 50], [140, 80]])

    def test_bad_components_warn(self):
        picture(self.root, "ill", "components:\n  - { part: hat, box: [0,0,5,5] }\n  - { part: shape }\n")
        L = self.lib()
        self.assertEqual(L.nodes["ill"]["components"], [])
        msgs = [i["message"] for i in L.issues if i["node"] == "ill"]
        self.assertTrue(any("알 수 없는 종류" in m for m in msgs))
        self.assertTrue(any("box" in m for m in msgs))

    def test_render_attaches_original_and_components(self):
        picture(self.root, "ill")
        rule(self.root, "sets/a.md", "a", None, "  palette: ref:ill\n")
        p = self.lib().package("a", "claude_desktop", write=True)
        pr = p["prompt"]
        self.assertIn("첨부 [1] ill.png", pr)
        self.assertIn("  - **캐릭터**: 캐릭터는 첨부 [1-a] ill.character.png", pr)
        self.assertIn("첨부 [1-b] ill.shape.png", pr)
        self.assertIn("첨부 [1-c] ill.composition.png", pr)
        self.assertIn("(도식 번호: 1 캐릭터 · 2 블록)", pr)
        self.assertIn("- [1-a] ill.character.png — 삽화 · 점눈 캐릭터", pr)
        self.assertIn("- [ ] 눈이 점 2개인가", pr)
        self.assertIn("- [ ] 캐릭터의 얼굴 표현·비례·외곽선이 첨부 [1-a]의 캐릭터와 같은가", pr)
        self.assertEqual(len(p["files"]), 5)  # prompt + 원본 + 요소 3
        for f in p["files"][2:]:
            self.assertTrue(os.path.isfile(f), f)
        with Image.open(p["files"][2]) as im:  # 잘라 낸 캐릭터 (여백 포함)
            self.assertGreater(im.width, 60)
            self.assertLess(im.width, 100)

    def test_deterministic(self):
        picture(self.root, "ill")
        rule(self.root, "sets/a.md", "a", None, "  palette: ref:ill\n")
        p1 = self.lib().package("a", "claude_desktop", write=True)
        with open(p1["files"][4], "rb") as f:
            b1 = f.read()
        shutil.rmtree(os.path.join(self.root, ".assets", "components"))
        p2 = self.lib().package("a", "claude_desktop", write=True)
        with open(p2["files"][4], "rb") as f:
            b2 = f.read()
        self.assertEqual(p1["prompt"], p2["prompt"])
        self.assertEqual(b1, b2)

    def test_image_key_ignores_components(self):
        """v3.10 (Q82-A): 이미지 키는 분리한 요소·요소 설명·점검 없이 원본만 넘긴다."""
        picture(self.root, "ill")
        rule(self.root, "sets/a.md", "a", None, "  image_style: ref:ill\n  character_style: 점 눈, 눈썹으로 표정\n")
        p = self.lib().package("a", "claude_desktop", write=False)
        pr = p["prompt"]
        self.assertIn("- [1] ill.png", pr)
        self.assertNotIn("1-a", pr)
        self.assertNotIn("분리한 요소", pr)
        self.assertNotIn("눈이 점 2개인가", pr)
        self.assertEqual(len(p["files"]), 2)  # prompt.md + 원본

    def test_part_reference(self):
        picture(self.root, "ill")
        rule(self.root, "sets/a.md", "a", None, "  palette: ref:ill#character\n")
        L = self.lib()
        self.assertEqual([i for i in L.issues if i["level"] == "error"], [])
        p = L.package("a", "claude_desktop", write=True)
        self.assertIn("첨부 [1] ill.character.png", p["prompt"])
        self.assertIn("- [ ] 눈이 점 2개인가", p["prompt"])
        self.assertNotIn("1-a", p["prompt"])
        self.assertEqual(len(p["files"]), 2)
        self.assertTrue(any(e["kind"] == "key" and e["target"] == "ill" for e in L.graph()["edges"]))

    def test_missing_part_is_error(self):
        picture(self.root, "ill")
        rule(self.root, "sets/a.md", "a", None, "  palette: ref:ill#nope\n")
        self.assertTrue(any("구성 요소 없음" in i["message"] for i in self.lib().issues))

    def test_trim_components_to_text_first(self):
        picture(self.root, "ill")
        rule(self.root, "sets/a.md", "a", None, "  palette: ref:ill\n")
        L = self.lib()
        L.targets["tiny"] = {"label": "작은", "kind": "chat", "max_files": 3}
        p = L.package("a", "tiny", trim=True, write=False)
        self.assertEqual(p["violations"], [])
        self.assertIn("첨부 [1] ill.png", p["prompt"])  # 원본은 그대로
        self.assertIn("(분리한 요소 '점눈 캐릭터'를 글로 옮긴 것): 2등신, 점 눈 2개", p["prompt"])
        self.assertTrue(any(l.startswith("요소:") for l in p["log"]))

    def test_no_components_prompt_unchanged(self):
        os.makedirs(os.path.join(self.root, "image"))
        picture(self.root, "ill", "")
        rule(self.root, "sets/a.md", "a", None, "  palette: ref:ill\n")
        pr = self.lib().package("a", "claude_desktop", write=False)["prompt"]
        self.assertNotIn("1-a", pr)
        self.assertNotIn("요소", pr)

    def test_request_roundtrip_with_edits(self):
        picture(self.root, "ill", "")
        L = self.lib()
        r = components.build(L, "ill", "chatgpt_web")
        self.assertTrue(os.path.isfile(r["files"][2]))  # 격자 사본
        self.assertIn("이미지 200×120 px", r["prompt"])
        plan = (f"```yaml\nrequest: {r['request']}\nnode: ill\nsummary: s\n" + COMPS.replace("\n", "\n", 1) + "```")
        pv = components.receive(L, r["request"], "답입니다\n" + plan)
        self.assertEqual([i["status"] for i in pv["items"]], ["new", "new", "new"])
        self.assertEqual(pv["problems"], [])
        pv = components.preview(L, r["request"], {"character": {"box": [12, 12, 50, 70]}})
        self.assertEqual(pv["items"][0]["box"], [12, 12, 50, 70])
        res = components.apply(L, r["request"], ["character", "composition"])
        self.assertEqual(res["applied"], ["character", "composition"])
        with open(os.path.join(self.root, "image", "ill.meta.yaml"), encoding="utf-8") as f:
            meta = yaml.safe_load(f)
        self.assertEqual([c["name"] for c in meta["components"]], ["character", "composition"])
        self.assertEqual(meta["components"][0]["box"], [12, 12, 50, 70])  # 미리보기 수정이 반영됨
        self.assertEqual(meta["spec"], "낙서풍 삽화")  # 다른 필드 보존
        self.assertEqual(freq._load(self.root, r["request"])["status"], "applied")

    def test_replace_default_off(self):
        picture(self.root, "ill")
        L = self.lib()
        import local_ai
        r = components.build(L, "ill", local_ai.agent_target(L))
        self.assertIn(f"inbox/{r['request']}/out/plan.yaml", r["prompt"])  # 에이전트는 직접 씀
        changed = COMPS.replace("2등신, 점 눈 2개", "3등신")
        components.receive(L, r["request"], f"request: {r['request']}\nnode: ill\n" + changed)
        pv = components.preview(L, r["request"])
        st = {i["name"]: (i["status"], i["checked"]) for i in pv["items"]}
        self.assertEqual(st["character"], ("replace", False))
        self.assertEqual(st["shape"], ("same", False))

    def test_zip_result_routes_to_components(self):
        import io
        import zipfile
        picture(self.root, "ill", "")
        L = self.lib()
        r = components.build(L, "ill", "chatgpt_web")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("plan.yaml", f"request: {r['request']}\nnode: ill\n" + COMPS)
        path = os.path.join(self.root, "assets-import.zip")
        with open(path, "wb") as f:
            f.write(buf.getvalue())
        o = drops.stage(L, "ill", [path])
        self.assertEqual(o["case"], "components")
        self.assertEqual(len(o["details"]["items"]), 3)

    def test_save_by_user(self):
        picture(self.root, "ill")
        L = self.lib()
        cur = [{k: v for k, v in c.items() if k != "file"} for c in L.nodes["ill"]["components"]]
        cur[0]["box"] = [5, 5, 70, 90]
        components.save(L, "ill", cur[:2])
        L2 = self.lib()
        self.assertEqual([c["name"] for c in L2.nodes["ill"]["components"]], ["character", "shape"])
        self.assertEqual(L2.nodes["ill"]["components"][0]["box"], [5, 5, 70, 90])
        with self.assertRaises(ValueError):
            components.save(L2, "ill", [{"part": "shape"}])

    def test_skill_compile_copies_components(self):
        import skills
        picture(self.root, "ill")
        rule(self.root, "sets/a.md", "a", None, "  palette: ref:ill\n")
        out = skills.compile_set(self.lib(), "a")
        self.assertIn("references/ill.character.png", out["files"])
        self.assertIn("references/ill.composition.png", out["files"])


if __name__ == "__main__":
    unittest.main()
