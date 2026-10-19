# PERF-5B CPU 입력 검증과 집계 준비 최적화 결과

작성: 2026-10-18

CPU 가속 1차 적용에 이어 광선별 입력 검증과 ordered summary 준비의 반복
비용을 줄였다. 동일 프로세스에서 직전 준비 방식과 비교한 warm 5회 중앙값은
직접광 38.7%, 갇힌 구조 10회 반사 19.0%, 샘플 TV 코너 4.2% 시간 단축이다.
공개 계산 결과는 비교 조건에서 기존과 정확히 일치했다.

이번 수치는 직전 CPU 가속 설정 대비 추가 개선이다. GPU 속도 향상, LT 정합,
사내 실제 TV 모델 또는 1억 Ray 성능을 나타내지 않는다.

## 환경과 비교 방법

- 작업 폴더: `C:/Users/Administrator/Documents/TV leakage simulator main`.
- Source checkout: main `157230c6` 이후 PERF-5A/5B 로컬 작업.
- Intel Core i7-10700, 8코어/16논리 프로세서.
- 기존 Python 3.13.3, NumPy 2.4.6, Numba 0.66.0, llvmlite 0.48.0 환경.
- CPU production auto 경로, 기본 batch 8,192, worker 상한 4를 양쪽에 동일 적용.
- 이번에 바꾼 준비·검증 함수 5개만 기준 소스에서 복원한 대조군과 개선본을
  같은 프로세스에서 교대로 실행했다. 기준 함수는 Counter 입력 검증,
  OrderedSummary 입력 검증, 정수 결과 검산, 제곱 광량 준비, 저장 path 준비다.
  BVH·반사 kernel·seed·기본 batch·worker·광학 설정은 공통이다.
- 기준 함수의 소스 hash와 제품 소스 hash를 JSON에 기록했다. 디스크의 제품
  파일을 교체하지 않고 호출 범위에서만 기준 함수를 사용한다.
- 조건별 최초 1회와 warm 5회를 분리하고 warm 순서를 번갈아 실행했다.
  최초 실행은 공통 JIT를 공유하므로 독립 cold-process 비교로 해석하지 않는다.
- 9개 장면 × 2개 구현 × 최초/warm 6회, 총 108번의 CPU 계산을 비교했다.
- 시간은 CPU 계산 호출 기준이다. CAD import·BVH 사전 준비, API 직렬화/전송과
  브라우저 렌더링은 아래 비교에 포함하지 않는다.

## 성능 결과

| 조건 | Ray 수 | 직전 준비 방식 | 개선 후 | 시간 단축 | 속도비 |
|---|---:|---:|---:|---:|---:|
| CAD 면광원 직접광 | 100,000 | 0.237013초 | 0.145211초 | 38.7% | 1.63배 |
| 확률 반사 2회 | 100,000 | 0.180381초 | 0.163294초 | 9.5% | 1.10배 |
| 갇힌 구조 10회 반사 | 100,000 | 1.140656초 | 0.924499초 | 19.0% | 1.23배 |
| 샘플 TV 코너 CAD | 20,000 | 0.418692초 | 0.401057초 | 4.2% | 1.04배 |

직접광처럼 수광 hit가 많은 조건은 Receiver 좌표 검증의 Python 반복을 줄인
효과가 크다. TV 코너의 계산 시간은 교차 탐색의 비중이 커서 이번 준비 단계
개선의 추가 이득은 작다. 모든 모델이 20% 이상 개선되었다고 해석하지 않는다.

TV 대조군은 50,944 triangle의 STEP 샘플이다. Receiver hit 0, surface hit
219,995회이므로 실제 빛샘 수광량 검증을 대신하지 않는 연산 부하 대조군이다.
수광 결과는 직접광·확률 반사·갇힌 구조와 회귀 테스트에서 별도로 비교했다.

![CPU 후속 개선 성능 비교](../../outputs/perf5_cpu/5b2-comparison.png)

