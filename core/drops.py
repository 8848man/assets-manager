"""예상 결과 창 (기획서 v3.3): 파일·폴더·zip을 놓으면 먼저 무슨 일이 일어날지 보여 주고, 고른 선택지로 실행한다.

stage(놓기)  → {sid, what, where, options[{id,label,desc,default}], details, simple}
commit(확정) → 고른 선택지 실행. 만든 파일 목록(created)을 돌려줘 "되돌리기"에 쓴다.

simple=True: 선택지가 사실상 하나이고 되돌리기 쉬운 경우. 설정에서 예상 결과 창을 끄면 이 경우만 창 없이 바로 실행된다.
"""
from __future__ import annotations

import os
import re
import shutil

import yaml

from engine import Library
import folder_requests as freq
import store

OPT = {
    "refs_only": ("레퍼런스로 등록", "파일을 라이브러리에 넣습니다. 그래프에 점선 원(미등록)으로 나타나고, 나중에 AI로 설명을 쓸 수 있습니다."),
    "ai_request": ("AI에게 맡기기", "AI가 파일을 보고 노드를 만들고 이 노드와 어떻게 연결할지 판단합니다. 결과는 적용 전에 다시 확인합니다."),
    "ai_new_set": ("새 세트의 재료로", "AI가 이 파일들로 새 세트를 만들고, 놓은 노드를 상속하게 합니다. 결과는 적용 전에 다시 확인합니다."),
    "fill_original": ("원본으로 채우기", "이 파일이 이 레퍼런스의 원본입니다. 설명은 그대로 두고 원본만 채웁니다."),
    "replace_original": ("원본 교체", "이 레퍼런스의 원본을 이 파일로 바꿉니다. 이전 원본은 버전으로 보관됩니다."),
    "variant": ("변형으로 추가", "이 레퍼런스의 다른 버전(다크 모드, 다른 크기 등)으로 새 노드를 만들고 '변형' 관계로 잇습니다."),
    "alternative": ("비슷한 대안으로 추가", "비슷하지만 다른 선택지로 새 노드를 만들고 '비슷함' 관계로 잇습니다."),
    "output": ("산출물로 기록", "이 세트로 만든 결과물로 기록합니다. 다음 작업 때 비슷한 작업이면 참고 예시로 자동으로 붙습니다."),
    "golden": ("골든 샘플로 지정", "이 세트의 완성형 기준으로 지정합니다. 이 세트로 드롭할 때 항상 함께 들어갑니다."),
    "import": ("그대로 가져오기", "zip 안의 파일들을 라이브러리에 넣습니다."),
    "apply": ("체크한 항목 적용", "AI가 만든 노드와 연결 중 체크한 것만 적용합니다."),
    "skill_import": ("스킬로 가져오기", "SKILL.md가 든 폴더를 통째로 라이브러리의 스킬로 보관하고 이 노드에 붙입니다. 원래 스킬은 꺼서 지침이 두 번 들어가지 않게 합니다."),
}


def _opt(oid: str, default: bool = False, **extra) -> dict:
    label, desc = OPT[oid]
    return {"id": oid, "label": label, "desc": desc, "default": default, **extra}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(text)[0].lower()).strip("-")
    return s or "item"


def _unique_id(lib: Library, base: str) -> str:
    nid, i = base, 2
    while nid in lib.nodes:
        nid = f"{base}-{i}"
        i += 1
    return nid


