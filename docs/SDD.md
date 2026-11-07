# Assets Manager — 소프트웨어 설계 문서 (SDD)

앱 버전 0.13.0 · 기획서 v3.3(+14A~14H) 기준 · 2026-11-08

> 이 문서는 **구현의 지도**다. "왜 이렇게 정했나"는 기획서(`../../assets_manager_기획서_v3.3.md`)에 있고, 결정마다 `Qnn` 번호로 연결된다. 코드를 바꾸면 이 문서의 해당 절도 함께 고친다.

---

## 1. 목적과 범위

내 작업 표준(규칙·레퍼런스·산출물)을 **상속 그래프**로 관리하고, 노드를 외부 AI 앱(Codex, Claude 데스크톱, ChatGPT 웹)에 끌어다 놓으면 **결정적으로 조립된 프롬프트와 첨부 파일**이 넘어가는 로컬 데스크톱 앱. 확인된 산출물은 다시 쌓여 다음 작업의 참고 예시가 되고, MCP로 개인 비서 AI가 직접 검색·조립한다.

범위 밖: 앱 안의 LLM 호출(Q36), 협업·클라우드 동기화, 임베딩 검색(Q60, 3단계 후보).

## 2. 설계 원칙 (불변 조건)

코드를 바꿀 때 깨지면 안 되는 규칙. 테스트가 대부분을 지킨다.

| # | 원칙 | 근거 | 지키는 곳 |
|---|---|---|---|
| P1 | **파일이 기준, 나머지는 파생물.** `index.md`, `graph.json`, `.assets/`, 검색 결과, RAG JSONL은 언제든 다시 만들 수 있어야 한다 | Q11, Q17 | `engine.build_index`, `rag.py`, `search.py` |
| P2 | **조립은 결정적.** 같은 폴더 상태 + 같은 세트 + 같은 작업 설명 → 글자 하나까지 같은 프롬프트. 조립 경로에 AI 호출·난수·현재 시각 금지 | Q11, Q58 | `Library.assemble/render/package` |
| P3 | **앱은 외부 AI API를 부르지 않는다.** 해석은 외부 AI가 프롬프트를 받아 하고, 결과는 파일로 돌아온다. 예외(v3.5): 사용자가 설정에서 연결한 **로컬 AI 명령줄 도구**(Claude Code, Codex CLI)를 **사용자가 누른 버튼으로만** 실행한다. API 키는 받지 않고, 도구가 이미 로그인한 구독을 쓴다. 프롬프트는 기존 에이전트용 그대로(P2), 결과는 파일 → 미리보기 → 적용(P4) | Q36, Q41, Q76 | 모든 모듈, `local_ai.py` |
| P4 | **사람이 넣은 단순한 것은 바로 반영, AI가 만든 구조는 미리 보고 확정** (예상 결과 창, 기본 켜짐) | Q38, Q49, Q67 | `drops.py`, `folder_requests.preview/apply` |
| P5 | **막지 말고 되돌릴 수 있게.** 변경 전 버전 보관, 삭제는 휴지통, Git 자동 커밋 | Q39 | `store.backup_sync`, `delete_node`, `undo` |
| P6 | **AI는 기존 파일을 직접 고치지 않는다.** 바꿀 것은 `plan.yaml`로 받고 앱이 키 단위로 적용. 키 삭제 불가, 덮어쓰기는 기본 꺼짐. 예외: 하위 정리(`tidy`)는 구조 변경이라 키 이동·규칙 노드 휴지통을 허용하되, 정리 전·후 **최종 프롬프트 비교**를 반드시 보여 준다 | Q51, Q53, Q72 | `folder_requests.preview/apply`, `_set_rule`, `tidy.preview` |
| P7 | **패키지 동등성.** 세 대상 앱이 받는 프롬프트 본문은 같다. 차이는 경로 단락과 에이전트용 "더 찾아볼 산출물" 단락뿐 | Q20, Q61 | `render(paths=...)` |
| P8 | **민감도는 조립 결과에서 계산.** 상속되는 속성이 아님. RAG 내보내기는 민감 노드 제외가 기본 | Q27 | `package`, `rag.chunks` |
| P9 | **기본값은 레이어 0.** `_defaults/`에 복사된 파일이 기준. 앱 업데이트는 알림 후 사용자가 적용 | Q16, Q17 | `ensure_defaults`, `store.defaults_updates` |

보충 (v3.8):
- **P3** — 콘솔 패널의 **AI 탭**은 사용자가 `+ AI`를 눌러 연 대화형 터미널이다. 앱은 입력을 보내지 않고 사용자 키 입력만 전달한다(자동 실행 없음).
- **P6** — AI 탭의 Claude Code·Codex가 노드를 바꾸는 길은 **`am` 명령**뿐이다. `am`은 앱 함수로 키 단위로 쓰고, 바꾸기 전 보관(P5)·`am undo`·히스토리(`actor: ai-tab`)를 남긴다. 라이브러리 `.claude/settings.json`이 규칙 파일 직접 편집을 막는다(`deny`). Codex는 이런 권한 파일이 없어 루트 `AGENTS.md`의 규칙으로만 막는다. 구조를 크게 바꾸는 일은 `am tidy` → 앱 미리보기(P4).

## 3. 전체 구조

```
 ┌───────────── Tauri 셸 (src-tauri/) ─────────────┐
 │  창 · 네이티브 파일 드래그(tauri-plugin-drag)       │
 │  시작 시 Python 사이드카 실행, 종료 시 정리          │
 └──────────────┬──────────────────────────────────┘
                │ http://127.0.0.1:8765 (fetch)
 ┌──────────────▼────────────── UI (ui/) ───────────┐
 │  app.js: 그래프(Cytoscape) · 상세 패널 · 드롭 영역 │
 │          · 예상 결과 창 · 내보내기 명령 · MCP 창    │
 └──────────────┬──────────────────────────────────┘
                │ JSON API
 ┌──────────────▼──────── 사이드카 (core/server.py) ─┐
 │  파일 서명이 같으면 Library 캐시, 바뀌면 다시 읽음   │
 │  console(am) · terminal(pty: AI 탭)                │
 ├──────────┬─────────┬──────────┬────────┬────────┤
 │ engine   │ drops   │ folder_  │ store  │ search │
 │ 로더·조립 │ 예상결과 │ requests │ 기록·  │ rag    │
 │ 패키지    │ stage/  │ 요청·결과 │ 버전·Git│ export_│
 │ 그래프    │ commit  │ plan 적용 │ 가져오기│ cmd    │
 ├──────────┴─────────┴──────────┴────────┴────────┤
 │ extract.py (결정적 값) · authoring.py (작성 프롬프트) │
 └──────────────┬──────────────────────────────────┘
                │ 파일 읽기·쓰기
 ┌──────────────▼──────── 라이브러리 폴더 (assets/) ──┐
 │ _defaults/ principles/ sets/ image/ web/ document/ │
 │ writing/ golden/ outputs/ history/ inbox/ .assets/ │
 └────────────────────────────────────────────────────┘
        ▲
        │ stdio JSON-RPC
 core/mcp_server.py ◀── Claude 데스크톱 (MCP)
```

## 4. 모듈 지도

