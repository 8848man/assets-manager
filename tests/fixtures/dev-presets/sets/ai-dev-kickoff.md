---
id: ai-dev-kickoff
type: rule
kind: set
title: 개발 착수 (테스트)
inherits:
- global
uses:
- dev-workflow
- dev-docs
- dev-design-approval
- dev-conflict-verify
rules:
  tone: 간결한 기술 문체
  audience_level: 비개발자도 판단할 수 있게 요약한다.
  output_format: 1) 설계안 2) AGENTS.md 초안 3) SDD
  open_questions: 남은 질문을 적는다.
  forbidden: 근거 없는 수치
---
테스트용 프리셋입니다.
