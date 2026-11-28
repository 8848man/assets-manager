# plan.yaml (폴더 요청 · AI에게 맡기기 · 내보내기)

`inbox/<요청 id>/out/plan.yaml`에 쓴다. 새 메타·규칙 파일과 스킬 폴더는 같은 `out/` 아래에 라이브러리와 같은 상대 경로로 둔다(예: `out/image/foo.meta.yaml`, `out/sets/bar.md`, `out/skills/example-skill/SKILL.md`).

```yaml
request: <요청 id>
summary: 무엇을 만들었는지 한 줄
create:                     # 새로 만든 파일
  - path: image/foo.meta.yaml
    reason: ...
  - type: skill
    path: skills/example-skill  # SKILL.md가 든 폴더 전체
    uses: <붙일 규칙 노드 id 또는 @target>
    approval: human             # 생략 가능; coordinator는 현재 적용 불가
    reason: ...
update:                     # 기존 규칙 노드의 키 추가·변경 (삭제 불가, 꼭 필요할 때만)
  - node: <기존 규칙 노드 id>     # 요청이 가리키는 노드면 @target
    set_rules:
      <키>: <텍스트 값 또는 ref:<id>>
    reason: ...
relations:                  # 참고용 연결 (프롬프트에는 안 들어감)
  - { from: <id>, type: similar_to | variant_of | pairs_with | supersedes | derived_from, to: <id>, reason: ... }
missing: []                 # 넣지 못한 원본 파일 이름
```
- 연결을 비워 두어도 앱이 놓인 노드의 하위로 자동 연결한다(새 원칙은 그 노드가 `uses`로 붙임).
- 기존 키를 바꾸는 update는 사용자가 따로 확인한다.
- 이미 등록된 파일은 새로 만들지 말고 그 id로 연결만 한다.
- 스킬의 `SKILL.md` 프런트매터 `name`은 폴더 이름과 같아야 한다. 앱이 요청 목록과 미리보기에 스킬 제안을 표시하며, 사람이 선택해 적용하면 기존 스킬 가져오기와 규칙 노드의 `uses` 연결을 실행한다. `approval: coordinator`는 허용 기록(`이름@초안 SHA-256`) 대조 인터페이스만 예약돼 있고 적용은 차단된다.
