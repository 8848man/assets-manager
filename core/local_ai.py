"""로컬 AI 도구 연결 (기획서 v3.5, Q76-B).

이 컴퓨터에 설치된 AI 명령줄 도구(Claude Code, Codex CLI)를 찾아 두고, 사용자가 버튼을 누르면
AI가 필요한 요청(하위 정리, AI에게 맡기기, 파일 등록)을 그 도구로 실행한다.

- 앱은 여전히 AI API를 직접 부르지 않는다. 사용자가 이미 로그인한 로컬 도구를 실행할 뿐이다(구독 그대로).
- 실행은 항상 사용자가 누른 버튼으로만 시작한다. 자동·예약 실행은 없다.
- 도구에게 주는 지시는 짧다: "inbox/<요청>/…/prompt.md를 읽고 수행하라". 프롬프트 본문은 기존 에이전트용 프롬프트
  그대로라서 결정적이고(P2), 결과는 언제나 파일 → 미리보기 → 적용(P4)을 거친다.
- 작업 폴더(cwd)는 라이브러리 루트. 도구는 루트의 AGENTS.md / CLAUDE.md(쓰기 위치 규칙)를 읽는다.

설정은 기기마다 다르므로 `.assets/local_ai.json`에 둔다(지우면 다시 찾으면 된다).
작업 기록: `.assets/local-ai/jobs/<job>.json`, 출력: `<job>.log`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

CONFIG = os.path.join(".assets", "local_ai.json")
JOBS = os.path.join(".assets", "local-ai", "jobs")
TIMEOUT = 20 * 60
WIN = os.name == "nt"

TOOLS = {
    "claude": {
        "label": "Claude Code",
        "exe": ["claude"],
        "candidates": ["~/.local/bin/claude.exe", "~/.local/bin/claude", "~/.claude/local/claude",
                       "%APPDATA%/npm/claude.cmd", "/usr/local/bin/claude", "/opt/homebrew/bin/claude"],
        # -p: 대화 없이 한 번 실행. acceptEdits: 파일 쓰기 허용. 허용 도구는 읽기·쓰기·검색·파이썬(extract.py)만.
        "args": ["-p", "{instruction}", "--permission-mode", "acceptEdits",
                 "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash(python:*),Bash(py:*),Bash(python3:*)",
                 "--max-turns", "60", "--output-format", "text"],
        "test_args": ["-p", "{instruction}", "--max-turns", "1", "--output-format", "text"],
        "install": [
            {"label": "Windows PowerShell", "cmd": "irm https://claude.ai/install.ps1 | iex"},
            {"label": "Windows CMD", "cmd": "curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd && del install.cmd"},
            {"label": "npm", "cmd": "npm install -g @anthropic-ai/claude-code"},
        ],
        "login": "터미널에서 claude 를 한 번 실행해 브라우저로 로그인 (Pro·Max·Team 구독)",
        "docs": "https://code.claude.com/docs/en/setup",
    },
    "codex": {
        "label": "Codex CLI",
        "exe": ["codex"],
        "candidates": ["%APPDATA%/npm/codex.cmd", "~/.local/bin/codex", "/usr/local/bin/codex", "/opt/homebrew/bin/codex"],
        # exec: 대화 없이 한 번 실행. --full-auto: 작업 폴더 안에서 파일 쓰기 허용(샌드박스 workspace-write).
        "args": ["exec", "--full-auto", "--skip-git-repo-check", "{instruction}"],
        "test_args": ["exec", "--skip-git-repo-check", "{instruction}"],
        "install": [{"label": "npm", "cmd": "npm install -g @openai/codex"}],
        "login": "터미널에서 codex login (또는 codex 실행 후 ChatGPT 계정으로 로그인)",
        "docs": "https://github.com/openai/codex",
    },
}

_jobs: dict[str, dict] = {}
_procs: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()


# ------------------------------------------------------------------ 설정
def _cfg_path(root: str) -> str:
    return os.path.join(root, CONFIG)


def load_config(root: str) -> dict:
    try:
        with open(_cfg_path(root), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("tools", {})
    cfg.setdefault("default", None)
    for k in TOOLS:
        cfg["tools"].setdefault(k, {})
    return cfg


def save_config(root: str, cfg: dict) -> dict:
    os.makedirs(os.path.dirname(_cfg_path(root)), exist_ok=True)
    with open(_cfg_path(root), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def _expand(p: str) -> str:
    return os.path.expanduser(os.path.expandvars(p.replace("/", os.sep)))


def _cmd(path: str, args: list[str]) -> list[str]:
    if WIN and path.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", path] + args
    return [path] + args


def _env() -> dict:
    """자식 프로세스 출력은 UTF-8로 (파이썬 도구는 Windows에서 기본이 cp949라 로그가 깨진다)."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _decode(data: bytes) -> str:
    """UTF-8로 읽고, 아니면 이 PC의 기본 인코딩(Windows 한국어 = cp949)으로. 잘린 앞머리 바이트는 버린다."""
    for cut in range(4):
        try:
            return data[cut:].decode("utf-8")
        except UnicodeDecodeError as e:
            if e.start > cut:  # 앞머리가 아니라 중간에서 깨짐 → UTF-8이 아님
                break
    import locale
    return data.decode(locale.getpreferredencoding(False) or "utf-8", "replace")


