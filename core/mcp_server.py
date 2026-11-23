"""Assets Manager MCP 서버 (기획서 v3.2 13.5). 외부 AI(Claude 데스크톱 등)가 라이브러리를 직접 검색하고 조립한다.

표준 입출력(stdio)으로 JSON-RPC 2.0을 주고받는다. 외부 패키지 없이 동작한다.
도구:
  search(query, kind?, set?, limit?)   결정적 검색 (키워드 + 켜져 있으면 로컬 임베딩)
  get(id)                              노드 하나 (메타, 규칙, 본문 일부)
  related(id)                          그래프 이웃 (상속, 키, 정보 엣지)
  assemble(id, task?, target?)         세트의 결정적 조립 프롬프트 (+ 참고 예시)

Claude 데스크톱 설정(claude_desktop_config.json) 예:
  "mcpServers": { "assets-manager": { "command": "py", "args": ["D:\\\\projects\\\\assets_manager\\\\app\\\\core\\\\mcp_server.py"] } }
"""
from __future__ import annotations

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import APP_DIR, Library  # noqa: E402
import search as searcher  # noqa: E402

PROTOCOL = "2024-11-05"
ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(APP_DIR)

TOOLS = [
    {"name": "standard", "description": "사용자가 맡긴 작업에 맞는 작업 표준을 찾아 조립된 지침(규칙·첨부 경로·함께 쓰는 스킬·진행 방식·자가 점검)을 돌려줍니다. 결과물을 만들기 전에 부르세요.",
     "inputSchema": {"type": "object", "properties": {
         "task": {"type": "string", "description": "맡은 작업 한 줄 (예: '네이버 블로그 여름 휴가 글')"},
         "set": {"type": "string", "description": "특정 세트 id (후보 중 다른 것을 고를 때)"}}, "required": ["task"]}},
    {"name": "search", "description": "Assets Manager 라이브러리에서 세트·레퍼런스·산출물을 키워드(뜻 검색이 켜져 있으면 뜻까지)로 찾습니다. 같은 질의면 항상 같은 결과입니다.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "찾을 내용 (예: '블로그 차트 스타일', 'AI 비서 소개 글')"},
         "kind": {"type": "string", "enum": ["rule", "reference", "output"], "description": "노드 종류로 좁히기 (선택)"},
         "set": {"type": "string", "description": "이 세트의 산출물만 (선택)"},
         "limit": {"type": "integer", "default": 5}}, "required": ["query"]}},
    {"name": "get", "description": "노드 하나의 상세(메타데이터, 규칙, spec, 본문 일부)를 돌려줍니다.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "related", "description": "노드의 그래프 이웃(상속, 키로 연결된 레퍼런스, 정보 엣지, 산출물)을 돌려줍니다.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "assemble", "description": "세트를 사용자 표준대로 조립한 작업 지침(프롬프트)과 첨부할 파일 경로를 돌려줍니다. 작업 설명을 주면 비슷한 과거 산출물이 참고 예시로 붙습니다. 작업을 시작하기 전에 이 지침을 따르세요.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "규칙 노드(세트) id"},
         "task": {"type": "string", "description": "이번 작업 설명 (선택)"},
         "target": {"type": "string", "default": "codex", "description": "대상 설정 이름 (경로 포함 여부와 한도에 영향)"}},
         "required": ["id"]}},
]


def call(name: str, args: dict):
    lib = Library(ROOT)
    if name == "standard":
        import standards
        r = standards.standard(lib, args["task"], args.get("set"))
        return {"found": r["found"], "set": r.get("set"), "candidates": r.get("candidates", []), "prompt": r["text"]}
    if name == "search":
        kinds = {args["kind"]} if args.get("kind") else None
        return searcher.search(lib, args["query"], kinds=kinds, set_id=args.get("set"), limit=int(args.get("limit") or 5))
    if name == "get":
        n = lib.nodes.get(args["id"])
        if not n:
            raise KeyError(f"노드 없음: {args['id']}")
        out = {k: v for k, v in n.items() if k not in ("mtime",)}
        if n.get("file"):
            out["abs_file"] = lib.abspath(n["file"])
        if n["type"] == "rule" and n["id"] not in lib.cyclic:
            out["final_rules"] = [{"key": e["key"], "label": e["label"], "value": e["value"], "source": e["source"]}
                                  for e in lib.assemble(n["id"])["entries"]]
        if n.get("exists") and n.get("file", "").lower().endswith((".txt", ".md", ".html", ".css")):
            with open(lib.abspath(n["file"]), encoding="utf-8", errors="replace") as f:
                out["text"] = f.read(3000)
        return out
    if name == "related":
        g = lib.graph()
        nid = args["id"]
        return [e for e in g["edges"] if nid in (e["source"], e["target"])]
    if name == "assemble":
        p = lib.package(args["id"], args.get("target") or "codex", task=args.get("task") or "", write=True)
        return {"prompt": p["clipboard"], "files": p["files"], "warnings": p["warnings"], "violations": p["violations"]}
    raise KeyError(f"알 수 없는 도구: {name}")


def handle(msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "assets-manager", "version": "0.5.0"}}
    elif method in ("notifications/initialized", "notifications/cancelled"):
        return None
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params") or {}
        try:
            data = call(p.get("name"), p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}]}
        except Exception as e:
            result = {"content": [{"type": "text", "text": f"오류: {e}"}], "isError": True}
    else:
        if mid is None:
            return None
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"지원하지 않는 메서드: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
