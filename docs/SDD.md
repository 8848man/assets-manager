# Assets Manager — 소프트웨어 설계 문서 (SDD)

앱 버전 0.16.4 · 기획서 v3.3(+14A~14L) 기준 · 2026-11-24

> 이 문서는 **구현의 지도**다. "왜 이렇게 정했나"는 기획서(`../../assets_manager_기획서_v3.3.md`)에 있고, 결정마다 `Qnn` 번호로 연결된다. 코드를 바꾸면 이 문서의 해당 절도 함께 고친다.

---

## 1. 목적과 범위

내 작업 표준(규칙·레퍼런스·산출물)을 **상속 그래프**로 관리하고, 노드를 외부 AI 앱(Codex, Claude 데스크톱, ChatGPT 웹)에 끌어다 놓으면 **결정적으로 조립된 프롬프트와 첨부 파일**이 넘어가는 로컬 데스크톱 앱. 확인된 산출물은 다시 쌓여 다음 작업의 참고 예시가 되고, MCP로 개인 비서 AI가 직접 검색·조립한다.

범위 밖: 앱 안의 LLM 호출(Q36), 협업·클라우드 동기화. (로컬 임베딩 뜻 검색은 v3.11에 선택 기능으로 들어옴, Q83-A)

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
| P7 | **패키지 동등성.** 세 대상 앱이 받는 프롬프트 본문은 같다. 차이는 경로 단락과 에이전트용 "더 찾아볼 산출물" 단락, 그리고 `## 이미지 생성` 단락의 두 판(ImageGen / 요청서, 대상의 `image_gen`)뿐 | Q20, Q61, Q82 | `render(paths=..., image_gen=...)` |
| P8 | **민감도는 조립 결과에서 계산.** 상속되는 속성이 아님. RAG 내보내기는 민감 노드 제외가 기본 | Q27 | `package`, `rag.chunks` |
| P9 | **기본값은 레이어 0.** `_defaults/`에 복사된 파일이 기준. 앱 업데이트는 알림 후 사용자가 적용 | Q16, Q17 | `ensure_defaults`, `store.defaults_updates` |

보충 (v3.8):
- **P3** — 콘솔 패널의 **AI 탭**은 사용자가 `+ AI`를 눌러 연 대화형 터미널이다. 앱은 입력을 보내지 않고 사용자 키 입력만 전달한다(자동 실행 없음).
- **P6·Q85** — AI 탭의 Claude Code는 `am`으로 노드를 바꾼다. Codex는 읽기 전용 샌드박스에서 실행하므로 `am` 쓰기가 차단된다. 로컬 AI 버튼의 Codex 결과는 표준 출력의 `plan.yaml`과 `inline_files`를 앱이 요청 작업장에 저장하고, 미리보기 후 적용한다. 구조 변경은 기존 정리 미리보기(P4)를 거친다.

보충 (v3.11, Q83-A):
- **P3** — 뜻 검색의 로컬 임베딩 모델(ONNX, CPU)은 AI API가 아니라 계산 라이브러리다. 앱이 인터넷에 닿는 것은 설정의 **"뜻 검색 켜기" 버튼**을 눌렀을 때 한 번(pip 설치 + 리비전·sha256 고정 모델 내려받기)뿐이다.
- **P2** — 뜻 검색이 켜져 있으면 `search`(→ 참고 예시)가 임베딩 점수를 쓴다. 스레드 1개·반올림·id 동점 정렬로 같은 모델·같은 폴더 상태에서는 같은 결과다. 켜짐/꺼짐은 이 PC의 상태(`.assets/embed/config.json`)이고, 꺼져 있으면 v3.10 검색과 글자 하나까지 같다.

보충 (v3.12, Q86~Q88):
- **Q86** — 한 계층에 붙은 두 부품 원칙의 목록 키(`value_type: list`)는 선언 순서대로 합치고 중복을 뺀다. 단일 키 값이 다르면 `RuleConflictError`로 조립을 중단하고 사용자에게 어느 값을 쓸지 묻는다. 상속 계층과 세트 자신의 덮어쓰기는 유지한다.
- **P3·Q88** — HTML 렌더링용 Playwright·Chromium은 설정 버튼에서만 설치한다. 렌더링은 로컬 HTML을 오프라인·JavaScript 비활성 브라우저에서 읽고, 기능이 꺼져 있으면 기존 정적 추출을 쓴다.

