"""스킬 연동 (기획서 v3.6, Q77-A).

스킬 = `<이름>/SKILL.md` + 부속 파일 폴더. Claude Code(`.claude/skills`)와 Codex·Gemini CLI 등(`.agents/skills`)이 읽는
공개 형식이다. Assets Manager는 그래프(원본)를 관리하고, 스킬은 그 **파생물(P1)** 로 내보낸다.

1. 도구 스킬 `assets-manager`: 앱에 들어 있는 `app/skills/assets-manager/`를 라이브러리 안에 설치한다(시작할 때).
   로컬 AI 실행 때 도구가 라이브러리 다루는 법(plan.yaml 형식, 정리 ops, 메타 규격)을 이 스킬에서 읽는다.
2. 세트 스킬: 세트의 최종 규칙(상속·원칙 펼침) + 진행 방식 + 자가 점검을 `SKILL.md`로, 첨부 레퍼런스는 `references/`로.
   설치 위치: 라이브러리 안(이 라이브러리에서 실행하는 도구만) 또는 개인 폴더(이 컴퓨터의 모든 프로젝트).
   내보낸 기록은 `.assets/skills-manifest.json`. 누가 SKILL.md를 직접 고쳤으면 덮어쓰기 전에 멈춘다.
3. 설치된 스킬 읽기: 라이브러리·개인 폴더의 스킬을 그래프 노드(`installed_skill`)로 보여 준다(읽기 전용).

프런트매터는 공개 형식에서 통하는 키만 쓴다: name, description, metadata (claude.ai 업로드도 이 키만 허용).
"""
from __future__ import annotations

import difflib
import hashlib
import sys
import json
import os
import re
import shutil
import time

import yaml

