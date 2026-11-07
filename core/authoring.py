"""AI 작성 패키지 (기획서 v3 6장): 미등록 파일을 작업하던 AI가 규격대로 노드로 만들게 하는 프롬프트.

- 에이전트(kind: agent): 내 PC에서 extract.py를 직접 실행하고, 원본 옆에 .meta.yaml을 쓴다.
- 채팅형(kind: chat): 패키지에 extract.py와 원본을 첨부한다. AI가 코드 실행 환경에서 돌리고,
  결과를 assets-import.zip 하나로 내려받게 준다. 사용자는 그 zip을 앱 창에 끌어다 놓는다.
프롬프트는 그 순간의 폴더 상태(키 목록, 기존 id)로 매번 새로 만든다.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil

from engine import APP_DIR, CACHE_DIR, Library
import extract as extractor

EXTRACT_PY = os.path.join(APP_DIR, "core", "extract.py")


def build(lib: Library, rels: list[str], target: str, note: str = "", write: bool = True) -> dict:
    tgt = lib.targets.get(target)
    if not tgt:
        raise KeyError(f"알 수 없는 대상: {target}")
    agent = tgt.get("kind", "agent" if tgt.get("include_paths") else "chat") == "agent"
    rels = [r for r in rels if os.path.isfile(lib.abspath(r))]
    if not rels:
        raise ValueError("등록할 파일이 없습니다")
    values = {r: extractor.extract(lib.abspath(r)) for r in rels}
    prompt = render(lib, rels, target, agent, values, note)

    out_dir = os.path.join(lib.root, CACHE_DIR, "authoring", target)
    prompt_path = os.path.join(out_dir, "prompt.md")
    files = [prompt_path]
    if write:
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir)
        os.makedirs(out_dir, exist_ok=True)
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)
    if not agent:
        ex = os.path.join(out_dir, "extract.py")
        if write:
            shutil.copy2(EXTRACT_PY, ex)
        files.append(ex)
        files += [lib.abspath(r) for r in rels]
    clipboard = prompt if agent else prompt + "\n## 파일 경로\n" + "\n".join(f"- {lib.abspath(r)}" for r in rels) + "\n"
    return {
        "target": target, "target_label": tgt.get("label", target), "agent": agent, "files": files,
        "prompt": prompt, "clipboard": clipboard, "rels": rels, "values": values,
        "prompt_hash": hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12],
        "drag_icon": lib.drag_icon() if write else None,
    }


def render(lib: Library, rels: list[str], target: str, agent: bool, values: dict, note: str = "") -> str:
    py = os.path.join(lib.root, "app", "core", "extract.py")
    L = [
        "# Assets Manager 노드 작성 요청", "",
        "당신은 아래 레퍼런스 파일을 Assets Manager 라이브러리에 등록하는 일을 맡았습니다.",
        "파일마다 규격에 맞는 `.meta.yaml`을 하나씩 만드세요. 규격을 벗어난 필드나 형식은 쓰지 마세요.", "",
    ]
    if note.strip():
        L += ["## 사용자 요청", note.strip(), ""]
    L += ["## 등록할 파일"]
    for r in rels:
        L.append(f"- `{os.path.basename(r)}` → 폴더 `{os.path.dirname(r)}/` " + (f"(경로: {lib.abspath(r)})" if agent else "(첨부됨)"))
    existing = {n["file"]: n for n in lib.nodes.values() if n.get("path") and n.get("file") in rels}
    if existing:
        L += ["", "## 이미 등록된 파일 (다시 쓰기)",
              "아래 파일은 이미 노드가 있습니다. **id·file·kind·set은 그대로 두고** summary, spec, check, tags, keywords를 보강해서 "
              "같은 경로의 .meta.yaml을 덮어쓰세요."]
        for r, n in existing.items():
            L.append(f"- `{r}` → 기존 메타 `{n['path']}` · id `{n['id']}`" + (f" · 산출물(set: {n.get('set')})" if n["kind"] == "output" else ""))
    L += ["", "## 1단계: 결정적인 값 추출"]
    if agent:
        L += ["아래 명령을 실행하고, 출력된 JSON을 `extracted:`에 그대로 넣으세요. 색상 hex 같은 값은 절대 눈으로 추측하지 마세요.", "",
              "```", f'python "{py}" ' + " ".join(f'"{lib.abspath(r)}"' for r in rels), "```", ""]
    else:
        L += ["첨부된 `extract.py`를 코드 실행 환경에서 실행하세요. 출력된 JSON을 `extracted:`에 그대로 넣으세요.", "",
              "```", "python extract.py " + " ".join(os.path.basename(r) for r in rels), "```", "",
              "코드를 실행할 수 없으면 앱이 미리 뽑은 아래 값을 그대로 쓰세요.", "",
              "```json", json.dumps(values, ensure_ascii=False, indent=1), "```", ""]
    L += ["## 2단계: 파일을 보고 해석",
          "- `summary`: 한 줄 요약 (40자 이내)",
          "- `spec`: 이 레퍼런스를 **첨부 없이 글로만** 전달할 때 쓰는 설명입니다. 3~6줄. **보이는 것만** 쓰고 추측하지 마세요.",
          "  색은 `extracted.palette`의 hex를 그대로 인용하세요.",
          "- `check`: 이 레퍼런스를 따른 결과물인지 확인하는 점검 문장 2~4개. \"~인가\"로 끝나는 예/아니오 질문으로 쓰세요.",
          "- `tags`: 영문 소문자 3~6개",
          "- `keywords`: 나중에 검색될 수 있도록 비슷한 말과 관련어 5~10개 (한국어 위주, 예: 소개글, 안내, 챗봇)", ""]
    L += ["## 3단계: 규격", "```yaml",
          "id: 영문-소문자-하이픈          # 아래 '기존 id'와 겹치면 안 됨. 파일명을 바탕으로",
          "type: image | web | document | writing   # 폴더에 맞게",
          "title: 한국어 제목",
          "file: 원본 파일명 (확장자 포함, 폴더 없이)",
          "summary: ...",
          "spec: |",
          "  ...",
          "check:",
          "  - ...인가",
          "tags: [..]",
          "keywords: [..]",
          "sensitivity: public           # 회사 내부 자료로 보이면 internal",
          f"authored_by: {target}",
          "extracted:                     # 1단계 JSON 그대로",
          "  ...",
          "```", ""]
    L += ["## 출력 방식"]
    if agent:
        L += [f"- 각 원본과 **같은 폴더**에 `<원본 파일명에서 확장자를 뺀 이름>.meta.yaml`로 저장하세요. 예: `image/foo.png` → `image/foo.meta.yaml`",
              "- 원본 파일과 다른 파일은 수정하지 마세요. 저장하면 Assets Manager가 자동으로 읽습니다(승인 단계 없음, 변경은 기록되어 되돌릴 수 있음).",
              "- 끝나면 만든 파일 목록만 짧게 알려 주세요."]
    else:
        L += ["- 결과를 **`assets-import.zip` 하나**로 묶어 내려받을 수 있게 주세요. 사용자는 그 zip을 Assets Manager 창에 끌어다 놓습니다.",
              "- zip 안 구조: `<폴더>/<원본 파일명에서 확장자를 뺀 이름>.meta.yaml` (예: `image/foo.meta.yaml`). 원본 파일은 넣지 마세요.",
              "- zip을 만들 수 없으면 파일마다 yaml 코드 블록을 하나씩 출력하세요. 블록 위에 `파일: image/foo.meta.yaml`처럼 경로를 한 줄 쓰세요."]
    L += ["", "## 참고: 기존 노드 id (중복 금지, 세트를 만들 때 ref:<id>로 연결)"]
    for n in sorted(lib.nodes.values(), key=lambda x: (x["type"], x["id"])):
        if n["kind"] == "unregistered":
            continue
        L.append(f"- `{n['id']}` · {n['type']} · {n['title']}")
    L += ["", "## 참고: 키 어휘 (사용자가 세트도 요청한 경우에만 사용, 새 키는 꼭 필요할 때만)"]
    for k, spec in lib.keys.items():
        L.append(f"- `{k}` · {spec.get('label', k)} · {spec.get('priority', 'optional')}")
    return "\n".join(L) + "\n"