# ------------------------------------------------------------------ stage
def stage(lib: Library, node_id: str | None, paths: list[str] | None = None, uploads: list[dict] | None = None,
          folder_hint: bool = False) -> dict:
    paths = [p for p in paths or [] if not re.search(r"[\\/]\.assets[\\/]", p)]
    uploads = uploads or []
    if node_id and node_id.startswith("ai:"):
        node_id = None
    node = lib.nodes.get(node_id) if node_id else None
    where = {"node": node_id, "title": node["title"] if node else None,
             "kind": (node["kind"] if node else None), "type": node["type"] if node else None}

    # 1) zip
    zips = [(os.path.basename(p), open_bytes(p)) for p in paths if p.lower().endswith(".zip") and os.path.isfile(p)]
    zips += [(u["name"].split("/")[-1], u["data"]) for u in uploads if u["name"].lower().endswith(".zip")]
    others_p = [p for p in paths if not p.lower().endswith(".zip")]
    others_u = [u for u in uploads if not u["name"].lower().endswith(".zip")]
    if zips:
        name, data = zips[0]
        if freq.zip_has_plan(data):
            rid = freq.receive_zip(lib, data, node_id)
            if freq._load(lib.root, rid).get("kind") == "tidy":  # v3.4 하위 정리 결과
                import tidy
                tidy_req = freq._load(lib.root, rid)
                tidy_req["status"] = "received"
                freq._save(lib.root, tidy_req)
                return {"sid": rid, "case": "tidy", "what": {"zip": name}, "where": where, "options": [],
                        "details": tidy.preview(lib, rid), "simple": False}
            extra = [(os.path.basename(p), open_bytes(p)) for p in others_p if os.path.isfile(p)]
            extra += [(u["name"].split("/")[-1], u["data"]) for u in others_u]
            if extra:
                freq.attach(lib, rid, extra)
            else:
                freq.auto_assign(lib, rid)
            pv = freq.preview(lib, rid)
            return {"sid": rid, "case": "result", "what": {"zip": name, "files": len(extra)}, "where": where,
                    "options": [_opt("apply", True)], "details": pv, "simple": False}
        req = freq.new_request(lib, node_id, kind="drop")
        with open(os.path.join(lib.root, freq.INBOX, req["id"], "src", store._safe_name(name)), "wb") as f:
            f.write(data)
        req["zip"] = store._safe_name(name)
        freq._save(lib.root, req)
        return {"sid": req["id"], "case": "zip", "what": {"zip": name}, "where": where,
                "options": [_opt("import", True)], "details": {}, "simple": True}

    # 2) 파일·폴더
    has_folder = folder_hint or any(os.path.isdir(p) for p in others_p)
    skill_dirs = sorted({os.path.dirname(p) for p in others_p if os.path.basename(p) == "SKILL.md"}
                        | {p for p in others_p if os.path.isdir(p) and os.path.isfile(os.path.join(p, "SKILL.md"))})
    req = freq.prepare(lib, node_id, others_p, others_u)
    req["kind"] = "drop"
    freq._save(lib.root, req)
    n_files = len(req["files"])
    single = n_files == 1 and not has_folder
    opts: list[dict] = []
    details: dict = {"files": req["files"], "skipped": req["skipped"], "over_limit": req["over_limit"],
                     "recommended": req["recommended"]}
    kind = node["kind"] if node else None
    if not node or kind == "unregistered":
        opts = [_opt("refs_only", not has_folder), _opt("ai_request", has_folder)]
    elif node["type"] == "rule" and kind == "set":
        if single:
            drops = [h for h in store.read_history(lib.root) if h.get("node") == node_id and h.get("kind", "prompt") == "prompt"][-5:][::-1]
            details["recent_drops"] = drops
            cur = (node["rules"].get("golden_sample") or {}).get("value")
            details["golden_current"] = cur
            opts = [_opt("output", True), _opt("ai_request"), _opt("golden", overwrite=bool(cur))]
        else:
            opts = [_opt("ai_request", True), _opt("refs_only")]
    elif node["type"] == "rule":  # principle, defaults
        opts = [_opt("ai_new_set", True), _opt("refs_only")]
    else:  # reference, output
        if single:
            if not node.get("exists"):
                opts = [_opt("fill_original", True), _opt("variant"), _opt("alternative"), _opt("ai_request")]
            else:
                opts = [_opt("variant", True), _opt("replace_original"), _opt("alternative"), _opt("ai_request")]
        else:
            opts = [_opt("ai_request", True), _opt("refs_only")]
    if skill_dirs and (not node or node["type"] == "rule"):  # v3.7 스킬 폴더
        req["skill_dirs"] = skill_dirs
        freq._save(lib.root, req)
        details["skill_dirs"] = skill_dirs
        opts = [_opt("skill_import", True)] + [o for o in opts if o["id"] != "skill_import"]
    simple = (not node) and not has_folder
    return {"sid": req["id"], "case": "files", "what": {"files": n_files, "folder": has_folder,
                                                       "types": sorted({f["type"] for f in req["files"]})},
            "where": where, "options": opts, "details": details, "simple": simple}


