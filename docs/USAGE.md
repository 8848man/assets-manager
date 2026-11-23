# Assets Manager 사용 안내

[← README](../README.md) · 설계 문서: [SDD.md](SDD.md)

이 문서는 설치부터 기능별 사용법까지 자세히 적은 안내입니다. 아래의 `D:\projectsssets`는 작성자 환경의 라이브러리 경로 예시입니다.

## 구성

| 경로 | 내용 |
| --- | --- |
| `core/engine.py` | 로더, 다중 상속 조립, 패키지, 자동 덜어내기, 민감도, 변경 감지, index 생성 |
| `core/server.py` | Python 사이드카 HTTP 서버 (표준 라이브러리 + PyYAML + Pillow) |
| `core/extract.py` | 결정적 값 추출기 (이미지·글·HTML/CSS·PDF). 단독 실행 가능: `python app\core\extract.py <파일>` |
| `core/authoring.py` | 미등록 파일을 AI가 규격대로 등록하게 하는 작성 프롬프트 |
| `core/drops.py` | 예상 결과 창: 놓은 것을 보고 선택지를 만들고(stage) 고른 대로 실행(commit) |
| `core/export_cmd.py` | AI 대화창에 붙여넣는 내보내기 명령 |
| `core/search.py` | 결정적 키워드 검색 (참고 예시, MCP) |
| `core/rag.py` | RAG 내보내기 (`노드id#부분` JSONL) |
| `core/mcp_server.py` | MCP 서버 (Claude 데스크톱이 라이브러리를 직접 검색·조립) |
| `core/folder_requests.py` | 폴더 → 노드 AI 요청: 요청 확인, 가이드 프롬프트·plan.yaml, 결과 확인, 선택 적용 |
| `core/store.py` | 드롭 히스토리, 버전 보관, 휴지통, Git 자동 커밋, 가져오기, 기본값 업데이트 |
| `ui/` | 그래프와 상세 패널 (빌드 단계 없음, Cytoscape.js 로컬 번들) |
| `src-tauri/` | Tauri v2 데스크톱 셸, 네이티브 파일 드래그 (`tauri-plugin-drag`) |
| `defaults/` | 앱이 번들하는 기본값. 라이브러리에 `_defaults/`가 없으면 여기서 복사됩니다 |
| `tests/` | 코어 엔진 테스트 |

## 0) 한 번에 실행: `start.bat`

라이브러리 폴더(`D:\projects\assets`)의 **`start.bat`를 더블클릭**하면 됩니다.
Python 찾기, 처음 한 번 패키지 설치, 앱 실행(서버는 앱이 알아서 띄우고 끔)까지 모두 처리합니다.

| 명령 | 하는 일 |
| --- | --- |
| `start.bat` | 빌드된 exe가 있으면 실행 → 없으면 개발 모드(`npm run dev`) → Rust/Node가 없으면 브라우저 모드 |
| `start.bat build` | 바로 실행되는 exe를 한 번 만들어 둠(몇 분). 이후 `start.bat`는 즉시 열림 |
| `start.bat browser` | 서버를 띄우고 브라우저로 엶 |

빌드된 exe(`app\src-tauri\target\release\assets-manager.exe`)는 바탕화면에 바로가기를 만들어 써도 됩니다. 단, 이 PC의 이 폴더 위치를 기준으로 동작합니다(배포용 설치 파일은 3단계).

## 1) 브라우저 모드로 먼저 확인 (Python만 있으면 됨)

```powershell
cd D:\projects\assets
python -m pip install -r app\requirements.txt
python app\core\server.py
```

브라우저에서 http://127.0.0.1:8765 를 엽니다. 그래프, 상세 패널, 고급 토글, 한도 검사, 자동 덜어내기, 변경 감지까지 모두 동작합니다.
브라우저 모드에서는 드래그 손잡이가 **경로가 포함된 프롬프트 텍스트**만 끌어 갑니다(파일 첨부는 데스크톱 모드에서).

