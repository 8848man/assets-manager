# 구성 요소 나누기 (`plan.yaml`의 `components`)

이미지 레퍼런스 하나를 **캐릭터·도형·구도**로 나눕니다. 앱이 원본에서 잘라 내고 구도 도식을 그려, 드롭할 때 원본과 함께 첨부합니다.
요청 파일(`inbox/<요청 id>/prompt*.md`)과 좌표 격자 사본(`inbox/<요청 id>/src/<id>.grid.png`)을 함께 보세요.

```yaml
request: <요청 id>
node: <레퍼런스 id>
summary: 한 줄 요약
components:
  - name: character          # 영문 소문자-하이픈, 요소마다 다르게 (같은 이름 = 기존 요소 고치기)
    part: character          # character | shape | composition
    title: 흰 점눈 캐릭터
    box: [x, y, w, h]        # 원본 픽셀, 정수. 격자 사본의 숫자를 읽어서
    spec: |
      다시 그릴 수 있는 사실: 비례, 얼굴 요소, 외곽선 굵기(px), 채색, 끝처리
    check: ["눈이 작은 검은 점 2개로만 되어 있는가"]
  - name: block
    part: shape
    title: 라벨 블록
    box: [x, y, w, h]
    spec: |
      모양, 모서리 둥글기, 외곽선, 면 채색, 라벨 위치
  - name: composition
    part: composition        # box 생략 = 전체 그림
    title: 왼쪽에서 오른쪽으로 내려가는 3단 흐름
    regions:                 # 큰 덩어리 3~6개
      - { label: 블록 탑, box: [x, y, w, h] }
    flow: [[x, y], [x, y]]   # 시선·이야기 흐름 점 2~6개, 순서대로
    spec: |
      흐름 방향, 크기 대비, 바닥선, 여백
```

- 캐릭터: 같은 디자인이면 가장 잘 보이는 하나, 디자인이 다르면 따로(최대 3개). 도형: 되풀이되는 대표 도형 1~3개. 구도: 1개.
- 색 값은 요청에 적힌 추출 색(hex)을 쓴다. 한 줄 값에 `#`이 들어가면 따옴표로 감싸거나 `|` 블록으로 쓴다(그렇지 않으면 yaml 주석이 된다).
- 메타 파일을 직접 고치지 않는다. 사용자가 앱에서 잘라 낸 결과를 보고(상자 조정 가능) 반영한다.
