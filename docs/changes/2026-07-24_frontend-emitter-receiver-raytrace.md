# React Emitter·Receiver·Ray tracing 실행 이전

## 목적

- 프레임워크 전환 로드맵 10단계를 완료한다.
- 기존 Python ray tracer 입력 계약과 비동기 job API를 React UI에서 그대로
  사용한다.

## 이전 내용

- Emitter
  - Viewer에서 선택한 CAD face를 발광면으로 등록
  - 중심 좌표, 회전, 폭·높이를 사용하는 Datum plane 등록
  - Total power / Power per area, Lambertian / Isotropic / Gaussian,
    ray 수와 normal flip 설정
- Receiver
  - 중심 좌표와 회전 기반 Datum plane 등록
  - 현재 메인 Viewer 카메라의 target, view normal, 수평·수직축으로 만드는
    Current view Receiver 등록
  - 폭·높이, grid 해상도, acceptance angle과 normal flip 설정
- Viewer
  - CAD surface Emitter를 주황색 face overlay로 표시
  - 가상 Emitter와 Receiver의 면 외곽선 및 normal 방향 표시
  - Current view Receiver의 반투명 면이 모델을 가리지 않도록 fill opacity를
    낮게 유지
- 실행
  - Material assignment를 `OpticalProfile`과 `OpticalAssignment`로 compile
  - component Transform, 해석 제외·삭제 component, 활성 ROI face를
    `RayTraceRequest`에 포함
  - `/api/raytrace/start` 실행 후 `/api/raytrace/status`를 300 ms polling
  - queued, preparing, tracing, completed, failed 상태와 진행률·잔여 시간을 표시
  - 설정 변경 시 이전 job/result 연결을 무효화

## 검증

- TypeScript 타입 검사, Oxlint, Vitest, production build 통과
- 실제 `tv_leakage_roi_left_bottom_no_gap.stp` CAD로 Datum Emitter와
  Current view Receiver 비동기 실행 완료
- 같은 CAD의 face `40698`을 CAD surface Emitter로 등록해 `2,000 rays`,
  `18 receiver hits`, 약 `1.12 s` 실행 완료
- Step 11에서 사용할 stored ray path 결과는 TanStack Query cache에 유지
