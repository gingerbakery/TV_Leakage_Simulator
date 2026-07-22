# Receiver 수신광 분리 및 Ray tracing 진행률

## 목적
- Receiver에 직접 도달한 광선과 한 번 이상 반사된 뒤 도달한 광선을 3D Viewer에서 구분한다.
- 대량 ray 계산 중 사용자가 멈춘 것으로 오해하지 않도록 실제 계산 진행률과 예상 남은 시간을 제공한다.

## 수신광 표시 변경
- `Receiver 도달 · Direct`: surface event 없이 Emitter에서 Receiver로 도달한 경로, 녹색.
- `Receiver 도달 · 반사광`: 하나 이상의 surface event를 거쳐 Receiver로 도달한 경로, 노란색.
- 두 유형은 왼쪽 `Result > 3D Ray path 표시`에서 독립적으로 켜고 끈다.
- Direct, Specular, Lambertian, Gaussian 비수신 경로 필터는 기존대로 유지한다.
- 기본값은 모든 유형 표시다.

## 진행률 데이터 계약
- `POST /api/raytrace/start`: 비동기 ray tracing job을 생성하고 `job_id`를 반환한다.
- `GET /api/raytrace/status?job_id=...`: 다음 상태를 반환한다.
  - `status`: `queued`, `running`, `completed`, `failed`
  - `phase`: `queued`, `preparing`, `tracing`, `completed`, `failed`
  - `processed_rays`, `total_rays`, `progress`
  - `elapsed_sec`, `estimated_remaining_sec`, `rays_per_sec`
  - 완료 시 `result`, 실패 시 `error`
- 기존 `POST /api/raytrace/direct` 동기 API는 호환성을 위해 유지한다.

## ETA 계산
- Ray tracer가 전체 계산당 최대 약 400회 간격으로 실제 처리 ray 수를 보고한다.
- `rays_per_sec = processed_rays / elapsed_sec`
- `estimated_remaining_sec = (total_rays - processed_rays) / rays_per_sec`
- 초기 CAD/광학 입력 준비 단계에는 ETA 대신 `예상 시간 계산 중`을 표시한다.
- 각 ray의 반사 횟수와 교차 비용이 다르므로 ETA는 실행 중 계속 보정되는 추정값이다.

## UI
- Ray tracing 버튼 아래에 진행률 게이지를 표시한다.
- 초 단위는 `12s`, 분 단위는 `10m 12s`, 장시간은 `1h 5m` 형식으로 표시한다.
- 완료 시 100%와 `완료`, 실패 시 붉은 실패 상태를 유지한다.

## 검증
- 100,000 ray 비동기 smoke test에서 진행률이 8.5%에서 100%까지 증가했다.
- ETA가 실행 중 감소하고 완료 시 `0.0`이 되는 것을 확인했다.
- 최종 result의 total ray가 요청한 100,000과 일치했다.
