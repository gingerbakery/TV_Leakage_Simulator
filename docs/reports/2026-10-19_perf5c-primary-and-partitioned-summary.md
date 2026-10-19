# PERF-5B/5C 광원 준비와 수송 기록 개선

작성: 2026-10-19. Source checkout의 로컬 구현/검증 기록이다.

이후의 높은 상한 CPU pool 개선과 최신 GPU 반복 실패/성능은
[`후속 검증`](2026-10-19_perf5c-bounded-cpu-events.md)에 기록했다. 아래 수치는
광학 palette 단계의 이전 측정이다.

**CPU와 GPU 모두 최소 2배라는 전체 목표는 아직 미달이다.** CPU 대표 네 장면은
2.121~3.431배, GPU TV 부하 대조군은 1.357배다. GPU 확률 2회 반사는 동일
소스의 독립 세션 두 번에서 2.461/1.885배로 달라 안정적인 2배 통과로 계산하지
않는다. 높은 상한의 CPU와 5C의 bounded depth window/직접 summary 기록 경로도
계속 개선해야 한다.

## 기준과 실행 환경

- 작업 폴더: `C:/Users/Administrator/Documents/TV leakage simulator main`.
- `main@157230c6` + 현재 로컬 작업. 커밋/push/배포하지 않았다.
- Intel Core i7-10700, RTX 3070 8GB, NVIDIA driver 591.59.
- CUDA Toolkit 13.1.1, Python 3.13.3 x64, Numba 0.66.0, llvmlite 0.48.0.
- GPU 기준은 목표 착수 전 CUDA 구현이다. 비교 시 해당 revision의 tracing,
  summary 및 primary sampler 함수를 Git에서 읽어 메모리에서만 교체했다.
  checkout 파일을 되돌리거나 Python fallback을 GPU 기준으로 사용하지 않았다.
  이번 광학 준비 변경은 기준 실행에서 비활성화하고 이전 GPU context 준비
  함수도 함께 고정했다. 후보의 빠른 준비 경로가 기준에 섞이지 않는다.
- CPU 기준은 5B 입력 검증 개선 완료본의 8,192 batch/4 worker 경로다.
  후보는 자동 CPU 수송과 32,768 batch/8 worker 및 새 광원 준비 경로다.
- CPU 반사 planner 기준 함수는 `157230c6`에서 읽어 별도 JIT cache로 실행한다.
  후보의 인라인화가 비교 대상의 planner까지 함께 바꾸지 않도록 분리했다.
- 각각 같은 장치/장면/세션에서 순서를 교대해 첫 실행과 warm 3회를 기록했다.
  CPU와 GPU의 벤치마크는 동시에 실행하지 않았다. 표는 warm 중앙값이다.

읽기 전용 prerequisite 점검 및 `run_web_gpu.ps1 -PreflightOnly`를 수행했다.
공유 junction인 `.venv-gpu`를 삭제/재생성하지 않았으며 OS driver/Toolkit 설치,
시스템 설정 변경, 재부팅과 서버 시작은 하지 않았다.

Production preflight의 네 boolean은 모두 true였고 scope/contract도 일치했다.

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

## 구현

### 1. CPU에서 수행하는 광원 준비를 통합

실측 profiling에서 GPU 모드의 Face 직달 100만 Ray는 광원 준비가 약 0.4초,
tracing/집계 호출이 약 0.2초를 차지했다. GPU kernel만 바꿔서는 전체 2배가
어려우므로 이 CPU 준비 비용을 함께 줄였다. profiling은 계측 오버헤드가 있어
아래 최종 벤치마크 수치 자체로 사용하지 않았다.

- NumPy의 난수 호출 순서와 sin/cos 계산은 유지한다.
- 면광원 barycentric 좌표, 평면 좌표 변환, normal별 기저와 방향 정규화의
  중간 배열 생성을 FP64 Numba loop로 합쳤다. `fastmath=False`를 유지한다.