| 파일 | 책임 | 주요 함수 / 클래스 | 의존 |
|---|---|---|---|
| `core/engine.py` | 로더, 검증, 상속 조립, 렌더, 패키지(한도·덜어내기·민감도), 변경 감지, 그래프·index. 구성 요소 종류 기본값 `PART_DEFAULTS` | `Library`(`load`, `assemble`, `_attach_components`, `render`, `add_examples`, `package`, `changes`, `approve`, `graph`, `build_index`, `_ref_id`·`_ref_part`), `make_contact_sheet` | PyYAML, Pillow, `search`·`components`(지연 import) |
| `core/server.py` | HTTP API, 정적 파일, 포트 인계(`/api/shutdown`), 레이아웃 저장 | `Handler`, `library()`, `node_detail`, `handle_drop`(v3.1 호환), `bind` | 모든 core 모듈 |
| `core/drops.py` | 예상 결과 창: 놓은 것을 분류해 선택지 생성(stage), 실행(commit), 승격, 되돌리기 | `stage`, `commit`, `promote`, `undo`, `OPT` | `folder_requests`, `store` |
| `core/folder_requests.py` | AI 요청의 수명: 준비 → 패키지 → 결과 수신 → 미리보기 → 적용. 원본 짝 맞추기 | `new_request`, `prepare`, `package`, `render`, `receive_zip`, `attach`, `auto_assign`, `assign`, `preview`, `apply`, `_set_rule` | `extract`, `store` |
| `core/store.py` | 드롭 히스토리, 버전 보관, Git, 휴지통, 가져오기, 기본값 업데이트 | `append_history`, `read_history`, `backup_sync`, `fresh_files`, `versions_for`, `restore_version`, `git_commit`, `delete_node`, `restore_trash`, `import_items`, `import_zip`, `defaults_updates`, `upgrade_defaults` | git(선택) |
| `core/authoring.py` | 파일 하나·여럿을 AI가 메타로 등록하게 하는 작성 프롬프트 (미등록·기존 노드 다시 쓰기) | `build`, `render` | `extract` |
| `core/components.py` | 구성 요소 분리(v3.9, Q81-A): 메타 `components` 정리(`normalize`), 잘라 낸 그림·구도 도식 캐시(`ensure`, `.assets/components/`), AI 요청(좌표 격자 사본 + 프롬프트) → 답 받기 → 미리보기(상자 수정 합치기) → 반영, 사람이 고친 요소 저장 | `normalize`, `ensure`, `crop_image`, `composition_image`, `grid_image`, `build`, `render_request`, `receive`, `preview`, `apply`, `save`, `open_request` | `folder_requests`, `store`, `tidy.parse_plan_text`, Pillow |
| `core/tidy.py` | 하위 정리: 범위(scope)·결정적 진단(diagnose) → AI 요청 프롬프트 → plan(ops) 메모리 시뮬레이션 → 전·후 그래프·변경 이유·프롬프트 비교 → 적용 | `scope`, `diagnose`, `build`, `receive`, `simulate`, `preview`, `apply` | `folder_requests`, `store` |
| `core/local_ai.py` | 로컬 AI 도구 찾기(PATH·기본 설치 위치·직접 경로)·버전·연결 시험·설정, 기능별 요청 준비(`run_for`: tidy·request·authoring → 에이전트용 prompt 파일), 백그라운드 실행·로그·취소·결과 파일 확인 | `detect`, `status`, `update_config`, `test`, `run_for`, `start`, `get_job`, `cancel` | `folder_requests`, `tidy`, `authoring`(지연 import) |
| `core/skills.py` | 스킬 연동: 도구 스킬 `assets-manager` 설치(시작 때), 세트 → 스킬 컴파일·내보내기(라이브러리 안/개인 폴더 × `.claude/skills`/`.agents/skills`)·고친 흔적 감지·제거, 설치된 스킬 읽기 → 그래프 노드 | `install_tool_skill`, `compile_set`, `status`, `export`, `remove`, `scan`, `graph_part` | `engine` |
| `skills/assets-manager/` | 앱 번들 도구 스킬(SKILL.md + references/ + 설치 때 scripts/extract.py) | | |
| `core/standards.py` | 작업 설명 → 맞는 세트 찾기(`find`, 결정적 점수: 제목·본문·규칙 값·붙은 스킬)·조립(`standard`, 맞는 것이 없으면 `global`) | `find`, `standard` | `engine`, `search.grams` |
| `core/cli.py` | 명령줄 입구: `standard "<작업>" [--set] [--no-gate] [--json]`, `sets`, `assemble <id>`. 다른 프로젝트의 AI가 스킬처럼 부름. 그 밖의 명령은 `console.run`으로(`AM_ACTOR` 환경 변수 = 기록의 actor, 기본 `cli`) | `main` | `standards`, `console` |
| `core/console.py` | `am` 노드 명령(v3.8): 보기(ls/show/find/standard), 바꾸기(inherit/use/unuse/set/unset/rename/new), undo, tidy 요청. 콘솔 탭·명령줄·AI 탭이 같은 명령을 쓴다 | `run`, `run_line`, `undo`, `HELP` | `engine`, `folder_requests`, `store`, `standards`, `tidy` |
| `core/terminal.py` | AI 탭 터미널 세션(v3.8): Windows ConPTY(pywinpty)·POSIX pty, 출력 고리 버퍼, 긴 폴링 읽기, 크기 조절, 종료. `am` 실행 파일(`.assets/bin`), 라이브러리 `.claude/settings.json` 권한 합치기 | `start`, `Session`(`read`, `write`, `resize`, `close`), `close_all`, `write_shims`, `ensure_permissions` | `local_ai`(도구 경로), pywinpty(Windows) |
| `core/export_cmd.py` | AI 대화창에 붙여넣는 내보내기 명령 (노드별: 요청 생성 / 일반: `@target`) | `build` | `folder_requests` |
| `core/extract.py` | 결정적 값 추출 (이미지·글·HTML/CSS·PDF). 단독 실행 가능, 외부 AI 코드 실행 환경에도 그대로 올라감 | `extract`, `kind_of` | Pillow, (선택) PyMuPDF |
| `core/search.py` | 결정적 키워드 검색 (단어 + 3글자 조각, 필드 가중치) | `search`, `grams`, `fields` | 없음 |
| `core/rag.py` | `노드id#부분` 조각 JSONL 내보내기 | `chunks`, `export_jsonl` | `engine` |
| `core/mcp_server.py` | MCP 서버 (stdio JSON-RPC 2.0, 외부 패키지 없음) | `handle`, `call`, `TOOLS` | `engine`, `search` |
| `ui/app.js` | 화면 전체 (빌드 단계 없음) | `loadGraph`, `select`, `render*`, `refreshPackage`, `handleDrop`, `openOutcome`, `confirmOutcome`, `copyExport` | Cytoscape·xterm.js 5.5 + addon-fit(번들, `ui/vendor/`), Tauri 전역(`window.__TAURI__`) |
| `src-tauri/src/main.rs` | 셸, 사이드카 실행(`py` → `python` 순서), 종료 처리 | `spawn_server` | tauri 2, tauri-plugin-drag 2 |
| `defaults/` | 앱 번들 기본값 (`keys.yaml`, `targets.yaml`, `defaults.md`) — 라이브러리에 없으면 복사 | | |
| `tests/` | 단위·통합 테스트 86개 | `test_engine`, `test_v3`, `test_requests`, `test_v33`, `test_local_ai`, `test_skills`, `test_standards`, `test_console`, `test_components` | |

## 5. 데이터 모델

### 5.1 노드 종류 (`Library.nodes[id]`)

| `type` | `kind` | 원천 파일 | 비고 |
|---|---|---|---|
| `rule` | `defaults` / `principle` / `set` | md 프런트매터 (`type: rule`) | `inherits`(계층), `uses`(부품 원칙), `rules`, `priorities`, `precedence` |
| `image` `web` `document` `writing` `skill` | `reference` | 원본 옆 `<이름>.meta.yaml` | `file`, `spec`, `check`, `extracted`, `relations`, `components`(이미지, v3.9: 각 항목에 캐시 경로 `file`이 붙음) |
| 위와 같음 | `output` | `outputs/<세트>/<이름>.meta.yaml` (`kind: output`) | `set`, `task`, `keywords`, `origin` |
| 위와 같음 | `unregistered` | 레퍼런스 폴더의 메타 없는 파일 | id는 `new:<상대경로>` |
| `ai` | `agent` / `chat` | `_defaults/targets.yaml` | 그래프 전용 (`ai:<대상>`) |
| `skill` | `skill` | `skills/<이름>/SKILL.md` (폴더째 보관, v3.7) | `name`, `description`, `files`, `body`, `origin`(`.assets-manager.yaml`: 가져온 곳·원래 스킬 끔 여부). 규칙 노드가 `uses`로 붙인다. id = 폴더 이름(겹치면 `-skill`) |
| `installed_skill` | `library` / `personal` / `synced` | 설치된 `<스킬>/SKILL.md` (라이브러리 안·`~`의 `.claude/skills`, `.agents/skills`) | 그래프 전용, 읽기 전용 (`skill:<범위>:<이름>`). `managed`(이 앱이 내보냄)·`stale`·`edited`·`tool` |

`exists: false`인 레퍼런스·산출물 = **원본 없음**(경고). 조립 때 spec이 있으면 글로 대체(`status: text_only`).

### 5.2 규칙 노드 프런트매터

```yaml
id: naver-blog-post
type: rule
kind: set                     # defaults | principle | set
title: 네이버 블로그 포스팅
inherits: [global]            # 계층. 없으면 [_defaults]. 뒤가 우선. 순환 금지
uses: [dev-docs, pdf-tools]   # 부품: 원칙(v3.4)과 라이브러리 스킬(v3.7). 원칙 노드는 inherits를 쓰지 않는다
gate: false                   # 승인 게이트 (v3.4). 없으면 상속, 기본 켜짐
skill_description: ...       # 스킬로 내보낼 때의 설명 (v3.6, 없으면 제목·본문 첫 줄로 생성)
rules:
  tone: 친근한 존댓말           # 텍스트 값
  chart_style: ref:naver-graph-style   # 레퍼런스 값
  image_style:
    value: ref:naver-illustration-style
    note: 엣지 지시문 ({n} {file} {title})
priorities: { image_style: required }  # 키 중요도 덮어쓰기
precedence:                   # 키 우선순위 뒤집기
  - { first: image_style, over: character_style, scope: 캐릭터 }
relations: [{ type: similar_to, target: <id> }]
```

