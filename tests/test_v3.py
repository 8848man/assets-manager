"""v3 기능 테스트: 우선순위·자가 점검, 미등록 노드, 작성 패키지, 가져오기, 히스토리, 버전·휴지통·Git, 기본값 업데이트."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
from engine import Library  # noqa: E402
import authoring  # noqa: E402
import store  # noqa: E402
from test_engine import image_ref, rule, write  # noqa: E402
from PIL import Image  # noqa: E402


class V3Test(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "image"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def lib(self):
        return Library(self.root)

    # ---------------------------------------------------------- 1.5단계
    def test_precedence_default_and_checks(self):
        image_ref(self.root, "ill")
        write(self.root, "image/ill.meta.yaml", "id: ill\ntype: image\ntitle: 삽화\nfile: ill.png\ncheck: [외곽선이 굵은가]\n")
        rule(self.root, "a.md", "a", None, "  chart_style: ref:ill\n  palette: 파스텔 5색\n")
        p = self.lib().package("a", "claude_desktop", write=False)["prompt"]
        self.assertNotIn("## 우선순위", p)  # 이미지 키의 우선순위는 문장으로 옮기지 않음
        self.assertIn("- 참조 이미지로 첨부: [1] ill.png (차트·도식 스타일)", p)
        self.assertIn("## 출력 전 자가 점검", p)
        self.assertNotIn("- [ ] 외곽선이 굵은가", p)  # 이미지 레퍼런스 점검은 옮기지 않음
        self.assertIn("- [ ] 사용한 색이 팔레트 안에 있는가", p)  # 키 점검
        self.assertLess(p.index("## 첨부 레퍼런스"), p.index("## 출력 전 자가 점검"))

    def test_precedence_override_by_set_and_ref_order(self):
        image_ref(self.root, "ill")
        image_ref(self.root, "chr", color="blue")
        rule(self.root, "a.md", "a", None, "  chart_style: ref:ill\n  palette: ref:chr\n",
             "precedence:\n  - { first: chart_style, over: palette, scope: 색 }\n")
        L = self.lib()
        asm = L.assemble("a")
        self.assertEqual(asm["precedence"][0]["first"], "chart_style")
        self.assertEqual(asm["precedence"][0]["source"], "a")
        p = L.package("a", "claude_desktop", write=False)["prompt"]
        self.assertNotIn("## 우선순위", p)  # 조립 우선순위는 유지하되 이미지 스타일 문장은 생략
        self.assertIn("- 참조 이미지로 첨부: [2] ill.png (차트·도식 스타일)", p)

    def test_character_ref_attached_first_by_default(self):
        image_ref(self.root, "ill")
        image_ref(self.root, "chr", color="blue")
        rule(self.root, "a.md", "a", None, "  image_style: ref:ill\n  character_style: ref:chr\n")
        p = self.lib().package("a", "claude_desktop", write=False)["prompt"]
        self.assertIn("- [1] chr.png", p)
        self.assertIn("- [2] ill.png", p)

    # ---------------------------------------------------------- 미등록 노드와 작성 패키지
    def test_unregistered_detection_and_authoring(self):
        Image.new("RGB", (30, 20), (200, 120, 90)).save(os.path.join(self.root, "image", "raw.png"))
        image_ref(self.root, "known")
        L = self.lib()
        self.assertIn("new:image/raw.png", L.nodes)
        self.assertNotIn("new:image/known.png", L.nodes)
        agent = authoring.build(L, ["image/raw.png"], "codex")
        self.assertTrue(agent["agent"])
        self.assertIn("extract.py", agent["prompt"])
        self.assertIn(os.path.join(self.root, "image", "raw.png"), agent["prompt"])
        self.assertIn("`known`", agent["prompt"])  # 기존 id 목록
        self.assertEqual(len(agent["files"]), 1)
        chat = authoring.build(L, ["image/raw.png"], "chatgpt_web")
        self.assertFalse(chat["agent"])
        self.assertTrue(any(f.endswith("extract.py") for f in chat["files"]))
        self.assertIn("assets-import.zip", chat["prompt"])
        self.assertIn('"palette"', chat["prompt"])  # 미리 뽑은 값

    # ---------------------------------------------------------- 가져오기
    def test_import_original_and_zip_with_author(self):
        png = io.BytesIO()
        Image.new("RGB", (10, 10), "green").save(png, "PNG")
        res = store.import_items(self.lib(), [{"name": "shot.png", "data": png.getvalue()}])
        self.assertEqual(res[0]["rel"], "image/shot.png")
        store.append_history(self.root, {"kind": "author", "target": "chatgpt_web", "files": ["image/shot.png"]})
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("assets-import/image/shot.meta.yaml", "id: shot\ntype: image\ntitle: 샷\nfile: shot.png\n")
            z.writestr("../evil.txt", "x")
        res = store.import_items(self.lib(), [{"name": "assets-import.zip", "data": buf.getvalue()}])
        self.assertEqual({r["status"] for r in res}, {"added", "skipped"})
        L = self.lib()
        self.assertEqual(L.nodes["shot"]["authored_by"], "chatgpt_web")
        self.assertNotIn("new:image/shot.png", L.nodes)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.root), "evil.txt")))
        g = L.graph()
        self.assertTrue(any(e["id"] == "auth:chatgpt_web:shot" for e in g["edges"]))

    def test_import_rule_md(self):
        data = "---\nid: dev\ntype: rule\nkind: principle\ntitle: 개발\nrules:\n  tone: 간결\n---\n".encode()
        res = store.import_items(self.lib(), [{"name": "dev.md", "data": data}])
        self.assertEqual(res[0]["rel"], "principles/dev.md")
        self.assertIn("dev", self.lib().nodes)

    # ---------------------------------------------------------- 히스토리
    def test_history_counts_in_graph(self):
        rule(self.root, "a.md", "a", None, "  tone: x\n")
        store.append_history(self.root, {"node": "a", "target": "codex", "method": "drag", "kind": "prompt"})
        store.append_history(self.root, {"node": "a", "target": "codex", "method": "copy", "kind": "prompt"})
        g = self.lib().graph(store.read_history(self.root))
        e = next(e for e in g["edges"] if e["id"] == "drop:a:codex")
        self.assertEqual(e["label"], "드롭 2회")
        self.assertEqual((e["drops"]["drag"], e["drops"]["copy"]), (1, 1))
        self.assertTrue(any(n["id"] == "ai:codex" for n in g["nodes"]))

    # ---------------------------------------------------------- 버전·휴지통
    def test_versions_and_restore(self):
        rule(self.root, "sets/a.md", "a", None, "  tone: 처음\n")
        store.backup_sync(self.lib())
        rule(self.root, "sets/a.md", "a", None, "  tone: 두번째\n")
        L = self.lib()
        store.backup_sync(L)
        vs = store.versions_for(L, "a")
        self.assertEqual(len(vs), 2)
        oldest = vs[-1]
        store.restore_version(L, oldest["rel"], oldest["id"])
        with open(os.path.join(self.root, "sets/a.md"), encoding="utf-8") as f:
            self.assertIn("처음", f.read())
        self.assertFalse(any(n.startswith("a") and n != "a" for n in self.lib().nodes))  # versions/는 노드가 아님

    def test_delete_to_trash_and_restore(self):
        image_ref(self.root, "g")
        rule(self.root, "a.md", "a", None, "  chart_style: ref:g\n")
        info = store.delete_node(self.lib(), "g")
        self.assertEqual(info["used_by"], ["a"])
        self.assertNotIn("g", self.lib().nodes)
        self.assertEqual(len(store.list_trash(self.root)), 1)
        store.restore_trash(self.root, info["trash_id"])
        self.assertIn("g", self.lib().nodes)
        self.assertEqual(store.list_trash(self.root), [])

    @unittest.skipUnless(shutil.which("git"), "git 없음")
    def test_git_auto_commit(self):
        rule(self.root, "a.md", "a", None, "  tone: x\n")
        r = store.backup_sync(self.lib(), "첫 기록")
        self.assertTrue(r["git"].get("committed"))
        log = subprocess.run(["git", "log", "--oneline"], cwd=self.root, capture_output=True, text=True, encoding="utf-8").stdout
        self.assertIn("첫 기록", log)
        with open(os.path.join(self.root, ".gitignore"), encoding="utf-8") as f:
            ignored = f.read()
        self.assertIn("history/versions/", ignored)
        self.assertFalse(store.backup_sync(self.lib())["git"].get("committed"))  # 변경 없으면 커밋 안 함

    def test_commit_message_summarizes_changes(self):
        changes = [("M", "sets/naver-blog-post.md", None), ("A", "image/ill.meta.yaml", None),
                   ("A", "image/ill.png", None), ("M", "layout.json", None), ("M", "history/drops.jsonl", None)]
        subject, body = store.commit_message(changes)
        self.assertEqual(subject, "규칙 수정 · 레퍼런스 추가 · 드롭 기록 · 노드 배치 저장: naver-blog-post, ill")
        self.assertIn("추가 image/ill.png", body)
        self.assertTrue(body.endswith("출처: 폴더 변경 감지"))
        subject, body = store.commit_message([("M", "layout.json", None)], "Assets Manager 시작")
        self.assertEqual(subject, "Assets Manager 시작 — 노드 배치 저장")
        self.assertTrue(body.endswith("출처: 앱 동작"))
        many = [("D", f"principles/p{i}.md", None) for i in range(5)] + [("R", "sets/b.md", "sets/a.md")]
        subject, body = store.commit_message(many)
        self.assertEqual(subject, "규칙 삭제 · 규칙 이름 변경: p0, p1, p2 외 3개")
        self.assertIn("이름 변경 sets/a.md → sets/b.md", body)
        subject, body = store.commit_message([("M", "sets/a.md", None)], checked_at="콘솔 명령 전 확인")
        self.assertEqual(subject, "규칙 수정: a")  # 동작 전 쓸어 담기에는 앱 동작 이름이 붙지 않는다
        self.assertTrue(body.endswith("출처: 폴더 변경 감지 (콘솔 명령 전 확인)"))
        subject, _ = store.commit_message([("M", ".gitignore", None), ("M", ".claude/settings.json", None)])
        self.assertEqual(subject, "설정 수정: .gitignore, settings")

    @unittest.skipUnless(shutil.which("git"), "git 없음")
    def test_root_docs_are_not_auto_committed(self):
        """기획서·AGENTS.md 같은 '기타' 파일은 자동 커밋에서 빼고 사람이 직접 커밋한다 (ISSUE-032·033)."""
        write(self.root, "AGENTS.md", "# 안내\n")
        rule(self.root, "sets/a.md", "a", None, "  tone: x\n")
        r = store.backup_sync(self.lib(), checked_at="앱 시작 때 확인")["git"]
        self.assertTrue(r["committed"], r)
        self.assertIn("AGENTS.md", r["skipped"])
        tracked = subprocess.run(["git", "ls-files"], cwd=self.root, capture_output=True, text=True,
                                 encoding="utf-8").stdout.splitlines()
        self.assertIn("sets/a.md", tracked)
        self.assertIn(".gitignore", tracked)  # 앱이 관리하는 설정은 커밋
        self.assertNotIn("AGENTS.md", tracked)
        log = subprocess.run(["git", "log", "-1", "--format=%s%n%b"], cwd=self.root, capture_output=True, text=True,
                             encoding="utf-8").stdout
        self.assertNotIn("AGENTS.md", log)
        self.assertIn("출처: 폴더 변경 감지 (앱 시작 때 확인)", log)
        # 문서만 바뀌면 커밋하지 않고, 파일은 그대로 남는다 (HEAD가 있는 경우의 unstage)
        write(self.root, "AGENTS.md", "# 안내 2\n")
        r = store.backup_sync(self.lib())["git"]
        self.assertFalse(r["committed"])
        self.assertEqual(r["skipped"], ["AGENTS.md"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "AGENTS.md")))
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=self.root, capture_output=True,
                                text=True, encoding="utf-8").stdout
        self.assertEqual(staged.strip(), "")

    @unittest.skipUnless(shutil.which("git"), "git 없음")
    def test_git_auto_commit_message(self):
        store.backup_sync(self.lib())  # 첫 기록 (기본값 복사 등)
        rule(self.root, "sets/a.md", "a", None, "  tone: x\n")
        store.backup_sync(self.lib())
        log =subprocess.run(["git", "log", "-1", "--format=%s%n%b"], cwd=self.root, capture_output=True, text=True,
                             encoding="utf-8").stdout
        self.assertIn("규칙 추가: a", log.splitlines()[0])
        self.assertIn("출처: 폴더 변경 감지", log)

    def test_fresh_only_after_baseline(self):
        image_ref(self.root, "old")
        store.backup_sync(self.lib())  # 첫 실행: 기존 파일은 기준선
        self.assertEqual(store.fresh_files(self.root), set())
        image_ref(self.root, "added")
        store.backup_sync(self.lib())
        fresh = store.fresh_files(self.root)
        self.assertIn("image/added.meta.yaml", fresh)
        g = self.lib().graph([], fresh)
        flags = {n["id"]: n["new"] for n in g["nodes"]}
        self.assertTrue(flags["added"])
        self.assertFalse(flags["old"])

    # ---------------------------------------------------------- 기본값 업데이트
    def test_defaults_update_notice(self):
        self.lib()
        with open(os.path.join(self.root, "_defaults", "keys.yaml"), "w", encoding="utf-8") as f:
            f.write("version: 1\nkeys: {}\n")
        ups = store.defaults_updates(self.root)
        self.assertTrue(any(u["file"] == "keys.yaml" for u in ups))
        store.upgrade_defaults(self.lib())
        self.assertFalse(any(u["file"] == "keys.yaml" for u in store.defaults_updates(self.root)))
        self.assertIn("character_style", self.lib().keys)


if __name__ == "__main__":
    unittest.main()
