---
name: assets-manager
description: 사용자의 작업 표준(Assets Manager). 사용자가 글·이미지·디자인·문서·코드 등 작업을 맡기면 시작하기 전에 이 스킬로 그 작업에 맞는 표준(규칙·레퍼런스·스킬·자가 점검)을 받아 따릅니다. Assets Manager가 준 요청(inbox/<요청 id>/의 prompt 파일)을 처리하거나 라이브러리에 레퍼런스·원칙·세트를 등록하거나 plan.yaml·하위 정리를 할 때도 사용합니다.
metadata:
  generator: Assets Manager
  kind: tool
---
# Assets Manager — 내 작업 표준

## 1. 작업을 시작하기 전에 (어느 프로젝트에서든)
사용자가 작업을 맡기면 결과물을 만들기 전에 먼저 맞는 표준을 받으세요.

```
{PY} "{CLI}" standard "<맡은 작업을 한 줄로>"
```
- 출력된 지침(규칙, 첨부 레퍼런스 경로, 함께 쓰는 스킬, 진행 방식, 자가 점검)을 **이번 작업의 지시로** 따릅니다. 진행 방식(승인 게이트)이 있으면 그대로 지킵니다.
- 출력 맨 위에 다른 후보가 보이고 그쪽이 더 맞으면 `--set <id>`를 붙여 다시 부르세요. 세트 목록: `{PY} "{CLI}" sets`
- "맞는 작업 표준이 없습니다"가 나오면 평소대로 진행합니다.
- Assets Manager 요청 파일(`inbox/<요청 id>/…`)을 처리하는 중이라면 이 단계는 건너뛰고 아래 2를 따르세요.

## 2. Assets Manager 라이브러리 다루기 (요청 처리·등록·정리)
라이브러리 위치: `{LIB}`

위 폴더는 사용자의 **작업 표준 라이브러리**이고, 아래 경로는 모두 이 폴더 기준입니다. Assets Manager 앱이 읽고 쓰며, 사용자는 앱에서 결과를 **미리 보고 확인한 뒤** 반영합니다. 당신은 파일을 만들어 돌려줄 뿐, 반영하지 않습니다.

### 가장 중요한 규칙
1. **요청 파일이 있으면 그 지시가 우선입니다.** `inbox/<요청 id>/prompt*.md` 또는 `inbox/<요청 id>/package/prompt.md`를 끝까지 읽고 그대로 수행하세요.
2. **지시된 위치에만 씁니다.** 보통 `inbox/<요청 id>/out/`입니다. 파일 등록 요청이면 원본 옆 `<이름>.meta.yaml`입니다. 그 밖의 파일(`sets/`, `principles/`, `_defaults/`, `history/`, `index.md`, `graph.json`, `app/`)은 고치거나 지우지 마세요.
3. **기존 규칙은 직접 고치지 않습니다.** 바꿀 것은 `plan.yaml`의 `update`(폴더 요청) 또는 `ops`(하위 정리)로 제안합니다.
4. **값은 추측하지 않습니다.** 색상 hex, 크기 같은 값은 `python app/core/extract.py <파일>`(또는 이 스킬의 `scripts/extract.py`, 라이브러리 루트에서)로 뽑습니다.
5. **사용자에게 질문하지 말고 끝까지 진행합니다.** 애매하면 가장 합리적인 해석을 고르고 `reason`에 한 줄로 적으세요.

### 라이브러리 구조
| 위치 | 내용 |
|---|---|
| `index.md` | 자동 생성 목차. 모든 노드 id·종류·상속이 한 줄씩. **id를 정하기 전에 먼저 읽어 겹치지 않게** |
| `_defaults/keys.yaml` | 규칙 키 어휘(라벨, 지시문, 자가 점검). 새 키는 꼭 필요할 때만 |
| `principles/*.md` `sets/*.md` | 규칙 노드(md 프런트매터). 자세히: [references/model.md](references/model.md) |
| `image/` `web/` `document/` `writing/` `golden/` | 레퍼런스: 원본 + `<이름>.meta.yaml` |
| `outputs/<세트>/` | 확인된 산출물 (`kind: output`) |
| `skills/<이름>/` | 라이브러리에 보관한 스킬(통째로). 규칙 노드가 `uses`로 붙인다 |
| `inbox/<요청 id>/` | 요청 작업장: `request.json`, `src/`(원본), `out/`(당신이 쓰는 곳) |

### 할 일별 안내
- **폴더 요청 / AI에게 맡기기** (레퍼런스 묶음을 노드로 만들기): 메타 파일 + `plan.yaml`. 형식은 [references/plan-format.md](references/plan-format.md)
- **파일 등록** (메타 없는 파일에 `.meta.yaml` 쓰기): [references/meta-format.md](references/meta-format.md)
- **하위 정리** (노드 아래 구조를 읽기 쉽게): `plan.yaml`의 `ops`. [references/tidy-ops.md](references/tidy-ops.md)
- **작업을 내 표준대로**: 해당 세트의 최종 규칙을 따르세요. 같은 이름의 세트 스킬이 설치돼 있으면 그 스킬이 이미 조립된 규칙입니다.

### 끝낼 때
만든 파일 목록과 각 파일을 만든 이유를 짧게 적고 끝내세요. 결과는 사용자가 Assets Manager에서 확인합니다.