### 5.3 레퍼런스·산출물 메타

```yaml
id: summer-post
type: writing                 # image | web | document | writing | skill
kind: output                  # 산출물일 때만
set: naver-blog-post          # 산출물일 때
title: 여름 휴가 글
file: summer-post.md          # 같은 폴더 기준
summary: 한 줄
spec: |                       # 첨부 대신 글로 넘길 때
  ...
check: [ ...인가 ]             # 자가 점검 (프롬프트 끝 체크리스트)
tags: [..]
keywords: [..]                # 검색용 관련어 (AI가 등록 때 작성)
task: 여름 휴가 준비 글          # 산출물: 만든 작업
origin: { ts, target, prompt_hash, method }   # 산출물: 출처 드롭
sensitivity: public | internal
authored_by: chatgpt_web      # 작성한 AI (targets.yaml의 키)
extracted: {...}              # extract.py 결과
components:                   # v3.9 이미지만: 캐릭터·도형·구도로 나눈 요소 (Q81-A)
  - { name: character, part: character, title: .., box: [x, y, w, h], spec: .., check: [..] }
  - { name: block, part: shape, title: .., box: [..], spec: .. }
  - { name: composition, part: composition, title: .., regions: [{label, box}], flow: [[x, y], ..], spec: .. }  # box 생략 = 전체
relations: [{ type: variant_of | similar_to | pairs_with | supersedes | derived_from, target: <id> }]
```

### 5.4 키 어휘 (`_defaults/keys.yaml`, version 4, 35개)

키마다 `label`, `domain`, `priority`(required/recommended/optional), `aliases`(동의어 경고), `instruction`, `ref_instruction`, `check`, `precedence`. 사용자 정의 키 허용(동의어면 경고). `domain: dev` 키 15개(설계 승인·문서 역할·충돌 처리 등)는 개발 착수 원칙 프리셋용. **조립은 키 단위 덮어쓰기라 한 세트에 붙는 원칙들은 서로 다른 키를 써야 한다**(같은 키 목록 병합은 미구현, 기획서 v3.4 열린 질문). 코드 안의 특수 키: `_example_<n>`(참고 예시, `priority: example` = 덜어내기 1순위).
v4(v3.9): `shape_style`(도형, `image_style`보다 도형에 관해 우선) 추가, 최상위 `parts:` — 구성 요소 종류(character·shape·composition)마다 `label`, `key`(같은 측면의 규칙 키), `ref_instruction`(`{n} {file} {title}`), `check`(`{n}`). 라이브러리 keys.yaml에 `parts`가 없으면 `engine.PART_DEFAULTS`(같은 내용)를 쓴다. 렌더용 특수 모드 키: `<키>#<요소 이름>`(요소 하나의 attach/text/drop).

### 5.5 엣지

| 종류 | 예 | 프롬프트에 들어가나 |
|---|---|---|
| `inherits` | `global → naver-blog-post` | 예 (조립 엣지, 계층) |
| `uses` | `ai-dev-kickoff —원칙→ dev-docs`, `naver-blog-post —스킬→ pdf-tools` | 예 (조립 엣지, 부품). 부품 원칙의 암묵적 `_defaults` 상속선은 그리지 않음 |
| `key` | `naver-blog-post —chart_style→ naver-graph-style` (값 `ref:<id>#<요소>`도 원본 노드로 이음, v3.9) | 예 (조립 엣지) |
| `info` | `similar_to`, `variant_of`, 작성(`ai:* → 노드`), 드롭 N회(`세트 → ai:*`), 산출물(`세트 → 산출물`) | 아니오 |

## 6. 핵심 알고리즘

### 6.1 상속 조립 (`Library.assemble`)
1. `linearize(id)`: 부모를 선언 순서대로 깊이 우선 펼치고 중복은 처음 위치만, 자신은 마지막. 순환이면 `CycleError`.
1-1. `sources(id)` (v3.4): 계층마다 그 계층이 `uses`로 붙인 원칙(`components`, 재귀·중복 제거)을 **계층 바로 앞**에 끼운다. 결과: 계층 자신 > 그 계층의 원칙(뒤에 붙인 것 우선) > 윗 계층. `descendants`도 `sources` 기준(원칙을 쓰는 세트가 영향 범위).
1-2. `skills_of(id)` (v3.7): `sources` 순서대로 각 노드의 `uses` 중 스킬 노드를 모은다(중복은 처음 위치). 전역 원칙에 붙인 스킬은 상속으로 모든 세트에 들어간다. `descendants(스킬)`은 그 스킬을 받는 모든 규칙 노드.
2. 그 순서로 규칙을 키 단위 덮어쓰기. 키마다 `source`, `overridden` 기록. `kind: defaults`에서 온 값은 `from_defaults`.
3. 레퍼런스 값은 `ref_*` 필드와 `status`(ok / text_only / empty / missing)를 채운다. v3.9 `_attach_components`: 원본에 요소가 있으면 `e.components`(요소별 `abs`, `spec`, `check`, `status`, `pkey`=짝 키, 구도 `legend`)를 붙이고 `components.ensure`로 그림 캐시를 맞춘다. 값이 `ref:<id>#<요소>`면 항목 자체가 그 요소(`ref_file`=잘라 낸 그림, `spec`·`check`=요소 것, `part_kind`, `legend`), 없는 요소면 `missing` + 검증 오류.
4. 우선순위: `keys.yaml`의 `precedence` 기본값 → 상속 순서대로 세트의 `precedence`가 같은 키 쌍을 덮어씀. 둘 다 있는 쌍만 활성. 레퍼런스 첨부 순서는 "우선하는 쪽 먼저".
5. 텍스트 항목은 키 어휘 순서, 레퍼런스 항목은 우선순위 순서로 정렬.

### 6.2 렌더 (`Library.render`) — 프롬프트 단락 순서
`# 작업 지침` → (`## 이번 작업`) → `## 규칙` → (`## 우선순위`: 키 쌍 문장 + 참고 예시 문장) → `## 첨부 레퍼런스` → `## 출력 전 자가 점검`(키 check + 레퍼런스 check, 중복 제거) → (경로 모드: `## 파일 경로` + 에이전트용 `## 더 찾아볼 수 있는 산출물`).
`mode[key]`: `attach` | `text`(spec으로 대체) | `sheet`(컨택트 시트 칸) | `drop`(제외).
함께 쓰는 스킬 (v3.7): `## 첨부 레퍼런스` 뒤, 자가 점검 앞에 `## 함께 쓰는 스킬` — 스킬마다 제목·설명·위치만(본문은 넣지 않고 "해당되면 읽고 따르라, 위 규칙이 우선"). 경로 모드는 SKILL.md 절대 경로, 채팅 대상은 첨부(패키지가 `<이름>.SKILL.md`로 복사해 이름이 겹치지 않게). 자가 점검에 "해당 스킬을 따랐는가" 한 줄.
구성 요소 (v3.9, Q81-A, `emit_components`): 원본 항목 바로 아래에 들여 쓴 줄로 요소마다 `parts[part].ref_instruction` + (구도) `(도식 번호: 1 … · 2 …)` + `설명: spec`. 원본이 첨부면 번호 `[n-a]` `[n-b]`…, 원본이 시트면 새 정수 번호, 원본이 글(text)이면 요소도 글로(spec 없으면 뺌). 첨부 목록에 `[n-a] <id>.<요소>.png — 원본 제목 · 요소 제목`. 자가 점검에 `parts[part].check`(번호 채움) + 요소 `check`. 짝 키(`pkey`)가 조립 결과에 있고 원본의 키와 다르면 `## 우선순위`에 "{요소}에 관해서는 **{키}** 규칙을 분리한 요소(첨부 [n-a])보다 우선합니다. 규칙에 없는 부분은 요소를 따르세요." 요소가 없으면 v3.8과 글자 하나까지 같다.
진행 방식 단락 (v3.4, Q73-A, `process_lines`): 게이트가 켜져 있으면 `## 이번 작업` 바로 뒤(규칙 앞)에 `## 진행 방식`을 넣는다 — 이해한 작업·결과물 구성·이미지 계획·확인할 것만 짧게 답하고 승인을 기다림(규칙 요약 금지), 승인 후에는 계획에 없는 것까지 규칙 전체·레퍼런스·자가 점검을 적용, 승인 전 금지는 결과물(본문·이미지·코드·파일) 만들기와 기존 파일 수정뿐(레퍼런스 열어 보기는 허용). 작업이 없으면 같은 단락 앞에 '레퍼런스를 살펴 두고 한 줄로 답한 뒤 기다리기'를 붙인다. 자가 점검 끝에 `GATE_CHECK` 한 줄을 더해 **프롬프트가 품질 기준으로 끝나게** 한다. 게이트를 끄면 아무것도 붙지 않아 v3.3 프롬프트와 글자 하나까지 같다. `gate`는 조립 순서에서 마지막으로 정한 노드가 이기고, 기본은 켜짐. 세 대상 앱에 같은 문장(P7). `canonical`에는 넣지 않는다.

