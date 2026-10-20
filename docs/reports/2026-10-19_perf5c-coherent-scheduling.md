# GPU 광선 정렬 가속 후보 검증

## 결론

광선의 방향과 위치를 기준으로 GPU 작업 순서를 묶는 두 후보를 구현하고 검증했다.
TV 코너 CAD의 개선은 각각 1.016배와 1.022배에 그쳤으며, 다른 세 조건에서는
느려졌다. 따라서 **제품 기본 경로에는 적용하지 않는다**. 광원 샘플, 반사 물성,
정밀도 또는 Receiver 해상도를 바꿔 얻은 속도 개선은 없다.

이전 제품 개선을 포함한 고정 기준 비교는 CPU 대표 7조건에서 2.143~4.910배이며,
GPU TV 코너 CAD는 1.470배다. 이번 후보의 수치를 여기에 곱하지 않는다.
CPU와 GPU 모두 최소 2배라는 전체 목표는 아직 완료되지 않았다.
제품 적용 수치는 [광학 경로 캐시 보고서](2026-10-19_perf5b-optical-routing-cache.md)를 따른다.

## 환경과 비교 조건

- 전달 방식은 Git source checkout, `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`에
  로컬 미커밋 개선이 있는 상태다. 실행 환경은 기존 `run_web_gpu`가 준비한 전용 환경이다.
- NVIDIA GeForce RTX 3070 8GB, Python 3.13 x64, Numba 0.66.0,
  CUDA Toolkit 13.1이다. 서버와 사용자 CAD 세션은 재시작하지 않았다.
- 모든 측정에서 production preflight의 `available`, `strict_float64`,
  `kernel_executed`, `kernel_verified`가 true이며,
  `preflight_scope=production_ray_bvh`,
  `provider_contract=strict_float64_bvh_v1`을 확인했다.
- 같은 프로세스, 같은 장면, 같은 입력과 seed에서 원본과 후보 순서를 교대했다.
  각 조건의 첫 실행과 warm 7회를 기록했다. 아래 시간은 warm 중앙값이다.
- CAD는 100,000 rays, 나머지는 1,000,000 rays다. CAD·확률 반사·갇힌 구조는
  Datum Plane 광원, 직접 도달 조건은 Face 광원이다.
- 전 조건에서 `compute_execution_state=gpu_active`, CUDA 성공/시도는 CAD 2/2,
  나머지 16/16이다. CPU hybrid와 resident fallback은 모두 0이다.
  `monte_carlo_contract=cpu_gpu_deterministic_batch_v1`을 유지한다.
- 최초 JIT가 포함된 CAD 첫 실행은 방향 비교의 원본 5.619초, 64분류 후보
  4.772초, 공간 비교의 원본 5.545초, 공간 8분할 후보 5.204초다.
  개별 첫 실행 전체는 아래 원시 기록을 따른다.

## 방향 정렬

방향을 1개, 8개 또는 64개 그룹으로 분류하고, 다음 반사 단계에서 활성 광선만
GPU에서 묶는다. 호스트로 활성 광선 수를 매 단계 내려받지 않는다.
원래 BVH, 원래 삼각형 순서, 원래 primary ID와 난수 key를 유지하고, 사건 기록은
원래 primary별 행에 쓴다. 단일 그룹은 정렬 자체의 추가 비용을 확인하는 대조군이다.

| 조건 | 원본 초 | 1그룹 초 | 8그룹 초 | 64그룹 초 | 64그룹 속도비 |
| --- | ---: | ---: | ---: | ---: | ---: |
| TV 코너 CAD | 0.149833 | 0.151178 | 0.150605 | 0.147542 | 1.016배 |
| 확률 2회 반사 | 0.178593 | 0.208339 | 0.220660 | 0.226507 | 0.788배 |
| 갇힌 구조 상한 10 | 0.244491 | 0.342378 | 0.337007 | 0.339380 | 0.720배 |
| Face 직접 도달 | 0.241548 | 0.268006 | 0.276347 | 0.267521 | 0.903배 |

원시 기록: `outputs/perf5_transport/coherent-directions-paired.json`.
각 분류의 최초 시간, warm 7개 값, CUDA 증거, 물리량 비교와 source hash를 포함한다.

## 위치와 방향 정렬

