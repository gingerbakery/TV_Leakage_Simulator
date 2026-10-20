# PERF-5D BVH 준비 비용과 캐시 검증

## 현재 소스의 재검증

검증 도구를 보완한 후 현재 고정 소스로 다시 실행한 결과는
[검증 보완 보고서](2026-10-19_perf5d-validation-repair.md)에 정리했다.
새 준비 감소율은 TV 41.1%, CASE2 34.9%이며 160회귀와 GPU sanitizer를
메인이 새로 실행했다. 아래는 이전 44%/34% 측정과 당시 검토의 이력이다.
전체 보조 집계 32 ULP 미통과 및 LT 미검증은 새 실행에서도 유지한다.

## 독립 검토 후 한계 보강

2026-10-19의 [독립 검토](2026-10-19_perf5d-independent-review.md)에서 TV
실행 기록의 `gpu_cuda_pair_scene.py` hash가 현재 소스와 다름을 확인했다.
아래 TV 약 44% 단축은 당시 기록의 수치이며 현재 버전의 통과 증거로 그대로
승계하지 않는다. CASE2의 기록된 소스 hash는 현재 snapshot과 일치한다.
TV의 Receiver hit는 0이므로 비영 수광량 검증과도 구분한다.

ULP 비교기의 부호 경계 오류와 실제 후보 BVH 선택을 강제하지 않는 검증
조건도 발견했다. 제품 계산 오류로 확정한 것은 아니며 보완·재검증 전까지
현재 버전에 대한 전체 승인은 보류한다. 아래 수치는 새 실행 없이 기존
기록으로 보존한다.

## 이번 적용 범위

PERF-5D의 첫 후속 적용으로 GPU 보조 BVH의 생성 비용을 줄이고, 형상이
변경됐을 때 오래된 보조 BVH 메모리가 남지 않도록 수정했다. 이미 적용된
16구간 SAH 분할은 유지한다. 탐색 트리의 모든 배열과 순서 metadata는
수정 전과 byte 단위로 같다.

TV 코너 CAD의 보조 BVH 생성은 **2.848초에서 1.608초로 약 44% 단축**됐다.
사용자 CASE2는 **0.199초에서 0.131초로 약 34% 단축**됐다. 기존 트리를
재사용하는 반복 Ray Tracing 시간은 사실상 유지된다. 이번 수치를 Ray
Tracing 전체의 추가 1.77배 가속으로 해석하지 않는다.

CPU 교차 kernel, GPU 교차 kernel, 광원 분포, 난수, 면 속성, 반사 상한,
종료 정책, 광량 누적, nit 환산은 변경하지 않았다. CPU 탐색 구조의 추가
개선과 wide BVH는 다음 5D 검토 범위이며 5D 전체 완료는 아니다.

## 구현

- 내부 노드마다 모든 하위 삼각형을 다시 훑던 경계 계산을 제거했다.
  삼각형의 경계는 leaf에서 한 번 읽고, 깊이별로 자식 경계를 묶어 합친다.
- 삼각형 두 개의 중앙 분할은 작은 NumPy 배열을 반복 생성하지 않고
  원래의 최장 축과 안정적인 입력 순서를 유지하는 전용 경로를 사용한다.
  cutoff 2에서 SAH가 적용되는 경우는 원래 계산을 유지한다.
- 형상 무효화 시 `_gpu_pair_scene_cache`도 비운다. 기존에는 다음 GPU
  준비 때까지 이전 보조 tree가 남을 수 있었다. 배열의 약한 참조로
  무효화 후 실제 참조가 해제되는지 검사했다.
- 면 속성만 바꾸면 불변 형상은 재사용하고 광학 값은 새 설정을 사용한다.
  작은 장면과 CPU 경로에 GPU 보조 tree를 강제로 생성하지 않는다.

## 실행 환경과 비교 기준

- 전달 경로: source checkout, `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`
  및 이전 PERF 개선분을 포함한 로컬 미커밋 코드.
- 대조군: 이번 변경 직전의 로컬 SAH 구현을 보존한 소스 snapshot.
  과거 Git 기준 버전이나 LT와의 속도 비교가 아니다.
- 기존 GPU launcher가 준비한 Python 3.13.3 x64, NumPy 2.4.6,
  Numba 0.66.0, llvmlite 0.48.0, CUDA Toolkit 13.1 환경을 사용했다.
