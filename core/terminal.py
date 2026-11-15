"""앱 안 터미널 세션 (기획서 v3.8, Q80-A).

AI 탭에서 사용자가 연결한 Claude Code·Codex를 **진짜 대화형 터미널**(pty)로 띄운다.
- Windows: pywinpty(ConPTY) `winpty.PtyProcess`.  POSIX: `os.openpty` + `subprocess`.
- 출력은 세션별 고리 버퍼(문자 오프셋)에 쌓고, UI가 `read(since)`로 가져간다(긴 폴링, 창이 보일 때만).
- 리소스: 패널을 열고 탭을 만들 때만 프로세스가 뜬다. 탭을 닫으면 종료. AI 탭은 기본 1개(MAX_AI).
  서버가 끝나면 모두 종료한다.
- 세션 환경: 작업 폴더 = 라이브러리 루트, PATH 앞에 `.assets/bin`(`am` 명령), `AM_ACTOR=ai-tab`.
  Claude Code는 `am` 명령으로 노드를 바꾼다. Codex는 읽기 전용이어서 변경 계획을 출력한다(Q85).

P3: 앱이 AI를 스스로 부르지 않는다. 사용자가 "+ AI" 탭을 눌러야 뜨고, 이후 입력도 사용자가 한다.
"""
from __future__ import annotations

import codecs
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

MAX_BUF = 400_000          # 세션당 보관하는 출력 문자 수 (UI 스크롤백은 5000줄)
MAX_AI = 1                 # 동시에 띄울 AI 탭 수 (기본 1)
IDLE_HINT = 30 * 60        # 이 시간(초) 입력이 없으면 UI가 "닫을까요?"를 묻는다

SESSIONS: dict[str, "Session"] = {}
LOCK = threading.Lock()


def available() -> dict:
    """pty를 쓸 수 있는지. Windows는 pywinpty가 필요하다."""
    if os.name == "nt":
        try:
            import winpty  # noqa: F401
            return {"ok": True}
        except ImportError:
            return {"ok": False, "hint": "AI 탭에는 pywinpty가 필요합니다. start.bat을 다시 실행하거나 `py -m pip install pywinpty`."}
    return {"ok": True}


def bin_dir(root: str) -> str:
    return os.path.join(root, ".assets", "bin")


def write_shims(root: str, py: str | None = None) -> str:
    """`.assets/bin/am(.cmd)`를 만든다. AI 탭과 사용자 터미널에서 `am ...`으로 부른다."""
    d = bin_dir(root)
    os.makedirs(d, exist_ok=True)
    py = py or sys.executable
    cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli.py")
    files = {
        "am.cmd": f'@echo off\r\nset PYTHONIOENCODING=utf-8\r\n"{py}" "{cli}" --root "{root}" %*\r\n',
        "am": f'#!/bin/sh\nPYTHONIOENCODING=utf-8 exec "{py}" "{cli}" --root "{root}" "$@"\n',
    }
    for name, text in files.items():
        p = os.path.join(d, name)
        cur = None
        if os.path.isfile(p):
            with open(p, encoding="utf-8", newline="") as f:
                cur = f.read()
        if cur != text:
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        if name == "am":
            try:
                os.chmod(p, 0o755)
            except OSError:
                pass
    return d


def session_env(root: str, actor: str = "ai-tab") -> dict:
    env = dict(os.environ)
    env["PATH"] = bin_dir(root) + os.pathsep + env.get("PATH", "")
    env["AM_ACTOR"] = actor
    env["ASSETS_MANAGER_ROOT"] = root
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("TERM", "xterm-256color")
    env["COLORTERM"] = "truecolor"
    return env