보충 (v3.10, Q82-A):
- **이미지의 기준은 원본 파일이다.** 이미지 키는 스타일을 글로 넘기지 않는다: 키 지시문·엣지 `note`·레퍼런스 `spec`·`check`·구성 요소·우선순위 문장을 프롬프트에 넣지 않고, 원본(+`similar_to`로 묶인 원본)만 ImageGen 참조로 넘긴다. 사용자가 세트에 쓴 텍스트 값만 "원본과 다르게 할 점"으로 들어간다. 원본이 없으면 글로 대신하지 않고 빼며 경고한다. 덜어내기도 이미지를 글·컨택트 시트로 바꾸지 않는다.

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
| `core/engine.py` | 로더, 검증, 상속 조립, 렌더, 패키지(한도·덜어내기·민감도), 변경 감지, 그래프·index. 구성 요소 종류 기본값 `PART_DEFAULTS`, 이미지 키·ImageGen 대상 기본값 `IMAGE_KEYS_DEFAULT`·`IMAGE_GEN_DEFAULT`(v3.10) | `Library`(`load`, `assemble`, `_attach_components`, `render`, `add_examples`, `package`, `changes`, `approve`, `graph`, `build_index`, `_ref_id`·`_ref_part`, `image_keys`·`image_gen`·`similar_images`), `image_lines`, `make_contact_sheet` | PyYAML, Pillow, `search`·`components`(지연 import) |
| `core/server.py` | HTTP API, 정적 파일, 포트 인계(`/api/shutdown`), 레이아웃 저장 | `Handler`, `library()`, `node_detail`, `handle_drop`(v3.1 호환), `bind` | 모든 core 모듈 |
| `core/drops.py` | 예상 결과 창: 놓은 것을 분류해 선택지 생성(stage), 실행(commit), 승격, 되돌리기 | `stage`, `commit`, `promote`, `undo`, `OPT` | `folder_requests`, `store` |
| `core/folder_requests.py` | AI 요청의 수명: 준비 → 패키지 → 결과 수신 → 미리보기 → 적용. 원본 짝 맞추기 | `new_request`, `prepare`, `package`, `render`, `receive_zip`, `attach`, `auto_assign`, `assign`, `preview`, `apply`, `_set_rule` | `extract`, `store` |
| `core/store.py` | 드롭 히스토리, 버전 보관, Git, 휴지통, 가져오기, 기본값 업데이트 | `append_history`, `read_history`, `backup_sync`, `fresh_files`, `versions_for`, `restore_version`, `git_commit`(라이브러리 데이터만, "기타" 제외), `_unstage`, `commit_message`, `delete_node`, `restore_trash`, `import_items`, `import_zip`, `defaults_updates`, `upgrade_defaults` | git(선택) |
| `core/authoring.py` | 파일 하나·여럿을 AI가 메타로 등록하게 하는 작성 프롬프트 (미등록·기존 노드 다시 쓰기) | `build`, `render` | `extract` |
| `core/components.py` | 구성 요소 분리(v3.9, Q81-A): 메타 `components` 정리(`normalize`), 잘라 낸 그림·구도 도식 캐시(`ensure`, `.assets/components/`), AI 요청(좌표 격자 사본 + 프롬프트) → 답 받기 → 미리보기(상자 수정 합치기) → 반영, 사람이 고친 요소 저장 | `normalize`, `ensure`, `crop_image`, `composition_image`, `grid_image`, `build`, `render_request`, `receive`, `preview`, `apply`, `save`, `open_request` | `folder_requests`, `store`, `tidy.parse_plan_text`, Pillow |
| `core/tidy.py` | 하위 정리: 범위(scope)·결정적 진단(diagnose) → AI 요청 프롬프트 → plan(ops) 메모리 시뮬레이션 → 전·후 그래프·변경 이유·프롬프트 비교 → 적용 | `scope`, `diagnose`, `build`, `receive`, `simulate`, `preview`, `apply` | `folder_requests`, `store` |
| `core/local_ai.py` | 로컬 AI 도구 찾기·설정·실행. Codex는 읽기 전용, stdout plan을 요청 작업장에 저장(Q85) | `detect`, `status`, `update_config`, `test`, `run_for`, `start`, `_save_stdout_plan`, `get_job`, `cancel` | `folder_requests`, `tidy`, `authoring`(지연 import) |
| `core/skills.py` | 스킬 연동: 도구 스킬 `assets-manager` 설치(시작 때), 세트 → 스킬 컴파일·내보내기(라이브러리 안/개인 폴더 × `.claude/skills`/`.agents/skills`)·고친 흔적 감지·제거, 설치된 스킬 읽기 → 그래프 노드 | `install_tool_skill`, `compile_set`, `status`, `export`, `remove`, `scan`, `graph_part` | `engine` |
| `skills/assets-manager/` | 앱 번들 도구 스킬(SKILL.md + references/ + 설치 때 scripts/extract.py) | | |
| `core/standards.py` | 작업 설명 → 맞는 세트 찾기(`find`, 결정적 점수: 제목·본문·규칙 값·붙은 스킬)·쓰기 없는 선택(`lookup`)·조립(`standard`, 맞는 것이 없으면 `global`) | `find`, `lookup`, `standard` | `engine`, `search.grams` |
| `core/cli.py` | 명령줄 입구: `standard "<작업>" [--set] [--no-gate] [--json] [--lookup]`, `sets`, `assemble <id>`. `--lookup`은 선택된 표준과 후보만 반환. 그 밖의 명령은 `console.run`으로(`AM_ACTOR` 환경 변수 = 기록의 actor, 기본 `cli`) | `main` | `standards`, `console` |
| `core/console.py` | `am` 노드 명령(v3.8): 보기(ls/show/find/standard), 바꾸기(inherit/use/unuse/set/unset/rename/new), undo, tidy 요청. 콘솔 탭·명령줄·AI 탭이 같은 명령을 쓴다 | `run`, `run_line`, `undo`, `HELP` | `engine`, `folder_requests`, `store`, `standards`, `tidy` |
| `core/terminal.py` | AI 탭 터미널 세션(v3.8): Windows ConPTY(pywinpty)·POSIX pty, 출력 고리 버퍼, 긴 폴링 읽기, 크기 조절, 종료. `am` 실행 파일(`.assets/bin`), 라이브러리 `.claude/settings.json` 권한 합치기 | `start`, `Session`(`read`, `write`, `resize`, `close`), `close_all`, `write_shims`, `ensure_permissions` | `local_ai`(도구 경로), pywinpty(Windows) |
| `core/export_cmd.py` | AI 대화창에 붙여넣는 내보내기 명령 (노드별: 요청 생성 / 일반: `@target`) | `build` | `folder_requests` |
| `core/extract.py` | 기본 정적 값 추출(이미지·글·HTML/CSS·PDF), 렌더링을 켜면 HTML 계산 스타일·애니메이션 추가 | `extract`, `kind_of` | Pillow, (선택) PyMuPDF·`render_web` |
| `core/render_web.py` | 사용자 설정 버튼에서만 Playwright·Chromium 설치, 오프라인 HTML 렌더링 값 추출(Q88) | `status`, `install`, `set_enabled`, `extract` | playwright(선택) |
| `core/search.py` | 결정적 키워드 검색 (단어 + 3글자 조각, 필드 가중치) + 뜻 검색이 켜져 있으면 임베딩 점수와 섞기(v3.11) | `search`, `grams`, `fields`, `_semantic`, `_blend` | `embed`(지연 import, 실패하면 키워드만) |
| `core/embed.py` | 로컬 임베딩(v3.11, Q83-A): multilingual-e5-small q8 ONNX(리비전·sha256 고정), 조각 벡터 캐시, 켜기 작업(pip → 내려받기·검증 → 켜기 → 색인) | `status`, `active`, `set_enabled`, `install`, `index`, `scores`, `encode`, `set_encoder`(테스트용) | `rag.node_parts`, onnxruntime·tokenizers·numpy(선택) |
| `core/rag.py` | `노드id#부분` 조각 JSONL 내보내기. 노드 → 조각은 `node_parts`(임베딩과 공용) | `node_parts`, `chunks`, `export_jsonl` | `engine` |
| `core/mcp_server.py` | MCP 서버 (stdio JSON-RPC 2.0, 외부 패키지 없음) | `handle`, `call`, `TOOLS` | `engine`, `search` |
| `ui/app.js` | 화면 전체 (빌드 단계 없음), 역할별 원칙 나누기 버튼·렌더링 설정(Q75·Q88) | `loadGraph`, `select`, `render*`, `refreshPackage`, `handleDrop`, `openOutcome`, `confirmOutcome`, `copyExport` | Cytoscape·xterm.js 5.5 + addon-fit(번들, `ui/vendor/`), Tauri 전역(`window.__TAURI__`) |
| `ui/compare.html` · `compare.js` | 두 프롬프트 버전의 결과와 골든 샘플을 출처를 가리고 비교(Q87) | `candidate`, `shuffle` | 브라우저 FileReader·Web Crypto |
| `src-tauri/src/main.rs` | 셸, `app/` 폴더 찾기, 사이드카 실행(`py` → `python` 순서), 종료 처리 | `find_app_dir`, `spawn_server` | tauri 2, tauri-plugin-drag 2 |
| `defaults/` | 앱 번들 기본값 (`keys.yaml`, `targets.yaml`, `defaults.md`) — 라이브러리에 없으면 복사 | | |
| `tests/` | 단위·통합 테스트 114개 | `test_engine`, `test_v3`, `test_requests`, `test_v33`, `test_local_ai`, `test_skills`, `test_standards`, `test_console`, `test_components`, `test_v310`, `test_embed`, `test_render_web` | |

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

`exists: false`인 레퍼런스·산출물 = **원본 없음**(경고). 조립 때 spec이 있으면 글로 대체(`status: text_only`). 단 이미지 키(v3.10)는 글로 대체하지 않고 빼며 경고한다.

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

### 5.4 키 어휘 (`_defaults/keys.yaml`, version 7, 35개)