### 6.3 패키지와 자동 덜어내기 (`Library.package`)
- `add_examples(task)`: 같은 세트의 산출물을 `search`로 상위 3개(골든 샘플 제외) → `_example_n` 항목.
- 위반 검사: 파일 수(prompt.md 포함), 파일 크기, 글자 수.
- `trim=True`: 0) 크기 초과 파일 → 텍스트/제외(요소면 그 요소만) → 0-1) 구성 요소 첨부 → spec 텍스트(없으면 제외, 원본보다 먼저, v3.9) → 1) 첨부 → spec 텍스트 (example → optional → recommended → required) → 2) 이미지 2장 이상이면 컨택트 시트 → 3) 키 제외 (required 제외).
- 민감 자료: 대상의 `allow_internal`이 false면 경고, `strip_sensitive`면 텍스트 대체.
- 출력: `.assets/drops/<세트>/<대상>/prompt.md`(+ contact-sheet.png). 요소 그림은 `.assets/components/<id>/`의 캐시 경로를 그대로 넘긴다. `entries[].components[].mode`로 요소별 결과를 돌려준다. `clipboard`는 항상 경로 포함판.

### 6.4 검색 (`search.search`)
질의와 문서(title, keywords, task, tags, summary, spec, 텍스트 본문 6000자)를 단어(`w:`) + 3글자 조각(`g:`, 짧은 단어는 2글자)으로 쪼갠다. 점수 = Σ 필드 가중치 × BM25식 idf × (1 + log tf) × (단어 일치면 2배). 동점은 id 순 → 결정적.

### 6.5 AI 요청 수명 (`folder_requests`)
```
prepare / new_request ──► package ──► (외부 AI) ──► receive_zip 또는 에이전트가 out/에 씀
   inbox/<rid>/src/         prompt.md                         │
                            (+ extract.py, 시트·묶음)            ▼
                                                    preview ──► apply(선택 항목)
                                                  (+ attach/assign: 원본 짝 맞추기)
```
- `request.json.status`: prepared → sent → received → applied. `kind`: folder | drop | export.
- `receive_zip(node)`: plan의 `request`가 있으면 그 요청으로, 없으면 새 요청(대상 = 놓은 노드). 메타·plan·규칙 md → `out/`, 원본 → `src/`.
- `preview`: `@target`을 요청 노드로 풀기. create(형식·id 중복·지문 중복·원본 유무), update(add / overwrite[기본 꺼짐, impact 계산] / same), relation. `depends_on`으로 생성 항목에 딸린 항목 표시.
- 짝 맞추기: 메타 `file` 이름 → 원래 경로 이름 → `assign` → `auto_assign`(종류별로 빠진 것 1개 + 남은 파일 1개).
- 자동 연결(`auto_links`, Q70-C, v3.4 수정): 노드에 놓은 결과는 AI가 연결을 비워도 생성 항목마다 `link` 항목을 붙인다. 새 원칙 → 놓은 노드가 `uses`로 붙임(기본값 아래면 `child_of`), 부품 원칙에 놓은 새 세트 → 그 세트가 원칙을 `uses`(`uses_target`), 그 밖의 새 규칙 노드 → `inherits`에 대상 추가(`_defaults` 제거), 세트 아래 산출물 → 적용 때 `set` 지정(링크 생략), 레퍼런스 아래 비규칙 → `variant_of`, 그 밖 → `child_of`(정보 엣지). 빈 공간에 놓아 대상이 없으면 UI가 대상을 물어 `set_target`으로 다시 미리보기.
- `apply`: 의존성이 끊긴 항목은 자동 제외. 산출물은 `outputs/<set>/`, 원본 없으면 메타만. 규칙은 `_set_rule`로 키만 수정(엣지 note 보존).

### 6.6 예상 결과 (`drops.stage` → `drops.commit`)
`stage`는 놓은 것을 분류해 `{sid, case, what, where, options, details, simple}`을 돌려준다. `case`: result(plan 있는 zip) | zip | files. 선택지 표는 기획서 v3.3 14.4. `simple=True`(빈 공간에 놓은 파일, 결과 없는 zip)만 설정이 꺼졌을 때 창 없이 실행된다. `commit`은 실행 전 `backup_sync`를 하고 `created`(되돌리기 대상)를 돌려준다.

### 6.7 하위 정리 (`tidy`, Q72-A)
- `scope(root)`: 기준 노드 + 상속 자식 + 부품 원칙 + 계층이 아닌 상속 원칙(다른 규칙을 상속하거나 범위 안에서만 상속됨) + 키 레퍼런스 + 정보 연결 + 세트의 산출물. 범위 밖 부모(전역 원칙 등)는 흐리게만 보여 준다.
- `diagnose`: `redundant_inherit`(직접·간접 중복 상속), `principles_as_parents`(원칙 2개 이상 상속), `principle_inherits`(원칙이 다른 규칙을 상속), `shadowed_key`(원칙끼리 같은 키 덮어씀), `repeated_relation`(같은 연결 3개 이상), `unused_principle`, `empty`.
- `build`: 요청 `kind: tidy`(`inbox/<rid>/prompt.md`). AI는 `plan.yaml`의 `ops`로 답한다: `set_inherits` `set_uses` `move_rule` `set_rule` `create_rule` `rename` `set_body` `set_relations` `trash`.
- `simulate`: `copy.deepcopy(lib)`에 ops 적용(create → 나머지 → trash 순), `_validate` 재실행. 항목마다 before/after/reason/error. 쓰이는 노드 `trash`·순환·없는 대상은 오류로 막힌다.
- `preview`: 전·후 `subgraph`, 전·후 진단, 세트마다 `render` 결과 비교(`same`, `changed_keys`, unified diff). 체크 해제 목록(`selected`)으로 다시 시뮬레이션.
- `apply`: `backup_sync` → 바뀐 규칙 노드 프런트매터 다시 쓰기(키 순서 유지)·새 노드 파일·레퍼런스 메타의 연결 → 빈 노드는 `store.delete_node`(휴지통) → 히스토리 `kind: tidy`.
- zip으로 받은 정리 답(`plan.request`가 tidy 요청)은 `drops.stage`가 `case: "tidy"`로 돌려 UI가 정리 창을 연다.

### 6.8 로컬 AI 실행 (`local_ai`, Q76-B)
- 도구 정의 `TOOLS`: `claude`(Claude Code: `-p {instruction} --permission-mode acceptEdits --allowedTools Read,Write,Edit,Glob,Grep,Bash(python:*)… --max-turns 60`), `codex`(Codex CLI: `exec --full-auto --skip-git-repo-check {instruction}`). 인자는 설정에서 바꿀 수 있다. Windows의 `.cmd`는 `cmd /c`로 감싼다.
- 지시(`instruction_for`)는 한 줄: "파일 `<prompt 상대 경로>` 를 읽고 그대로 수행, 지시된 위치에만 쓰기, 질문하지 말고 끝까지". 따옴표·`&|<>%^` 없음(cmd 안전). 작업 폴더는 라이브러리 루트(루트 AGENTS.md·CLAUDE.md의 쓰기 규칙이 함께 읽힘).
- `run_for`: tidy → 에이전트용 `tidy.render`를 `inbox/<rid>/prompt-local.md`에, 기대 결과 `inbox/<rid>/out/plan.yaml` / request → `folder_requests.package(에이전트 대상)`의 `package/prompt.md`, 기대 결과 같은 plan / authoring → `authoring.build(에이전트 대상)`, 메타는 원본 옆.
- `start`: 스레드에서 `Popen`, 출력은 `.assets/local-ai/jobs/<job>.log`, 상태는 `<job>.json`(running → done | failed | cancelled). 자식 프로세스에는 `PYTHONIOENCODING=utf-8`·`PYTHONUTF8=1`을 주고, 출력은 UTF-8로 읽되 아니면 이 PC 기본 인코딩(Windows 한국어 cp949)으로 읽는다(`_decode`). 20분 제한. 종료 코드 0이어도 기대 결과 파일이 없으면 failed. 취소는 Windows `taskkill /T /F`.
- 흐름: 기능 버튼 → `ensureLocalAI`(준비 안 됐으면 설정 창을 안내 모드로) → `runLocal` → 오른쪽 아래 진행 표시 → 끝나면 기존 미리보기(정리 창·결과 창)를 연다. 반영은 언제나 사용자가 한다.

