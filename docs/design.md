# 빛샘 시뮬레이터 아키텍처

## 전체 파이프라인
- `importers.py`
  - CAD 업로드 또는 합성 장면 생성
  - `obj`, `stl(ascii)`, `step/stp`, `x_t` 처리
- `components.py`
  - mesh를 component 단위로 분해
- `roi.py`
  - ROI/receiver 해석 및 scene payload 구성
- `gap.py`
  - gap rule, move/tilt 기반 gap strength 생성
- `materials.py`
  - 기본 material 라이브러리 제공
- `raytracer.py`
  - 광선 추적 및 receiver 누적
- `engine.py`
  - 실행 orchestration 및 결과 저장
- `render.py`
  - 2D 결과 시각화

## 데이터 흐름
1. CAD import 또는 샘플/합성 장면 생성
2. component/face 분해
3. ROI 선택 및 receiver 정의
4. gap rule / transform rule / material / emitter 설정
5. Monte Carlo ray tracing
6. 상대 밝기 및 대략 절대 밝기 추정
7. JSON/CSV/PNG/HTML 출력

## 핵심 계산식
- 수신면 축적 광량
  - `E_j = Σ(P_i * cosθ_i / d_i^2 / A_j)`
- 휘도 근사
  - `L_rel = k_brdf * E_j * (rho_diffuse / π)`
- 절대 추정
  - `Nits_est = k_abs * L_rel`

## V1의 gap 해석 방식
- 실제 solid deformation 해석은 하지 않는다.
- 대신 move/tilt를 face-level gap strength로 환산하는 근사 모델을 사용한다.

### 지원 개념
- `component_move_gap`
  - 부품 전체 rigid move/tilt
- `face_gap`
  - 선택된 local face 집합만 move
- `bbox_gap`
  - 특정 공간 박스 내 face 선택

## 웹 UI 구조
- 상단: `Model Import`
- 좌측: 세로 아코디언 기반 작업 패널
- 우측: 3D viewer + popup 입력

권장 메뉴 순서:
- CAD / Model import
- Components
- ROI 설정
- Transform manager
- Material library
- Ray tracing
- Result

## 역할 분리 포인트
- ROI/CAD scene selection
  - `components.py`, `roi.py`, `run_web.py`의 ROI UI 구간
- Gap/Transform
  - `gap.py`, `types.py`, `run_web.py`의 transform UI 구간
- Ray trace/Brightness
  - `raytracer.py`, `engine.py`, `render.py`

## 확장 포인트
- M2/M3:
  - 분광
  - 시감도
  - 색온도/색좌표
- GUI:
  - Three.js 기반 viewer
  - 관측 시점 렌더
  - ray path overlay
  - before/after compare 고도화
- CAD:
  - STP/X_T 정합성 향상
  - 대형 어셈블리 성능 개선