키마다 `label`, `domain`, `priority`(required/recommended/optional), `aliases`(동의어 경고), `instruction`, `ref_instruction`, `check`, `precedence`. 사용자 정의 키 허용(동의어면 경고). `domain: dev` 키 15개(설계 승인·문서 역할·충돌 처리 등)는 개발 착수 원칙 프리셋용. `value_type: list`인 키(`forbidden`, `open_questions`)는 여러 부품 원칙의 값을 합치고 중복을 뺀다. 나머지는 단일 키이며 같은 계층의 부품 원칙끼리 값이 다르면 조립을 멈춘다(Q86). 코드 안의 특수 키: `_example_<n>`(참고 예시, `priority: example` = 덜어내기 1순위).
**이미지 키 (v7, Q82-A):** `image: true`인 키(`chart_style`, `character_style`, `composition`, `shape_style`, `image_style`)는 `ref_instruction`·`check`를 두지 않는다. 차트·도식도 원본 이미지를 ImageGen 참조로 넘긴다. `precedence`는 첨부 순서에만 쓰고 문장은 만들지 않는다. 라이브러리 `keys.yaml`에 `image: true`가 하나도 없으면(v4 이하) `IMAGE_KEYS_DEFAULT`를 쓴다. `golden_sample`은 이미지 키가 아니다.
**드롭 대상 (`targets.yaml` version 3):** `image_gen`(ImageGen으로 직접 그리는지; 기본값은 `chatgpt_web`만 true). 항목이 없으면(v2) `IMAGE_GEN_DEFAULT`로 판단.
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
2. 그 순서로 규칙을 조립한다. 같은 계층에 붙은 부품 원칙끼리 `value_type: list` 키가 겹치면 쉼표 구분 문자열 또는 YAML 목록의 항목을 순서대로 합치고 중복 제거한다. 단일 키 값이 다르면 `RuleConflictError`로 중단한다. 그 밖의 상속 계층·세트 규칙은 뒤가 우선한다. 키마다 `source`, `overridden` 기록. `kind: defaults`에서 온 값은 `from_defaults`.
3. 레퍼런스 값은 `ref_*` 필드와 `status`(ok / text_only / empty / missing)를 채운다. v3.9 `_attach_components`: 원본에 요소가 있으면 `e.components`(요소별 `abs`, `spec`, `check`, `status`, `pkey`=짝 키, 구도 `legend`)를 붙이고 `components.ensure`로 그림 캐시를 맞춘다. 값이 `ref:<id>#<요소>`면 항목 자체가 그 요소(`ref_file`=잘라 낸 그림, `spec`·`check`=요소 것, `part_kind`, `legend`), 없는 요소면 `missing` + 검증 오류.
4. 우선순위: `keys.yaml`의 `precedence` 기본값 → 상속 순서대로 세트의 `precedence`가 같은 키 쌍을 덮어씀. 둘 다 있는 쌍만 활성. 레퍼런스 첨부 순서는 "우선하는 쪽 먼저".
5. 텍스트 항목은 키 어휘 순서, 레퍼런스 항목은 우선순위 순서로 정렬.
6. 이미지 키(v3.10, Q82-A): 항목에 `image: true`, `check: []`. 레퍼런스면 구성 요소를 붙이지 않고(`ref:<id>#<요소>`도 원본으로) `similar`에 `similar_images(ref)` — 어느 쪽에서 걸었든 `similar_to`로 묶인 원본 있는 이미지 레퍼런스, id 순. 비슷한 원본 중 내부 자료가 있으면 항목 민감도도 internal.

### 6.2 렌더 (`Library.render`) — 프롬프트 단락 순서
`# 작업 지침` → (`## 이번 작업`) → `## 규칙` → (`## 우선순위`: 키 쌍 문장 + 참고 예시 문장) → `## 첨부 레퍼런스` → `## 출력 전 자가 점검`(키 check + 레퍼런스 check, 중복 제거) → (경로 모드: `## 파일 경로` + 에이전트용 `## 더 찾아볼 수 있는 산출물`).
`mode[key]`: `attach` | `text`(spec으로 대체) | `sheet`(컨택트 시트 칸) | `drop`(제외). 이미지 키는 `attach` 외에는 모두 제외, `<키>~similar`로 비슷한 원본만 뺄 수 있다.
이미지 생성 단락 (v3.10, Q82-A, `image_lines`): 삽화·차트·도식의 이미지 키는 `## 규칙`에 넣지 않는다. 원본 → 비슷한 원본 순으로 첨부 번호를 이어 받고(같은 레퍼런스는 한 번만), `## 첨부 레퍼런스` 바로 뒤에 `## 이미지 생성`을 넣는다. `image_gen=True`(ImageGen판, ChatGPT 웹): 첨부를 참조 이미지로 직접 넣고 ImageGen으로 만들도록 지시한다. 첨부마다 `[n] 파일 — 역할(키 라벨, 비슷한 원본이면 '· 비슷한 원본')`을 적고, 글은 삽화의 장면 또는 차트·도식의 데이터·축·라벨·관계와 사용자 규칙 값만 넣는다. `False`(요청서판): SVG·코드로 직접 그리지 않고 자리마다 고정 형식 ImageGen 요청서(참조 첨부 목록·장면 또는 내용·다르게 할 점·고정 지시)를 결과물 끝에 쓰게 한다. 자가 점검의 `IMAGE_CHECK`는 삽화·차트·도식을 모두 확인한다. 이미지 키가 없으면 단락과 점검을 넣지 않는다.
함께 쓰는 스킬 (v3.7): `## 첨부 레퍼런스` 뒤, 자가 점검 앞에 `## 함께 쓰는 스킬` — 스킬마다 제목·설명·위치만(본문은 넣지 않고 "해당되면 읽고 따르라, 위 규칙이 우선"). 경로 모드는 SKILL.md 절대 경로, 채팅 대상은 첨부(패키지가 `<이름>.SKILL.md`로 복사해 이름이 겹치지 않게). 자가 점검에 "해당 스킬을 따랐는가" 한 줄.
구성 요소 (v3.9, Q81-A, `emit_components`, **v3.10부터 이미지가 아닌 키에서만**): 원본 항목 바로 아래에 들여 쓴 줄로 요소마다 `parts[part].ref_instruction` + (구도) `(도식 번호: 1 … · 2 …)` + `설명: spec`. 원본이 첨부면 번호 `[n-a]` `[n-b]`…, 원본이 시트면 새 정수 번호, 원본이 글(text)이면 요소도 글로(spec 없으면 뺌). 첨부 목록에 `[n-a] <id>.<요소>.png — 원본 제목 · 요소 제목`. 자가 점검에 `parts[part].check`(번호 채움) + 요소 `check`. 짝 키(`pkey`)가 조립 결과에 있고 원본의 키와 다르면 `## 우선순위`에 "{요소}에 관해서는 **{키}** 규칙을 분리한 요소(첨부 [n-a])보다 우선합니다. 규칙에 없는 부분은 요소를 따르세요." 요소가 없으면 v3.8과 글자 하나까지 같다.
진행 방식 단락 (v3.4, Q73-A, `process_lines`): 게이트가 켜져 있으면 `## 이번 작업` 바로 뒤(규칙 앞)에 `## 진행 방식`을 넣는다 — 이해한 작업·결과물 구성·이미지 계획·확인할 것만 짧게 답하고 승인을 기다림(규칙 요약 금지), 승인 후에는 계획에 없는 것까지 규칙 전체·레퍼런스·자가 점검을 적용, 승인 전 금지는 결과물(본문·이미지·코드·파일) 만들기와 기존 파일 수정뿐(레퍼런스 열어 보기는 허용). 작업이 없으면 같은 단락 앞에 '레퍼런스를 살펴 두고 한 줄로 답한 뒤 기다리기'를 붙인다. 자가 점검 끝에 `GATE_CHECK` 한 줄을 더해 **프롬프트가 품질 기준으로 끝나게** 한다. 게이트를 끄면 아무것도 붙지 않아 v3.3 프롬프트와 글자 하나까지 같다. `gate`는 조립 순서에서 마지막으로 정한 노드가 이기고, 기본은 켜짐. 세 대상 앱에 같은 문장(P7). `canonical`에는 넣지 않는다.

### 6.3 패키지와 자동 덜어내기 (`Library.package`)
- `add_examples(task)`: 같은 세트의 산출물을 `search`로 상위 3개(골든 샘플 제외) → `_example_n` 항목.
- 위반 검사: 파일 수(prompt.md 포함), 파일 크기, 글자 수.
- `trim=True`: 0) 크기 초과 파일 → 텍스트/제외(요소면 그 요소만, 이미지 키·비슷한 원본은 제외만) → 0-1) 구성 요소 첨부 → spec 텍스트(없으면 제외, 원본보다 먼저, v3.9) → 1) 첨부 → spec 텍스트 (example → optional → recommended → required, 이미지 키는 건너뜀) → 1-1) 이미지 키의 비슷한 원본 제외(v3.10) → 2) 이미지 키가 아닌 이미지 2장 이상이면 컨택트 시트 → 3) 키 제외 (required 제외).
- `image_gen = Library.image_gen(target)`을 두 렌더(본 프롬프트·클립보드)에 넘기고 결과에 `image_gen`을 돌려준다. 원본 없는 이미지 레퍼런스(`text_only`)는 "원본 없음 — 이미지 생성에서 빠졌습니다" 경고.
- 민감 자료: 대상의 `allow_internal`이 false면 경고, `strip_sensitive`면 텍스트 대체(이미지 키는 제외). 스킬 컴파일(`skills.compile_set`)도 내부 이미지는 글로 바꾸지 않고 뺀다.
- 출력: `.assets/drops/<세트>/<대상>/prompt.md`(+ contact-sheet.png). 요소 그림은 `.assets/components/<id>/`의 캐시 경로를 그대로 넘긴다. `entries[].components[].mode`로 요소별 결과를 돌려준다. `clipboard`는 항상 경로 포함판.

