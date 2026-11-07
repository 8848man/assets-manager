"""하위 정리 (기획서 v3.4, Q72-A).

노드 하나를 기준으로 그 아래(상속 자식, 부품 원칙, 키로 붙은 레퍼런스, 정보 연결)를 정리한다.

1. `diagnose`  : 앱이 결정적으로 찾는 구조 문제 (겹치는 상속, 상속으로 쌓인 원칙, 덮어써진 키, 반복 출처…)
2. `build`     : AI에게 줄 정리 요청 프롬프트 (요청 `kind: tidy`). 앱은 AI를 부르지 않는다(P3).
3. `receive`   : AI 답(plan.yaml 텍스트 또는 zip 안의 plan.yaml)을 `inbox/<rid>/out/plan.yaml`로
4. `preview`   : 메모리 안에서 ops를 적용해 본 뒤 전·후 그래프, 변경 목록(이유), **최종 프롬프트 비교**를 돌려준다
5. `apply`     : 선택한 항목만 파일에 쓴다. 쓰기 전 `backup_sync`(P5), 지우는 것은 휴지통

ops (plan.yaml `ops:`):
  set_inherits {node, inherits}      계층(상속) 바꾸기
  set_uses     {node, uses}          부품 원칙 바꾸기
  move_rule    {key, from, to}       키를 다른 노드로 옮기기 (값·note 그대로)
  set_rule     {node, key, value}    값 바꾸기 (합칠 때만; 프롬프트 비교에 드러남)
  create_rule  {id, kind, title, inherits?, uses?, rules?, body?}
  rename       {node, title}
  set_body     {node, body}          규칙 노드 본문(설명) 바꾸기 — 프롬프트에는 안 들어감
  set_relations{node, relations}     정보 연결(출처 등) 바꾸기
  trash        {node}                규칙 노드를 휴지통으로 (더 이상 아무도 쓰지 않을 때만)
정리는 구조를 바꾸는 일이므로 키 이동·삭제(trash)를 허용한다. 대신 내용이 바뀌면 프롬프트 비교로 드러난다.
"""
from __future__ import annotations

import copy
import difflib
import os
import re

import yaml

from engine import Library, CycleError, FRONTMATTER
import folder_requests as freq
import store

OP_LABEL = {
    "set_inherits": "상속 바꾸기", "set_uses": "원칙 붙이기", "move_rule": "키 옮기기", "set_rule": "값 바꾸기",
    "create_rule": "새 규칙 노드", "rename": "이름 바꾸기", "set_body": "설명 바꾸기", "set_relations": "연결 바꾸기", "trash": "휴지통으로",
}
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


# ------------------------------------------------------------------ 범위와 진단
def scope(lib: Library, root: str) -> list[str]:
    """정리 대상: 기준 노드 + 상속 자식 + 부품 원칙 + 키로 붙은 레퍼런스 + 정보 연결 + 세트의 산출물."""
    if root not in lib.nodes:
        raise KeyError(f"노드 없음: {root}")
    ids = {root}
    if lib.nodes[root]["type"] == "rule":
        ids.update(x for x in lib.descendants(root) if x not in lib.cyclic)
        for x in list(ids):
            if x not in lib.cyclic:
                ids.update(lib.components(x))
        # 상속으로 걸린 원칙 중 '계층'이 아닌 것도 정리 대상: 다른 것을 상속하는 원칙(원칙 쌓기)이거나
        # 이 범위 안에서만 상속되는 원칙. 범위 밖에서도 상속되는 전역 원칙 같은 계층은 제외.
        for x in list(ids):
            for p in lib.nodes[x].get("inherits") or []:
                pn = lib.nodes.get(p)
                if not pn or pn["kind"] != "principle" or p == root or p in ids:
                    continue
                inheritors = {m["id"] for m in lib.rule_nodes() if p in m["inherits"]}
                if _stacked(lib, pn) or inheritors <= ids:
                    ids.add(p)
                    ids.update(lib.components(p))
    rules = [x for x in ids if lib.nodes[x]["type"] == "rule"]
    for x in rules:  # 붙은 스킬 (v3.7)
        ids.update(u for u in lib.nodes[x].get("uses") or [] if lib.nodes.get(u, {}).get("type") == "skill")
    for x in rules:
        for r in lib.nodes[x]["rules"].values():
            ref = lib._ref_id(r["value"])
            if ref in lib.nodes:
                ids.add(ref)
    for n in lib.nodes.values():
        if n["type"] == "ai":
            continue
        if n.get("kind") == "output" and n.get("set") in ids:
            ids.add(n["id"])
        for rel in n.get("relations") or []:
            if n["id"] in ids and rel.get("target") in lib.nodes:
                ids.add(rel["target"])
            elif rel.get("target") in ids and n["id"] not in ids and n["type"] != "rule":
                ids.add(n["id"])
    # 계층 위쪽(전역 원칙 등)은 정리 대상이 아니다. 자식이 상속하는 부모로 보이게만 한다.
    return sorted(ids)


