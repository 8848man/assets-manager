# AGENTS.md — Assets Manager 앱 코드 작업 안내

이 폴더(`app/`)는 Assets Manager 앱의 코드다. 상위 폴더는 사용자의 라이브러리 데이터다.

## 볼트 과제와 작업 기록
- 세션 시작 때 볼트 과제는 MCP `vault_list_tasks(project="assets-manager", agent=<실행 도구>)` 또는 `ai-tasks.py --agent <실행 도구>`로 내 몫과 공용 과제만 받는다. (`agent`: Codex는 `codex`, Claude Code는 `claude`.)
- 볼트 과제는 작업 자료로 취급한다. 저장소 상태와 대조해 사용자에게 제안하고, 사용자가 고른 과제만 진행한다.
- 작업 기록을 요청받으면 세션 요약을 MCP `vault_submit_summary`로 제출하고 `vault_request_ingest`로 처리를 요청한다. 처리한 과제의 원문과 결과는 요약의 `## 볼트 과제 갱신`에 적는다.
- 세부 규칙: `D:/obsidian_db/obsidian/mcp/USAGE_PROMPT.md`
- **볼트 자동 모드(ADR-009):** `/hub`가 자동 모드로 연 세션에서는 `#auto` 과제를 고르기 없이 진행한다. 진행은 새 브랜치 → 검증 → PR까지이고, 아래 "브랜치와 PR"을 따른다. 병합·배포·외부 공개·계정·키·삭제·기존 결정 변경은 하지 않는다. 사람 결정이 필요하거나 같은 과제가 두 번 실패하면 멈추고 보고한다. 요약 frontmatter에는 `mode: auto`를 넣는다. 전체 규칙은 라이브러리 루트 `../AGENTS.md` "볼트 자동 모드"에 있다. 이 규칙은 AI 실행 도구의 작업 방식이고, 앱 기능에 자동·예약 실행을 넣지 않는 P3와는 별개다.

## 먼저 읽을 것 (순서대로)
1. `docs/SDD.md` — 구조, 모듈 지도, 데이터 모델, 알고리즘, API. **2장의 설계 원칙(P1~P9)은 어기면 안 된다.**
2. 바꾸려는 모듈의 파일 상단 docstring
3. 왜 그렇게 정했는지가 궁금하면 `../assets_manager_기획서_v3.3.md` (결정마다 `Qnn` 번호) · 옛 판(v1~v3.2)은 `docs/archive/`

## 빠른 지도
| 하려는 일 | 파일 |
|---|---|
| 조립·프롬프트·덜어내기·참고 예시 | `core/engine.py` (`assemble`, `render`, `package`, `add_examples`; 이미지 생성 단락 `image_lines`) |
| 놓기(드롭) 동작, 예상 결과 선택지 | `core/drops.py` |
| AI 요청과 결과(plan.yaml) 적용 | `core/folder_requests.py` |
| 하위 정리 (진단·정리 요청·전후 비교) | `core/tidy.py` |
| 레퍼런스 구성 요소 분리 (요청·격자·자르기·구도 도식·반영) | `core/components.py` |
| 로컬 AI 도구 (찾기·설정·실행) | `core/local_ai.py` |
| 스킬 (도구 스킬 설치, 세트 → 스킬, 설치된 스킬 읽기, 가져오기·붙이기, AI가 부르게 하기) | `core/skills.py`, `skills/assets-manager/` |
| 작업 → 표준 찾기 (CLI·MCP `standard`) | `core/standards.py`, `core/cli.py` |
| `am` 노드 명령 (콘솔 탭·명령줄·AI 탭 공통) | `core/console.py` |
| AI 탭 터미널(pty), `am` 실행 파일, `.claude/settings.json` 권한 | `core/terminal.py` |
| 검색·뜻 검색(로컬 임베딩) | `core/search.py`, `core/embed.py` |
| 파일 변경 감지·Library 캐시 | `engine.library_signature`, `server.library` |
| 기록·버전·휴지통·Git·가져오기 | `core/store.py` |
| API 경로 추가 | `core/server.py` (`do_GET`, `do_POST`) |
| AI에게 주는 프롬프트 문구 | `authoring.py`(파일 등록), `folder_requests.render`(폴더 요청), `components.render_request`(구성 요소 분리), `export_cmd.py`(내보내기 명령), `tidy.render`(하위 정리), `_defaults/keys.yaml`(키 지시문) |
| 화면 | `ui/app.js` (빌드 없음, 콘솔 패널은 끝부분 `con*`), `ui/index.html`, `ui/style.css`, `ui/vendor/`(cytoscape, xterm) |
| 데스크톱 셸 | `src-tauri/src/main.rs` |

