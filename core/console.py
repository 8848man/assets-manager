"""`am` 명령 (기획서 v3.8, Q79-A·Q80-A).

앱 안 콘솔 탭, 명령줄(`cli.py`, `.assets/bin/am`), 그리고 AI 탭의 Claude Code·Codex가 **같은 명령**으로 노드를 다룬다.
모든 쓰기는 앱 함수를 거친다:
  1) 바꾸기 전 `store.backup_sync`(P5) + 바뀔 파일의 이전 내용을 `.assets/console/undo.jsonl`에 쌓기 (`am undo`)
  2) 프런트매터를 키 단위로 고치기 (`folder_requests._rewrite_frontmatter`)
  3) `history/drops.jsonl`에 `kind: console`로 기록 (누가: 콘솔 = user, AI 탭 = ai-tab, 그 밖 = cli)
구조를 크게 바꾸는 일(여러 노드 재배치)은 `am tidy <id>`로 정리 요청을 만들어 앱에서 미리 보고 반영한다(P4).
"""
from __future__ import annotations

import io
import json
import os
import re
import shlex
import time
from contextlib import redirect_stdout

import yaml

from engine import Library, CycleError

UNDO = os.path.join(".assets", "console", "undo.jsonl")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

HELP = """am — Assets Manager 노드 명령

  보기
    am ls [sets|principles|skills|refs|all]   노드 목록
    am show <id>                              노드 자세히 (상속·원칙·스킬·최종 규칙)
    am find <검색어>                           세트·레퍼런스·산출물 검색
    am standard "<작업>" [--set <id>]          작업에 맞는 표준 조립 (AI가 작업 전에 부름)

  바꾸기 (바꾸기 전 상태는 보관되고 `am undo`로 되돌림)
    am inherit <id> [<부모> ...]              상속(계층) 바꾸기. 부모를 비우면 앱 기본값만
    am use <id> <원칙·스킬> ...                부품 붙이기          am unuse <id> <원칙·스킬> ...   떼기
    am set <id> <키> <값...>                   규칙 키 값 (텍스트 또는 ref:<레퍼런스 id>)
    am unset <id> <키>                         규칙 키 지우기
    am rename <id> <새 제목...>
    am new principle|set <id> <제목...> [--inherits a,b]
    am undo                                    마지막 변경 되돌리기

  큰 정리
    am tidy <id>                               하위 정리 요청 만들기 (앱의 정리 창에서 미리 보고 반영)
"""


class AmError(Exception):
    pass


def _lib(root: str) -> Library:
    return Library(root)


def _rule(lib: Library, nid: str) -> dict:
    n = lib.nodes.get(nid)
    if not n:
        near = [k for k in sorted(lib.nodes) if nid in k][:5]
        raise AmError(f"노드가 없습니다: {nid}" + (f" (비슷한 id: {', '.join(near)})" if near else ""))
    if n["type"] != "rule":
        raise AmError(f"규칙 노드가 아닙니다: {nid} ({n['type']})")
    return n


# ------------------------------------------------------------------ 되돌리기 스택
def _snapshot(root: str, rels: list[str], line: str, actor: str) -> None:
    files = {}
    for rel in rels:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                files[rel] = f.read()
        else:
            files[rel] = None
    os.makedirs(os.path.dirname(os.path.join(root, UNDO)), exist_ok=True)
    with open(os.path.join(root, UNDO), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "line": line, "actor": actor, "files": files},
                           ensure_ascii=False) + "\n")


def _pop_undo(root: str) -> dict | None:
    p = os.path.join(root, UNDO)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    if not lines:
        return None
    last = json.loads(lines[-1])
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(l + "\n" for l in lines[:-1]))
    return last


# ------------------------------------------------------------------ 쓰기 도우미
def _write(lib: Library, line: str, actor: str, rels: list[str], fn) -> None:
    import store
    store.backup_sync(lib, f"콘솔 전: {line}")
    _snapshot(lib.root, rels, line, actor)
    fn()


def _mutate(lib: Library, nid: str, mut) -> None:
    import folder_requests as freq
    freq._rewrite_frontmatter(lib.abspath(lib.nodes[nid]["path"]), mut)


