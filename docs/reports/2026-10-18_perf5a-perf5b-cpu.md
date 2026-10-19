# PERF-5A CPU 기준 측정과 PERF-5B CPU 가속 결과

작성: 2026-10-18

CPU 범위의 PERF-5A 기준 측정과 PERF-5B 1차 구현을 진행했다. CPU 전용 의존성
공급, production batch 조정, strict FP64 교차/반사 계산의 분리된 병렬 작업을
적용했다. 샘플 TV 코너 대조군의 warm 중앙값은 이전 설정 대비 약 2.08배
빨라졌으며, 비교한 공개 결과는 수정 전 기준과 모두 동일했다.

GPU 기준 측정과 PERF-5C~5F는 이번 결과에 포함하지 않는다. LT 정합 또는
사내 실제 TV 모델의 처리 시간을 확정하는 결과도 아니다.

## 환경과 측정 조건

- Source checkout: main `157230c6` 이후 이번 CPU 작업.
- CPU: Intel Core i7-10700, 8코어/16논리 프로세서.
- Python 3.13.3 x64, NumPy 2.4.6, Numba 0.66.0, llvmlite 0.48.0.
- 기존 `.venv-gpu`의 Python으로 CPU 경로만 실행했다.
- CPU 실행 과정에서 `numba.cuda`는 import되지 않았다. CUDA import를 강제로
  차단한 별도 새 프로세스의 실제 CPU 수송 검사도 통과했다.
- 모델별 네 설정을 동일 프로세스에서 비교했다: batch 1,024/8,192 × worker 1/4.
- 각 설정의 최초 실행을 별도 기록한 뒤 warm 3회를 측정했다. warm round마다
  설정 순서를 정방향/역방향으로 바꾸었다.
- 최초 실행은 프로세스의 공통 JIT를 공유하므로 설정별 독립적인 cold-process
  시간으로 해석하지 않는다. 이번 표는 warm 중앙값이다.
- 아래 시간은 CPU 계산 호출 기준이다. CAD 준비, 결과 직렬화/API 응답,
  브라우저 렌더링은 별도이며 표의 속도비에 포함하지 않는다.

## 성능 결과

| 장면 | primary Ray | 이전 설정 1,024·직렬 | 8,192·직렬 | 8,192·최대 4 worker | 이전 설정 대비 |
|---|---:|---:|---:|---:|---:|
| CAD 면광원 직접광 | 100,000 | 0.519669초 | 0.241611초 | 0.241167초 | 2.15배 |
| 확률 반사 2회 | 100,000 | 0.544541초 | 0.187178초 | 0.183939초 | 2.96배 |
| 갇힌 구조 10회 | 100,000 | 2.152087초 | 1.164473초 | 1.160449초 | 1.85배 |
| 샘플 TV 코너 CAD | 20,000 | 0.900066초 | 0.671355초 | 0.433344초 | 2.08배 |

교차가 간단한 합성 장면의 개선은 주로 batch 증가로 반복 준비·집계 비용이
줄어든 효과다. 이 장면에서 4개 교차 worker를 실제 사용했다고 주장하지 않는다.

TV 코너는 50,944 triangle의 실제 STEP 샘플을 사용한 계산 부하 대조군이다.
동일 batch 8,192에서도 직렬 0.671355초 대비 병렬 0.433344초로 약 1.55배
빨라졌다. 이 실행은 교차 최대 4 worker를 사용했고 전체 계산의 process CPU
time / wall time은 약 1.47이었다. 전체가 항상 4코어를 꽉 사용한 것은 아니다.
본 대조군의 Receiver hit는 0이며 surface hit는 219,995회다. 따라서 TV 대조군
자체는 수광량 정합 검증을 대신하지 않는다. Receiver/히트맵/기여도/저장 경로는
별도의 직접광·확률 반사 장면과 회귀 테스트로 확인했다.

### 높은 반사 상한의 남은 비용

실제로 첫 반사에서 흡수되는 동일 구조에 상한만 다르게 설정했다.
10,000 Ray, 새 기본 설정의 warm 중앙값은 다음과 같다.

| 최대 반사 수 | CPU 시간 |
|---:|---:|
| 20 | 0.017881초 |
| 50 | 0.018845초 |
| 100 | 0.018223초 |
| 300 | 0.024374초 |
| 1,000 | 0.033746초 |

상한에 따른 event 공간과 batch 제한 비용은 남아 있다. CPU 최적화가
GPU/고반사 기록 구조를 바꾸는 PERF-5C를 대체하지 않는다.

## 구현 내용

### CPU 런타임

- `requirements-cpu-accelerated.txt`에 Numba/llvmlite를 고정했다.
- `run_web.bat`은 기존 환경에서도 CPU 의존성 동기화, pip check, exact-pin
  검사를 수행한다. CPU 소스 실행에 GPU 또는 CUDA Toolkit을 요구하지 않는다.
- frontend도 매번 lockfile에 맞춰 동기화한 뒤 빌드한다.
- source requirement 검증 출력은 실제 입력 requirements 이름을 표시한다.
  GPU 실행기의 기존 requirements 파일과 환경 marker는 변경하지 않았다.

### 계산

- production CPU 자동 경로의 기본 교차 batch를 1,024에서 8,192로 조정했다.
- 진단용 명시적 경로의 기존 1,024 기본값과 GPU의 65,536 기본값은 유지했다.
- 높은 반사 상한의 event slot 보호 제한은 계속 적용한다.
- Numba의 `nogil=True`, `fastmath=False` 계산을 disjoint range로 나누어
  재사용하는 bounded thread pool을 추가했다.
