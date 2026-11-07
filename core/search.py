"""검색 (기획서 v3.2 13.4). 임베딩 없이 결정적으로 동작하는 키워드 검색.

- 한국어는 띄어쓰기 단위 단어와 3글자 조각(짧은 단어는 2글자)으로 색인한다.
- 뜻의 이해는 등록할 때 AI가 써 둔 `keywords:`(관련어)로 보충한다.
- 같은 질의 + 같은 폴더 상태 → 항상 같은 결과 (점수가 같으면 id 순).
- 검색 입구는 search() 하나로 고정한다. 나중에 로컬 임베딩으로 바꿔도 바깥(엔진, MCP)은 그대로.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter

WORD = re.compile(r"[0-9A-Za-z가-힣]+")
TEXT_EXT = {".txt", ".md", ".html", ".htm", ".css"}
FIELD_WEIGHT = {"title": 3.0, "keywords": 3.0, "task": 2.5, "tags": 2.0, "summary": 1.5, "spec": 1.0, "body": 0.6}


def grams(text: str) -> list[str]:
    out = []
    for w in WORD.findall((text or "").lower()):
        out.append("w:" + w)
        n = 3 if len(w) >= 3 else 2
        if len(w) >= n:
            out += ["g:" + w[i:i + n] for i in range(len(w) - n + 1)]
    return out


def fields(lib, n: dict) -> dict[str, str]:
    f = {
        "title": n.get("title") or "",
        "keywords": " ".join(n.get("keywords") or []),
        "task": n.get("task") or "",
        "tags": " ".join(n.get("tags") or []),
        "summary": n.get("summary") or "",
        "spec": n.get("spec") or "",
        "body": "",
    }
    if n.get("exists") and n.get("file") and os.path.splitext(n["file"])[1].lower() in TEXT_EXT:
        try:
            with open(lib.abspath(n["file"]), encoding="utf-8", errors="replace") as fh:
                body = fh.read(6000)
            f["body"] = re.sub(r"<[^>]+>", " ", body)
        except OSError:
            pass
    return f


def search(lib, query: str, kinds: set | None = None, set_id: str | None = None, limit: int = 5,
           exclude: set | None = None) -> list[dict]:
    """kinds: {'output', 'reference', 'rule'} 중 일부. set_id: 그 세트의 산출물만."""
    cands = []
    for n in lib.nodes.values():
        if n["kind"] == "unregistered" or (exclude and n["id"] in exclude):
            continue
        k = "rule" if n["type"] == "rule" else n["kind"]
        if kinds and k not in kinds:
            continue
        if set_id and n.get("set") != set_id:
            continue
        cands.append(n)
    q = Counter(grams(query))
    if not q or not cands:
        return []
    docs = []
    df: Counter = Counter()
    for n in cands:
        fg = {name: Counter(grams(text)) for name, text in fields(lib, n).items()}
        present = set().union(*[set(c) for c in fg.values()])
        df.update(present)
        docs.append((n, fg))
    N = len(docs)
    out = []
    for n, fg in docs:
        score, hits = 0.0, set()
        for g in q:
            if g not in df:
                continue
            idf = math.log(1 + (N - df[g] + 0.5) / (df[g] + 0.5))
            for name, c in fg.items():
                if g in c:
                    score += FIELD_WEIGHT[name] * idf * (1 + math.log(c[g])) * (2.0 if g.startswith("w:") else 1.0)
                    hits.add(g)
        if score > 0:
            out.append({"id": n["id"], "score": round(score, 4), "title": n["title"], "kind": n["kind"],
                        "type": n["type"], "set": n.get("set"), "matched": len(hits)})
    out.sort(key=lambda r: (-r["score"], r["id"]))
    return out[:limit]