### 6.4 검색 (`search.search`)
질의와 문서(title, keywords, task, tags, summary, spec, 텍스트 본문 6000자)를 단어(`w:`) + 3글자 조각(`g:`, 짧은 단어는 2글자)으로 쪼갠다. 점수 = Σ 필드 가중치 × BM25식 idf × (1 + log tf) × (단어 일치면 2배). 동점은 id 순 → 결정적.

뜻 검색 (v3.11, Q83-A, `embed.active(root)`일 때만):
- 후보 노드마다 `rag.node_parts` 조각을 `"passage: " + 글`로, 질의는 `"query: " + 질의`로 임베딩(평균 풀링 + L2 정규화, 최대 512토큰, 묶음 16, 스레드 1, 벡터 소수 6자리). 노드의 뜻 점수 = 조각 코사인 최댓값(4자리).
- `_blend`: 최종 `score` = `W_KW`(0.5) × 키워드/그 질의의 최고 키워드 점수 + `W_SEM`(0.5) × clamp((코사인 − `SEM_FLOOR` 0.80) / (`SEM_CEIL` 0.90 − 0.80)), 4자리. 키워드 0점인 노드는 코사인 ≥ `SEM_ONLY_MIN`(0.85)일 때만 남긴다. 결과에 `kw`(원래 키워드 점수)·`sem`(코사인)이 붙는다. 동점은 id 순.
- 벡터 캐시: 키 = sha1(모델 키 + 조각 글) 앞 16자 → `.assets/embed/vectors.json`(`{model, vecs}`, 모델 키가 다르면 버림). 검색 때 없는 조각만 계산해 더한다. `index`(켜기·다시 색인)는 전체를 계산하고 안 쓰는 벡터를 지운다.
- 인코더 오류는 stderr에 남기고 키워드 결과만 돌려준다.
- 켜기 `install(root, get_lib)`: 스레드에서 (패키지가 없으면) `python -m pip install onnxruntime tokenizers` → 모델 파일을 `huggingface.co/<REPO>/resolve/<REV>/…`에서 `.part`로 받으며 sha256 확인 → `verified` 표시 → `enabled: true` → `index`. 이미 있는 파일도 sha256이 맞아야 건너뛴다. 상태는 메모리 `_JOBS`(state running/done/failed, step, 받은 바이트, 조각 수).

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
- 역할별로 나누기(Q75): 세트 상세 버튼은 `goal=roles`로 같은 정리 요청을 만든다. AI에게 세트의 규칙을 역할별 부품 원칙으로 옮길 `create_rule`·`move_rule`·`set_uses` 계획을 요구한다. 기존 전·후 그래프와 프롬프트 비교를 그대로 사용한다.

### 6.8 로컬 AI 실행 (`local_ai`, Q76-B)
- 도구 정의 `TOOLS`: `claude`(Claude Code: `-p {instruction} --permission-mode acceptEdits --allowedTools Read,Write,Edit,Glob,Grep,Bash(python:*)… --max-turns 60`), `codex`(Codex CLI: `exec --sandbox read-only --skip-git-repo-check {instruction}`). Claude 인자는 설정에서 바꿀 수 있다. Codex 인자는 읽기 전용으로 고정한다. Windows의 `.cmd`는 `cmd /c`로 감싼다.
- 지시(`instruction_for`)는 한 줄: "파일 `<prompt 상대 경로>` 를 읽고 그대로 수행, 지시된 위치에만 쓰기, 질문하지 말고 끝까지". 따옴표·`&|<>%^` 없음(cmd 안전). 작업 폴더는 라이브러리 루트(루트 AGENTS.md·CLAUDE.md의 쓰기 규칙이 함께 읽힘).
- `run_for`: tidy → 에이전트용 `tidy.render`를 `inbox/<rid>/prompt-local.md`에, 기대 결과 `inbox/<rid>/out/plan.yaml` / request → `folder_requests.package(에이전트 대상)`의 `package/prompt.md`, 기대 결과 같은 plan / authoring → Claude는 `authoring.build(에이전트 대상)`의 메타를 원본 옆에 쓰고, Codex는 새 요청의 `plan.yaml`을 표준 출력으로 돌려 미리보기로 보낸다.
- `start`: 스레드에서 `Popen`, 출력은 `.assets/local-ai/jobs/<job>.log`, 상태는 `<job>.json`(running → done | failed | cancelled). 자식 프로세스에는 `PYTHONIOENCODING=utf-8`·`PYTHONUTF8=1`을 주고, 출력은 UTF-8로 읽되 아니면 이 PC 기본 인코딩(Windows 한국어 cp949)으로 읽는다(`_decode`). Codex는 최종 출력의 YAML 코드 블록에서 같은 요청 id의 plan을 찾고, `inline_files`는 요청 `out/` 안의 `.md`·`.meta.yaml`에만 저장한다. 20분 제한. 종료 코드 0이어도 기대 결과가 없으면 failed. 취소는 Windows `taskkill /T /F`.
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
- **쓰기 없는 조회** `cli.py standard "<작업>" --lookup [--json]`: `Library(read_only=True)`가 없는 기본값 파일은 번들에서 읽고 디스크에 복사하지 않는다. `standards.lookup`은 선택된 세트·후보·전역 원칙 대체 여부만 반환한다. `package`를 호출하지 않아 `.assets/drops/`의 기존 파일을 지우거나 새 첨부 경로를 만들지 않는다. 실제 프롬프트와 첨부가 필요하면 기존 `standard`를 별도로 호출한다(ISSUE-050).
- **AI가 부르게 하기** `skills.caller_apply`: 도구 스킬(경로를 채운 SKILL.md)을 개인 `~/.claude/skills`·`~/.agents/skills`에 설치, 또는 `~/.claude/CLAUDE.md`·`~/.codex/AGENTS.md`에 `<!-- assets-manager:start -->…end` 블록 한 줄. 블록 밖은 건드리지 않는다.