- 교차는 64 triangle 이상이며 worker당 최소 512 Ray일 때 병렬 실행한다.
  worker별 BVH stack과 Ray별 출력 구간은 서로 공유하지 않는다.
- 반사 planner는 worker당 최소 8,192행인 큰 묶음에서만 병렬화한다.
  작은 반사 묶음의 이득이 미미하여 기본 production batch에서는 직렬로 남긴다.
- 카운터 RNG key와 원본 행 순서는 그대로 사용하고 ordered reducer는 직렬로
  유지해 합산·기여도·저장 path 순서를 보존했다. worker별 부분 합산으로
  reducer 순서를 바꾸는 최적화는 이번에 적용하지 않았다.
- worker 실패 시 모든 실행 중 작업의 종료를 기다리고 예외를 전달한다.
  불완전한 교차/반사 결과는 정상 결과로 반환하지 않는다. 기존 batch fallback을 따른다.
- 작은 장면은 직렬이며, 중첩 호출도 직렬로 처리해 worker pool 교착을 막는다.

기본 worker 상한은 논리 CPU 수의 절반과 4 중 작은 값이며 최소 1이다.
`LEAKAGE_CPU_WORKERS=1`은 직렬 대조군, 양수 값은 장치 논리 CPU 수/32 범위 안의
상한이다. 잘못된 설정은 안전하게 1로 처리한다. 이 값은 `.bitsam` 저장 계약에
추가하지 않았고 UI에 새 전문 옵션도 만들지 않았다.

결과 `_performance_summary`에 다음 증거를 추가했다.

- `native_cpu_parallel_batch_count`
- `native_cpu_parallel_max_workers`
- `wavefront_planner_native_parallel_batch_count`
- `wavefront_planner_native_parallel_max_workers`

max worker 값은 해당 호출에 허용된 분할 작업 수이며 지속적인 물리 코어
점유율을 의미하지 않는다.

## 정합성 및 회귀 결과

- 9개 장면 × batch 2종 × worker 2종 = 36조건, 조건별 최초 1회와 warm 3회.
- 모든 실행의 공개 결과 hash가 같은 장면의 다른 설정/반복과 일치했다.
- 수정 전 별도 기준 측정 파일의 공개 결과 hash와도 36조건 모두 일치했다.
- 공개 결과 비교에는 광선 count, receiver grid, 광량, 광학/반사/기여도 요약을
  포함하며 runtime/run ID/성능 계측만 제외했다.
- 실제 native provider 성공을 요구했고 교차 fallback은 0회였다.
- 측정 중 제품 소스 hash는 바뀌지 않았다.
- CPU 병렬 분할·실패 전파·재진입, 2/4/8 worker 반복 교차 exact,
  17,003행 혼합 반사/roulette의 모든 출력 exact,
  기본 production의 heatmap/기여도/저장 path 정합을 검사했다.
- 기존 native CPU, wavefront, optical transport/ROI/portable/distribution,
  좌표 변환 및 source launcher 회귀를 포함해 84개 테스트가 통과했다.
- CPU용 10개 exact dependency pin 검사가 통과했다.
- `git diff --check` 오류 없음.

GPU 실기 정합, 사내 실제 모델, 1억 Ray 장시간 메모리 안정성, LT 비교는 후속
측정 대상이다. 이번 속도비를 그 조건에 선형 보장하지 않는다.

## 실행과 적용

CPU 소스 사용자는 프로젝트에서 `run_web.bat 8788`을 실행하면 CPU 가속
의존성과 최신 frontend를 준비한다. 기존 동작 중인 서버는 메모리의 CAD
세션을 보존하기 위해 이번 작업에서 재시작하지 않았다. 새 계산 코드는
사용자 작업을 저장한 뒤 서버를 재시작하면 적용된다.

새 CPU 환경의 실제 다운로드/설치를 이 PC의 기존 `.venv-gpu` 위에 덮어씌우지
않았다. 검증에는 이미 설치된 동일 핀과 CPU-only subprocess를 사용했다.

## 재현

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf5_cpu.py `
  --workers 1 4 --batch-sizes 1024 8192 `
  --json-out outputs\perf5_cpu\final-counterbalanced.json
```

이는 Python 환경 이름과 관계없이 CPU만 실행한다. CPU 소스 설치에서는
동일 핀을 준비한 `.venv\Scripts\python.exe`로 바꿔 실행할 수 있다.

```powershell
.\.venv-gpu\Scripts\python.exe scripts\plot_perf5_cpu.py `
  outputs\perf5_cpu\final-counterbalanced.json outputs\perf5_cpu\comparison.png
```

로컬 산출물:

- `outputs/perf5_cpu/baseline-synthetic.json`: 구현 전 합성 기준.
- `outputs/perf5_cpu/baseline-matrix.json`: 구현 전 CAD/반사 상한을 포함한 기준.
- `outputs/perf5_cpu/final-counterbalanced.json`: 최종 교대 순서 비교.
- `outputs/perf5_cpu/comparison.png`: 성능 비교 그래프.
- `outputs/perf5_cpu-regression-final.log`: 84개 테스트 결과.

산출물은 Git 제외 경로다. 코드·벤치마크·테스트·이 문서는 아직 커밋/push하지 않았다.
