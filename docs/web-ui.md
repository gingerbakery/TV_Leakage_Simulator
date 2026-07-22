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

## Ray Tracing
- Web UI `v0.9.12`부터 RT-3 다회 반사를 지원한다.
- Advanced에서 최대 반사 횟수 `0~3`, 최소 ray power, threshold/Russian roulette 종료 방식을 설정한다.
- Receiver heatmap은 direct와 설정된 최대 depth까지의 반사 flux를 합산한다.
- 3D ray path는 surface와 Receiver event의 bounce depth를 유지한다.
- Web UI `v0.9.13`부터 `Fast summary`와 `Detailed contribution` 결과 모드를 선택한다.
- 일반 반복 계산은 Fast summary, 부품·면·소재별 원인 분석은 Detailed contribution을 사용한다.
- Web UI `v0.9.14`부터 왼쪽 `Result` 메뉴에서 저장 광선을 유형별로 표시하거나 숨긴다.
- 기본값은 Receiver 도달, Direct, Specular, Lambertian, Gaussian 경로를 모두 표시하며, 체크박스를 해제한 유형만 3D Viewer에서 숨긴다.
- Web UI `v0.9.15`부터 Receiver 도달 경로를 `Direct`와 `반사광`으로 분리해 표시한다.
- Ray tracing 실행 중 실제 처리 ray 수 기반 진행률, 경과 시간, 예상 남은 시간을 게이지로 표시한다.

## Three.js 전환
- 3D viewer는 `docs/viewer-data-contract.md`의 `mesh-scene.v1`을 기준으로 단계 전환한다.
- 세부 전환 계획은 `docs/threejs-viewer-migration.md`를 따른다.
- 2026-07-14부터 기본 viewer engine은 Three.js이며, Canvas viewer는 비교/비상용 fallback으로 유지한다.
- 현재 Three.js viewer는 CAD 표시, orbit 조작, camera preset, render mode 확인을 우선 지원한다.
- ROI/component/face picking은 Canvas 기반 기존 로직이 아직 주 기능이며, 다음 단계에서 Three.js raycaster로 이관한다.