class Session:
    def __init__(self, sid: str, tool: str, label: str, cmd: list[str], cwd: str, env: dict, cols: int, rows: int):
        self.id, self.tool, self.label = sid, tool, label
        self.cmd = cmd
        self.buf = ""
        self.base = 0              # buf[0]의 전체 오프셋
        self.cond = threading.Condition()
        self.alive = True
        self.exit_code = None
        self.created = time.time()
        self.last_input = self.created
        self._win = None
        self._proc = None
        self._fd = None
        if os.name == "nt":
            from winpty import PtyProcess  # type: ignore
            self._win = PtyProcess.spawn(cmd, cwd=cwd, env=env, dimensions=(rows, cols))
        else:
            import pty  # noqa: F401  (POSIX 확인용)
            master, slave = os.openpty()
            self._set_size(slave, cols, rows)
            self._proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
                                          start_new_session=True, close_fds=True)
            os.close(slave)
            self._fd = master
        threading.Thread(target=self._pump, daemon=True).start()

    # ---------------------------------------------------------------- 내부
    @staticmethod
    def _set_size(fd: int, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def _append(self, text: str) -> None:
        if not text:
            return
        with self.cond:
            self.buf += text
            if len(self.buf) > MAX_BUF:
                cut = len(self.buf) - MAX_BUF
                self.buf = self.buf[cut:]
                self.base += cut
            self.cond.notify_all()

    def _pump(self) -> None:
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                if self._win is not None:
                    try:
                        data = self._win.read(4096)
                    except EOFError:
                        break
                    if not data and not self._win.isalive():
                        break
                    self._append(data)
                else:
                    try:
                        data = os.read(self._fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    self._append(dec.decode(data))
        finally:
            self._append(dec.decode(b"", final=True))
            self._finish()

    def _finish(self) -> None:
        code = None
        if self._win is not None:
            try:
                code = self._win.exitstatus
            except Exception:
                code = None
        elif self._proc is not None:
            try:
                code = self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                code = None
        with self.cond:
            self.alive = False
            self.exit_code = code
            self.cond.notify_all()

    # ---------------------------------------------------------------- 공개
    def read(self, since: int, wait: float = 0.0) -> dict:
        end = time.time() + wait
        with self.cond:
            while wait and self.alive and self.base + len(self.buf) <= since:
                left = end - time.time()
                if left <= 0:
                    break
                self.cond.wait(left)
            start = max(since, self.base)
            data = self.buf[start - self.base:]
            return {"data": data, "next": self.base + len(self.buf), "lost": since < self.base,
                    "alive": self.alive, "exit_code": self.exit_code,
                    "idle": int(time.time() - self.last_input)}

    def write(self, data: str) -> None:
        if not self.alive:
            return
        self.last_input = time.time()
        if self._win is not None:
            self._win.write(data)
        else:
            os.write(self._fd, data.encode("utf-8"))

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(10, int(cols)), max(3, int(rows))
        try:
            if self._win is not None:
                self._win.setwinsize(rows, cols)
            elif self._fd is not None:
                self._set_size(self._fd, cols, rows)
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._win is not None:
                if self._win.isalive():
                    self._win.terminate(force=True)
            elif self._proc is not None and self._proc.poll() is None:
                import signal
                try:
                    os.killpg(self._proc.pid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(self._proc.pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        self._proc.kill()
        finally:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None

    def info(self) -> dict:
        return {"id": self.id, "tool": self.tool, "label": self.label, "alive": self.alive,
                "exit_code": self.exit_code, "idle": int(time.time() - self.last_input)}


def _resolve(path: str) -> list[str]:
    """Windows에서 .cmd/.bat(npm 설치)은 cmd /c로 감싼다."""
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", path]
    return [path]


def start(root: str, tool: str, cols: int = 100, rows: int = 30, cmd: list[str] | None = None) -> dict:
    """AI 탭을 띄운다. tool = claude | codex (로컬 AI 설정에서 찾은 경로). cmd는 테스트용."""
    av = available()
    if not av["ok"]:
        return {"error": av["hint"], "need": "pywinpty"}
    label = tool
    if cmd is None:
        import local_ai
        st = {t["name"]: t for t in local_ai.status(root)["tools"]}
        t = st.get(tool)
        if not t:
            return {"error": f"모르는 도구: {tool}"}
        if not t.get("path") or not t.get("found"):
            return {"error": f"{t['label']}을(를) 찾지 못했습니다. 설정 > 로컬 AI 도구에서 연결하세요.", "need": "connect"}
        label = t["label"]
        cmd = _resolve(t["path"])
        if tool == "codex":
            cmd += ["--sandbox", "read-only"]  # Q85: AI 탭에서도 am 쓰기는 차단된다.
        if not shutil.which(cmd[0]) and not os.path.isfile(cmd[0]):
            return {"error": f"실행 파일이 없습니다: {cmd[0]}", "need": "connect"}
    with LOCK:
        live = [s for s in SESSIONS.values() if s.alive]
        if len(live) >= MAX_AI:
            return {"error": f"AI 탭은 {MAX_AI}개까지 띄웁니다. 쓰지 않는 탭을 닫으세요.", "busy": [s.info() for s in live]}
        write_shims(root)
        sid = uuid.uuid4().hex[:10]
        s = Session(sid, tool, label, cmd, root, session_env(root), int(cols), int(rows))
        SESSIONS[sid] = s
    return {"ok": True, "session": s.info()}


def get(sid: str) -> Session:
    s = SESSIONS.get(sid)
    if not s:
        raise KeyError(f"없는 세션: {sid}")
    return s


def close(sid: str) -> dict:
    with LOCK:
        s = SESSIONS.pop(sid, None)
    if s:
        s.close()
    return {"ok": True}


def list_sessions() -> list[dict]:
    return [s.info() for s in SESSIONS.values()]


def close_all() -> None:
    for sid in list(SESSIONS):
        close(sid)


# ---------------------------------------------------------------- 권한 (라이브러리 .claude/settings.json)
PERM_ALLOW = ["Bash(am)", "Bash(am *)", "Bash(python app/core/cli.py *)", "Bash(py app/core/cli.py *)"]
PERM_DENY = ["Edit(./sets/**)", "Edit(./principles/**)", "Edit(./skills/**)",
             "Edit(./history/**)", "Edit(./index.md)", "Edit(./graph.json)"]


def ensure_permissions(root: str) -> dict:
    """라이브러리에서 뜨는 Claude Code가 규칙 파일을 직접 고치지 못하게 하고(`am`으로만), `am`은 묻지 않게 한다.
    사용자가 넣은 항목은 그대로 두고 빠진 것만 더한다. JSON이 깨져 있으면 손대지 않는다."""
    import json
    p = os.path.join(root, ".claude", "settings.json")
    cur: dict = {}
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                cur = json.load(f)
        except (OSError, ValueError):
            return {"ok": False, "path": p, "error": "settings.json을 읽지 못해 그대로 두었습니다."}
        if not isinstance(cur, dict):
            return {"ok": False, "path": p, "error": "settings.json 형식이 객체가 아닙니다."}
    perms = cur.setdefault("permissions", {})
    added = []
    for key, want in (("allow", PERM_ALLOW), ("deny", PERM_DENY)):
        lst = perms.setdefault(key, [])
        for r in want:
            if r not in lst:
                lst.append(r)
                added.append(r)
    if added:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return {"ok": True, "path": p, "added": added}
