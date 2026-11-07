"""구성 요소 분리 (기획서 v3.9, Q81-A).

레퍼런스 이미지 한 장을 캐릭터·도형·구도로 나눠, 드롭할 때 원본과 함께 첨부한다.
삽화를 통째로 넘기면 대상 AI가 캐릭터·도형 디자인을 뭉개는 문제를 줄이려는 것이다.

- 데이터: 원본 메타의 `components:` (name, part, title, box, regions, flow, spec, check)
- 분리는 AI가(P3): `build`가 좌표 격자 사본을 붙인 요청 프롬프트를 만들고, AI는 `plan.yaml`의 `components`로 답한다.
- 자르기와 그리기는 앱이(P2): 같은 원본 + 같은 box → 같은 PNG. 결과는 `.assets/components/<id>/`의 캐시(P1).
- 받은 답은 미리 보고(상자 조정 가능) 사용자가 반영한다(P4). 같은 이름의 기존 요소 바꾸기는 기본 꺼짐(P6).
- 사람이 상세 화면에서 상자를 고치거나 지우는 것은 `save`로 바로 반영한다(P4, 반영 전 보관 P5).

part 종류와 지시문은 `_defaults/keys.yaml`의 `parts:`(없으면 `engine.PART_DEFAULTS`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re

import yaml

from engine import Library, CACHE_DIR, IMAGE_EXT
import folder_requests as freq
import store

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
PARTS_ORDER = ("character", "shape", "composition")
COLORS = [(46, 111, 94), (205, 82, 60), (58, 92, 186), (176, 124, 18), (128, 72, 168), (36, 140, 170)]


# ------------------------------------------------------------------ 형식
def _box(v) -> list[int] | None:
    if isinstance(v, (list, tuple)) and len(v) == 4:
        try:
            b = [int(round(float(x))) for x in v]
        except (TypeError, ValueError):
            return None
        if b[2] > 0 and b[3] > 0:
            return b
    return None


def _points(v) -> list[list[int]]:
    out = []
    for p in v or []:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            try:
                out.append([int(round(float(p[0]))), int(round(float(p[1])))])
            except (TypeError, ValueError):
                pass
    return out


def _checks(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    return [str(x).strip() for x in v if str(x).strip()]


def normalize(raw, parts: dict, size: tuple[int, int] | None = None) -> tuple[list[dict], list[str]]:
    """메타의 components 목록 → (정리된 목록, 문제 목록). 저장 형식과 같은 키 순서."""
    out: list[dict] = []
    problems: list[str] = []
    if raw in (None, ""):
        return out, problems
    if not isinstance(raw, list):
        return out, ["components는 목록이어야 합니다"]
    seen: set[str] = set()
    for i, c in enumerate(raw, 1):
        if not isinstance(c, dict):
            problems.append(f"{i}번째 구성 요소가 매핑이 아닙니다")
            continue
        part = str(c.get("part") or "").strip()
        if part not in parts:
            problems.append(f"{i}번째 구성 요소: 알 수 없는 종류 '{part}' (가능: {', '.join(parts)})")
            continue
        name = str(c.get("name") or part).strip().lower()
        if not NAME_RE.fullmatch(name):
            problems.append(f"{i}번째 구성 요소: 이름 '{name}'은 영문 소문자·숫자·하이픈만 됩니다")
            continue
        base, k = name, 2
        while name in seen:
            name, k = f"{base}-{k}", k + 1
        seen.add(name)
        box = _box(c.get("box"))
        if part != "composition" and not box:
            problems.append(f"{name}: box [x, y, w, h]가 필요합니다")
            continue
        if box and size and (box[0] < 0 or box[1] < 0 or box[0] + box[2] > size[0] + 2 or box[1] + box[3] > size[1] + 2):
            problems.append(f"{name}: box {box}가 이미지({size[0]}×{size[1]}) 밖으로 나갑니다 (잘라 낼 때 맞춥니다)")
        d = {"name": name, "part": part, "title": str(c.get("title") or parts[part].get("label", part))}
        if box:
            d["box"] = box
        if part == "composition":
            regions = []
            for r in c.get("regions") or []:
                if isinstance(r, dict) and _box(r.get("box")):
                    regions.append({"label": str(r.get("label") or ""), "box": _box(r.get("box"))})
            if regions:
                d["regions"] = regions
            flow = _points(c.get("flow"))
            if flow:
                d["flow"] = flow
        d["spec"] = " ".join(str(c.get("spec") or "").split())
        chk = _checks(c.get("check"))
        if chk:
            d["check"] = chk
        out.append(d)
    return out, problems


def part_rel(ref_id: str, name: str) -> str:
    return f"{CACHE_DIR}/components/{ref_id}/{ref_id}.{name}.png"


def legend(comp: dict, all_comps: list[dict] | None = None) -> str:
    regions = comp.get("regions") or [{"label": o.get("title", ""), "box": o.get("box")}
                                      for o in (all_comps or []) if o.get("part") != "composition" and o.get("box")]
    return " · ".join(f"{i} {r['label']}" for i, r in enumerate(regions, 1) if r.get("label"))


# ------------------------------------------------------------------ 그리기 (결정적)
def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _clamp(box: list[int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    x, y, w, h = box
    x0, y0 = max(0, min(x, size[0] - 1)), max(0, min(y, size[1] - 1))
    x1, y1 = max(x0 + 1, min(x + w, size[0])), max(y0 + 1, min(y + h, size[1]))
    return x0, y0, x1, y1


def crop_image(img, box: list[int]):
    """요소 하나를 잘라 흰 여백을 조금 둘러 준다 (외곽선이 잘리지 않게)."""
    from PIL import Image
    x0, y0, x1, y1 = _clamp(box, img.size)
    part = img.crop((x0, y0, x1, y1)).convert("RGB")
    pad = max(8, round(max(part.size) * 0.04))
    out = Image.new("RGB", (part.width + pad * 2, part.height + pad * 2), "white")
    out.paste(part, (pad, pad))
    return out


def composition_image(img, comp: dict, all_comps: list[dict]):
    """구도 도식: 원본을 흐리게 깔고, 덩어리마다 번호 상자, 흐름은 화살표로."""
    from PIL import Image, ImageDraw
    base = Image.blend(img.convert("RGB"), Image.new("RGB", img.size, "white"), 0.62)
    d = ImageDraw.Draw(base)
    W, H = img.size
    lw = max(3, round(max(W, H) / 320))
    regions = comp.get("regions") or [{"label": o.get("title", ""), "box": o["box"]}
                                      for o in all_comps if o.get("part") != "composition" and o.get("box")]
    font = _font(max(18, lw * 7))
    for i, r in enumerate(regions, 1):
        col = COLORS[(i - 1) % len(COLORS)]
        x0, y0, x1, y1 = _clamp(r["box"], img.size)
        d.rectangle((x0, y0, x1, y1), outline=col, width=lw)
        rad = max(14, lw * 5)
        cx, cy = x0 + rad, y0 + rad
        d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=col)
        d.text((cx, cy), str(i), fill="white", font=font, anchor="mm")
    flow = comp.get("flow") or []
    if len(flow) >= 2:
        ink = (34, 34, 34)
        d.line([tuple(p) for p in flow], fill=ink, width=lw * 2, joint="curve")
        (ax, ay), (bx, by) = flow[-2], flow[-1]
        import math
        ang = math.atan2(by - ay, bx - ax)
        L = lw * 9
        for s in (-1, 1):
            d.line([(bx, by), (bx - L * math.cos(ang + s * 0.5), by - L * math.sin(ang + s * 0.5))], fill=ink, width=lw * 2)
        r0 = lw * 3
        d.ellipse((flow[0][0] - r0, flow[0][1] - r0, flow[0][0] + r0, flow[0][1] + r0), fill=ink)
    if comp.get("box"):
        base = base.crop(_clamp(comp["box"], img.size))
    return base


def grid_image(img):
    """AI용 좌표 격자 사본: 일정 간격 선 + 가장자리 좌표 숫자 (원본 픽셀 기준)."""
    from PIL import Image, ImageDraw
    W, H = img.size
    step = next(s for s in (25, 50, 100, 200, 250, 500, 1000, 10 ** 9) if max(W, H) / s <= 20)
    base = img.convert("RGBA")
    over = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    lw = max(1, round(max(W, H) / 1000))
    for x in range(0, W, step):
        d.line([(x, 0), (x, H)], fill=(255, 0, 80, 110 if x % (step * 2) else 170), width=lw)
    for y in range(0, H, step):
        d.line([(0, y), (W, y)], fill=(255, 0, 80, 110 if y % (step * 2) else 170), width=lw)
    font = _font(max(12, round(max(W, H) / 90)))
    for x in range(step, W, step):
        d.rectangle(d.textbbox((x + 2, 2), str(x), font=font), fill=(255, 255, 255, 220))
        d.text((x + 2, 2), str(x), fill=(200, 0, 60, 255), font=font)
    for y in range(step, H, step):
        d.rectangle(d.textbbox((2, y + 2), str(y), font=font), fill=(255, 255, 255, 220))
        d.text((2, y + 2), str(y), fill=(200, 0, 60, 255), font=font)
    return Image.alpha_composite(base, over).convert("RGB")


def _render_all(src: str, comps: list[dict], out_dir: str, ref_id: str) -> dict[str, str]:
    from PIL import Image
    os.makedirs(out_dir, exist_ok=True)
    out = {}
    with Image.open(src) as im:
        im.load()
        for c in comps:
            pic = composition_image(im, c, comps) if c["part"] == "composition" else crop_image(im, c["box"])
            path = os.path.join(out_dir, f"{ref_id}.{c['name']}.png")
            pic.save(path)
            out[c["name"]] = path
    return out


def _stamp(src: str, comps: list[dict]) -> str:
    st = os.stat(src)
    key = json.dumps({"size": st.st_size, "mtime": st.st_mtime_ns, "c": comps}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def ensure(lib: Library, node: dict) -> bool:
    """노드의 요소 그림 캐시를 맞춘다. 원본이 없거나 그릴 수 없으면 False (조립은 글로 대체)."""
    comps = node.get("components") or []
    src = lib.abspath(node["file"])
    if not comps or not node.get("exists") or os.path.splitext(src)[1].lower() not in IMAGE_EXT:
        return False
    out_dir = os.path.join(lib.root, CACHE_DIR, "components", node["id"])
    stamp_p = os.path.join(out_dir, "stamp.txt")
    want = _stamp(src, comps)
    files = [os.path.join(out_dir, f"{node['id']}.{c['name']}.png") for c in comps]
    try:
        with open(stamp_p, encoding="utf-8") as f:
            if f.read().strip() == want and all(os.path.isfile(p) for p in files):
                return True
    except OSError:
        pass
    try:
        _render_all(src, comps, out_dir, node["id"])
    except Exception:
        return False
    with open(stamp_p, "w", encoding="utf-8") as f:
        f.write(want)
    return True


def _image_size(lib: Library, node: dict) -> tuple[int, int] | None:
    ex = node.get("extracted") or {}
    if ex.get("width") and ex.get("height"):
        return int(ex["width"]), int(ex["height"])
    p = lib.abspath(node["file"])
    if node.get("exists") and os.path.splitext(p)[1].lower() in IMAGE_EXT:
        try:
            from PIL import Image
            with Image.open(p) as im:
                return im.size
        except Exception:
            return None
    return None


# ------------------------------------------------------------------ 요청 (AI가 나눔)
def _check_node(lib: Library, nid: str) -> dict:
    n = lib.nodes.get(nid)
    if not n or n["type"] != "image" or n.get("kind") == "unregistered":
        raise KeyError(f"등록된 이미지 레퍼런스가 아닙니다: {nid}")
    if not n.get("exists"):
        raise FileNotFoundError("원본 이미지가 없어 나눌 수 없습니다")
    return n


def render_request(lib: Library, nid: str, rid: str, target: str | None) -> str:
    n = lib.nodes[nid]
    parts = lib.parts
    agent = (lib.targets.get(target) or {}).get("kind") == "agent"
    size = _image_size(lib, n) or (0, 0)
    grid_rel = f"{freq.INBOX}/{rid}/src/{nid}.grid.png"
    pal = (n.get("extracted") or {}).get("palette") or []
    L = ["# Assets Manager: 레퍼런스 구성 요소 분리 요청", "",
         f"요청 id: `{rid}` · 레퍼런스: **{n['title']}** (`{nid}`) · 이미지 {size[0]}×{size[1]} px", "",
         "이 이미지는 다른 AI에게 그림을 그리게 할 때 넘기는 레퍼런스입니다. 통째로 넘기면 캐릭터와 도형 디자인이 뭉개져서, "
         "**캐릭터·도형·구도를 따로 떼어** 함께 넘기려 합니다. 어디를 떼어 낼지(좌표)와 각 요소를 그대로 다시 그릴 수 있는 설명을 정해 주세요. "
         "자르기와 도식 그리기는 앱이 합니다. 파일을 직접 고치지 말고 `plan.yaml` 하나로 답하세요.", ""]
    if agent:
        L += ["## 볼 파일", f"- 원본: {lib.abspath(n['file'])}", f"- 좌표 격자 사본: {lib.abspath(grid_rel)}", ""]
    else:
        L += ["## 첨부", f"- [1] {os.path.basename(n['file'])} — 원본", f"- [2] {nid}.grid.png — 좌표 격자를 그린 사본", ""]
    L += ["## 나눌 요소",
          "- **character (캐릭터)**: 그림에 나오는 캐릭터. 같은 디자인이 여러 번 나오면 가장 잘 보이는 **하나**만, 디자인이 다른 캐릭터가 있으면 따로(최대 3개). "
          "box는 캐릭터 한 명의 머리부터 발까지를 꼭 맞게 감쌉니다(들고 있는 소품은 빼도 됩니다).",
          "- **shape (도형)**: 그림에서 되풀이되는 대표 도형 1~3개(블록, 상자, 아이콘, 말풍선, 화살표 등). 종류마다 가장 잘 보이는 하나만 고릅니다.",
          "- **composition (구도)**: 1개. `regions`에 큰 덩어리 3~6개(짧은 한국어 `label` + box), `flow`에 시선·이야기가 흘러가는 점 2~6개를 순서대로. box는 생략(전체 그림).",
          "",
          "## 좌표",
          f"- 원본 픽셀 기준 `[x, y, w, h]` 정수입니다(왼쪽 위가 0,0, 이미지 {size[0]}×{size[1]}). 격자 사본의 빨간 숫자를 읽어서 정하세요. 눈짐작으로 쓰지 마세요.",
          "- 요소가 잘리지 않게 테두리를 포함하되, 이웃 요소는 최대한 덜 들어가게 합니다.",
          "",
          "## spec 쓰는 법 (가장 중요)",
          "다른 AI가 이 설명과 잘라 낸 그림만 보고 **같은 디자인으로 새 장면을 그릴 수 있게** 씁니다. 분위기 말고 재현에 필요한 사실을 쓰세요.",
          "- 캐릭터: 머리와 몸의 비율(예: 머리:몸 = 1:1.2), 얼굴 요소(눈 모양·크기·위치, 입·코·눈썹의 유무), 팔다리 모양과 끝처리, 외곽선 굵기(원본 px), 채색(면 색, 그림자 유무).",
          "- 도형: 비율, 모서리 둥글기, 외곽선 굵기와 색, 면 채색, 라벨을 넣는 위치와 글자 느낌, 쌓거나 겹칠 때의 규칙.",
          "- 구도: 흐름 방향, 덩어리끼리의 크기 대비, 기준선(바닥선), 여백, 시선이 시작하고 끝나는 곳.",
          "- `check`: 요소마다 1~2개. 결과 그림을 보고 예/아니오로 판정할 수 있는 \"~인가\" 문장.", ""]
    if pal:
        L += ["참고: 앱이 뽑은 주요 색 " + " ".join(pal[:8]) + " (색을 쓸 때는 이 값을 쓰세요)", ""]
    if n.get("components"):
        L += ["## 지금 있는 요소 (고칠 것이 있으면 같은 name으로 다시 쓰세요)", "```yaml",
              yaml.safe_dump({"components": [_strip(c) for c in n["components"]]}, allow_unicode=True,
                             sort_keys=False, width=10 ** 6, default_flow_style=None).rstrip(), "```", ""]
    L += ["## 답변 형식: `plan.yaml`", "```yaml",
          f"request: {rid}",
          f"node: {nid}",
          "summary: 한 줄 요약",
          "components:",
          "  - name: character            # 영문 소문자-하이픈, 요소마다 다르게",
          "    part: character            # " + " | ".join(p for p in parts),
          "    title: 흰 점눈 캐릭터",
          "    box: [x, y, w, h]",
          "    spec: |                    # 여러 줄이면 이렇게. 한 줄에 쓸 때 #이 들어가면 따옴표로 감싸기",
          "      ...",
          "    check: [\"...인가\"]",
          "  - name: block",
          "    part: shape",
          "    title: 라벨 블록",
          "    box: [x, y, w, h]",
          "    spec: |",
          "      ...",
          "  - name: composition",
          "    part: composition",
          "    title: 왼쪽에서 오른쪽으로 내려가는 3단 흐름",
          "    regions:",
          "      - { label: 블록 탑, box: [x, y, w, h] }",
          "    flow: [[x, y], [x, y], [x, y]]",
          "    spec: |",
          "      ...",
          "```", ""]
    if agent:
        L += [f"이 파일을 `{freq.INBOX}/{rid}/out/plan.yaml`에 쓰세요. 다른 파일은 건드리지 마세요."]
    else:
        L += ["답변은 **`plan.yaml` 내용을 담은 yaml 코드 블록 하나**로 주세요. 사용자가 그대로 복사해 앱에 붙여넣습니다."]
    return "\n".join(L) + "\n"


def _strip(c: dict) -> dict:
    return {k: v for k, v in c.items() if k not in ("file", "abs") and v not in (None, "", [])}


def build(lib: Library, nid: str, target: str | None = None) -> dict:
    n = _check_node(lib, nid)
    req = freq.new_request(lib, nid, kind="components", status="sent")
    req.update(target=target, sent=store.now())
    freq._save(lib.root, req)
    rdir = freq._rdir(lib.root, req["id"])
    from PIL import Image
    grid_p = os.path.join(rdir, "src", f"{nid}.grid.png")
    with Image.open(lib.abspath(n["file"])) as im:
        im.load()
        grid_image(im).save(grid_p)
    prompt = render_request(lib, nid, req["id"], target)
    prompt_p = os.path.join(rdir, "prompt.md")
    with open(prompt_p, "w", encoding="utf-8") as f:
        f.write(prompt)
    return {"request": req["id"], "prompt": prompt, "prompt_path": prompt_p, "drag_icon": lib.drag_icon(),
            "files": [prompt_p, lib.abspath(n["file"]), grid_p],
            "grid": f"{freq.INBOX}/{req['id']}/src/{nid}.grid.png", "current": n.get("components") or []}


def open_request(lib: Library, nid: str) -> dict:
    for r in freq.list_requests(lib.root):
        if r.get("kind") == "components" and r.get("node") == nid and r.get("status") in ("sent", "received"):
            has = os.path.isfile(os.path.join(freq._rdir(lib.root, r["id"]), "out", "plan.yaml"))
            return {"request": r["id"], "status": r["status"], "preview": preview(lib, r["id"]) if has else None}
    return {"request": None}


def receive(lib: Library, rid: str, text: str) -> dict:
    import tidy
    plan = tidy.parse_plan_text(text)
    out = os.path.join(freq._rdir(lib.root, rid), "out")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "plan.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(plan, f, allow_unicode=True, sort_keys=False)
    req = freq._load(lib.root, rid)
    req.update(status="received", received=store.now())
    freq._save(lib.root, req)
    return preview(lib, rid)


def _plan(lib: Library, rid: str) -> dict:
    p = os.path.join(freq._rdir(lib.root, rid), "out", "plan.yaml")
    if not os.path.isfile(p):
        raise FileNotFoundError("아직 AI 답(plan.yaml)이 없습니다")
    with open(p, encoding="utf-8") as f:
        plan = yaml.safe_load(f) or {}
    if not isinstance(plan, dict):
        raise ValueError("plan.yaml 형식이 아닙니다")
    return plan


def _apply_edits(raw: list, edits: dict | None) -> list:
    """미리보기에서 사용자가 끌어 고친 box·regions·flow를 덮는다. edits = {name: {box?, regions?, flow?}}"""
    if not edits:
        return raw
    out = []
    for c in raw:
        c = dict(c) if isinstance(c, dict) else c
        if isinstance(c, dict):
            e = edits.get(str(c.get("name") or c.get("part") or ""))
            if e:
                for k in ("box", "regions", "flow", "title", "spec"):
                    if k in e:
                        c[k] = e[k]
        out.append(c)
    return out


def preview(lib: Library, rid: str, edits: dict | None = None) -> dict:
    req = freq._load(lib.root, rid)
    nid = req.get("node")
    n = _check_node(lib, nid)
    plan = _plan(lib, rid)
    if plan.get("node") and plan["node"] != nid:
        raise ValueError(f"이 답은 다른 노드({plan['node']})에 대한 것입니다")
    size = _image_size(lib, n)
    stored = dict(req.get("edits") or {})
    if edits:  # 이번 수정을 앞선 수정과 합친다 (요소별로 덮음)
        for name, e in edits.items():
            stored[name] = dict(stored.get(name) or {}, **e)
        req["edits"] = stored
        freq._save(lib.root, req)
    raw = _apply_edits(plan.get("components") or [], stored)
    comps, problems = normalize(raw, lib.parts, size)
    pdir = os.path.join(freq._rdir(lib.root, rid), "preview")
    pics = _render_all(lib.abspath(n["file"]), comps, pdir, nid) if comps else {}
    have = {c["name"]: c for c in n.get("components") or []}
    items = []
    for c in comps:
        old = have.get(c["name"])
        status = "new" if not old else ("same" if _strip(old) == _strip(c) else "replace")
        items.append(dict(c, status=status, checked=status == "new",
                          legend=legend(c, comps) if c["part"] == "composition" else "",
                          image=f"{freq.INBOX}/{rid}/preview/{nid}.{c['name']}.png"
                          + f"?v={hashlib.sha1(json.dumps(c, sort_keys=True).encode()).hexdigest()[:8]}"))
    return {"request": rid, "node": nid, "title": n["title"], "image": n["file"], "size": list(size or (0, 0)),
            "summary": plan.get("summary") or "", "items": items, "problems": problems,
            "current": [_strip(c) for c in n.get("components") or []],
            "parts": {k: v.get("label", k) for k, v in lib.parts.items()}}


def _write_meta(lib: Library, n: dict, comps: list[dict]) -> str:
    path = lib.abspath(n["path"])
    with open(path, encoding="utf-8") as f:
        meta = yaml.safe_load(f) or {}
    clean = [_strip(c) for c in comps]
    if clean:
        meta["components"] = clean
    else:
        meta.pop("components", None)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, allow_unicode=True, sort_keys=False, width=10 ** 6, default_flow_style=None)
    return n["path"]


def apply(lib: Library, rid: str, selected: list[str], edits: dict | None = None) -> dict:
    pv = preview(lib, rid, edits)
    n = lib.nodes[pv["node"]]
    chosen = {it["name"]: it for it in pv["items"] if it["name"] in set(selected or [])}
    if not chosen:
        raise ValueError("반영할 요소를 하나 이상 고르세요")
    merged: list[dict] = []
    for c in n.get("components") or []:
        merged.append(chosen.pop(c["name"]) if c["name"] in chosen else c)
    for it in pv["items"]:  # 새 요소는 답의 순서대로 뒤에
        if it["name"] in chosen:
            merged.append(chosen[it["name"]])
    keep = ("name", "part", "title", "box", "regions", "flow", "spec", "check")
    merged = [{k: c[k] for k in keep if k in c} for c in merged]
    store.backup_sync(lib)
    written = _write_meta(lib, n, merged)
    req = freq._load(lib.root, rid)
    req.update(status="applied", applied=store.now(), applied_items=sorted(set(selected or [])))
    freq._save(lib.root, req)
    store.append_history(lib.root, {"kind": "components", "node": n["id"], "request": rid,
                                    "applied": sorted(set(selected or []))})
    return {"node": n["id"], "written": [written], "applied": sorted(set(selected or []))}


def save(lib: Library, nid: str, comps: list) -> dict:
    """상세 화면에서 사람이 고친 요소 목록을 그대로 저장한다 (상자 조정·이름·설명·지우기)."""
    n = _check_node(lib, nid) if lib.nodes.get(nid, {}).get("exists") else lib.nodes[nid]
    clean, problems = normalize(comps, lib.parts, _image_size(lib, n))
    hard = [p for p in problems if "밖으로" not in p]
    if hard:
        raise ValueError("; ".join(hard))
    store.backup_sync(lib)
    written = _write_meta(lib, n, clean)
    store.append_history(lib.root, {"kind": "components", "node": nid, "actor": "user",
                                    "names": [c["name"] for c in clean]})
    return {"node": nid, "written": [written], "components": clean, "warnings": problems}