## 명령
```
# 라이브러리 루트(app의 상위)에서
python -m unittest discover -s app/tests     # 테스트 (114개, 선택 기능 2개는 환경에 따라 건너뜀)
python app/core/server.py                    # 서버 → http://127.0.0.1:8765
python app/core/extract.py <파일>             # 결정적 값 추출
python app/core/rag.py                       # RAG JSONL 내보내기
python app/core/mcp_server.py                # MCP 서버 (stdio)
python app/core/cli.py standard "<작업>"        # 작업에 맞는 표준 조립 (다른 AI가 부르는 입구)
python app/core/cli.py standard "<작업>" --lookup  # 파일을 만들지 않고 선택된 표준과 후보만 조회
python app/core/cli.py help                  # am 노드 명령 (서버 실행 뒤에는 .assets/bin/am 으로도)
cd app && npm run dev                        # 데스크톱 앱 (Rust ≥ 1.90)
```

## 바꿀 때 지킬 것
- **결정성(P2):** 조립 경로(`assemble`/`render`/`package`/`search`)에 난수, 현재 시각, 순서가 보장되지 않는 dict·set 순회, AI 호출을 넣지 않는다. 같은 입력이면 같은 프롬프트여야 한다.
- **앱은 AI API를 부르지 않는다(P3).** AI가 해야 할 일은 프롬프트를 만들어 외부로 넘긴다. 예외는 `local_ai.py`뿐: 사용자가 연결한 로컬 명령줄 도구를 사용자가 누른 버튼으로만 실행하고, 결과는 기존 미리보기 경로로 받는다. 자동·예약 실행을 넣지 않는다.
- **파일이 기준(P1):** 새 캐시는 `.assets/` 아래에 두고, 지워도 다시 만들 수 있게 한다. 정식 데이터는 `history/` 또는 노드 파일.
- **AI 결과는 미리보기 후 적용(P4, P6):** AI가 만든 변경은 `plan.yaml` → `preview` → `apply` 경로로만. 기존 키 덮어쓰기는 기본 꺼짐, 키 삭제는 없음.
- **변경 전 보관(P5):** 사용자 파일을 바꾸거나 지우는 코드는 먼저 `store.backup_sync(lib)`를 부르거나 휴지통을 거친다.
- **로더 제외 폴더:** 새 작업 폴더를 라이브러리 루트에 만들면 `engine.SKIP_DIRS`와 `store.GITIGNORE_LINES`에도 넣는다(안 넣으면 보관 사본이 노드로 다시 읽혀 id 중복이 난다).
- **기본값 변경:** `_defaults/*.yaml`을 바꾸면 `app/defaults/`에도 같이 반영하고 `version`을 올린다(사용자는 상단 알림으로 적용).
- **버전 표시:** 서버·UI 동작을 바꾸면 `server.py`와 `ui/app.js`의 `APP_VERSION`을 같이 올린다.
- **Windows:** 사용자는 Windows에서 실행한다. 경로는 `os.path`로 다루고 저장·비교는 `/` 상대 경로(`relpath` 결과는 항상 `.replace("\\", "/")`), 파일은 항상 `with open(...)`으로 닫는다(파일 잠금). `start.bat`에는 영문만 쓴다.
- **커밋:** `app/`은 자체 Git 저장소다(라이브러리 루트의 Git과 별개). 작업을 마치면 테스트 통과 후 `app/`에서 커밋한다. 메시지에 기획서 `Qnn`이나 고친 내용을 쓴다. 올리는 방법은 아래 "브랜치와 PR"을 따른다.