- 장치: NVIDIA GeForce RTX 3070, compute capability 8.6.
- Production preflight의 `available`, `strict_float64`, `kernel_executed`,
  `kernel_verified`는 모두 true. `preflight_scope=production_ray_bvh`,
  `provider_contract=strict_float64_bvh_v1`을 확인했다.
- 동일 프로세스·동일 장면에서 수정 전/후 순서를 교대했다. 최초 실행을
  별도로 기록하고 warm 3회의 중앙값을 사용했다.

| 조건 | TV 코너 CAD | 사용자 CASE2 |
| --- | ---: | ---: |
| 해석 삼각형 | 50,944 | 4,106 |
| Ray 수 | 1,000,000 | 1,000,000 |
| 반사 상한 | 10 | 100 |
| 광원 | Datum Plane, Lambertian | Datum Plane, Aim Area |
| 면 속성 | Mixed, 반사율 60% | 저장된 면별 설정 유지 |
| Seed | 42 | 42 |
| Receiver | 40 × 24 cells | 5 × 3 mm, 25 × 15 cells |
| 경로 저장 | 끔 | 최대 2,000개 |

CASE2는 사용자가 제공한 `CASE2_ASSY_6bodies-1(acc).bitsam`을 임시 별도
runtime에 복원했다. 원본 파일과 실행 중인 페이지는 변경하지 않았다.
저장된 정밀 형상·광학 profile·지정 우선순위·제외 부품·Aim 설정·수광면을
사용하고, 검증 Ray 수만 원래 5,000만에서 100만으로 낮췄다. 원본 파일의
SHA-256과 실제 입력/실행 근거는 결과 JSON에 남겼다. 이 fixture에는 ROI와
transform이 없으며 일반적인 ROI 검증은 별도 회귀 검사에 포함했다.

## 성능

| 측정 구간 | 조건 | 수정 전 | 수정 후 | 판정 |
| --- | --- | ---: | ---: | --- |
| 보조 BVH 재생성 중앙값 | TV 코너 | 2.847853초 | 1.607681초 | 약 44% 단축 |
| 보조 BVH 재생성 중앙값 | CASE2 | 0.199260초 | 0.130691초 | 약 34% 단축 |
| 캐시 사용 전체 해석 중앙값 | TV 코너 100만 Ray | 0.791684초 | 0.792549초 | 약 0.1% 차이, 유지 |
| 캐시 사용 전체 해석 중앙값 | CASE2 100만 Ray | 1.678566초 | 1.655782초 | 약 1.4% 차이, 유지 |

최초 보조 tree 생성은 TV 코너 2.740902→1.745049초, CASE2
0.210306→0.128144초였다. JIT를 포함한 최초 전체 해석은 각각
14.936211→2.627449초, 17.876370→1.894516초로 기록됐지만, 뒤에 실행한
조건이 이미 컴파일된 공통 kernel을 재사용하므로 이 비율을 가속률로
사용하지 않는다. CAD 파싱과 viewer 로딩을 빠르게 한 결과도 아니다.

두 장면 모두 수정 전/후 첫 실행과 warm 3회, 총 16회가 `gpu_active`였고
실행마다 CUDA 성공/시도는 16/16이었다. `compute_execution_reason`은 null,
CPU hybrid와 resident fallback은 0이었다. 보조 BVH 선택도 각 16 batch였다.

형상 배열 크기는 TV 코너 16,776,704 bytes, CASE2 1,271,602 bytes로 같았다.
GPU resident workspace peak도 각각 53,609,296 및 121,247,488 bytes로
변하지 않았다. 캐시가 유효한 반복 실행의 보조 tree 준비 시간은 0이다.

## 정확도와 반복성

### 그대로 유지된 항목

두 실제 형상에서 수정 전/후의 삼각형 좌표·edge·추적 mask·모든 BVH
노드·면 순서·원래 탐색 순서 metadata·stack width의 전체 hash가 같다.
분할 규칙과 tie 순서, signed zero, trace 제외, cache 무효화도 검사했다.

