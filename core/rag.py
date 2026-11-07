"""RAG 내보내기 (기획서 v3.2 13.5).

노드를 `노드id#부분` 조각으로 펼쳐 JSONL로 내보낸다. 대부분의 RAG 도구(LlamaIndex, LangChain 등)가 그대로 읽는다.
조각 id는 다시 내보내도 유지되고, metadata.sha로 바뀐 조각만 다시 색인할 수 있다.

사용:  python app/core/rag.py [라이브러리 루트] [--out 파일] [--include-internal]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import APP_DIR, CACHE_DIR, Library  # noqa: E402

TEXT_EXT = {".txt", ".md", ".html", ".htm", ".css"}
CHUNK_CHARS = 800


def _split(text: str, size: int = CHUNK_CHARS) -> list[str]:
    paras = [p.strip() for p in text.replace("\r", "").split("\n\n") if p.strip()]
    out, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > size:
            out.append(cur)
            cur = ""
        cur = (cur + "\n\n" + p).strip() if cur else p
        while len(cur) > size * 1.5:
            out.append(cur[:size])
            cur = cur[size:]
    if cur:
        out.append(cur)
    return out


def chunks(lib: Library, include_internal: bool = False):
    for n in sorted(lib.nodes.values(), key=lambda x: x["id"]):
        if n["kind"] == "unregistered":
            continue
        if n.get("sensitivity") == "internal" and not include_internal:
            continue
        meta = {"node": n["id"], "title": n["title"], "type": n["type"], "kind": n["kind"],
                "tags": n.get("tags") or [], "sensitivity": n.get("sensitivity", "public"),
                "path": n.get("path"), "file": n.get("file"), "set": n.get("set")}
        parts: list[tuple[str, str]] = []
        if n["type"] == "rule":
            parts.append(("summary", f"{n['title']} ({n['kind']}). 상속: {', '.join(n['inherits']) or '-'}. {n.get('body', '')}".strip()))
            if n["id"] not in lib.cyclic:
                for e in lib.assemble(n["id"])["entries"]:
                    v = e["value"] if isinstance(e["value"], str) else json.dumps(e["value"], ensure_ascii=False)
                    parts.append((f"rule.{e['key']}", f"{e['label']}: {v} (출처: {e['source']})"))
        else:
            head = " ".join(x for x in [n["title"], n.get("summary", ""), ("작업: " + n["task"]) if n.get("task") else "",
                                        ("관련어: " + ", ".join(n["keywords"])) if n.get("keywords") else ""] if x)
            parts.append(("summary", head))
            if n.get("spec"):
                parts.append(("spec", n["spec"]))
            if n.get("check"):
                parts.append(("check", "\n".join("- " + c for c in n["check"])))
            if n.get("exists") and os.path.splitext(n["file"])[1].lower() in TEXT_EXT:
                with open(lib.abspath(n["file"]), encoding="utf-8", errors="replace") as f:
                    for i, c in enumerate(_split(f.read())):
                        parts.append((f"body.{i}", c))
        for part, text in parts:
            if not text.strip():
                continue
            yield {"id": f"{n['id']}#{part}", "text": text,
                   "metadata": {**meta, "part": part, "sha": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]}}


def export_jsonl(lib: Library, out: str | None = None, include_internal: bool = False) -> dict:
    out = out or os.path.join(lib.root, CACHE_DIR, "export", "chunks.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    count = 0
    with open(out, "w", encoding="utf-8") as f:
        for c in chunks(lib, include_internal):
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
            count += 1
    return {"path": out, "chunks": count}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=os.path.dirname(APP_DIR))
    ap.add_argument("--out")
    ap.add_argument("--include-internal", action="store_true")
    a = ap.parse_args()
    print(json.dumps(export_jsonl(Library(a.root), a.out, a.include_internal), ensure_ascii=False))