def _list_arg(v) -> list[str]:
    if v is None:
        return []
    return [v] if isinstance(v, str) else [str(x) for x in v]


def _check_parts(lib: Library, parts: list[str]) -> None:
    for p in parts:
        n = lib.nodes.get(p)
        if not n:
            raise AmError(f"노드가 없습니다: {p}")
        if not (n["type"] == "skill" or (n["type"] == "rule" and n["kind"] == "principle")):
            raise AmError(f"`use`로는 원칙이나 스킬만 붙입니다: {p} ({n['type']}/{n['kind']}). 계층은 `am inherit`")


# ------------------------------------------------------------------ 명령
def run(root: str, argv: list[str], actor: str = "cli") -> dict:
    """명령 하나를 실행한다. {code, output, changed: [노드 id]}"""
    buf = io.StringIO()
    changed: list[str] = []
    code = 0
    line = " ".join(shlex.quote(a) for a in argv)
    try:
        with redirect_stdout(buf):
            changed = _dispatch(root, argv, actor, line) or []
    except AmError as e:
        buf.write(f"오류: {e}\n")
        code = 2
    except CycleError as e:
        buf.write(f"오류: 순환 상속이 생깁니다 — {e}\n")
        code = 2
    if changed:
        import store
        store.append_history(root, {"kind": "console", "actor": actor, "line": line, "changed": changed})
    return {"code": code, "output": buf.getvalue(), "changed": changed}


def run_line(root: str, line: str, actor: str = "user") -> dict:
    line = line.strip()
    if line.startswith("am "):
        line = line[3:]
    elif line == "am":
        line = "help"
    try:
        argv = shlex.split(line)
    except ValueError as e:
        return {"code": 2, "output": f"오류: 따옴표가 맞지 않습니다 ({e})\n", "changed": []}
    return run(root, argv, actor)


