# PERF 5C GPU 재검산 분리 실행 검증

후속의 기본 소스 개선과 고정 기준 재측정은
[`면 속성 준비 비용 개선`](2026-10-19_perf5b-optical-routing-cache.md)을 따른다.
아래 후보의 성능과 미적용 결정은 당시 비교 결과다.

**GPU 전체 2배 목표는 아직 미완료다.** 재검산 광선만 GPU queue로 모으는
분리 실행과 일부 단계를 합치는 실행을 구현하고 비교했다. 원래 면 판정과
광학 결과는 검사 범위에서 보존했지만, 전체 시간의 채택 기준은 통과하지
못했다. 이 후보들은 기본 프로그램에 적용하지 않았다.

고정 기준 `157230c6` 대비 기존 로컬 작업본의 CPU 자동 7조건은
2.111~4.882배, GPU 복잡 CAD는 1.309배, 확률 반사는 1.761배다.
이번에는 이 기본 수송 소스를 바꾸지 않았으므로 앞선 고정 기준 결과와
아래 후보 비교를 곱해 새로운 제품 가속비로 주장하지 않는다.

## 분리 실행 구현

기존 면 선택을 보존하는 빠른 BVH와 GPU 재검산을 사용한다. 같은 거리에
가까운 면 후보가 있으면 원래 BVH로 판정하는 보호를 제거하지 않았다.

- 빠른 교차 질의를 실행하고 불확실한 primary 번호만 GPU queue에 압축한다.
- 별도 GPU kernel이 압축된 질의를 원래 BVH로 계산한다.
- 확정된 결과로 원래 FP64 반사 계산을 수행하고 다음 active queue를 만든다.
- 비교 후보는 반사 계산과 다음 교차 질의를 합쳐 단계별 호출 수를 줄인다.
  이 후보는 원래 primary 행에서 종료 여부를 확인하고 재검산 queue만 압축한다.

Queue 순서를 광선 번호나 난수 seed로 사용하지 않는다. 원래 primary 행에
event와 terminal을 기록하고, Receiver 최대 교차 거리도 원래 scan으로
계산한다. 광원 분포, MIS weight, 표면 속성과 반사 허용 횟수는 유지한다.
매 단계의 active 또는 재검산 개수를 CPU로 내려받아 정렬하지 않는다.

GPU 입력 사본은 중간 수송 상태로 재사용하지만 host의 초기 packet은
수정하지 않는다. 기록 한도 재시도, 표시 path 재추적과 CPU 오류 복구에는
원래 입력을 다시 사용한다. Kernel 호출 깊이는 실제 기록 한도에 맞춰 제한해
상한 1,000에서 기록 공간을 넘은 뒤 빈 단계들을 계속 호출하지 않도록 했다.

## 같은 세션 성능 비교

각 후보를 같은 장면에서 교대로 실행한 warm 7회 중앙값이다. CAD는
100,000 primary와 50,944 triangle, 나머지는 1,000,000 primary다.
CAD import와 추가 BVH 준비는 표의 해석 시간에서 분리한다.
아래 비율은 **현재 GPU 경로 대비**이며 고정 Git 기준 대비 값이 아니다.

| 조건 | 현재 GPU | 3단계 분리 | 속도비 | 반사와 다음 질의 통합 | 속도비 |
| --- | ---: | ---: | ---: | ---: | ---: |
| TV 코너 CAD | 0.166629초 | 0.165039초 | 1.010배 | 0.194007초 | 0.859배 |
| 확률 2회 반사 | 0.167609초 | 0.196631초 | 0.852배 | 0.196710초 | 0.852배 |
| 갇힌 구조 10회 반사 | 0.223543초 | 0.339293초 | 0.659배 | 0.316442초 | 0.706배 |

분리 실행의 CAD 약 1% 차이는 목표 달성으로 판정하지 않는다. 다른 장면이
크게 느려졌으며 전체 대표 조건의 회귀 기준도 통과하지 못했다.
Kernel을 합쳐 호출 수를 줄여도 이번 측정에서는 CAD가 오히려 느려졌다.
Register 수는 기준 114개, 분리 query 83개, 재검산 77개, 반사 94개,
통합 96개다. Register 감소 자체도 가속의 증거가 아니다.

65,536개 logical packet의 추가 GPU workspace는 2,359,488 bytes다.
CAD의 추가 BVH 준비는 1.381초였다. 해당 세션의 첫 호출은 기준 5.642초,
분리 5.311초, 통합 5.228초로 별도 기록했다. 첫 호출의 JIT 비용을 warm
성능으로 표현하지 않는다.

## 재검산 이유 계측

별도 계측 실행은 timing benchmark가 아니다. 원래 GPU 경로와 host ordered
집계의 공개 결과를 비교해 bit 단위 일치도 확인했다.

| 항목 | 질의 수 |
| --- | ---: |
| 전체 논리 교차 질의 | 1,099,987 |
| 기존 BVH 재검산 | 214,170 |
| 거리 차이 1e-10 mm 이내의 면 후보 | 214,167 |
| 빠른 탐색 miss | 3 |
| 삼각형 barycentric 경계 | 0 |
| 최소 거리 또는 Receiver 거리 경계 | 0 |
| Stack overflow 또는 거의 평행한 방향 | 0 |

