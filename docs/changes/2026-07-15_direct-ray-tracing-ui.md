# Emitter → Receiver Direct Ray Tracing 연결

## 목표

- 등록한 Emitter와 Receiver를 이용해 첫 실제 ray tracing 실행 흐름을 완성한다.
- RT-1 범위에서는 반사나 산란 없이 Emitter에서 Receiver로 직접 도달하는 광선만 계산한다.
- 사용자가 계산 성공 여부, 상대 밝기와 공간 분포를 UI에서 즉시 확인하도록 한다.

## 구현 내용

- Web UI 버전을 `v0.9.0`으로 갱신했다.
- `/api/raytrace/direct` JSON API를 추가했다.
- `/api/scene` 응답에 `scene_token`을 추가하고 최근 CAD mesh 3개를 서버 메모리에 보관한다.
- 브라우저는 실행할 때 전체 CAD mesh를 다시 보내지 않고 scene token, Emitter, Receiver, Transform과 계산 조건만 전송한다.
- `raytrace_bridge.py`를 추가해 Web UI 계약을 `DirectRayTraceInput`으로 변환한다.
- 적용된 component move/tilt를 UI와 동일한 pivot 및 X→Y→Z 회전 순서로 trace mesh에 반영한다.
- Emitter별 `Rays` 합계를 전체 ray 수로 사용한다.
- Result에 전체 ray 수, Receiver hit 수, hit ratio와 runtime을 표시한다.
- Receiver별 `peak/mean/p95 nit_est`, flux, hit count, 수광 면적을 표시한다.
- Receiver grid의 누적 flux를 컬러 heatmap으로 표시한다.
- 저장된 direct hit ray path를 녹색 선으로 Three.js viewer에 표시한다.
- Emitter, Receiver 또는 Transform 변경 시 기존 결과를 무효화하고 재실행을 안내한다.

## 계산 범위

- 포함: 면/Datum/Reference Emitter sampling, Lambertian/Isotropic/Gaussian 방향 분포, Receiver plane 교차, acceptance angle, flux grid와 pseudo nit 계산.
- 제외: CAD 구조물에 의한 ray 차폐, 재료 반사율, scattering, 1회 이상 bounce.
- 따라서 현재 결과는 설정과 실행 흐름 및 직접광 경향을 검증하는 RT-1 결과이며 실제 빛샘 최종값은 아니다.

## 검증

- Python 문법 검사와 전체 단위 테스트 10개가 통과했다.
- Component move가 direct trace mesh vertex에 반영되는 회귀 테스트를 추가했다.
- 저장된 ray path가 `emitter → receiver` 두 event로 구성되는지 확인했다.
- 실제 HTTP 통합 테스트에서 120개 ray 중 120개가 Receiver에 도달했다.
- 통합 테스트에서 저장 path 10개와 `peak_nit_est=130.487...` 응답을 확인했다.
- 생성된 inline JavaScript를 Node `--check`로 검사했다.

## 다음 단계

1. Receiver보다 먼저 CAD triangle을 만나는 ray를 차단하는 occlusion 판정을 추가한다.
2. 첫 CAD hit의 material optical profile을 조회한다.
3. specular/Lambertian/Gaussian 1회 반사를 계산한다.
4. 직접광과 1회 반사광 contribution을 분리해 Result에 표시한다.