원래 BVH의 root 범위를 축마다 4개 또는 8개로 나누고 방향 8분류를 결합했다.
총 512개 또는 4,096개 그룹이다. 정수 histogram과 prefix scan만 병렬 처리하며,
광량 합산 순서는 바꾸지 않는다. prefix scan은 256 thread의 단일 block에서
명시적인 동기화를 사용한다. 위치 분류는 유한 범위로 clamp하고, 광선의 실제
좌표와 방향은 수정하지 않는다.

| 조건 | 원본 초 | 공간 4분할 초 | 공간 8분할 초 | 4분할 속도비 | 8분할 속도비 |
| --- | ---: | ---: | ---: | ---: | ---: |
| TV 코너 CAD | 0.149152 | 0.147153 | 0.145890 | 1.014배 | 1.022배 |
| 확률 2회 반사 | 0.189870 | 0.194555 | 0.210730 | 0.976배 | 0.901배 |
| 갇힌 구조 상한 10 | 0.223754 | 0.333257 | 0.329222 | 0.671배 | 0.680배 |
| Face 직접 도달 | 0.243830 | 0.263543 | 0.271681 | 0.925배 | 0.897배 |

원시 기록: `outputs/perf5_transport/spatial-coherent-paired.json`.
진단 중 내부에서 사용한 임시 별칭과 실제 공간 분할 수의 대응도 기록했다.

## 정확도와 제어 흐름

두 비교의 모든 이산 결과가 일치하고, 광량·heatmap은 FP64 절대/상대
`1e-9` 기준을 통과했다. GPU atomic 누적은 bit-exact 보장이 아니며,
ordered host 누적기를 사용한 별도 회귀에서는 사건 경로와 공개 결과를 exact로 확인했다.

방향 후보와 위치 후보는 각각 18개의 전용 테스트를 통과했다. 범위는 다음과 같다.

- `5e-11 mm` 미세 간격에서 mirror와 absorber의 기존 면 판정 유지.
- 미세 간격 사이의 Receiver cap, source ignore, partial warp와 8,193개 tail.
- 원래 광원 방향에 대한 histogram, 단계별 활성 수, 원본 batch 불변성.
- CAD 경로, 면별 ROI 물성, specular·Lambertian·Gaussian·mixed 재편집.
- MIS 난수 계약, 실제 1,000회 반사, 사건 window 재시도와 표시 경로 재추적.
- GPU 실행 뒤 오류가 나도 미확정 suffix만 CPU 복구하며, Stop 시 부분 사건은 미반영.

관련 51개 모듈의 통합 회귀는 **393개 통과, 531.645초**다.
원시 로그는 `outputs/perf5_transport/coherent-full-regression.log`다.

실제 장치의 Compute Sanitizer memcheck 4개 조건은 59.072초에 통과했고,
synccheck 2개 조건은 36.614초에 통과했다. 두 검사 모두 error summary 0이다.
부분 warp histogram과 공간 prefix scan, 실제 1,000회 반사 재시도를 포함한다.
로그는 같은 폴더의 `coherent-memcheck.log`, `coherent-synccheck.log`다.

## 구간 계측과 다음 후보

별도 CUDA event 계측에서 CAD 원본 wall 중앙값은 약 151ms였다.
방향 1분류와 64분류의 교차 계산 event 구간 합은 약 101.0ms와 98.2ms로,
정렬에 따른 감소는 약 2.8ms에 그쳤다. histogram·prefix·scatter를 더 조정하는 것만으로
추가 2배를 얻기는 어렵다는 판단이다.

이 값은 순수 kernel 시간이나 wall time의 독립 분해가 아니다. CUDA event 구간에는
호스트 dispatch 간격이 포함될 수 있고, 호스트 launch 호출 시간은 driver 대기 및
GPU 실행과 겹칠 수 있다. 두 시간을 합산하지 않는다.
원시 계측은 `outputs/perf5_transport/coherent-stage-intervals.json`이다.

다음 후보는 원래 교차 판정의 우선순위를 보존하면서, 매우 가까운 hit 후보군의
전체 BVH 재탐색을 줄이는 방식이다. 단순한 최근접 면 교체는 미세 간격에서 물성이
바뀔 수 있으므로 금지한다. 원래 BVH 순서 복원, 간섭 구간 검사, 후보 수 상한,
원래 leaf의 반올림 도달성 검사를 만족할 때만 단축하고, 나머지는 원래 GPU
탐색으로 돌아가도록 별도 실험한다. 이 후보의 제품 적용과 성능 판정은 후속 검증 대상이다.
