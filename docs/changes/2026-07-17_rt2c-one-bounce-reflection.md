# RT-2C Specular/Gaussian/Lambertian 1회 반사 구현

## 변경 목적
- 최초 CAD surface 충돌 이후 실제 반사/산란 방향 ray를 생성한다.
- 반사 ray가 Receiver에 도달하는지 또는 다른 기구물에 다시 차폐되는지 계산한다.
- V1 표면 모델을 Specular, Gaussian, Lambertian 세 가지로 고정해 상대 밝기와 분포 비교를 시작한다.

## Backend 구현
- `src/leakage_simulator/reflection.py` 추가.
- 이상적인 정반사 벡터 구현.
- cosine-weighted Lambertian hemisphere sampling 구현.
- 정반사축 주변 isotropic Gaussian lobe sampling 구현.
- `mixed` profile의 Gaussian glossy/Lambertian 확률 선택 구현.
- RT-2B의 `outgoing_energy_lumen`을 반사 ray 광량으로 그대로 사용.
- 반사 후 Receiver와 secondary CAD surface 교차 거리 재비교.
- 두 번째 surface가 먼저 맞으면 ray 종료.
- `max_depth=0`에서는 기존 직접광/차폐 동작 유지.
- `max_depth>=1`에서는 최대 한 번 반사.

## 결과 계약
- `RayHit.ray_kind` 추가:
  - `direct`
  - `specular`
  - `gaussian`
  - `lambertian`
- `_reflection_summary` 추가:
  - direct/reflected Receiver hit와 flux
  - 반사 emitted/blocked/escaped 수
  - lobe별 emitted flux, Receiver flux, 차폐/이탈 수
- 반사 경로는 `Emitter → first surface → Receiver/secondary surface` 이벤트 배열로 저장.

## Web UI
- Web UI 버전: `v0.9.8`
- 실행 깊이를 1회 반사로 변경.
- Result에 direct/reflected flux와 lobe별 통계를 표시.
- 3D ray path 색상:
  - direct Receiver: 녹색
  - 최초 surface 진행: 파란색
  - Specular: 주황색
  - Gaussian: 청록색
  - Lambertian: 보라색
- Receiver heatmap은 direct + 1회 반사 누적 결과를 표시.

## 자동 검증
- 이상적인 반사각 법칙 검증.
- 반사율 0.2/0.8에서 Receiver flux 4배 비례 검증.
- `max_depth=0` RT-2A 호환성 검증.
- Gaussian sigma 증가 시 작은 Receiver hit 감소 검증.
- Lambertian cosine-weighted 평균 `cos(theta) ≈ 2/3` 검증.
- mixed profile lobe 선택 비율 검증.
- 반사 후 secondary blocker 차폐 검증.

## 그래픽 검증
- 모델별 ray 수: 25,000.
- 총 입사 power: 1 lumen.
- reflector 반사율: 0.5.

결과:
- Specular: Receiver hit 100%, 약 0.500 lumen.
- Gaussian 12도: Receiver hit 100%, 약 0.478 lumen.
- Lambertian: Receiver hit 약 83.2%, 약 0.310 lumen.
- secondary blocker:
  - blocker 없음: 5,000 hit
  - blocker 있음: 0 hit, 반사 후 5,000 ray 차폐

산출물:
- `scripts/generate_rt2c_reflection_report.py`
- `outputs/rt2c_reflection_report/rt2c_reflection_report.png`
- `outputs/rt2c_reflection_report/summary.json`

## 현재 한계
- 다중 bounce는 아직 수행하지 않는다.
- Fresnel, Microfacet, Oren–Nayar, anisotropic, measured BSDF는 V2 백로그다.
- Receiver flux의 절대 nit 정합은 향후 실측 보정이 필요하다.

## 다음 단계
- RT-2D에서 direct/reflected/component 기여도를 분리하고 결과 리포트와 3D 경로 해석성을 고도화한다.