def _run(cmd: list[str], cwd: str, timeout: int) -> tuple[int, str]:
    kw = {"creationflags": 0x08000000} if WIN else {}  # CREATE_NO_WINDOW
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, env=_env(), **kw)
    out = _decode(p.stdout or b"") + _decode(p.stderr or b"")
    return p.returncode, out.strip()


def find(name: str, override: str | None = None) -> str | None:
    if override:
        p = _expand(override)
        return p if os.path.isfile(p) else None
    for exe in TOOLS[name]["exe"]:
        p = shutil.which(exe)
        if p:
            return p
    for c in TOOLS[name]["candidates"]:
        p = _expand(c)
        if os.path.isfile(p):
            return p
    return None


def detect(root: str) -> dict:
    """설치된 도구를 찾아 버전을 확인하고 설정에 저장한다. 기본 도구가 없으면 처음 찾은 것으로."""
    cfg = load_config(root)
    for name in TOOLS:
        t = cfg["tools"][name]
        path = find(name, t.get("custom_path"))
        t.update(path=path, version=None, found=bool(path), checked=time.strftime("%Y-%m-%dT%H:%M:%S"))
        if path:
            try:
                code, out = _run(_cmd(path, ["--version"]), root, 20)
                t["version"] = out.splitlines()[0][:80] if out else None
                t["found"] = code == 0
            except (OSError, subprocess.TimeoutExpired) as e:
                t.update(found=False, error=str(e))
        t.setdefault("enabled", True)
    if not cfg.get("default") or not cfg["tools"].get(cfg["default"], {}).get("found"):
        cfg["default"] = next((n for n in TOOLS if cfg["tools"][n].get("found")), None)
    return save_config(root, cfg)


def status(root: str) -> dict:
    cfg = load_config(root)
    tools = []
    for name, spec in TOOLS.items():
        t = cfg["tools"][name]
        tools.append({"name": name, "label": spec["label"], "path": t.get("path"), "custom_path": t.get("custom_path"),
                      "found": bool(t.get("found")), "version": t.get("version"), "enabled": t.get("enabled", True),
                      "tested": t.get("tested"), "test_ok": t.get("test_ok"), "args": t.get("args") or spec["args"],
                      "default_args": spec["args"], "install": spec["install"], "login": spec["login"], "docs": spec["docs"]})
    d = cfg.get("default")
    ready = bool(d and cfg["tools"].get(d, {}).get("found") and cfg["tools"][d].get("enabled", True))
    return {"default": d, "ready": ready, "tools": tools, "detected": any(t.get("checked") for t in cfg["tools"].values()),
            "running": [j for j in list_jobs(root) if j["state"] == "running"]}


def update_config(root: str, body: dict) -> dict:
    cfg = load_config(root)
    if "default" in body:
        cfg["default"] = body["default"] if body["default"] in TOOLS else None
    for name, patch in (body.get("tools") or {}).items():
        if name not in TOOLS:
            continue
        t = cfg["tools"][name]
        if "enabled" in patch:
            t["enabled"] = bool(patch["enabled"])
        if "custom_path" in patch:
            t["custom_path"] = (patch["custom_path"] or "").strip() or None
        if "args" in patch:
            a = patch["args"]
            t["args"] = [str(x) for x in a] if a else None
    save_config(root, cfg)
    if any("custom_path" in (p or {}) for p in (body.get("tools") or {}).values()):
        detect(root)
    return status(root)