- 출력은 worker별 disjoint row로 나눈다. 8,192개 미만은 기존 NumPy를 사용한다.
- 오류가 나면 이미 생성한 동일 난수 입력으로 NumPy 연산을 재실행한다.
- 좌표/방향의 uint64 bit, 후속 난수 상태와 CPU 공개 결과를 별도로 대조한다.
- `native_primary_preparation_*`는 실제 실행/실패/시간을 기록한다. 처리 row는
  여러 준비 단계에서 중복 집계되므로 발사 Ray 수로 해석하지 않는다.

GPU 모드여도 광원 난수 생성/준비는 CPU에서 수행한다. 이번 변경을 광원 생성까지
CUDA로 옮겼다는 뜻으로 설명하지 않는다.

### 2. GPU 정수 집계를 분산

작은 공통 카운터와 first-touch 값에 몰리던 GPU atomic 경합을 여러 partition으로
나눴다. 정수 합계는 합하고, 관측 깊이/오류 코드는 max, first-touch는 min으로
GPU에서 병합한다. 큰 heatmap 배열을 partition 수만큼 복제하지 않는다.

- 자동 선택은 큰 batch에서 최대 128 partition을 사용하며 작은 batch는 기존
  경로를 유지한다. 추가 workspace는 metadata를 포함해 8 MiB 이하로 제한한다.
- partition은 GPU 메모리에서 초기화/병합한다. 매번 host로 복사하지 않는다.
- 반환 snapshot은 기존처럼 독립 소유/read-only 배열이며 chunk 실패 시 기존
  host 경로로 batch 전체를 재실행한다.
- **FP64 광량 합산은 기존 global atomic 방식 그대로 보존했다.** 정수 카운터만
  분산하며 모든 float state가 partition에서 제외되는지 검사한다.

FP64까지 분산하는 첫 후보는 폐기했다. Ray 수와 물리 광량의 차이는 작았지만,
합산 묶음 변경 때문에 기존 CPU의 순차 반올림과 1,057 ULP까지 달랐고 엄격
MIS 검사가 실패했다. near-uniform 입사의 분산/오차 추정에도 차이가 나타났다.
이를 숨기기 위해 허용치를 넓히거나 variance를 임의로 0으로 만들지 않았다.

폐기 후보의 `partition-paired-million.json`, `partition-tuning.json`,
`partition-full-regression.log`는 진단 기록이며 최종 채택 결과가 아니다.

### 3. 함께 발견한 기존 GPU 표시 path 누락 수정

기존 sparse selector는 현재 batch에서 먼저 저장한 미수광 path를 같은 batch의
나중 수광 path로 교체할 후보 공간이 부족했다. `free + existing_dead_end`만
확보해 후보가 가득 차면 Receiver 도달 ray를 더 확인하지 않고 종료했다.

후보 공간을 `2*free + existing_dead_end`로 늘리고 모든 저장 slot이 수광 path로
채워지면 종료한다. 실제 저장 path 상한은 바꾸지 않았다. 다운로드/재추적 후보만
최대 두 배가 될 수 있으며 전체 primary batch 크기로도 제한한다.
Face/확률 반사/갇힌 구조의 10,000 Ray와 작은 path quota에서 CPU/GPU의 path
선택을 대조하고 새 dead-end 및 기존 dead-end 교체를 직접 검사한다.

### 4. CPU 반사 호출과 기록 복사를 줄임

반사 planner의 입력을 네 묶음으로 전달해 통합 CPU 수송 loop 안에 인라인했다.
반사 수식과 counter RNG loop의 AST는 기존 함수와 동일함을 검사했다.
`fastmath=False`와 원래 난수 lane을 유지하며 기존 planner API도 보존한다.

방문한 event만 연속 배열로 옮기는 병렬 packing을 추가했다. 각 worker는 서로
다른 primary 구간만 쓰며, 큰 row/depth index 배열 두 개와 중복 복사를 없앴다.
반환 배열은 원본 scratch와 공유하지 않는 read-only snapshot이다. 경로를
저장하지 않는 실행에서는 geometry 배열을 읽지 않는다. 미방문 slot을 NaN으로
채운 검사와 signed-zero bit 검사도 통과했다.

중복 index 계산을 줄였지만 광량 합산 순서, 독립 float 검산, shadow 검산,
정수 count, 결과 digest와 실패 시 batch 전체 재실행은 유지했다.

### 5. 다음 광원 batch 준비와 GPU 추적을 병행