### 반사 상한 대조군

첫 반사에서 흡수되는 동일 구조에 상한만 바꾸었다. 각 10,000 Ray다.

| 반사 상한 | 직전 준비 방식 | 개선 후 | 시간 단축 |
|---:|---:|---:|---:|
| 20 | 0.017880초 | 0.016066초 | 10.1% |
| 50 | 0.017875초 | 0.016230초 | 9.2% |
| 100 | 0.017917초 | 0.016023초 | 10.6% |
| 300 | 0.022664초 | 0.021484초 | 5.2% |
| 1,000 | 0.032377초 | 0.030930초 | 4.5% |

비교한 9개 장면 모두 3% 초과 회귀는 없었다. 상한에 따른 기록 공간과 batch
제한 비용 자체를 없앤 것은 아니다. 높은 상한의 GPU 기록 비용은 PERF-5C에서
별도 개선한다.

## 변경 내용과 정확도 보존

1. 반사 모델 코드 검증을 광선마다 Python 정수로 변환하는 반복 대신 배열
   membership 검사로 바꿨다. 알 수 없는 모델 코드는 계속 거부한다.
2. Receiver의 행·열 범위 검증을 일괄 처리한다. 수광면마다 다른 grid 크기를
   사용하고, 잘못된 Receiver ID는 배열 조회 전에 거부한다. 비수광 terminal의
   sentinel 좌표와 Receiver가 없는 빈 묶음도 기존대로 처리한다.
3. 제곱 광량 준비와 검증에서 Python 인덱스 반복 비용을 줄였다. 각 값의
   `float(power) ** 2` 연산은 그대로 유지한다. `power * power`나 NumPy 제곱으로
   바꾸지 않았으며 1 ULP 차이가 나는 입력과 subnormal·0의 bit 검사를 추가했다.
4. 정수 분포 검산의 중복 업데이트를 정수 `bincount`로 바꿨다. 반사·깊이·수광
   count를 독립적으로 재계산하며 int64를 사용한다. 실수 광량 집계에는 이 방식을
   적용하지 않았다.
5. path 저장이 꺼졌거나 저장 quota가 0이면 저장할 수 없는 모든 terminal을
   순회하지 않는다. 저장이 켜진 경우의 Receiver 우선 quota와 path 순서는 유지한다.

광량 ordered reducer의 합산 순서, 독립 float oracle, redundant shadow,
결과 digest, 입력/출력 소유권과 alias 검사 및 실패 시 전체 묶음 fallback은
유지했다. shared heatmap을 여러 worker가 동시에 수정하도록 바꾸지 않았다.
광원 분포·Aim·총 광속·물성·종료 정책·Receiver grid·nit 환산도 변경하지 않았다.

## 검증 결과

- 회귀 테스트 178개 통과: CPU 병렬/준비 검증, 기존 교차·반사 planner,
  Python/native ordered reducer, event tape·runtime 통합, 광학/ROI/portable/
  산란/좌표 검증, 종료 정책, Aim Area/Sphere와 광원 분포, source launcher.
- 신규 검사는 다른 크기의 Receiver grid 경계, 잘못된 ID와 모델 코드,
  제곱 광량의 1 ULP 변조, 비수광 sentinel, 비어 있는 Receiver,
  1,000회 깊이 정수 분포와 path 비활성 시 terminal 비순회를 포함한다.
- 동일 프로세스의 기준/개선 18조건은 공개 결과 hash가 일치했고 native
  교차·planner·reducer fallback은 모두 0회였다.
- 별도 9장면 × batch 1,024/8,192 × worker 1/4의 36조건에서도 공개 결과가
  CPU 1차 완료본 및 PERF-5 구현 전 기준과 모두 일치했다. 각각 최초 1회와
  warm 3회를 측정했다.