def test(root: str, name: str) -> dict:
    """연결 시험: 아주 짧은 질문 하나. 로그인 안 됨·경로 문제를 여기서 잡는다."""
    cfg = load_config(root)
    t = cfg["tools"].get(name) or {}
    if not t.get("path"):
        return {"ok": False, "output": "도구를 찾지 못했습니다. 먼저 설치하고 '다시 찾기'를 누르세요."}
    args = [a.replace("{instruction}", "Assets Manager 연결 시험입니다. OK 두 글자만 답하세요.") for a in TOOLS[name]["test_args"]]
    try:
        code, out = _run(_cmd(t["path"], args), root, 180)
    except subprocess.TimeoutExpired:
        code, out = -1, "3분 안에 응답이 없습니다. 터미널에서 직접 실행해 로그인 상태를 확인하세요."
    except OSError as e:
        code, out = -1, str(e)
    ok = code == 0 and "OK" in out.upper()
    t.update(tested=time.strftime("%Y-%m-%dT%H:%M:%S"), test_ok=ok)
    save_config(root, cfg)
    return {"ok": ok, "code": code, "output": out[-2000:]}


# ------------------------------------------------------------------ 실행
def _jobs_dir(root: str) -> str:
    d = os.path.join(root, JOBS)
    os.makedirs(d, exist_ok=True)
    return d


def _save_job(root: str, job: dict) -> None:
    with open(os.path.join(_jobs_dir(root), job["id"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)


def instruction_for(prompt_rel: str) -> str:
    # cmd /c 를 거쳐도 깨지지 않도록 따옴표·&·|·<·>·%·^ 를 쓰지 않는다
    return (f"Assets Manager 요청입니다. 파일 {prompt_rel} 를 읽고 그 지시를 그대로 수행하세요. "
            "결과 파일은 지시에 적힌 위치에만 쓰고, 다른 파일은 고치거나 지우지 마세요. 사용자에게 질문하지 말고 끝까지 진행하세요. "
            "라이브러리를 다루는 규칙과 형식은 assets-manager 스킬에 있습니다.")


def start(root: str, kind: str, prompt_path: str, ref: dict, tool: str | None = None, expect: list[str] | None = None) -> dict:
    """백그라운드로 도구를 실행한다. expect: 끝난 뒤 있어야 할 결과 파일(상대 경로)."""
    cfg = load_config(root)
    name = tool or cfg.get("default")
    if not name or name not in TOOLS:
        raise LookupError("연결된 로컬 AI 도구가 없습니다")
    t = cfg["tools"][name]
    if not t.get("found") or not t.get("path"):
        raise LookupError(f"{TOOLS[name]['label']}을(를) 찾지 못했습니다")
    if not t.get("enabled", True):
        raise LookupError(f"{TOOLS[name]['label']}이(가) 꺼져 있습니다")
    rel = os.path.relpath(prompt_path, root).replace("\\", "/")
    instr = instruction_for(rel)
    args = [a.replace("{instruction}", instr).replace("{root}", root) for a in (t.get("args") or TOOLS[name]["args"])]
    jid = time.strftime("job-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    job = {"id": jid, "tool": name, "tool_label": TOOLS[name]["label"], "kind": kind, "ref": ref, "prompt": rel,
           "instruction": instr, "state": "running", "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "t0": time.time(),
           "expect": expect or [], "code": None, "result_found": None}
    log_path = os.path.join(_jobs_dir(root), jid + ".log")
    _save_job(root, job)
    with _lock:
        _jobs[jid] = job

    def work():
        kw = {"creationflags": 0x08000000} if WIN else {}
        try:
            with open(log_path, "wb") as log:
                p = subprocess.Popen(_cmd(t["path"], args), cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=_env(), **kw)
                with _lock:
                    _procs[jid] = p
                try:
                    code = p.wait(timeout=TIMEOUT)
                except subprocess.TimeoutExpired:
                    _kill(p)
                    code = -9
                    job["error"] = f"{TIMEOUT // 60}분 안에 끝나지 않아 멈췄습니다"
        except OSError as e:
            code = -1
            job["error"] = str(e)
        with _lock:
            _procs.pop(jid, None)
        found = all(os.path.exists(os.path.join(root, e)) for e in job["expect"]) if job["expect"] else None
        if job["state"] != "cancelled":
            job["state"] = "done" if code == 0 and found is not False else "failed"
            if code == 0 and found is False:
                job["error"] = "도구는 끝났지만 결과 파일이 없습니다: " + ", ".join(e for e in job["expect"]
                                                                       if not os.path.exists(os.path.join(root, e)))
        job.update(code=code, result_found=found, ended=time.strftime("%Y-%m-%dT%H:%M:%S"),
                   seconds=round(time.time() - job["t0"]))
        _save_job(root, job)

    threading.Thread(target=work, daemon=True).start()
    return job


def _kill(p: subprocess.Popen) -> None:
    try:
        if WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, creationflags=0x08000000)
        else:
            p.kill()
    except OSError:
        pass


def cancel(root: str, jid: str) -> dict:
    job = get_job(root, jid)
    with _lock:
        p = _procs.get(jid)
    if p and job["state"] == "running":
        job["state"] = "cancelled"
        if jid in _jobs:
            _jobs[jid]["state"] = "cancelled"
        _kill(p)
    return get_job(root, jid)


def get_job(root: str, jid: str, tail: int = 4000) -> dict:
    with _lock:
        job = dict(_jobs.get(jid) or {})
    if not job:
        try:
            with open(os.path.join(_jobs_dir(root), jid + ".json"), encoding="utf-8") as f:
                job = json.load(f)
        except OSError:
            raise KeyError(f"작업 없음: {jid}")
        if job.get("state") == "running":  # 서버가 다시 시작됨
            job["state"] = "failed"
            job["error"] = "앱이 다시 시작되어 작업 상태를 알 수 없습니다"
    try:
        with open(os.path.join(_jobs_dir(root), jid + ".log"), "rb") as f:
            data = f.read()
        job["log"] = _decode(data[-tail:])
    except OSError:
        job["log"] = ""
    if job.get("state") == "running":
        job["elapsed"] = round(time.time() - job.get("t0", time.time()))
    return job


def list_jobs(root: str) -> list[dict]:
    d = os.path.join(root, JOBS)
    out = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d))[-20:]:
            if fn.endswith(".json"):
                try:
                    out.append(get_job(root, fn[:-5], tail=0))
                except KeyError:
                    pass
    return out