### 6.11 콘솔과 AI 탭 (v3.8, Q79-A·Q80-A)
- **`am` 명령** `console.run(root, argv, actor)` → `{code, output, changed}`. 쓰기마다: `store.backup_sync(checked_at="콘솔 명령 전 확인")`(그때까지의 변경을 이름 없이 기록) → 바뀔 파일의 이전 내용을 `.assets/console/undo.jsonl`에 한 줄로(새 파일은 `null`) → `folder_requests._rewrite_frontmatter`/`_set_rule`로 키 단위 쓰기(본문·키 순서 유지) → `history/drops.jsonl`에 `{kind: console, actor, line, changed}` → `backup_sync("콘솔: <명령>")`(이 명령의 변경과 기록 한 줄만, 0.16.3). `inherit`는 쓴 뒤 `sources`로 순환을 확인하고 순환이면 곧바로 되돌린다. `use`는 원칙·스킬만 받는다(세트는 `inherit`). `new set`은 `global`이 있으면 기본으로 상속. `undo`는 스택 맨 위를 되돌린다(만든 파일은 지움, 그 전에 보관).
- **입구 세 곳이 같은 명령**: 콘솔 탭(`POST /api/console`, actor `user`), 명령줄(`cli.py <명령>`·`.assets/bin/am`, actor `cli`), AI 탭(PATH 앞에 `.assets/bin`, `AM_ACTOR=ai-tab`).
- **AI 탭** `terminal.start(root, tool)`: `local_ai.status`의 경로로 Claude Code·Codex를 pty에서 띄운다(Windows `.cmd`는 `cmd /c`). 작업 폴더 = 라이브러리 루트, `TERM=xterm-256color`. 출력은 UTF-8 증분 디코딩 후 세션 버퍼(최대 40만 자, 넘치면 앞을 버리고 `base` 오프셋을 올림). `read(since, wait)`는 새 출력이 올 때까지 최대 `wait`초 기다리는 긴 폴링. 동시에 `MAX_AI = 1`개. 입력이 `IDLE_HINT`(30분) 없으면 UI가 닫기를 묻는다. 서버 종료(`/api/shutdown`, atexit, Ctrl+C) 때 모두 종료.
- **권한** `terminal.ensure_permissions`: 서버 시작·AI 탭 시작 때 `.claude/settings.json`의 `permissions.allow`에 `Bash(am)` `Bash(am *)` `Bash(python|py app/core/cli.py *)`, `deny`에 `Edit(./sets/**)` `Edit(./principles/**)` `Edit(./skills/**)` `Edit(./history/**)` `Edit(./index.md)` `Edit(./graph.json)`을 없는 것만 더한다. 사용자 항목은 그대로, JSON이 깨져 있으면 손대지 않는다. (`inbox/<rid>/out/` 아래 쓰기는 막지 않는다 — 기존 요청 흐름.)
- **변경 감지·캐시** `engine.library_signature(root)`: 로더가 읽는 파일의 (상대 경로, 크기, 수정 시각) sha1 앞 16자. 앱이 쓰는 `index.md` `graph.json` `layout.json`과 숨김·제외 폴더는 뺀다. `server.library()`는 서명이 같으면 캐시한 `Library`를 돌려주고, `sync=True`면 항상 다시 읽는다. 그래프 노드마다 `sig`(노드 파일 크기·시각)가 붙어 UI가 바뀐 노드를 깜빡인다.

### 6.12 구성 요소 분리 (`components`, v3.9, Q81-A)
- v3.10(Q82-A): 분리한 요소는 이미지 키(차트·도식·캐릭터·구도·도형·화풍)에서는 프롬프트에 넣지 않는다. 이미지가 아닌 키(`palette` 등)에서만 원본과 함께 첨부된다. 분리·저장·미리보기 기능은 그대로.
- `normalize(raw, parts, size)`: 저장 형식으로 정리하고 문제를 돌려준다. 알 수 없는 `part`·잘못된 이름(영문 소문자-하이픈)·box 없는 캐릭터/도형은 빼고 경고, 이름이 겹치면 `-2`를 붙인다. box가 이미지 밖이면 경고만(자를 때 맞춤). 로더가 부르고, 문제는 노드 이슈(warning).
- 그리기(결정적): `crop_image`(box 자르기 + 흰 여백 4%, 최소 8px), `composition_image`(원본을 흰색과 62% 섞고 `regions` 번호 상자·`flow` 화살표, regions가 없으면 캐릭터·도형 box), `grid_image`(AI용: 20칸 이하가 되는 간격의 선 + 가장자리 좌표 숫자). `ensure`: 원본 크기·수정 시각·요소 목록의 sha1을 `stamp.txt`와 비교해 다를 때만 다시 그린다. 실패하면 False → 조립은 spec 글로 대체.
- 요청: `build` → `request.json`(`kind: components`, status sent) + `src/<id>.grid.png` + `prompt.md`(`render_request`: 요소별 고르는 법, 좌표 규칙, 다시 그릴 수 있는 spec 쓰는 법, 추출 색, 지금 있는 요소, `plan.yaml` 형식; 에이전트는 절대 경로를 받고 `out/plan.yaml`에 직접 씀). 로컬 AI `run_for(kind: components)`는 에이전트용 프롬프트를 `prompt-local.md`에 쓴다.
- 받기: 붙여넣기 `receive`(tidy와 같은 파서) · 에이전트의 `out/plan.yaml` · zip(`drops.stage`가 `case: components`).
- `preview(rid, edits)`: 요소별 수정(`{name: {box?, regions?, title?, spec?}}`)을 `request.json.edits`에 합쳐 두고 plan에 덮은 뒤 `inbox/<rid>/preview/`에 그려 돌려준다. 항목 `status`: new(기본 체크) / replace(같은 이름의 기존 요소, 기본 해제, P6) / same.
- `apply(rid, selected)`: 기존 순서 유지하며 같은 이름은 바꾸고 새 것은 뒤에 붙임 → `backup_sync` → 메타 다시 쓰기(다른 필드 보존, 짧은 목록은 한 줄 표기) → 요청 applied → 히스토리 `kind: components`.
- `save(nid, comps)`: 상세 화면의 상자 고치기·빼기. 사람이 한 변경이라 바로 반영(P4), 쓰기 전 보관(P5), 형식 오류는 거부.

### 6.13 블라인드 비교와 HTML 렌더링 (Q87·Q88)
- `ui/compare.html`·`compare.js`: 사용자가 같은 세트·작업의 두 프롬프트 버전 결과(텍스트 또는 이미지)와 골든 샘플을 넣는다. 화면에서 세 결과의 출처를 무작위 순서로 가리고 선택 후 공개한다. 결과는 JSON으로 내려받는다. 앱은 AI를 자동 호출하거나 결과를 서버에 전송하지 않는다.
- `render_web.status/install/set_enabled`: Playwright·Chromium은 설정 버튼에서만 내려받는다. 켜져 있으면 `extract.extract`가 정적 HTML 값에 `rendered`(1280×800 오프라인 브라우저의 계산 스타일·애니메이션)를 더한다. 브라우저가 없거나 꺼져 있으면 정적 값만 돌려준다(꺼져 있으면 준비 확인도 하지 않는다). 오류는 `render_error`로 돌려 등록 자체는 계속한다.
- 준비 확인(0.16.1) `_browser_ready`: 추출과 같은 방식(`launch(headless=True)`)으로 실제로 띄워 본다. Playwright 패키지가 업데이트되면 기대하는 Chromium 리비전이 바뀌어 예전에 받은 브라우저로는 실행되지 않기 때문이다(파일 존재만 보면 놓침). 성공한 Playwright 버전만 프로세스 안에서 기억하고, 실패는 매번 다시 확인한다. `status`는 준비가 안 됐을 때 `reason`(버전 불일치면 "Playwright <버전>에 맞는 Chromium이 없습니다. 다시 내려받으세요.")을 돌려준다.

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
| `.assets/embed/config.json` · `vectors.json` | 뜻 검색 켜짐 여부(기기별) · 조각 벡터 캐시 (지우면 다시 계산) (v3.11) | 아니오 | 아니오 |
| `.assets/render/config.json` | 선택형 HTML 렌더링 켜짐 여부(기기별). Chromium은 Playwright 사용자 캐시에 설치 | 아니오 | 아니오 |
| `%LOCALAPPDATA%/assets-manager/models/<모델 키>/` (`AM_MODEL_DIR`로 바꿀 수 있음) | 라이브러리 **밖**: 임베딩 모델 파일 + `verified`. 여러 라이브러리가 함께 씀 | 아니오 | 아니오 |
| `.assets/bin/am` `am.cmd` | `am` 실행 파일 (서버 시작 때 이 PC의 Python·경로로 다시 씀) | 아니오 | 아니오 |
| `.claude/settings.json` | 라이브러리에서 뜨는 Claude Code 권한: `am` 허용, 규칙 파일 직접 편집 금지 (앱은 빠진 항목만 더함) | 예 | 아니오 |
| `index.md` `graph.json` | 자동 생성 | **아니오** | 아니오 |
| `app/` | 앱 코드. **자체 Git 저장소 `app/.git`** (라이브러리 기록과 분리: 루트 `.gitignore`의 `app/`은 `store.GITIGNORE_LINES`가 유지). `app/.gitignore`: `node_modules/` `src-tauri/target/` `src-tauri/gen/` `__pycache__/` `*.log` | 루트: 아니오 / app: 예 | 아니오 |
| `app/docs/archive/` | 옛 기획서 v1~v3.2 (결정 Qnn의 배경). 현재 기준은 루트의 `assets_manager_기획서_v3.3.md` | app | 아니오 |

