"""Assets Manager 코어 엔진 (기획서 v2, 1단계 MVP).

- 로더: 레퍼런스 노드(*.meta.yaml)와 규칙 노드(md 프런트매터)를 하나의 모델로 읽는다.
- 조립: 다중 상속을 결정적으로 펼치고, 키 단위로 덮어쓰며 출처를 기록한다.
- 패키지: 대상 앱별 한도와 민감도를 검사하고, 자동 덜어내기(spec 대체 → 컨택트 시트 → 키 제외)를 수행한다.
- 변경 통제: 조립 결과 스냅숏과 비교해 diff를 만든다.

AI 호출은 하나도 없다. 같은 폴더 상태면 결과는 항상 같다.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
from typing import Any

import yaml

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLED_DEFAULTS = os.path.join(APP_DIR, "defaults")
SKIP_DIRS = {"app", "history", "inbox", "node_modules", "__pycache__"}
REF_FOLDERS = {"image": "image", "web": "web", "document": "document", "writing": "writing", "golden": None, "outputs": None}
EXT_TYPE = {".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
            ".html": "web", ".htm": "web", ".css": "web", ".pdf": "document", ".txt": "writing", ".md": "writing"}
NEW_SECONDS = 24 * 3600
REF_TYPES = {"image", "web", "document", "writing", "skill"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PRIORITY_ORDER = {"example": -1, "optional": 0, "recommended": 1, "required": 2}
CACHE_DIR = ".assets"
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


GENERATED = {"index.md", "graph.json", "layout.json"}  # 앱이 쓰는 파일 (변경 감지에서 뺀다)
# v3.9 구성 요소 분리 (Q81-A): 레퍼런스 이미지의 캐릭터·도형·구도. keys.yaml의 parts:가 있으면 그쪽이 우선
PART_DEFAULTS = {
    "character": {"label": "캐릭터", "key": "character_style",
                  "ref_instruction": "캐릭터는 첨부 [{n}] {file}(원본에서 잘라 낸 캐릭터)을 보고 얼굴 표현, 머리와 몸의 비례, "
                                     "팔다리 모양, 외곽선 굵기를 그대로 따르세요. 자세와 소품은 장면에 맞게 바꿔도 됩니다.",
                  "check": "캐릭터의 얼굴 표현·비례·외곽선이 첨부 [{n}]의 캐릭터와 같은가"},
    "shape": {"label": "도형", "key": "shape_style",
              "ref_instruction": "도형(블록, 상자, 아이콘 등)은 첨부 [{n}] {file}의 모양, 모서리 둥글기, 외곽선, 면 채색, "
                                 "라벨을 넣는 위치를 따르세요.",
              "check": "도형의 모양·모서리·외곽선이 첨부 [{n}]의 도형과 같은가"},
    "composition": {"label": "구도", "key": "composition",
                    "ref_instruction": "배치와 구도는 첨부 [{n}] {file}(원본 위에 덩어리 위치와 흐름 방향을 그린 도식)을 따르세요.",
                    "check": "덩어리 배치와 흐름 방향이 첨부 [{n}]의 구도 도식과 같은가"},
}


def library_signature(root: str) -> str:
    """로더가 읽는 파일들의 (경로, 크기, 수정 시각) 요약. 바뀌면 그래프를 다시 읽는다 (v3.8 실시간 반영·캐시).
    결정적이다: 정렬된 순회, 시각은 파일 메타데이터만 쓴다."""
    h = hashlib.sha1()
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if dirpath == root and fn in GENERATED:
                continue
            try:
                st = os.stat(os.path.join(dirpath, fn))
            except OSError:
                continue
            h.update(f"{os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, '/')}|{st.st_size}|{st.st_mtime_ns}\n".encode("utf-8"))
    return h.hexdigest()[:16]


class Library:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.load()

    # ------------------------------------------------------------------ 로더
    def ensure_defaults(self) -> None:
        """레이어 0: _defaults가 없으면 앱 번들에서 복사한다 (Q17-B)."""
        dst = os.path.join(self.root, "_defaults")
        if os.path.isdir(BUNDLED_DEFAULTS):
            os.makedirs(dst, exist_ok=True)
            for name in os.listdir(BUNDLED_DEFAULTS):
                if not os.path.exists(os.path.join(dst, name)):
                    shutil.copy2(os.path.join(BUNDLED_DEFAULTS, name), os.path.join(dst, name))

    def load(self) -> None:
        self.ensure_defaults()
        self.nodes: dict[str, dict] = {}
        self.issues: list[dict] = []  # {level: error|warning, node, message}
        self.keys = self._read_yaml("_defaults/keys.yaml").get("keys", {}) or {}
        user_parts = self._read_yaml("_defaults/keys.yaml").get("parts") or {}
        self.parts = {k: dict(v, **(user_parts.get(k) or {})) for k, v in PART_DEFAULTS.items()}
        self.parts.update({k: dict(v) for k, v in user_parts.items() if k not in self.parts and isinstance(v, dict)})
        self.targets = self._read_yaml("_defaults/targets.yaml").get("targets", {}) or {}
        self.defaults_version = {
            "keys": self._read_yaml("_defaults/keys.yaml").get("version"),
            "targets": self._read_yaml("_defaults/targets.yaml").get("version"),
        }

        candidates: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            rel_dir = os.path.relpath(dirpath, self.root).replace("\\", "/")  # Windows: 항상 / 로 비교
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
            if rel_dir == ".":
                rel_dir = ""
            for fn in sorted(filenames):
                path = os.path.join(dirpath, fn)
                rel = os.path.join(rel_dir, fn).replace("\\", "/")
                try:
                    if fn == "SKILL.md" and rel_dir.split("/")[0] == "skills" and rel_dir.count("/") == 1:
                        self._add_skill(rel, path)  # v3.7 라이브러리에 보관한 스킬
                    elif rel_dir.split("/")[0] == "skills":
                        pass  # 스킬의 부속 파일은 노드가 아님
                    elif fn.endswith(".meta.yaml"):
                        self._add_ref(rel, path)
                    elif fn.endswith(".md") and self._add_rule(rel, path):
                        pass
                    elif rel_dir.split("/")[0] in REF_FOLDERS and not fn.startswith("."):
                        candidates.append(rel)
                except Exception as e:  # 파일 하나가 깨져도 나머지는 읽는다
                    self.issues.append({"level": "error", "node": rel, "message": f"읽기 실패: {e}"})
        self._add_unregistered(candidates)
        self._validate()

    def _add_skill(self, rel: str, path: str) -> None:
        """skills/<이름>/SKILL.md → 스킬 노드 (v3.7, Q78-A). 폴더째 보관, 조립 때는 이름·설명·경로만."""
        with open(path, encoding="utf-8") as f:
            text = f.read().lstrip("\ufeff")
        m = FRONTMATTER.match(text)
        fm = (yaml.safe_load(m.group(1)) or {}) if m else {}
        if not isinstance(fm, dict):
            fm = {}
        folder = rel.split("/")[1]
        name = str(fm.get("name") or folder)
        nid = folder
        if nid in self.nodes:
            nid = folder + "-skill"
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        d = os.path.dirname(path)
        files = []
        for dp, dns, fns in os.walk(d):
            dns[:] = sorted(x for x in dns if not x.startswith("."))
            for fn in sorted(fns):
                if not fn.startswith("."):
                    files.append(os.path.relpath(os.path.join(dp, fn), d).replace("\\", "/"))
        origin = {}
        op = os.path.join(d, ".assets-manager.yaml")
        if os.path.exists(op):
            with open(op, encoding="utf-8") as f:
                origin = yaml.safe_load(f) or {}
        desc = str(fm.get("description") or "").strip()
        self._register({
            "id": nid, "type": "skill", "kind": "skill", "title": str(meta.get("title") or name), "name": name,
            "path": rel, "file": rel, "dir": os.path.dirname(rel), "description": desc, "summary": desc[:200],
            "spec": "", "tags": [], "keywords": [], "sensitivity": str(meta.get("sensitivity") or "public"),
            "relations": [], "source": "", "check": [], "authored_by": None, "extracted": {}, "exists": True,
            "size": os.path.getsize(path), "mtime": os.path.getmtime(path), "files": files, "origin": origin,
            "body": (m.group(2) if m else text).strip(),
        })

    def _add_unregistered(self, candidates: list[str]) -> None:
        """레퍼런스 폴더에 있지만 .meta.yaml이 없는 파일 → 미등록 노드 (v3 4.1)."""
        used = {n.get("file") for n in self.nodes.values()}
        for rel in candidates:
            if rel in used:
                continue
            ext = os.path.splitext(rel)[1].lower()
            folder_type = REF_FOLDERS.get(rel.split("/")[0])
            t = folder_type or EXT_TYPE.get(ext)
            if not t:
                continue
            path = self.abspath(rel)
            self.nodes["new:" + rel] = {
                "id": "new:" + rel, "type": t, "kind": "unregistered", "title": os.path.basename(rel),
                "path": None, "file": rel, "summary": "", "spec": "", "tags": [], "sensitivity": "public",
                "relations": [], "source": "", "check": [], "authored_by": None, "extracted": {},
                "exists": True, "size": os.path.getsize(path), "mtime": os.path.getmtime(path),
            }

    def _read_yaml(self, rel: str) -> dict:
        path = os.path.join(self.root, rel)
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _register(self, node: dict) -> None:
        nid = node["id"]
        if nid in self.nodes:
            self.issues.append({"level": "error", "node": nid,
                                "message": f"id 중복: {self.nodes[nid]['path']} 와 {node['path']}"})
            return
        self.nodes[nid] = node

    def _add_ref(self, rel: str, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
        if meta.get("type") not in REF_TYPES:
            return
        folder = os.path.dirname(rel)
        file_rel = (folder + "/" if folder else "") + str(meta.get("file", ""))
        is_output = meta.get("kind") == "output" or rel.startswith("outputs/")
        node = {
            "id": str(meta.get("id") or os.path.basename(rel)[: -len(".meta.yaml")]),
            "type": meta["type"],
            "kind": "output" if is_output else "reference",
            "set": meta.get("set"),
            "task": meta.get("task") or "",
            "keywords": _as_list(meta.get("keywords")),
            "origin": meta.get("origin") or {},
            "title": meta.get("title") or meta.get("id"),
            "path": rel,
            "file": file_rel,
            "summary": meta.get("summary", ""),
            "spec": (meta.get("spec") or "").strip(),
            "tags": meta.get("tags") or [],
            "sensitivity": meta.get("sensitivity", "public"),
            "relations": meta.get("relations") or [],
            "source": meta.get("source", ""),
            "check": _as_list(meta.get("check")),
            "authored_by": meta.get("authored_by"),
            "extracted": meta.get("extracted") or {},
        }
        import components as comps_mod  # v3.9 구성 요소 (Q81-A)
        ex = node["extracted"]
        size = (int(ex["width"]), int(ex["height"])) if ex.get("width") and ex.get("height") else None
        node["components"], problems = comps_mod.normalize(meta.get("components"), self.parts, size)
        for c in node["components"]:
            c["file"] = comps_mod.part_rel(node["id"], c["name"])
        for p in problems:
            self.issues.append({"level": "warning", "node": node["id"], "message": f"구성 요소: {p}"})
        abs_file = os.path.join(self.root, file_rel)
        node["exists"] = os.path.isfile(abs_file)
        node["size"] = os.path.getsize(abs_file) if node["exists"] else 0
        if not node["exists"]:  # v3.3: 원본 없는 노드도 허용 (설명만 있음, 드롭 때 spec 글로 대체)
            self.issues.append({"level": "warning", "node": node["id"],
                                "message": "원본 없음: 파일을 이 노드에 끌어다 놓으면 채울 수 있습니다"})
        elif node["size"] == 0:
            self.issues.append({"level": "warning", "node": node["id"], "message": "원본 파일이 비어 있음"})
        node["mtime"] = os.path.getmtime(path)
        self._register(node)

    def _add_rule(self, rel: str, path: str) -> bool | None:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = FRONTMATTER.match(text.lstrip("﻿"))
        if not m:
            return
        fm = yaml.safe_load(m.group(1)) or {}
        if fm.get("type") != "rule":
            return
        nid = str(fm.get("id") or os.path.splitext(os.path.basename(rel))[0])
        inherits = fm.get("inherits")
        implicit = inherits is None
        if inherits is None:
            inherits = [] if nid == "_defaults" else ["_defaults"]
        elif isinstance(inherits, str):
            inherits = [inherits]
        rules = {}
        for key, raw in (fm.get("rules") or {}).items():
            if isinstance(raw, dict):
                rules[str(key)] = {"value": raw.get("value"), "note": raw.get("note")}
            else:
                rules[str(key)] = {"value": raw, "note": None}
        node = {
            "id": nid,
            "type": "rule",
            "kind": fm.get("kind", "set"),
            "title": fm.get("title") or nid,
            "path": rel,
            "inherits": [str(i) for i in inherits],
            "implicit_inherits": implicit,
            "uses": [str(u) for u in _as_list(fm.get("uses"))],  # v3.4 부품 원칙 (Q72-A)
            "rules": rules,
            "priorities": fm.get("priorities") or {},
            "precedence": fm.get("precedence") or [],
            "authored_by": fm.get("authored_by"),
            "sensitivity": fm.get("sensitivity", "public"),
            "relations": fm.get("relations") or [],
            "gate": fm.get("gate"),  # v3.4 승인 게이트: true | false | 없음(상속)
            "body": m.group(2).strip(),
            "version": fm.get("version"),
        }
        node["mtime"] = os.path.getmtime(path)
        self._register(node)
        return True

    def _validate(self) -> None:
        alias_map = {}
        for k, spec in self.keys.items():
            for a in spec.get("aliases") or []:
                alias_map[str(a).lower()] = k
        for n in self.rule_nodes():
            for p in n["inherits"]:
                if p not in self.nodes or self.nodes[p]["type"] != "rule":
                    self.issues.append({"level": "error", "node": n["id"], "message": f"상속 대상 없음: {p}"})
            for u in n.get("uses") or []:
                if self.nodes.get(u, {}).get("type") == "skill":
                    continue  # 스킬도 부품으로 붙일 수 있다 (v3.7)
                if u not in self.nodes or self.nodes[u]["type"] != "rule":
                    self.issues.append({"level": "error", "node": n["id"], "message": f"원칙(uses) 대상 없음: {u}"})
                elif self.nodes[u]["kind"] != "principle":
                    self.issues.append({"level": "warning", "node": n["id"],
                                        "message": f"uses는 원칙 노드를 붙이는 곳입니다: {u}({self.nodes[u]['kind']})"})
            for key, r in n["rules"].items():
                if key not in self.keys:
                    canon = alias_map.get(key.lower())
                    msg = (f"사용자 정의 키 '{key}'는 기본 키 '{canon}'와 같은 뜻으로 보입니다. 합칠까요?"
                           if canon else f"사용자 정의 키 '{key}' (기본 키 목록에 없음)")
                    self.issues.append({"level": "warning", "node": n["id"], "message": msg,
                                        "key": key, "suggest": canon})
                ref = self._ref_id(r["value"])
                if ref and ref not in self.nodes:
                    self.issues.append({"level": "error", "node": n["id"], "message": f"{key}: 레퍼런스 없음 ref:{ref}"})
                part = self._ref_part(r["value"])
                if ref in self.nodes and part and part not in {c["name"] for c in self.nodes[ref].get("components") or []}:
                    self.issues.append({"level": "error", "node": n["id"],
                                        "message": f"{key}: 구성 요소 없음 ref:{ref}#{part}"})
        for n in self.nodes.values():
            for rel in n.get("relations") or []:
                if rel.get("target") not in self.nodes:
                    self.issues.append({"level": "warning", "node": n["id"],
                                        "message": f"정보 엣지 대상 없음: {rel.get('target')}"})
        # 순환 검사 (Q13)
        self.cyclic: set[str] = set()
        for n in self.rule_nodes():
            try:
                self.sources(n["id"])
            except CycleError as e:
                self.cyclic.add(n["id"])
                self.issues.append({"level": "error", "node": n["id"], "message": str(e)})

    # ------------------------------------------------------------------ 조회
    def rule_nodes(self) -> list[dict]:
        return [n for n in self.nodes.values() if n["type"] == "rule"]

    @staticmethod
    def _ref_id(value: Any) -> str | None:
        """`ref:<id>` 또는 `ref:<id>#<요소>`(v3.9)의 노드 id."""
        if isinstance(value, str) and value.startswith("ref:"):
            return value[4:].split("#", 1)[0].strip()
        return None

    @staticmethod
    def _ref_part(value: Any) -> str | None:
        """`ref:<id>#<요소>`의 요소 이름 (없으면 None)."""
        if isinstance(value, str) and value.startswith("ref:") and "#" in value:
            return value.split("#", 1)[1].strip() or None
        return None

    def _ensure_components(self, rn: dict) -> bool:
        try:
            import components as comps_mod
            return comps_mod.ensure(self, rn)
        except Exception:
            return False

    def abspath(self, rel: str) -> str:
        return os.path.normpath(os.path.join(self.root, rel))

    def linearize(self, nid: str, _stack: tuple = ()) -> list[str]:
        """상속 순서. 부모를 선언 순서대로 펼치고(중복은 처음 위치 유지), 자신이 마지막. 뒤에 올수록 우선."""
        if nid in _stack:
            raise CycleError("순환 상속: " + " → ".join(_stack + (nid,)))
        node = self.nodes.get(nid)
        if not node or node["type"] != "rule":
            return []
        order: list[str] = []
        for p in node["inherits"]:
            for x in self.linearize(p, _stack + (nid,)):
                if x not in order:
                    order.append(x)
        order.append(nid)
        return order

    def components(self, nid: str, _stack: tuple = ()) -> list[str]:
        """nid가 uses로 붙인 원칙들(부품). 부품의 uses도 펼친다. 선언 순서, 중복은 처음 위치."""
        if nid in _stack:
            raise CycleError("순환 uses: " + " → ".join(_stack + (nid,)))
        out: list[str] = []
        for u in (self.nodes.get(nid) or {}).get("uses") or []:
            if u not in self.nodes or self.nodes[u]["type"] != "rule":
                continue
            for x in self.components(u, _stack + (nid,)) + [u]:
                if x not in out:
                    out.append(x)
        return out

    def sources(self, nid: str) -> list[str]:
        """조립 순서 (v3.4): 상속 계층을 펼치고, 각 계층 바로 앞에 그 계층이 붙인 원칙(부품)을 끼운다.
        뒤가 우선 → 계층 자신의 규칙 > 그 계층의 원칙(나중에 붙인 것 우선) > 윗 계층."""
        order: list[str] = []
        for layer in self.linearize(nid):
            for x in self.components(layer) + [layer]:
                if x not in order:
                    order.append(x)
        return order

    def skills_of(self, nid: str) -> list[str]:
        """조립 순서대로, 각 노드가 uses로 붙인 스킬 (v3.7). 중복은 처음 위치."""
        out: list[str] = []
        for src in self.sources(nid):
            for u in self.nodes[src].get("uses") or []:
                if self.nodes.get(u, {}).get("type") == "skill" and u not in out:
                    out.append(u)
        return out

    def users(self, nid: str) -> list[str]:
        """nid를 uses로 직접 붙인 규칙 노드."""
        return sorted(n["id"] for n in self.rule_nodes() if nid in (n.get("uses") or []))

    def descendants(self, nid: str) -> list[str]:
        """영향 범위: nid를 상속하거나 부품으로 쓰는 모든 규칙 노드 (Q12, v3.4)."""
        out = []
        for n in self.rule_nodes():
            if n["id"] != nid and n["id"] not in self.cyclic and (nid in self.sources(n["id"]) or nid in self.skills_of(n["id"])):
                out.append(n["id"])
        return out

    def key_spec(self, key: str) -> dict:
        return self.keys.get(key) or {"label": key, "priority": "optional", "domain": "custom"}

    # ------------------------------------------------------------------ 조립
    def assemble(self, nid: str) -> dict:
        if nid not in self.nodes or self.nodes[nid]["type"] != "rule":
            raise KeyError(f"규칙 노드가 아님: {nid}")
        if nid in self.cyclic:
            raise CycleError(f"순환 상속 때문에 조립할 수 없음: {nid}")
        chain = self.sources(nid)
        final: dict[str, dict] = {}
        history: dict[str, list] = {}
        priorities: dict[str, str] = {}
        for src in chain:
            n = self.nodes[src]
            for key, r in n["rules"].items():
                if key in final:
                    history.setdefault(key, []).append({"source": final[key]["source"], "value": final[key]["value"]})
                final[key] = {"value": r["value"], "note": r["note"], "source": src}
            for key, p in (n.get("priorities") or {}).items():
                priorities[key] = p

        key_order = list(self.keys.keys())
        ordered = sorted(final.keys(), key=lambda k: (key_order.index(k) if k in key_order else len(key_order), k))
        entries = []
        for key in ordered:
            f = final[key]
            spec = self.key_spec(key)
            ref = self._ref_id(f["value"])
            e = {
                "key": key,
                "label": spec.get("label", key),
                "priority": priorities.get(key, spec.get("priority", "optional")),
                "value": f["value"],
                "note": f["note"],
                "source": f["source"],
                "from_defaults": self.nodes[f["source"]].get("kind") == "defaults",
                "overridden": history.get(key, []),
                "kind": "ref" if ref else "text",
                "status": "ok",
                "sensitivity": self.nodes[f["source"]].get("sensitivity", "public"),
            }
            if ref:
                rn = self.nodes.get(ref)
                e["ref"] = ref
                if not rn:
                    e["status"] = "missing"
                else:
                    e.update({
                        "ref_title": rn["title"], "ref_type": rn["type"], "ref_file": rn["file"],
                        "ref_abs": self.abspath(rn["file"]), "ref_size": rn["size"], "spec": rn["spec"],
                        "sensitivity": "internal" if "internal" in (rn["sensitivity"], e["sensitivity"]) else "public",
                    })
                    if not rn["exists"]:
                        e["status"] = "text_only" if rn["spec"] else "missing"
                    elif rn["size"] == 0:
                        e["status"] = "empty" if not rn["spec"] else "text_only"
                    self._attach_components(e, rn, self._ref_part(f["value"]))
            own = e.pop("_own_check", None)
            e["check"] = ([spec["check"]] if spec.get("check") else []) + (
                own if own is not None else (list(self.nodes[ref]["check"]) if ref and ref in self.nodes else []))
            entries.append(e)

        # 우선순위 (Q46~Q47): 키 어휘 기본값 → 상속 순서대로 세트가 덮어씀
        pairs: dict[frozenset, dict] = {}
        for k, spec in self.keys.items():
            for p in spec.get("precedence") or []:
                pairs[frozenset((k, p["over"]))] = {"first": k, "over": p["over"],
                                                    "scope": p.get("scope", ""), "source": "_defaults/keys.yaml"}
        for src in chain:
            for p in self.nodes[src].get("precedence") or []:
                if p.get("first") and p.get("over"):
                    pairs[frozenset((p["first"], p["over"]))] = {"first": p["first"], "over": p["over"],
                                                                "scope": p.get("scope", ""), "source": src}
        present = {e["key"] for e in entries}
        active = [p for p in pairs.values() if p["first"] in present and p["over"] in present]
        active.sort(key=lambda p: (key_order.index(p["first"]) if p["first"] in key_order else 999, p["over"]))
        # 첨부 순서: 우선하는 키가 먼저 오도록 안정 정렬
        rank = {k: 0 for k in present}
        for p in active:
            rank[p["first"]] += 1
        text_first = [e for e in entries if e["kind"] == "text"]
        refs_sorted = sorted([e for e in entries if e["kind"] == "ref"], key=lambda e: -rank[e["key"]])
        entries = sorted(text_first, key=lambda e: ordered.index(e["key"])) + refs_sorted
        gate = True  # 기본 켜짐. 조립 순서에서 마지막으로 정한 노드가 이긴다
        for src in chain:
            if self.nodes[src].get("gate") is not None:
                gate = bool(self.nodes[src]["gate"])
        return {"id": nid, "title": self.nodes[nid]["title"], "chain": chain, "entries": entries, "gate": gate,
                "precedence": active, "layers": self.linearize(nid), "uses": self.components(nid),
                "skills": [{"id": k, "name": self.nodes[k]["name"], "title": self.nodes[k]["title"],
                            "description": self.nodes[k]["description"], "abs": self.abspath(self.nodes[k]["path"]),
                            "dir_abs": self.abspath(self.nodes[k]["dir"]), "files": self.nodes[k]["files"],
                            "sensitivity": self.nodes[k]["sensitivity"]} for k in self.skills_of(nid)]}

    def _attach_components(self, e: dict, rn: dict, part: str | None) -> None:
        """v3.9 (Q81-A): `ref:<id>#<요소>`면 항목 자체를 그 요소로, `ref:<id>`이고 요소가 있으면 e["components"]."""
        comps = rn.get("components") or []
        if not comps and not part:
            return
        import components as comps_mod
        drawn = rn["exists"] and rn["size"] > 0 and self._ensure_components(rn)

        def status(c):
            return "ok" if drawn else ("text_only" if c["spec"] else "missing")

        if part:
            c = next((c for c in comps if c["name"] == part), None)
            if not c:
                e["status"] = "missing"
                return
            e.update(part=part, part_kind=c["part"], ref_title=f"{rn['title']} · {c['title']}", ref_file=c["file"],
                     ref_abs=self.abspath(c["file"]), spec=c["spec"] or rn["spec"], status=status(c),
                     legend=comps_mod.legend(c, comps) if c["part"] == "composition" else "",
                     ref_size=os.path.getsize(self.abspath(c["file"])) if drawn else 0, _own_check=list(c.get("check") or []))
            return
        e["components"] = [{
            "name": c["name"], "part": c["part"], "label": self.parts.get(c["part"], {}).get("label", c["part"]),
            "title": c["title"], "file": c["file"], "abs": self.abspath(c["file"]), "spec": c["spec"],
            "check": list(c.get("check") or []), "status": status(c), "pkey": self.parts.get(c["part"], {}).get("key"),
            "legend": comps_mod.legend(c, comps) if c["part"] == "composition" else "",
        } for c in comps]

    # ------------------------------------------------------------------ 렌더
    def render(self, asm: dict, mode: dict | None = None, paths: bool = False,
               closing: bool = True) -> tuple[str, list[dict]]:
        """조립 결과를 프롬프트로 바꾼다.
        mode[key] = 'attach' | 'text' | 'sheet' | 'drop' (기본 attach)
        순서: 규칙 → 우선순위 → 첨부 목록 → 출력 전 자가 점검 → (경로)
        반환: (프롬프트, 첨부 목록[{n, key, abs, file, slots?}])
        """
        mode = mode or {}
        lines = [f"# 작업 지침: {asm['title']}", "",
                 "아래 규칙과 첨부 레퍼런스를 함께 따르세요. 규칙이 첨부와 다르면 규칙을 우선합니다.", ""]
        if asm.get("task"):
            lines += ["## 이번 작업", asm["task"], ""]
        gate = closing and asm.get("gate", True)
        if gate:  # v3.4 (Q73-A): 절차는 앞에, 품질 기준(자가 점검)은 끝에
            lines += process_lines(bool(asm.get("task"))) + [""]
        lines += ["## 규칙"]
        attachments: list[dict] = []
        sheet_slots: list[dict] = []
        where: dict[str, str] = {}   # 키 → "첨부 [n]" 등 (우선순위 문장용)
        included: list[dict] = []
        comp_checks: list[str] = []
        comp_prec: list[tuple[str, str, str]] = []  # (규칙 키, 요소 라벨, 요소 위치)
        n = 1  # 첨부 레퍼런스 번호 (prompt.md 자신은 세지 않음)

        def emit_components(e: dict, pnum, force_text: bool) -> None:
            """v3.9 (Q81-A): 레퍼런스에서 분리한 요소를 그 항목 바로 아래에. 원본이 첨부면 [2-a], 아니면 새 번호."""
            nonlocal n
            letter = 0
            for c in e.get("components") or []:
                ck = f"{e['key']}#{c['name']}"
                cm = mode.get(ck, "attach")
                if cm == "drop":
                    continue
                if force_text or c["status"] != "ok":
                    cm = "text"
                if cm == "text" and not c["spec"]:
                    continue
                pt = self.parts.get(c["part"], {})
                leg = f" (도식 번호: {c['legend']})" if c.get("legend") and cm == "attach" else (
                    f" 덩어리: {c['legend']}." if c.get("legend") else "")
                if cm == "text":
                    lines.append(f"  - **{c['label']}** (분리한 요소 '{c['title']}'를 글로 옮긴 것): {c['spec']}{leg}")
                    cwhere = "(글로 옮긴 요소)"
                    comp_checks.extend(c["check"])
                else:
                    if isinstance(pnum, int):
                        cnum = f"{pnum}-{'abcdefghijklmnopqrstuvwxyz'[letter % 26]}"
                        letter += 1
                    else:
                        cnum = n
                        n += 1
                    fname = os.path.basename(c["file"])
                    attachments.append({"n": cnum, "key": ck, "abs": c["abs"], "file": fname,
                                        "title": f"{e['ref_title']} · {c['title']}", "component": True})
                    tmpl = pt.get("ref_instruction") or "{title}은 첨부 [{n}] {file}을 따르세요."
                    lines.append(f"  - **{c['label']}**: " + tmpl.format(n=cnum, file=fname, title=c["title"]) + leg
                                 + (f" 설명: {c['spec']}" if c["spec"] else ""))
                    cwhere = f"(첨부 [{cnum}])"
                    if pt.get("check"):
                        comp_checks.append(pt["check"].format(n=cnum))
                    comp_checks.extend(c["check"])
                if c.get("pkey") and c["pkey"] != e["key"]:
                    comp_prec.append((c["pkey"], c["label"], cwhere))

        for e in asm["entries"]:
            m = mode.get(e["key"], "attach")
            if m == "drop":
                continue
            spec = self.key_spec(e["key"])
            if e["kind"] == "text":
                tmpl = spec.get("instruction") or "{value}"
                lines.append(f"- **{e['label']}**: " + tmpl.format(value=e["value"]))
                included.append(e)
                continue
            if e["status"] in ("missing", "empty"):
                continue
            included.append(e)
            if m == "text" or e["status"] == "text_only":
                lines.append(f"- **{e['label']}** (레퍼런스 '{e['ref_title']}'를 글로 옮긴 것): "
                             + " ".join(e["spec"].split()) + (f" 덩어리: {e['legend']}." if e.get("legend") else ""))
                where[e["key"]] = "(글로 옮긴 레퍼런스)"
                emit_components(e, None, True)
                continue
            if m == "sheet":
                slot = len(sheet_slots) + 1
                sheet_slots.append({"key": e["key"], "abs": e["ref_abs"], "slot": slot, "title": e["ref_title"]})
                file_label = f"contact-sheet.png의 {slot}번 칸"
                num = "S"
                where[e["key"]] = f"(첨부 [S]의 {slot}번 칸)"
            else:
                num = n
                n += 1
                attachments.append({"n": num, "key": e["key"], "abs": e["ref_abs"],
                                    "file": os.path.basename(e["ref_file"]), "title": e["ref_title"]})
                file_label = os.path.basename(e["ref_file"])
                where[e["key"]] = f"(첨부 [{num}])"
            tmpl = e["note"] or spec.get("ref_instruction") or "첨부 [{n}] {file}를 참고하세요."
            lines.append(f"- **{e['label']}**: " + tmpl.format(n=num, file=file_label, title=e["ref_title"])
                         + (f" (도식 번호: {e['legend']})" if e.get("legend") else ""))
            emit_components(e, num, False)

        keys_in = {e["key"] for e in included}
        prec = [p for p in asm.get("precedence", []) if p["first"] in keys_in and p["over"] in keys_in]
        has_examples = any(k.startswith("_example_") for k in keys_in)
        cprec = []
        for pk, label, cw in comp_prec:  # v3.9: 같은 측면의 규칙이 있으면 규칙이 분리한 요소보다 우선
            if pk in keys_in and (pk, label, cw) not in cprec:
                cprec.append((pk, label, cw))
        if prec or has_examples or cprec:
            lines += ["", "## 우선순위 (규칙끼리 겹칠 때)"]
        if has_examples:
            lines.append("- 참고 예시는 참고용입니다. 골든 샘플이나 위 규칙과 다르면 **기준(골든 샘플과 규칙)을 따르세요.**")
        if prec:
            for p in prec:
                a, b = self.key_spec(p["first"]), self.key_spec(p["over"])
                lines.append(f"- {p['scope'] or '겹치는 부분'}에 관해서는 **{a.get('label', p['first'])}**"
                             f"{where.get(p['first'], '')}을(를) **{b.get('label', p['over'])}**"
                             f"{where.get(p['over'], '')}보다 우선합니다.")
        for pk, label, cw in cprec:
            lines.append(f"- {label}에 관해서는 **{self.key_spec(pk).get('label', pk)}** 규칙{where.get(pk, '')}을 "
                         f"분리한 요소{cw}보다 우선합니다. 규칙에 없는 부분은 요소를 따르세요.")
        if sheet_slots:
            attachments.append({"n": "S", "key": "_sheet", "abs": None, "file": "contact-sheet.png",
                                "title": "레퍼런스 이미지 모음", "slots": sheet_slots})
        if attachments:
            lines += ["", "## 첨부 레퍼런스"]
            for a in attachments:
                lines.append(f"- [{a['n']}] {a['file']} — {a['title']}")
        checks: list[str] = []
        for e in included:
            for c in e.get("check") or []:
                if c not in checks:
                    checks.append(c)
        for c in comp_checks:
            if c not in checks:
                checks.append(c)
        skills = [k for k in asm.get("skills") or [] if (mode or {}).get("_skill_" + k["id"]) != "drop"]
        if skills:  # v3.7 붙은 스킬: 이름·설명만, 본문은 필요할 때 읽게 (스킬처럼)
            lines += ["", "## 함께 쓰는 스킬",
                      "작업이 아래 스킬의 설명에 해당하면 그 스킬의 지침을 읽고 따르세요. 위 규칙과 다르면 **위 규칙이 우선**합니다."]
            for k in skills:
                if paths:
                    where_s = f"파일: {k['abs']} (부속 파일은 같은 폴더)"
                else:
                    sn = n
                    n += 1
                    attachments.append({"n": sn, "key": "_skill_" + k["id"], "abs": k["abs"], "file": f"{k['name']}.SKILL.md",
                                        "title": f"스킬 {k['title']}", "skill": True})
                    where_s = f"첨부 [{sn}] {k['name']}.SKILL.md"
                lines.append(f"- **{k['title']}** — {k['description'] or '(설명 없음)'} · {where_s}")
            checks.append("해당되는 스킬이 있었다면 그 스킬의 지침을 따랐는가")
        if gate:
            checks.append(GATE_CHECK)
        if checks:
            lines += ["", "## 출력 전 자가 점검", "결과를 내기 전에 아래 항목을 하나씩 확인하고, 어긋나면 고친 뒤 내세요."]
            lines += [f"- [ ] {c}" for c in checks]
        if attachments and paths:
            lines += ["", "## 파일 경로 (로컬 에이전트용, 직접 열어서 보세요)"]
            for a in attachments:
                lines.append(f"- [{a['n']}] {a['abs'] or '(contact-sheet.png는 함께 드롭된 파일)'}")
        if paths and asm.get("more_outputs"):  # Q61-C: 에이전트만 더 찾아볼 수 있음
            lines += ["", "## 더 찾아볼 수 있는 산출물 (에이전트용)",
                      "위 참고 예시로 부족하면 아래에서 이번 작업과 가까운 것을 골라 직접 열어 보세요. 기준은 여전히 골든 샘플과 규칙입니다."]
            for o in asm["more_outputs"]:
                lines.append(f"- `{o['id']}`{(' · ' + o['task']) if o['task'] else ''} · {o['abs']}")
        return "\n".join(lines) + "\n", attachments

    def canonical(self, nid: str) -> str:
        """스냅숏 비교용: 대상·덜어내기와 무관한 기본 렌더."""
        return self.render(self.assemble(nid), closing=False)[0]

    # ------------------------------------------------------------------ 패키지
    def add_examples(self, asm: dict, task: str, agent: bool = False, limit: int = 3) -> list[dict]:
        """v3.2 13.2: 작업 설명과 비슷한 과거 산출물을 '참고 예시'로 붙인다 (결정적 검색)."""
        import search
        asm["task"] = task.strip()
        if not asm["task"]:
            return []
        golden = {e.get("ref") for e in asm["entries"] if e["key"] == "golden_sample"}
        hits = search.search(self, asm["task"], kinds={"output"}, set_id=asm["id"], limit=limit, exclude=golden)
        added = []
        for i, h in enumerate(hits, 1):
            rn = self.nodes[h["id"]]
            e = {
                "key": f"_example_{i}", "label": f"참고 예시 {i}", "priority": "example", "value": f"ref:{rn['id']}",
                "note": ("첨부 [{n}] {file}는 이전에 확인된 산출물입니다" + (f"(그때 작업: '{rn['task']}')" if rn.get("task") else "")
                         + ". 형식과 완성도를 참고하되 내용은 이번 작업에 맞게 새로 쓰세요."),
                "source": asm["id"], "from_defaults": False, "overridden": [], "kind": "ref", "ref": rn["id"],
                "ref_title": rn["title"], "ref_type": rn["type"], "ref_file": rn["file"], "ref_abs": self.abspath(rn["file"]),
                "ref_size": rn["size"], "spec": rn["spec"], "sensitivity": rn["sensitivity"], "check": [],
                "status": "ok" if rn["exists"] and rn["size"] else ("text_only" if rn["spec"] else "missing"),
                "score": h["score"],
            }
            asm["entries"].append(e)
            added.append(e)
        if agent:
            outs = sorted([n for n in self.nodes.values() if n["kind"] == "output" and n.get("set") == asm["id"]],
                          key=lambda n: n["id"])
            asm["more_outputs"] = [{"id": n["id"], "task": n.get("task", ""), "abs": self.abspath(n["file"])} for n in outs[:20]]
        return added

    def package(self, nid: str, target: str, trim: bool = False, strip_sensitive: bool = False,
                trim_all_targets: bool = False, write: bool = True, task: str = "", gate: bool | None = None) -> dict:
        asm = self.assemble(nid)
        set_gate = asm["gate"]
        if gate is not None:  # 드롭 영역의 '승인 게이트' 토글이 세트 기본값을 이번만 덮어씀
            asm["gate"] = bool(gate)
        tgt = dict(self.targets.get(target) or {})
        self.add_examples(asm, task or "", agent=tgt.get("kind") == "agent")
        if not tgt:
            raise KeyError(f"알 수 없는 대상: {target}")
        if trim_all_targets and self.targets:  # 세 앱 모두 같은 기준: 가장 작은 한도
            for k in ("max_files", "max_file_mb", "max_chars"):
                tgt[k] = min(t.get(k, 10 ** 9) for t in self.targets.values())
        mode: dict[str, str] = {}
        log: list[str] = []
        warnings: list[str] = []
        refs = [e for e in asm["entries"] if e["kind"] == "ref"]
        for e in refs:
            if e["status"] == "empty":
                warnings.append(f"{e['label']}: 레퍼런스 '{e.get('ref_title', e['ref'])}'가 비어 있고 spec도 없어 빠졌습니다.")
            elif e["status"] == "missing":
                warnings.append(f"{e['label']}: 레퍼런스 ref:{e['ref']}를 찾을 수 없어 빠졌습니다.")

        # 민감도 (Q27): 조립 결과에서 계산
        internal = [e for e in asm["entries"] if e["sensitivity"] == "internal"]
        sensitive_nodes = sorted({e.get("ref") or e["source"] for e in internal})
        if internal and not tgt.get("allow_internal", True):
            if strip_sensitive:
                for e in internal:
                    if e["kind"] == "ref":
                        if e.get("spec"):
                            mode[e["key"]] = "text"
                            log.append(f"민감 자료 '{e['ref_title']}' 첨부를 텍스트로 대체")
                        else:
                            mode[e["key"]] = "drop"
                            log.append(f"민감 자료 '{e['ref_title']}'는 spec이 없어 규칙에서 제외")
            else:
                warnings.append(f"{tgt.get('label', target)}에는 민감 자료가 허용되지 않습니다: " + ", ".join(sensitive_nodes))

        def evaluate():
            prompt, att = self.render(asm, mode, paths=tgt.get("include_paths", False))
            files = 1 + len(att)  # prompt.md 포함
            v = []
            if files > tgt.get("max_files", 10 ** 9):
                v.append({"kind": "files", "message": f"첨부 {files}개 / 한도 {tgt['max_files']}개"})
            limit_b = tgt.get("max_file_mb", 10 ** 9) * 1024 * 1024
            big = [a for a in att if a["abs"] and os.path.getsize(a["abs"]) > limit_b]
            for a in big:
                v.append({"kind": "size", "key": a["key"],
                          "message": f"{a['file']} {os.path.getsize(a['abs']) / 1048576:.1f}MB / 한도 {tgt['max_file_mb']}MB"})
            if len(prompt) > tgt.get("max_chars", 10 ** 9):
                v.append({"kind": "chars", "message": f"프롬프트 {len(prompt):,}자 / 한도 {tgt['max_chars']:,}자"})
            return prompt, att, v

        prompt, att, violations = evaluate()
        if trim and violations:
            attached = lambda: [e for e in refs if mode.get(e["key"], "attach") == "attach" and e["status"] == "ok"]
            by_prio = lambda es: sorted(es, key=lambda e: (PRIORITY_ORDER.get(e["priority"], 0), e["key"]))
            # 0) 크기 초과 파일은 먼저 텍스트로
            for vv in [v for v in violations if v["kind"] == "size"]:
                if "#" in vv["key"]:  # v3.9 분리한 요소
                    pk, cn = vv["key"].split("#", 1)
                    c = next(c for x in refs if x["key"] == pk for c in x.get("components") or [] if c["name"] == cn)
                    mode[vv["key"]] = "text" if c["spec"] else "drop"
                    log.append(f"{c['label']} 요소: 크기 초과 첨부를 {'텍스트로 대체' if c['spec'] else '제외'}")
                    continue
                e = next(x for x in refs if x["key"] == vv["key"])
                if e.get("spec"):
                    mode[e["key"]] = "text"; log.append(f"{e['label']}: 크기 초과 첨부를 텍스트로 대체")
                else:
                    mode[e["key"]] = "drop"; log.append(f"{e['label']}: 크기 초과, spec이 없어 제외")
            prompt, att, violations = evaluate()
            # 0-1) v3.9: 분리한 요소 첨부 → 글 (원본보다 먼저)
            for e in by_prio(attached()):
                for c in e.get("components") or []:
                    ck = f"{e['key']}#{c['name']}"
                    if not any(v["kind"] == "files" for v in violations) or mode.get(ck, "attach") != "attach":
                        continue
                    mode[ck] = "text" if c["spec"] else "drop"
                    log.append(f"요소: {e['label']}의 {c['label']} 첨부 → {'텍스트(spec)로 대체' if c['spec'] else '제외'}")
                    prompt, att, violations = evaluate()
            # 1) spec 텍스트 대체 (선택 → 권장 → 필수)
            for e in by_prio(attached()):
                if not any(v["kind"] == "files" for v in violations):
                    break
                if e.get("spec"):
                    mode[e["key"]] = "text"
                    log.append(f"1단계: {e['label']} 첨부 → 텍스트(spec)로 대체")
                    prompt, att, violations = evaluate()
                else:
                    log.append(f"1단계: {e['label']}는 spec이 없어 텍스트 대체 불가")
            # 2) 컨택트 시트
            if any(v["kind"] == "files" for v in violations):
                imgs = [e for e in attached() if os.path.splitext(e["ref_file"])[1].lower() in IMAGE_EXT]
                if len(imgs) >= 2:
                    for e in imgs:
                        mode[e["key"]] = "sheet"
                    log.append(f"2단계: 이미지 {len(imgs)}장을 컨택트 시트 1장으로 합침")
                    prompt, att, violations = evaluate()
            # 3) 키 제외 (선택 → 권장, 필수는 제외하지 않음)
            for e in by_prio([x for x in asm["entries"] if mode.get(x["key"]) != "drop"]):
                if not violations:
                    break
                if e["priority"] == "required":
                    continue
                kinds = {v["kind"] for v in violations}
                frees_file = e["kind"] == "ref" and mode.get(e["key"], "attach") in ("attach", "sheet")
                if not ("chars" in kinds or ("files" in kinds and frees_file)):
                    continue
                mode[e["key"]] = "drop"
                log.append(f"3단계: {e['label']}({e['priority']}) 규칙 제외")
                prompt, att, violations = evaluate()

        # 출력 파일
        out_dir = os.path.join(self.root, CACHE_DIR, "drops", nid, target)
        files: list[str] = []
        prompt_path = os.path.join(out_dir, "prompt.md")
        if write:
            if os.path.isdir(out_dir):
                shutil.rmtree(out_dir)
            os.makedirs(out_dir, exist_ok=True)
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt)
        files.append(prompt_path)
        for a in att:
            if a.get("skill"):  # 스킬 본문은 이름이 겹치지 않게 '<이름>.SKILL.md'로 복사해 넘긴다
                dest = os.path.join(out_dir, a["file"])
                if write:
                    shutil.copyfile(a["abs"], dest)
                files.append(dest)
            elif a["key"] == "_sheet":
                sheet_path = os.path.join(out_dir, "contact-sheet.png")
                if write:
                    make_contact_sheet([s["abs"] for s in a["slots"]], sheet_path)
                files.append(sheet_path)
            else:
                files.append(a["abs"])
        # 클립보드용: 항상 경로를 붙인 판 (Codex 등)
        clip_prompt, _ = self.render(asm, mode, paths=True)
        if any(a["key"] == "_sheet" for a in att):
            clip_prompt = clip_prompt.replace("(contact-sheet.png는 함께 드롭된 파일)",
                                              os.path.join(out_dir, "contact-sheet.png"))
        return {
            "id": nid, "target": target, "target_label": tgt.get("label", target), "limits": tgt,
            "prompt": prompt, "clipboard": clip_prompt, "files": files, "chain": asm["chain"], "task": asm.get("task", ""),
            "gate": asm.get("gate", True), "set_gate": set_gate,
            "entries": [dict(e, mode=mode.get(e["key"], "attach"),
                             **({"components": [dict(c, mode=mode.get(f"{e['key']}#{c['name']}", "attach"))
                                                for c in e["components"]]} if e.get("components") else {}))
                        for e in asm["entries"]],
            "violations": violations, "warnings": warnings, "log": log,
            "sensitive": sensitive_nodes, "can_trim": bool(violations) and not trim,
            "needs_strip": bool(internal) and not tgt.get("allow_internal", True) and not strip_sensitive,
            "drag_icon": self.drag_icon() if write else None,
        }

    def drag_icon(self) -> str:
        path = os.path.join(self.root, CACHE_DIR, "drag-icon.png")
        if not os.path.exists(path):
            from PIL import Image, ImageDraw
            os.makedirs(os.path.dirname(path), exist_ok=True)
            im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rounded_rectangle((4, 8, 60, 56), 10, fill=(46, 111, 94, 230))
            d.rounded_rectangle((14, 20, 50, 26), 3, fill=(255, 255, 255, 230))
            d.rounded_rectangle((14, 32, 42, 38), 3, fill=(255, 255, 255, 200))
            im.save(path)
        return path

    # ------------------------------------------------------------------ 변경 통제 (Q12)
    def _snap_path(self, nid: str) -> str:
        return os.path.join(self.root, CACHE_DIR, "snapshots", f"{nid}.md")

    def changes(self) -> list[dict]:
        out = []
        for n in sorted(self.rule_nodes(), key=lambda x: x["id"]):
            if n["id"] in self.cyclic:
                continue
            cur = self.canonical(n["id"])
            sp = self._snap_path(n["id"])
            if not os.path.exists(sp):
                out.append({"id": n["id"], "status": "new", "diff": ""})
                continue
            with open(sp, encoding="utf-8") as f:
                old = f.read()
            if old != cur:
                diff = "".join(difflib.unified_diff(old.splitlines(True), cur.splitlines(True),
                                                    "승인된 버전", "현재", n=1))
                out.append({"id": n["id"], "status": "changed", "diff": diff})
        return out

    def approve(self, nid: str | None = None) -> list[str]:
        ids = [nid] if nid else [n["id"] for n in self.rule_nodes() if n["id"] not in self.cyclic]
        for i in ids:
            sp = self._snap_path(i)
            os.makedirs(os.path.dirname(sp), exist_ok=True)
            with open(sp, "w", encoding="utf-8") as f:
                f.write(self.canonical(i))
        return ids

    # ------------------------------------------------------------------ 그래프와 index
    def graph(self, history: list[dict] | None = None, fresh: set | None = None) -> dict:
        nodes, edges = [], []
        err_nodes = {i["node"] for i in self.issues if i["level"] == "error"}
        fresh = fresh or set()
        for n in self.nodes.values():
            nodes.append({
                "id": n["id"], "label": n["title"], "type": n["type"], "kind": n["kind"],
                "file": n.get("file"), "path": n.get("path"), "sensitivity": n.get("sensitivity", "public"),
                "error": n["id"] in err_nodes,
                "image": (n["type"] == "image" and n.get("exists") and n.get("size", 0) > 0),
                "new": bool({n.get("path"), n.get("file")} & fresh), "authored_by": n.get("authored_by"),
            })
            if n["type"] == "rule":
                used = bool(self.users(n["id"]))
                for p in n["inherits"]:
                    if p == "_defaults" and n.get("implicit_inherits") and used:
                        continue  # 부품 원칙은 기본값에서 선을 긋지 않는다 (v3.4)
                    if p in self.nodes:
                        edges.append({"id": f"inh:{n['id']}:{p}", "source": p, "target": n["id"],
                                      "kind": "inherits", "label": "상속"})
                for u in n.get("uses") or []:
                    if u in self.nodes:
                        edges.append({"id": f"use:{n['id']}:{u}", "source": n["id"], "target": u,
                                      "kind": "uses", "label": "스킬" if self.nodes[u]["type"] == "skill" else "원칙"})
                for key, r in n["rules"].items():
                    ref = self._ref_id(r["value"])
                    if ref and ref in self.nodes:
                        edges.append({"id": f"key:{n['id']}:{key}", "source": n["id"], "target": ref,
                                      "kind": "key", "key": key, "label": self.key_spec(key).get("label", key)})
            for rel in n.get("relations") or []:
                if rel.get("target") in self.nodes:
                    edges.append({"id": f"rel:{n['id']}:{rel['type']}:{rel['target']}", "source": n["id"],
                                  "target": rel["target"], "kind": "info",
                                  "label": {"child_of": "하위", "derived_from": "출처"}.get(rel["type"], rel["type"])})
            if n["kind"] == "output" and n.get("set") in self.nodes:
                edges.append({"id": f"out:{n['set']}:{n['id']}", "source": n["set"], "target": n["id"],
                              "kind": "info", "label": "산출물"})
        # AI 노드 (v3 4.1): 대상 AI, 작성 엣지, 드롭 횟수 엣지
        for tid, t in self.targets.items():
            nodes.append({"id": "ai:" + tid, "label": t.get("label", tid), "type": "ai", "kind": t.get("kind", "chat"),
                          "file": None, "sensitivity": "public", "error": False, "image": False, "new": False})
        for n in self.nodes.values():
            a = n.get("authored_by")
            if a and a in self.targets:
                edges.append({"id": f"auth:{a}:{n['id']}", "source": "ai:" + a, "target": n["id"],
                              "kind": "info", "label": "작성"})
        counts: dict[tuple, dict] = {}
        for rec in history or []:
            if rec.get("kind", "prompt") != "prompt" or rec.get("node") not in self.nodes or rec.get("target") not in self.targets:
                continue
            c = counts.setdefault((rec["node"], rec["target"]), {"drag": 0, "copy": 0, "last": ""})
            c["drag" if rec.get("method") == "drag" else "copy"] += 1
            c["last"] = max(c["last"], rec.get("ts", ""))
        for (nid, tid), c in counts.items():
            edges.append({"id": f"drop:{nid}:{tid}", "source": nid, "target": "ai:" + tid, "kind": "info",
                          "label": f"드롭 {c['drag'] + c['copy']}회", "drops": c})
        return {"nodes": nodes, "edges": edges, "issues": self.issues}

    def build_index(self) -> None:
        g = self.graph()
        with open(os.path.join(self.root, "graph.json"), "w", encoding="utf-8") as f:
            json.dump({"nodes": g["nodes"], "edges": g["edges"]}, f, ensure_ascii=False, indent=2)
        lines = ["# Assets 라이브러리 index (자동 생성, 직접 고치지 마세요)", "",
                 "AI는 이 파일을 먼저 읽고 필요한 노드 파일로 내려가세요.",
                 "규칙 노드는 `inherits` 순서대로 상속되며 뒤에 오는 쪽이 우선합니다.",
                 "`uses`는 부품 원칙입니다. 계층 자신의 규칙 > 그 계층이 붙인 원칙(나중 것 우선) > 윗 계층 순서로 우선합니다.", ""]
        lines.append("## 규칙 노드")
        for n in sorted(self.rule_nodes(), key=lambda x: (x["kind"] != "defaults", x["kind"], x["id"])):
            inh = ", ".join(n["inherits"]) or "-"
            use = (" · 원칙: " + ", ".join(n["uses"])) if n.get("uses") else ""
            lines.append(f"- [{n['title']}]({n['path']}) `{n['id']}` · {n['kind']} · 상속: {inh}{use}")
        lines += ["", "## 레퍼런스 노드"]
        for n in sorted([x for x in self.nodes.values() if x["type"] != "rule"], key=lambda x: (x["type"], x["id"])):
            tags = ", ".join(n["tags"])
            if n["kind"] == "unregistered":
                lines.append(f"- {n['file']} · {n['type']} · (미등록: 아직 .meta.yaml 없음)")
                continue
            lines.append(f"- [{n['title']}]({n['file']}) `{n['id']}` · {n['type']} · {n['summary']} · 태그: {tags}")
        with open(os.path.join(self.root, "index.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


# v3.4 승인 게이트 (Q73-A로 다듬음). 프롬프트 앞쪽 '이번 작업' 바로 뒤에 들어간다. 게이트를 끄면 아무것도 붙지 않는다.
GATE_STEPS = [
    "결과물을 바로 만들지 말고, 먼저 아래만 짧게 답한 뒤 사용자의 승인을 기다리세요. 규칙을 다시 옮겨 적지는 마세요.",
    "1. 이번 작업을 어떻게 이해했는지 (한 줄)",
    "2. 결과물 구성 (목차, 화면·파일 목록 등)",
    "3. 이미지·도식 계획 (어디에, 무엇을, 몇 개)",
    "4. 시작하기 전에 확인이 필요한 것 (없으면 \"없음\")",
    "사용자가 승인하면(\"승인\", \"진행\", \"좋아\" 등) 결과물을 만드세요. 이때 계획에 적지 않은 것까지 **아래 규칙 전체와 첨부 레퍼런스, 마지막의 자가 점검을 모두** 적용하세요. "
    "고쳐 달라고 하면 계획을 고쳐 다시 승인을 받으세요.",
    "승인 전에는 결과물(본문, 이미지, 코드, 파일)을 만들거나 기존 파일을 고치지 마세요. 첨부 레퍼런스를 열어 보고 읽는 것은 지금 해도 됩니다.",
]
GATE_CHECK = "계획에 적지 않은 규칙까지 위 규칙과 첨부 레퍼런스를 모두 지켰는가"


def process_lines(has_task: bool) -> list[str]:
    """'## 진행 방식' 단락. 작업이 없으면 먼저 기다리게 한다. 세 대상 앱에 같은 문장(P7)."""
    out = ["## 진행 방식: 계획을 먼저 보여 주고 승인을 받으세요"]
    if not has_task:
        out += ["지금은 작업 내용이 정해지지 않았습니다. 첨부 레퍼런스를 먼저 살펴 두고, 이 지침을 확인했다고 한 줄로 답한 뒤 "
                "사용자가 작업을 말할 때까지 기다리세요.", "작업을 받으면:"]
    return out + GATE_STEPS


class CycleError(Exception):
    pass


def _as_list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def make_contact_sheet(paths: list[str], out: str, cell: int = 640) -> None:
    """이미지 여러 장을 번호 붙은 격자 한 장으로 합친다 (덜어내기 2단계)."""
    from PIL import Image, ImageDraw, ImageFont
    cols = 2 if len(paths) > 1 else 1
    rows = (len(paths) + cols - 1) // cols
    pad, head = 16, 56
    sheet = Image.new("RGB", (cols * (cell + pad) + pad, rows * (cell + head + pad) + pad), "white")
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=36)
    except TypeError:
        font = ImageFont.load_default()
    for i, p in enumerate(paths):
        im = Image.open(p).convert("RGB")
        im.thumbnail((cell, cell))
        c, r = i % cols, i // cols
        x, y = pad + c * (cell + pad), pad + r * (cell + head + pad)
        d.rounded_rectangle((x, y, x + 52, y + 46), 8, fill=(46, 111, 94))
        d.text((x + 15, y + 3), str(i + 1), fill="white", font=font)
        sheet.paste(im, (x, y + head))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sheet.save(out, optimize=True)
