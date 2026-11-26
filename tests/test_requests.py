"""폴더 → 노드 AI 요청 테스트 (v3.1): 준비, 패키지(시트·묶음), 결과 수신, 미리보기, 선택 적용."""
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
from engine import Library  # noqa: E402
import folder_requests as freq  # noqa: E402
from test_engine import image_ref, rule, write  # noqa: E402
from PIL import Image  # noqa: E402


class RequestTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.drop = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "image"))
        image_ref(self.root, "ill")
        rule(self.root, "sets/blog.md", "blog", None, "  image_style: ref:ill\n  tone: 친근\n")
        rule(self.root, "sets/child.md", "child", ["blog"], "  length: 짧게\n")
        # 놓을 폴더: 새 이미지 2개, 이미 있는 이미지 1개(ill.png와 같은 내용), css 1개
        d = os.path.join(self.drop, "dev")
        os.makedirs(os.path.join(d, "sub"))
        Image.new("RGB", (20, 20), (10, 120, 200)).save(os.path.join(d, "a.png"))
        Image.new("RGB", (20, 20), (200, 30, 90)).save(os.path.join(d, "sub", "b.png"))
        shutil.copy(os.path.join(self.root, "image", "ill.png"), os.path.join(d, "same.png"))
        write(d, "style.css", ":root{--accent:#2E6F5E} .card{border-radius:12px}")
        write(d, "notes.docx", "x")  # 지원하지 않는 형식
        self.folder = d

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.drop, ignore_errors=True)

    def lib(self):
        return Library(self.root)

    def prepared(self, node="blog"):
        return freq.prepare(self.lib(), node, [self.folder])

    def test_prepare_marks_existing_and_skips_unsupported(self):
        req = self.prepared()
        names = {f["name"]: f for f in req["files"]}
        self.assertEqual(set(names), {"a.png", "b.png", "same.png", "style.css"})
        self.assertEqual(names["same.png"]["existing"], "ill")
        self.assertIsNone(names["a.png"]["existing"])
        self.assertIn("dev/notes.docx", req["skipped"])
        self.assertEqual(req["node_kind"], "set")
        self.assertIn("세트에 연결", req["recommended"])
        self.assertNotIn(req["id"], " ".join(self.lib().nodes))  # inbox는 노드가 아님

    def test_package_agent_and_chat_with_merge(self):
        req = self.prepared()
        a = freq.package(self.lib(), req["id"], "codex")
        self.assertEqual(len(a["files"]), 1)
        self.assertIn(os.path.join(self.root, "inbox", req["id"], "out"), a["prompt"])
        self.assertIn("이미 `ill` 노드로 등록됨", a["prompt"])
        self.assertIn("plan.yaml", a["prompt"])
        import yaml
        tp = os.path.join(self.root, "_defaults", "targets.yaml")
        with open(tp, encoding="utf-8") as f:
            t = yaml.safe_load(f)
        t["targets"]["chatgpt_web"]["max_files"] = 4  # 원본 3개 > 남은 자리 2개 → 합치기
        with open(tp, "w", encoding="utf-8") as f:
            yaml.safe_dump(t, f, allow_unicode=True)
        c = freq.package(self.lib(), req["id"], "chatgpt_web")
        self.assertEqual(set(c["merged"]), {"contact-sheet.png", "bundle.txt"})
        self.assertEqual(c["merged"]["contact-sheet.png"], ["a.png", "b.png"])
        self.assertTrue(all(os.path.exists(f) for f in c["files"]))
        self.assertIn("assets-import.zip", c["prompt"])

    def _result_zip(self, rid, plan, files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("assets-import/plan.yaml", plan.replace("RID", rid))
            for k, v in files.items():
                z.writestr("assets-import/" + k, v)
        return buf.getvalue()

    def test_full_flow_preview_and_apply(self):
        req = self.prepared()
        freq.package(self.lib(), req["id"], "chatgpt_web")
        plan = """request: RID
summary: 개발 레퍼런스 등록
create:
  - { path: image/a.meta.yaml, reason: 새 아이콘 스타일 }
  - { path: image/dupe.meta.yaml, reason: 실수로 중복 }
  - { path: web/style.meta.yaml, reason: 토큰 }
update:
  - node: blog
    set_rules: { css_style: "ref:dev-tokens", image_style: "ref:dev-icon" }
    reason: 세트에 연결
relations:
  - { from: dev-icon, type: similar_to, to: ill, reason: 비슷함 }
"""
        files = {
            "image/a.meta.yaml": "id: dev-icon\ntype: image\ntitle: 아이콘\nfile: a.png\nsummary: 파란 아이콘\n",
            "image/dupe.meta.yaml": "id: dev-dupe\ntype: image\ntitle: 중복\nfile: same.png\n",
            "web/style.meta.yaml": "id: dev-tokens\ntype: web\ntitle: 토큰\nfile: style.css\n",
        }
        rid = freq.receive_zip(self.lib(), self._result_zip(req["id"], plan, files))
        self.assertEqual(rid, req["id"])
        pv = freq.preview(self.lib(), rid)
        it = {i["id"]: i for i in pv["items"]}
        self.assertTrue(it["c0"]["checked"])
        self.assertEqual(it["c1"]["duplicate_of"], "ill")
        self.assertFalse(it["c1"]["checked"])
        add = next(i for i in pv["items"] if i.get("key") == "css_style")
        ow = next(i for i in pv["items"] if i.get("key") == "image_style")
        self.assertEqual((add["kind"], add["checked"]), ("add", True))
        self.assertEqual((ow["kind"], ow["checked"]), ("overwrite", False))
        self.assertEqual(ow["before"], "ref:ill")
        self.assertEqual(ow["impact"], ["blog", "child"])
        self.assertEqual(add["depends_on"], ["c2"])
        # 기본 선택으로 적용하되 c2(토큰 생성)를 빼면 css_style 추가도 함께 빠져야 함
        sel = [i["id"] for i in pv["items"] if i["checked"] and i["id"] != "c2"]
        res = freq.apply(self.lib(), rid, sel)
        L = self.lib()
        self.assertIn("dev-icon", L.nodes)
        self.assertTrue(os.path.exists(os.path.join(self.root, "image", "a.png")))
        self.assertEqual(L.nodes["dev-icon"]["authored_by"], "chatgpt_web")
        self.assertNotIn("dev-tokens", L.nodes)
        self.assertNotIn("css_style", L.nodes["blog"]["rules"])
        self.assertEqual(L.nodes["blog"]["rules"]["image_style"]["value"], "ref:ill")  # 덮어쓰기 꺼짐 → 그대로
        self.assertIn({"type": "similar_to", "target": "ill"}, L.nodes["dev-icon"]["relations"])
        self.assertIn(add["id"], res["skipped"])
        self.assertEqual(L.nodes["blog"]["rules"]["tone"]["value"], "친근")  # 나머지 규칙 보존

    def test_overwrite_when_checked_and_agent_out_detection(self):
        req = self.prepared()
        freq.package(self.lib(), req["id"], "codex")
        out = os.path.join(self.root, "inbox", req["id"], "out")
        write(out, "plan.yaml", f"request: {req['id']}\nupdate:\n  - node: blog\n    set_rules: {{ tone: 단호 }}\n")
        self.assertEqual(freq.list_requests(self.root)[0]["status"], "received")
        pv = freq.preview(self.lib(), req["id"])
        ow = pv["items"][0]
        self.assertEqual(ow["kind"], "overwrite")
        freq.apply(self.lib(), req["id"], [ow["id"]])
        self.assertEqual(self.lib().nodes["blog"]["rules"]["tone"]["value"], "단호")

    def test_bad_plan_reported(self):
        req = self.prepared()
        out = os.path.join(self.root, "inbox", req["id"], "out")
        write(out, "plan.yaml", "request: x\ncreate: [ {path: image/none.meta.yaml} ]\nupdate:\n  - node: nope\n    set_rules: {tone: x}\n")
        pv = freq.preview(self.lib(), req["id"])
        self.assertTrue(all(i.get("error") for i in pv["items"]))
        self.assertTrue(all(not i["checked"] for i in pv["items"]))

    def test_skill_create_preview_request_list_and_apply(self):
        req = self.prepared()
        out = os.path.join(self.root, "inbox", req["id"], "out")
        write(out, "skills/example-skill/SKILL.md", "---\nname: example-skill\ndescription: Example workflow\n---\nDo the work.\n")
        write(out, "skills/example-skill/references/guide.md", "Example guide.\n")
        write(out, "plan.yaml", f"request: {req['id']}\ncreate:\n  - type: skill\n    path: skills/example-skill\n    uses: blog\n")
        pv = freq.preview(self.lib(), req["id"])
        item = pv["items"][0]
        self.assertEqual((item["node_type"], item["approval"], item["uses"]), ("skill", "human", "blog"))
        self.assertTrue(item["checked"])
        self.assertEqual(freq.list_requests(self.root)[0]["proposed_skills"][0]["name"], "example-skill")
        freq.apply(self.lib(), req["id"], [item["id"]])
        lib = self.lib()
        self.assertEqual(lib.nodes["example-skill"]["type"], "skill")
        self.assertIn("example-skill", lib.nodes["blog"]["uses"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, "skills", "example-skill", "references", "guide.md")))

    def test_coordinator_skill_approval_stays_disabled(self):
        req = self.prepared()
        out = os.path.join(self.root, "inbox", req["id"], "out")
        write(out, "skills/example-skill/SKILL.md", "---\nname: example-skill\n---\nSteps.\n")
        write(out, "plan.yaml", f"request: {req['id']}\ncreate:\n  - type: skill\n    path: skills/example-skill\n    uses: blog\n    approval: coordinator\n")
        item = freq.preview(self.lib(), req["id"])["items"][0]
        self.assertFalse(item["checked"])
        self.assertIn("조율자 승인은 꺼져 있음", item["error"])
        self.assertTrue(freq.coordinator_skill_allowed("example-skill", item["draft_sha256"], {"example-skill@" + item["draft_sha256"]}))
        self.assertFalse(freq.coordinator_skill_allowed("example-skill", item["draft_sha256"], set()))
        result = freq.apply(self.lib(), req["id"], [item["id"]])
        self.assertEqual(result["applied"], [])
        self.assertNotIn("example-skill", self.lib().nodes)


if __name__ == "__main__":
    unittest.main()