GPU가 현재 batch를 계산하는 동안 CPU worker 하나가 다음 primary batch를
준비한다. 준비 가능한 batch는 한 개로 제한하고 소비 순서를 바꾸지 않는다.
광원 난수 생성 방식·batch 크기·전력·반사 수식과 GPU 집계 kernel은 그대로다.
GPU 수송/집계/partition 세 모듈의 hash도 이전 검증본과 같다.

- 자동 적용은 resident GPU 경로, Emitter Ray 65,536 초과, 교차 batch 8,192
  이상인 경우다. 작은 실행과 높은 상한 때문에 batch가 줄어든 경우는 제외한다.
- CPU 광원 준비의 thread-local 실적은 소비 시 호출 thread로 합친다. 준비가
  앞서갔지만 취소돼 사용하지 않은 batch를 완료 Ray로 집계하지 않는다.
- 정상 종료, 사용자 취소, producer/consumer 예외 모두 worker를 join하고
  generator를 닫는다. background thread에서는 CUDA를 호출하지 않는다.
- `primary_prefetch_*`에 실제 Emitter 수·소비 batch·대기 시간을 기록한다.
  진단용 `LEAKAGE_PRIMARY_PREFETCH=off`는 병행 처리만 끈다.

GPU 오류 주입 때에도 동일 primary sequence로 기존 경로를 재실행했다.
이때 resident fallback은 1회였지만 별도 CUDA BVH는 성공했으므로 부분 fallback
검사로 해석한다. 아래 정상 성능 표는 이 실패 주입 실행을 포함하지 않는다.

### 6. 깊이별 GPU 집계 병렬화 후보는 제외

추가 후보는 같은 primary의 여러 반사 깊이를 병렬로 집계했다. 이산 결과는
같았지만 엄격한 MIS 검사에서 최대 38 ULP로 기존 상한 32를 초과했다.
허용치를 넓히지 않고 해당 구현과 실행 옵션을 제거했다. 아래 최종 검증은
이 후보를 제외한 결과다. 실패 기록은 `event-parallel-experiment-tests.log`에 남겼다.

### 7. CAD 광학 속성을 실행별 palette로 준비

같은 property를 쓰는 삼각형마다 별도 resolved 객체와 숫자 해석을 반복하던
비용을 줄였다. 실행마다 새 palette와 삼각형별 slot을 만들고, palette에서
반사율·roughness·scatter·specular ratio·Gaussian sigma를 해석한 뒤 NumPy로
각 삼각형에 펼친다. 집계용 profile slot도 같은 palette에서 준비한다.

우선순위는 원래대로 Face override → 부품 지정 → mesh material → default →
미지정 흡광 순서다. ROI의 `source_face_index`, 비활성 지정, 동률 priority,
존재하지 않는 profile의 다음 단계 fallback도 유지한다. 실행 간 cache는 두지
않으므로 profile·assignment·material·metadata를 수정하면 다시 해석한다.
숫자 배열은 독립 소유/read-only이며 값의 uint64 bit까지 원래 배열과 대조했다.

자동 적용은 256 triangle 이상이다. 작은 합성 장면은 원래 경로를 유지하고,
진단용 `LEAKAGE_OPTICAL_TABLES=on/off`로 강제 대조할 수 있다. 강제 적용 검사에는
ROI/면별 override 및 Specular/Lambertian/Gaussian/Mixed의 CPU·GPU 교차 비교와
저장 파일 복원 후 재편집 검사를 포함했다.

GPU 재현 세션의 CAD 광학 해석 구간은 0.0399 → 0.0190초, 전체 ray tracing은
0.2263 → 0.1667초였다. 전체 시간은 기준 대비 약 26% 줄었지만 목표 2배에는
미달이다. 작은 합성 장면의 측정 변동을 이 palette 개선의 효과로 귀속하지 않는다.

### 8. BVH 구조 및 layout 후보는 아직 적용하지 않음

같은 CAD에서 leaf 크기 8 → 1은 현재 후보의 약 0.171 → 0.120초를 보였다.
F-order 배열 전송은 큰 추가 이득을 확인하지 못했다. 이 측정은 순차 후보 탐색이며
표의 고정 기준 교대 벤치마크와 구분한다. leaf를 줄이면 node 수와 메모리도 늘어난다.

