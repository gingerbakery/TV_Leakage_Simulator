# Ray Tracing 계산 고속화 및 정확도 향상 방안

## 목적

Receiver Heatmap 해상도를 높이고 수렴성을 확보하기 위해 Ray 수와 Resolution을 증가시키면 계산 시간이 크게 늘어난다. 본 문서는 도구의 복잡성을 과도하게 높이지 않으면서 계산 시간을 단축하고, 결과의 정밀도와 실제 현상 재현 정확도를 함께 개선하기 위한 개발 방향을 정리한다.

## 우선 적용 권장 기능

### 1. 계산 품질 프리셋

사용자가 세부 옵션을 매번 조정하지 않도록 다음 세 가지 실행 모드를 제공한다.

| 모드 | 용도 | 권장 동작 |
|---|---|---|
| Preview | 구조와 대략적인 Ray 경로의 빠른 확인 | 낮은 Ray 수, 낮은 Heatmap Resolution, 제한된 Stored Paths |
| Standard | 일반 구조 비교 및 반복 검증 | 중간 Ray 수와 Resolution, 자동 수렴 사용 |
| Final | 최종 보고서와 정밀 비교 | 높은 Ray 수와 Resolution, 엄격한 수렴 기준 |

프리셋을 선택해도 전문 사용자는 세부 설정을 직접 변경할 수 있도록 한다.

### 2. 계산 대상 Receiver 선택

여러 Receiver가 등록되어 있더라도 현재 분석에 필요한 Receiver만 선택해 계산할 수 있도록 한다. 같은 구조에서 Receiver 1, Receiver 2를 각각 검토할 때 불필요한 Heatmap 누적과 결과 집계를 줄일 수 있다.

### 3. Adaptive Heatmap

처음부터 Receiver 전체를 고해상도로 계산하지 않고 다음 단계로 처리한다.

1. 낮은 해상도로 전체 Receiver를 계산한다.
2. 빛이 도달하거나 휘도 변화가 큰 영역을 찾는다.
3. 해당 영역만 셀을 세분화해 추가 계산한다.
4. 최종 결과에서는 셀 면적을 반영해 Peak, Mean, Flux 및 광영역을 산출한다.

빛이 거의 없는 넓은 영역에 동일한 계산량을 소비하지 않으므로 국부 빛샘 분석에 특히 효과적이다.

### 4. CPU 병렬 계산

Ray 묶음을 CPU 코어별 작업으로 나눈 뒤 결과를 합산한다. 현재 적용된 BVH는 개별 Ray와 삼각형의 교차 탐색을 줄이고, CPU 병렬화는 여러 Ray를 동시에 처리한다. 두 기능은 서로 대체 관계가 아니며 함께 사용할 때 효과가 크다.

병렬화 시 다음 결과가 단일 실행과 통계적으로 동일한지 검증해야 한다.

- Receiver별 도달 Ray 수
- Total Receiver Flux
- Peak/Mean nit
- Heatmap 셀 누적값
- 반사 횟수와 Component 기여도
- 동일 Seed 조건의 재현성

## 추가 고속화 방안

### BVH 캐시 활용

CAD 형상, ROI, Transform, Trace 제외 조건이 바뀌지 않았다면 기존 BVH를 재사용한다. Material 광학값, Emitter 출력, Receiver 조건, Ray 수만 변경한 경우에는 형상 가속 구조를 다시 만들 필요가 없다.

### Stored Path와 상세 통계 분리

Stored Paths는 시각화용 대표 경로이므로 전체 계산 Ray 수와 독립적으로 제한한다. Preview에서는 적게, Final에서는 필요한 수준으로 늘린다. Component 상세 기여도와 Representative Sequences도 사용자가 요청한 경우에만 상세 저장하도록 하면 메모리와 후처리 시간을 줄일 수 있다.

### 벡터화 및 JIT 최적화

Ray 생성, 방향 계산, Receiver 좌표 변환, Heatmap 누적처럼 반복량이 큰 구간을 배열 단위로 처리한다. 프로파일링 결과 Python 반복문 비중이 높다면 NumPy 벡터화 또는 Numba JIT 적용을 검토한다.

### Importance Sampling

