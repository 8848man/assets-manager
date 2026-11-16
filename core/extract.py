"""결정적 값 추출기 (기획서 v3 6.1). AI 없이 계산하므로 같은 파일이면 같은 결과가 나온다.

사용:  python extract.py <파일> [<파일> ...]
출력:  파일마다 JSON 한 덩어리 (UTF-8)

이미지  크기, 비율, 주요 색 hex
글      문단 수, 평균 문장 길이, 소제목·불릿·이모지 수, 존댓말 비율
HTML/CSS 색, 폰트, 여백, 애니메이션 (기본은 정적 분석, 설정에서 켜면 Chromium 렌더링 결과도 추출)
PDF     페이지 수와 크기, 폰트와 글자 크기 (PyMuPDF가 있으면)

이 파일 하나만 있으면 동작한다(외부 AI의 코드 실행 환경에 그대로 올려도 됨). 필요한 패키지: Pillow, (선택) PyMuPDF
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
WEB_EXT = {".html", ".htm", ".css"}
DOC_EXT = {".pdf"}
TEXT_EXT = {".txt", ".md"}


def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in WEB_EXT:
        return "web"
    if ext in DOC_EXT:
        return "document"
    if ext in TEXT_EXT:
        return "writing"
    return "unknown"


def extract(path: str) -> dict:
    k = kind_of(path)
    out = {"file": os.path.basename(path), "kind": k, "bytes": os.path.getsize(path)}
    try:
        if k == "image":
            out.update(extract_image(path))
        elif k == "web":
            out.update(extract_web(path))
            if path.lower().endswith((".html", ".htm")):
                try:
                    import render_web
                    root = os.environ.get("ASSETS_MANAGER_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    if render_web.enabled(root) and render_web.status(root)["active"]:
                        out["rendered"] = render_web.extract(path)
                except ImportError:
                    pass  # 외부 AI에 단독으로 올린 extract.py는 정적 추출만 쓴다.
                except Exception as e:
                    out["render_error"] = f"{type(e).__name__}: {e}"
        elif k == "document":
            out.update(extract_pdf(path))
        elif k == "writing":
            out.update(extract_text(path))
    except Exception as e:  # 추출 실패는 값이 없을 뿐, 등록은 계속 가능
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------- 이미지
def extract_image(path: str) -> dict:
    from PIL import Image
    im = Image.open(path)
    w, h = im.size
    rgb = im.convert("RGB")
    small = rgb.copy()
    small.thumbnail((240, 240))
    q = small.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    total = small.size[0] * small.size[1]
    colors = []
    for count, idx in sorted(q.getcolors(), reverse=True):
        r, g, b = pal[idx * 3: idx * 3 + 3]
        colors.append(("#%02X%02X%02X" % (r, g, b), count / total, (r, g, b)))

    def neutral(c):  # 흰색·검은색·회색 계열 (배경과 선)
        r, g, b = c
        return max(r, g, b) - min(r, g, b) < 18

    accents = [(hx, round(p, 3)) for hx, p, c in colors if not neutral(c)][:6]
    neutrals = [(hx, round(p, 3)) for hx, p, c in colors if neutral(c)][:3]
    return {
        "width": w, "height": h, "aspect": _aspect(w, h),
        "palette": [hx for hx, _ in accents],
        "palette_share": dict(accents),
        "neutrals": [hx for hx, _ in neutrals],
        "background": colors[0][0] if colors else None,
    }


def _aspect(w: int, h: int) -> str:
    from math import gcd
    g = gcd(w, h) or 1
    a, b = w // g, h // g
    return f"{a}:{b}" if a <= 50 and b <= 50 else f"{w / h:.2f}:1"


# ---------------------------------------------------------------- 글
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
SENT_END = re.compile(r"(?<=[.!?。])\s+|(?<=[요다죠까네])\s*[.!?]?\s*\n")


def extract_text(path: str) -> dict:
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if not text.strip():
        return {"empty": True}
    lines = text.splitlines()
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    body = "\n".join(l for l in lines if not l.lstrip().startswith(("#", "-", "*", ">")))
    sents = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", body) if len(s.strip()) > 1]
    polite = sum(1 for s in sents if re.search(r"(요|니다|세요|죠)[.!?]?$", s))
    plain = sum(1 for s in sents if re.search(r"(다|어|야|지|네)[.!?]?$", s) and not re.search(r"니다[.!?]?$", s))
    return {
        "chars": len(text),
        "paragraphs": len(paras),
        "avg_paragraph_chars": round(sum(len(p) for p in paras) / max(1, len(paras))),
        "sentences": len(sents),
        "avg_sentence_chars": round(sum(len(s) for s in sents) / max(1, len(sents))),
        "headings": sum(1 for l in lines if l.lstrip().startswith("#")),
        "bullets": sum(1 for l in lines if re.match(r"\s*([-*•]|\d+\.)\s", l)),
        "emoji": len(EMOJI.findall(text)),
        "polite_ratio": round(polite / max(1, polite + plain), 2),
    }


# ---------------------------------------------------------------- HTML/CSS
HEX = re.compile(r"#(?:[0-9a-fA-F]{3}){1,2}\b")
RGB = re.compile(r"rgba?\([^)]*\)")
DECL = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;{}]+)")


def extract_web(path: str) -> dict:
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    css = src
    if path.lower().endswith((".html", ".htm")):
        blocks = re.findall(r"<style[^>]*>(.*?)</style>", src, re.S | re.I)
        inline = re.findall(r'style="([^"]*)"', src, re.I)
        base = os.path.dirname(path)
        linked = []
        for href in re.findall(r'<link[^>]+href="([^"]+\.css)"', src, re.I):
            p = os.path.normpath(os.path.join(base, href))
            if os.path.isfile(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    linked.append(f.read())
        css = "\n".join(blocks + linked + [i + ";" for i in inline])
    decls = [(k.lower(), v.strip()) for k, v in DECL.findall(css)]

    def top(props, n=6, split=False):
        c = Counter()
        for k, v in decls:
            if k in props:
                if split:
                    for part in v.split():
                        c[part] += 1
                else:
                    c[v] += 1
        return [v for v, _ in c.most_common(n)]

    colors = Counter(HEX.findall(css) + RGB.findall(css))
    variables = {k: v for k, v in decls if k.startswith("--")}
    out = {
        "colors": [c for c, _ in colors.most_common(10)],
        "css_variables": dict(list(variables.items())[:30]),
        "font_family": top({"font-family"}, 4),
        "font_size": top({"font-size"}, 8),
        "font_weight": top({"font-weight"}, 4),
        "line_height": top({"line-height"}, 4),
        "spacing": top({"padding", "margin", "gap", "padding-top", "padding-bottom", "margin-top",
                        "margin-bottom", "row-gap", "column-gap"}, 8, split=True),
        "radius": top({"border-radius"}, 5),
        "shadow": top({"box-shadow"}, 3),
        "transition": top({"transition"}, 4),
        "animation": top({"animation"}, 4),
        "keyframes": sorted(set(re.findall(r"@keyframes\s+([\w-]+)", css))),
        "durations": [v for v, _ in Counter(re.findall(r"(?<![\w.])\d*\.?\d+m?s\b", " ".join(
            v for k, v in decls if k.startswith(("transition", "animation"))))).most_common(5)],
        "easings": [v for v, _ in Counter(re.findall(r"cubic-bezier\([^)]*\)|ease-in-out|ease-in|ease-out|\bease\b|linear",
                                                     " ".join(v for k, v in decls if k.startswith(("transition", "animation"))))).most_common(4)],
    }
    if path.lower().endswith((".html", ".htm")):
        out["sections"] = len(re.findall(r"<section\b", src, re.I))
        out["headings"] = {f"h{i}": len(re.findall(rf"<h{i}\b", src, re.I)) for i in range(1, 4)}
    return out


# ---------------------------------------------------------------- PDF
def extract_pdf(path: str) -> dict:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return {"note": "PyMuPDF가 없어 PDF 값을 뽑지 못했습니다 (pip install pymupdf)"}
    doc = fitz.open(path)
    fonts, sizes = Counter(), Counter()
    for page in list(doc)[:10]:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    fonts[span["font"]] += len(span["text"])
                    sizes[round(span["size"], 1)] += len(span["text"])
    r = doc[0].rect if len(doc) else None
    return {
        "pages": len(doc),
        "page_size_pt": [round(r.width), round(r.height)] if r else None,
        "fonts": [f for f, _ in fonts.most_common(5)],
        "font_sizes": [s for s, _ in sizes.most_common(6)],
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for p in sys.argv[1:]:
        print(json.dumps(extract(p), ensure_ascii=False, indent=2))
