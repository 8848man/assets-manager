"""로컬 임베딩 (기획서 Q83-A, 3단계). 키워드 검색(search.py)에 '뜻 점수'를 더한다.

- 모델: multilingual-e5-small 8비트 양자화 ONNX (Xenova 변환본, 리비전·sha256 고정). onnxruntime + tokenizers로
  이 PC의 CPU에서만 돈다. 외부 API를 부르지 않는다(P3). 앱이 인터넷에 닿는 것은 사용자가 설정에서
  '뜻 검색 켜기'를 누를 때 한 번(패키지 설치 + 모델 내려받기)뿐이다.
- 결정성(P2): 스레드 1개로 계산하고 벡터·점수를 반올림한다. 같은 모델·같은 폴더 상태 → 같은 결과.
  모델이 없거나 꺼져 있으면 search는 지금의 키워드 검색과 글자 하나까지 같다.
- 캐시(P1): 조각 글의 sha1 → 벡터를 `.assets/embed/vectors.json`에 둔다. 지워도 다시 계산된다.
  조각은 RAG와 같은 `노드id#부분`(rag.node_parts). 모델 파일은 라이브러리 밖 사용자 캐시 폴더에 둔다(여러 라이브러리가 같이 씀).
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

REPO = "Xenova/multilingual-e5-small"
REV = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
MODEL_KEY = "multilingual-e5-small-q8@" + REV[:8]
FILES = {  # 상대 경로: (sha256, 바이트)
    "onnx/model_quantized.onnx": ("f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193", 118308185),
    "tokenizer.json": ("0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39", 17082730),
}
PACKAGES = ["onnxruntime>=1.17", "tokenizers>=0.15"]
DEPS = ("onnxruntime", "tokenizers", "numpy")
CONFIG = os.path.join(".assets", "embed", "config.json")
VECTORS = os.path.join(".assets", "embed", "vectors.json")
MAX_TOKENS = 512
BATCH = 16
VEC_DIGITS = 6
SCORE_DIGITS = 4

_LOCK = threading.Lock()
_ENCODER = None          # 실제 모델 (지연 로드)
_FAKE = None             # 테스트용: fn(texts) -> vectors
_CACHE: dict = {}        # root -> {"mtime": .., "vecs": {..}}
_JOBS: dict = {}         # root -> 설치 작업 상태


# ------------------------------------------------------------------ 상태
def model_dir() -> str:
    base = os.environ.get("AM_MODEL_DIR")
    if not base:
        local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
        base = os.path.join(local, "assets-manager", "models")
    return os.path.join(base, MODEL_KEY.replace("@", "-"))


def deps_ok() -> bool:
    importlib.invalidate_caches()
    return all(importlib.util.find_spec(m) is not None for m in DEPS)


def model_ok() -> bool:
    d = model_dir()
    if not os.path.exists(os.path.join(d, "verified")):
        return False
    return all(os.path.isfile(os.path.join(d, rel)) and os.path.getsize(os.path.join(d, rel)) == size
               for rel, (_, size) in FILES.items())


def _read_config(root: str) -> dict:
    try:
        with open(os.path.join(root, CONFIG), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def set_enabled(root: str, enabled: bool) -> dict:
    p = os.path.join(root, CONFIG)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    cfg = _read_config(root)
    cfg["enabled"] = bool(enabled)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return status(root)


def enabled(root: str) -> bool:
    return bool(_read_config(root).get("enabled"))


def active(root: str) -> bool:
    """검색에 뜻 점수를 섞는가: 켜져 있고 모델이 준비됨."""
    if not enabled(root):
        return False
    return _FAKE is not None or (deps_ok() and model_ok())


def status(root: str) -> dict:
    vecs = _load(root)["vecs"]
    job = _JOBS.get(os.path.abspath(root))
    return {"enabled": enabled(root), "deps": deps_ok(), "model": model_ok(), "active": active(root),
            "model_name": MODEL_KEY, "model_dir": model_dir(),
            "download_mb": round(sum(s for _, s in FILES.values()) / 1e6), "vectors": len(vecs),
            "job": dict(job) if job else None}


# ------------------------------------------------------------------ 인코더
class _Encoder:
    def __init__(self, d: str):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np = np
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1  # 결정성: 병렬 합산 순서가 바뀌지 않게
        so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(os.path.join(d, "onnx", "model_quantized.onnx"), so,
                                         providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        self.tok = Tokenizer.from_file(os.path.join(d, "tokenizer.json"))
        self.tok.enable_truncation(MAX_TOKENS)
        self.tok.enable_padding()

    def __call__(self, texts: list[str]) -> list[list[float]]:
        np = self.np
        out = []
        for i in range(0, len(texts), BATCH):
            enc = self.tok.encode_batch(texts[i:i + BATCH])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            h = self.sess.run(None, feed)[0]
            m = mask[..., None].astype(np.float32)
            v = (h * m).sum(1) / np.maximum(m.sum(1), 1e-9)
            v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
            out += [[round(float(x), VEC_DIGITS) for x in row] for row in v]
        return out


def set_encoder(fn) -> None:
    """테스트용: 가짜 인코더를 꽂는다(None이면 해제)."""
    global _FAKE
    _FAKE = fn


def encode(texts: list[str]) -> list[list[float]]:
    global _ENCODER
    if _FAKE is not None:
        return [[round(float(x), VEC_DIGITS) for x in v] for v in _FAKE(texts)]
    with _LOCK:
        if _ENCODER is None:
            _ENCODER = _Encoder(model_dir())
        return _ENCODER(texts)


# ------------------------------------------------------------------ 벡터 캐시
def _key(text: str) -> str:
    return hashlib.sha1((MODEL_KEY + "\n" + text).encode("utf-8")).hexdigest()[:16]


def _load(root: str) -> dict:
    p = os.path.join(root, VECTORS)
    try:
        mt = os.stat(p).st_mtime_ns
    except OSError:
        return {"mtime": None, "vecs": {}}
    c = _CACHE.get(root)
    if c and c["mtime"] == mt:
        return c
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        vecs = data.get("vecs", {}) if data.get("model") == MODEL_KEY else {}
    except (OSError, ValueError):
        vecs = {}
    c = {"mtime": mt, "vecs": vecs}
    _CACHE[root] = c
    return c


def _save(root: str, vecs: dict) -> None:
    p = os.path.join(root, VECTORS)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"model": MODEL_KEY, "vecs": {k: vecs[k] for k in sorted(vecs)}}, f, separators=(",", ":"))
    os.replace(tmp, p)
    _CACHE[root] = {"mtime": os.stat(p).st_mtime_ns, "vecs": vecs}


def _vectors(lib, nodes: list[dict], prune: bool = False) -> dict[str, list[tuple[str, str]]]:
    """노드마다 [(조각 키, 글)]을 만들고, 캐시에 없는 조각만 계산해 저장한다."""
    import rag
    per = {}
    for n in nodes:
        per[n["id"]] = [(_key("passage: " + t), "passage: " + t) for _, t in rag.node_parts(lib, n)]
    root = lib.root
    vecs = dict(_load(root)["vecs"])
    need = sorted({k: t for items in per.values() for k, t in items if k not in vecs}.items())
    changed = False
    if need:
        for (k, _), v in zip(need, encode([t for _, t in need])):
            vecs[k] = v
        changed = True
    if prune:
        used = {k for items in per.values() for k, _ in items}
        if set(vecs) - used:
            vecs = {k: v for k, v in vecs.items() if k in used}
            changed = True
    if changed:
        _save(root, vecs)
    return per


def scores(lib, nodes: list[dict], query: str) -> dict[str, float]:
    """노드 id → 질의와의 코사인(조각 중 최댓값, 반올림)."""
    if not nodes or not query.strip():
        return {}
    per = _vectors(lib, nodes)
    vecs = _load(lib.root)["vecs"]
    q = encode(["query: " + query.strip()])[0]
    out = {}
    for nid in sorted(per):
        best = 0.0
        for k, _ in per[nid]:
            v = vecs.get(k)
            if v:
                best = max(best, sum(a * b for a, b in zip(q, v)))
        out[nid] = round(best, SCORE_DIGITS)
    return out


def index(lib) -> int:
    """라이브러리 전체 조각을 계산해 두고 안 쓰는 벡터는 지운다. 서로 다른 조각 수를 돌려준다."""
    nodes = [n for n in sorted(lib.nodes.values(), key=lambda x: x["id"]) if n["kind"] != "unregistered"
             and n["type"] != "ai" and n["type"] != "installed_skill"]
    per = _vectors(lib, nodes, prune=True)
    return len({k for items in per.values() for k, _ in items})  # 같은 글은 한 번만


# ------------------------------------------------------------------ 켜기 (사용자 버튼)
def _download(rel: str, sha: str, size: int, d: str, job: dict) -> None:
    dst = os.path.join(d, rel)
    if os.path.isfile(dst) and os.path.getsize(dst) == size:
        h = hashlib.sha256()
        with open(dst, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        if h.hexdigest() == sha:  # 이미 있는 파일도 검증한 뒤에만 쓴다
            job["done_bytes"] += size
            return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    url = f"https://huggingface.co/{REPO}/resolve/{REV}/{rel}"
    part = dst + ".part"
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "assets-manager"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            h.update(b)
            job["done_bytes"] += len(b)
    if h.hexdigest() != sha:
        os.remove(part)
        raise RuntimeError(f"{rel}: 내려받은 파일의 sha256이 맞지 않습니다")
    os.replace(part, dst)


def install(root: str, get_lib) -> dict:
    """설정의 '뜻 검색 켜기' 버튼: (필요하면) 패키지 설치 → 모델 내려받기·검증 → 켜기 → 색인. 백그라운드."""
    key = os.path.abspath(root)
    cur = _JOBS.get(key)
    if cur and cur["state"] == "running":
        return status(root)
    job = {"state": "running", "step": "준비", "error": None, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "total_bytes": sum(s for _, s in FILES.values()), "done_bytes": 0, "chunks": None}
    _JOBS[key] = job

    def run():
        try:
            if not deps_ok():
                job["step"] = "패키지 설치 (onnxruntime, tokenizers)"
                r = subprocess.run([sys.executable, "-m", "pip", "install", *PACKAGES], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=900)
                if r.returncode != 0 or not deps_ok():
                    raise RuntimeError("패키지 설치 실패: " + (r.stderr or r.stdout)[-800:])
            d = model_dir()
            job["step"] = "모델 내려받기"
            for rel, (sha, size) in FILES.items():
                _download(rel, sha, size, d, job)
            with open(os.path.join(d, "verified"), "w", encoding="utf-8") as f:
                f.write(MODEL_KEY + "\n")
            job["done_bytes"] = job["total_bytes"]
            set_enabled(root, True)
            job["step"] = "라이브러리 색인"
            job["chunks"] = index(get_lib())
            job["state"], job["step"] = "done", "완료"
        except Exception as e:  # noqa: BLE001 — 사용자에게 사유를 보여 준다
            job["state"], job["error"] = "failed", str(e)
    threading.Thread(target=run, daemon=True).start()
    return status(root)