def _stacked(lib: Library, n: dict) -> bool:
    """앱 기본값 말고 다른 규칙을 상속하는가 (원칙 쌓기)."""
    return any(lib.nodes.get(p, {}).get("kind") not in (None, "defaults") for p in n.get("inherits") or [])


def _is_layer(lib: Library, nid: str) -> bool:
    """누군가 상속하는 규칙 노드 = 계층. 아무도 상속하지 않고 uses로만 쓰이는 원칙 = 부품."""
    return any(nid in n["inherits"] for n in lib.rule_nodes())


def diagnose(lib: Library, ids: list[str]) -> list[dict]:
    out: list[dict] = []
    rules = [lib.nodes[i] for i in ids if lib.nodes[i]["type"] == "rule" and i not in lib.cyclic]
    for n in rules:
        parents = [p for p in n["inherits"] if p in lib.nodes]
        for p in parents:
            for q in parents:
                if p != q and p in lib.linearize(q)[:-1]:
                    out.append({"code": "redundant_inherit", "nodes": [n["id"], p, q],
                                "message": f"'{n['title']}'은(는) '{lib.nodes[p]['title']}'을(를) 직접 상속하지만 "
                                           f"'{lib.nodes[q]['title']}'을(를) 통해서도 이미 받습니다."})
        principle_parents = [p for p in parents if lib.nodes[p]["kind"] == "principle"]
        if len(principle_parents) >= 2:
            out.append({"code": "principles_as_parents", "nodes": [n["id"]] + principle_parents,
                        "message": f"'{n['title']}'이(가) 원칙 {len(principle_parents)}개를 상속합니다. "
                                   "주제별 원칙은 부품이므로 `uses`로 붙이는 편이 맞습니다."})
        if n["kind"] == "principle" and _stacked(lib, n):
            out.append({"code": "principle_inherits", "nodes": [n["id"]] + parents,
                        "message": f"원칙 '{n['title']}'이(가) 다른 규칙을 상속합니다. 부품 원칙은 자기 규칙만 갖고, "
                                   "상속은 세트 쪽 계층에서 합니다."})
        if not n["rules"] and not n.get("uses") and n["kind"] != "defaults":
            out.append({"code": "empty", "nodes": [n["id"]], "message": f"'{n['title']}'에 규칙이 없습니다."})
    for n in rules:
        if n["kind"] != "set":
            continue
        for e in lib.assemble(n["id"])["entries"]:
            for o in e["overridden"]:
                src = lib.nodes.get(o["source"], {})
                if src.get("kind") == "principle" and lib.nodes[e["source"]]["kind"] == "principle" and o["value"] != e["value"]:
                    out.append({"code": "shadowed_key", "nodes": [n["id"], o["source"], e["source"]],
                                "message": f"'{n['title']}'에서 `{e['key']}`: '{src.get('title')}'의 값이 "
                                           f"'{lib.nodes[e['source']]['title']}'에 덮어써져 사라집니다."})
    seen: dict[tuple, list[str]] = {}
    for i in ids:
        for rel in lib.nodes[i].get("relations") or []:
            seen.setdefault((rel.get("type"), rel.get("target")), []).append(i)
    for (typ, tgt), who in sorted(seen.items(), key=lambda kv: str(kv[0])):
        if len(who) >= 3 and tgt in lib.nodes:
            out.append({"code": "repeated_relation", "nodes": sorted(who) + [tgt],
                        "message": f"같은 연결({typ} → '{lib.nodes[tgt]['title']}')이 {len(who)}개 노드에 반복됩니다."})
    for n in rules:
        if n["kind"] == "principle" and not lib.users(n["id"]) and not _is_layer(lib, n["id"]):
            out.append({"code": "unused_principle", "nodes": [n["id"]],
                        "message": f"원칙 '{n['title']}'을(를) 아무 세트도 쓰지 않습니다."})
    uniq, keys = [], set()
    for d in out:
        k = (d["code"], tuple(d["nodes"]))
        if k not in keys:
            keys.add(k)
            uniq.append(d)
    return uniq


