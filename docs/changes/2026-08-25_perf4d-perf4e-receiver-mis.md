# 2026-08-25 PERF-4D·PERF-4E 1차 변경 이력

## 변경 범위

### PERF-4D

- GPU summary용 compact workspace 추가
- 전체 point/normal/distance 배열 대신 path quota 크기 geometry workspace 사용
- 선택된 표시 path만 CUDA sparse retrace
- full/compact workspace cache 분리
- sparse retrace 실패 시 logical chunk 전체 replay
- workspace contract·byte·geometry capacity·retrace timing 결과 필드 추가

### PERF-4E-A

- Lambertian/isotropic Emitter의 Receiver-directed primary MIS 추가
- CAD face와 datum plane batch Emitter 지원
- per-ray power weight를 CPU wavefront와 GPU resident 경로에 연결
- Gaussian/scalar-only 경로의 명시적 source fallback 추가
- UI에 `Primary ray sampling`과 Receiver sample ratio 추가

### PERF-4E-C

- Auto convergence를 전체 재실행에서 독립 구간 누적으로 변경
- `1→2→4→8배` 처리량을 15배에서 8배로 감소
- Receiver grid Flux·제곱합·hit의 통계적으로 올바른 결합
- contribution Flux 가중 결합과 count 합산
- config/Emitter seed의 구간별 독립화
- Receiver 계약 변경 시 fail-closed 중단

## 검증

- Python PERF-4D/4E 관련 테스트: 통과
- Frontend typecheck와 convergence/model 테스트: 통과
- RTX 3070 production FP64 Ray/BVH preflight: 통과
- PERF-4D full/compact parity 및 fallback 0: 통과
- PERF-4E CPU/GPU parity 및 12-seed 분산 비교: 통과

## 미완료·다음 작업

- PERF-4E-B surface reflection NEE 또는 bounce MIS
- 실제 회사 TV ROI에서 1억 Ray·10회 반사 장시간 VRAM/열/오차 검증
- 실제 장면에서 Receiver MIS 기본 활성화 여부 결정