def _dispatch(root: str, argv: list[str], actor: str, line: str) -> list[str] | None:
    if not argv or argv[0] in ("help", "-h", "--help"):
        print(HELP)
        return None
    cmd, args = argv[0], argv[1:]
    lib = _lib(root)

    if cmd == "ls":
        what = args[0] if args else "all"
        groups = {"sets": lambda n: n["type"] == "rule" and n["kind"] == "set",
                  "principles": lambda n: n["type"] == "rule" and n["kind"] == "principle",
                  "skills": lambda n: n["type"] == "skill",
                  "refs": lambda n: n["type"] not in ("rule", "skill", "ai") and n["kind"] != "unregistered"}
        if what not in groups and what != "all":
            raise AmError("ls 다음에는 sets | principles | skills | refs | all")
        for g, test in groups.items():
            if what not in ("all", g):
                continue
            rows = sorted((n for n in lib.nodes.values() if test(n)), key=lambda n: n["id"])
            print(f"[{g}] {len(rows)}개")
            for n in rows:
                extra = ""
                if n["type"] == "rule":
                    inh = [p for p in n["inherits"] if not (p == "_defaults" and n.get("implicit_inherits"))]
                    extra = (f"  ← {', '.join(inh)}" if inh else "") + (f"  + {', '.join(n['uses'])}" if n.get("uses") else "")
                print(f"  {n['id']:<28} {n['title']}{extra}")
        return None

    if cmd == "show":
        if not args:
            raise AmError("am show <id>")
        n = lib.nodes.get(args[0])
        if not n:
            raise AmError(f"노드가 없습니다: {args[0]}")
        print(f"{n['title']}  ({n['id']} · {n['type']}/{n['kind']})")
        print(f"파일: {n.get('path') or n.get('file')}")
        if n["type"] == "rule":
            if n["id"] in lib.cyclic:
                raise AmError("순환 상속 때문에 조립할 수 없습니다")
            a = lib.assemble(n["id"])
            inh = [p for p in n["inherits"] if not (p == "_defaults" and n.get("implicit_inherits"))]
            print(f"상속: {', '.join(inh) or '(앱 기본값만)'}")
            if n.get("uses"):
                print(f"부품: {', '.join(n['uses'])}")
            print(f"조립 순서: {' → '.join(a['chain'])}")
            print("최종 규칙:")
            for e in a["entries"]:
                v = e["value"] if isinstance(e["value"], str) else json.dumps(e["value"], ensure_ascii=False)
                src = "" if e["source"] == n["id"] else f"  ← {e['source']}"
                print(f"  {e['key']:<18} {v[:90]}{'…' if len(v) > 90 else ''}{src}")
            if a.get("skills"):
                print("함께 쓰는 스킬: " + ", ".join(k["id"] for k in a["skills"]))
            users = lib.users(n["id"])
            if users:
                print("이 노드를 붙인 곳: " + ", ".join(users))
            kids = [m["id"] for m in lib.rule_nodes() if n["id"] in m["inherits"]]
            if kids:
                print("상속하는 노드: " + ", ".join(sorted(kids)))
        else:
            for k in ("summary", "description", "spec"):
                if n.get(k):
                    print(f"{k}: {str(n[k]).strip()[:400]}")
            if n.get("users") is None and n["type"] == "skill":
                print("붙인 곳: " + (", ".join(lib.users(n["id"])) or "없음"))
        return None

    if cmd == "find":
        import search
        q = " ".join(args)
        if not q:
            raise AmError("am find <검색어>")
        for r in search.search(lib, q, limit=10):
            print(f"  {r['id']:<28} {r['title']}  ({r['kind']}, {r['score']})")
        sets = __import__("standards").find(lib, q)
        if sets:
            print("세트 후보: " + ", ".join(f"{s['id']}({s['score']})" for s in sets))
        return None

    if cmd == "standard":
        import standards
        set_id = None
        if "--set" in args:
            i = args.index("--set")
            set_id = args[i + 1] if i + 1 < len(args) else None
            args = args[:i] + args[i + 2:]
        r = standards.standard(lib, " ".join(args), set_id)
        print(r["text"])
        return None

    if cmd == "inherit":
        if not args:
            raise AmError("am inherit <id> [<부모> ...]")
        n = _rule(lib, args[0])
        parents = args[1:]
        for p in parents:
            _rule(lib, p)
        if n["kind"] == "principle" and parents and any(lib.nodes[p]["kind"] != "defaults" for p in parents):
            print("주의: 원칙은 부품이라 보통 상속하지 않습니다. 세트에 `am use`로 붙이는 편이 맞습니다.")
        def mut(fm):
            if parents:
                fm["inherits"] = parents
            else:
                fm.pop("inherits", None)
        _write(lib, line, actor, [n["path"]], lambda: _mutate(lib, n["id"], mut))
        try:
            _lib(root).sources(n["id"])
        except CycleError:
            undo(root)
            raise AmError("순환 상속이 생겨 되돌렸습니다")
        print(f"{n['id']} 상속: {', '.join(parents) or '(앱 기본값만)'}")
        return [n["id"]]

    if cmd in ("use", "unuse"):
        if len(args) < 2:
            raise AmError(f"am {cmd} <id> <원칙·스킬> ...")
        n = _rule(lib, args[0])
        parts = args[1:]
        if cmd == "use":
            _check_parts(lib, parts)
        cur = list(n.get("uses") or [])
        new = cur + [p for p in parts if p not in cur] if cmd == "use" else [u for u in cur if u not in parts]
        if new == cur:
            print("바뀐 것이 없습니다")
            return None
        def mut(fm):
            if new:
                fm["uses"] = new
            else:
                fm.pop("uses", None)
        _write(lib, line, actor, [n["path"]], lambda: _mutate(lib, n["id"], mut))
        print(f"{n['id']} 부품: {', '.join(new) or '(없음)'}")
        return [n["id"]] + [p for p in parts if p in lib.nodes]

    if cmd == "set":
        if len(args) < 3:
            raise AmError("am set <id> <키> <값...>")
        n = _rule(lib, args[0])
        key, value = args[1], " ".join(args[2:])
        ref = lib._ref_id(value)
        if ref and ref not in lib.nodes:
            raise AmError(f"없는 레퍼런스입니다: {value}")
        if key not in lib.keys:
            print(f"참고: `{key}`는 키 어휘에 없는 사용자 정의 키입니다")
        import folder_requests as freq
        _write(lib, line, actor, [n["path"]], lambda: freq._set_rule(lib, n["id"], key, value))
        print(f"{n['id']}.{key} = {value}")
        return [n["id"]]

    if cmd == "unset":
        if len(args) != 2:
            raise AmError("am unset <id> <키>")
        n = _rule(lib, args[0])
        if args[1] not in n["rules"]:
            raise AmError(f"{n['id']}에 `{args[1]}` 키가 없습니다")
        def mut(fm):
            (fm.get("rules") or {}).pop(args[1], None)
        _write(lib, line, actor, [n["path"]], lambda: _mutate(lib, n["id"], mut))
        print(f"{n['id']}.{args[1]} 지움")
        return [n["id"]]

    if cmd == "rename":
        if len(args) < 2:
            raise AmError("am rename <id> <새 제목...>")
        n = lib.nodes.get(args[0])
        if not n or n["type"] not in ("rule",):
            raise AmError("규칙 노드만 이름을 바꿀 수 있습니다 (레퍼런스는 앱에서)")
        title = " ".join(args[1:])
        _write(lib, line, actor, [n["path"]], lambda: _mutate(lib, n["id"], lambda fm: fm.__setitem__("title", title)))
        print(f"{n['id']} 제목: {title}")
        return [n["id"]]

    if cmd == "new":
        if len(args) < 3 or args[0] not in ("principle", "set"):
            raise AmError("am new principle|set <id> <제목...> [--inherits a,b]")
        kind, nid = args[0], args[1]
        rest = args[2:]
        inh: list[str] = []
        if "--inherits" in rest:
            i = rest.index("--inherits")
            inh = [x for x in (rest[i + 1] if i + 1 < len(rest) else "").split(",") if x]
            rest = rest[:i] + rest[i + 2:]
        if not ID_RE.match(nid):
            raise AmError("id는 영문 소문자·숫자·하이픈")
        if nid in lib.nodes:
            raise AmError(f"이미 있는 id입니다: {nid}")
        for p in inh:
            _rule(lib, p)
        if kind == "set" and not inh and "global" in lib.nodes:
            inh = ["global"]
        rel = f"{'principles' if kind == 'principle' else 'sets'}/{nid}.md"
        fm = {"id": nid, "type": "rule", "kind": kind, "title": " ".join(rest)}
        if inh and kind == "set":
            fm["inherits"] = inh
        fm["rules"] = {}

        def make():
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write("---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n콘솔에서 만든 노드입니다.\n")
        _write(lib, line, actor, [rel], make)
        print(f"만들었습니다: {rel}" + (f" (상속: {', '.join(inh)})" if inh and kind == "set" else ""))
        return [nid]

    if cmd == "undo":
        res = undo(root)
        if not res:
            print("되돌릴 콘솔 변경이 없습니다")
            return None
        print(f"되돌렸습니다: {res['line']}  ({', '.join(res['files'])})")
        return res["ids"]

    if cmd == "tidy":
        if not args:
            raise AmError("am tidy <id>")
        _rule(lib, args[0])
        import tidy
        b = tidy.build(lib, args[0])
        print(f"하위 정리 요청을 만들었습니다: {b['request']}")
        print("앱에서 이 노드의 '하위 정리 (AI)'를 열면 이어서 진행합니다. 에이전트라면 아래 파일을 읽고 그대로 수행하세요:")
        print(f"  inbox/{b['request']}/prompt.md")
        for d in b["diagnostics"]:
            print(f"  - {d['message']}")
        return None

    raise AmError(f"모르는 명령입니다: {cmd}  (am help)")


def undo(root: str) -> dict | None:
    last = _pop_undo(root)
    if not last:
        return None
    import store
    store.backup_sync(_lib(root), f"콘솔 되돌리기 전: {last['line']}")
    for rel, text in last["files"].items():
        p = os.path.join(root, rel)
        if text is None:
            if os.path.exists(p):
                os.remove(p)
        else:
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
    lib = _lib(root)
    ids = sorted({n["id"] for n in lib.nodes.values() if n.get("path") in last["files"]})
    return {"line": last["line"], "files": sorted(last["files"]), "ids": ids}