from engine import Library, CycleError, FRONTMATTER

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL_SRC = os.path.join(APP_DIR, "skills", "assets-manager")
EXTRACT = os.path.join(APP_DIR, "core", "extract.py")
MANIFEST = os.path.join(".assets", "skills-manifest.json")
AGENTS = {  # 키: 설치 폴더(범위 기준 상대), 라벨
    "claude": (os.path.join(".claude", "skills"), "Claude Code"),
    "agents": (os.path.join(".agents", "skills"), "Codex · Gemini CLI 등"),
}
SCOPES = {"library": "라이브러리 안", "personal": "개인 폴더"}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _sha(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha1(data).hexdigest()[:12]


def _base(root: str, scope: str) -> str:
    if scope == "library":
        return root
    if scope == "personal":
        return os.path.expanduser("~")
    raise ValueError(f"알 수 없는 범위: {scope}")


def skill_name(set_id: str) -> str:
    name = re.sub(r"[^a-z0-9-]+", "-", set_id.lower()).strip("-")[:64].strip("-")
    if not NAME_RE.match(name or ""):
        raise ValueError(f"스킬 이름으로 바꿀 수 없는 id입니다: {set_id}")
    return name


# ------------------------------------------------------------------ 기록
def load_manifest(root: str) -> dict:
    try:
        with open(os.path.join(root, MANIFEST), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_manifest(root: str, m: dict) -> None:
    os.makedirs(os.path.dirname(os.path.join(root, MANIFEST)), exist_ok=True)
    with open(os.path.join(root, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _ensure_gitignore(root: str, lines: list[str]) -> None:
    gi = os.path.join(root, ".gitignore")
    have = []
    if os.path.exists(gi):
        with open(gi, encoding="utf-8") as f:
            have = f.read().splitlines()
    missing = [l for l in lines if l not in have]
    if missing:
        with open(gi, "a", encoding="utf-8") as f:
            f.write(("\n" if have and have[-1] else "") + "\n".join(missing) + "\n")


# ------------------------------------------------------------------ 1. 도구 스킬
CLI = os.path.join(APP_DIR, "core", "cli.py")


def tool_files(root: str) -> dict[str, bytes]:
    """도구 스킬 파일. SKILL.md의 {PY} {CLI} {LIB}를 이 컴퓨터의 실제 경로로 채운다(개인 폴더에서도 부를 수 있게)."""
    files: dict[str, bytes] = {}
    for dp, _, fns in os.walk(TOOL_SRC):
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            with open(p, "rb") as f:
                data = f.read()
            if fn == "SKILL.md":
                py = sys.executable or "python"
                data = (data.decode("utf-8").replace("{PY}", f'"{py}"' if " " in py else py)
                        .replace("{CLI}", CLI).replace("{LIB}", os.path.abspath(root)).encode("utf-8"))
            files[os.path.relpath(p, TOOL_SRC).replace("\\", "/")] = data
    with open(EXTRACT, "rb") as f:
        files["scripts/extract.py"] = f.read()
    return files


def install_tool_skill(root: str) -> list[str]:
    """라이브러리 안 `.claude/skills/assets-manager`, `.agents/skills/assets-manager`를 앱 번들과 같게 맞춘다."""
    if not os.path.isdir(TOOL_SRC):
        return []
    return _write_tool(root, [os.path.join(root, sub, "assets-manager") for sub, _ in AGENTS.values()], gitignore=True)


def _write_tool(root: str, dests: list[str], gitignore: bool = False) -> list[str]:
    files = tool_files(root)
    written = []
    for dest in dests:
        for rel, data in files.items():
            p = os.path.join(dest, *rel.split("/"))
            try:
                with open(p, "rb") as f:
                    if f.read() == data:
                        continue
            except OSError:
                pass
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
            written.append(os.path.relpath(p, root).replace("\\", "/") if p.startswith(os.path.abspath(root)) else p)
    if gitignore:
        _ensure_gitignore(root, [".claude/skills/assets-manager/", ".agents/skills/assets-manager/"])
    return written


# ------------------------------------------------------------------ v3.7 AI가 Assets Manager를 부르게 하기
RULE_START, RULE_END = "<!-- assets-manager:start -->", "<!-- assets-manager:end -->"
CALLER = {  # 항목: (종류, 경로(~ 기준), 라벨)
    "skill_claude": ("skill", os.path.join(".claude", "skills", "assets-manager"), "개인 스킬 · Claude Code"),
    "skill_agents": ("skill", os.path.join(".agents", "skills", "assets-manager"), "개인 스킬 · Codex·Gemini CLI 등"),
    "rule_claude": ("rule", os.path.join(".claude", "CLAUDE.md"), "전역 지침 한 줄 · Claude Code (~/.claude/CLAUDE.md)"),
    "rule_codex": ("rule", os.path.join(".codex", "AGENTS.md"), "전역 지침 한 줄 · Codex (~/.codex/AGENTS.md)"),
}


def _rule_block(root: str) -> str:
    py = sys.executable or "python"
    py = f'"{py}"' if " " in py else py
    return (f"{RULE_START}\n## Assets Manager (내 작업 표준)\n"
            "작업을 맡으면 결과물을 만들기 전에 `assets-manager` 스킬로 이 작업에 맞는 내 작업 표준을 확인하고 따른다. "
            f"명령: `{py} \"{CLI}\" standard \"<작업 한 줄>\"`\n{RULE_END}\n")


def caller_status(root: str) -> dict:
    home = os.path.expanduser("~")
    items = []
    for k, (kind, rel, label) in CALLER.items():
        p = os.path.join(home, rel)
        if kind == "skill":
            sk = os.path.join(p, "SKILL.md")
            cur = None
            if os.path.exists(sk):
                with open(sk, "rb") as f:
                    cur = f.read()
            installed = cur is not None
            ours = installed and b"generator: Assets Manager" in cur
            fresh = ours and cur == tool_files(root)["SKILL.md"]
        else:
            text = ""
            if os.path.exists(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            installed = ours = RULE_START in text
            fresh = ours and _rule_block(root).strip() in text
        items.append({"id": k, "kind": kind, "label": label, "path": p, "installed": installed, "ours": ours,
                      "fresh": fresh})
    py = sys.executable or "python"
    return {"items": items, "command": f'{py} "{CLI}" standard "<작업 한 줄>"'}


def caller_apply(root: str, item: str, action: str) -> dict:
    kind, rel, _ = CALLER[item]
    p = os.path.join(os.path.expanduser("~"), rel)
    if kind == "skill":
        sk = os.path.join(p, "SKILL.md")
        if os.path.exists(sk):
            with open(sk, "rb") as f:
                if b"generator: Assets Manager" not in f.read():
                    raise ValueError(f"같은 이름의 다른 스킬이 있습니다: {p}")
        if action == "install":
            _write_tool(root, [p])
        else:
            files = tool_files(root)
            for rel_f in files:
                try:
                    os.remove(os.path.join(p, *rel_f.split("/")))
                except OSError:
                    pass
            for dp, _, _ in sorted(os.walk(p), key=lambda x: -len(x[0])):
                try:
                    os.rmdir(dp)
                except OSError:
                    pass
    else:
        text = ""
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
            bak = p + ".assets-manager.bak"
            if not os.path.exists(bak):  # 처음 고치기 전에 한 번 보관
                shutil.copyfile(p, bak)
        text = re.sub(re.escape(RULE_START) + r".*?" + re.escape(RULE_END) + r"\n?", "", text, flags=re.S)
        if action == "install":
            text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + _rule_block(root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    return caller_status(root)


# ------------------------------------------------------------------ v3.7 스킬을 라이브러리로 가져오기 (Q78-A)
DISABLED = "SKILL.md.disabled-by-assets-manager"


def import_skill(lib: Library, src_dir: str, attach_to: str | None = None, disable_original: bool = True) -> dict:
    """설치된 스킬 폴더를 skills/<이름>/로 통째로 복사한다. 원하면 원래 스킬을 꺼서 지침이 두 번 들어가지 않게 한다."""
    import store
    import folder_requests as freq
    src_dir = os.path.abspath(src_dir)
    sk = os.path.join(src_dir, "SKILL.md")
    if not os.path.isfile(sk):
        raise FileNotFoundError(f"SKILL.md가 없습니다: {src_dir}")
    with open(sk, encoding="utf-8", errors="replace") as f:
        m = FRONTMATTER.match(f.read().lstrip("\ufeff"))
    fm = (yaml.safe_load(m.group(1)) or {}) if m else {}
    base = skill_name(str((fm or {}).get("name") or os.path.basename(src_dir)))
    name, i = base, 2
    while os.path.exists(os.path.join(lib.root, "skills", name)):
        name, i = f"{base}-{i}", i + 1
    dest = os.path.join(lib.root, "skills", name)
    store.backup_sync(lib)
    shutil.copytree(src_dir, dest, ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules", DISABLED))
    with open(sk, "rb") as f:
        sha = _sha(f.read())
    inside = os.path.normcase(src_dir).startswith(os.path.normcase(os.path.abspath(lib.root)) + os.sep)
    disabled = False
    if disable_original and not inside:
        os.replace(sk, os.path.join(src_dir, DISABLED))  # 폴더는 그대로, 도구가 읽는 SKILL.md만 이름을 바꾼다
        disabled = True
    with open(os.path.join(dest, ".assets-manager.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump({"origin": src_dir, "imported": time.strftime("%Y-%m-%dT%H:%M:%S"), "origin_sha": sha,
                        "disabled_original": disabled}, f, allow_unicode=True, sort_keys=False)
    lib2 = Library(lib.root)
    nid = next(n["id"] for n in lib2.nodes.values() if n.get("type") == "skill" and n.get("dir") == f"skills/{name}")
    if attach_to:
        attach(lib2, nid, attach_to, backup=False)
    return {"id": nid, "path": f"skills/{name}", "disabled_original": disabled, "origin": src_dir}


def attach(lib: Library, skill_id: str, node_id: str, detach: bool = False, backup: bool = True) -> dict:
    import store
    import folder_requests as freq
    if lib.nodes.get(skill_id, {}).get("type") != "skill":
        raise KeyError(f"스킬 노드가 아닙니다: {skill_id}")
    n = lib.nodes.get(node_id)
    if not n or n["type"] != "rule":
        raise KeyError(f"규칙 노드가 아닙니다: {node_id}")
    if backup:
        store.backup_sync(lib)

    def mut(fm):
        use = fm.get("uses") or []
        use = [use] if isinstance(use, str) else list(use)
        if detach:
            use = [u for u in use if u != skill_id]
        elif skill_id not in use:
            use.append(skill_id)
        if use:
            fm["uses"] = use
        else:
            fm.pop("uses", None)
    freq._rewrite_frontmatter(lib.abspath(n["path"]), mut)
    return {"node": node_id, "skill": skill_id, "detached": detach}


def restore_original(lib: Library, skill_id: str) -> dict:
    n = lib.nodes.get(skill_id)
    if not n or n["type"] != "skill":
        raise KeyError(f"스킬 노드가 아닙니다: {skill_id}")
    origin = (n.get("origin") or {}).get("origin")
    if not origin:
        raise ValueError("가져온 원래 위치 기록이 없습니다")
    dis, sk = os.path.join(origin, DISABLED), os.path.join(origin, "SKILL.md")
    if os.path.exists(dis) and not os.path.exists(sk):
        os.replace(dis, sk)
    op = os.path.join(lib.abspath(n["dir"]), ".assets-manager.yaml")
    meta = dict(n.get("origin") or {}, disabled_original=False)
    with open(op, "w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, allow_unicode=True, sort_keys=False)
    return {"restored": sk}


# ------------------------------------------------------------------ 2. 세트 스킬
def _raw_frontmatter(lib: Library, nid: str) -> dict:
    with open(lib.abspath(lib.nodes[nid]["path"]), encoding="utf-8") as f:
        m = FRONTMATTER.match(f.read().lstrip("﻿"))
    return (yaml.safe_load(m.group(1)) or {}) if m else {}


def default_description(lib: Library, nid: str) -> str:
    n = lib.nodes[nid]
    first = next((l.strip() for l in (n.get("body") or "").splitlines() if l.strip() and not l.startswith(("-", "#"))), "")
    first = re.sub(r"`", "", first)[:300]
    d = f"'{n['title']}' 작업 표준(규칙·레퍼런스·자가 점검). 사용자가 {n['title']} 작업을 요청하거나 이 표준대로 해 달라고 할 때 사용합니다."
    return (d + (" " + first if first else ""))[:1000]


def compile_set(lib: Library, nid: str) -> dict:
    """세트 → 스킬 폴더 내용. 같은 라이브러리 상태면 같은 결과(결정적)."""
    n = lib.nodes.get(nid)
    if not n or n["type"] != "rule" or n["kind"] == "defaults":
        raise KeyError(f"규칙 노드가 아닙니다: {nid}")
    if nid in lib.cyclic:
        raise CycleError(f"순환 상속: {nid}")
    fm = _raw_frontmatter(lib, nid)
    name = skill_name(nid)
    asm = lib.assemble(nid)
    warnings = []
    mode = {}
    for e in asm["entries"]:  # P8: 사내 자료는 파일로 내보내지 않고 글로만
        ref = lib._ref_id(e.get("value"))
        if ref and lib.nodes.get(ref, {}).get("sensitivity", "public") != "public":
            mode[e["key"]] = "text"
            warnings.append(f"`{e['key']}`의 레퍼런스 '{lib.nodes[ref]['title']}'은(는) 내부 자료라 파일 대신 글로 넣었습니다")
    prompt, attachments = lib.render(asm, mode=mode)
    files: dict[str, bytes] = {}
    for a in attachments:
        if a.get("abs") and os.path.isfile(a["abs"]):
            with open(a["abs"], "rb") as f:
                files["references/" + a["file"]] = f.read()
    desc = str(fm.get("skill_description") or default_description(lib, nid))[:1024]
    head = [f"# {n['title']}", "",
            "이 스킬은 Assets Manager가 그래프의 세트에서 만든 작업 표준입니다. 아래 지침을 그대로 따르세요."]
    if files:
        head.append("첨부 레퍼런스 [n]은 이 스킬 폴더의 `references/` 안에 있습니다. 필요할 때 열어 보세요: "
                    + ", ".join(f"`references/{os.path.basename(k)}`" for k in files) + ".")
    head += ["이 파일은 자동 생성됩니다. 고칠 것은 Assets Manager의 세트에서 고친 뒤 다시 내보내세요.", "", "---", ""]
    body = "\n".join(head) + prompt.split("\n", 1)[1].lstrip("\n") if prompt.startswith("# ") else "\n".join(head) + prompt
    digest = _sha(body + "".join(sorted(f"{k}:{_sha(v)}" for k, v in files.items())))
    front = {"name": name, "description": desc,
             "metadata": {"generator": "Assets Manager", "set": nid, "hash": digest}}
    skill_md = "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False, width=10 ** 6) + "---\n" + body
    if len(skill_md.splitlines()) > 500:
        warnings.append("SKILL.md가 500줄을 넘습니다. 규칙을 원칙으로 나누거나 줄이는 편이 좋습니다")
    files["SKILL.md"] = skill_md.encode("utf-8")
    return {"set": nid, "name": name, "description": desc, "hash": digest, "files": files,
            "skill_md": skill_md, "warnings": warnings}


def _dests(lib: Library, name: str, scope: str, agents: list[str]) -> list[tuple[str, str]]:
    base = _base(lib.root, scope)
    return [(a, os.path.join(base, AGENTS[a][0], name)) for a in agents if a in AGENTS]


def _state(dest: str, entry: dict | None) -> str:
    sk = os.path.join(dest, "SKILL.md")
    if not os.path.exists(sk):
        return "absent"
    if not entry:
        return "foreign"
    with open(sk, "rb") as f:
        return "edited" if _sha(f.read()) != entry.get("skill_sha") else "ok"


def status(lib: Library, nid: str) -> dict:
    comp = compile_set(lib, nid)
    man = load_manifest(lib.root)
    rows = []
    for scope in SCOPES:
        for a, dest in _dests(lib, comp["name"], scope, list(AGENTS)):
            e = man.get(_key(dest))
            st = _state(dest, e)
            rows.append({"scope": scope, "scope_label": SCOPES[scope], "agent": a, "agent_label": AGENTS[a][1],
                         "path": dest, "state": st, "stale": bool(e and st == "ok" and e.get("hash") != comp["hash"]),
                         "exported": e.get("exported") if e else None})
    return {"set": nid, "name": comp["name"], "description": comp["description"], "hash": comp["hash"],
            "warnings": comp["warnings"], "skill_md": comp["skill_md"],
            "files": sorted(k for k in comp["files"] if k != "SKILL.md"), "installs": rows}


def export(lib: Library, nid: str, scope: str, agents: list[str], overwrite: bool = False) -> dict:
    comp = compile_set(lib, nid)
    man = load_manifest(lib.root)
    targets = _dests(lib, comp["name"], scope, agents)
    if not targets:
        raise ValueError("설치할 도구를 하나 이상 고르세요")
    conflicts = []
    for a, dest in targets:
        st = _state(dest, man.get(_key(dest)))
        if st in ("foreign", "edited") and not overwrite:
            with open(os.path.join(dest, "SKILL.md"), encoding="utf-8", errors="replace") as f:
                cur = f.read()
            conflicts.append({"agent": a, "path": dest, "state": st,
                              "diff": "".join(difflib.unified_diff(cur.splitlines(True), comp["skill_md"].splitlines(True),
                                                                   "지금 파일", "내보낼 내용", n=1))[:8000]})
    if conflicts:
        return {"ok": False, "conflicts": conflicts, "name": comp["name"]}
    done = []
    for a, dest in targets:
        old = man.get(_key(dest)) or {}
        for rel in old.get("files", []):  # 지난번에 우리가 쓴 파일만 정리
            if rel not in comp["files"]:
                try:
                    os.remove(os.path.join(dest, *rel.split("/")))
                except OSError:
                    pass
        for rel, data in comp["files"].items():
            p = os.path.join(dest, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        man[_key(dest)] = {"set": nid, "name": comp["name"], "scope": scope, "agent": a, "path": dest,
                           "hash": comp["hash"], "skill_sha": _sha(comp["files"]["SKILL.md"]),
                           "files": sorted(comp["files"]), "exported": time.strftime("%Y-%m-%dT%H:%M:%S")}
        done.append(dest)
    save_manifest(lib.root, man)
    if scope == "library":
        _ensure_gitignore(lib.root, [f"{AGENTS[a][0].replace(os.sep, '/')}/{comp['name']}/" for a, _ in targets])
    return {"ok": True, "written": done, "name": comp["name"], "warnings": comp["warnings"]}


def remove(lib: Library, nid: str, scope: str, agents: list[str], force: bool = False) -> dict:
    man = load_manifest(lib.root)
    name = skill_name(nid)
    removed, refused = [], []
    for a, dest in _dests(lib, name, scope, agents):
        e = man.get(_key(dest))
        if not e:
            continue
        if _state(dest, e) == "edited" and not force:
            refused.append(dest)
            continue
        for rel in e.get("files", []):
            try:
                os.remove(os.path.join(dest, *rel.split("/")))
            except OSError:
                pass
        for dp, _, _ in sorted(os.walk(dest), key=lambda x: -len(x[0])):  # 비면 폴더도 지움
            try:
                os.rmdir(dp)
            except OSError:
                pass
        man.pop(_key(dest), None)
        removed.append(dest)
    save_manifest(lib.root, man)
    return {"removed": removed, "refused": refused}


# ------------------------------------------------------------------ 3. 설치된 스킬 읽기
def _locations(root: str) -> list[tuple[str, str, str]]:
    out = []
    for scope in SCOPES:
        for a, (sub, _) in AGENTS.items():
            out.append((scope, a, os.path.join(_base(root, scope), sub)))
    out.append(("synced", "claude", os.path.join(os.path.expanduser("~"), ".claude", "skills", "synced")))
    return out


def scan(lib: Library) -> list[dict]:
    """설치된 스킬 목록. 같은 범위·이름이면 Claude용과 .agents용을 한 노드로 묶는다."""
    man = load_manifest(lib.root)
    found: dict[tuple, dict] = {}
    for scope, agent, d in _locations(lib.root):
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            sk = os.path.join(d, name, "SKILL.md")
            if name == "synced" or not os.path.isfile(sk):
                continue
            try:
                with open(sk, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                m = FRONTMATTER.match(text.lstrip("﻿"))
                fm = (yaml.safe_load(m.group(1)) or {}) if m else {}
            except (OSError, yaml.YAMLError):
                fm = {}
            if not isinstance(fm, dict):
                fm = {}
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            k = (scope, str(fm.get("name") or name))
            e = found.setdefault(k, {"id": f"skill:{scope}:{k[1]}", "name": k[1], "scope": scope,
                                     "scope_label": {"library": "라이브러리 안", "personal": "개인 폴더",
                                                     "synced": "claude.ai 동기화"}[scope],
                                     "description": str(fm.get("description") or "")[:1024],
                                     "set": meta.get("set"), "tool": meta.get("kind") == "tool" and meta.get("generator") == "Assets Manager",
                                     "locations": [], "managed": False, "edited": False, "stale": False})
            entry = man.get(_key(os.path.join(d, name)))
            st = _state(os.path.join(d, name), entry) if entry else None
            files = sum(len(fs) for _, _, fs in os.walk(os.path.join(d, name)))
            e["locations"].append({"agent": agent, "agent_label": AGENTS[agent][1], "path": os.path.join(d, name),
                                   "files": files, "state": st})
            if entry:
                e["managed"] = True
                e["set"] = e.get("set") or entry.get("set")
                e["edited"] = e["edited"] or st == "edited"
                e["hash"] = entry.get("hash")
    out = sorted(found.values(), key=lambda x: (x["scope"], x["name"]))
    for e in out:  # 세트가 바뀌어 다시 내보내야 하는가
        if e["managed"] and e.get("set") in lib.nodes:
            try:
                e["stale"] = compile_set(lib, e["set"])["hash"] != e.get("hash")
            except (KeyError, CycleError, ValueError):
                pass
    return out


def graph_part(lib: Library, installed: list[dict] | None = None) -> dict:
    installed = scan(lib) if installed is None else installed
    nodes, edges = [], []
    for s in installed:
        nodes.append({"id": s["id"], "label": s["name"], "type": "installed_skill", "kind": s["scope"],
                      "managed": s["managed"], "stale": s["stale"], "edited": s["edited"], "tool": s["tool"]})
        if s.get("set") in lib.nodes:
            edges.append({"id": f"skl:{s['set']}:{s['id']}", "source": s["set"], "target": s["id"], "kind": "info",
                          "label": "스킬"})
    return {"nodes": nodes, "edges": edges}