테스트: `python -m unittest discover -s app\tests`

## 2) 데스크톱 모드 (Tauri)

처음 한 번 준비할 것:
- Rust: https://rustup.rs (Windows는 설치 중 "Desktop development with C++" 빌드 도구 설치 안내를 따르세요)
- Node.js 18 이상
- WebView2: Windows 10/11에는 기본으로 있습니다

```powershell
cd D:\projects\assets\app
npm install
npm run dev
```

- 앱이 시작되면 Python 서버를 자동으로 띄우고, 앱을 닫으면 함께 끕니다.
- `python` 명령 이름이 다르면 환경 변수 `ASSETS_PYTHON`(예: `py`)으로 지정하세요.
- 라이브러리 위치를 바꾸려면 `ASSETS_ROOT`를 지정하세요.
- 첫 컴파일은 몇 분 걸립니다.

## 부록: 0단계 드래그 실험 체크리스트 (기획서 12장)

`naver-blog-post` 노드를 선택하고, 대상을 고른 뒤 손잡이를 끌어 각 앱에 놓아 보세요.

| 대상 | 확인할 것 | 결과 |
| --- | --- | --- |
| Codex | `prompt.md`와 png 2장이 모두 첨부되는가 / 클립보드 텍스트의 경로를 열어 보는가 | |
| Claude 데스크톱 | `prompt.md`와 png 2장이 모두 첨부되는가 | |
| ChatGPT 웹 (브라우저) | 앱 → 브라우저 창으로 파일 3개가 모두 첨부되는가 | |

결과에 맞게 `_defaults/targets.yaml`의 한도값을 고쳐 주세요. 드래그가 실패하면 기획서의 탈출구(Electron 전환)를 검토합니다.

## 새 레퍼런스 등록하기 (v3)

1. **파일 넣기:** 앱 창에 이미지·html/css·pdf·txt/md를 끌어다 놓거나, `image/`, `web/`, `document/`, `writing/` 폴더에 직접 넣습니다. 그래프에 점선 원(미등록)으로 나타납니다.
2. **AI로 등록하기:** 점선 원을 고르고, AI를 고른 뒤 손잡이를 끌어다 놓습니다(또는 "작성 프롬프트 복사").
   - **Codex(에이전트):** AI가 `extract.py`를 직접 실행하고 원본 옆에 `.meta.yaml`을 씁니다. 저장되면 바로 반영됩니다.
   - **Claude·ChatGPT(채팅형):** prompt.md, extract.py, 원본이 함께 첨부됩니다. AI가 준 `assets-import.zip`을 앱 창에 끌어다 놓으세요.
3. 승인 단계는 없습니다. 모든 변경은 기록되어 되돌릴 수 있습니다.

## AI 대화에서 결과물 가져오기 (v3.3)

1. 상단 "내보내기 명령 복사" 또는 노드 상세의 "이 노드로 내보내기 명령 복사"를 누릅니다.
2. 작업하던 ChatGPT·Claude 대화창에 붙여넣으면 AI가 `assets-import.zip`을 만들어 줍니다.
3. zip을 앱 창에 놓습니다(일반 명령은 넣을 노드 위에). AI가 넣지 못한 파일이 있으면 함께 한 번에 놓으세요.
4. **예상 결과** 창에서 확인하고 적용합니다. 원본이 없어도 설명만으로 등록되고, 나중에 파일을 그 노드에 놓아 채울 수 있습니다.

## 예상 결과 창 (v3.3)

무엇이든 놓으면 무슨 일이 일어날지 먼저 보여 줍니다. 상단 "예상 결과"를 끄면, 선택이 필요 없는 경우(빈 공간에 파일 등)는 바로 반영하고 "되돌리기"를 띄웁니다.

## 산출물과 참고 예시 (v3.2)