추가 스트레스 검사에서 간격이 `5e-11 mm`인 두 삼각형은 leaf 8/1일 때 선택
면 ID가 각각 0/1이었다. 이는 사내 CAD가 아니라 의도적으로 만든 극미소 간격
조건이며, 현재 near-tie 판정이 탐색 순서에 영향을 받는다는 증거다. 면에 서로
다른 property가 있으면 결과 차이가 될 수 있어, 속도만 보고 tree 변경을 채택하지
않았다. 제품의 BVH/교차 계산 코드는 수정하지 않았다. 5D에서는 먼저 안정적인
tie 계약과 경계 검증을 확보해야 한다.

## CPU 결과

| 장면 | Ray | 기존 CPU | 현재 CPU | 속도비 |
| --- | ---: | ---: | ---: | ---: |
| Face 직달 | 1,000,000 | 1.404초 | 0.409초 | 3.431배 |
| 확률 2회 반사 | 1,000,000 | 1.477초 | 0.522초 | 2.829배 |
| 갇힌 구조 상한 10 | 1,000,000 | 8.803초 | 4.150초 | 2.121배 |
| TV CAD 부하 대조군 | 100,000 | 1.601초 | 0.727초 | 2.201배 |
| 흡광 대조군 상한 20 | 100,000 | 0.136초 | 0.067초 | 2.028배 |
| 흡광 대조군 상한 100 | 100,000 | 0.133초 | 0.122초 | 1.095배 |
| 흡광 대조군 상한 1,000 | 100,000 | 0.299초 | 0.289초 | 1.036배 |

CPU는 일곱 장면 모두 semantic hash exact, fallback 0, source 고정 검사를 통과했다.
Face의 첫 후보 실행은 14.199초로 추가 JIT를 포함한다. 첫 실행 2배 개선을
보장하지 않으며, 위 숫자는 warm 실행만 비교한다.

## GPU 결과

같은 소스의 독립 프로세스 재현 세션 B를 표로 제시한다. 각 세션 안에서는
기존/후보를 교대했고 각각 첫 실행과 warm 3회를 기록했다.

| 장면 | Ray | 기존 GPU | 현재 GPU | 속도비 | CUDA 성공/시도 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Face 직달 | 1,000,000 | 0.669초 | 0.252초 | 2.658배 | 16/16 |
| 확률 2회 반사 | 1,000,000 | 0.471초 | 0.250초 | 1.885배 | 16/16 |
| 갇힌 구조 상한 10 | 1,000,000 | 0.466초 | 0.221초 | 2.114배 | 16/16 |
| TV CAD 부하 대조군 | 100,000 | 0.226초 | 0.167초 | 1.357배 | 2/2 |
| 흡광 대조군 상한 20 | 100,000 | 0.052초 | 0.030초 | 1.705배 | 2/2 |
| 흡광 대조군 상한 100 | 100,000 | 0.093초 | 0.048초 | 1.938배 | 7/7 |
| 흡광 대조군 상한 1,000 | 100,000 | 0.609초 | 0.273초 | 2.230배 | 49/49 |

일곱 장면 모두 `gpu_active`, CUDA 성공 > 0, CPU hybrid/fallback 0이었다.
기존 CUDA 및 저장 CPU 기준에 대해 discrete exact와 기존 `1e-9` FP64 물리량
허용범위를 통과했다. 목표 착수 전 GPU 대비 모든 5C 변경을 합친 속도비이며,
상한 1,000의 이득 전체를 이번 정수 partition 변경 하나에 귀속하지 않는다.
상한 1,000은 실제 batch가 작아 partition과 primary prefetch를 사용하지 않았다.

Face 첫 후보 실행 4.701초, 다른 장면의 첫 실행과 warm 세 번의 원자료는 JSON에
모두 기록했다. CPU 대비 GPU의 비율이나 LT 대비 비율을 위 표로 계산하지 않는다.