# ------------------------------------------------------------------ 요청 프롬프트
def _rule_view(lib: Library, n: dict) -> dict:
    v = {"id": n["id"], "kind": n["kind"], "title": n["title"]}
    if not n.get("implicit_inherits"):
        v["inherits"] = n["inherits"]
    if n.get("uses"):
        v["uses"] = n["uses"]
    v["rules"] = {k: (r["value"] if not r["note"] else {"value": r["value"], "note": r["note"]})
                  for k, r in n["rules"].items()}
    if n.get("relations"):
        v["relations"] = n["relations"]
    return v


def render(lib: Library, root: str, rid: str, ids: list[str], diags: list[dict], target: str | None,
           goal: dict | None = None) -> str:
    rn = lib.nodes[root]
    agent = (lib.targets.get(target) or {}).get("kind") == "agent"
    L = ["# Assets Manager: 하위 노드 정리 요청", "",
         f"요청 id: `{rid}` · 기준 노드: **{rn['title']}** (`{root}`, {rn['kind']})", "",
         "당신은 이 작업 표준 라이브러리의 구조를 정리하는 담당입니다. 아래 노드들을 **읽기 쉬운 구조**로 바꾸는 계획을 세워 주세요. "
         "파일을 직접 고치지 말고 `plan.yaml` 하나로 답하세요. 사용자는 앱에서 전·후 그래프와 이유를 보고 반영 여부를 정합니다.", "",
         "## 구조 규칙 (반드시 따를 것)",
         "- **계층(inherits):** `앱 기본값 → 내 전역 원칙 → 세트 → 파생 세트`처럼 위에서 아래로 한 줄기. 세트는 보통 계층 부모 하나만 상속합니다.",
         "- **부품(uses):** 주제별 원칙(`kind: principle`)은 레퍼런스처럼 세트에 붙는 부품입니다. 원칙은 **아무것도 상속하지 않고**(inherits 생략) 자기 규칙만 가집니다. 세트가 `uses: [원칙 id, …]`로 붙입니다.",
         "- **우선순위:** 세트 자신의 규칙 > 세트가 붙인 원칙(뒤에 붙인 것이 우선) > 윗 계층. 같은 키를 여러 원칙이 주면 앞의 것은 사라지므로, 원칙마다 서로 다른 키를 씁니다.",
         "- **원칙 하나 = 주제 하나.** 키 2~6개가 알맞습니다. 너무 잘게 나뉜 원칙은 합치고, 섞인 원칙은 나눕니다.",
         "- **출처 연결(derived_from)** 은 같은 내용이면 한 곳(보통 세트)에만 둡니다.",
         "- 레퍼런스·산출물 노드는 지우지 않습니다(연결만 바꿀 수 있음). 규칙 노드는 내용을 다른 노드로 옮긴 뒤에만 `trash` 합니다.",
         "- **최종 프롬프트 내용은 유지**합니다. 키를 옮기는 것은 괜찮지만 값을 바꾸거나 없애야 한다면 그 이유를 `reason`에 분명히 쓰세요. 앱이 정리 전·후 프롬프트를 비교해 사용자에게 보여 줍니다.",
         "- 고칠 것이 없으면 `ops: []`와 그 이유를 쓰세요. 억지로 바꾸지 마세요.", ""]
    if goal and goal.get("kind") == "skill_to_principle" and goal.get("skill") in lib.nodes:
        sk = lib.nodes[goal["skill"]]
        L += ["## 이번 정리의 목표: 스킬을 원칙으로 풀기",
              f"스킬 **{sk['title']}** (`{sk['id']}`)의 내용 중 **규칙형 내용**(말투, 형식, 금지 사항, 판단 기준처럼 키와 값으로 쓸 수 있는 것)을 "
              "원칙 노드로 옮기세요(`create_rule`, kind: principle, 키 2~6개, 키 어휘에서 고르기). 그 원칙을 이 스킬을 붙인 노드에 `set_uses`로 붙이세요.",
              "- 절차·명령·스크립트처럼 키로 옮기면 내용을 잃는 부분이 남으면 **스킬은 그대로 붙여 둡니다**(규칙형만 원칙으로).",
              "- 전부 규칙형이라 스킬이 더 필요 없으면 `set_uses`에서 스킬을 빼세요. 스킬 노드 자체는 지우지 않습니다.",
              "- 새 원칙에 `relations: [{type: derived_from, target: " + sk["id"] + "}]`를 다세요.",
              "", "스킬 본문:", "```markdown", (sk.get("body") or "")[:8000], "```", ""]
    L += ["## 앱이 찾은 구조 문제"]
    L += [f"- `{d['code']}` {d['message']}" for d in diags] or ["- (없음)"]
    L += ["", "## 현재 노드 (정리 범위)", "```yaml"]
    rules = [lib.nodes[i] for i in ids if lib.nodes[i]["type"] == "rule"]
    others = [lib.nodes[i] for i in ids if lib.nodes[i]["type"] != "rule"]
    L.append(yaml.safe_dump({"rules": [_rule_view(lib, n) for n in rules]}, allow_unicode=True, sort_keys=False, width=10**6).rstrip())
    if others:
        L.append(yaml.safe_dump({"references": [
            {k: v for k, v in {"id": n["id"], "type": n["type"], "kind": n["kind"], "title": n["title"],
                               "summary": n.get("summary") or None, "relations": n.get("relations") or None}.items() if v}
            for n in others]}, allow_unicode=True, sort_keys=False, width=10**6).rstrip())
    L += ["```", ""]
    outside = sorted({p for n in rules for p in n["inherits"] if p not in ids and p in lib.nodes})
    if outside:
        L += ["범위 밖 부모(바꾸지 말 것, 상속 대상으로만 사용): " + ", ".join(f"`{p}` {lib.nodes[p]['title']}" for p in outside), ""]
    L += ["## 답변 형식: `plan.yaml`", "```yaml",
          f"request: {rid}",
          "summary: 한 줄 요약",
          "why: |                         # 전체적으로 왜 이렇게 바꾸는지 2~4줄",
          "  ...",
          "ops:                           # 위에서부터 차례로 적용",
          "  - { op: set_inherits, node: <세트 id>, inherits: [global], reason: ... }",
          "  - { op: set_uses, node: <세트 id>, uses: [<원칙 id>, ...], reason: ... }",
          "  - { op: set_inherits, node: <원칙 id>, inherits: [], reason: 부품 원칙은 상속하지 않음 }",
          "  - { op: move_rule, key: <키>, from: <노드 id>, to: <노드 id>, reason: ... }",
          "  - { op: create_rule, id: <새 id>, kind: principle, title: 한국어 제목, rules: { <키>: <값> }, reason: ... }",
          "  - { op: set_rule, node: <id>, key: <키>, value: <값>, reason: 합치느라 문장을 바꿈 }",
          "  - { op: rename, node: <id>, title: 새 제목, reason: ... }",
          "  - { op: set_body, node: <규칙 노드 id>, body: 노드 설명(구조가 바뀌어 설명이 틀려졌을 때), reason: ... }",
          "  - { op: set_relations, node: <id>, relations: [{ type: derived_from, target: <id> }], reason: ... }",
          "  - { op: trash, node: <규칙 노드 id>, reason: 내용을 X로 옮겨서 빈 노드가 됨 }",
          "```",
          "- `inherits: []`은 \"앱 기본값만 받음\"(상속선 없음)입니다.",
          "- 새 id는 영문 소문자-하이픈이며 기존 id와 겹치면 안 됩니다.", ""]
    if agent:
        L += [f"이 파일을 `inbox/{rid}/out/plan.yaml`에 쓰세요. 다른 파일은 건드리지 마세요."]
    else:
        L += ["답변은 **`plan.yaml` 내용을 담은 yaml 코드 블록 하나**로 주세요. 사용자가 그대로 복사해 앱의 정리 창에 붙여넣습니다."]
    return "\n".join(L) + "\n"


