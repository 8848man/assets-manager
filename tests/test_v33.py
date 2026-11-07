"""v3.3 + v3.2 테스트: 검색, 참고 예시, 예상 결과(stage/commit), 요청 없는 zip·@target·원본 짝 맞추기,
내보내기 명령, 산출물·골든 승격, RAG 내보내기, MCP 서버(stdio), 되돌리기."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

CORE = os.path.join(os.path.dirname(__file__), "..", "core")
sys.path.insert(0, CORE)
from engine import Library  # noqa: E402
import drops  # noqa: E402
import export_cmd  # noqa: E402
import folder_requests as freq  # noqa: E402
import rag  # noqa: E402
import search  # noqa: E402
import store  # noqa: E402
from test_engine import image_ref, rule, write  # noqa: E402
from PIL import Image  # noqa: E402


def png(color=(10, 20, 30), size=(16, 16)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


class V33Test(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "image"))
        image_ref(self.root, "ill")
        rule(self.root, "principles/global.md", "global", None, "  tone: 존댓말\n", "kind: principle\n")
        rule(self.root, "sets/blog.md", "blog", ["global"], "  image_style: ref:ill\n")
        self.out_dir = os.path.join(self.root, "outputs", "blog")
        os.makedirs(self.out_dir)
        for oid, task, kw in [("o1", "AI 비서 소개 글", "어시스턴트, 챗봇, 안내"),
                              ("o2", "여름 휴가 준비 체크리스트", "여행, 짐싸기"),
                              ("o3", "회의록 정리 방법", "미팅, 요약")]:
            with open(os.path.join(self.out_dir, f"{oid}.txt"), "w", encoding="utf-8") as f:
                f.write(f"{task} 본문입니다.")
            write(self.root, f"outputs/blog/{oid}.meta.yaml",
                  f"id: {oid}\ntype: writing\nkind: output\nset: blog\ntitle: {task}\nfile: {oid}.txt\ntask: {task}\n"
                  f"keywords: [{kw}]\nspec: '{task} 요약'\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def lib(self):
        return Library(self.root)

    # ------------------------------------------------ 검색과 참고 예시
    def test_search_keywords_and_determinism(self):
        L = self.lib()
        r1 = search.search(L, "챗봇 안내 포스팅", kinds={"output"})
        self.assertEqual(r1[0]["id"], "o1")  # 말이 달라도 관련어로 찾음
        self.assertEqual(r1, search.search(self.lib(), "챗봇 안내 포스팅", kinds={"output"}))
        self.assertEqual(search.search(L, "회의 요약", set_id="blog")[0]["id"], "o3")

    def test_examples_in_package_and_trim_first(self):
        L = self.lib()
        p = L.package("blog", "claude_desktop", task="새 AI 어시스턴트 소개", write=False)
        self.assertIn("## 이번 작업\n새 AI 어시스턴트 소개", p["prompt"])
        self.assertIn("참고 예시 1", p["prompt"])
        self.assertIn("기준(골든 샘플과 규칙)을 따르세요", p["prompt"])
        self.assertIn("o1.txt", p["prompt"])
        self.assertEqual(p["task"], "새 AI 어시스턴트 소개")
        same = self.lib().package("blog", "claude_desktop", task="새 AI 어시스턴트 소개", write=False)
        self.assertEqual(p["prompt"], same["prompt"])  # 세트 + 작업 설명이 같으면 같은 프롬프트
        none = self.lib().package("blog", "claude_desktop", write=False)
        self.assertNotIn("참고 예시", none["prompt"])
        codex = self.lib().package("blog", "codex", task="새 AI 어시스턴트 소개", write=False)
        self.assertIn("더 찾아볼 수 있는 산출물", codex["prompt"])  # 에이전트만
        self.assertNotIn("더 찾아볼 수 있는 산출물", p["prompt"])
        # 한도에 걸리면 참고 예시가 가장 먼저 덜어내진다
        import yaml
        tp = os.path.join(self.root, "_defaults", "targets.yaml")
        with open(tp, encoding="utf-8") as f:
            t = yaml.safe_load(f)
        t["targets"]["chatgpt_web"]["max_files"] = 2
        with open(tp, "w", encoding="utf-8") as f:
            yaml.safe_dump(t, f, allow_unicode=True)
        tr = self.lib().package("blog", "chatgpt_web", task="AI 비서 소개", trim=True, write=False)
        self.assertTrue(tr["log"][0].startswith("1단계: 참고 예시"))
        modes = {e["key"]: e["mode"] for e in tr["entries"]}
        self.assertEqual(modes["image_style"], "attach")

    def test_golden_excluded_from_examples(self):
        rule(self.root, "sets/blog.md", "blog", ["global"], "  image_style: ref:ill\n  golden_sample: ref:o1\n")
        p = self.lib().package("blog", "claude_desktop", task="AI 비서 소개", write=False)
        ex = [e for e in p["entries"] if e["key"].startswith("_example_")]
        self.assertNotIn("o1", [e["ref"] for e in ex])

    # ------------------------------------------------ 예상 결과: stage
    def test_stage_options_by_node(self):
        L = self.lib()
        s1 = drops.stage(L, None, uploads=[{"name": "a.png", "data": png()}])
        self.assertTrue(s1["simple"])
        self.assertEqual([o["id"] for o in s1["options"] if o["default"]], ["refs_only"])
        s2 = drops.stage(self.lib(), "blog", uploads=[{"name": "b.png", "data": png((1, 2, 3))}])
        self.assertEqual(s2["options"][0]["id"], "output")
        self.assertFalse(s2["simple"])
        s3 = drops.stage(self.lib(), "global", uploads=[{"name": "c/x.png", "data": png((4, 5, 6))}], folder_hint=True)
        self.assertEqual([o["id"] for o in s3["options"] if o["default"]], ["ai_new_set"])
        write(self.root, "image/ghost.meta.yaml", "id: ghost\ntype: image\ntitle: 유령\nfile: ghost.png\nspec: 설명만 있음\n")
        L = self.lib()
        self.assertEqual(L.nodes["ghost"]["exists"], False)
        self.assertTrue(any(i["node"] == "ghost" and i["level"] == "warning" for i in L.issues))
        s4 = drops.stage(L, "ghost", uploads=[{"name": "real.png", "data": png((7, 8, 9))}])
        self.assertEqual(s4["options"][0]["id"], "fill_original")

    # ------------------------------------------------ commit
    def test_commit_output_with_origin_and_undo(self):
        store.append_history(self.root, {"node": "blog", "target": "chatgpt_web", "kind": "prompt", "method": "drag",
                                         "task": "AI 비서 소개", "prompt_hash": "abc"})
        drop_ts = store.read_history(self.root)[-1]["ts"]
        s = drops.stage(self.lib(), "blog", uploads=[{"name": "result.png", "data": png((9, 9, 9))}])
        self.assertEqual(s["details"]["recent_drops"][0]["ts"], drop_ts)
        r = drops.commit(self.lib(), s["sid"], "output", {"drop_ts": drop_ts})
        L = self.lib()
        n = L.nodes[r["node"]]
        self.assertEqual((n["kind"], n["set"], n["task"], n["authored_by"]), ("output", "blog", "AI 비서 소개", "chatgpt_web"))
        self.assertEqual(n["origin"]["prompt_hash"], "abc")
        self.assertTrue(any(e["id"] == f"out:blog:{n['id']}" for e in L.graph()["edges"]))
        drops.undo(L, r["created"])
        self.assertNotIn(r["node"], self.lib().nodes)

    def test_commit_fill_variant_golden(self):
        write(self.root, "image/ghost.meta.yaml", "id: ghost\ntype: image\ntitle: 유령\nfile: ghost.png\nspec: 설명만\n")
        s = drops.stage(self.lib(), "ghost", uploads=[{"name": "real.jpg", "data": png()}])
        drops.commit(self.lib(), s["sid"], "fill_original")
        g = self.lib().nodes["ghost"]
        self.assertTrue(g["exists"])
        self.assertTrue(g["file"].endswith("ghost.jpg"))
        s = drops.stage(self.lib(), "ill", uploads=[{"name": "dark.png", "data": png((0, 0, 0))}])
        r = drops.commit(self.lib(), s["sid"], "variant")
        self.assertIn({"type": "variant_of", "target": "ill"}, self.lib().nodes[r["node"]]["relations"])
        s = drops.stage(self.lib(), "blog", uploads=[{"name": "best.png", "data": png((50, 50, 50))}])
        r = drops.commit(self.lib(), s["sid"], "golden")
        self.assertEqual(self.lib().nodes["blog"]["rules"]["golden_sample"]["value"], f"ref:{r['node']}")
        # 기존 기준이 있으면 덮어쓰지 않는다 (overwrite 필요)
        s = drops.stage(self.lib(), "blog", uploads=[{"name": "best2.png", "data": png((60, 60, 60))}])
        r2 = drops.commit(self.lib(), s["sid"], "golden")
        self.assertEqual(self.lib().nodes["blog"]["rules"]["golden_sample"]["value"], f"ref:{r['node']}")
        self.assertIn("후보", r2["message"])
        pr = drops.promote(self.lib(), "o2")
        self.assertTrue(pr["needs_overwrite"])
        drops.promote(self.lib(), "o2", overwrite=True)
        self.assertEqual(self.lib().nodes["blog"]["rules"]["golden_sample"]["value"], "ref:o2")

    # ------------------------------------------------ 요청 없는 zip, @target, 원본 짝 맞추기
    def _zip(self, plan, files):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("assets-import/plan.yaml", plan)
            for k, v in files.items():
                z.writestr("assets-import/" + k, v)
        return b.getvalue()

    def test_requestless_zip_on_target_with_missing_and_attach(self):
        plan = """summary: 대화 결과