로더 제외 목록: `engine.SKIP_DIRS`(`app`, `history`, `inbox`, 숨김 폴더). Git 제외 목록: `store.GITIGNORE_LINES`(없는 줄만 자동 추가).

라이브러리 자동 커밋 메시지 (0.14.1, `store.commit_message`): `git add -A` 뒤 스테이징된 변경 목록(`_staged`, `status --porcelain=v1 -z`의 인덱스 열)으로 만든다.
- 커밋 대상 (0.16.3, ISSUE-032·033): 분류가 "기타"인 파일(기획서·`AGENTS.md`·`start.bat` 같은 사람이 쓰는 루트 문서)은 `_unstage`로 스테이징에서 빼고(파일은 그대로) `git_commit` 결과의 `skipped`로 돌려준다. 사람이 직접 커밋한다. 앱이 관리하는 루트 파일 `.gitignore`·`.claude/settings.json`은 "설정" 분류로 커밋한다.
- 제목: `[앱 동작 문구 — ]분류 동작 · …: 이름1, 이름2, 이름3 외 N개`. 분류는 앞 폴더 기준 `COMMIT_AREAS`(규칙·레퍼런스·산출물·스킬·기본값·드롭 기록·기록·노드 배치, 나머지는 기타), 동작은 추가·수정·삭제·이름 변경. 이름은 노드 id에 가깝게(확장자·`.meta.yaml` 제거, 스킬은 폴더 이름). `layout.json`은 "노드 배치 저장", `history/drops.jsonl`은 "드롭 기록"으로 이름 없이 쓴다.
- 본문: 파일마다 `동작 경로` 한 줄(최대 50줄) + `출처: 폴더 변경 감지[ (확인 시점)] | 앱 동작`.
- 앱 동작 문구(`backup_sync(lib, message)`: "놓기: …", "콘솔: …", "하위 정리 …" 등)는 제목 앞에 붙는다. **동작이 끝난 뒤** 그 동작이 만든 변경을 기록할 때만 준다. 동작 전·앱 시작 때 그때까지의 변경을 쓸어 담는 호출은 `message` 없이 `checked_at`("앱 시작 때 확인", "콘솔 명령 전 확인", "콘솔 되돌리기 전 확인")만 주어, 사람이 만든 변경에 앱 동작 이름이 붙지 않는다(0.16.3). 0.16.2까지는 "Assets Manager 시작", "콘솔 전: …"이 동작 전 쓸어 담기에 붙었다. 0.14.0까지의 커밋은 "자동 기록: 변경 N개 (폴더 변경 감지)"였고, 지난 이력은 고치지 않는다.

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
| GET `/api/tidy?node&target[&goal=skill:<id>\|roles]` · `/api/tidy/open?node` | 하위 정리 또는 역할별 분리 요청 만들기(진단·프롬프트·현재 그래프) · 이 노드의 열린 정리 요청 |
| POST `/api/tidy/<rid>/receive` {text} · `/preview` {selected} · `/apply` {selected} | AI 답 붙여넣기 → 미리보기 → 반영 |
| GET `/api/components/request?node&target` · `/api/components/open?node` | 구성 요소 분리 요청 만들기(프롬프트·파일 3개·격자 사본·드래그 아이콘) · 이 노드의 열린 요청(답이 있으면 미리보기 포함) (v3.9) |
| POST `/api/components/<rid>/receive` {text} · `/preview` {edits} · `/apply` {selected} | AI 답 붙여넣기 → 미리보기(상자 수정 합치기) → 반영 |
| POST `/api/components/node/<id>` {components} | 반영된 요소 목록 저장(상자 고치기·빼기) |
| POST `/api/request/<rid>/target` {node} | 결과를 둘 노드 지정(빈 공간에 놓은 경우) → 미리보기 |
| GET `/api/embed` | 뜻 검색 상태 `{enabled, deps, model, active, model_name, model_dir, download_mb, vectors, job}` (v3.11) |
| POST `/api/embed/install` · `/api/embed/config` {enabled} | 켜기(내려받기·색인, 백그라운드, 다시 누르면 다시 색인) · 끄기/켜기만 |
| GET `/api/render-web` · POST `/api/render-web/install` · `/api/render-web/config` {enabled} | 선택형 Chromium 상태·설정 버튼 설치·켜기/끄기 |
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
| `search` | query, kind?, set?, limit? | `[{id, score, title, kind, type, set}]` (뜻 검색이 켜져 있으면 `kw`·`sem`도) |
| `get` | id | 노드 메타 + `final_rules`(규칙 노드) + `text`(텍스트 원본 일부) + `abs_file` |
| `related` | id | 그 노드에 닿는 엣지 목록 |
| `assemble` | id, task?, target? | `{prompt(경로 포함), files, warnings, violations}` |

프로토콜 `2024-11-05`, 줄 단위 JSON. 알림(`notifications/*`)에는 응답하지 않는다. 오류는 `isError: true` 결과로.

## 10. 외부 AI와 주고받는 형식

| 방향 | 형식 | 만드는 곳 |
|---|---|---|
| 앱 → AI (작업) | `prompt.md` + 레퍼런스 원본 (+ 경로 텍스트). 삽화·차트·도식은 ChatGPT 웹에 ImageGen 지시, 다른 대상에 ImageGen 요청서 | `engine.package` |
| 앱 → AI (파일 등록) | 작성 프롬프트 (+ `extract.py`, 원본) | `authoring.build` |
| 앱 → AI (폴더 요청) | 가이드 프롬프트 + `plan.yaml` 규격 (+ 시트·묶음) | `folder_requests.package` |
| 앱 → AI (구성 요소 분리) | 분리 요청 프롬프트 + 원본 + 좌표 격자 사본 → `plan.yaml`의 `components` | `components.build` |
| 사용자 → AI (내보내기) | 내보내기 명령 (붙여넣기) | `export_cmd.build` |
| AI → 앱 | `assets-import.zip`: `plan.yaml`, `<폴더>/<이름>.meta.yaml`, 원본, 규칙 md / 또는 에이전트가 `inbox/<rid>/out/`에 직접 | `receive_zip`, `store.import_zip` |

구성 요소 답 `plan.yaml`: `request`, `node`, `summary`, `components[{name, part, title, box, regions?, flow?, spec, check}]` (형식: `skills/assets-manager/references/components-format.md`).
`plan.yaml`: `request?`, `summary`, `create[{path, reason}]`, `update[{node | "@target", set_rules{}, reason}]`, `relations[{from, type, to, reason}]`, `missing[]`.
Codex 읽기 전용 실행(Q85)은 최종 표준 출력의 YAML 코드 블록에 같은 요청 id의 `plan.yaml`을 돌려준다. 새 파일은 `inline_files: {상대 경로: 전체 텍스트}`에 담고 앱이 `inbox/<rid>/out/`에 저장한다. 저장 경로는 `.md`·`.meta.yaml`로 제한하고 미리보기 후 적용한다.

## 11. UI 구조 (`ui/app.js`)