세션 A/B의 CAD 속도비는 1.3566/1.3572배로 재현됐지만 확률 2회 반사는
2.4605/1.8848배였다. 두 세션은 입력과 소스 hash가 같고 정확도 검사를 모두
통과했다. 이 변동의 원인을 이번 측정만으로 확정하지 않으며, 유리한 세션만
골라 2배 달성으로 판정하지 않는다.

TV 부하 대조군은 50,944 triangle이지만 Receiver hit가 0인 기존 probe다.
실제 TV 빛샘의 정확도/희귀 입사 성능 검증이 아니며, 회사 PC 결과도 아니다.

## 검증 원자료 및 미완료 범위

- `outputs/perf5_cpu/optical-palette-final-million.json`: 최종 CPU 비교.
- `outputs/perf5_transport/optical-palette-final-million.json`: GPU 세션 A 비교 및 실행 증거.
- `outputs/perf5_transport/optical-palette-replication-million.json`: GPU 재현 세션 B 및 실행 증거.
- `outputs/perf5_transport/cad-preparation-preflight.log`: source launcher preflight/build.
- `outputs/perf5_transport/partition-baseline-profile.log`: 착수 시 profiling.
- `outputs/perf5_transport/partition-candidate-profile.log`: 광원 준비/집계 이후 profiling.
- `outputs/perf5_transport/partition-integer-regression.log`: 조정 이후 관련 20개 검사 통과.
- `outputs/perf5_transport/optical-palette-final-regression.log`: 최종 자동 정책 통합 240개 통과.
- `outputs/perf5_transport/optical-palette-forced-integration.log`: 작은 모델에도 palette를 강제해 39개 추가 재검사 통과.
- `outputs/perf5_transport/optical-palette-final-roundoff.json`: 기존/현재 각각 8회 MIS 반복 비교.
- `outputs/perf5_transport/partition-forced-failure-regression.log`: 작은 batch에도 분산을 강제해 snapshot/미사용 slot/오류 재실행/표시 path 6개 재검사 통과.
- `outputs/perf5_cpu/optical-palette-final-million.png`: CPU 비교 그래프.
- `outputs/perf5_transport/optical-palette-replication-million.png`: GPU 세션 B 비교 그래프.
- `outputs/perf5_transport/optical-palette-leaf-experiment.json`: 미채택 leaf 탐색 결과.
- `outputs/perf5_transport/optical-palette-layout-experiment.json`: 미채택 배열 layout 탐색 결과.
- `outputs/perf5_transport/bvh-near-tie-characterization.json`: tree 변경 전 추가 정확도 검증이 필요한 극미소 간격 사례.

최종 통합 검사는 240개 모두 통과했고 palette 강제 적용 39개도 통과했다.
별도 32 ULP MIS 반복에서는 기존 구현이 8회 중 1회 실패(max 33 ULP), 현재
구현은 8회 모두 통과(max 30 ULP)했다.
두 구현 모두 discrete 차이는 없었다. 이 8회 관측을 모든 seed/반복에서 해당
위험이 완전히 제거됐다는 증거로 확대하지 않는다. 폐기한 float 분산/깊이 병렬화
후보의 엄격 검사 실패를 최종 구현의 통과 결과와 섞지 않았다.

미완료 작업은 다음과 같다.

1. CPU 높은 반사 상한에서도 큰 임시 배열 없이 실제 방문 깊이에 비례해 기록한다.
2. CAD에서 남은 교차 계산 비용을 줄이되 극미소 간격/공유 경계에서 면 ID와
   면별 property가 달라지지 않는지 먼저 검증한다.
3. GPU event를 실제 방문 깊이에 비례해 기록하는 bounded window/직접 summary,
   host snapshot 빈도 조정으로 5C 본 범위를 마무리한다.
4. 기존에 관측된 드문 32 ULP MIS 판정의 반복 안정성을 계속 확인한다.
5. 같은 고정 기준에서 CPU/GPU 각각 2배를 다시 판정한다. 일반/복잡 모델의
   미달을 단순 직달 또는 흡광 대조군 하나의 통과로 대체하지 않는다.

LT 정합, 실제 회사 TV 도면, clean-PC 배포 및 1억 Ray 실측의 검증 완료를
이 보고서로 주장하지 않는다. 전체 목표와 5B/5C는 계속 진행 상태다.