Receiver에 도달할 가능성이 높은 방향이나 Specular lobe에 Ray를 더 많이 배분하고, 통계 가중치를 적용해 편향 없는 결과를 만든다. 단순 균등 방출보다 Receiver 도달 Ray가 적은 구조에서 효율적이지만, 가중치 검증이 필요하므로 후순위로 적용한다.

## 정밀도와 정확도의 구분

- **정밀도 개선**: Ray 수 증가, 작은 Pixel Size, 높은 Resolution, 수렴 기준 강화처럼 Monte Carlo 노이즈를 줄이는 작업이다.
- **정확도 개선**: 실제 Material 반사율, Specular/Diffuse 분포, Gaussian 폭, Emitter 휘도와 방향, Receiver 방향·면적·Acceptance Angle, CAD 간극을 실물과 맞추는 작업이다.

Ray 수를 늘려 Error Estimate가 낮아져도 Material이나 광원 조건이 실제와 다르면 실물 빛샘을 정확히 재현하지 못한다. 따라서 정밀도와 정확도를 별도로 검증해야 한다.

## 정확도 향상 점검 항목

### Material

- 측정 파장별 Reflectance와 Absorption 적용
- Specular/Diffuse 비율 적용
- SECC 등 Gaussian Scattering 재질의 분포 폭 적용
- Reflectance + Loss 에너지 보존 검증
- 임의 기본값과 실측값 구분 표시

### Emitter

- 완제품 휘도와 선택 면적의 관계 확인
- 다중 CAD Surface의 면적 가중 방출 및 면별 Normal 사용
- 광원의 각도 분포 검증
- 선택한 CAD Face가 변경되지 않았는지 확인

### Receiver

- Front 방향과 X+/Y+ 축 확인
- Acceptance Angle 확인
- CAD Surface 실제 면적과 Datum Plane 크기 확인
- Heatmap Pixel Size와 Resolution의 상호 계산 확인
- 구조 비교 시 동일 Receiver 조건 사용

### Geometry

- CAD 단위와 Scale 확인
- 실제 간극과 부품 위치 확인
- Trace On/Off 및 Emitter-only Component 조건 확인
- 곡면 Tessellation 품질과 누락된 면 확인

## 권장 실행 절차

1. Preview 모드로 Emitter 방향, Receiver 방향, ROI, Trace 조건과 주요 반사 경로를 확인한다.
2. 선택 Receiver만 Standard 모드로 계산한다.
3. Error Estimate와 Peak-area Error를 확인한다.
4. 수렴하지 않은 경우 전체 Resolution을 즉시 높이기보다 Ray 수 또는 문제 영역의 해상도를 단계적으로 높인다.
5. 구조 후보를 비교한 뒤 최종 후보만 Final 모드로 계산한다.
6. 실제 측정값이 있다면 Peak nit뿐 아니라 위치, 광영역, Total Flux와 경로를 함께 비교한다.

## 개발 우선순위

1. Preview / Standard / Final 프리셋
2. 계산 대상 Receiver 선택
3. Adaptive Heatmap 및 영역별 수렴
4. 프로파일링 후 CPU 병렬화와 벡터화
5. Importance Sampling

초기 구현은 1~4번에 집중하는 것이 기능 복잡도 대비 체감 성능 개선 효과가 가장 크다.

## PERF-3B 구현 상태

- Batch 교차 입출력 계약과 CPU reference adapter를 완료했다.
- Virtual-plane `max_depth <= 1` 실행 경로는 명시적 runtime `batch` 요청에서
  primary/secondary wavefront batch를 사용한다. native backend 전까지 기본
  `auto`는 scalar를 유지한다.
- 같은 seed에서 Receiver grid, flux, contribution, reflection summary와
  stored path가 scalar 실행과 exact 일치한다.
- face/polygon emitter와 multi-bounce는 reflection RNG 순서를 보존하기 위해
  아직 scalar 실행을 사용한다.
- 현재 reference batch는 native 가속이 아니어서 100,000-ray synthetic의
  최선 4,096 chunk 기준 scalar 대비 처리량이 약 28.9% 낮다.
- 다음 성능 단계는 동일 계약 뒤에 native CPU kernel을 연결해 Python BVH
  traversal과 ray-AABB 호출 병목을 제거하는 것이다.
- GPU backend는 capability/precision/fallback 계약까지 준비한 뒤 추가한다.
