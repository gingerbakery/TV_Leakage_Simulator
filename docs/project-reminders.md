# 프로젝트 리마인드 목록

## 목적
- 사용자가 “나중에 다시 알려달라”고 명시한 항목을 한곳에서 관리한다.
- 단순 보류 항목과 명시적 리마인드 요청을 구분한다.
- 각 phase 진입 시 관련 문서를 다시 검토할 수 있도록 조건을 기록한다.

## 명시적으로 요청된 리마인드

### 1. 전체 프레임워크 전환 시점 재평가

사용자 요청:
- ray tracing 핵심 기능을 갖춘 뒤 전체 프레임워크 전환 시점을 반드시 다시 알려줄 것.

전환 후보:
- Next.js(React) + TypeScript
- Tailwind CSS + shadcn/ui
- Zustand
- Three.js 또는 React Three Fiber
- Supabase를 포함한 backend/database 구조

현재 상태:
- 전체 프레임워크 전환은 보류 상태다.
- 3D viewer만 Three.js로 먼저 전환하여 사용 중이다.
- RT-2C 1회 반사 구현이 완료되었으며 RT-2D 완료 후 전환 시점 재평가 조건을 다시 확인한다.

다시 검토할 조건:
- RT-2C/RT-2D가 완료되어 ray tracing 핵심 workflow가 동작한다.
- Emitter, Receiver, Material, Result 데이터 계약이 안정화된다.
- `run_web.py` 단일 파일 유지보수 비용이 기능 개발 속도를 저하시킨다.
- 다수 개발자가 frontend/backend를 병렬 개발해야 한다.
- UI 컴포넌트와 상태 관리 복잡도가 현재 구조의 안전 범위를 넘는다.

### 2. V2 고급 표면 광학 모델 검토

사용자 요청:
- V2 phase부터 더욱 상세한 표면 특성을 반영하기 위해 고급 반사·산란 모델을 반드시 다시 검토할 것.

검토 대상:
- Oren–Nayar 계열
- Fresnel + Microfacet GGX/Beckmann
- Anisotropic Gaussian/GGX
- Retroreflective lobe
- 측정 BSDF/BRDF

다시 검토할 조건:
- V2 phase 계획을 시작한다.
- V1 표면 모델과 실측 분포의 차이가 설계 우열에 영향을 준다.
- 절대 밝기 정합 목표가 강화된다.

상세 문서:
- `docs/v2-advanced-surface-models.md`

## 중요 보류 백로그

다음 항목은 명시적인 “알람 요청”과는 구분되지만, 사용자가 향후 반영을 언급한 중요 백로그다.

### 분광·시감도 고도화
- M2/M3는 현재 보류한다.
- 색온도와 색좌표는 당장 범위에서 제외한다.
- 향후 절대 밝기 정확도와 광원 종류 구분이 중요해지면 시감도/분광 모델을 재검토한다.

### 측정 BSDF 연결
- V1에서는 파일 등록과 데이터 계약 중심으로 유지한다.
- 측정 파일 포맷, 좌표계, normalization, interpolation 방식은 후속 구현한다.

### Transform preview 표시 제어
- ray tracing 실행 중 또는 결과 확인 시 기존/이동 후 객체가 함께 보여 혼동되지 않도록 preview overlay를 사용자가 끌 수 있게 한다.

## 운영 원칙
- phase 계획을 시작할 때 이 문서를 확인한다.
- 리마인드 조건을 충족하면 관련 작업을 신규 phase 또는 backlog로 제안한다.
- 완료된 항목은 삭제하지 않고 상태와 완료 날짜를 기록한다.
- 새로운 리마인드 요청은 이 문서와 해당 기능 문서에 함께 기록한다.
## 성능 관련 리마인드
- PERF-1 Python hot path 최적화는 완료되었다.
- PERF-2 flat BVH CAD intersection 1차 가속은 완료되었다.
- 실제 회사 TV ROI CAD의 end-to-end 시간을 측정한 뒤 Embree/Open3D/GPU 필요성을 다시 판단한다.
- GPU 경로를 추가하더라도 GPU가 없는 PC에서 CPU fallback이 반드시 동작해야 한다.
- 전체 프레임워크 전환 시점은 RT-2D와 계산 백엔드 경계가 안정화된 뒤 다시 알린다.