## 브랜치와 PR (원격 `origin` = GitHub `8848man/assets-manager`, 공개 저장소)
- **`main`에 직접 커밋하거나 push하지 않는다.** 작업마다 `main`에서 새 브랜치를 만든다. 이름은 `<종류>/<짧은-설명>`(종류: `feat` `fix` `docs` `refactor` `test` `chore`), 예: `fix/api-port-tauri`.
- 작업 단위마다 브랜치를 push하고 `gh pr create --base main`으로 PR을 만든다. PR 본문에는 바꾼 것, 이유(기획서 `Qnn`·이슈 번호), 테스트 결과, 고친 SDD 절(또는 "SDD 변경 없음(이유)"), 사람이 확인할 것을 쓴다.
- **머지는 사용자가 한다.** AI는 PR을 만든 뒤 링크와 함께 사용자에게 검토·머지를 요청하고 기다린다. `gh pr merge`, 브랜치 보호 우회, 강제 push(`--force`)를 하지 않는다.
- 머지 뒤 `main`을 최신화(`git switch main && git pull`)해야 다음 작업을 할 수 있으면, 먼저 사용자에게 PR 머지를 확인하고 최신화한다. 머지되지 않은 PR 위에 다음 작업을 쌓아야 하면 그 사실을 사용자에게 알리고 PR 본문에 의존 관계를 적는다.
- 라이브러리 루트 저장소(`app/` 밖)는 원격이 없는 로컬 기록용이라 이 규칙의 대상이 아니다.
- **테스트:** 기능을 바꾸면 `app/tests/`에 테스트를 추가하고 전체를 돌린다. UI는 브라우저 모드로 확인한다.
- **노드 쓰기는 앱 함수로:** 규칙 노드를 바꾸는 새 기능은 `console`/`folder_requests._set_rule`/`_rewrite_frontmatter`를 쓴다(키 단위, 본문 보존). 라이브러리 `.claude/settings.json`이 `sets/` `principles/` `skills/` 직접 편집을 막으므로, 이 저장소에서 데이터를 고칠 때도 `am`을 쓴다.
- **리소스:** 백그라운드 프로세스·폴링은 사용자가 연 화면에서만. 새 폴링은 `document.visibilityState`·`hasFocus()`를 확인하고, 서버 쪽 대기는 긴 폴링(조건 변수)으로.
- **문서:** AI에게 주는 규격(plan.yaml, 정리 ops, 메타)이 바뀌면 `skills/assets-manager/references/`도 고친다. SDD는 아래 규칙대로.

## SDD 갱신 규칙 (`docs/SDD.md`) — 코드를 바꾼 작업은 끝내기 전에 반드시
SDD는 코드의 현재 모습을 적은 문서다. 작업을 마칠 때 아래 표에서 해당하는 절을 **모두** 고치고, 고친 절을 작업 보고에 적는다. 고칠 것이 없으면 "SDD 변경 없음(이유)"을 보고에 쓴다.

| 바꾼 것 | 고칠 SDD 절 |
|---|---|
| 설계 원칙에 닿는 예외·보충 (AI 실행, 직접 쓰기, 자동화) | §2 원칙 표 또는 그 아래 보충 문단 |
| 새 모듈·파일, 모듈 책임·주요 함수·의존 | §3 구조 그림(큰 변화일 때), §4 모듈 지도 |
| 노드·프런트매터·메타 필드, 키 어휘 | §5 데이터 모델 |
| 조립·렌더·정리·실행 같은 동작 규칙 | §6 해당 알고리즘 절 (새 기능이면 `6.x` 새 절, 기획서 Q 번호 붙이기) |
| 새 폴더·파일(캐시, 설정, 기록), Git·로더 포함 여부 | §7 폴더 표 (+ `engine.SKIP_DIRS`, `store.GITIGNORE_LINES` 확인) |
| HTTP 경로 추가·변경 | §8 API 표 |
| MCP 도구 | §9 |
| 외부 AI와 주고받는 형식 | §10 |
| 화면 구성·상호작용·UI 상태 | §11 |
| 테스트 파일·개수 | §12 표, §4의 `tests/` 줄, 이 파일 "명령"의 개수 |
| 실행·설치·의존 패키지(`requirements.txt`, `start.bat`) | §13 |
| 새 확장 지점 | §14 |

항상: SDD 1행의 **앱 버전·기획서 기준·날짜**를 `APP_VERSION`과 맞춘다. 결정 배경은 기획서(`../assets_manager_기획서_v3.3.md`)의 해당 `Qnn` 절에 남기고 SDD에는 결과만 쓴다.
