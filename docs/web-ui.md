# Web UI 설계 메모

## 목표
- CAD 프로그램처럼 직관적인 흐름으로 빛샘 시뮬레이터를 사용할 수 있게 한다.

## 기본 작업 흐름
1. Model import
2. Components 확인
3. ROI 설정
4. Transform / gap 설정
5. Material 지정
6. Ray tracing
7. Result 확인

## 현재 UX 방향
- 상단 `Model import`는 독립 카드
- 좌측은 세로 아코디언
- 우측은 3D viewer 중심
- 세부 입력은 가능하면 viewer popup에서 수행

## ROI
- 기본 상태는 `미선택`
- ROI 선택 방식:
  - 3D view에서 선택
  - component 선택
  - 3차원 공간 선택(확장 예정)
- viewer 탐색과 ROI 선택은 분리

## Components
- tree 구조로 component 표시
- component 클릭으로 선택/해제
- component별 action:
  - `Transform`
  - `Material`

## Transform
- component 전체 move가 기본
- local face move는 보조 기능
- x/y/z move, Rx/Ry/Rz tilt 지원
- 입력 즉시 preview
- `Apply` 시 실제 transform rule 반영
- `Reset`, `Restore original` 제공

## Material
- 왼쪽은 library 관리
- 오른쪽 viewer popup으로 assignment
- component 전체 적용 우선
- 필요 시 face override

## Result
- V1에서는 수치/2D 결과 중심
- 추후:
  - 3D observer view
  - ray path overlay
  - before/after compare 고도화
