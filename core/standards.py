"""작업 설명 → 맞는 표준 찾기와 조립 (v3.7, Q78-A).

다른 프로젝트에서 일하던 AI가 Assets Manager를 스킬처럼 부를 때 쓰는 입구.
`cli.py standard "<작업>"`, MCP 도구 `standard`가 같은 함수를 쓴다. 결정적(P2): 같은 라이브러리 + 같은 작업 → 같은 결과.
"""
from __future__ import annotations

import math
from collections import Counter

from engine import Library, CycleError
from search import grams


def _text(lib: Library, n: dict) -> dict[str, str]:
    vals = []
    for r in n["rules"].values():
        v = r.get("value")
        if isinstance(v, str) and not v.startswith("ref:"):
            vals.append(v)
    skills = " ".join(lib.nodes[k]["title"] + " " + lib.nodes[k]["description"] for k in lib.skills_of(n["id"]))
    return {"title": n["title"] + " " + n["id"].replace("-", " "), "body": n.get("body") or "", "rules": " ".join(vals),
            "skills": skills}


WEIGHT = {"title": 4.0, "body": 2.0, "rules": 1.0, "skills": 1.5}


def find(lib: Library, task: str, limit: int = 5) -> list[dict]:
    """작업 설명과 가장 맞는 세트들. 점수 = Σ 필드 가중치 × idf × (1 + log tf)."""
    sets = [n for n in lib.rule_nodes() if n["kind"] == "set" and n["id"] not in lib.cyclic]
    q = Counter(grams(task))
    if not q or not sets:
        return []
    docs = [(n, {k: Counter(grams(v)) for k, v in _text(lib, n).items()}) for n in sets]
    df: Counter = Counter()
    for _, fg in docs:
        df.update(set().union(*[set(c) for c in fg.values()]))
    N = len(docs)
    out = []
    for n, fg in docs:
        score = 0.0
        for g in q:
            idf = math.log(1 + (N - df[g] + 0.5) / (df[g] + 0.5))
            for field, c in fg.items():
                if c[g]:
                    score += WEIGHT[field] * idf * (1 + math.log(c[g])) * (2 if g.startswith("w:") else 1)
        if score > 0:
            out.append({"id": n["id"], "title": n["title"], "score": round(score, 3)})
    out.sort(key=lambda x: (-x["score"], x["id"]))
    return out[:limit]


def agent_target(lib: Library) -> str:
    for k, v in lib.targets.items():
        if v.get("kind") == "agent":
            return k
    return next(iter(lib.targets))


def standard(lib: Library, task: str, set_id: str | None = None, gate: bool | None = None) -> dict:
    """맞는 세트를 골라 경로 포함 프롬프트로 조립한다. 맞는 게 없으면 전역 원칙(있으면)으로."""
    cands = find(lib, task) if not set_id else []
    chosen = set_id or (cands[0]["id"] if cands else None)
    fallback = False
    if not chosen:
        g = lib.nodes.get("global")
        if g and g["type"] == "rule":
            chosen, fallback = "global", True
    if not chosen:
        return {"found": False, "text": "맞는 작업 표준이 없습니다. 평소대로 진행하세요.", "candidates": []}
    try:
        pkg = lib.package(chosen, agent_target(lib), write=True, task=task, gate=gate)
    except (KeyError, CycleError) as e:
        return {"found": False, "text": f"표준을 조립하지 못했습니다: {e}", "candidates": cands}
    head = [f"<!-- Assets Manager 표준: {lib.nodes[chosen]['title']} ({chosen}) -->"]
    if fallback:
        head.append("이 작업에 딱 맞는 세트가 없어 '내 전역 원칙'을 따릅니다.")
    elif len(cands) > 1:
        head.append("다른 후보: " + ", ".join(f"{c['title']} ({c['id']})" for c in cands[1:4])
                    + " — 더 맞는 것이 있으면 `--set <id>`로 다시 부르세요.")
    return {"found": True, "set": chosen, "title": lib.nodes[chosen]["title"], "fallback": fallback,
            "candidates": cands, "text": "\n".join(head) + "\n\n" + pkg["clipboard"], "files": pkg["files"]}
