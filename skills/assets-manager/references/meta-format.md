# 레퍼런스·산출물 메타 (`<폴더>/<이름>.meta.yaml`)

```yaml
id: 영문-소문자-하이픈            # index.md의 id와 겹치지 않게
type: image | web | document | writing
kind: output                      # 확인된 산출물일 때만 (outputs/<세트>/)
title: 한국어 제목
file: 원본 파일 이름              # 같은 폴더 기준
summary: 한 줄 요약 (40자 이내)
spec: |                           # 첨부 없이 글로만 전달할 때의 설명 3~6줄, 보이는 것만
  ...
check:                            # 이 레퍼런스를 따랐는지 확인하는 예/아니오 질문 2~4개
  - ...인가
keywords: [비슷한 말 5~10개]       # 검색용
tags: [..]
sensitivity: public               # 회사 내부 자료면 internal
authored_by: claude_desktop | codex | chatgpt_web
extracted: {...}                  # extract.py 결과 그대로
components: [...]                 # 이미지만. 캐릭터·도형·구도로 나눈 요소 (앱의 '구성 요소 나누기' 요청으로만 씀, components-format.md)
```
- `spec`에는 보이는 것만 쓴다. 추측한 의도나 평가를 쓰지 않는다.
- 이미지 레퍼런스의 `spec`·`check`는 검색과 사람의 결과 판정에 쓰인다. 이미지 생성 프롬프트에는 들어가지 않는다(원본 파일이 기준, v3.10). 그래도 보이는 대로 정확히 쓴다.
- 레퍼런스가 여러 역할(캐릭터·사물·화풍·구도)을 섞고 있으면 `spec`을 역할별 문단으로 나눠 쓴다. 파일 등록 때는 `components`를 쓰지 않는다(사용자가 앱에서 따로 요청한다).