### 6.9 스킬 연동 (`skills`, Q77-A)
- 형식: 공개 스킬 표준. 프런트매터는 `name`(세트 id를 소문자-하이픈으로), `description`(≤1024자), `metadata{generator, set, hash}`만 — Claude Code 밖(claude.ai 업로드 등)에서 다른 키는 거부된다.
- `compile_set`: `assemble` → `render`(게이트·자가 점검 포함, 경로 없음) → 첫 줄을 스킬 머리말(“자동 생성, 고칠 것은 세트에서”, `references/` 안내)로 바꿈. 첨부는 `references/<파일>`로 복사. 내부 자료(sensitivity ≠ public) 레퍼런스는 글로만(P8). 같은 라이브러리 상태 → 같은 SKILL.md(P2). 500줄 넘으면 경고.
- `export`: 대상 = 범위(`library`=라이브러리 루트, `personal`=`~`) × 도구 폴더(`claude`=`.claude/skills`, `agents`=`.agents/skills`). 이미 있으면 상태 확인: 기록에 없으면 `foreign`, SKILL.md 지문이 기록과 다르면 `edited` → `overwrite` 없이는 멈추고 diff를 돌려준다. 지난번에 쓴 파일 중 이번에 없는 것만 지운다.
- `scan`: 라이브러리 안·개인 폴더·`~/.claude/skills/synced`의 `*/SKILL.md`를 읽어 (범위, 이름)별로 묶음. 기록과 대조해 `managed`/`edited`, 세트 hash와 대조해 `stale`.
- 도구 스킬: 서버 시작 때 `install_tool_skill`이 `app/skills/assets-manager/` + `core/extract.py`(→ `scripts/`)를 라이브러리 안 두 폴더에 바이트 단위로 맞춘다. 로컬 AI 지시문이 이 스킬을 가리킨다.

### 6.10 스킬 보관과 호출 (v3.7, Q78-A)
- **가져오기** `skills.import_skill`: 설치된 스킬 폴더를 `skills/<이름>/`로 복사(`.git` 등 제외) → `.assets-manager.yaml`(origin, imported, origin_sha, disabled_original). 원래 스킬 끄기를 고르면 원래 폴더의 `SKILL.md`만 `SKILL.md.disabled-by-assets-manager`로 이름을 바꿔 도구가 읽지 않게 한다(`restore_original`로 되돌림). 노드를 고르면 그 노드의 `uses`에 붙인다. 폴더(SKILL.md 포함)를 규칙 노드에 놓아도 `drops.stage`가 `skill_import` 선택지를 낸다.
- **붙이기** `skills.attach`: 규칙 노드 프런트매터 `uses`에 추가/제거(`backup_sync` 먼저).
- **원칙으로 풀기**: `tidy.build(goal={kind: skill_to_principle, skill})` — 정리 요청에 스킬 본문과 "규칙형만 원칙으로, 절차·스크립트가 남으면 스킬 유지" 목표를 넣는다. 결과는 일반 하위 정리 미리보기(프롬프트 비교 포함).
- **호출** `standards.standard(task)`: `find`로 세트 점수(제목 4, 본문 2, 붙은 스킬 1.5, 규칙 값 1, 단어 일치 2배, 동점은 id) → 최고 세트를 에이전트 대상으로 `package(task=작업)` → 경로 포함 프롬프트 + 머리말(선택된 세트, 다른 후보). 없으면 `global`. 입구: `cli.py standard`, MCP `standard`, 도구 스킬 1장.
- **AI가 부르게 하기** `skills.caller_apply`: 도구 스킬(경로를 채운 SKILL.md)을 개인 `~/.claude/skills`·`~/.agents/skills`에 설치, 또는 `~/.claude/CLAUDE.md`·`~/.codex/AGENTS.md`에 `<!-- assets-manager:start -->…end` 블록 한 줄. 블록 밖은 건드리지 않는다.

### 6.11 콘솔과 AI 탭 (v3.8, Q79-A·Q80-A)
- **`am` 명령** `console.run(root, argv, actor)` → `{code, output, changed}`. 쓰기마다: `store.backup_sync` → 바뀔 파일의 이전 내용을 `.assets/console/undo.jsonl`에 한 줄로(새 파일은 `null`) → `folder_requests._rewrite_frontmatter`/`_set_rule`로 키 단위 쓰기(본문·키 순서 유지) → `history/drops.jsonl`에 `{kind: console, actor, line, changed}`. `inherit`는 쓴 뒤 `sources`로 순환을 확인하고 순환이면 곧바로 되돌린다. `use`는 원칙·스킬만 받는다(세트는 `inherit`). `new set`은 `global`이 있으면 기본으로 상속. `undo`는 스택 맨 위를 되돌린다(만든 파일은 지움, 그 전에 보관).
- **입구 세 곳이 같은 명령**: 콘솔 탭(`POST /api/console`, actor `user`), 명령줄(`cli.py <명령>`·`.assets/bin/am`, actor `cli`), AI 탭(PATH 앞에 `.assets/bin`, `AM_ACTOR=ai-tab`).
- **AI 탭** `terminal.start(root, tool)`: `local_ai.status`의 경로로 Claude Code·Codex를 pty에서 띄운다(Windows `.cmd`는 `cmd /c`). 작업 폴더 = 라이브러리 루트, `TERM=xterm-256color`. 출력은 UTF-8 증분 디코딩 후 세션 버퍼(최대 40만 자, 넘치면 앞을 버리고 `base` 오프셋을 올림). `read(since, wait)`는 새 출력이 올 때까지 최대 `wait`초 기다리는 긴 폴링. 동시에 `MAX_AI = 1`개. 입력이 `IDLE_HINT`(30분) 없으면 UI가 닫기를 묻는다. 서버 종료(`/api/shutdown`, atexit, Ctrl+C) 때 모두 종료.
- **권한** `terminal.ensure_permissions`: 서버 시작·AI 탭 시작 때 `.claude/settings.json`의 `permissions.allow`에 `Bash(am)` `Bash(am *)` `Bash(python|py app/core/cli.py *)`, `deny`에 `Edit(./sets/**)` `Edit(./principles/**)` `Edit(./skills/**)` `Edit(./history/**)` `Edit(./index.md)` `Edit(./graph.json)`을 없는 것만 더한다. 사용자 항목은 그대로, JSON이 깨져 있으면 손대지 않는다. (`inbox/<rid>/out/` 아래 쓰기는 막지 않는다 — 기존 요청 흐름.)
- **변경 감지·캐시** `engine.library_signature(root)`: 로더가 읽는 파일의 (상대 경로, 크기, 수정 시각) sha1 앞 16자. 앱이 쓰는 `index.md` `graph.json` `layout.json`과 숨김·제외 폴더는 뺀다. `server.library()`는 서명이 같으면 캐시한 `Library`를 돌려주고, `sync=True`면 항상 다시 읽는다. 그래프 노드마다 `sig`(노드 파일 크기·시각)가 붙어 UI가 바뀐 노드를 깜빡인다.

