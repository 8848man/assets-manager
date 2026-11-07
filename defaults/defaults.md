---
id: _defaults
type: rule
kind: defaults
title: 앱 기본값
version: 1
rules:
  language: 한국어
  tone: 정중하고 명확한 존댓말
  audience_level: 해당 분야 비전문가도 이해할 수 있는 수준
  output_format: 마크다운(소제목과 짧은 문단)
  forbidden: 근거 없는 수치, 과장 표현, 출처 없는 인용
---
앱이 제공하는 범용 기본값(레이어 0)입니다. 모든 규칙 노드의 최상위 부모입니다.

내 취향은 여기에 쓰지 말고 `principles/global.md` 같은 위쪽 노드에서 덮어써 주세요.
나중에 앱을 배포할 때 이 파일은 그대로 다른 사람에게 갑니다.
