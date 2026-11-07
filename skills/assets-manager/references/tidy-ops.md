# 하위 정리 plan.yaml (`ops`)

목표: 그래프를 읽기 쉽게. **최종 프롬프트 내용은 유지**(키를 옮기는 건 괜찮지만, 값을 바꾸거나 없애면 이유를 분명히). 고칠 것이 없으면 `ops: []`.

```yaml
request: <요청 id>
summary: 한 줄 요약
why: |                         # 전체적으로 왜 이렇게 바꾸는지 2~4줄
  ...
ops:                           # 위에서부터 차례로 적용
  - { op: set_inherits, node: <세트 id>, inherits: [global], reason: ... }
  - { op: set_uses, node: <세트 id>, uses: [<원칙 id>, ...], reason: ... }
  - { op: set_inherits, node: <원칙 id>, inherits: [], reason: 부품 원칙은 상속하지 않음 }
  - { op: move_rule, key: <키>, from: <노드 id>, to: <노드 id>, reason: ... }
  - { op: create_rule, id: <새 id>, kind: principle, title: 제목, rules: { <키>: <값> }, reason: ... }
  - { op: set_rule, node: <id>, key: <키>, value: <값>, reason: 합치느라 문장을 바꿈 }
  - { op: rename, node: <id>, title: 새 제목, reason: ... }
  - { op: set_body, node: <규칙 노드 id>, body: 노드 설명, reason: ... }
  - { op: set_relations, node: <id>, relations: [{ type: derived_from, target: <id> }], reason: ... }
  - { op: trash, node: <규칙 노드 id>, reason: 내용을 옮겨 빈 노드가 됨 }
```
- `inherits: []`은 "앱 기본값만 받음"(상속선 없음).
- 원칙 하나 = 주제 하나(키 2~6개). 너무 잘게 나뉜 원칙은 합치고, 섞인 원칙은 나눈다.
- 같은 출처 연결은 한 곳(보통 세트)에만.
- 레퍼런스·산출물 노드는 지우지 않는다. 규칙 노드는 내용을 옮긴 뒤에만 `trash`.