### 6.12 구성 요소 분리 (`components`, v3.9, Q81-A)
- `normalize(raw, parts, size)`: 저장 형식으로 정리하고 문제를 돌려준다. 알 수 없는 `part`·잘못된 이름(영문 소문자-하이픈)·box 없는 캐릭터/도형은 빼고 경고, 이름이 겹치면 `-2`를 붙인다. box가 이미지 밖이면 경고만(자를 때 맞춤). 로더가 부르고, 문제는 노드 이슈(warning).
- 그리기(결정적): `crop_image`(box 자르기 + 흰 여백 4%, 최소 8px), `composition_image`(원본을 흰색과 62% 섞고 `regions` 번호 상자·`flow` 화살표, regions가 없으면 캐릭터·도형 box), `grid_image`(AI용: 20칸 이하가 되는 간격의 선 + 가장자리 좌표 숫자). `ensure`: 원본 크기·수정 시각·요소 목록의 sha1을 `stamp.txt`와 비교해 다를 때만 다시 그린다. 실패하면 False → 조립은 spec 글로 대체.
- 요청: `build` → `request.json`(`kind: components`, status sent) + `src/<id>.grid.png` + `prompt.md`(`render_request`: 요소별 고르는 법, 좌표 규칙, 다시 그릴 수 있는 spec 쓰는 법, 추출 색, 지금 있는 요소, `plan.yaml` 형식; 에이전트는 절대 경로를 받고 `out/plan.yaml`에 직접 씀). 로컬 AI `run_for(kind: components)`는 에이전트용 프롬프트를 `prompt-local.md`에 쓴다.
- 받기: 붙여넣기 `receive`(tidy와 같은 파서) · 에이전트의 `out/plan.yaml` · zip(`drops.stage`가 `case: components`).
- `preview(rid, edits)`: 요소별 수정(`{name: {box?, regions?, title?, spec?}}`)을 `request.json.edits`에 합쳐 두고 plan에 덮은 뒤 `inbox/<rid>/preview/`에 그려 돌려준다. 항목 `status`: new(기본 체크) / replace(같은 이름의 기존 요소, 기본 해제, P6) / same.
- `apply(rid, selected)`: 기존 순서 유지하며 같은 이름은 바꾸고 새 것은 뒤에 붙임 → `backup_sync` → 메타 다시 쓰기(다른 필드 보존, 짧은 목록은 한 줄 표기) → 요청 applied → 히스토리 `kind: components`.
- `save(nid, comps)`: 상세 화면의 상자 고치기·빼기. 사람이 한 변경이라 바로 반영(P4), 쓰기 전 보관(P5), 형식 오류는 거부.

## 7. 폴더 구조와 수명

| 경로 | 성격 | Git | 로더가 읽나 |
|---|---|---|---|
| `_defaults/` `principles/` `sets/` | 규칙 노드 | 예 | 예 |
| `image/` `web/` `document/` `writing/` `golden/` | 레퍼런스 (+ 미등록 파일) | 예 | 예 |
| `outputs/<세트>/` | 산출물 | 예 | 예 |
| `layout.json` | 노드 위치 | 예 | 서버만 |
| `history/drops.jsonl` | 드롭 히스토리 (정식 데이터) | 예 | 아니오 |
| `history/versions/` `history/trash/` | 버전 보관, 휴지통 | **아니오** | 아니오 |
| `inbox/<rid>/` | AI 요청 작업장 (구성 요소 요청은 `src/<id>.grid.png`, `preview/`도) | **아니오** | 아니오 |
| `.assets/` | 캐시 (드롭 패키지, 스냅숏, 작성 패키지, export) + 기기별 설정 `local_ai.json`(지우면 다시 찾기)·로컬 AI 작업 기록 `local-ai/jobs/` | **아니오** | 아니오 |
| `skills/<이름>/` | 라이브러리에 보관한 스킬(폴더째) | 예 | 예 (`SKILL.md`만 노드, 부속 파일은 노드 아님) |
| `.claude/skills/` `.agents/skills/` | 라이브러리 안 스킬: 도구 스킬 `assets-manager`(앱이 맞춤) + 내보낸 세트 스킬 (파생물) | **아니오**(앱이 쓴 폴더만 `.gitignore`에 추가) | 아니오 (숨김 폴더) |
| `.assets/skills-manifest.json` | 내보낸 스킬 기록 (경로별 세트·hash·SKILL.md 지문·파일 목록) | 아니오 | 아니오 |
| `.assets/components/<id>/` | 구성 요소 캐시: `<id>.<요소>.png`(잘라 낸 그림·구도 도식) + `stamp.txt` (원본·요소가 같으면 다시 그리지 않음, 지우면 다시 그림) | 아니오 | 아니오 |
| `.assets/console/undo.jsonl` | `am` 되돌리기 스택 (바뀌기 전 파일 내용) | 아니오 | 아니오 |
| `.assets/bin/am` `am.cmd` | `am` 실행 파일 (서버 시작 때 이 PC의 Python·경로로 다시 씀) | 아니오 | 아니오 |
| `.claude/settings.json` | 라이브러리에서 뜨는 Claude Code 권한: `am` 허용, 규칙 파일 직접 편집 금지 (앱은 빠진 항목만 더함) | 예 | 아니오 |
| `index.md` `graph.json` | 자동 생성 | **아니오** | 아니오 |
| `app/` | 앱 코드. **자체 Git 저장소 `app/.git`** (라이브러리 기록과 분리: 루트 `.gitignore`의 `app/`은 `store.GITIGNORE_LINES`가 유지). `app/.gitignore`: `node_modules/` `src-tauri/target/` `src-tauri/gen/` `__pycache__/` `*.log` | 루트: 아니오 / app: 예 | 아니오 |
| `app/docs/archive/` | 옛 기획서 v1~v3.2 (결정 Qnn의 배경). 현재 기준은 루트의 `assets_manager_기획서_v3.3.md` | app | 아니오 |

로더 제외 목록: `engine.SKIP_DIRS`(`app`, `history`, `inbox`, 숨김 폴더). Git 제외 목록: `store.GITIGNORE_LINES`(없는 줄만 자동 추가).

## 8. HTTP API (`core/server.py`, 127.0.0.1:8765)

| 메서드 · 경로 | 용도 |
|---|---|
| GET `/api/health` | 버전·pid (UI가 버전 불일치 감지) |
| GET `/api/graph` | 노드·엣지·이슈·대상·변경·기본값 업데이트·요청·레이아웃 (버전 보관·Git 동기화 포함) |
| GET `/api/node/<id>` | 상세 (조립, 버전, 히스토리, 산출물 목록, 추출 값) |
| GET `/api/package/<id>?target&trim&strip&all&task&gate` | 드롭 패키지 (`gate=1|0`은 이번만 덮어씀, 결과에 `gate`·`set_gate`) |
| GET `/api/authoring?files=a|b&target&note` | 작성 프롬프트 패키지 |
| GET `/api/export-command[?node]` | 내보내기 명령 (노드별이면 요청 생성) |
| GET `/api/search?q&kinds&set&limit` | 검색 |
| GET `/api/request/<rid>/package?target&note` · `/preview` | 요청 패키지, 결과 미리보기 |
| GET `/api/requests` `/api/trash` `/api/history` `/api/layout` | 목록 |
| GET `/api/rag/export[?internal=1]` · `/api/mcp-config` | RAG JSONL, MCP 설정 |
| POST `/api/stage` {node, paths \| uploads[{name,b64}], folder} | 예상 결과 만들기 |
| POST `/api/stage/<sid>/commit` {option, params} · `/discard` | 실행·취소 |
| POST `/api/request/<rid>/attach` {paths \| uploads} · `/assign` {path, file} | 원본 짝 맞추기 |
| POST `/api/request/<rid>/apply` {selected} · `/discard` | 결과 적용·버리기 |
| GET `/api/tidy?node&target[&goal=skill:<id>]` · `/api/tidy/open?node` | 하위 정리 요청 만들기(진단·프롬프트·현재 그래프) · 이 노드의 열린 정리 요청 |
| POST `/api/tidy/<rid>/receive` {text} · `/preview` {selected} · `/apply` {selected} | AI 답 붙여넣기 → 미리보기 → 반영 |
| GET `/api/components/request?node&target` · `/api/components/open?node` | 구성 요소 분리 요청 만들기(프롬프트·파일 3개·격자 사본·드래그 아이콘) · 이 노드의 열린 요청(답이 있으면 미리보기 포함) (v3.9) |
| POST `/api/components/<rid>/receive` {text} · `/preview` {edits} · `/apply` {selected} | AI 답 붙여넣기 → 미리보기(상자 수정 합치기) → 반영 |
| POST `/api/components/node/<id>` {components} | 반영된 요소 목록 저장(상자 고치기·빼기) |
| POST `/api/request/<rid>/target` {node} | 결과를 둘 노드 지정(빈 공간에 놓은 경우) → 미리보기 |
| GET `/api/local-ai` · `/api/local-ai/job/<jid>` | 로컬 AI 도구 상태(처음이면 찾기)·`ready` · 작업 상태와 로그 끝부분 |
| POST `/api/local-ai/detect` · `/config` {default, tools{name:{enabled, custom_path, args}}} · `/test` {tool} | 다시 찾기 · 설정 · 연결 시험(짧은 질문, 최대 3분) |
| POST `/api/local-ai/run` {kind: tidy\|components\|request\|authoring, request?, files?, note?, tool?} · `/job/<jid>/cancel` | 실행(연결 안 됐으면 409 `needs_setup`) · 취소 |
| GET `/api/skills/set/<id>` | 세트 → 스킬 미리보기(SKILL.md, 설명, 첨부, 경고)·위치별 설치 상태(absent/ok/edited/foreign, stale) |
| POST `/api/skills/export` {set, scope: library\|personal, agents: [claude, agents], overwrite} · `/api/skills/remove` {set, scope, agents, force} | 내보내기(고친 흔적·다른 스킬이 있으면 `conflicts`+diff로 멈춤) · 제거(앱이 쓴 파일만) |
| POST `/api/skills/import` {path, node?, disable_original} · `/attach` {skill, node, detach?} · `/restore-original` {skill} | 설치된 스킬을 라이브러리로 가져오기(원래 SKILL.md는 `SKILL.md.disabled-by-assets-manager`로) · 붙이기/떼기 · 원래 스킬 되살리기 |
| GET `/api/caller` · POST `/api/caller` {item: skill_claude\|skill_agents\|rule_claude\|rule_codex, action: install\|remove} | AI가 Assets Manager를 부르게 하기: 개인 스킬 설치, 전역 지침 한 줄(표시 블록 사이에만 씀, 처음 고치기 전 `.assets-manager.bak`) |
| POST `/api/console` {line} | `am` 한 줄 실행 → `{code, output, changed}` (v3.8) |
| GET `/api/changes` | 라이브러리 파일 서명 `{sig}` — UI가 창이 보이고 포커스일 때 2초마다 확인 |
| GET `/api/term` | AI 탭 사용 가능 여부(`available{ok, hint}`), 세션 목록, `max_ai`, `idle_hint` |
| POST `/api/term/start` {tool: claude\|codex, cols, rows} | AI 탭 시작 (연결 안 됨 `need: connect`, pywinpty 없음 `need: pywinpty`, 이미 1개 `busy`) |
| GET `/api/term/<sid>/read?since&wait` | 출력 긴 폴링 (최대 10초) → `{data, next, lost, alive, exit_code, idle}` |
| POST `/api/term/<sid>/write` {data} · `/resize` {cols, rows} · `/close` | 키 입력, 크기, 종료 |
| POST `/api/node/<id>/promote` {overwrite} | 산출물 → 골든 샘플 |
| POST `/api/node/<id>/gate` {gate} | 세트의 승인 게이트 기본값 저장 (프런트매터 `gate`) |
| POST `/api/history` · `/api/layout` · `/api/approve` | 기록, 위치, 변경 확인 |
| POST `/api/delete` · `/api/restore-trash` · `/api/restore-version` · `/api/undo` | 되돌리기 계열 |
| POST `/api/defaults/upgrade` · `/api/shutdown` | 기본값 적용, 서버 인계 |
| POST `/api/import` · `/api/drop` · `/api/request/prepare` | v3·v3.1 호환 (UI는 `/api/stage`를 씀) |