def build(lib: Library, root: str, target: str | None = None, goal: dict | None = None) -> dict:
    ids = scope(lib, root)
    if goal and goal.get("skill") in lib.nodes and goal["skill"] not in ids:
        ids = sorted(set(ids) | {goal["skill"]})
    diags = diagnose(lib, ids)
    req = freq.new_request(lib, root, kind="tidy", status="sent")
    req.update(target=target, scope=ids, sent=store.now(), goal=goal)
    freq._save(lib.root, req)
    prompt = render(lib, root, req["id"], ids, diags, target, goal)
    with open(os.path.join(freq._rdir(lib.root, req["id"]), "prompt.md"), "w", encoding="utf-8") as f:
        f.write(prompt)
    return {"request": req["id"], "prompt": prompt, "diagnostics": diags, "scope": ids,
            "graph": subgraph(lib, ids)}


# ------------------------------------------------------------------ 답 받기
def parse_plan_text(text: str) -> dict:
    m = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.S)
    body = m.group(1) if m else text
    plan = yaml.safe_load(body)
    if not isinstance(plan, dict):
        raise ValueError("plan.yaml 형식이 아닙니다 (yaml 매핑이어야 합니다)")
    return plan


def receive(lib: Library, rid: str, text: str) -> dict:
    plan = parse_plan_text(text)
    out = os.path.join(freq._rdir(lib.root, rid), "out")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "plan.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(plan, f, allow_unicode=True, sort_keys=False)
    req = freq._load(lib.root, rid)
    req.update(status="received", received=store.now())
    freq._save(lib.root, req)
    return preview(lib, rid)