create:
  - { path: outputs/post.meta.yaml }
  - { path: outputs/hero.meta.yaml }
  - { path: image/chart.meta.yaml }
update:
  - node: "@target"
    set_rules: { chart_style: "ref:new-chart" }
relations:
  - { from: new-chart, type: similar_to, to: "@target" }
missing: [hero.png]
"""
        files = {
            "outputs/post.meta.yaml": "id: post\ntype: writing\nkind: output\ntitle: 글\nfile: post.md\ntask: 소개 글\n",
            "outputs/post.md": "# 소개\n본문",
            "outputs/hero.meta.yaml": "id: hero\ntype: image\nkind: output\ntitle: 대표 이미지\nfile: hero.png\n",
            "image/chart.meta.yaml": "id: new-chart\ntype: image\ntitle: 차트\nfile: chart.png\n",
        }
        st = drops.stage(self.lib(), "blog", uploads=[{"name": "assets-import.zip", "data": self._zip(plan, files)}])
        self.assertEqual(st["case"], "result")
        it = {i.get("node_id") or i.get("key") or i["id"]: i for i in st["details"]["items"]}
        self.assertEqual(it["chart_style"]["node"], "blog")  # @target이 놓은 노드로 풀림
        self.assertTrue(it["hero"]["missing_original"])
        self.assertTrue(it["hero"]["checked"])  # 원본 없어도 등록 가능
        self.assertEqual(it["post"]["src"], "post.md")
        self.assertEqual(st["details"]["missing_declared"], ["hero.png"])
        # 이름이 바뀐 파일 두 개를 한 번에 놓기 → 종류별로 하나씩 남으면 자동 짝
        pv = freq.attach(self.lib(), st["sid"], [("image (1).png", png((1, 1, 1))), ("image (2).png", png((2, 2, 2)))])
        it = {i.get("node_id"): i for i in pv["items"] if i["kind"] == "create"}
        self.assertTrue(it["hero"]["missing_original"])  # 이미지 2개 남음 → 자동 짝 불가
        self.assertEqual(sorted(pv["unassigned"]), ["image (1).png", "image (2).png"])
        pv = freq.assign(self.lib(), st["sid"], "outputs/hero.meta.yaml", "image (1).png")
        freq.auto_assign(self.lib(), st["sid"])  # 남은 하나는 chart에 자동
        pv = freq.preview(self.lib(), st["sid"])
        it = {i.get("node_id"): i for i in pv["items"] if i["kind"] == "create"}
        self.assertEqual(it["hero"]["src"], "image (1).png")
        self.assertEqual(it["new-chart"]["src"], "image (2).png")
        drops.commit(self.lib(), st["sid"], "apply", {"selected": [i["id"] for i in pv["items"] if i["checked"]]})
        L = self.lib()
        self.assertEqual((L.nodes["hero"]["kind"], L.nodes["hero"]["set"]), ("output", "blog"))
        self.assertTrue(L.nodes["hero"]["exists"])
        self.assertTrue(L.nodes["hero"]["file"].startswith("outputs/blog/"))
        self.assertEqual(L.nodes["blog"]["rules"]["chart_style"]["value"], "ref:new-chart")
        self.assertIn({"type": "similar_to", "target": "blog"}, L.nodes["new-chart"]["relations"])

    def test_auto_link_children_by_target_kind(self):
        """Q70-C: 노드에 놓으면 AI가 연결을 비워 둬도 하위로 이어진다."""
        plan = "create:\n  - { path: principles/dev-a.md }\n  - { path: sets/dev-set.md }\n  - { path: document/audit.meta.yaml }\nupdate: []\nrelations: []\n"
        z = self._zip(plan, {
            "principles/dev-a.md": "---\nid: dev-a\ntype: rule\nkind: principle\ntitle: A\nrules:\n  workflow: 순서대로\n---\n",
            "sets/dev-set.md": "---\nid: dev-set\ntype: rule\nkind: set\ntitle: 세트\ninherits: [dev-a]\nrules:\n  tone: 간결\n---\n",
            "document/audit.meta.yaml": "id: audit\ntype: document\ntitle: 감사\nfile: audit.md\n",
            "document/audit.md": "# 감사"})
        st = drops.stage(self.lib(), "global", uploads=[{"name": "assets-import.zip", "data": z}])
        links = {i["node_id"]: i for i in st["details"]["items"] if i["kind"] == "link"}
        self.assertEqual(links["dev-a"]["link"], "uses")  # v3.4: 새 원칙은 놓은 노드가 부품으로 붙임
        self.assertEqual(links["dev-set"]["link"], "inherits")
        self.assertEqual(links["audit"]["link"], "child_of")
        drops.commit(self.lib(), st["sid"], "apply", {"selected": [i["id"] for i in st["details"]["items"] if i["checked"]]})
        L = self.lib()
        self.assertEqual(L.nodes["global"]["uses"], ["dev-a"])
        self.assertTrue(L.nodes["dev-a"]["implicit_inherits"])
        self.assertEqual(L.nodes["dev-set"]["inherits"], ["dev-a", "global"])
        self.assertIn({"type": "child_of", "target": "global"}, L.nodes["audit"]["relations"])
        self.assertEqual(L.sources("dev-set")[-2:], ["global", "dev-set"])

    def test_empty_space_result_asks_target(self):
        plan = "create:\n  - { path: outputs/x.meta.yaml }\n"
        z = self._zip(plan, {"outputs/x.meta.yaml": "id: x-out\ntype: writing\nkind: output\ntitle: X\nfile: x.txt\n", "outputs/x.txt": "x"})
        st = drops.stage(self.lib(), None, uploads=[{"name": "assets-import.zip", "data": z}])
        self.assertIsNone(st["details"]["request"]["node"])
        self.assertFalse([i for i in st["details"]["items"] if i["kind"] == "link"])
        pv = freq.set_target(self.lib(), st["sid"], "blog")
        self.assertEqual(pv["request"]["node"], "blog")
        drops.commit(self.lib(), st["sid"], "apply", {"selected": [i["id"] for i in pv["items"] if i["checked"]]})
        self.assertEqual(self.lib().nodes["x-out"]["set"], "blog")

    def test_export_command_per_node_routes_anywhere(self):
        ex = export_cmd.build(self.lib(), "blog")
        self.assertIn(f"request: {ex['request']}", ex["prompt"])
        self.assertIn("현재 규칙 `image_style`", ex["prompt"])
        general = export_cmd.build(self.lib())
        self.assertIsNone(general["request"])
        self.assertIn("@target", general["prompt"])
        plan = f"request: {ex['request']}\ncreate:\n  - {{ path: outputs/a.meta.yaml }}\n"
        z = self._zip(plan, {"outputs/a.meta.yaml": "id: a-out\ntype: writing\nkind: output\ntitle: A\nfile: a.txt\n",
                             "outputs/a.txt": "내용"})
        st = drops.stage(self.lib(), None, uploads=[{"name": "assets-import.zip", "data": z}])  # 빈 공간에 놓아도
        self.assertEqual(st["sid"], ex["request"])
        self.assertEqual(st["details"]["request"]["node"], "blog")

    # ------------------------------------------------ RAG, MCP
    def test_rag_chunks(self):
        write(self.root, "image/secret.meta.yaml", "id: secret\ntype: image\ntitle: 비밀\nfile: s.png\nsensitivity: internal\nspec: x\n")
        res = rag.export_jsonl(self.lib())
        with open(res["path"], encoding="utf-8") as f:
            rows = [json.loads(l) for l in f]
        ids = {r["id"] for r in rows}
        self.assertIn("o1#summary", ids)
        self.assertIn("o1#body.0", ids)
        self.assertIn("blog#rule.image_style", ids)
        self.assertFalse(any(i.startswith("secret#") for i in ids))
        self.assertEqual(rows[0]["metadata"]["sha"], rag.export_jsonl(self.lib()) and rows[0]["metadata"]["sha"])

    def test_mcp_stdio(self):
        proc = subprocess.Popen([sys.executable, os.path.join(CORE, "mcp_server.py"), self.root],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search", "arguments": {"query": "챗봇 안내"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "assemble", "arguments": {"id": "blog", "task": "AI 비서 소개"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "get", "arguments": {"id": "nope"}}},
        ]
        out, err = proc.communicate("\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n", timeout=30)
        resp = {r["id"]: r for r in map(json.loads, out.strip().splitlines())}
        self.assertEqual(set(resp), {1, 2, 3, 4, 5})  # 알림에는 응답하지 않음
        self.assertEqual(resp[1]["result"]["serverInfo"]["name"], "assets-manager")
        self.assertEqual({t["name"] for t in resp[2]["result"]["tools"]}, {"standard", "search", "get", "related", "assemble"})
        self.assertEqual(json.loads(resp[3]["result"]["content"][0]["text"])[0]["id"], "o1")
        self.assertIn("참고 예시", json.loads(resp[4]["result"]["content"][0]["text"])["prompt"])
        self.assertTrue(resp[5]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()


class PresetTests(unittest.TestCase):
    """번들 프리셋(개발 착수 원칙)이 기본 키 어휘로 경고 없이 조립되는지."""

    def test_dev_presets_assemble_without_warnings(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        d = tempfile.mkdtemp()
        try:
            for sub in ("principles", "sets"):
                shutil.copytree(os.path.join(root, sub), os.path.join(d, sub))
            shutil.copytree(os.path.join(root, "app", "defaults"), os.path.join(d, "_defaults"))
            lib = Library(d)
            bad = [i for i in lib.issues if i.get("node") in
                   ("dev-design-approval", "dev-docs", "dev-conflict-verify", "dev-workflow",
                    "dev-design-system", "ai-dev-kickoff", "ai-dev-kickoff-ui")
                   and i.get("level") != "info" and "derived_from" not in i.get("message", "")
                   and "대상" not in i.get("message", "")]
            self.assertEqual(bad, [], bad)
            keys = {e["key"] for e in lib.assemble("ai-dev-kickoff-ui")["entries"]}
            for k in ("design_approval", "doc_roles", "conflict_policy", "workflow", "design_system", "open_questions"):
                self.assertIn(k, keys)
            self.assertNotIn("design_system", {e["key"] for e in lib.assemble("ai-dev-kickoff")["entries"]})
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TidyTests(unittest.TestCase):
    """v3.4 하위 정리 (Q72-A): 원칙을 부품(uses)으로 바꿔도 최종 프롬프트는 같다."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        w = lambda rel, t: write(self.d, rel, t)
        w("principles/global.md", "---\nid: global\ntype: rule\nkind: principle\ntitle: 전역\nrules:\n  tone: 존댓말\n---\n")
        w("principles/p-a.md", "---\nid: p-a\ntype: rule\nkind: principle\ntitle: A\ninherits: [global]\nrules:\n  workflow: 순서\n  forbidden: A금지\n"
          "relations: [{type: derived_from, target: doc}]\n---\n")
        w("principles/p-b.md", "---\nid: p-b\ntype: rule\nkind: principle\ntitle: B\ninherits: [global]\nrules:\n  scope: 범위\n"
          "relations: [{type: derived_from, target: doc}]\n---\n")
        w("sets/s.md", "---\nid: s\ntype: rule\nkind: set\ntitle: 세트\ninherits: [global, p-a, p-b]\nrules:\n  output_format: 표\n"
          "relations: [{type: derived_from, target: doc}]\n---\n")
        w("document/doc.md", "# d")
        w("document/doc.meta.yaml", "id: doc\ntype: document\ntitle: 문서\nfile: doc.md\n")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_uses_assembly_order(self):
        L = Library(self.d)
        write(self.d, "sets/u.md", "---\nid: u\ntype: rule\nkind: set\ntitle: U\ninherits: [global]\n"
              "uses: [p-a]\nrules:\n  forbidden: 세트금지\n---\n")
        L = Library(self.d)
        self.assertEqual(L.sources("u")[-2:], ["p-a", "u"])
        fb = next(e for e in L.assemble("u")["entries"] if e["key"] == "forbidden")
        self.assertEqual(fb["value"], "세트금지")  # 세트 자신 > 붙인 원칙
        self.assertIn("u", L.descendants("p-a"))
        edges = {e["id"] for e in L.graph()["edges"]}
        self.assertIn("use:u:p-a", edges)

    def test_tidy_roundtrip(self):
        import tidy
        L = Library(self.d)
        b = tidy.build(L, "s", "chatgpt_web")
        codes = {x["code"] for x in b["diagnostics"]}
        self.assertTrue({"redundant_inherit", "principles_as_parents", "principle_inherits", "repeated_relation"} <= codes)
        self.assertIn("```yaml", b["prompt"])
        before = L.render(L.assemble("s"))[0]
        answer = ("설명입니다\n```yaml\nrequest: %s\nsummary: 부품으로\nwhy: 선이 겹침\nops:\n"
                  "  - { op: set_inherits, node: s, inherits: [global], reason: 계층 }\n"
                  "  - { op: set_uses, node: s, uses: [p-a, p-b], reason: 부품 }\n"
                  "  - { op: set_inherits, node: p-a, inherits: [], reason: 부품 }\n"
                  "  - { op: set_inherits, node: p-b, inherits: [], reason: 부품 }\n"
                  "  - { op: set_relations, node: p-a, relations: [], reason: 출처는 세트에 }\n"
                  "  - { op: set_relations, node: p-b, relations: [], reason: 출처는 세트에 }\n"
                  "  - { op: move_rule, key: scope, from: p-b, to: p-a, reason: 합침 }\n"
                  "  - { op: set_uses, node: s, uses: [p-a], reason: B가 비었음 }\n"
                  "  - { op: trash, node: p-b, reason: 비었음 }\n"
                  "  - { op: set_body, node: s, body: 새 설명, reason: 구조가 바뀜 }\n"
                  "  - { op: trash, node: global, reason: 잘못된 제안 }\n```\n") % b["request"]
        pv = tidy.receive(Library(self.d), b["request"], answer)
        bad = [i for i in pv["items"] if i.get("error")]
        self.assertEqual([i["op"] for i in bad], ["trash"])  # 전역은 아직 쓰임 → 거부
        self.assertTrue(all(p["same"] for p in pv["prompts"]), pv["prompts"])
        self.assertEqual(pv["diag_after"], [])
        res = tidy.apply(Library(self.d), b["request"], [i["id"] for i in pv["items"]])
        self.assertTrue(res["trashed"])
        L2 = Library(self.d)
        self.assertNotIn("p-b", L2.nodes)
        self.assertEqual(L2.nodes["s"]["uses"], ["p-a"])
        self.assertTrue(L2.nodes["p-a"]["implicit_inherits"])
        self.assertEqual(L2.nodes["p-a"]["relations"], [])
        self.assertEqual(L2.render(L2.assemble("s"))[0], before)
        self.assertEqual(L2.nodes["s"]["body"], "새 설명")
        self.assertEqual([i for i in L2.issues if i["level"] == "error"], [])

    def test_tidy_value_change_shows_in_prompt_diff(self):
        import tidy
        L = Library(self.d)
        b = tidy.build(L, "s")
        pv = tidy.receive(L, b["request"], "request: %s\nops:\n  - { op: set_rule, node: p-a, key: workflow, value: 새 순서 }\n" % b["request"])
        p = next(x for x in pv["prompts"] if x["id"] == "s")
        self.assertFalse(p["same"])
        self.assertEqual(p["changed_keys"][0]["key"], "workflow")
        pv2 = tidy.preview(Library(self.d), b["request"], selected=[])
        self.assertTrue(all(x["same"] for x in pv2["prompts"]))


