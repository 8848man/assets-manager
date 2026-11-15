"""선택형 Chromium 렌더링 추출(Q88). 설정 버튼을 누를 때만 설치한다.

HTML 파일을 오프라인 브라우저로 열어 계산된 스타일과 CSS 애니메이션을 읽는다.
브라우저가 없거나 꺼져 있으면 기존 extract.py의 정적 추출만 사용한다.
"""
from __future__ import annotations

import importlib.util
import importlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

CONFIG = os.path.join(".assets", "render", "config.json")
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _config(root: str) -> dict:
    try:
        with open(os.path.join(root, CONFIG), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def enabled(root: str) -> bool:
    return bool(_config(root).get("enabled"))


def _browser_ready() -> bool:
    if importlib.util.find_spec("playwright") is None:
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            return os.path.isfile(p.chromium.executable_path)
    except Exception:
        return False


def status(root: str) -> dict:
    ready = _browser_ready()
    return {"enabled": enabled(root), "ready": ready, "active": ready and enabled(root),
            "job": _JOBS.get(os.path.abspath(root))}


def set_enabled(root: str, value: bool) -> dict:
    path = os.path.join(root, CONFIG)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"enabled": bool(value)}, f)
    return status(root)


def install(root: str) -> dict:
    """사용자의 설정 버튼으로만 패키지와 Chromium을 내려받는다."""
    key = os.path.abspath(root)
    with _LOCK:
        if _JOBS.get(key, {}).get("state") == "running":
            return status(root)
        job = {"state": "running", "step": "패키지 확인"}
        _JOBS[key] = job

    def work():
        try:
            kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
            if importlib.util.find_spec("playwright") is None:
                job["step"] = "Playwright 설치"
                subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600, **kw)
                importlib.invalidate_caches()
            job["step"] = "Chromium 내려받기"
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=900, **kw)
            set_enabled(root, True)
            job.update(state="done", step="준비됨")
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            job.update(state="failed", error=str(e))

    threading.Thread(target=work, daemon=True).start()
    return status(root)


def extract(path: str) -> dict:
    """로컬 HTML의 실제 레이아웃·계산 스타일·애니메이션을 고정된 뷰포트에서 읽는다."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 800}, offline=True,
                                          java_script_enabled=False)
            page = context.new_page()
            page.goto(Path(path).resolve().as_uri(), wait_until="load", timeout=15000)
            return page.evaluate("""() => {
              const count = (sel) => document.querySelectorAll(sel).length;
              const samples = [...document.querySelectorAll('body *')].slice(0, 100).map(el => {
                const s = getComputedStyle(el);
                return { tag: el.tagName.toLowerCase(), color: s.color, background: s.backgroundColor,
                  font: s.fontFamily, size: s.fontSize, radius: s.borderRadius,
                  display: s.display, animation: s.animationName, duration: s.animationDuration,
                  easing: s.animationTimingFunction };
              });
              return { viewport: [1280, 800], title: document.title,
                sections: count('section'), headings: { h1: count('h1'), h2: count('h2'), h3: count('h3') },
                computed_styles: samples, animations: samples.filter(x => x.animation !== 'none') };
            }""")
        finally:
            browser.close()