- 설정 창(`#settings-dialog`, 상단 `설정` 버튼·초록 점 = 연결됨): `openSettings({reason, then})` — 도구 카드(찾음/버전/기본 선택/사용/연결 시험/설치 명령 복사/고급: 직접 경로·실행 인자). `reason`이 있으면 위에 '이 기능은 AI가 필요합니다' 안내와 단계, 연결되면 `계속하기`로 원래 작업을 이어 실행. 진행 표시 `#job-bar`(`runLocal`·`pollJob`·`renderJobs`: 경과 시간, 로그 보기, 취소, 실패 사유).
- 설정 창의 뜻 검색 칸(v3.11, `loadEmbedBox`, `#embed-box`): 켜짐/꺼짐, 모델·패키지·색인 조각 수, `내려받고 켜기`/`켜기`·`끄기`·`다시 색인`. 작업 중에는 창이 열려 있고 보일 때만 1.5초마다 상태를 다시 읽는다.
- 설정 창의 HTML 렌더링 칸(Q88, `loadRenderWebBox`): 상태, 선택형 Chromium 설치·켜기·끄기. 켜 두었지만 준비가 안 됐으면(버전 불일치 등) `reason`을 보여 주고 버튼은 "Chromium 다시 내려받기". 작업 중에는 창이 보일 때만 상태를 다시 읽는다.
- 스킬: 그래프의 태그 모양 노드(보라 = 라이브러리 안, 파랑 = 개인 폴더, 이중 테두리 = 도구 스킬, 점선 = 갱신 필요, 빨강 = 직접 고침), 상단 `스킬` 토글. 세트 상세의 `#skill-box`(`loadSkillBox`: 범위×도구 상태 표, 내보내기·다시 내보내기·제거, 충돌 diff와 덮어쓰기, SKILL.md 미리보기), 스킬 상세 `renderSkill`.
- 라이브러리 스킬: 초록 태그 노드(`libskill`). 상세 `renderLibSkill`(설명, 붙인 노드·떼기, 상속으로 받는 세트, 붙이기+범위 안내 `scopeHint`, 원칙으로 풀기, 파일, 가져온 곳·되살리기). 규칙 상세의 `skillsHtml`(함께 쓰는 스킬 + 붙이기). 설치된 스킬 상세의 '앱으로 가져오기'(붙일 노드, 원래 스킬 끄기). 설정 창 `callerHtml`(개인 스킬·전역 지침 설치/제거, 직접 부르는 명령).
- 하위 정리 창(`#tidy-dialog`): `openTidy` → (열린 요청이 있으면 이어서) 1 진단 · 2 요청 복사 · 3 답 붙여넣기 → `showTidyPreview`(왜, 전·후 미니 그래프 `miniGraph`, 최종 프롬프트 비교, 변경 체크) → `applyTidy` → `arrangeTidy`(세트는 깊이 순 아래로, 원칙은 오른쪽 줄, 레퍼런스는 왼쪽 줄로 다시 배치하고 저장).
- 세트 상세의 `역할별로 나누기`(Q75)는 같은 정리 창을 `goal=roles`로 열어 역할별 원칙 분리안을 받고 동일한 미리보기·적용을 사용한다. 상단 `블라인드 비교`(Q87)는 별도 `compare.html`에서 두 버전과 골든 샘플을 출처를 가리고 비교한다.