def _load_plan(lib: Library, rid: str) -> dict:
    p = os.path.join(freq._rdir(lib.root, rid), "out", "plan.yaml")
    if not os.path.isfile(p):
        raise FileNotFoundError("아직 AI 답(plan.yaml)이 없습니다")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ------------------------------------------------------------------ 시뮬레이션
def _as_ids(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def simulate(lib: Library, plan: dict, selected: set | None = None) -> tuple[Library, list[dict]]:
    """ops를 메모리 안의 사본에 적용한다. 파일은 건드리지 않는다. 결정적."""
    lib2 = copy.deepcopy(lib)
    ops = plan.get("ops") or []
    if not isinstance(ops, list):
        ops = []
    items: list[dict] = []
    order = sorted(range(len(ops)), key=lambda i: (0 if (ops[i] or {}).get("op") == "create_rule" else
                                                   2 if (ops[i] or {}).get("op") == "trash" else 1, i))
    for i in order:
        op = ops[i] if isinstance(ops[i], dict) else {}
        it = {"id": f"o{i}", "op": op.get("op"), "label": OP_LABEL.get(op.get("op"), str(op.get("op"))),
              "reason": str(op.get("reason") or ""), "checked": True, "warnings": []}
        items.append(it)
        if selected is not None and it["id"] not in selected:
            it["checked"] = False
            continue
        try:
            _apply_op(lib2, op, it)
        except (KeyError, ValueError, TypeError) as e:
            it.update(error=str(e).strip("'\""), checked=False)
    items.sort(key=lambda x: int(x["id"][1:]))
    # 다시 검증 (순환·없는 대상)
    lib2.issues = [x for x in lib2.issues if not str(x.get("message", "")).startswith(("상속 대상", "원칙(uses)", "순환", "uses는"))]
    lib2._validate()
    lib2.issues = _dedupe(lib2.issues)
    for it in items:
        if it.get("checked") and it.get("node") in lib2.cyclic:
            it["error"] = "이 변경으로 순환이 생깁니다"
    return lib2, items


def _dedupe(issues: list[dict]) -> list[dict]:
    out, seen = [], set()
    for x in issues:
        k = (x.get("level"), x.get("node"), x.get("message"))
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _rule(lib2: Library, nid) -> dict:
    n = lib2.nodes.get(str(nid))
    if not n or n["type"] != "rule":
        raise KeyError(f"규칙 노드가 없습니다: {nid}")
    return n


def _apply_op(lib2: Library, op: dict, it: dict) -> None:
    kind = op.get("op")
    if kind == "set_inherits":
        n = _rule(lib2, op.get("node"))
        new = _as_ids(op.get("inherits"))
        for p in new:
            _rule(lib2, p)
        if n["id"] == "_defaults":
            raise ValueError("앱 기본값의 상속은 바꿀 수 없습니다")
        it.update(node=n["id"], title=n["title"], before=list(n["inherits"]) if not n.get("implicit_inherits") else [],
                  after=new)
        n["inherits"] = new or ["_defaults"]
        n["implicit_inherits"] = not new
    elif kind == "set_uses":
        n = _rule(lib2, op.get("node"))
        new = _as_ids(op.get("uses"))
        for u in new:
            if lib2.nodes.get(u, {}).get("type") == "skill":
                continue
            if _rule(lib2, u)["kind"] != "principle":
                it["warnings"].append(f"`{u}`는 원칙 노드가 아닙니다")
        it.update(node=n["id"], title=n["title"], before=list(n.get("uses") or []), after=new)
        n["uses"] = new
    elif kind == "move_rule":
        key = str(op.get("key"))
        a, b = _rule(lib2, op.get("from")), _rule(lib2, op.get("to"))
        if key not in a["rules"]:
            raise KeyError(f"`{a['id']}`에 `{key}` 키가 없습니다")
        val = a["rules"][key]
        if key in b["rules"] and b["rules"][key]["value"] != val["value"] and not op.get("overwrite"):
            raise ValueError(f"`{b['id']}`에 이미 다른 `{key}` 값이 있습니다 (덮어쓰려면 overwrite: true)")
        it.update(node=a["id"], to=b["id"], title=a["title"], to_title=b["title"], key=key,
                  key_label=lib2.key_spec(key).get("label", key), value=val["value"])
        del a["rules"][key]
        b["rules"][key] = val
    elif kind == "set_rule":
        n = _rule(lib2, op.get("node"))
        key = str(op.get("key"))
        if lib2._ref_id(op.get("value")) and lib2._ref_id(op.get("value")) not in lib2.nodes:
            raise KeyError(f"없는 레퍼런스: {op.get('value')}")
        old = n["rules"].get(key)
        it.update(node=n["id"], title=n["title"], key=key, key_label=lib2.key_spec(key).get("label", key),
                  before=old["value"] if old else None, after=op.get("value"))
        n["rules"][key] = {"value": op.get("value"), "note": old["note"] if old else None}
    elif kind == "create_rule":
        nid = str(op.get("id") or "")
        if not ID_RE.fullmatch(nid):
            raise ValueError(f"id 형식이 올바르지 않습니다: {nid}")
        if nid in lib2.nodes:
            raise ValueError(f"id `{nid}`가 이미 있습니다")
        rk = op.get("kind") or "principle"
        if rk not in ("principle", "set"):
            raise ValueError(f"kind는 principle 또는 set이어야 합니다: {rk}")
        inh = _as_ids(op.get("inherits"))
        rules = {}
        for k, raw in (op.get("rules") or {}).items():
            rules[str(k)] = ({"value": raw.get("value"), "note": raw.get("note")} if isinstance(raw, dict)
                             else {"value": raw, "note": None})
        folder = "principles" if rk == "principle" else "sets"
        node = {"id": nid, "type": "rule", "kind": rk, "title": op.get("title") or nid,
                "path": f"{folder}/{nid}.md", "inherits": inh or ["_defaults"], "implicit_inherits": not inh,
                "uses": _as_ids(op.get("uses")), "rules": rules, "priorities": {}, "precedence": [],
                "authored_by": None, "sensitivity": "public", "relations": op.get("relations") or [],
                "body": str(op.get("body") or "").strip(), "version": None, "mtime": 0, "created": True}
        lib2.nodes[nid] = node
        it.update(node=nid, title=node["title"], kind=rk, after=_rule_view(lib2, node))
    elif kind == "rename":
        n = lib2.nodes.get(str(op.get("node")))
        if not n:
            raise KeyError(f"노드가 없습니다: {op.get('node')}")
        it.update(node=n["id"], before=n["title"], after=str(op.get("title") or n["title"]), title=n["title"])
        n["title"] = it["after"]
    elif kind == "set_body":
        n = _rule(lib2, op.get("node"))
        it.update(node=n["id"], title=n["title"], before=n.get("body") or "", after=str(op.get("body") or "").strip())
        n["body"] = it["after"]
    elif kind == "set_relations":
        n = lib2.nodes.get(str(op.get("node")))
        if not n or n["type"] == "ai" or n.get("kind") == "unregistered":
            raise KeyError(f"연결을 바꿀 수 없는 노드: {op.get('node')}")
        rels = op.get("relations") or []
        if not isinstance(rels, list) or any(not isinstance(r, dict) or not r.get("type") for r in rels):
            raise ValueError("relations는 [{type, target}] 목록이어야 합니다")
        it.update(node=n["id"], title=n["title"], before=list(n.get("relations") or []), after=rels)
        n["relations"] = [{"type": str(r["type"]), "target": str(r.get("target"))} for r in rels]
    elif kind == "trash":
        n = _rule(lib2, op.get("node"))
        if n["kind"] == "defaults":
            raise ValueError("앱 기본값은 지울 수 없습니다")
        nid = n["id"]
        users = sorted(m["id"] for m in lib2.rule_nodes() if m["id"] != nid and
                       (nid in m["inherits"] or nid in (m.get("uses") or [])))
        if users:
            raise ValueError(f"아직 쓰는 노드가 있습니다: {', '.join(users)}")
        it.update(node=nid, title=n["title"], lost_keys=sorted(n["rules"]))
        if n["rules"]:
            it["warnings"].append("남아 있는 키가 함께 사라집니다: " + ", ".join(sorted(n["rules"])))
        del lib2.nodes[nid]
    else:
        raise ValueError(f"알 수 없는 op: {kind}")


# ------------------------------------------------------------------ 미리보기
def subgraph(lib: Library, ids) -> dict:
    ids = set(ids)
    g = lib.graph()
    nodes = [n for n in g["nodes"] if n["id"] in ids]
    # 범위 밖 부모(예: 내 전역 원칙)는 흐리게 함께 보여 준다
    outside = {e["source"] for e in g["edges"] if e["kind"] == "inherits" and e["target"] in ids and e["source"] not in ids
               and e["source"] != "_defaults"}
    nodes += [dict(n, outside=True) for n in g["nodes"] if n["id"] in outside]
    keep = ids | outside
    edges = [e for e in g["edges"] if e["source"] in keep and e["target"] in keep and e["kind"] != "info"
             or (e["kind"] == "info" and e["source"] in ids and e["target"] in ids and e["label"] in ("출처", "하위", "산출물"))]
    return {"nodes": nodes, "edges": edges}


def _prompt(lib: Library, nid: str) -> str | None:
    try:
        return lib.render(lib.assemble(nid))[0]
    except (KeyError, CycleError):
        return None


def prompt_diffs(lib: Library, lib2: Library, ids: list[str]) -> list[dict]:
    out = []
    for nid in ids:
        n = lib.nodes.get(nid)
        if not n or n["type"] != "rule" or n["kind"] != "set":
            continue
        a = _prompt(lib, nid)
        b = _prompt(lib2, nid) if nid in lib2.nodes else None
        if a is None:
            continue
        ka = {e["key"]: e["value"] for e in lib.assemble(nid)["entries"]}
        kb = {e["key"]: e["value"] for e in lib2.assemble(nid)["entries"]} if b is not None else {}
        changed = [{"key": k, "label": lib.key_spec(k).get("label", k), "before": ka.get(k), "after": kb.get(k)}
                   for k in sorted(set(ka) | set(kb)) if ka.get(k) != kb.get(k)]
        diff = "" if a == b else "".join(difflib.unified_diff(a.splitlines(True), (b or "").splitlines(True),
                                                             "정리 전", "정리 후", n=0))
        out.append({"id": nid, "title": n["title"], "same": a == b, "removed": b is None,
                    "changed_keys": changed, "diff": diff})
    return out


def preview(lib: Library, rid: str, selected: list[str] | None = None) -> dict:
    req = freq._load(lib.root, rid)
    root = req.get("node")
    try:
        plan = _load_plan(lib, rid)
    except FileNotFoundError as e:
        return {"request": req, "waiting": True, "message": str(e)}
    if plan.get("request") and plan["request"] != rid:
        return {"request": req, "errors": [f"다른 요청의 답입니다: {plan['request']}"], "items": []}
    ids = req.get("scope") or scope(lib, root)
    ids = [i for i in ids if i in lib.nodes]
    lib2, items = simulate(lib, plan, set(selected) if selected is not None else None)
    created = [it["node"] for it in items if it.get("op") == "create_rule" and it.get("checked") and not it.get("error")]
    ids2 = sorted({i for i in ids if i in lib2.nodes} | set(created))
    diag_before = diagnose(lib, ids)
    diag_after = diagnose(lib2, ids2) if not lib2.cyclic else []
    errors = [x["message"] for x in lib2.issues if x["level"] == "error" and x.get("node") in ids2
              and x not in lib.issues]
    return {"request": req, "summary": str(plan.get("summary") or ""), "why": str(plan.get("why") or "").strip(),
            "items": items, "errors": errors,
            "diag_before": diag_before, "diag_after": diag_after,
            "graph_before": subgraph(lib, ids), "graph_after": subgraph(lib2, ids2),
            "prompts": prompt_diffs(lib, lib2, ids)}


# ------------------------------------------------------------------ 적용
def _write_rule(lib: Library, lib2: Library, nid: str) -> str | None:
    """lib2의 규칙 노드 상태를 파일에 쓴다. 바뀐 것이 없으면 None."""
    a, b = lib.nodes.get(nid), lib2.nodes[nid]

    def fm_rules(n):
        return {k: (r["value"] if not r["note"] else {"value": r["value"], "note": r["note"]}) for k, r in n["rules"].items()}

    if a is None:  # 새 노드
        fm = {"id": nid, "type": "rule", "kind": b["kind"], "title": b["title"]}
        if not b.get("implicit_inherits"):
            fm["inherits"] = b["inherits"]
        if b.get("uses"):
            fm["uses"] = b["uses"]
        fm["rules"] = fm_rules(b)
        if b.get("relations"):
            fm["relations"] = b["relations"]
        path = store._free_name(os.path.join(lib.root, b["path"]))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n" +
                    (b.get("body") or "정리 기능으로 만든 노드입니다.") + "\n")
        return os.path.relpath(path, lib.root).replace("\\", "/")
    fields = ("inherits", "implicit_inherits", "uses", "rules", "title", "relations", "body")
    if all(a.get(f) == b.get(f) for f in fields):
        return None

    def mut(fm):
        if b.get("implicit_inherits"):
            fm.pop("inherits", None)
        else:
            fm["inherits"] = b["inherits"]
        if b.get("uses"):
            fm["uses"] = b["uses"]
        else:
            fm.pop("uses", None)
        old = fm.get("rules") or {}
        new = fm_rules(b)
        ordered = {k: (old[k] if k in old and isinstance(old[k], dict) and isinstance(new[k], dict) and old[k] == new[k]
                       else new[k]) for k in list(old) if k in new}
        ordered.update({k: v for k, v in new.items() if k not in ordered})
        fm["rules"] = ordered
        fm["title"] = b["title"]
        if b.get("relations"):
            fm["relations"] = b["relations"]
        else:
            fm.pop("relations", None)
        # 키 순서 정돈: id, type, kind, title, inherits, uses, rules 먼저
        head = ["id", "type", "kind", "title", "inherits", "uses", "rules"]
        rest = {k: v for k, v in fm.items() if k not in head}
        snapshot = {k: fm[k] for k in head if k in fm}
        fm.clear()
        fm.update(snapshot)
        fm.update(rest)
    freq._rewrite_frontmatter(lib.abspath(a["path"]), mut)
    if (a.get("body") or "") != (b.get("body") or ""):
        path = lib.abspath(a["path"])
        with open(path, encoding="utf-8") as f:
            m = FRONTMATTER.match(f.read().lstrip("\ufeff"))
        with open(path, "w", encoding="utf-8") as f:
            f.write("---\n" + m.group(1) + "\n---\n" + (b.get("body") or "") + "\n")
    return a["path"]