def open_bytes(p: str) -> bytes:
    with open(p, "rb") as f:
        return f.read()


# ------------------------------------------------------------------ commit
def commit(lib: Library, sid: str, option: str, params: dict | None = None) -> dict:
    params = params or {}
    req = freq._load(lib.root, sid)
    d = freq._rdir(lib.root, sid)
    node = lib.nodes.get(req.get("node")) if req.get("node") else None
    created: list[str] = []
    store.backup_sync(lib)  # 바꾸기 전 상태를 보관 (되돌리기용)

    if option in ("ai_request", "ai_new_set"):
        req["kind"] = "folder"
        if option == "ai_new_set":
            req["recommended"] = freq.RECOMMENDED["principle"]
        freq._save(lib.root, req)
        return {"next": "request", "request": req}
    if option == "apply":
        res = freq.apply(lib, sid, params.get("selected") or [])
        return {"applied": res["applied"], "skipped": res["skipped"], "created": res["applied"]}
    if option == "import":
        with open(os.path.join(d, "src", req["zip"]), "rb") as f:
            res = store.import_zip(lib, f.read())
        freq.discard(lib.root, sid)
        return {"results": res, "created": [r["rel"] for r in res if r.get("status") == "added"]}

    if option == "skill_import":
        import skills
        made = [skills.import_skill(lib, sd, req.get("node") if node and node["type"] == "rule" else None,
                                    params.get("disable_original", True)) for sd in req.get("skill_dirs") or []]
        freq.discard(lib.root, sid)
        return {"created": [m["path"] for m in made], "skills": made,
                "message": f"스킬 {len(made)}개를 가져왔습니다" + (" (원래 스킬은 껐습니다)" if any(m["disabled_original"] for m in made) else "")}
    srcs = [(f, os.path.join(d, "src", f["name"])) for f in req["files"]]
    if option == "refs_only":
        for f, p in srcs:
            r = store._place_original(lib, f["name"], open_bytes(p))
            if r.get("status") == "added":
                created.append(r["rel"])
        freq.discard(lib.root, sid)
        return {"created": created, "message": f"{len(created)}개를 레퍼런스로 넣었습니다"}

    if not srcs:
        raise ValueError("놓은 파일이 없습니다")
    f, src = srcs[0]
    if option in ("fill_original", "replace_original"):
        folder = os.path.dirname(node["path"])
        old = lib.abspath(node["file"])
        new_name = node["file"].split("/")[-1]
        if os.path.splitext(new_name)[1].lower() != os.path.splitext(f["name"])[1].lower():
            new_name = os.path.splitext(new_name)[0] + os.path.splitext(f["name"])[1].lower()
        dst = os.path.join(lib.root, folder, new_name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        if os.path.abspath(dst) != os.path.abspath(old) and os.path.exists(old):
            os.remove(old)
        _edit_meta(lib, node, lambda m: m.update(file=new_name, extracted=f.get("extracted") or m.get("extracted")))
        freq.discard(lib.root, sid)
        return {"created": [], "modified": [node["path"]], "message": "원본을 채웠습니다" if option == "fill_original" else "원본을 교체했습니다 (이전 원본은 버전 보관)"}

    if option in ("variant", "alternative"):
        r = store._place_original(lib, f["name"], open_bytes(src))
        rel = r["rel"]
        nid = _unique_id(lib, _slug(f["name"]))
        typ = store.EXT_TYPE.get(os.path.splitext(rel)[1].lower(), "image")
        meta = {"id": nid, "type": typ, "title": f"{node['title']} {'변형' if option == 'variant' else '대안'}",
                "file": os.path.basename(rel), "summary": "", "spec": "", "tags": list(node.get("tags") or []),
                "sensitivity": node.get("sensitivity", "public"),
                "relations": [{"type": "variant_of" if option == "variant" else "similar_to", "target": node["id"]}],
                "extracted": f.get("extracted") or {}}
        mp = _write_meta(lib, rel, meta)
        freq.discard(lib.root, sid)
        return {"created": [rel, mp], "node": nid, "message": "새 노드를 만들었습니다. 설명은 'AI로 설명 쓰기'로 채울 수 있습니다"}

    if option in ("output", "golden"):
        set_id = node["id"]
        folder = f"outputs/{set_id}" if option == "output" else "golden"
        dst = store._free_name(os.path.join(lib.root, folder, f["name"]))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        rel = os.path.relpath(dst, lib.root).replace("\\", "/")
        nid = _unique_id(lib, f"{set_id}-{'out' if option == 'output' else 'golden'}-{_slug(f['name'])}")
        drop = next((h for h in store.read_history(lib.root) if h.get("ts") == params.get("drop_ts")), None) if params.get("drop_ts") else None
        meta = {"id": nid, "type": f["type"], "title": params.get("title") or os.path.splitext(f["orig"].split("/")[-1])[0],
                "file": os.path.basename(dst), "summary": params.get("summary", ""), "spec": "", "tags": [],
                "sensitivity": node.get("sensitivity", "public"), "extracted": f.get("extracted") or {}}
        if option == "output":
            meta.update(kind="output", set=set_id, task=(drop or {}).get("task") or params.get("task", ""),
                        keywords=[], origin={k: drop.get(k) for k in ("ts", "target", "prompt_hash", "method")} if drop else {})
            if drop and drop.get("target"):
                meta["authored_by"] = drop["target"]
        mp = _write_meta(lib, rel, meta)
        created += [rel, mp]
        msg = "산출물로 기록했습니다"
        if option == "golden":
            cur = (node["rules"].get("golden_sample") or {}).get("value")
            if cur and not params.get("overwrite"):
                msg = f"골든 샘플 후보로 등록했습니다. 기존 기준({cur})은 그대로입니다"
            else:
                freq._set_rule(Library(lib.root), set_id, "golden_sample", f"ref:{nid}")
                msg = "골든 샘플로 지정했습니다"
        freq.discard(lib.root, sid)
        return {"created": created, "node": nid, "message": msg}
    raise ValueError(f"알 수 없는 선택지: {option}")


def _write_meta(lib: Library, rel: str, meta: dict) -> str:
    mp = os.path.splitext(rel)[0] + ".meta.yaml"
    mp = os.path.relpath(store._free_name(lib.abspath(mp)), lib.root).replace("\\", "/")
    with open(lib.abspath(mp), "w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, allow_unicode=True, sort_keys=False)
    return mp


def _edit_meta(lib: Library, node: dict, mutate) -> None:
    path = lib.abspath(node["path"])
    with open(path, encoding="utf-8") as f:
        meta = yaml.safe_load(f) or {}
    mutate(meta)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, allow_unicode=True, sort_keys=False)


def promote(lib: Library, nid: str, overwrite: bool = False) -> dict:
    """산출물 → 그 세트의 골든 샘플 (v3.2 13.3). 기존 기준이 있으면 overwrite가 있어야 바꾼다."""
    n = lib.nodes[nid]
    set_id = n.get("set")
    if not set_id or set_id not in lib.nodes:
        raise ValueError("이 산출물이 속한 세트를 찾을 수 없습니다")
    cur = (lib.nodes[set_id]["rules"].get("golden_sample") or {}).get("value")
    if cur and cur != f"ref:{nid}" and not overwrite:
        return {"needs_overwrite": True, "current": cur, "set": set_id,
                "impact": [set_id] + lib.descendants(set_id)}
    store.backup_sync(lib)
    freq._set_rule(lib, set_id, "golden_sample", f"ref:{nid}")
    return {"promoted": nid, "set": set_id}


def undo(lib: Library, created: list[str]) -> dict:
    """방금 만든 파일들을 휴지통으로 옮긴다 (알림의 '되돌리기')."""
    files = [r for r in created if os.path.isfile(lib.abspath(r))]
    if not files:
        return {"trash_id": None, "files": []}
    tid = f"{store._stamp()}_undo"
    tdir = os.path.join(lib.root, store.HISTORY, "trash", tid)
    for rel in files:
        dst = os.path.join(tdir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(lib.abspath(rel), dst)
    import json
    with open(os.path.join(tdir, "trash.json"), "w", encoding="utf-8") as f:
        json.dump({"id": tid, "node": "되돌리기", "title": "되돌리기", "files": files, "ts": store.now()}, f, ensure_ascii=False)
    return {"trash_id": tid, "files": files}
