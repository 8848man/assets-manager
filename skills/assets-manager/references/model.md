# 규칙 노드 모델

## 두 가지 관계
- **계층 `inherits`**: `앱 기본값 → 내 전역 원칙 → 세트 → 파생 세트`. 뒤에 쓴 부모가 우선. 순환 금지. 세트는 보통 계층 부모 하나.
- **부품 `uses`**: 주제별 원칙(`kind: principle`)과 라이브러리 스킬(`skills/<이름>/`)을 세트가 붙인다. 원칙은 **아무것도 상속하지 않고** 자기 규칙만 가진다. 스킬은 조립 때 이름·설명·경로만 들어가고, 해당될 때 읽는다.

우선순위: 세트 자신의 규칙 > 세트가 붙인 원칙(뒤에 붙인 것 우선) > 윗 계층.
같은 키를 여러 원칙이 주면 앞의 것은 사라지므로 원칙마다 **서로 다른 키**를 쓴다.

## 프런트매터
```yaml
---
id: naver-blog-post            # 영문 소문자-하이픈, index.md의 id와 겹치지 않게
type: rule
kind: set                      # principle | set
title: 네이버 블로그 포스팅
inherits: [global]             # 세트만. 원칙에는 쓰지 않는다
uses: [dev-docs]               # 세트가 붙이는 원칙
rules:
  tone: 친근한 존댓말           # 텍스트 값
  chart_style: ref:naver-graph-style   # 레퍼런스 값 (ref:<레퍼런스 id>)
relations:
  - { type: derived_from, target: <근거 문서 id> }
---
본문: 이 노드를 언제 쓰는지 사람용 설명
```

## 원칙을 문서에서 뽑을 때
주제별로 `principles/<id>.md`로 나누고(키 2~6개), 그 원칙들을 `uses`로 붙이는 세트 하나를 만든다. 미해결 문제는 `open_questions`, 폐기한 결정은 `forbidden`. 근거 문서는 `document/` 레퍼런스로 등록하고 **세트 한 곳에만** `derived_from`을 단다.
