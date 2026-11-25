"""기록과 되돌리기 (기획서 v3 7장): 드롭 히스토리, 버전 보관, 휴지통, Git 자동 커밋, 가져오기, 기본값 업데이트."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import zipfile

import yaml

from engine import BUNDLED_DEFAULTS, FRONTMATTER, Library

HISTORY = "history"
MAX_VERSIONS = 20
MAX_VERSION_BYTES = 5 * 1024 * 1024
# 라이브러리 데이터만 기록한다. 앱 코드(app/)와 캐시, 자동 생성물은 제외.
GITIGNORE_LINES = ["app/", ".assets/", "inbox/", "history/versions/", "history/trash/", "__pycache__/",
                   "graph.json", "index.md"]
TYPE_FOLDER = {"image": "image", "web": "web", "document": "document", "writing": "writing", "skill": "skills"}
EXT_TYPE = {".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
            ".html": "web", ".htm": "web", ".css": "web", ".pdf": "document",
            ".txt": "writing", ".md": "writing"}


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _sha(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ 드롭 히스토리 (Q34~Q35)
def append_history(root: str, rec: dict) -> dict:
    rec = {"ts": now(), **rec}
    path = os.path.join(root, HISTORY, "drops.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_history(root: str) -> list[dict]:
    path = os.path.join(root, HISTORY, "drops.jsonl")
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def last_author_target(root: str, rel: str | None = None) -> str | None:
    for rec in reversed(read_history(root)):
        if rec.get("kind") == "author" and (rel is None or rel in (rec.get("files") or [])):
            return rec.get("target")
    return None


# ------------------------------------------------------------------ 버전 보관 + Git (Q39)
def tracked_files(lib: Library) -> list[str]:
    rels = set()
    for n in lib.nodes.values():
        if n.get("path"):
            rels.add(n["path"])
        if n.get("file"):
            rels.add(n["file"])
    for name in ("keys.yaml", "targets.yaml", "defaults.md"):
        rels.add(f"_defaults/{name}")
    return sorted(r for r in rels if os.path.isfile(lib.abspath(r)))


def _vdir(root: str, rel: str) -> str:
    return os.path.join(root, HISTORY, "versions", rel.replace("/", "__"))


def backup_sync(lib: Library, message: str | None = None, checked_at: str | None = None) -> dict:
    """폴더를 읽을 때마다 호출: 바뀐 파일의 새 내용을 보관하고, Git이 있으면 커밋한다.

    message: 앱 동작이 **방금 만든** 변경을 기록할 때만 준다(동작이 끝난 뒤 호출).
    동작 전에 그때까지의 변경을 쓸어 담는 호출은 message 없이 checked_at(예: "콘솔 명령 전 확인")만 준다.
    그 변경은 사람이나 다른 도구가 만든 것이므로 앱 동작 이름을 붙이지 않는다 (ISSUE-032·033)."""
    root = lib.root
    man_path = os.path.join(root, HISTORY, "versions", "manifest.json")
    try:
        with open(man_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError):
        manifest = {}
    changed = []
    baseline = not manifest  # 처음 실행: 이미 있던 파일은 "새로 추가됨"으로 표시하지 않음
    for rel in tracked_files(lib):
        path = lib.abspath(rel)
        if os.path.getsize(path) > MAX_VERSION_BYTES:
            continue
        sha = _sha(path)
        hist = manifest.get(rel, [])
        if hist and hist[-1]["sha"] == sha:
            continue
        vid = f"{_stamp()}_{sha[:8]}"
        d = _vdir(root, rel)
        os.makedirs(d, exist_ok=True)
        shutil.copy2(path, os.path.join(d, vid + os.path.splitext(rel)[1]))
        hist.append({"id": vid, "sha": sha, "ts": now(), "first": not hist, "baseline": baseline and not hist})
        for old in hist[:-MAX_VERSIONS]:
            try:
                os.remove(os.path.join(d, old["id"] + os.path.splitext(rel)[1]))
            except OSError:
                pass
        manifest[rel] = hist[-MAX_VERSIONS:]
        changed.append(rel)
    if changed or not os.path.exists(man_path):
        os.makedirs(os.path.dirname(man_path), exist_ok=True)
        with open(man_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
    git = git_commit(root, message, checked_at)
    return {"versioned": changed, "git": git}


def fresh_files(root: str, seconds: int = 24 * 3600) -> set[str]:
    """최근 처음 등장한 파일 (그래프의 "새로 추가됨" 표시용)."""
    try:
        with open(os.path.join(root, HISTORY, "versions", "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError):
        return set()
    limit = (dt.datetime.now() - dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S")
    return {rel for rel, hist in manifest.items() if hist and not hist[0].get("baseline") and hist[0]["ts"] >= limit}


def versions_for(lib: Library, nid: str) -> list[dict]:
    n = lib.nodes.get(nid)
    if not n:
        return []
    try:
        with open(os.path.join(lib.root, HISTORY, "versions", "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for rel in {n.get("path"), n.get("file")} - {None}:
        for v in reversed(manifest.get(rel, [])):
            out.append({"rel": rel, **v})
    return sorted(out, key=lambda v: v["ts"], reverse=True)


def restore_version(lib: Library, rel: str, vid: str) -> str:
    src = os.path.join(_vdir(lib.root, rel), vid + os.path.splitext(rel)[1])
    if not os.path.isfile(src):
        raise FileNotFoundError("보관된 버전이 없습니다")
    backup_sync(lib)  # 지금 내용을 먼저 보관
    shutil.copy2(src, lib.abspath(rel))
    return rel


def _git(root: str, *args, timeout=20, env: dict | None = None) -> subprocess.CompletedProcess:
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # 콘솔 창 숨김
    if env is not None:
        kw["env"] = env
    return subprocess.run(["git", "-c", "user.name=Assets Manager", "-c", "user.email=assets-manager@localhost",
                           "-c", "core.quotepath=false", *args], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, **kw)


def git_status(root: str) -> dict:
    if not shutil.which("git"):
        return {"available": False}
    r = _git(root, "rev-parse", "--show-toplevel")
    top = os.path.normcase(os.path.abspath(r.stdout.strip())) if r.returncode == 0 else None
    return {"available": True, "repo": top == os.path.normcase(os.path.abspath(root)),
            "inside_other": bool(top) and top != os.path.normcase(os.path.abspath(root))}


def _staged(root: str) -> list[tuple[str, str, str | None]]:
    """스테이징된 변경 목록(인덱스 열만): (상태 글자, 경로, 이름 변경 전 경로). 작업 폴더에만 있는 변경은 뺀다."""
    parts = _git(root, "status", "--porcelain=v1", "-z").stdout.split("\0")
    out, i = [], 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if len(e) < 4:
            continue
        code, path, orig = e[0], e[3:], None
        if code in "RC" and i < len(parts):
            orig = parts[i]
            i += 1
        if code in " ?!":
            continue
        out.append((code, path, orig))
    return out


# 자동 커밋 메시지의 분류 (앞 폴더 기준, 표시 순서). 고정 문구 분류는 이름을 붙이지 않는다.
# "설정"은 앱이 관리하는 루트 파일(.gitignore 줄 추가, Claude Code 권한 합치기)이라 자동 커밋한다.
COMMIT_AREAS = [("규칙", ("sets/", "principles/")), ("레퍼런스", ("image/", "web/", "document/", "writing/", "golden/")),
                ("산출물", ("outputs/",)), ("스킬", ("skills/",)), ("기본값", ("_defaults/",)),
                ("드롭 기록", ("history/drops.jsonl",)), ("기록", ("history/",)), ("노드 배치", ("layout.json",)),
                ("설정", (".gitignore", ".claude/settings.json"))]
COMMIT_FIXED = {"드롭 기록": "드롭 기록", "노드 배치": "노드 배치 저장"}
COMMIT_ACTION = {"A": "추가", "M": "수정", "D": "삭제", "R": "이름 변경", "C": "복사"}


def _commit_area(rel: str) -> tuple[str, str]:
    """경로 → (분류, 사람이 읽는 이름). 이름은 노드 id에 가깝게 (확장자·.meta 제거, 스킬은 폴더 이름)."""
    area = next((a for a, pre in COMMIT_AREAS if any(rel == p or rel.startswith(p) for p in pre)), "기타")
    parts = rel.split("/")
    if area == "스킬" and len(parts) > 1:
        return area, parts[1]
    name = parts[-1]
    for suf in (".meta.yaml", ".meta.yml"):
        if name.endswith(suf):
            return area, name[: -len(suf)]
    return area, (os.path.splitext(name)[0] if area != "기타" else name)


def commit_message(changes: list[tuple[str, str, str | None]], message: str | None = None,
                   checked_at: str | None = None) -> tuple[str, str]:
    """변경 목록으로 커밋 제목과 본문을 만든다.
    제목: `[앱 동작 — ]분류 동작 · …: 이름1, 이름2, 이름3 외 N개`. 본문: 파일마다 한 줄 + 출처
    (`앱 동작` 또는 `폴더 변경 감지[ (확인 시점)]`)."""
    order = [a for a, _ in COMMIT_AREAS] + ["기타"]
    groups: dict[tuple[str, str], list[str]] = {}
    lines = []
    for code, path, orig in changes:
        area, name = _commit_area(path)
        act = COMMIT_ACTION.get(code, "변경")
        groups.setdefault((area, act), [])
        if name not in groups[(area, act)]:
            groups[(area, act)].append(name)
        lines.append(f"{act} {orig} → {path}" if orig else f"{act} {path}")
    keys = sorted(groups, key=lambda k: (order.index(k[0]), list(COMMIT_ACTION.values()).index(k[1])
                                         if k[1] in COMMIT_ACTION.values() else 99))
    labels, names = [], []
    for area, act in keys:
        label = COMMIT_FIXED.get(area) or f"{area} {act}"
        if label not in labels:
            labels.append(label)
        if area not in COMMIT_FIXED:
            names += [n for n in groups[(area, act)] if n not in names]
    summary = " · ".join(labels)
    if names:
        summary += ": " + ", ".join(names[:3]) + (f" 외 {len(names) - 3}개" if len(names) > 3 else "")
    subject = f"{message} — {summary}" if message else summary
    lines.sort()
    if len(lines) > 50:
        lines = lines[:50] + [f"… 외 {len(lines) - 50}개"]
    source = "앱 동작" if message else "폴더 변경 감지" + (f" ({checked_at})" if checked_at else "")
    body = "\n".join(lines) + "\n\n출처: " + source
    return subject, body


def _unstage(root: str, paths: list[str]) -> None:
    """스테이징에서만 뺀다(파일은 그대로). 첫 커밋 전(HEAD 없음)에는 reset이 안 되므로 rm --cached."""
    if not paths:
        return
    has_head = _git(root, "rev-parse", "--verify", "-q", "HEAD").returncode == 0
    if has_head:
        _git(root, "reset", "-q", "--", *paths)
    else:
        _git(root, "rm", "-q", "-r", "--cached", "--ignore-unmatch", "--", *paths)


def git_commit(root: str, message: str | None = None, checked_at: str | None = None) -> dict:
    """라이브러리 데이터만 자동 커밋한다.

    분류가 "기타"인 파일(기획서·AGENTS.md·start.bat 같은 사람이 쓰는 루트 문서)은 스테이징에서 빼고
    `skipped`로 돌려준다. 사람이 직접 커밋한다 (ISSUE-032·033, 선택지 C)."""
    try:
        st = git_status(root)
        if not st["available"] or st.get("inside_other"):
            return st
        if not st["repo"]:
            _git(root, "init")
        gi = os.path.join(root, ".gitignore")
        have = []
        if os.path.exists(gi):
            with open(gi, encoding="utf-8") as f:
                have = f.read().splitlines()
        missing = [l for l in GITIGNORE_LINES if l not in have]
        if missing:
            with open(gi, "a", encoding="utf-8") as f:
                f.write(("\n" if have and have[-1] else "") + "\n".join(missing) + "\n")
        _git(root, "add", "-A")
        changes = _staged(root)
        other = [c for c in changes if _commit_area(c[1])[0] == "기타"
                 or (c[2] and _commit_area(c[2])[0] == "기타")]
        skipped = sorted({p for c in other for p in (c[1], c[2]) if p})
        if other:
            _unstage(root, skipped)
            changes = _staged(root)
        if not changes:
            return {"available": True, "repo": True, "committed": False, "skipped": skipped}
        msg, body = commit_message(changes, message, checked_at)
        r = _git(root, "commit", "-q", "-m", msg, "-m", body)
        return {"available": True, "repo": True, "committed": r.returncode == 0, "message": msg,
                "skipped": skipped, "error": r.stderr.strip() if r.returncode else None}
    except Exception as e:
        return {"available": True, "error": str(e)}


def git_remote_status(root: str) -> dict:
    """원격 대비 앞선(ahead)·뒤진(behind) 커밋 수. 네트워크에 닿지 않는다(마지막으로 받은 원격 정보 기준).

    현재 브랜치에 연결된 원격 브랜치(upstream)가 없으면 {"upstream": None}."""
    try:
        st = git_status(root)
        if not st["available"] or not st["repo"]:
            return {"upstream": None}
        up = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if up.returncode:
            return {"upstream": None}
        c = _git(root, "rev-list", "--left-right", "--count", "@{u}...HEAD")
        behind, ahead = (int(x) for x in c.stdout.split())
        return {"upstream": up.stdout.strip(), "ahead": ahead, "behind": behind}
    except Exception as e:
        return {"upstream": None, "error": str(e)}


def git_push(root: str, timeout: int = 60) -> dict:
    """라이브러리 커밋을 원격에 올린다. 사용자가 버튼을 누를 때만 부른다(P3: 외부 전송은 누를 때만).

    강제 push는 하지 않는다. 인증 창·터미널 입력을 기다리지 않도록 프롬프트를 끄고 시간 제한을 둔다."""
    st = git_remote_status(root)
    if not st.get("upstream"):
        return {"ok": False, **st, "error": "원격 저장소가 연결되어 있지 않습니다. 터미널에서 git push -u로 한 번 연결해 주세요."}
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    remote = _git(root, "config", f"branch.{branch}.remote").stdout.strip()
    merge = _git(root, "config", f"branch.{branch}.merge").stdout.strip()
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    try:
        r = _git(root, "push", remote, f"HEAD:{merge}", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, **st, "error": f"{timeout}초 안에 끝나지 않았습니다. 인터넷 연결이나 로그인 상태를 확인해 주세요."}
    after = git_remote_status(root)
    if r.returncode:
        out = r.stderr.strip() or r.stdout.strip()
        if any(k in out for k in ("non-fast-forward", "fetch first", "rejected")):
            msg = "원격에 이 PC에 없는 커밋이 있어 올리지 않았습니다(강제로 덮어쓰지 않음). 터미널에서 git pull로 합친 뒤 다시 올려 주세요."
        else:
            msg = "올리기 실패: " + (out.splitlines()[-1] if out else f"git 종료 코드 {r.returncode}")
        return {"ok": False, **after, "error": msg, "output": out}
    return {"ok": True, **after}


# ------------------------------------------------------------------ 휴지통
def node_files(lib: Library, nid: str) -> list[str]:
    n = lib.nodes.get(nid)
    if not n:
        raise KeyError(f"노드 없음: {nid}")
    if n.get("type") == "skill" and n.get("dir"):  # v3.7 스킬은 폴더째 (숨김 파일 포함)
        base = lib.abspath(n["dir"])
        out = []
        for dp, _, fns in os.walk(base):
            for fn in sorted(fns):
                out.append(os.path.relpath(os.path.join(dp, fn), lib.root).replace("\\", "/"))
        return sorted(out)
    files = [n["path"]] if n.get("path") else []
    if n.get("file") and n.get("kind") != "rule":
        files.append(n["file"])
    return [f for f in dict.fromkeys(files) if os.path.isfile(lib.abspath(f))]


def delete_node(lib: Library, nid: str) -> dict:
    files = node_files(lib, nid)
    users = [r["id"] for r in lib.rule_nodes() if any(lib._ref_id(x["value"]) == nid for x in r["rules"].values())]
    children = [r["id"] for r in lib.rule_nodes() if nid in r["inherits"]]
    users += [r["id"] for r in lib.rule_nodes() if nid in (r.get("uses") or []) and r["id"] not in users]
    tid = f"{_stamp()}_{re.sub(r'[^A-Za-z0-9_-]+', '-', nid)}"
    tdir = os.path.join(lib.root, HISTORY, "trash", tid)
    for rel in files:
        dst = os.path.join(tdir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(lib.abspath(rel), dst)
    if lib.nodes[nid].get("type") == "skill":  # 빈 폴더 정리
        for dp, _, _ in sorted(os.walk(lib.abspath(lib.nodes[nid]["dir"])), key=lambda x: -len(x[0])):
            try:
                os.rmdir(dp)
            except OSError:
                pass
    with open(os.path.join(tdir, "trash.json"), "w", encoding="utf-8") as f:
        json.dump({"id": tid, "node": nid, "title": lib.nodes[nid].get("title"), "files": files, "ts": now(),
                   "used_by": users, "children": children}, f, ensure_ascii=False, indent=1)
    return {"trash_id": tid, "files": files, "used_by": users, "children": children}


def list_trash(root: str) -> list[dict]:
    base = os.path.join(root, HISTORY, "trash")
    out = []
    if os.path.isdir(base):
        for tid in sorted(os.listdir(base), reverse=True):
            try:
                with open(os.path.join(base, tid, "trash.json"), encoding="utf-8") as f:
                    out.append(json.load(f))
            except (OSError, json.JSONDecodeError):
                continue
    return out


def restore_trash(root: str, tid: str) -> list[str]:
    tdir = os.path.join(root, HISTORY, "trash", tid)
    with open(os.path.join(tdir, "trash.json"), encoding="utf-8") as f:
        info = json.load(f)
    restored = []
    for rel in info["files"]:
        dst = _free_name(os.path.join(root, rel))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(os.path.join(tdir, rel), dst)
        restored.append(os.path.relpath(dst, root).replace("\\", "/"))
    shutil.rmtree(tdir, ignore_errors=True)
    return restored


def _free_name(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    if base.endswith(".meta"):
        base, ext = base[:-5], ".meta" + ext
    i = 2
    while os.path.exists(f"{base}-{i}{ext}"):
        i += 1
    return f"{base}-{i}{ext}"


# ------------------------------------------------------------------ 가져오기 (Q37, Q42)
def _safe_name(name: str) -> str:
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "file"


def import_items(lib: Library, items: list[dict]) -> list[dict]:
    """items: [{path: 디스크 경로}] (데스크톱) 또는 [{name, data: bytes}] (브라우저)."""
    backup_sync(lib)
    results = []
    for it in items:
        name = _safe_name(it.get("name") or it["path"])
        data = it.get("data")
        if data is None:
            with open(it["path"], "rb") as f:
                data = f.read()
        if name.lower().endswith(".zip"):
            results += import_zip(lib, data)
        else:
            results.append(_place_original(lib, name, data))
    return results


def _place_original(lib: Library, name: str, data: bytes, folder: str | None = None) -> dict:
    ext = os.path.splitext(name)[1].lower()
    if ext == ".md" and FRONTMATTER.match(data.decode("utf-8", "replace").lstrip("﻿")):
        return _place_rule(lib, name, data)
    t = EXT_TYPE.get(ext)
    if not t and not folder:
        return {"name": name, "status": "skipped", "message": "지원하지 않는 파일 형식"}
    folder = folder or TYPE_FOLDER[t]
    dst = os.path.join(lib.root, folder, name)
    if os.path.exists(dst):
        with open(dst, "rb") as f:
            if f.read() == data:
                return {"name": name, "rel": f"{folder}/{name}", "status": "same", "message": "이미 있는 파일"}
        dst = _free_name(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(data)
    rel = os.path.relpath(dst, lib.root).replace("\\", "/")
    return {"name": name, "rel": rel, "status": "added", "kind": "original"}


def _place_rule(lib: Library, name: str, data: bytes, rel_hint: str | None = None) -> dict:
    fm = yaml.safe_load(FRONTMATTER.match(data.decode("utf-8", "replace").lstrip("﻿")).group(1)) or {}
    folder = "principles" if fm.get("kind") == "principle" else "sets"
    if rel_hint and rel_hint.split("/")[0] in ("principles", "sets"):
        folder = os.path.dirname(rel_hint)
    dst = os.path.join(lib.root, folder, _safe_name(name))
    status = "updated" if os.path.exists(dst) else "added"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(data)
    return {"name": name, "rel": os.path.relpath(dst, lib.root).replace("\\", "/"), "status": status, "kind": "rule"}


def import_zip(lib: Library, data: bytes) -> list[dict]:
    results = []
    default_author = last_author_target(lib.root)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            member = info.filename.replace("\\", "/")
            parts = [p for p in member.split("/") if p not in ("", ".")]
            if not parts or ".." in parts or member.startswith("/") or ":" in parts[0]:
                results.append({"name": member, "status": "skipped", "message": "허용되지 않는 경로"})
                continue
            if parts[0] == "assets-import" and len(parts) > 1:
                parts = parts[1:]
            if parts[-1].startswith(".") or parts[0] == "__MACOSX":
                continue
            raw = z.read(info)
            name = parts[-1]
            if name.endswith(".meta.yaml"):
                results.append(_place_meta(lib, parts, raw, default_author))
            elif name.endswith(".md") and FRONTMATTER.match(raw.decode("utf-8", "replace").lstrip("﻿")):
                results.append(_place_rule(lib, name, raw, "/".join(parts)))
            else:
                folder = parts[0] if len(parts) > 1 and parts[0] in TYPE_FOLDER.values() else None
                results.append(_place_original(lib, name, raw, folder))
    return results


def _place_meta(lib: Library, parts: list[str], raw: bytes, default_author: str | None) -> dict:
    text = raw.decode("utf-8", "replace")
    try:
        meta = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        return {"name": parts[-1], "status": "error", "message": f"yaml 문법 오류: {e}"}
    target_file = str(meta.get("file") or parts[-1][: -len(".meta.yaml")])
    # 원본이 있는 폴더 옆에 둔다. zip 안 폴더가 타입 폴더면 그것을 쓴다.
    folder = parts[0] if len(parts) > 1 and parts[0] in TYPE_FOLDER.values() else None
    if len(parts) > 1 and parts[0] in ("outputs", "golden"):  # 산출물은 outputs/<세트>/ 그대로
        folder = "/".join(parts[:-1])
    if not folder:
        for f in TYPE_FOLDER.values():
            if os.path.exists(os.path.join(lib.root, f, target_file)):
                folder = f
                break
    folder = folder or TYPE_FOLDER.get(meta.get("type"), "image")
    if not meta.get("authored_by"):
        author = last_author_target(lib.root, f"{folder}/{target_file}") or default_author
        if author:
            text = text.rstrip("\n") + f"\nauthored_by: {author}\n"
    dst = os.path.join(lib.root, folder, parts[-1])
    status = "updated" if os.path.exists(dst) else "added"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)
    rel = os.path.relpath(dst, lib.root).replace("\\", "/")
    warn = None if os.path.exists(os.path.join(lib.root, folder, target_file)) else f"원본 {target_file}이(가) {folder}/에 없습니다"
    return {"name": parts[-1], "rel": rel, "status": status, "kind": "meta", "message": warn}


# ------------------------------------------------------------------ 기본값 업데이트 (Q17, 알림형)
def _version_of(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = FRONTMATTER.match(text)
        data = yaml.safe_load(m.group(1) if m else text) or {}
        return int(data.get("version") or 0)
    except Exception:
        return 0


def defaults_updates(root: str) -> list[dict]:
    out = []
    if not os.path.isdir(BUNDLED_DEFAULTS):
        return out
    for name in sorted(os.listdir(BUNDLED_DEFAULTS)):
        bv = _version_of(os.path.join(BUNDLED_DEFAULTS, name))
        lv = _version_of(os.path.join(root, "_defaults", name))
        if bv > lv:
            out.append({"file": name, "current": lv, "bundled": bv})
    return out


def upgrade_defaults(lib: Library) -> list[str]:
    backup_sync(lib)  # 이전 판 보관
    done = []
    for u in defaults_updates(lib.root):
        shutil.copy2(os.path.join(BUNDLED_DEFAULTS, u["file"]), os.path.join(lib.root, "_defaults", u["file"]))
        done.append(u["file"])
    return done