100만 Ray 실제 GPU 비교에서 Ray 수·Receiver hit·surface hit 등 모든
이산 결과가 일치했다. CASE2는 모든 실행에서 Receiver hit가 219개,
surface hit가 5,527,604개였다. Receiver 지표와 heatmap은 엄격 기준인
절대·상대 `1e-12` 및 최대 32 ULP를 모두 통과했다. 관찰된 차이는 최대
1 ULP이며, 해당 heatmap cell의 차이는 `6.776263578034403e-21 lm`이다.
수정 전 버전끼리 반복해도 동일한 cell에서 같은 1 ULP 차이가 관찰됐다.

### 모든 보조 집계의 bit 일치와는 구분

초기 검사는 전체 결과의 완전한 bit 일치를 요구해 실패했다. 보존된 진단은
`outputs/perf5d_bvh/diagnostic.difference.json`에 있다. 이후 수정 전끼리와
수정 후끼리 반복한 값을 별도로 비교했다. 기존 GPU 병렬 누적을 사용하는
반사 단계·profile별 광량과 작은 잔여 광량에서도 반올림 차이가 나타났다.

수정 전/후 비교의 전체 수치 최대 절대 차이는 TV 코너 `6.62e-14`, CASE2
`4.17e-12`였다. 수정 전 반복에서도 각각 `2.98e-14`, `6.08e-12`의 차이가
나타났다. 이전 CAD 검증에서 사용한 이산 exact·물리량 절대/상대 `1e-9`
기준은 모두 통과했다. 제품의 계산 허용오차나 누적 방식을 바꾼 것은 아니다.

**전체 보조 집계까지 32 ULP 이하라는 검사는 두 버전 모두 통과하지 못한다.**
특히 작은 잔여값은 뺄셈에 따른 상대오차와 ULP 수가 커진다. 이 부분을
이번 5D의 엄격 정합 완료로 표시하지 않으며, 전체 GPU 집계의 bit 결정성이
필요하면 별도 누적 순서 개선으로 다룬다. 탐색 트리와 교차 kernel이 같고
수정 전 반복에서도 재현되는 점을 근거로 이번 BVH 준비 변경이 새로운
광선 누락이나 광학 경로 차이를 만든 것으로 판단하지 않는다.

## 회귀 검사

관련 11개 모듈의 **133개 검사가 통과**했다. 실패·오류·skip은 0,
실행 중 소스·테스트·검증 스크립트 hash는 유지됐다. 전체 소요 시간은
122.46초다. 전체 저장소 테스트를 모두 다시 실행했다는 뜻은 아니다.

검사 범위는 신규 준비/캐시 6검사, 기존 GPU pair BVH·SAH, CPU 교차 및
fused transport, ROI/transform/면 속성, 미소 간격, Receiver 거리 경계,
실제 1,000회 반사, 중단 및 실패 후 재실행이다.

NVIDIA Compute Sanitizer 2025.4.1에서는 실제 GPU 4검사를 각각 다시
실행했다. 미소 간격, CAD 속성 재편집, ROI custom/MIS, 실제 1,000회
반사와 중단을 포함한다.

| 검사 | 테스트 | 결과 |
| --- | ---: | --- |
| memcheck | 4/4 | 오류 0 |
| synccheck | 4/4 | 오류 0 |
| racecheck | 4/4 | 경합 0, 오류 0, 경고 0 |

## 결과 파일과 다음 단계

- `outputs/perf5d_bvh/preflight.json`
- `outputs/perf5d_bvh/reference/`: 변경 직전 비교 소스
- `outputs/perf5d_bvh/preparation-and-transport.json`
- `outputs/perf5d_bvh/case2-transport.json`
- `outputs/perf5d_bvh/regression.json`
- `outputs/perf5d_bvh/sanitizers.json`, 각 sanitizer 로그
- `scripts/benchmark_perf5d_bvh_preparation.py`: 비교 재실행 도구
- `tests/test_perf5d_bvh_preparation.py`: 새 회귀 검사

다음 5D는 CPU의 탐색 비용과 leaf/layout·선택적 wide BVH를 별도 후보로
검증한다. 원래의 근접 면 선택과 FP64 판정을 유지하고 실제 전체 해석이
빨라질 때만 채택한다. 기존의 무기한 최소 2배 목표를 자동 재개하거나
OptiX/Embree 단계로 넘어가지 않았다.

8788/8789 서버는 재시작하지 않았으며 커밋·push도 하지 않았다. 이미
실행 중인 서버에 새 준비 코드가 적용되려면 작업 저장 후 재시작해야 한다.