이번 CAD에서는 재검산의 약 99.9986%가 가까운 거리의 면 후보 때문이었다.
이 수치만으로 모두 중복 CAD 면이거나 같은 물성이라고 결론 내리지 않는다.
미세 간격의 서로 다른 물성을 가진 면을 구분하는 보호가 포함돼 있으므로,
단순히 거리 허용오차를 줄이거나 재검산을 끄는 방식은 채택하지 않는다.

## 같은 탐색 순서의 연산 단순화 후보

별도 후보는 원래 BVH와 triangle 순서, 동률 판정을 유지하며 축에 정렬된
triangle의 0 곱셈만 줄였다. 좌표와 방향의 범위가 안전 조건을 벗어나면
원래 식을 사용한다. FP32 또는 fast math로 바꾸지 않았다.

| 조건 | 현재 GPU | 축 정렬 연산 후보 | 속도비 |
| --- | ---: | ---: | ---: |
| TV 코너 CAD 100K | 0.163590초 | 0.197140초 | 0.830배 |
| 확률 2회 반사 1M | 0.148268초 | 0.142162초 | 1.043배 |
| 갇힌 구조 10회 반사 1M | 0.221449초 | 0.219834초 | 1.007배 |
| Face 직달 1M | 0.179836초 | 0.171422초 | 1.049배 |

이 역시 다른 세션의 교대 warm 7회 결과다. 일부 조건의 약 4~5% 차이에도
CAD가 느려져 기본에 적용하지 않았다. 3,600 scalar 질의에서 축 교환,
signed zero, 미세 간격과 큰 좌표 fallback의 면 번호 및 거리 비트를 비교했다.
별도 GPU 4검사에서 CAD 경로, 물성 재편집, 미세 간격, 실제 1,000회 반사도
원래 결과와 같았다.

## 정확도와 복구 검증

통합 337검사 통과, 359.043초다. 새 분리 실행 검사 19개와 기존 318개를
함께 실행했다. 축 정렬 후보의 4개 검사는 별도로 통과했다.
실제 장치 memcheck 6검사, 83.962초와 synccheck 2검사, 26.349초에서도
오류 0건을 확인했다. 최종 세 결과의 source 및 script hash도 현재 파일과
일치한다.

분리와 통합 두 방식 모두 다음을 확인했다.

- `5e-11 mm` 간격의 경면과 흡광면에서 원래 면 선택과 수광 결과 유지.
- 두 면 사이의 Receiver 최대 거리로 교차 질의를 제한하는 순서 유지.
- ROI 면 override와 specular, Lambertian, Gaussian, mixed 재편집 반영.
- Face 광원의 원본 source face 제외와 부분 tail, MIS seed 및 표시 경로 유지.
- 실제 1,000회 반사에서 `17 × 1,001` surface hit와 기록 한도 재시도 유지.
- GPU state 변경 후 오류를 주입해도 host 초기 packet 불변 및 미반영 suffix만
  CPU로 재실행. 앞선 정상 GPU chunk는 중복 집계하지 않음.
- 기록 한도 재시도 중 Stop에서 미완료 event를 commit하지 않음.

오류 주입 검사는 의도한 GPU mixed 실행이다. 이를 fallback 0의 정상 GPU
성능 비교에 포함하지 않았다. 기본 atomic 집계의 미세한 합산 순서 차이와
LT 또는 실측 정합은 별도 항목으로 남는다.

## 실행 환경과 증거

Git source checkout `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`의 로컬
작업본이다. RTX 3070, driver 591.59, CUDA Toolkit 13.1, Python 3.13 x64와
Numba 0.66.0 환경에서 실행했다. 각 결과는 source와 script hash를 기록한다.

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

각 정상 성능 비교는 Datum Plane 또는 Face 광원, `compute_backend=gpu_cuda`,
BVH, `compute_execution_state=gpu_active`, CPU hybrid와 resident fallback
0이다. CAD CUDA 성공/시도는 2/2, 1M 조건은 16/16이다. 재검산 queue도
GPU에서 실행했다. CAD import, BVH 준비 또는 UI 전체가 가속됐다는 뜻이 아니다.

## 다음 개선점

분리 실행은 구현했지만 전체 가속 효과가 없어 기본 경로를 유지한다.
후속은 가까운 거리의 면 후보에서 원래 판정이 필요한 이유를 세분화하고,
기존 탐색 순서를 바꾸지 않는 경계 처리와 입력 준비의 반복 비용을 검토한다.
실제 기록한 면 번호, 물성, Receiver 거리 계약을 보존하는 것이 채택 조건이다.

UI, 서버, 설치 환경, 커밋과 push는 변경하지 않았다.

## 원본 결과

- `outputs/perf5_transport/compacted-frontier-fused-paired.json`
- `outputs/perf5_transport/guarded-replay-reasons.json`
- `outputs/perf5_transport/axis-triangle-paired.json`
- `outputs/perf5_transport/compacted-frontier-full-regression.log`
- `outputs/perf5_transport/axis-triangle-tests.log`
- `outputs/perf5_transport/frontier-axis-memcheck.log`
- `outputs/perf5_transport/frontier-axis-synccheck.log`
