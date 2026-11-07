"""폴더 → 노드 AI 요청 (기획서 v3.1).

흐름
  1. prepare  : 폴더(또는 파일들)를 노드에 놓으면 파일을 모으고 지문으로 기존 파일을 표시한다. → 요청 확인 다이얼로그
  2. package  : 확인하면 가이드 프롬프트(+ plan.yaml 규격)와 첨부를 만든다. 채팅형은 한도를 넘으면
                이미지는 컨택트 시트, 텍스트는 묶음 파일로 합친다.
  3. receive  : 결과가 돌아온다. 채팅형은 plan.yaml이 든 zip을 앱에 놓고, 에이전트는 inbox/<rid>/out/에 쓴다.
  4. preview  : plan.yaml을 항목(새 노드 / 키 추가 / 키 덮어쓰기 / 연결)으로 펼친다. 덮어쓰기는 기본 꺼짐. → 결과 확인 다이얼로그
  5. apply    : 체크한 항목만 적용한다. 키는 추가·변경만 허용(삭제 없음).

요청 폴더: inbox/<rid>/  (request.json, src/ 원본 사본, out/ AI 결과)  — 로더와 Git에서 제외
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import zipfile

import yaml

from engine import APP_DIR, FRONTMATTER, IMAGE_EXT, Library, make_contact_sheet
import extract as extractor
import store

INBOX = "inbox"
MAX_FILES = 50
SUPPORTED = set(store.EXT_TYPE)
TEXT_EXT = {".html", ".htm", ".css", ".txt", ".md"}
EXTRACT_PY = os.path.join(APP_DIR, "core", "extract.py")

# 문서에서 원칙·결정을 뽑을 때의 공통 안내 (Q71-B). export_cmd도 같은 문장을 쓴다.
PRINCIPLE_GUIDE = ("자료에 작업 원칙·결정·금지 사항이 있으면 규칙 노드로 만드세요: 주제별로 `principles/<id>.md`(kind: principle)로 나누고, "
                   "원칙에는 `inherits`를 쓰지 마세요(원칙은 레퍼런스처럼 세트에 붙는 부품입니다). "
                   "그 원칙들을 `uses: [원칙 id, …]`로 붙이는 `sets/<id>.md`(kind: set, `inherits: [global]`)를 하나 만드세요. "
                   "주제마다 **서로 다른 키**를 쓰세요(같은 키는 뒤에 붙인 원칙이 덮어씁니다). "
                   "개발 원칙이면 키 어휘의 dev 키(design_approval, doc_roles, conflict_policy, workflow 등)를 먼저 쓰고, 미해결 문제는 `open_questions`, 폐기한 결정은 `forbidden`에 넣으세요. "
                   "근거가 된 원본 문서는 `document/` 레퍼런스로 등록하고, 세트 한 곳에만 `relations: [{type: derived_from, target: <문서 id>}]`를 다세요.")

RECOMMENDED = {
    "set": "이 세트에 쓸 레퍼런스로 등록하고, 알맞은 키로 세트에 연결 (update.set_rules)",
    "principle": "폴더 내용으로 새 세트를 만들어 이 원칙을 상속 (create에 세트 md 포함)",
    "defaults": "새 세트를 만들어 기본값을 상속",
    "reference": "이 레퍼런스의 변형이나 비슷한 대안으로 등록 (relations: variant_of / similar_to)",
    "unregistered": "레퍼런스로 등록하고 필요하면 서로 연결",
    None: "연결 없이 레퍼런스로만 등록",
}


def _rdir(root: str, rid: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", rid or ""):
        raise ValueError("잘못된 요청 id")
    return os.path.join(root, INBOX, rid)


def _load(root: str, rid: str) -> dict:
    with open(os.path.join(_rdir(root, rid), "request.json"), encoding="utf-8") as f:
        return json.load(f)


def _save(root: str, req: dict) -> None:
    with open(os.path.join(_rdir(root, req["id"]), "request.json"), "w", encoding="utf-8") as f:
        json.dump(req, f, ensure_ascii=False, indent=1)


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def library_hashes(lib: Library) -> dict[str, str]:
    """라이브러리의 레퍼런스 원본 지문 → 노드 id (등록된 것만)."""
    out = {}
    for n in lib.nodes.values():
        if n["type"] in ("rule", "ai") or not n.get("file") or n.get("kind") == "unregistered":
            continue
        p = lib.abspath(n["file"])
        if os.path.isfile(p):
            out[store._sha(p)] = n["id"]
    return out


def new_request(lib: Library, node_id: str | None, kind: str = "folder", status: str = "prepared") -> dict:
    node = lib.nodes.get(node_id) if node_id else None
    if node_id and not node:
        raise KeyError(f"노드 없음: {node_id}")
    rid = dt.datetime.now().strftime("req-%Y%m%d-%H%M%S")
    while os.path.exists(os.path.join(lib.root, INBOX, rid)):
        rid += "x"
    os.makedirs(os.path.join(lib.root, INBOX, rid, "src"))
    nkind = (node["kind"] if node and node["type"] == "rule" else ("reference" if node else None))
    if node and node.get("kind") in ("unregistered", "output"):
        nkind = node["kind"] if node["kind"] == "unregistered" else "reference"
    req = {"id": rid, "created": store.now(), "status": status, "kind": kind, "node": node_id,
           "node_title": node["title"] if node else None, "node_kind": nkind,
           "recommended": RECOMMENDED.get(nkind, RECOMMENDED[None]), "files": [], "skipped": [], "over_limit": [],
           "target": None, "assign": {}}
    _save(lib.root, req)
    return req


def add_src(lib: Library, req: dict, name: str, data: bytes, hashes: dict | None = None) -> dict:
    d = _rdir(lib.root, req["id"])
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    fn = store._safe_name(name)
    base, ext = os.path.splitext(fn)
    used = {f["name"].lower() for f in req["files"]}
    same = next((f for f in req["files"] if f["sha"] == _sha_bytes(data)), None)
    if same:
        return same
    i = 2
    while fn.lower() in used:
        fn = f"{base}-{i}{ext}"
        i += 1
    with open(os.path.join(d, "src", fn), "wb") as f:
        f.write(data)
    sha = _sha_bytes(data)
    hashes = hashes if hashes is not None else library_hashes(lib)
    entry = {"name": fn, "orig": name, "type": store.EXT_TYPE.get(ext.lower(), "image"), "size": len(data), "sha": sha,
             "existing": hashes.get(sha), "extracted": extractor.extract(os.path.join(d, "src", fn))}
    req["files"].append(entry)
    return entry


# ------------------------------------------------------------------ 1. prepare
def collect(paths: list[str]) -> list[tuple[str, str]]:
    """디스크 경로(폴더 포함) → [(상대 이름, 절대 경로)]"""
    out = []
    for p in paths:
        if os.path.isdir(p):
            base = os.path.dirname(os.path.abspath(p))
            for dp, dns, fns in os.walk(p):
                dns[:] = sorted(d for d in dns if not d.startswith("."))
                for fn in sorted(fns):
                    if not fn.startswith("."):
                        full = os.path.join(dp, fn)
                        out.append((os.path.relpath(full, base).replace("\\", "/"), full))
        elif os.path.isfile(p):
            out.append((os.path.basename(p), p))
    return out


def prepare(lib: Library, node_id: str | None, paths: list[str] | None = None,
            uploads: list[dict] | None = None) -> dict:
    """uploads: [{name: '폴더/파일.png', data: bytes}] (브라우저)"""
    items = []  # (이름, bytes)
    for rel, full in collect(paths or []):
        with open(full, "rb") as f:
            items.append((rel, f.read()))
    for u in uploads or []:
        items.append((u["name"].replace("\\", "/"), u["data"]))
    skipped = [n for n, _ in items if os.path.splitext(n)[1].lower() not in SUPPORTED]
    items = [(n, b) for n, b in items if os.path.splitext(n)[1].lower() in SUPPORTED]
    over = items[MAX_FILES:]
    items = items[:MAX_FILES]
    if not items:
        raise ValueError("지원하는 파일이 없습니다 (이미지, html/css, pdf, txt/md)")
    node = lib.nodes.get(node_id) if node_id else None
    if node_id and not node:
        raise KeyError(f"노드 없음: {node_id}")
    rid = dt.datetime.now().strftime("req-%Y%m%d-%H%M%S")
    d = _rdir(lib.root, rid)
    while os.path.exists(d):
        rid += "x"
        d = _rdir(lib.root, rid)
    os.makedirs(os.path.join(d, "src"))
    hashes = library_hashes(lib)
    files, used = [], set()
    for name, data in items:
        fn = store._safe_name(name)
        base, ext = os.path.splitext(fn)
        i = 2
        while fn.lower() in used:
            fn = f"{base}-{i}{ext}"
            i += 1
        used.add(fn.lower())
        with open(os.path.join(d, "src", fn), "wb") as f:
            f.write(data)
        sha = _sha_bytes(data)
        ex = extractor.extract(os.path.join(d, "src", fn))
        files.append({"name": fn, "orig": name, "type": store.EXT_TYPE[ext.lower()], "size": len(data), "sha": sha,
                      "existing": hashes.get(sha), "extracted": ex})
    kind = (node["kind"] if node and node["type"] == "rule" else ("reference" if node else None))
    if node and node.get("kind") == "unregistered":
        kind = "unregistered"
    req = {"id": rid, "created": store.now(), "status": "prepared", "node": node_id,
           "node_title": node["title"] if node else None, "node_kind": kind,
           "recommended": RECOMMENDED.get(kind, RECOMMENDED[None]), "files": files,
           "skipped": skipped, "over_limit": [n for n, _ in over], "target": None}
    _save(lib.root, req)
    return req


# ------------------------------------------------------------------ 2. package
def package(lib: Library, rid: str, target: str, note: str = "", write: bool = True) -> dict:
    req = _load(lib.root, rid)
    tgt = lib.targets.get(target)
    if not tgt:
        raise KeyError(f"알 수 없는 대상: {target}")
    agent = tgt.get("kind", "chat") == "agent"
    d = _rdir(lib.root, rid)
    src = os.path.join(d, "src")
    pkg_dir = os.path.join(d, "package")
    if write:
        shutil.rmtree(pkg_dir, ignore_errors=True)
        os.makedirs(pkg_dir)
    new_files = [f for f in req["files"] if not f["existing"]]
    attach, merged = [], {}
    if not agent:
        slots = int(tgt.get("max_files", 10)) - 2  # prompt.md, extract.py
        if len(new_files) <= slots:
            attach = [os.path.join(src, f["name"]) for f in new_files]
        else:  # Q55-B: 이미지 → 컨택트 시트, 텍스트 → 묶음 파일
            imgs = [f for f in new_files if os.path.splitext(f["name"])[1].lower() in IMAGE_EXT]
            texts = [f for f in new_files if os.path.splitext(f["name"])[1].lower() in TEXT_EXT]
            others = [f for f in new_files if f not in imgs and f not in texts]
            if imgs:
                sheet = os.path.join(pkg_dir, "contact-sheet.png")
                if write:
                    make_contact_sheet([os.path.join(src, f["name"]) for f in imgs], sheet)
                attach.append(sheet)
                merged["contact-sheet.png"] = [f["name"] for f in imgs]
            if texts:
                bundle = os.path.join(pkg_dir, "bundle.txt")
                if write:
                    with open(bundle, "w", encoding="utf-8") as out:
                        for f in texts:
                            with open(os.path.join(src, f["name"]), encoding="utf-8", errors="replace") as fin:
                                out.write(f"\n===== 파일: {f['name']} =====\n{fin.read()}\n")
                attach.append(bundle)
                merged["bundle.txt"] = [f["name"] for f in texts]
            attach += [os.path.join(src, f["name"]) for f in others][: max(0, slots - len(attach))]
    prompt = render(lib, req, target, agent, note, merged)
    prompt_path = os.path.join(pkg_dir, "prompt.md")
    if write:
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)
        req.update({"status": "sent", "target": target, "note": note, "sent": store.now()})
        _save(lib.root, req)
    files = [prompt_path] + ([] if agent else [EXTRACT_PY] + attach)
    return {"request": rid, "target": target, "target_label": tgt.get("label", target), "agent": agent,
            "files": files, "prompt": prompt, "clipboard": prompt, "merged": merged,
            "prompt_hash": hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12],
            "drag_icon": lib.drag_icon() if write else None}


def render(lib: Library, req: dict, target: str, agent: bool, note: str, merged: dict) -> str:
    rid = req["id"]
    out_dir = os.path.join(lib.root, INBOX, rid, "out")
    node = lib.nodes.get(req["node"]) if req["node"] else None
    L = ["# Assets Manager 폴더 등록 요청", "",
         f"요청 id: `{rid}`", "",
         "사용자가 레퍼런스 파일 묶음을 그래프의 한 노드 위에 놓았습니다. 파일을 라이브러리 노드로 만들고,",
         "놓인 노드와 어떻게 연결할지 **판단해서 계획서(plan.yaml)로** 돌려주세요. 기존 파일은 직접 고치지 마세요.", ""]
    if note.strip():
        L += ["## 사용자 요청", note.strip(), ""]
    L += ["## 놓인 노드"]
    if node:
        L.append(f"- `{node['id']}` · {node['title']} · 종류: {req['node_kind']}")
        if node["type"] == "rule" and node["id"] not in lib.cyclic:
            for e in lib.assemble(node["id"])["entries"]:
                if e["source"] == node["id"]:
                    L.append(f"  - 현재 규칙 `{e['key']}`: {e['value'] if isinstance(e['value'], str) else json.dumps(e['value'], ensure_ascii=False)}")
    else:
        L.append("- (빈 공간에 놓음)")
    L += ["", f"**권장 해석:** {req['recommended']}",
          "권장 해석과 다르게 판단해도 됩니다. 그때는 해당 항목의 `reason`에 이유를 한 줄로 쓰세요.", ""]
    L += ["## 파일"]
    for f in req["files"]:
        if f["existing"]:
            L.append(f"- `{f['name']}` · {f['type']} · **이미 `{f['existing']}` 노드로 등록됨 → 새로 만들지 말고 이 id로 연결만 하세요**")
        else:
            where = next((k for k, v in merged.items() if f["name"] in v), None)
            loc = (f"경로: {os.path.join(lib.root, INBOX, rid, 'src', f['name'])}" if agent
                   else (f"{where} 안에 포함" if where else "첨부됨"))
            L.append(f"- `{f['name']}` · {f['type']} · {loc}")
    if merged:
        L += ["", "첨부 한도 때문에 일부 파일을 합쳤습니다. `contact-sheet.png`는 위 순서대로 번호가 붙은 이미지 모음이고,",
              "`bundle.txt`는 `===== 파일: 이름 =====` 줄로 구분된 텍스트 모음입니다. meta의 `file`에는 **원래 파일 이름**을 쓰세요."]
    L += ["", "## 결정적인 값 (앱이 원본에서 뽑음, hex는 이 값을 그대로 쓰세요)", "```json",
          json.dumps({f["name"]: f["extracted"] for f in req["files"] if not f["existing"]}, ensure_ascii=False, indent=1),
          "```", ""]
    L += ["## 산출물 1: 새 레퍼런스마다 `<폴더>/<파일명에서 확장자를 뺀 이름>.meta.yaml`",
          "폴더는 image / web / document / writing 중 하나입니다. 형식:", "```yaml",
          "id: 영문-소문자-하이픈       # 아래 '기존 id'와 겹치면 안 됨",
          "type: image | web | document | writing",
          "title: 한국어 제목",
          "file: 원래 파일 이름",
          "summary: 한 줄 요약 (40자 이내)",
          "spec: |                     # 첨부 없이 글로만 전달할 때 쓰는 설명 3~6줄, 보이는 것만",
          "  ...",
          "check:                      # 이 레퍼런스를 따랐는지 확인하는 예/아니오 질문 2~4개",
          "  - ...인가",
          "tags: [..]",
          "sensitivity: public",
          f"authored_by: {target}",
          "extracted: {...}            # 위 결정적인 값 그대로",
          "```", "",
          "새 세트나 원칙이 필요하면 `sets/<id>.md` 또는 `principles/<id>.md`를 만드세요(프런트매터: id, type: rule, kind, title, inherits(세트만), uses(세트가 붙이는 원칙), rules).",
          PRINCIPLE_GUIDE, ""]
    L += ["## 산출물 2: `plan.yaml` (반드시 이 형식)", "```yaml",
          f"request: {rid}",
          "summary: 무엇을 만들었는지 한 줄",
          "create:                     # 새로 만든 파일 (산출물 1의 경로)",
          "  - path: web/landing.meta.yaml",
          "    reason: ...",
          "update:                     # 기존 규칙 노드의 키 추가·변경 (삭제는 불가)",
          "  - node: <기존 규칙 노드 id>",
          "    set_rules:",
          "      <키>: <텍스트 값 또는 ref:<id>>",
          "    reason: ...",
          "relations:                  # 참고용 정보 엣지 (프롬프트에는 안 들어감)",
          "  - { from: <id>, type: similar_to | variant_of | pairs_with | supersedes | derived_from, to: <id>, reason: ... }",
          "```",
          "- 기존 키를 바꾸는 update는 사용자가 따로 확인합니다. 꼭 필요할 때만 쓰고 이유를 분명히 쓰세요.",
          "- 키 이름은 아래 '키 어휘'에서 고르세요. 새 키는 꼭 필요할 때만.", ""]
    L += ["## 돌려주는 방법"]
    if agent:
        L += [f"- 모든 산출물을 `{out_dir}` 폴더 안에 쓰세요 (예: `{os.path.join(out_dir, 'plan.yaml')}`, `{os.path.join(out_dir, 'web', 'landing.meta.yaml')}`).",
              "- 라이브러리의 다른 폴더에는 아무것도 쓰지 마세요. 사용자가 Assets Manager에서 결과를 확인한 뒤 적용합니다.",
              "- 끝나면 만든 파일 목록만 짧게 알려 주세요."]
    else:
        L += ["- `plan.yaml`과 산출물 1을 **`assets-import.zip` 하나**로 묶어 내려받게 주세요(원본 파일은 넣지 마세요).",
              "  zip 안 구조: `plan.yaml`, `image/foo.meta.yaml`, `sets/bar.md` …",
              "- 사용자는 이 zip을 Assets Manager 창에 끌어다 놓고, 결과를 확인한 뒤 적용합니다."]
    L += ["", "## 기존 id"]
    for n in sorted(lib.nodes.values(), key=lambda x: (x["type"], x["id"])):
        if n["kind"] != "unregistered":
            L.append(f"- `{n['id']}` · {n['type']}/{n['kind']} · {n['title']}")
    L += ["", "## 키 어휘"]
    for k, spec in lib.keys.items():
        L.append(f"- `{k}` · {spec.get('label', k)} · {spec.get('domain', '')} · {spec.get('priority', 'optional')}")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 3. receive
def auto_links(lib: Library, target: str | None, items: list[dict]) -> list[dict]:
    """Q70-C + v3.4(Q72-A 부품 원칙): 노드에 놓은 결과는 반드시 그 노드의 하위가 된다. AI가 연결을 비워 둬도 앱이 잇는다.
    세트·계층 원칙 → 새 원칙은 그 노드가 uses로 붙임(부품), 새 세트는 상속 자식
    부품 원칙     → 새 세트는 그 원칙을 uses로 붙임, 새 원칙은 그 원칙이 uses로 묶음
    기본값        → 새 세트는 상속 자식, 새 원칙은 '하위' 정보 연결
    세트          → 산출물은 그 세트 소속(set)
    레퍼런스      → 레퍼런스는 변형(variant_of), 그 밖은 '하위'
    """
    t = lib.nodes.get(target) if target else None
    if not t:
        return []
    creates = [i for i in items if i["kind"] == "create" and not i.get("error") and i.get("node_id")]
    linked = set()
    for i in items:
        ref = lib._ref_id(i.get("after")) if i["kind"] in ("add", "overwrite") and i.get("node") == target else None
        if ref:
            linked.add(ref)
        if i["kind"] == "relation" and target in (i.get("from"), i.get("to")):
            linked.update({i.get("from"), i.get("to")})
    out = []
    for c in creates:
        nid = c["node_id"]
        if nid in linked:
            continue
        if c["node_type"] == "rule" and t["type"] == "rule":
            if target in (c.get("inherits") or []) or target in (c.get("uses") or []):
                continue
            new_principle = c.get("rule_kind") == "principle"
            t_component = t["kind"] == "principle" and not any(target in m["inherits"] for m in lib.rule_nodes())
            if t["kind"] == "defaults":
                link, label = (("child_of", f"'{t['title']}'의 하위로 (참고 연결)") if new_principle
                               else ("inherits", f"'{t['title']}'을(를) 상속하는 하위 노드로"))
            elif new_principle:
                link, label = "uses", f"'{t['title']}'이(가) 붙이는 원칙(부품)으로"
            elif t_component:
                link, label = "uses_target", f"'{t['title']}' 원칙을 붙이는 세트로"
            else:
                link, label = "inherits", f"'{t['title']}'을(를) 상속하는 하위 노드로"
        elif c.get("output") and t.get("kind") == "set":
            continue  # 적용할 때 set으로 소속됨 (그래프에 '산출물' 연결)
        elif t["type"] not in ("rule", "ai") and c["node_type"] != "rule":
            link, label = "variant_of", f"'{t['title']}'의 변형으로"
        else:
            link, label = "child_of", f"'{t['title']}'의 하위로 (참고 연결, 프롬프트에는 안 들어감)"
        out.append({"id": f"l{len(out)}", "kind": "link", "node_id": nid, "title": c.get("title"), "link": link,
                    "target": target, "target_title": t["title"], "checked": True, "depends_on": [c["id"]],
                    "reason": f"노드에 놓아서 {label} 자동 연결", "warnings": []})
    return out


def set_target(lib: Library, rid: str, node_id: str | None) -> dict:
    """빈 공간에 놓은 결과 zip: 예상 결과 창에서 '어느 노드 아래에 둘까요?'로 고른 노드를 대상으로."""
    req = _load(lib.root, rid)
    node = lib.nodes.get(node_id) if node_id else None
    if node_id and not node:
        raise KeyError(f"노드 없음: {node_id}")
    req.update(node=node_id, node_title=node["title"] if node else None,
               node_kind=(node["kind"] if node and node["type"] == "rule" else ("reference" if node else None)))
    _save(lib.root, req)
    return preview(lib, rid)


def zip_has_plan(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return any(os.path.basename(i.filename) == "plan.yaml" for i in z.infolist())
    except zipfile.BadZipFile:
        return False


def receive_zip(lib: Library, data: bytes, node_id: str | None = None) -> str:
    """plan.yaml이 든 zip → inbox/<rid>/ 에 풀어 둔다.
    - plan.request가 있는 요청이면 그 요청으로 (노드별 내보내기 명령, 폴더 요청)
    - 없으면 새 요청을 만들고 놓은 노드를 대상으로 한다 (상단 내보내기 명령, @target)
    - 원본 파일(.meta.yaml·plan·규칙 md가 아닌 것)은 src/로, 나머지는 out/으로
    """
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [i.filename.replace("\\", "/") for i in z.infolist() if not i.is_dir()]
        plan_name = next(n for n in names if os.path.basename(n) == "plan.yaml")
        prefix = plan_name[: -len("plan.yaml")]
        plan = yaml.safe_load(z.read(plan_name)) or {}
        rid = str(plan.get("request") or "")
        if rid and os.path.isfile(os.path.join(lib.root, INBOX, rid, "request.json")):
            req = _load(lib.root, rid)
            if node_id and not req.get("node"):
                node = lib.nodes.get(node_id)
                req.update(node=node_id, node_title=node["title"] if node else None)
        else:
            req = new_request(lib, node_id, kind="export", status="received")
            rid = req["id"]
        out = os.path.join(_rdir(lib.root, rid), "out")
        shutil.rmtree(out, ignore_errors=True)
        hashes = library_hashes(lib)
        for n in names:
            if not n.startswith(prefix):
                continue
            rel = n[len(prefix):]
            parts = [p for p in rel.split("/") if p not in ("", ".")]
            if not parts or ".." in parts or ":" in parts[0] or parts[0] == "__MACOSX" or parts[-1].startswith("."):
                continue
            raw = z.read(n)
            is_plan = rel == "plan.yaml"
            is_meta = parts[-1].endswith(".meta.yaml")
            is_rule = parts[-1].endswith(".md") and bool(FRONTMATTER.match(raw.decode("utf-8", "replace").lstrip("\ufeff")))
            if is_plan or is_meta or is_rule:
                dst = os.path.join(out, *parts)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(raw)
            elif os.path.splitext(parts[-1])[1].lower() in SUPPORTED:
                add_src(lib, req, parts[-1], raw, hashes)
    req.update({"status": "received", "received": store.now(), "missing_declared": plan.get("missing") or []})
    _save(lib.root, req)
    return rid


def attach(lib: Library, rid: str, items: list[tuple[str, bytes]]) -> dict:
    """결과 확인 중에 원본 여러 개를 한 번에 놓기 → 짝 맞추기 (이름 → 종류별 하나 남음 → 직접 선택)."""
    req = _load(lib.root, rid)
    hashes = library_hashes(lib)
    for name, data in items:
        if os.path.splitext(name)[1].lower() in SUPPORTED:
            add_src(lib, req, name, data, hashes)
    _save(lib.root, req)
    auto_assign(lib, rid)
    return preview(lib, rid)


def auto_assign(lib: Library, rid: str) -> None:
    req = _load(lib.root, rid)
    pv = preview(lib, rid)
    missing = [i for i in pv["items"] if i["kind"] == "create" and i.get("missing_original")]
    used = {i.get("src") for i in pv["items"] if i.get("src")}
    free = [f for f in req["files"] if f["name"] not in used and not f["existing"]]
    for t in sorted({store.EXT_TYPE.get(os.path.splitext(str(i.get("file") or ""))[1].lower(), i.get("node_type")) for i in missing} - {None}):
        ms = [i for i in missing if store.EXT_TYPE.get(os.path.splitext(str(i.get("file") or ""))[1].lower(), i.get("node_type")) == t]
        fs = [f for f in free if f["type"] == t]
        if len(ms) == 1 and len(fs) == 1:
            req["assign"][ms[0]["path"]] = fs[0]["name"]
    _save(lib.root, req)


def assign(lib: Library, rid: str, path: str, src_name: str | None) -> dict:
    req = _load(lib.root, rid)
    if src_name:
        req["assign"][path] = src_name
    else:
        req["assign"].pop(path, None)
    _save(lib.root, req)
    return preview(lib, rid)


def list_requests(root: str) -> list[dict]:
    base = os.path.join(root, INBOX)
    out = []
    if os.path.isdir(base):
        for rid in sorted(os.listdir(base), reverse=True):
            try:
                req = _load(root, rid)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if req["status"] == "sent" and os.path.isfile(os.path.join(base, rid, "out", "plan.yaml")):
                req["status"] = "received"  # 에이전트가 out/에 직접 씀
                _save(root, req)
            out.append({k: req.get(k) for k in ("id", "status", "kind", "node", "node_title", "target", "created", "sent")}
                       | {"files": len(req["files"])})
    return out


def latest_open(root: str) -> str | None:
    for r in list_requests(root):
        if r["status"] in ("sent", "received"):
            return r["id"]
    return None


# ------------------------------------------------------------------ 4. preview
def preview(lib: Library, rid: str) -> dict:
    req = _load(lib.root, rid)
    if req.get("kind") == "tidy":  # v3.4 하위 정리는 별도 미리보기
        import tidy
        return {"tidy": True, **tidy.preview(lib, rid)}
    out = os.path.join(_rdir(lib.root, rid), "out")
    plan_path = os.path.join(out, "plan.yaml")
    items, errors = [], []
    if not os.path.isfile(plan_path):
        return {"request": req, "items": [], "errors": ["아직 결과(plan.yaml)가 없습니다"], "summary": ""}
    try:
        with open(plan_path, encoding="utf-8") as f:
            plan = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        return {"request": req, "items": [], "errors": [f"plan.yaml 문법 오류: {e}"], "summary": ""}
    by_name = {f["name"]: f for f in req["files"]}
    by_orig = {os.path.basename(f["orig"]): f for f in req["files"]}
    assign_map = req.get("assign") or {}
    target = req.get("node")

    def tgt(x):
        x = str(x or "")
        return (target or "") if x == "@target" else x
    lib_hashes = library_hashes(lib)
    created_ids: dict[str, str] = {}

    for i, c in enumerate(plan.get("create") or []):
        path = str(c.get("path") or "").replace("\\", "/").lstrip("/")
        iid = f"c{i}"
        it = {"id": iid, "kind": "create", "path": path, "reason": c.get("reason", ""), "checked": True, "warnings": []}
        full = os.path.normpath(os.path.join(out, path))
        if ".." in path.split("/") or not os.path.isfile(full):
            it.update(checked=False, error=f"결과에 파일이 없습니다: {path}")
            items.append(it)
            continue
        with open(full, encoding="utf-8", errors="replace") as f:
            text = f.read()
        try:
            if path.endswith(".md"):
                m = FRONTMATTER.match(text.lstrip("﻿"))
                meta = yaml.safe_load(m.group(1)) if m else None
                if not meta or meta.get("type") != "rule":
                    raise ValueError("규칙 노드 프런트매터가 없습니다")
                it["node_type"] = "rule"
            else:
                meta = yaml.safe_load(text) or {}
                if meta.get("type") not in store.TYPE_FOLDER:
                    raise ValueError(f"type이 올바르지 않습니다: {meta.get('type')}")
                it["node_type"] = meta["type"]
        except Exception as e:
            it.update(checked=False, error=f"형식 오류: {e}")
            items.append(it)
            continue
        nid = str(meta.get("id") or "")
        it.update(node_id=nid, title=meta.get("title") or nid, summary=meta.get("summary", ""))
        if it["node_type"] == "rule":
            inh = meta.get("inherits") or []
            it["inherits"] = [inh] if isinstance(inh, str) else list(inh)
            use = meta.get("uses") or []
            it["uses"] = [use] if isinstance(use, str) else list(use)
            it["rule_kind"] = meta.get("kind", "set")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", nid):
            it["warnings"].append("id 형식이 규격과 다릅니다")
        if nid in lib.nodes:
            it.update(checked=False, error=f"id `{nid}`가 이미 있습니다")
        if it["node_type"] != "rule":
            fname = str(meta.get("file") or "")
            src = by_name.get(assign_map.get(path, "")) or by_name.get(fname) or by_orig.get(fname)
            it["file"] = fname
            it["output"] = meta.get("kind") == "output" or path.startswith("outputs/")
            if not src:  # v3.3: 원본 없이도 등록 가능, 나중에 채움
                it["missing_original"] = True
                it["warnings"].append("원본 없음: 파일을 이 창에 놓으면 짝을 맞춥니다 (없어도 설명만으로 등록됩니다)")
            else:
                it["src"] = src["name"]
                dup = src["existing"] or lib_hashes.get(src["sha"])
                if dup:  # Q54: AI가 지시를 어기고 중복 노드를 만든 경우
                    it.update(checked=False, duplicate_of=dup)
                    it["warnings"].append(f"같은 파일이 이미 `{dup}` 노드로 있습니다 (중복 노드)")
        created_ids[nid] = iid
        items.append(it)

    def ref_ok(v, deps):
        r = lib._ref_id(v)
        if not r:
            return True
        if r in created_ids:
            deps.add(created_ids[r])
            return True
        return r in lib.nodes

    for i, u in enumerate(plan.get("update") or []):
        nid = tgt(u.get("node"))
        n = lib.nodes.get(nid)
        for j, (key, val) in enumerate((u.get("set_rules") or {}).items()):
            iid = f"u{i}_{j}"
            it = {"id": iid, "node": nid, "key": str(key), "after": val, "reason": u.get("reason", ""), "warnings": []}
            deps: set = set()
            if not n or n["type"] != "rule":
                it.update(kind="add", checked=False, error=(f"규칙 노드 `{nid}`가 없습니다" if nid else
                                                              "대상 노드가 없습니다 (@target): zip을 세트 노드 위에 놓아 주세요"))
            elif not ref_ok(val, deps):
                it.update(kind="add", checked=False, error=f"없는 레퍼런스: {val}")
            else:
                before = (n["rules"].get(str(key)) or {}).get("value")
                impact = [nid] + lib.descendants(nid)
                if before is None:
                    it.update(kind="add", checked=True)
                elif before == val:
                    it.update(kind="same", checked=False)
                else:  # Q53-B: 덮어쓰기는 기본 꺼짐, 영향 범위 표시
                    it.update(kind="overwrite", checked=False, before=before, impact=impact)
                if str(key) not in lib.keys:
                    it["warnings"].append(f"키 어휘에 없는 키입니다: {key}")
                it["node_title"] = n["title"]
            it["depends_on"] = sorted(deps)
            items.append(it)

    for i, r in enumerate(plan.get("relations") or []):
        iid = f"r{i}"
        frm, to = tgt(r.get("from")), tgt(r.get("to"))
        deps = {created_ids[x] for x in (frm, to) if x in created_ids}
        it = {"id": iid, "kind": "relation", "from": frm, "to": to, "type": r.get("type", "similar_to"),
              "reason": r.get("reason", ""), "checked": True, "depends_on": sorted(deps), "warnings": []}
        for x in (frm, to):
            if x not in lib.nodes and x not in created_ids:
                it.update(checked=False, error=f"없는 노드: {x}")
        if frm in lib.nodes and lib.nodes[frm]["type"] == "rule" and frm not in created_ids:
            pass
        items.append(it)
    items += auto_links(lib, target, items)
    used = {i.get("src") for i in items if i.get("src")}
    unassigned = [f["name"] for f in req["files"] if f["name"] not in used and not f["existing"]]
    return {"request": req, "summary": plan.get("summary", ""), "items": items, "errors": errors,
            "unassigned": unassigned, "missing_declared": req.get("missing_declared") or []}


# ------------------------------------------------------------------ 5. apply
def apply(lib: Library, rid: str, selected: list[str]) -> dict:
    pv = preview(lib, rid)
    req = pv["request"]
    out = os.path.join(_rdir(lib.root, rid), "out")
    sel = set(selected)
    items = {it["id"]: it for it in pv["items"]}
    # 생성 항목을 빼면 그것에 딸린 항목도 빠진다
    changed = True
    while changed:
        changed = False
        for it in items.values():
            if it["id"] in sel and (it.get("error") or any(d not in sel for d in it.get("depends_on", []))):
                sel.discard(it["id"])
                changed = True
    store.backup_sync(lib)
    done = []
    for it in pv["items"]:
        if it["id"] not in sel or it["kind"] != "create":
            continue
        full = os.path.join(out, it["path"])
        if it["node_type"] == "rule":
            with open(full, "rb") as f:
                data = f.read()
            r = store._place_rule(lib, os.path.basename(it["path"]), data, it["path"])
            done.append(r["rel"])
            continue
        with open(full, encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
        if it.get("output"):
            meta["kind"] = "output"
            tn = lib.nodes.get(req.get("node") or "")
            if not meta.get("set") and tn and tn.get("kind") == "set":
                meta["set"] = req["node"]
            folder = f"outputs/{meta.get('set') or 'misc'}"
        else:
            folder = store.TYPE_FOLDER[meta["type"]]
        if it.get("src"):
            src = os.path.join(_rdir(lib.root, rid), "src", it["src"])
            dst = store._free_name(os.path.join(lib.root, folder, meta.get("file") or it["src"]))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            meta["file"] = os.path.basename(dst)
        else:  # 원본 없음: 설명만으로 등록
            meta["file"] = os.path.basename(str(meta.get("file") or (meta.get("id", "item") + ".png")))
            os.makedirs(os.path.join(lib.root, folder), exist_ok=True)
        meta.setdefault("authored_by", req.get("target"))
        meta_dst = store._free_name(os.path.join(lib.root, folder, os.path.splitext(meta["file"])[0] + ".meta.yaml"))
        with open(meta_dst, "w", encoding="utf-8") as f:
            yaml.safe_dump(meta, f, allow_unicode=True, sort_keys=False)
        done.append(os.path.relpath(meta_dst, lib.root).replace("\\", "/"))
    lib2 = Library(lib.root)  # 새 노드를 포함해 다시 읽고 키·연결 적용
    for it in pv["items"]:
        if it["id"] not in sel:
            continue
        if it["kind"] in ("add", "overwrite"):
            _set_rule(lib2, it["node"], it["key"], it["after"])  # it["node"]는 preview에서 @target이 풀린 값
            done.append(f"{it['node']}.{it['key']}")
        elif it["kind"] == "relation":
            _add_relation(lib2, it["from"], it["type"], it["to"])
            done.append(f"{it['from']} -{it['type']}-> {it['to']}")
        elif it["kind"] == "link" and it["node_id"] in lib2.nodes:
            if it["link"] == "inherits":
                def mut(fm, tgt=it["target"]):
                    inh = fm.get("inherits") or []
                    inh = [inh] if isinstance(inh, str) else [x for x in inh if x != "_defaults"]
                    if tgt not in inh:
                        inh.append(tgt)
                    fm["inherits"] = inh
                _rewrite_frontmatter(lib2.abspath(lib2.nodes[it["node_id"]]["path"]), mut)
            elif it["link"] in ("uses", "uses_target"):
                owner, part = ((it["target"], it["node_id"]) if it["link"] == "uses" else (it["node_id"], it["target"]))
                if owner in lib2.nodes:
                    def mut(fm, part=part):
                        use = fm.get("uses") or []
                        use = [use] if isinstance(use, str) else list(use)
                        if part not in use:
                            use.append(part)
                        fm["uses"] = use
                    _rewrite_frontmatter(lib2.abspath(lib2.nodes[owner]["path"]), mut)
            else:
                _add_relation(lib2, it["node_id"], it["link"], it["target"])
            done.append(f"{it['node_id']} -{it['link']}-> {it['target']}")
    req.update({"status": "applied", "applied": store.now(), "applied_items": sorted(sel)})
    _save(lib.root, req)
    return {"applied": done, "skipped": sorted(set(items) - sel)}


def discard(root: str, rid: str) -> None:
    shutil.rmtree(_rdir(root, rid), ignore_errors=True)


def _rewrite_frontmatter(path: str, mutate) -> None:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = FRONTMATTER.match(text.lstrip("﻿"))
    fm = yaml.safe_load(m.group(1)) or {}
    mutate(fm)
    body = m.group(2)
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n" + body)


def _set_rule(lib: Library, nid: str, key: str, value) -> None:
    n = lib.nodes[nid]

    def mut(fm):
        rules = fm.setdefault("rules", {}) or {}
        old = rules.get(key)
        if isinstance(old, dict) and not isinstance(value, dict):
            old["value"] = value  # note는 유지
        else:
            rules[key] = value
        fm["rules"] = rules
    _rewrite_frontmatter(lib.abspath(n["path"]), mut)


def _add_relation(lib: Library, frm: str, typ: str, to: str) -> None:
    n = lib.nodes[frm]
    rel = {"type": typ, "target": to}
    if n["type"] == "rule":
        _rewrite_frontmatter(lib.abspath(n["path"]), lambda fm: fm.setdefault("relations", []).append(rel)
                             if rel not in (fm.get("relations") or []) else None)
        return
    path = lib.abspath(n["path"])
    with open(path, encoding="utf-8") as f:
        meta = yaml.safe_load(f) or {}
    rels = meta.get("relations") or []
    if rel not in rels:
        rels.append(rel)
        meta["relations"] = rels
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(meta, f, allow_unicode=True, sort_keys=False)