## 9. MCP 도구 (`core/mcp_server.py`)

| 도구 | 입력 | 출력 |
|---|---|---|
| `standard` | task, set? | `{found, set, candidates, prompt}` — 작업에 맞는 표준을 찾아 조립 (v3.7) |
| `search` | query, kind?, set?, limit? | `[{id, score, title, kind, type, set}]` |
| `get` | id | 노드 메타 + `final_rules`(규칙 노드) + `text`(텍스트 원본 일부) + `abs_file` |
| `related` | id | 그 노드에 닿는 엣지 목록 |
| `assemble` | id, task?, target? | `{prompt(경로 포함), files, warnings, violations}` |

프로토콜 `2024-11-05`, 줄 단위 JSON. 알림(`notifications/*`)에는 응답하지 않는다. 오류는 `isError: true` 결과로.

## 10. 외부 AI와 주고받는 형식

| 방향 | 형식 | 만드는 곳 |
|---|---|---|
| 앱 → AI (작업) | `prompt.md` + 레퍼런스 원본 (+ 경로 텍스트) | `engine.package` |
| 앱 → AI (파일 등록) | 작성 프롬프트 (+ `extract.py`, 원본) | `authoring.build` |
| 앱 → AI (폴더 요청) | 가이드 프롬프트 + `plan.yaml` 규격 (+ 시트·묶음) | `folder_requests.package` |
| 앱 → AI (구성 요소 분리) | 분리 요청 프롬프트 + 원본 + 좌표 격자 사본 → `plan.yaml`의 `components` | `components.build` |
| 사용자 → AI (내보내기) | 내보내기 명령 (붙여넣기) | `export_cmd.build` |
| AI → 앱 | `assets-import.zip`: `plan.yaml`, `<폴더>/<이름>.meta.yaml`, 원본, 규칙 md / 또는 에이전트가 `inbox/<rid>/out/`에 직접 | `receive_zip`, `store.import_zip` |

구성 요소 답 `plan.yaml`: `request`, `node`, `summary`, `components[{name, part, title, box, regions?, flow?, spec, check}]` (형식: `skills/assets-manager/references/components-format.md`).
`plan.yaml`: `request?`, `summary`, `create[{path, reason}]`, `update[{node | "@target", set_rules{}, reason}]`, `relations[{from, type, to, reason}]`, `missing[]`.

## 11. UI 구조 (`ui/app.js`)

- 설정 창(`#settings-dialog`, 상단 `설정` 버튼·초록 점 = 연결됨): `openSettings({reason, then})` — 도구 카드(찾음/버전/기본 선택/사용/연결 시험/설치 명령 복사/고급: 직접 경로·실행 인자). `reason`이 있으면 위에 '이 기능은 AI가 필요합니다' 안내와 단계, 연결되면 `계속하기`로 원래 작업을 이어 실행. 진행 표시 `#job-bar`(`runLocal`·`pollJob`·`renderJobs`: 경과 시간, 로그 보기, 취소, 실패 사유).
- 스킬: 그래프의 태그 모양 노드(보라 = 라이브러리 안, 파랑 = 개인 폴더, 이중 테두리 = 도구 스킬, 점선 = 갱신 필요, 빨강 = 직접 고침), 상단 `스킬` 토글. 세트 상세의 `#skill-box`(`loadSkillBox`: 범위×도구 상태 표, 내보내기·다시 내보내기·제거, 충돌 diff와 덮어쓰기, SKILL.md 미리보기), 스킬 상세 `renderSkill`.
- 라이브러리 스킬: 초록 태그 노드(`libskill`). 상세 `renderLibSkill`(설명, 붙인 노드·떼기, 상속으로 받는 세트, 붙이기+범위 안내 `scopeHint`, 원칙으로 풀기, 파일, 가져온 곳·되살리기). 규칙 상세의 `skillsHtml`(함께 쓰는 스킬 + 붙이기). 설치된 스킬 상세의 '앱으로 가져오기'(붙일 노드, 원래 스킬 끄기). 설정 창 `callerHtml`(개인 스킬·전역 지침 설치/제거, 직접 부르는 명령).
- 하위 정리 창(`#tidy-dialog`): `openTidy` → (열린 요청이 있으면 이어서) 1 진단 · 2 요청 복사 · 3 답 붙여넣기 → `showTidyPreview`(왜, 전·후 미니 그래프 `miniGraph`, 최종 프롬프트 비교, 변경 체크) → `applyTidy` → `arrangeTidy`(세트는 깊이 순 아래로, 원칙은 오른쪽 줄, 레퍼런스는 왼쪽 줄로 다시 배치하고 저장).

