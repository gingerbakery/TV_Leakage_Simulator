# 빛샘 시뮬레이터 요구사항

## 배경
- 작성일: 2026-07-07
- 목표:
  - TV 기구부 gap에서 발생하는 빛샘을 설계 단계에서 빠르게 예측
  - 설계 대안 간 상대 비교 및 대략 절대 밝기 비교 지원
- 범위:
  - V1 우선
  - M2/M3(분광/시감도/색도 고도화)는 보류

## 기능 범위 (V1)

### 1. CAD 입력 + ROI
- 지원 입력:
  - `stp`
  - `step`
  - `x_t`
  - `obj`
  - `stl(ascii)`
- ROI는 receiver 대상 face/patch로 지정 가능

### 2. Gap / 공차 모델
- gap을 통해 누설 경로 활성화
- 면 기반 gap rule
- 평균/표준편차 기반 샘플링
- move/tilt 기반 근사 gap 생성

### 3. Material / Scatter
- 기본 material library
- black powder aluminum
- black PC resin
- matte ABS
- white reference

### 4. 광원 모델
- face emitter
- volume box emitter
- volume sphere emitter
- 방향 분포:
  - isotropic
  - uniform_toward_normal
  - random_cosine

### 5. 경량 ray tracing
- Specular / Lambertian / Gaussian / Mixed 반사·산란
- 최대 반사 depth 0~20
- 누설 경로 누적 및 감쇠 계산
- CPU와 NVIDIA CUDA 실행 경로
- 작은 Receiver의 표본 효율을 위한 primary Receiver MIS
- 순수 Lambertian 반사광의 Receiver-directed bounce MIS
- MIS는 기본 비활성화하며, 미지원 분포는 source sampling으로 안전 fallback

### 6. 결과
- receiver별:
  - peak
  - mean
  - p95
  - area_above_threshold
  - hit_count
- `Nits_est` 기반 상대/대략 절대 밝기 추정
- JSON / CSV / PNG 출력

## 제외 항목 (현재)
- 정밀한 분광 모델
- 정밀 시감도 보정
- 고급 PBR 렌더 엔진
- 고정밀 변형 해석

## 수용 기준
1. 최소 입력으로도 빠르게 실행 가능
2. gap=0일 때 누설 지표가 명확히 감소
3. 동일 seed/ray에서 설계안 순위가 일관적
4. 결과 파일(JSON/CSV/PNG)이 자동 생성

## 변경 이력

- 2026-08-25: 다회 반사 0~20, CUDA 실행, primary Receiver MIS,
  Lambertian bounce MIS와 명시적 fallback 요구사항을 현재 구현에 맞게 반영했다.
