"""Assets Manager 사이드카 서버 (표준 라이브러리만 사용).

실행:  python app/core/server.py [라이브러리 루트] [--port 8765]
기본 루트는 app 폴더의 상위 폴더(= assets 라이브러리)입니다.
브라우저에서 http://127.0.0.1:8765 를 열어도 됩니다 (드래그는 텍스트·클립보드로 대체).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import APP_DIR, Library  # noqa: E402
import authoring  # noqa: E402
import extract as extractor  # noqa: E402
import folder_requests as freq  # noqa: E402
import drops  # noqa: E402
import export_cmd  # noqa: E402
import rag  # noqa: E402
import store  # noqa: E402
import tidy  # noqa: E402
import components  # noqa: E402
import local_ai  # noqa: E402
import skills  # noqa: E402
import console  # noqa: E402
import terminal  # noqa: E402
from engine import library_signature  # noqa: E402

APP_VERSION = "0.14.1"  # UI(app.js)의 APP_VERSION과 같아야 함
UI_DIR = os.path.join(APP_DIR, "ui")
LOCK = threading.Lock()
STATE: dict = {}


def library(sync: bool = False, message: str | None = None) -> Library:
    """라이브러리를 읽는다. 파일이 그대로면(서명 같음) 캐시를 쓴다 (v3.8).
    sync=True면 항상 다시 읽고 버전 보관·Git 기록도 한다."""
    with LOCK:
        sig = library_signature(STATE["root"])
        cached = STATE.get("lib")
        if cached is not None and not sync and STATE.get("sig") == sig:
            return cached
        lib = Library(STATE["root"])
        lib.build_index()
        if sync:
            STATE["last_sync"] = store.backup_sync(lib, message)
            sig = library_signature(STATE["root"])  # 보관이 _defaults 등을 만들었을 수 있다
        STATE["lib"], STATE["sig"] = lib, sig
        return lib


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 조용히
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _file(self, path: str) -> None:
        if not os.path.isfile(path):
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        try:
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            p = unquote(u.path)
            if p in ("/", "/index.html"):
                return self._file(os.path.join(UI_DIR, "index.html"))
            if p.startswith("/ui/"):
                return self._file(safe_join(UI_DIR, p[4:]))
            if p.startswith("/file/"):
                return self._file(safe_join(STATE["root"], p[6:]))
            if p == "/api/health":
                return self._json({"ok": True, "root": STATE["root"], "version": APP_VERSION, "pid": os.getpid()})
            if p == "/api/graph":
                lib = library(sync=True)
                history = store.read_history(lib.root)
                g = lib.graph(history, store.fresh_files(lib.root))
                g["targets"] = {k: {"label": v.get("label", k), "kind": v.get("kind", "chat")} for k, v in lib.targets.items()}
                g["root"] = lib.root
                g["changes"] = lib.changes()
                g["defaults_updates"] = store.defaults_updates(lib.root)
                g["backup"] = STATE.get("last_sync", {}).get("git", {})
                g["trash_count"] = len(store.list_trash(lib.root))
                g["requests"] = freq.list_requests(lib.root)
                g["layout"] = read_layout(lib.root)
                inst = skills.scan(lib)  # v3.6 설치된 스킬 (라이브러리 안·개인 폴더)
                part = skills.graph_part(lib, inst)
                g["nodes"] += part["nodes"]
                g["edges"] += part["edges"]
                g["skills"] = inst
                for n in g["nodes"]:  # v3.8 바뀐 노드 깜빡이기용: 노드 파일(규칙 md·메타·원본)의 크기·수정 시각
                    n["sig"] = node_sig(lib, n)
                g["sig"] = STATE.get("sig")
                return self._json(g)
            if p.startswith("/api/node/"):
                return self._json(node_detail(library(), p[len("/api/node/"):]))
            if p.startswith("/api/package/"):
                lib = library()
                nid = p[len("/api/package/"):]
                pkg = lib.package(nid, q.get("target", "claude_desktop"),
                                  trim=q.get("trim") == "1", strip_sensitive=q.get("strip") == "1",
                                  trim_all_targets=q.get("all") == "1", task=q.get("task", ""),
                                  gate=None if q.get("gate") in (None, "") else q.get("gate") == "1")
                pkg["prompt_hash"] = hashlib.sha1(pkg["prompt"].encode("utf-8")).hexdigest()[:12]
                return self._json(pkg)
            if p == "/api/authoring":
                lib = library()
                rels = [r for r in q.get("files", "").split("|") if r]
                return self._json(authoring.build(lib, rels, q.get("target", "codex"), q.get("note", "")))
            if p == "/api/layout":
                return self._json(read_layout(STATE["root"]))
            if p == "/api/export-command":
                return self._json(export_cmd.build(library(), q.get("node") or None))
            if p == "/api/rag/export":
                return self._json(rag.export_jsonl(library(), include_internal=q.get("internal") == "1"))
            if p == "/api/search":
                import search as searcher
                kinds = set(q["kinds"].split(",")) if q.get("kinds") else None
                return self._json(searcher.search(library(), q.get("q", ""), kinds=kinds, set_id=q.get("set"), limit=int(q.get("limit", 8))))
            if p == "/api/mcp-config":
                script = os.path.join(APP_DIR, "core", "mcp_server.py")
                py = "py" if os.name == "nt" else "python3"
                cfg = {"mcpServers": {"assets-manager": {"command": py, "args": [script, STATE["root"]]}}}
                return self._json({"config": cfg, "text": json.dumps(cfg, ensure_ascii=False, indent=2)})
            if p == "/api/local-ai":  # v3.5 로컬 AI 도구 상태 (처음이면 찾기까지)
                root = library().root
                st = local_ai.status(root)
                if not st["detected"]:
                    local_ai.detect(root)
                    st = local_ai.status(root)
                return self._json(st)
            m = re.fullmatch(r"/api/local-ai/job/([A-Za-z0-9_-]+)", u.path)
            if m:
                return self._json(local_ai.get_job(library().root, m.group(1)))
            if p == "/api/changes":  # v3.8 파일 변경 감지 (UI가 창이 보일 때만 폴링)
                return self._json({"sig": library_signature(STATE["root"])})
            if p == "/api/term":  # v3.8 터미널 세션 목록·사용 가능 여부
                return self._json({"available": terminal.available(), "sessions": terminal.list_sessions(),
                                   "max_ai": terminal.MAX_AI, "idle_hint": terminal.IDLE_HINT})
            m = re.fullmatch(r"/api/term/([0-9a-f]+)/read", u.path)
            if m:
                try:
                    s = terminal.get(m.group(1))
                except KeyError as e:
                    return self._json({"error": str(e), "alive": False}, 404)
                return self._json(s.read(int(q.get("since", 0)), min(float(q.get("wait", 0)), 10.0)))
            if p == "/api/caller":  # v3.7 AI가 Assets Manager를 부르게 하기
                return self._json(skills.caller_status(library().root))
            m = re.fullmatch(r"/api/skills/set/(.+)", u.path)
            if m:  # v3.6 세트 → 스킬 미리보기·설치 상태
                return self._json(skills.status(library(), unquote(m.group(1))))
            if p == "/api/tidy":  # v3.4 하위 정리: 진단 + AI 요청 프롬프트
                lib = library()
                goal = None
                if (q.get("goal") or "").startswith("skill:"):
                    goal = {"kind": "skill_to_principle", "skill": q["goal"][6:]}
                res = tidy.build(lib, q["node"], q.get("target") or None, goal)
                res["prompt_path"] = os.path.join(lib.root, freq.INBOX, res["request"], "prompt.md")
                return self._json(res)
            if p == "/api/tidy/open":  # 이 노드의 열린 정리 요청 (에이전트가 out/plan.yaml에 썼을 수도)
                lib = library()
                for r in freq.list_requests(lib.root):
                    if r.get("kind") == "tidy" and r.get("node") == q.get("node") and r["status"] in ("sent", "received"):
                        return self._json({"request": r["id"], "status": r["status"],
                                           "preview": tidy.preview(lib, r["id"]) if r["status"] == "received" else None})
                return self._json({"request": None})
            if p == "/api/components/request":  # v3.9 구성 요소 분리 요청 (Q81-A)
                return self._json(components.build(library(), q["node"], q.get("target") or None))
            if p == "/api/components/open":  # 이 노드의 열린 분리 요청 (에이전트가 out/plan.yaml에 썼을 수도)
                return self._json(components.open_request(library(), q.get("node", "")))
            if p == "/api/requests":
                return self._json(freq.list_requests(STATE["root"]))
            m = re.fullmatch(r"/api/request/([A-Za-z0-9_-]+)/(package|preview)", p)
            if m:
                lib = library()
                if m.group(2) == "package":
                    return self._json(freq.package(lib, m.group(1), q.get("target", "codex"), q.get("note", "")))
                return self._json(freq.preview(lib, m.group(1)))
            if p == "/api/trash":
                return self._json(store.list_trash(STATE["root"]))
            if p == "/api/history":
                return self._json(store.read_history(STATE["root"])[-200:])
            return self._json({"error": "unknown route"}, 404) if p.startswith("/api/") else \
                self._file(safe_join(UI_DIR, p.lstrip("/")))
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        try:
            u = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            root = STATE["root"]
            if u.path == "/api/shutdown":  # 새 버전 서버가 이전 서버를 정리할 때 사용
                self._json({"ok": True})
                terminal.close_all()
                threading.Thread(target=STATE["server"].shutdown, daemon=True).start()
                return
            if u.path == "/api/console":  # v3.8 콘솔 탭: `am` 한 줄 실행
                with LOCK:
                    res = console.run_line(root, body.get("line", ""), "user")
                return self._json(res)
            if u.path == "/api/term/start":  # v3.8 AI 탭 (사용자가 누를 때만)
                terminal.ensure_permissions(root)
                return self._json(terminal.start(root, body.get("tool", "claude"),
                                                 int(body.get("cols") or 100), int(body.get("rows") or 30)))
            m = re.fullmatch(r"/api/term/([0-9a-f]+)/(write|resize|close)", u.path)
            if m:
                sid, act = m.group(1), m.group(2)
                if act == "close":
                    return self._json(terminal.close(sid))
                try:
                    sess = terminal.get(sid)
                except KeyError as e:
                    return self._json({"error": str(e)}, 404)
                if act == "write":
                    sess.write(body.get("data", ""))
                else:
                    sess.resize(body.get("cols", 100), body.get("rows", 30))
                return self._json({"ok": True})
            if u.path == "/api/approve":
                return self._json({"approved": library().approve(body.get("id"))})
            if u.path == "/api/history":
                rec = {k: body.get(k) for k in ("node", "target", "method", "kind", "prompt_hash", "files", "task", "request") if body.get(k)}
                return self._json(store.append_history(root, rec))
            if u.path == "/api/layout":
                write_layout(root, body.get("positions") or {}, body.get("replace", False))
                return self._json({"ok": True})
            if u.path == "/api/stage":  # v3.3 예상 결과 창: 놓은 모든 것의 단일 입구
                uploads = [{"name": x["name"], "data": base64.b64decode(x["b64"])} for x in body.get("uploads") or []]
                return self._json(drops.stage(library(), body.get("node"), body.get("paths"), uploads, bool(body.get("folder"))))
            m = re.fullmatch(r"/api/stage/([A-Za-z0-9_-]+)/(commit|discard)", u.path)
            if m:
                if m.group(2) == "discard":
                    freq.discard(root, m.group(1))
                    return self._json({"ok": True})
                res = drops.commit(library(), m.group(1), body.get("option"), body.get("params") or {})
                library(sync=True, message=f"놓기: {body.get('option')} ({m.group(1)})")
                return self._json(res)
            m = re.fullmatch(r"/api/request/([A-Za-z0-9_-]+)/(attach|assign|target)", u.path)
            if m:
                if m.group(2) == "target":
                    return self._json(freq.set_target(library(), m.group(1), body.get("node") or None))
                if m.group(2) == "assign":
                    return self._json(freq.assign(library(), m.group(1), body["path"], body.get("file")))
                items = [(os.path.basename(x), drops.open_bytes(x)) for x in body.get("paths") or [] if os.path.isfile(x)]
                items += [(x["name"].split("/")[-1], base64.b64decode(x["b64"])) for x in body.get("uploads") or []]
                return self._json(freq.attach(library(), m.group(1), items))
            m = re.fullmatch(r"/api/node/([^/]+)/gate", u.path)  # v3.4 세트의 승인 게이트 기본값
            if m:
                lib = library()
                nid = unquote(m.group(1))
                n = lib.nodes.get(nid)
                if not n or n["type"] != "rule":
                    return self._json({"error": "규칙 노드가 아닙니다"}, 400)
                store.backup_sync(lib)
                freq._rewrite_frontmatter(lib.abspath(n["path"]), lambda fm: fm.__setitem__("gate", bool(body.get("gate"))))
                library(sync=True, message=f"승인 게이트 {'켬' if body.get('gate') else '끔'}: {nid}")
                return self._json({"ok": True, "gate": bool(body.get("gate"))})
            m = re.fullmatch(r"/api/node/([^/]+)/promote", u.path)
            if m:
                res = drops.promote(library(), unquote(m.group(1)), bool(body.get("overwrite")))
                if res.get("promoted"):
                    library(sync=True, message=f"골든 샘플 승격: {res['promoted']} → {res['set']}")
                return self._json(res)
            if u.path == "/api/undo":
                res = drops.undo(library(), body.get("created") or [])
                library(sync=True, message=f"되돌리기: {len(res['files'])}개 휴지통으로")
                return self._json(res)
            if u.path == "/api/drop":  # (v3.1 호환) 창에 놓은 모든 것의 단일 입구
                return self._json(handle_drop(body))
            if u.path == "/api/request/prepare":
                uploads = [{"name": x["name"], "data": base64.b64decode(x["b64"])} for x in body.get("uploads") or []]
                return self._json(freq.prepare(library(), body.get("node"), body.get("paths"), uploads))
            if u.path == "/api/skills/export":
                lib = library()
                res = skills.export(lib, body["set"], body.get("scope", "library"), body.get("agents") or list(skills.AGENTS),
                                    bool(body.get("overwrite")))
                if res.get("ok"):
                    store.append_history(lib.root, {"kind": "skill_export", "node": body["set"], "scope": body.get("scope"),
                                                    "written": res["written"]})
                return self._json(res)
            if u.path == "/api/caller":
                return self._json(skills.caller_apply(library().root, body["item"], body.get("action", "install")))
            if u.path == "/api/skills/import":
                lib = library()
                res = skills.import_skill(lib, body["path"], body.get("node") or None, body.get("disable_original", True))
                library(sync=True, message=f"스킬 가져오기: {res['id']}")
                return self._json(res)
            if u.path == "/api/skills/attach":
                lib = library()
                res = skills.attach(lib, body["skill"], body["node"], bool(body.get("detach")))
                library(sync=True, message=f"스킬 {'떼기' if body.get('detach') else '붙이기'}: {body['skill']} → {body['node']}")
                return self._json(res)
            if u.path == "/api/skills/restore-original":
                return self._json(skills.restore_original(library(), body["skill"]))
            if u.path == "/api/skills/remove":
                lib = library()
                return self._json(skills.remove(lib, body["set"], body.get("scope", "library"),
                                                body.get("agents") or list(skills.AGENTS), bool(body.get("force"))))
            if u.path == "/api/local-ai/detect":
                root = library().root
                local_ai.detect(root)
                return self._json(local_ai.status(root))
            if u.path == "/api/local-ai/config":
                return self._json(local_ai.update_config(library().root, body))
            if u.path == "/api/local-ai/test":
                return self._json(local_ai.test(library().root, body.get("tool") or ""))
            if u.path == "/api/local-ai/run":
                lib = library()
                try:
                    job = local_ai.run_for(lib, body)
                except LookupError as e:
                    return self._json({"error": str(e), "needs_setup": True}, 409)
                store.append_history(lib.root, {"kind": "local_ai", "job": job["id"], "tool": job["tool"],
                                                "task": job["kind"], **{k: v for k, v in job["ref"].items() if v}})
                return self._json(job)
            m = re.fullmatch(r"/api/local-ai/job/([A-Za-z0-9_-]+)/cancel", u.path)
            if m:
                return self._json(local_ai.cancel(library().root, m.group(1)))
            m = re.fullmatch(r"/api/components/([A-Za-z0-9_-]+)/(receive|preview|apply)", u.path)
            if m:  # v3.9 구성 요소: AI 답 붙여넣기 → 미리보기(상자 조정) → 반영
                lib = library()
                rid, act = m.group(1), m.group(2)
                if act == "receive":
                    return self._json(components.receive(lib, rid, body.get("text") or ""))
                if act == "preview":
                    return self._json(components.preview(lib, rid, body.get("edits")))
                res = components.apply(lib, rid, body.get("selected") or [], body.get("edits"))
                library(sync=True, message=f"구성 요소 반영 ({res['node']}): {', '.join(res['applied'])}")
                return self._json(res)
            m = re.fullmatch(r"/api/components/node/([^/]+)", u.path)
            if m:  # v3.9 상세 화면에서 사람이 고친 요소 목록 저장
                res = components.save(library(), unquote(m.group(1)), body.get("components") or [])
                library(sync=True, message=f"구성 요소 수정 ({res['node']})")
                return self._json(res)
            m = re.fullmatch(r"/api/tidy/([A-Za-z0-9_-]+)/(receive|preview|apply)", u.path)
            if m:
                lib = library()
                rid, act = m.group(1), m.group(2)
                if act == "receive":
                    return self._json(tidy.receive(lib, rid, body.get("text") or ""))
                if act == "preview":
                    return self._json(tidy.preview(lib, rid, body.get("selected")))
                res = tidy.apply(lib, rid, body.get("selected") or [])
                library(sync=True, message=f"하위 정리 ({rid}): {len(res['written'])}개 파일, 휴지통 {len(res['trashed'])}")
                return self._json(res)
            m = re.fullmatch(r"/api/request/([A-Za-z0-9_-]+)/(apply|discard)", u.path)
            if m:
                if m.group(2) == "discard":
                    freq.discard(root, m.group(1))
                    return self._json({"ok": True})
                res = freq.apply(library(), m.group(1), body.get("selected") or [])
                library(sync=True, message=f"AI 결과 적용 ({m.group(1)}): {len(res['applied'])}개")
                return self._json(res)
            if u.path == "/api/import":
                items = []
                for it in body.get("items", []):
                    if it.get("path"):
                        items.append({"path": it["path"]})
                    elif it.get("b64") is not None:
                        items.append({"name": it["name"], "data": base64.b64decode(it["b64"])})
                for it in list(items):  # plan.yaml이 든 zip은 폴더 요청의 결과 → 바로 쓰지 않고 결과 확인으로
                    data = it.get("data")
                    if data is None and it["path"].lower().endswith(".zip"):
                        with open(it["path"], "rb") as f:
                            data = f.read()
                    if data is not None and (it.get("name") or it.get("path", "")).lower().endswith(".zip") and freq.zip_has_plan(data):
                        rid = freq.receive_zip(library(), data)
                        return self._json({"results": [], "request_result": rid})
                res = store.import_items(library(), items)
                added = [r for r in res if r.get("status") in ("added", "updated")]
                lib = library(sync=True, message=f"가져오기: {len(added)}개 ({', '.join(r['rel'] for r in added[:5])})")
                return self._json({"results": res, "issues": lib.issues})
            if u.path == "/api/delete":
                lib = library()
                info = store.delete_node(lib, body["id"])
                library(sync=True, message=f"삭제: {body['id']} (휴지통 {info['trash_id']})")
                return self._json(info)
            if u.path == "/api/restore-trash":
                files = store.restore_trash(root, body["id"])
                library(sync=True, message=f"휴지통에서 복구: {', '.join(files)}")
                return self._json({"restored": files})
            if u.path == "/api/restore-version":
                rel = store.restore_version(library(), body["rel"], body["version"])
                library(sync=True, message=f"이전 버전으로 되돌림: {rel} ({body['version']})")
                return self._json({"restored": rel})
            if u.path == "/api/defaults/upgrade":
                done = store.upgrade_defaults(library())
                library(sync=True, message=f"앱 기본값 업데이트: {', '.join(done)}")
                return self._json({"upgraded": done})
            return self._json({"error": "unknown route"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)


def node_detail(lib: Library, nid: str) -> dict:
    if nid.startswith("skill:"):
        s = next((x for x in skills.scan(lib) if x["id"] == nid), None)
        if not s:
            raise KeyError(f"노드 없음: {nid}")
        return {"id": nid, "type": "installed_skill", "skill": s}
    if nid.startswith("ai:"):
        tid = nid[3:]
        t = lib.targets.get(tid)
        if not t:
            raise KeyError(f"노드 없음: {nid}")
        hist = [h for h in store.read_history(lib.root) if h.get("target") == tid]
        authored = [n["id"] for n in lib.nodes.values() if n.get("authored_by") == tid]
        return {"id": nid, "type": "ai", "kind": t.get("kind", "chat"), "title": t.get("label", tid),
                "path": "_defaults/targets.yaml", "target": t, "history": hist[-50:][::-1],
                "drops": sum(1 for h in hist if h.get("kind", "prompt") == "prompt"),
                "author_requests": sum(1 for h in hist if h.get("kind") == "author"), "authored": authored}
    n = lib.nodes.get(nid)
    if not n:
        raise KeyError(f"노드 없음: {nid}")
    out = dict(n)
    out["issues"] = [i for i in lib.issues if i["node"] == nid]
    out["descendants"] = lib.descendants(nid) if n["type"] == "rule" else []
    out["users"] = lib.users(nid) if n["type"] in ("rule", "skill") else []
    if n["type"] == "skill":  # v3.7: 이 스킬이 들어가는 세트 (상속으로 받는 것 포함)
        out["descendants"] = lib.descendants(nid)
    if n["type"] == "rule" and nid not in lib.cyclic:
        out["assembly"] = lib.assemble(nid)
    if n["type"] == "writing" and n.get("exists"):
        with open(lib.abspath(n["file"]), encoding="utf-8", errors="replace") as f:
            out["text"] = f.read(4000)
    if n["kind"] == "unregistered":
        out["extracted"] = extractor.extract(lib.abspath(n["file"]))
    if n.get("components"):  # v3.9 잘라 낸 그림·구도 도식 캐시를 맞춘다
        out["components_drawn"] = components.ensure(lib, n)
        out["components"] = [dict(c, legend=components.legend(c, n["components"]) if c["part"] == "composition" else "")
                             for c in n["components"]]
    out["used_by"] = [r["id"] for r in lib.rule_nodes()
                      if any(lib._ref_id(x["value"]) == nid for x in r["rules"].values())]
    if n["type"] == "rule":
        out["outputs"] = sorted([{"id": o["id"], "title": o["title"], "task": o.get("task", ""), "file": o.get("file"),
                                  "type": o["type"], "exists": o.get("exists")}
                                 for o in lib.nodes.values() if o["kind"] == "output" and o.get("set") == nid],
                                key=lambda o: o["id"], reverse=True)
        out["golden_current"] = (n["rules"].get("golden_sample") or {}).get("value")
    if n["kind"] == "output" and n.get("set") in lib.nodes:
        out["set_title"] = lib.nodes[n["set"]]["title"]
        out["is_golden"] = (lib.nodes[n["set"]]["rules"].get("golden_sample") or {}).get("value") == f"ref:{nid}"
    out["versions"] = store.versions_for(lib, nid)[:20]
    hist = store.read_history(lib.root)
    out["history"] = [h for h in hist if h.get("node") == nid][-30:][::-1]
    return out


def handle_drop(body: dict) -> dict:
    """놓은 것을 보고 판단한다.
    - plan.yaml이 든 zip → 폴더 요청의 결과 (결과 확인으로)
    - 노드 위에 놓았거나 폴더가 섞여 있음 → 폴더 요청 준비 (요청 확인으로)
    - 그 밖 → 바로 가져오기 (v3 규칙: 사람이 넣은 것은 바로 반영)
    """
    node = body.get("node") or None
    paths = [p for p in body.get("paths") or [] if not re.search(r"[\\/]\.assets[\\/]", p)]
    uploads = [{"name": x["name"], "data": base64.b64decode(x["b64"])} for x in body.get("uploads") or []]
    for p in paths:
        if p.lower().endswith(".zip") and os.path.isfile(p):
            with open(p, "rb") as f:
                data = f.read()
            if freq.zip_has_plan(data):
                return {"action": "result", "request": freq.receive_zip(library(), data)}
    for u in uploads:
        if u["name"].lower().endswith(".zip") and freq.zip_has_plan(u["data"]):
            return {"action": "result", "request": freq.receive_zip(library(), u["data"])}
    has_folder = any(os.path.isdir(p) for p in paths) or bool(body.get("folder"))
    if node and node.startswith("ai:"):
        node = None
    if node or has_folder:
        return {"action": "request", "request": freq.prepare(library(), node, paths, uploads)}
    items = [{"path": p} for p in paths] + [{"name": u["name"].split("/")[-1], "data": u["data"]} for u in uploads]
    res = store.import_items(library(), items)
    added = [r for r in res if r.get("status") in ("added", "updated")]
    library(sync=True, message=f"가져오기: {len(added)}개 ({', '.join(r['rel'] for r in added[:5])})")
    return {"action": "import", "results": res}


def node_sig(lib: Library, n: dict) -> str | None:
    src = lib.nodes.get(n["id"]) or {}
    rels = [x for x in (n.get("path"), src.get("path"), src.get("meta"), n.get("file")) if isinstance(x, str) and x]
    parts = []
    for rel in dict.fromkeys(rels):
        try:
            st = os.stat(os.path.join(lib.root, rel))
            parts.append(f"{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            continue
    return "|".join(parts) or None


def read_layout(root: str) -> dict:
    try:
        with open(os.path.join(root, "layout.json"), encoding="utf-8") as f:
            return json.load(f).get("positions", {})
    except (OSError, json.JSONDecodeError):
        return {}


def write_layout(root: str, positions: dict, replace: bool = False) -> None:
    cur = {} if replace else read_layout(root)
    for k, v in positions.items():
        try:
            cur[str(k)] = {"x": round(float(v["x"]), 1), "y": round(float(v["y"]), 1)}
        except (KeyError, TypeError, ValueError):
            continue
    with open(os.path.join(root, "layout.json"), "w", encoding="utf-8") as f:
        json.dump({"positions": dict(sorted(cur.items()))}, f, ensure_ascii=False, indent=1)


def safe_join(base: str, rel: str) -> str:
    path = os.path.normpath(os.path.join(base, rel))
    if os.path.commonpath([os.path.abspath(base), os.path.abspath(path)]) != os.path.abspath(base):
        raise PermissionError("허용되지 않은 경로")
    return path


def bind(port: int) -> ThreadingHTTPServer:
    """포트를 연다. 이전 서버가 남아 있으면 종료를 요청하고 다시 시도한다."""
    import time
    import urllib.request
    for attempt in range(10):
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            if attempt == 0:
                print(f"포트 {port}를 이미 쓰는 서버가 있어 종료를 요청합니다...", flush=True)
                try:
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}", method="POST",
                                                 headers={"Content-Type": "application/json"})
                    urllib.request.urlopen(req, timeout=2).read()
                except Exception:
                    print("이전 서버가 종료 요청을 받지 못했습니다(예전 버전). start.bat이 정리합니다.", flush=True)
            time.sleep(0.5)
    raise SystemExit(f"포트 {port}를 열 수 없습니다. 이전 Assets Manager 서버를 종료한 뒤 다시 실행하세요.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=os.path.dirname(APP_DIR))
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    STATE["root"] = os.path.abspath(args.root)
    srv = bind(args.port)
    STATE["server"] = srv
    library(sync=True, message="Assets Manager 시작")  # 시작 시 _defaults 복사, index 생성, 버전·Git 기록
    try:
        skills.install_tool_skill(STATE["root"])  # v3.6 도구 스킬을 라이브러리 안에 설치·갱신
    except OSError as e:
        print("도구 스킬 설치 실패:", e, flush=True)
    try:
        terminal.write_shims(STATE["root"])  # v3.8 `am` 명령 (.assets/bin)
        terminal.ensure_permissions(STATE["root"])  # v3.8 Claude Code: 규칙 파일 직접 편집 막기, `am` 허용
    except OSError as e:
        print("am 명령 준비 실패:", e, flush=True)
    import atexit
    atexit.register(terminal.close_all)
    print(f"Assets Manager 서버: http://127.0.0.1:{args.port}  (라이브러리: {STATE['root']})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        terminal.close_all()


if __name__ == "__main__":
    main()