- 구성 요소(v3.9): 이미지 레퍼런스 상세의 `compsHtml`(요소 카드: 잘라 낸 그림, 종류, 설명, 점검, `ref:<id>#<요소>` 복사, 빼기 / `AI로 나누기`·`상자 고치기`). 창 `#comp-dialog`: `openComponents`(열린 요청에 답이 있으면 바로 미리보기) → `renderCompRequest`(대상 선택, 로컬 AI, 끌어 놓기 손잡이 = 프롬프트+원본+격자 사본, 요청 복사, 답 붙여넣기) → `showCompPreview`(상자 편집기 `boxEditorHtml`/`bindBoxEditor`: 끌어 옮기기·모서리로 크기, 놓으면 바뀐 것만 `preview`에 보내 다시 그림 / 요소 카드 체크 → 반영). `editComponents`는 같은 편집기로 반영된 요소를 고쳐 바로 저장. 세트 규칙 표의 레퍼런스 칸에 `+ 요소 N개`. 놓은 zip이 분리 답이면 `case: components`로 미리보기를 연다.
- 콘솔 패널(`#console-panel`, 상단 `콘솔` 버튼·Ctrl+\`): 아래쪽, 높이 끌어 조절(localStorage `am.consoleH`). 처음 열 때 xterm을 만든다(`conInit`). **콘솔 탭**은 앱 안 줄 편집기(`conConsoleInput`: ←→ Home End, ↑↓ 기록 200줄, Tab 명령·노드 id 완성, Ctrl+C·L·U, 여러 줄 붙여넣기, 한글 폭 2칸 계산) → `POST /api/console` → 출력 → 바뀐 노드가 있으면 `conRefresh`. **AI 탭**(`+ AI` 선택 → `conAddAI`)은 xterm ↔ `/api/term/*`(입력 8ms 모아 보내기, 긴 폴링 `conReadLoop`, 창 크기 `fit`). 탭 × = 프로세스 종료. 스크롤백 5000줄.
- 실시간 반영: `conPoll`이 창이 보이고 포커스일 때 2초(아니면 4초, 요청 없음)마다 `/api/changes` → 서명이 바뀌면 `conRefresh`(대화상자가 열려 있으면 닫힌 뒤) → `loadGraph` 후 노드 `sig`가 달라진 노드에 `.flash`(2.4초).
- 상태: 전역 `state` (graph, cy, selected, target, authorTarget, pkg, opts, tasks, outcome, request, requestPkg). 사용자 편의 설정만 localStorage(`am.*`).
- 그래프: `loadGraph`가 매번 Cytoscape를 다시 만들되 위치는 현재 화면 > `layout.json` > 이웃 근처 순으로 유지. 미리보기 요소는 `.preview` 클래스(저장 제외).
- 상세 패널: `renderRule` / `renderRef`(산출물 포함) / `renderUnregistered`(작성·다시 쓰기) / `renderAi`.
- 드롭: `setupInbound`(Tauri `tauri://drag-*` 이벤트 또는 HTML5) → `handleDrop` → `/api/stage` → `openOutcome` → `confirmOutcome` → `/api/stage/<sid>/commit`. 결과 확인 중 놓은 파일은 `attach`로 간다.
- 내보내기 드래그: Tauri는 `plugin:drag|start_drag`(파일 여러 개), 브라우저는 텍스트 드래그. 놓으면 `/api/history`에 기록.
- `APP_VERSION`은 `server.py`와 `app.js`에서 같아야 한다(다르면 UI가 경고).

## 12. 테스트

```
python -m unittest discover -s app/tests      # 라이브러리 루트에서
```

| 파일 | 다루는 것 |
|---|---|
| `test_engine.py` | 기본값 복사, 다중 상속, 순환, 동의어, 지시문, 결정성, 덜어내기 3단계, 민감도, 변경 감지, index |
| `test_v3.py` | 우선순위·점검, 미등록·작성, 가져오기, 히스토리, 버전·휴지통·Git, 기본값 업데이트, fresh |
| `test_requests.py` | 폴더 요청: 준비, 시트·묶음, 결과 미리보기·적용, 덮어쓰기, 잘못된 plan |
| `test_v33.py` | 검색, 참고 예시·덜어내기, stage 선택지, commit(산출물·원본·변형·골든·승격·되돌리기), 요청 없는 zip·`@target`·짝 맞추기, 내보내기 명령, RAG, MCP(stdio 실제 프로세스), 노드 종류별 자동 연결, 개발 착수 프리셋 조립, `uses` 조립 순서, 하위 정리 왕복(프롬프트 동일·쓰이는 노드 휴지통 거부·값 변경 감지), 승인 게이트·작업 대기 끝 단락 |

| `test_local_ai.py` | 로컬 AI 도구: 찾기(직접 경로)·버전·연결 시험, 연결 안 됐을 때 오류, 실행 → 결과 파일 → 정리 미리보기, 결과 파일이 없으면 failed. 가짜 도구(POSIX는 shebang 스크립트, Windows는 `.cmd`로 감쌈) |
| `test_skills.py` | 도구 스킬 설치(다시 해도 같음), 세트 → 스킬 컴파일(이식 가능한 프런트매터·결정성), 내보내기·고친 흔적 감지·제거, 다른 스킬은 덮어쓰지 않고 읽기만 |
| `test_standards.py` | 스킬 노드 보관·붙이기·조립, 표준 찾기·`cli.py standard`, 설치된 스킬 가져오기(원래 끄기), 스킬 폴더를 노드에 놓기, AI가 부르게 하기(개인 스킬·전역 지침 줄), 원칙으로 풀기 요청, 스킬 휴지통(폴더째) |
| `test_components.py` | 구성 요소: 메타 정리·경고, 원본 + 요소 첨부 렌더·파일 생성, 결정성(같은 PNG·프롬프트), 규칙 우선 문장, `ref:<id>#<요소>`·없는 요소 오류, 덜어내기(요소 먼저 글로), 요소 없으면 프롬프트 그대로, 요청 왕복(격자·붙여넣기·상자 수정·반영·다른 필드 보존), 기존 요소 바꾸기 기본 꺼짐·에이전트 경로, zip → `case: components`, 사람 수정 저장, 스킬 내보내기에 요소 포함 |
| `test_console.py` | `am` set·unset·undo(본문 보존), use·inherit·순환 거부, new·rename·기록의 actor, 오류, 명령줄 연결(`cli.py set`·`standard`), 파일 서명, `.claude/settings.json` 합치기(사용자 항목 유지·깨진 JSON 보존), `am` 실행 파일, POSIX pty 세션(환경·한글 입출력·1개 제한·종료) |

UI는 자동 테스트가 없다. 변경 후 브라우저 모드(`start.bat browser`)로 확인한다.

## 13. 실행과 배포

- `start.bat`(라이브러리 루트): Python 확인 → 패키지 설치(최초) → (선택) AI 탭용 `pywinpty` 설치, 실패해도 계속 → 8765 포트의 이전 서버 정리 → 빌드된 exe / `npm run dev` / 브라우저 모드. 메시지는 영문만(cmd 한글 파싱 문제).
- 앱 코드 이력: `app/`에서 `git log`. 코드를 바꾼 작업은 끝날 때 `app/`에서 커밋한다(라이브러리 데이터의 자동 기록과 별개).
- 서버 시작 시 포트가 막혀 있으면 이전 서버에 `/api/shutdown`을 요청하고 인계받는다.
- exe는 빌드한 PC의 `app/` 경로를 기준으로 사이드카를 찾는다(`CARGO_MANIFEST_DIR`). 다른 PC 배포는 3단계.

## 14. 확장 지점

| 하고 싶은 것 | 고칠 곳 |
|---|---|
| 새 키 | `_defaults/keys.yaml`(+ `app/defaults/keys.yaml`, `version` 올리기) |
| 새 구성 요소 종류 | `keys.yaml`의 `parts:`(+ `engine.PART_DEFAULTS`), 요청 문구 `components.render_request`, UI `PART_LABEL` |
| 새 드롭 대상 AI | `targets.yaml`에 항목 추가(`kind: agent|chat`) |
| 새 레퍼런스 타입 | `engine.REF_TYPES`, `REF_FOLDERS`, `EXT_TYPE`, `store.TYPE_FOLDER`/`EXT_TYPE`, `extract.kind_of`, UI `TYPE_LABEL` |
| 예상 결과 선택지 | `drops.OPT`, `stage`의 분기, `commit`의 실행부, UI `choiceHtml` |
| 임베딩 검색 | `search.search`의 내부만 교체 (입구 시그니처 유지) |
| MCP 도구 | `mcp_server.TOOLS` + `call` |
| 원칙 프리셋 나누기 | 앱의 `하위 정리 (AI)`로 AI에게 맡기거나, `principles/dev-*.md`(키 묶음 이동) + 세트의 `uses` 직접 수정. 새 주제면 `keys.yaml`에 dev 키 추가 |
| 정리 op 추가 | `tidy._apply_op`, `OP_LABEL`, `render`의 형식 예시, UI `itemText`·`OP_ICON` |
| 스킬을 읽는 새 도구 폴더 | `skills.AGENTS`에 (상대 폴더, 라벨) 추가 |
| 도구 스킬 내용 | `app/skills/assets-manager/` (프롬프트 규격이 바뀌면 references도 함께). SKILL.md의 `{PY}` `{CLI}` `{LIB}`는 설치 때 채워진다 |
| `am` 명령 추가 | `console._dispatch` + `HELP`, UI `CON_CMDS`(완성), 테스트 `test_console` |
| AI 탭 도구 추가 | `local_ai.TOOLS`(경로 찾기) + UI `#con-add` 선택지 |
| 표준 찾기 점수 | `standards.WEIGHT`, `_text` (입구 시그니처 유지) |