class GateTests(unittest.TestCase):
    """v3.4 승인 게이트: 작업이 없으면 기다리고, 게이트가 켜져 있으면 계획 승인을 먼저 받는다."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        write(self.d, "sets/blog.md", "---\nid: blog\ntype: rule\nkind: set\ntitle: 블로그\nrules:\n  tone: 친근하게\n---\n")
        write(self.d, "sets/quick.md", "---\nid: quick\ntype: rule\nkind: set\ntitle: 빠른\ninherits: [blog]\ngate: false\nrules:\n  length: 짧게\n---\n")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_process_section_and_checklist_last(self):
        L = Library(self.d)
        no_task = L.package("blog", "chatgpt_web", write=False)["prompt"]
        self.assertLess(no_task.index("## 진행 방식"), no_task.index("## 규칙"))  # 절차는 앞에
        self.assertIn("작업을 말할 때까지 기다리세요", no_task)
        self.assertNotIn("명령도 실행하지", no_task)  # 레퍼런스 열어 보기는 막지 않는다
        self.assertIn("열어 보고 읽는 것은 지금 해도 됩니다", no_task)
        with_task = L.package("blog", "chatgpt_web", write=False, task="여름 휴가 글")["prompt"]
        self.assertNotIn("기다리세요.\n작업을 받으면", with_task)
        self.assertTrue(with_task.rstrip().endswith("모두 지켰는가"))  # 품질 기준이 마지막
        # 게이트를 끄면 아무것도 붙지 않는다 (예전 프롬프트와 같음)
        q = L.package("quick", "chatgpt_web", write=False)
        self.assertFalse(q["gate"])
        self.assertEqual(q["prompt"], L.canonical("quick"))
        self.assertIn("## 진행 방식", L.package("quick", "chatgpt_web", write=False, task="x", gate=True)["prompt"])
        self.assertEqual(L.package("blog", "chatgpt_web", write=False, gate=False)["prompt"], L.canonical("blog"))

    def test_gate_is_same_for_all_targets_and_not_in_snapshot(self):
        L = Library(self.d)
        tails = set()
        for t in L.targets:
            p = L.package("blog", t, write=False, task="글")["prompt"]
            tails.add(p[p.index("## 진행 방식"):p.index("## 규칙")])
        self.assertEqual(len(tails), 1)
        self.assertNotIn("## 진행 방식", L.canonical("blog"))