- 결과 파일을 세트 노드에 놓고 "산출물로 기록"을 고르면 `outputs/<세트>/`에 저장되고, 어떤 드롭(AI, 프롬프트, 작업 설명)으로 만들었는지 연결됩니다.
- 세트의 드롭 영역에 **이번 작업**을 쓰면, 비슷한 과거 산출물 2~3개가 "참고 예시"로 붙습니다(골든 샘플과 규칙이 우선).
- 산출물 상세의 "골든 샘플로 승격"으로 기준을 바꿉니다.

## 명령줄에서 표준만 조회하기

`python app/core/cli.py standard "<작업 설명>" --lookup`은 맞는 세트와 다른 후보만 보여 줍니다. `--json`을 붙이면 같은 내용을 JSON으로 받습니다. 이 조회는 기본값이나 드롭 패키지 파일을 만들거나 바꾸지 않습니다. 규칙 전문과 첨부 경로가 필요하면 `--lookup` 없이 `standard`를 실행합니다.

## AI 비서 연결 (MCP · RAG)

상단 "AI 비서 연결"에서 Claude 데스크톱용 MCP 설정을 복사해 `claude_desktop_config.json`에 넣으면, Claude가 이 라이브러리를 직접 검색(`search`)하고 세트를 조립(`assemble`)합니다. 같은 창에서 RAG용 JSONL 내보내기도 할 수 있습니다.

## 폴더를 노드에 놓아 AI로 노드 추가하기 (v3.1)

1. 폴더를 그래프의 노드(예: 블로그 세트) 위에 끌어다 놓습니다. **요청 확인** 창에서 파일 목록과 AI에게 요청할 내용을 확인하고, AI를 골라 "요청 만들기".
2. 손잡이를 AI 앱에 끌어다 놓습니다(또는 프롬프트 복사).
3. 채팅형 AI가 준 `assets-import.zip`을 앱 창에 놓거나, 에이전트가 결과를 쓰면 상단에 "AI 결과 도착"이 뜹니다.
4. **결과 확인** 패널에서 항목을 고르고 "체크한 항목 적용". 기존 키를 바꾸는 항목은 빨간색이고 기본으로 꺼져 있습니다.

노드를 옮기면 위치가 `layout.json`에 저장됩니다. "배치 초기화"로 자동 정렬할 수 있습니다.

## 기록과 되돌리기

- **삭제:** 노드의 "삭제"는 휴지통(`history/trash/`)으로 옮깁니다. 알림의 "되돌리기"나 상단 "휴지통"에서 복구합니다.
- **이전 버전:** "고급"을 켜면 노드마다 이전 버전 목록이 보이고 한 번에 되돌릴 수 있습니다(`history/versions/`).
- **Git:** PC에 Git이 있으면 라이브러리 폴더를 저장소로 만들고 변경마다 자동 커밋합니다(`app/`, 캐시, 버전 보관함은 제외). 상위 폴더가 이미 Git 저장소면 만들지 않고 버전 보관만 합니다.
- **드롭 기록:** `history/drops.jsonl`. 그래프의 AI 노드(마름모)에 드롭 횟수와 작성한 노드가 표시됩니다.
- **앱 기본값 업데이트:** 앱에 새 기본값이 있으면 상단에 알림이 뜹니다. 누르면 적용하고 이전 판은 보관합니다.

## 손으로 노드 추가하기 (MVP)

- **레퍼런스:** 원본 옆에 `이름.meta.yaml`을 둡니다. 예시는 `image/naver-graph-style.meta.yaml`.
- **규칙:** `principles/`나 `sets/`에 md 노트를 만들고 프런트매터에 `type: rule`, `inherits`, `rules`를 씁니다. 예시는 `sets/naver-blog-post.md`.
- 저장하고 앱 창으로 돌아오면 자동으로 다시 읽습니다. 조립 결과가 바뀐 노드는 상단 "변경 감지"에 나타납니다.