def _write_ref(lib: Library, lib2: Library, nid: str) -> str | None:
    a, b = lib.nodes[nid], lib2.nodes[nid]
    if a.get("relations") == b.get("relations") and a.get("title") == b.get("title"):
        return None
    path = lib.abspath(a["path"])
    with open(path, encoding="utf-8") as f:
        meta = yaml.safe_load(f) or {}
    meta["title"] = b["title"]
    if b.get("relations"):
        meta["relations"] = b["relations"]
    else:
        meta.pop("relations", None)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, allow_unicode=True, sort_keys=False)
    return a["path"]


def apply(lib: Library, rid: str, selected: list[str]) -> dict:
    pv = preview(lib, rid, selected)
    if pv.get("waiting"):
        raise FileNotFoundError(pv["message"])
    if pv.get("errors"):
        raise ValueError("적용할 수 없습니다: " + "; ".join(pv["errors"]))
    plan = _load_plan(lib, rid)
    ok = {it["id"] for it in pv["items"] if it.get("checked") and not it.get("error")}
    lib2, _ = simulate(lib, plan, ok)
    store.backup_sync(lib)
    written: list[str] = []
    for nid in sorted(lib2.nodes):
        n = lib2.nodes[nid]
        if n["type"] == "rule":
            r = _write_rule(lib, lib2, nid)
        elif n["type"] != "ai" and nid in lib.nodes and n.get("kind") != "unregistered":
            r = _write_ref(lib, lib2, nid)
        else:
            r = None
        if r:
            written.append(r)
    trashed = []
    for nid in sorted(set(lib.nodes) - set(lib2.nodes)):
        if lib.nodes[nid]["type"] == "rule":
            trashed.append(store.delete_node(lib, nid)["trash_id"])
    req = freq._load(lib.root, rid)
    req.update(status="applied", applied=store.now(), applied_items=sorted(ok))
    freq._save(lib.root, req)
    store.append_history(lib.root, {"kind": "tidy", "node": req.get("node"), "request": rid,
                                    "written": written, "trashed": trashed})
    return {"written": written, "trashed": trashed, "applied": sorted(ok),
            "scope": sorted(x["id"] for x in pv["graph_after"]["nodes"] if not x.get("outside"))}
