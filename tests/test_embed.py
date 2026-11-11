"""뜻 검색(로컬 임베딩, Q83-A) 테스트. 실제 모델 대신 결정적인 가짜 인코더를 꽂는다."""
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

CORE = os.path.join(os.path.dirname(__file__), "..", "core")
sys.path.insert(0, CORE)
from engine import Library  # noqa: E402
import embed  # noqa: E402
import search  # noqa: E402
from test_engine import rule, write  # noqa: E402

# 뜻 묶음: 같은 묶음이면 코사인 1.0, 다른 묶음이면 0.8, 묶음 없는 글과도 0.8
CONCEPTS = [("강아지", "반려견", "산책"), ("여름", "휴가", "바다"), ("회의", "미팅", "요약")]


def fake(texts):
    out = []
    for t in texts:
        v = [2.0] + [0.0] * (len(CONCEPTS) + 1)
        hit = [i for i, words in enumerate(CONCEPTS) if any(w in t for w in words)]
        if hit:
            v[1 + hit[0]] = 1.0
        else:
            v[-1] = 1.0
        n = math.sqrt(sum(x * x for x in v))
        out.append([x / n for x in v])
    return out


class EmbedTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.models = tempfile.mkdtemp()
        os.environ["AM_MODEL_DIR"] = self.models
        rule(self.root, "sets/blog.md", "blog", None, "  tone: 존댓말\n")
        os.makedirs(os.path.join(self.root, "outputs", "blog"))
        for oid, task in [("o1", "강아지 돌보기 안내"), ("o2", "여름 준비 체크리스트"), ("o3", "회의록 정리 방법")]:
            with open(os.path.join(self.root, "outputs", "blog", f"{oid}.txt"), "w", encoding="utf-8") as f:
                f.write(f"{task} 본문입니다.")
            write(self.root, f"outputs/blog/{oid}.meta.yaml",
                  f"id: {oid}\ntype: writing\nkind: output\nset: blog\ntitle: {task}\nfile: {oid}.txt\ntask: {task}\n")
        self.calls = []

        def counting(texts):
            self.calls.append(list(texts))
            return fake(texts)
        embed.set_encoder(counting)

    def tearDown(self):
        embed.set_encoder(None)
        os.environ.pop("AM_MODEL_DIR", None)
        embed._CACHE.clear()
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.models, ignore_errors=True)

    def lib(self):
        return Library(self.root)

    def test_off_is_keyword_search_unchanged(self):
        r = search.search(self.lib(), "회의록 정리", kinds={"output"})
        self.assertEqual([x["id"] for x in r], ["o3"])
        self.assertNotIn("sem", r[0])
        self.assertEqual(search.search(self.lib(), "반려견 산책", kinds={"output"}), [])
        self.assertEqual(self.calls, [])  # 꺼져 있으면 인코더를 부르지 않는다
        self.assertFalse(os.path.exists(os.path.join(self.root, embed.VECTORS)))

    def test_on_finds_by_meaning_and_is_deterministic(self):
        embed.set_enabled(self.root, True)
        r = search.search(self.lib(), "반려견 산책", kinds={"output"})
        self.assertEqual([x["id"] for x in r], ["o1"])  # 낱말이 하나도 겹치지 않아도 뜻으로 찾음, 관계없는 것은 뺌
        self.assertEqual(r[0]["kw"], 0)
        self.assertEqual(r[0]["sem"], 1.0)
        self.assertEqual(r, search.search(self.lib(), "반려견 산책", kinds={"output"}))
        # 키워드가 맞는 결과는 그대로 위에 남는다
        r2 = search.search(self.lib(), "회의록 정리", kinds={"output"})
        self.assertEqual(r2[0]["id"], "o3")

    def test_vectors_cached_and_only_changed_chunks_recomputed(self):
        embed.set_enabled(self.root, True)
        search.search(self.lib(), "반려견", kinds={"output"})
        passages = [t for c in self.calls for t in c if t.startswith("passage: ")]
        self.assertTrue(passages)
        self.calls.clear()
        search.search(self.lib(), "휴가", kinds={"output"})
        self.assertEqual([t for c in self.calls for t in c if t.startswith("passage: ")], [])  # 캐시
        with open(os.path.join(self.root, "outputs", "blog", "o2.txt"), "w", encoding="utf-8") as f:
            f.write("바다 여행 짐 목록")
        self.calls.clear()
        search.search(self.lib(), "휴가", kinds={"output"})
        self.assertEqual([t for c in self.calls for t in c if t.startswith("passage: ")], ["passage: 바다 여행 짐 목록"])
        with open(os.path.join(self.root, embed.VECTORS), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["model"], embed.MODEL_KEY)
        old = embed._key("passage: 여름 준비 체크리스트 본문입니다.")
        self.assertIn(old, data["vecs"])
        n = embed.index(self.lib())  # 안 쓰는 벡터(바뀌기 전 본문) 정리, 규칙 노드까지 전부 색인
        vecs = embed._load(self.root)["vecs"]
        self.assertNotIn(old, vecs)
        self.assertEqual(n, len(vecs))

    def test_examples_use_meaning(self):
        embed.set_enabled(self.root, True)
        p = self.lib().package("blog", "claude_desktop", task="반려견 산책 글", write=False)
        self.assertIn("o1.txt", p["prompt"])
        self.assertNotIn("o2.txt", p["prompt"])

    def test_encoder_failure_falls_back_to_keywords(self):
        embed.set_enabled(self.root, True)

        def broken(texts):
            raise RuntimeError("model broken")
        embed.set_encoder(broken)
        r = search.search(self.lib(), "회의록 정리", kinds={"output"})
        self.assertEqual([x["id"] for x in r], ["o3"])
        self.assertNotIn("sem", r[0])

    def test_status_and_model_required(self):
        embed.set_encoder(None)
        st = embed.status(self.root)
        self.assertFalse(st["enabled"])
        self.assertFalse(st["model"])  # 빈 모델 폴더
        embed.set_enabled(self.root, True)
        self.assertTrue(embed.enabled(self.root))
        self.assertFalse(embed.active(self.root))  # 켜도 모델이 없으면 키워드 검색
        self.assertEqual([x["id"] for x in search.search(self.lib(), "회의록", kinds={"output"})], ["o3"])
        self.assertFalse(embed.set_enabled(self.root, False)["enabled"])


if __name__ == "__main__":
    unittest.main()
