# RT-2A CAD 차폐 판정

## 목표

- Emitter와 Receiver 사이의 CAD 구조물이 direct ray를 실제로 차단하도록 한다.
- 차단된 ray가 Receiver 밝기와 heatmap에 기여하지 않도록 한다.
- 실제 STEP 삼각망에서도 반복 계산이 가능한 교차 성능을 확보한다.

## 구현 내용

- `run_direct_ray_trace()`에서 Receiver와 CAD의 최초 교차 거리를 비교한다.
- `t_surface < t_receiver`이면 CAD 차폐로 판정한다.
- 차폐 ray는 `surface_hit_count`에 누적하고 Receiver grid에는 누적하지 않는다.
- 차폐 경로에 face index, component ID, material ID, hit point/normal을 저장한다.
- source face와 `epsilon_mm` 이내 교차는 self-intersection 방지를 위해 제외한다.
- `TriangleMesh.intersect_ray()`에 `min_t`, `max_t` 범위를 추가했다.
- 삼각형이 24개를 넘는 mesh에는 lazy BVH를 자동 구축해 nearest-hit 탐색을 가속한다.
- Web UI를 `v0.9.6`으로 갱신했다.
- 결과에 `CAD blocked`, `Blocked ratio`를 추가했다.
- 3D viewer에서 Receiver 도달 ray는 녹색, CAD 차단 ray는 주황색으로 구분한다.

## 검증 시나리오

동일한 Datum Emitter, Receiver, Gaussian 8도 분포와 시나리오당 10,000개 ray를 사용했다.

| 시나리오 | Receiver hit | CAD blocked | 결과 |
|---|---:|---:|---|
| Open path | 10,000 (100.0%) | 0 (0.0%) | 차폐가 없으면 전량 도달 |
| Full blocker | 0 (0.0%) | 10,000 (100.0%) | 완전 차폐 확인 |
| 4 mm gap | 9,100 (91.0%) | 900 (9.0%) | gap 통과/차단 분리 확인 |

추가 회귀 검증:

- Receiver가 CAD보다 앞에 있으면 Receiver hit가 우선한다.
- 차단 경로가 component/material ID를 보존한다.
- 128 triangle mesh에서 BVH 경로가 동일하게 완전 차폐를 판정한다.
- gap 크기 증가에 따라 Receiver hit가 단조 증가한다.
- 부분 gap의 Receiver flux가 완전 차폐와 완전 개방 사이에 위치한다.
- 기존 RT-1 emitter/receiver 테스트가 모두 유지된다.

## 성능

- 9,522 triangle 합성 차폐판 × 10,000 ray: 약 `1.30 s`
- 환경: bundled Python 3.13 runtime, 단일 프로세스
- BVH 구축 시간은 최초 교차 호출의 runtime에 포함된다.
- 실제 CAD에서는 형상 분포와 ray 방향에 따라 시간이 달라지므로 후속 실도면 benchmark가 필요하다.

## 결과 파일

- 그래픽 리포트: `outputs/rt2a_occlusion_report/rt2a_occlusion_report.png`
- Gap sweep/heatmap: `outputs/rt2a_occlusion_report/rt2a_gap_sweep_report.png`
- 수치 데이터: `outputs/rt2a_occlusion_report/summary.json`
- 재생성 스크립트: `scripts/generate_rt2a_occlusion_report.py`

## 현재 한계와 다음 단계

- RT-2A는 CAD를 불투명 차폐체로만 처리한다.
- 반사율, 흡수율, 산란 모델은 아직 차폐 판정에 사용하지 않는다.
- 다음 단계는 `RT-2B`로, 최초 충돌 face에서 Part Assignment/Face Override를 해석해 optical property를 결정한다.
