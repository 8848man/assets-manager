"""내보내기 명령 (기획서 v3.3, Q63~Q64).

앱에서 복사해 작업하던 AI 대화창에 붙여넣으면, AI가 그 대화의 맥락(만든 결과물, 쓴 레퍼런스, 논의한 스타일)을
Assets Manager 규격 zip(assets-import.zip)으로 묶어 준다. 사용자는 그 zip을 앱 창에 놓는다.

- 노드별 명령: 복사하는 순간 가벼운 요청을 만든다. 돌아온 zip은 어디에 놓든 그 노드로 연결된다. 그 노드의 규칙이 들어간다.
- 상단(일반) 명령: 요청 없음. zip을 놓은 노드가 대상이 되며, plan에서는 `@target`으로 쓴다.
"""
from __future__ import annotations

import json

from engine import Library
import folder_requests as freq


def build(lib: Library, node_id: str | None = None) -> dict:
    node = lib.nodes.get(node_id) if node_id else None
    rid = None
    if node:
        rid = freq.new_request(lib, node_id, kind="export", status="sent")["id"]
    target_ref = f"`{node['id']}`" if node else "`@target`"
    L = ["# Assets Manager로 내보내기", "",
         "지금까지 이 대화에서 만든 결과물과 쓴 레퍼런스를 **Assets Manager 규격의 zip 파일 하나**로 묶어 주세요.",
         "코드 실행 환경(파일 생성 기능)을 사용해 실제로 내려받을 수 있는 zip을 만들어 주세요.", ""]
    if node:
        L += [f"요청 id: `{rid}`", "",
              f"## 보낼 곳: {node['title']} (`{node['id']}`, {node['kind']})"]
        if node["type"] == "rule" and node["id"] not in lib.cyclic:
            for e in lib.assemble(node["id"])["entries"]:
                if not e["from_defaults"]:
                    v = e["value"] if isinstance(e["value"], str) else json.dumps(e["value"], ensure_ascii=False)
                    L.append(f"- 현재 규칙 `{e['key']}`: {v}")
        L.append("")
    else:
        L += ["## 보낼 곳: 사용자가 zip을 놓는 노드",
              "어느 노드에 넣을지는 사용자가 zip을 놓을 때 정합니다. plan.yaml에서 그 노드를 가리킬 때는 `@target`이라고 쓰세요.", ""]
    L += ["## 1. 무엇을 담을지 고르기",
          "- **산출물(output):** 이 대화에서 사용자가 확인한 최종 결과물 (완성된 글, 이미지, html 등). 메타에 `kind: output`.",
          "- **레퍼런스:** 스타일의 기준으로 삼은 자료 (참고 이미지, 디자인 토큰, 문체 예시 등).",
          "- **원칙·규칙(작업 방식):** " + freq.PRINCIPLE_GUIDE,
          "- 확인되지 않은 초안이나 버린 시안은 넣지 마세요. 애매하면 넣지 말고 목록으로만 알려 주세요.", ""]
    L += ["## 2. 파일마다 `<폴더>/<이름>.meta.yaml`",
          "폴더: 산출물은 `outputs/`, 레퍼런스는 `image/` `web/` `document/` `writing/` 중 하나. 원본 파일도 같은 폴더에 넣으세요.",
          "```yaml",
          "id: 영문-소문자-하이픈            # 아래 '기존 id'와 겹치지 않게",
          "type: image | web | document | writing",
          "kind: output                      # 산출물일 때만",
          "title: 한국어 제목",
          "file: 원본 파일 이름",
          "summary: 한 줄 요약 (40자 이내)",
          "task: 이 결과물을 만든 작업 설명 한 줄   # 산출물일 때",
          "keywords: [비슷한 말, 관련어 5~10개]   # 나중에 검색될 수 있게 (예: 소개글, 안내, 챗봇)",
          "spec: |                           # 첨부 없이 글로만 전달할 때의 설명 3~6줄, 보이는 것만",
          "  ...",
          "check:                            # 이 결과물·레퍼런스를 따랐는지 확인하는 예/아니오 질문 2~4개",
          "  - ...인가",
          "tags: [..]",
          "sensitivity: public               # 회사 내부 자료면 internal",
          "authored_by: chatgpt_web | claude_desktop | codex   # 지금 당신",
          "```", ""]
    L += ["## 3. `plan.yaml`", "```yaml"]
    if rid:
        L.append(f"request: {rid}")
    L += ["summary: 무엇을 담았는지 한 줄",
          "create:",
          "  - { path: outputs/intro-post.meta.yaml, reason: 확인된 최종 글 }",
          "update:                           # 보낼 곳 세트의 키 추가·변경 (꼭 필요할 때만, 삭제 불가)",
          f"  - node: {node['id'] if node else '@target'}",
          "    set_rules: { <키>: <텍스트 또는 ref:<id>> }",
          "    reason: ...",
          "relations:                        # 참고용 연결 (선택)",
          f"  - {{ from: <새 id>, type: similar_to | variant_of | pairs_with, to: {node['id'] if node else '@target'}, reason: ... }}",
          "missing:                          # zip에 넣지 못한 파일 이름 (없으면 빈 목록)",
          "  - hero-image.png",
          "```",
          "- 산출물만 담을 때는 update가 필요 없습니다(산출물은 보낼 곳 세트에 자동으로 묶입니다).",
          "- 연결을 비워 두어도 Assets Manager가 놓인 노드의 하위로 자동 연결합니다(원칙 아래 규칙 노드는 상속 자식).",
          "- 기존 키를 바꾸는 update는 사용자가 따로 확인합니다.", ""]
    L += ["## 4. zip 만들기",
          "- 이름: `assets-import.zip`. 구조: `plan.yaml`, `outputs/…`, `image/…` 처럼 폴더째로.",
          "- 이 대화에서 생성한 이미지가 코드 실행 환경에서 파일로 보이지 않으면 억지로 다시 만들지 말고, `missing:`에 이름을 적으세요.",
          "- zip을 준 뒤 사용자에게 이렇게 안내하세요: \"zip과 함께, missing에 적힌 파일을 내려받아 **한 번에** Assets Manager 창에 끌어다 놓으세요"
          + (".\"" if node else ". 넣을 노드 위에 놓으면 됩니다.\""), ""]
    L += ["## 참고: 기존 id"]
    for n in sorted(lib.nodes.values(), key=lambda x: (x["type"], x["id"])):
        if n["kind"] != "unregistered":
            L.append(f"- `{n['id']}` · {n['type']}/{n['kind']} · {n['title']}")
    L += ["", "## 참고: 키 어휘"]
    for k, spec in lib.keys.items():
        L.append(f"- `{k}` · {spec.get('label', k)} · {spec.get('priority', 'optional')}")
    return {"prompt": "\n".join(L) + "\n", "request": rid, "node": node_id}