# ------------------------------------------------------------------ 기능별 준비 (어떤 요청을 어떤 프롬프트로 돌릴지)
def agent_target(lib) -> str:
    """에이전트용 프롬프트(경로 포함, out/에 쓰기)를 만들 때 쓸 대상 키."""
    for k, v in lib.targets.items():
        if v.get("kind") == "agent":
            return k
    raise LookupError("targets.yaml에 kind: agent 대상이 없습니다")


def run_for(lib, body: dict) -> dict:
    """body: {kind: tidy|request|authoring, request?, files?, note?, tool?} → 작업 시작."""
    import folder_requests as freq
    kind = body.get("kind")
    tgt = agent_target(lib)
    note = body.get("note") or ""
    if kind == "tidy":
        import tidy
        rid = body["request"]
        req = freq._load(lib.root, rid)
        ids = [i for i in (req.get("scope") or tidy.scope(lib, req["node"])) if i in lib.nodes]
        prompt = tidy.render(lib, req["node"], rid, ids, tidy.diagnose(lib, ids), tgt, req.get("goal"))
        path = os.path.join(freq._rdir(lib.root, rid), "prompt-local.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(prompt)
        expect = [f"{freq.INBOX}/{rid}/out/plan.yaml"]
        ref = {"request": rid, "node": req.get("node")}
    elif kind == "request":
        rid = body["request"]
        pkg = freq.package(lib, rid, tgt, note)
        path = pkg["files"][0]
        expect = [f"{freq.INBOX}/{rid}/out/plan.yaml"]
        ref = {"request": rid, "node": freq._load(lib.root, rid).get("node")}
    elif kind == "authoring":
        import authoring
        files = body.get("files") or []
        if not files:
            raise ValueError("등록할 파일이 없습니다")
        pkg = authoring.build(lib, files, tgt, note)
        path = pkg["files"][0]
        expect = None
        ref = {"files": files}
    else:
        raise ValueError(f"알 수 없는 작업: {kind}")
    return start(lib.root, kind, path, ref, body.get("tool"), expect)