- 비교에는 Receiver grid·광량·count·반사/광학/기여도 요약 등 공개 결과를
  포함하며 runtime/run ID/성능 계측만 제외했다. 저장 path 순서와 실수 bit
  정합은 기존 ordered reducer 회귀에서도 검사했다.
- 제품 소스는 각 성능 측정 도중 바뀌지 않았다.
- 측정 프로세스의 CUDA import는 없었다. 별도 CPU-only 새 프로세스 검증도
  통과했다. 기존 GPU runtime 폴더를 재설치하거나 변경하지 않았다.
- `git diff --check` 오류 없음.

`cProfile`은 병목 확인용이다. 갇힌 구조 10만 Ray에서 입력 검증 등의 Python
호출 수는 약 124만에서 45만으로 감소했다. profiler가 켜진 시간은 위 성능 표와
섞지 않았다. 독립 float oracle와 ordered reducer의 비용은 여전히 남아 있다.

## GPU 목표와 다음 단계

기존 채택 기준은 대표 장면 warm 중앙값 20% 이상 단축과 작은 대조군 회귀
3% 이내다. 이번에는 직접광이 그 개선 기준을 충족하며 모든 대조군이 회귀
기준을 충족했다. 갇힌 구조와 TV 모델은 각각 19.0%, 4.2%로 장면별 이득이 다르다.

GPU 중기 제안 목표는 현재 GPU production 대비 같은 입력/Ray 수의 총 계산
시간 50% 단축, 즉 2배다. CPU 대비 GPU 비율과 혼동하지 않는다. 이 목표는
현재 달성된 실측 수치가 아니며, GPU 기준 측정과 PERF-5C~5E에서 검증한다.
1억 Ray·반사 10회 이상·목표 오차의 절대 실행 시간은 대표 모델과 실제 장치의
장시간 측정 뒤 정한다. Flux와 Peak 영역의 품질을 유지하는지도 함께 본다.

다음 착수점은 GPU의 PERF-5A 기준 측정을 보완한 뒤 PERF-5C의 높은 반사 상한
기록/초기화 비용을 줄이는 작업이다. 추가 CPU 멀티코어 집계나 C++ 확장은
남은 profile과 정확도 계약을 근거로 별도 채택한다. 이번 결과만으로 GPU 실행
성공, LT 일치 또는 모든 다회 반사 문제의 검증 완료를 주장하지 않는다.

서버 실행·재시작, 커밋과 push는 하지 않았다. 새 CPU 코드는 다음 서버 실행부터
적용된다. 기존의 사용자 변경과 다른 작업의 파일은 그대로 유지했다.

## 재현과 산출물

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf5b_cpu_preparation.py `
  --reference-revision 157230c6 `
  --baseline-json outputs\perf5_cpu\5b2-before.json `
  --json-out outputs\perf5_cpu\5b2-final-comparison.json
```

`--baseline-json`은 이번 로컬 기준 파일을 이용하는 추가 검증이다. 다른 checkout에서는
생략할 수 있지만 기준 함수 소스를 읽을 수 있는 해당 Git commit이 필요하다.
환경 이름과 관계없이 CPU만 실행하며 별도의 GPU setup/preflight를 수행하지 않는다.

- `outputs/perf5_cpu/5b2-before.json`: 이번 변경 전 실제 CPU 실행 기준.
- `outputs/perf5_cpu/5b2-final-comparison.json`: 최종 교대 순서 비교, 함수/소스 hash와 실행 증거.
- `outputs/perf5_cpu/5b2-final-matrix.json`: batch/worker 36조건 비교.
- `outputs/perf5_cpu/5b2-comparison.png`: 비교 그래프.
- `outputs/perf5_cpu/5b2-before.prof`, `5b2-after.prof`: Python 병목 profile.
- `outputs/perf5_cpu-5b2-regression.log`: 회귀 테스트 로그.

산출물은 Git 제외 경로다. 코드·테스트·보고서는 아직 커밋/push하지 않았다.