- 구성 요소(v3.9): 이미지 레퍼런스 상세의 `compsHtml`(요소 카드: 잘라 낸 그림, 종류, 설명, 점검, `ref:<id>#<요소>` 복사, 빼기 / `AI로 나누기`·`상자 고치기`). 창 `#comp-dialog`: `openComponents`(열린 요청에 답이 있으면 바로 미리보기) → `renderCompRequest`(대상 선택, 로컬 AI, 끌어 놓기 손잡이 = 프롬프트+원본+격자 사본, 요청 복사, 답 붙여넣기) → `showCompPreview`(상자 편집기 `boxEditorHtml`/`bindBoxEditor`: 끌어 옮기기·모서리로 크기, 놓으면 바뀐 것만 `preview`에 보내 다시 그림 / 요소 카드 체크 → 반영). `editComponents`는 같은 편집기로 반영된 요소를 고쳐 바로 저장. 세트 규칙 표의 레퍼런스 칸에 `+ 요소 N개`. 놓은 zip이 분리 답이면 `case: components`로 미리보기를 연다.
- 콘솔 패널(`#console-panel`, 상단 `콘솔` 버튼·Ctrl+\`): 아래쪽, 높이 끌어 조절(localStorage `am.consoleH`). 처음 열 때 xterm을 만든다(`conInit`). **콘솔 탭**은 앱 안 줄 편집기(`conConsoleInput`: ←→ Home End, ↑↓ 기록 200줄, Tab 명령·노드 id 완성, Ctrl+C·L·U, 여러 줄 붙여넣기, 한글 폭 2칸 계산) → `POST /api/console` → 출력 → 바뀐 노드가 있으면 `conRefresh`. **AI 탭**(`+ AI` 선택 → `conAddAI`)은 xterm ↔ `/api/term/*`(입력 8ms 모아 보내기, 긴 폴링 `conReadLoop`, 창 크기 `fit`). 탭 × = 프로세스 종료. 스크롤백 5000줄.
- 실시간 반영: `conPoll`이 창이 보이고 포커스일 때 2초(아니면 4초, 요청 없음)마다 `/api/changes` → 서명이 바뀌면 `conRefresh`(대화상자가 열려 있으면 닫힌 뒤) → `loadGraph` 후 노드 `sig`가 달라진 노드에 `.flash`(2.4초).
- 상태: 전역 `state` (graph, cy, selected, target, authorTarget, pkg, opts, tasks, outcome, request, requestPkg). 사용자 편의 설정만 localStorage(`am.*`).
- 그래프: `loadGraph`가 매번 Cytoscape를 다시 만들되 위치는 현재 화면 > `layout.json` > 이웃 근처 순으로 유지. 미리보기 요소는 `.preview` 클래스(저장 제외).
- 상세 패널: `renderRule` / `renderRef`(산출물 포함) / `renderUnregistered`(작성·다시 쓰기) / `renderAi`.
- 드롭: `setupInbound`(Tauri `tauri://drag-*` 이벤트 또는 HTML5) → `handleDrop` → `/api/stage` → `openOutcome` → `confirmOutcome` → `/api/stage/<sid>/commit`. 결과 확인 중 놓은 파일은 `attach`로 간다.
- 내보내기 드래그: Tauri는 `plugin:drag|start_drag`(파일 여러 개), 브라우저는 텍스트 드래그. 놓으면 `/api/history`에 기록.
- `APP_VERSION`은 `server.py`와 `app.js`에서 같아야 한다(다르면 UI가 경고).
- API 주소 `API`(0.16.2): Tauri 창이면(`window.__TAURI__`: 빌드된 exe의 `tauri.localhost`, 개발 모드 `npm run dev`의 개발 서버 `127.0.0.1:1430` 모두) 사이드카 `http://127.0.0.1:8765`, 그 밖(서버가 UI를 직접 내주는 브라우저 모드)은 같은 출처 `""`. `server.py --port`로 다른 포트에 띄운 서버도 브라우저 모드로 열 수 있다. (0.16.1은 호스트 이름으로 판단해 개발 모드 창이 1430으로 요청하는 문제가 있었다)

## 12. 테스트

```
python -m unittest discover -s app/tests      # 라이브러리 루트에서
```

| 파일 | 다루는 것 |
|---|---|
| `test_engine.py` | 기본값 복사, 다중 상속, 순환, 동의어, 지시문, 결정성, 덜어내기 3단계, 민감도, 변경 감지, index |
| `test_v3.py` | 우선순위·점검, 미등록·작성, 가져오기, 히스토리, 버전·휴지통·Git, 자동 커밋 메시지(분류·이름·이름 변경·출처·확인 시점), 루트 문서("기타")는 자동 커밋 제외·설정 파일은 커밋, 기본값 업데이트, fresh |
| `test_requests.py` | 폴더 요청: 준비, 시트·묶음, 결과 미리보기·적용, 덮어쓰기, 잘못된 plan |
| `test_v33.py` | 검색, 참고 예시·덜어내기, stage 선택지, commit(산출물·원본·변형·골든·승격·되돌리기), 요청 없는 zip·`@target`·짝 맞추기, 내보내기 명령, RAG, MCP(stdio 실제 프로세스), 노드 종류별 자동 연결, 개발 착수 프리셋 조립, `uses` 조립 순서, 하위 정리 왕복(프롬프트 동일·쓰이는 노드 휴지통 거부·값 변경 감지), 승인 게이트·작업 대기 끝 단락 |

| `test_local_ai.py` | 로컬 AI 도구: 찾기(직접 경로)·버전·연결 시험, 실행 → 결과 파일 → 정리 미리보기, 결과 파일이 없으면 failed, Codex 읽기 전용 인자·표준 출력 plan과 인라인 파일의 요청 경로 검사. 가짜 도구 사용 |
| `test_skills.py` | 도구 스킬 설치(다시 해도 같음), 세트 → 스킬 컴파일(이식 가능한 프런트매터·결정성), 내보내기·고친 흔적 감지·제거, 다른 스킬은 덮어쓰지 않고 읽기만 |
| `test_standards.py` | 스킬 노드 보관·붙이기·조립, 표준 찾기·`cli.py standard`·쓰기 없는 `--lookup`(기존 패키지와 기본값 파일 보존), 설치된 스킬 가져오기(원래 끄기), 스킬 폴더를 노드에 놓기, AI가 부르게 하기(개인 스킬·전역 지침 줄), 원칙으로 풀기 요청, 스킬 휴지통(폴더째) |
| `test_components.py` | 구성 요소(이미지가 아닌 키 `palette`로): 메타 정리·경고, 원본 + 요소 첨부 렌더·파일 생성, 결정성(같은 PNG·프롬프트), 이미지 키는 요소 없이 원본만, `ref:<id>#<요소>`·없는 요소 오류, 덜어내기(요소 먼저 글로), 요소 없으면 프롬프트 그대로, 요청 왕복(격자·붙여넣기·상자 수정·반영·다른 필드 보존), 기존 요소 바꾸기 기본 꺼짐·에이전트 경로, zip → `case: components`, 사람 수정 저장, 스킬 내보내기에 요소 포함 |
| `test_v310.py` | 이미지 키(Q82-A): 규칙 단락·spec·ref check·우선순위 문장 없음, ImageGen판(chatgpt_web)·요청서판(claude_desktop·codex), 차트·도식 참조와 SVG·코드 금지, 비슷한 원본 양방향·중복·원본 없는 것 제외, 원본 없음 경고, 덜어내기(비슷한 원본 먼저, 글·시트 변환 없음), 이미지 키 없으면 단락 없음, 옛 기본값 대비·사용자 `image: true` 키, 결정성 |
| `test_embed.py` | 뜻 검색(가짜 인코더): 꺼져 있으면 키워드 검색 그대로·인코더 안 부름, 켜면 낱말이 안 겹쳐도 뜻으로 찾고 관계없는 것은 뺌·결정성, 벡터 캐시·바뀐 조각만 다시 계산·`index`의 정리, 참고 예시에 반영, 인코더 오류 시 키워드로, 모델 없으면 켜도 키워드 |
| `test_console.py` | `am` set·unset·undo(본문 보존), 명령 전 변경과 명령 변경을 따로 커밋(출처·이름), use·inherit·순환 거부, new·rename·기록의 actor, 오류, 명령줄 연결(`cli.py set`·`standard`), 파일 서명, `.claude/settings.json` 합치기(사용자 항목 유지·깨진 JSON 보존), `am` 실행 파일, POSIX pty 세션(환경·한글 입출력·1개 제한·종료) |
| `test_render_web.py` | Chromium 렌더링은 기본 꺼짐이고 설정에서 켜고 끌 수 있음, Playwright 버전과 맞는 Chromium이 없으면 준비 안 됨 + 다시 받으라는 `reason`, 실행 성공은 기억(headless로 한 번만 띄움), 꺼져 있으면 HTML 추출이 브라우저를 띄우지 않음 (브라우저는 모의) |

UI는 자동 테스트가 없다. 변경 후 브라우저 모드(`start.bat browser`)로 확인한다.

## 13. 실행과 배포

- 뜻 검색(선택, v3.11): `requirements.txt`에 넣지 않는다. 설정의 버튼이 서버를 돌리는 Python에 `onnxruntime`·`tokenizers`를 설치하고 모델(약 135MB)을 사용자 캐시 폴더에 받는다.
- HTML 렌더링(선택, Q88): `requirements.txt`에 넣지 않는다. 설정 버튼에서만 `playwright`와 Chromium을 설치한다. 실제 브라우저 추출은 이 PC에서 선택 기능을 켠 경우에만 실행한다.
- `start.bat`(라이브러리 루트): Python 확인 → 패키지 설치(최초) → (선택) AI 탭용 `pywinpty` 설치, 실패해도 계속 → 8765 포트의 이전 서버 정리 → 빌드된 exe / `npm run dev` / 브라우저 모드. 메시지는 영문만(cmd 한글 파싱 문제).
- 앱 코드 이력: `app/`에서 `git log`. 코드를 바꾼 작업은 끝날 때 `app/`에서 커밋한다(라이브러리 데이터의 자동 기록과 별개). `main`에 직접 push하지 않고 작업 브랜치 → PR → 사용자 머지 순서로 올린다(`AGENTS.md` "브랜치와 PR").
- 서버 시작 시 포트가 막혀 있으면 이전 서버에 `/api/shutdown`을 요청하고 인계받는다.
- exe는 `find_app_dir`로 사이드카(`core/server.py`)가 있는 `app/`을 찾는다: `ASSETS_APP_DIR` → exe 위치 기준(`app/src-tauri/target/<release|debug>/`의 세 단계 위, `core/server.py`가 있을 때) → 빌드할 때의 경로(`CARGO_MANIFEST_DIR`). 라이브러리 폴더를 옮기거나 이름을 바꿔도 다시 빌드하지 않아도 된다(2026-11-24). 다른 PC 배포는 3단계.

## 14. 확장 지점

| 하고 싶은 것 | 고칠 곳 |
|---|---|
| 새 키 | `_defaults/keys.yaml`(+ `app/defaults/keys.yaml`, `version` 올리기) |
| 새 구성 요소 종류 | `keys.yaml`의 `parts:`(+ `engine.PART_DEFAULTS`), 요청 문구 `components.render_request`, UI `PART_LABEL` |
| 새 드롭 대상 AI | `targets.yaml`에 항목 추가(`kind: agent|chat`, `image_gen`) |
| 이미지 키 추가 | `keys.yaml`에 `image: true` (지시문·check 없이). ImageGen 단락 문구는 `engine.image_lines` |
| 새 레퍼런스 타입 | `engine.REF_TYPES`, `REF_FOLDERS`, `EXT_TYPE`, `store.TYPE_FOLDER`/`EXT_TYPE`, `extract.kind_of`, UI `TYPE_LABEL` |
| 예상 결과 선택지 | `drops.OPT`, `stage`의 분기, `commit`의 실행부, UI `choiceHtml` |
| 임베딩 검색 | 섞는 비율·구간: `search.W_KW`·`W_SEM`·`SEM_FLOOR`·`SEM_CEIL`·`SEM_ONLY_MIN`. 모델 교체: `embed.REPO`·`REV`·`FILES`·`MODEL_KEY`(바꾸면 벡터 캐시는 자동으로 버려짐), e5가 아니면 `query:`·`passage:` 접두어도 |
| MCP 도구 | `mcp_server.TOOLS` + `call` |
| 원칙 프리셋 나누기 | 앱의 `하위 정리 (AI)`로 AI에게 맡기거나, `principles/dev-*.md`(키 묶음 이동) + 세트의 `uses` 직접 수정. 새 주제면 `keys.yaml`에 dev 키 추가 |
| 정리 op 추가 | `tidy._apply_op`, `OP_LABEL`, `render`의 형식 예시, UI `itemText`·`OP_ICON` |
| 스킬을 읽는 새 도구 폴더 | `skills.AGENTS`에 (상대 폴더, 라벨) 추가 |
| 도구 스킬 내용 | `app/skills/assets-manager/` (프롬프트 규격이 바뀌면 references도 함께). SKILL.md의 `{PY}` `{CLI}` `{LIB}`는 설치 때 채워진다 |
| `am` 명령 추가 | `console._dispatch` + `HELP`, UI `CON_CMDS`(완성), 테스트 `test_console` |
| AI 탭 도구 추가 | `local_ai.TOOLS`(경로 찾기) + UI `#con-add` 선택지 |
| 표준 찾기 점수 | `standards.WEIGHT`, `_text` (입구 시그니처 유지) |
