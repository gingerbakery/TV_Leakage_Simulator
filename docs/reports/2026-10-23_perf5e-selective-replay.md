# PERF-5E: OptiX 선택적 재검산 후보

## 적용 범위

기준은 `codex/ux-project-batch@373659d16bfb58190f8790c89ff7d71114acc887`이다.
이번 변경은 독립 진단 도구와 테스트다. 제품 `src/`, `frontend/`는 바꾸지
않았으며 서버 재시작, commit/push, 설치, main 병합도 수행하지 않았다.

이전 단계가 OptiX 결과를 관찰만 했다면, 이번 단계는 후보 결과를 보정한 뒤
실제 진단용 다회반사 수송에 전달한다. 단, **모든 batch를 기존 전체 BVH와
다시 비교하는 oracle 검사를 여전히 유지한다**. 제품 가속 완료가 아니다.

## 구현

`tools/perf5e_native/selective_replay.py`:

1. 기존 dispatch의 실제 실행 장치를 식별한다. GPU 요청이라도 해당 batch가
   small-wave CPU로 실행됐다면 CPU 기준을 유지한다.
2. OptiX의 별도 일반 query로 후보를 얻는다. 이 query에는 oracle face ID를
   전달하지 않는다. Coverage 관찰 query는 별도다.
3. 겹침/근접/경계/수치 guard 및 miss는 기존 BVH 전체 탐색으로 재검산한다.
4. 그 외에는 선택된 삼각형 하나만 원래 CPU/CUDA의 FP64 연산 순서로 재계산한다.
   정밀 재계산이 유효하지 않으면 ray를 버리지 않고 BVH로 되돌린다.
5. 부분 batch의 크기가 작아졌다는 이유로 CUDA 재검산을 CPU로 바꾸지 않는다.
6. 결과의 행 수, 면 ID, 거리 비트를 전체 canonical oracle와 비교한다.
   하나라도 다르면 즉시 중단한다. Oracle 값으로 조용히 교체해 통과시키지 않는다.

Ray 수, seed, 반사 상한, 물성, 광량, FP64 정밀도는 낮추지 않는다.
`verify_perf5e_transport.py --selective-replay`로만 활성화할 수 있다.
기본 앱은 이 모듈을 import하거나 선택하지 않는다.

## 차이의 원인과 해석

이전 TV 코너의 54,108개 원시 면 선택 차이는 그대로 보존된 진단값이다.
OptiX의 후보 방문 순서와 기존 BVH의 근접 거리/면 ID 선택 순서가 다르다.
이 차이를 무시하지 않고 해당 ray를 기존 경로로 재검산한다.
따라서 이번 작업은 OptiX 원시 선택을 물리적으로 더 정확하게 고쳤다는 뜻이
아니라 **기존 계산 결과를 보존하는 선택적 재검산 경로를 구현한 것**이다.

또한 CUDA는 선택 면이 같더라도 OptiX와 거리의 마지막 비트가 다를 수 있었다.
원래 장치의 거리 재계산으로 이 차이도 검사한다. CPU/GPU 서로의 기존 결과를
강제로 같게 만드는 변경은 아니다.

## 사전 판정 기준

- 각 모드의 원래 계산과 후보: 면 ID exact, 거리 bit-exact, 수송 결과 exact.
- 같은 모드 첫 실행 + warm 3회: 의미 있는 결과 동일.
- 합성 경계조건: CPU/CUDA 각각 첫 실행 + warm 2회, 면/거리 및 반복성 동일.
- runtime/run ID/performance metadata만 최종 수송 비교에서 제외한다.
- 소스/입력 identity 및 원시 NPZ/JSON hash를 기록하고 독립 재계산한다.
- 전역 candidate coverage, 물리적 정확도, LT 일치, 성능 채택은 별도이며
  이번 통과로 인정하지 않는다. 기존 성능 채택 기준을 완화하지 않는다.

## 검증 환경

기존 `run_web_gpu` source 환경의 `.venv-gpu`를 사용했다. RTX 3070,
driver 591.59, CUDA 13.1, Python 3.13.3, Numba 0.66.0, NumPy 2.4.6이다.
설치되어 있던 OptiX SDK 9.1.0/MSVC로 별도 ignored 폴더에 빌드했다.
기존 서버나 사용자 프로젝트는 변경하지 않았다.

각 실행의 production preflight는 `available`, `strict_float64`,
`kernel_executed`, `kernel_verified` 모두 true,
`preflight_scope=production_ray_bvh`,
`provider_contract=strict_float64_bvh_v1`을 요구한다.

합성 경계조건에는 0/양의 미소 gap, 순서가 뒤집힌 중복면, 회전·이동,
큰 좌표의 작은 삼각형, trace exclusion, ignore face, min/max 거리,
공유 모서리, 경사 입사, 작은 방향 성분과 먼 출발점 guard가 포함된다.
이는 합성 교차 검사이며 전체 제품의 ROI/Transform 파이프라인 검증은 아니다.

## 독립 검토

한 명의 검증 담당(`gpt-6.1-sol / high`)이 고정 snapshot을 evidence-only로
검토했다. GPU/테스트를 새로 실행하지 않았고 제품/검증 입력을 수정하지 않았다.

R1에서 `assert_exact_query`가 서로 다른 길이의 배열을 NumPy broadcasting으로
같다고 판정할 수 있는 P2 결함을 찾았다. 실제 수송 실행의 행 수가 틀렸다는
증거는 아니지만 비교 계약의 결함이므로 수정했다. 양방향 행 수 불일치,
observer 오류 전파, hybrid CPU 유지, canonical 실패, 다른 thread의 진입을
추가 검사했다. 원시 감사기에도 shape 검사와 감사기 소스 SHA-256을 추가했다.

R1 snapshot manifest:
`610e833f120479fdf853cde6e7fe6209bf0f861d27a938045168272f03d96eb8`.
R2 snapshot manifest:
`9a09ea58846808a4b1cc441010958aa13896352cb3287285314f70b88776bbdd`.

R2는 R1의 비교 함수 결함 수정에 PASS를 주었다. 다만 원본 NPZ의 shape가
`(1,)`에서 `(1,1)`로 바뀌어도 byte가 같으면 감사기가 허용할 수 있는 별도 P2를
찾았다. 메인이 원본 row shape 검사와 두 회귀 테스트를 추가해 수정하고
최종 `audit-v3.json`으로 원시 근거를 다시 확인했다. 이 마지막 감사기 수정은
메인 검증이며 독립 재심까지 완료했다고 표시하지 않는다. 실제 실행 결과에서
잘못된 shape가 관측된 것은 아니다. Reviewer는 R2 종료 후 닫았다.

## 수정 후 실제 실행 결과

`transport-v2`: 직접 Face 광원, 확률 2회 반사, 갇힌 구조 10회 반사, TV 코너
CAD를 각 8,193 primary ray로 실행했다. CPU/GPU 각각 첫 실행 + warm 3회,
32개 기준/후보 쌍(64번 해석)이다. Normal production fused 경로가 아니라
host-roundtrip 진단 수송이며 저장 path 상한은 32다.

- 32/32에서 Receiver grid/flux/Peak/Mean 및 stored paths를 포함한 결과 동일.
- 같은 모드의 네 번 반복도 동일. CPU와 GPU 서로의 결과 일치를 뜻하지 않는다.
- 반복을 포함한 1,574,728개 교차 질의에서 보정 후 면 ID/거리 bit 차이 0.
- 후보 경로 내 전체 BVH 재탐색 273,480행, 단일 면 거리 재계산 1,301,248행.
- TV 코너 한 번의 90,116행 중 전체 BVH 재탐색 17,680행(19.62%).
  나머지 72,436행(80.38%)은 단일 면 재계산이다. **80.38% 속도 향상이 아니다.**
  검증을 위한 별도의 전체 oracle는 90,116행 모두 계속 실행했다.
- 최초 관찰에서 원시 면이 달랐던 54,108행은 수정 후에도 원시 차이로 기록한다.
  선택적 재검산 결과에서는 모두 기존 면과 일치했다.
- Flag 없는 차이 및 관찰한 기준 면 후보 방문 누락은 0. 직접광처럼 CAD hit가
  없는 대조군은 coverage 미평가이므로 전체 coverage 통과 표시는 false다.
- 원시 transport 감사: 8,928검사, 실패 0.

`boundaries-v2`: 18조건 × CPU/CUDA × 첫 실행 및 warm 2회 = 108기록,
27,648개 질의. 보정 후 면/거리 차이 0, 원시 감사 1,662검사 실패 0.
Guard 3,072행은 모두 기존 BVH로 재검산했다. 나머지 관찰에서도 기준 면
방문 누락은 없었으나 이를 임의 형상 전체의 coverage 증명으로 쓰지 않는다.
두 실행 모두 전후 입력 소스 hash가 동일했다.

Backend/Workspace/저장·복원/API 회귀는 94개 통과했고, 마지막 감사기 보강 후
focused 43개가 통과했다(기존 41개 재실행 + 원본 shape 관련 신규 2개).
전체 frontend는 540개 통과, 실패/보류 0이었다. Canvas 미구현 경고가 있는
jsdom 검사이며 브라우저의 3D 시각 검증으로 간주하지 않는다.
실제 GPU Workspace 저장/복원/광량 편집 후 재해석 검사도 포함했다.
최종 감사기 수정은 수송 실행 이후의 증거 검사 도구 변경이며 제품/후보의
교차 계산 코드는 바뀌지 않았다. 감사 JSON에 실제 감사기 SHA-256을 기록했다.

### Canonical 장치 증거

아래 CUDA/CPU 카운터는 독립 기준 dispatch의 실행 기록이다. OptiX 관찰 및
선택적 재검산 launch를 제품 CUDA 성공 수에 합산하지 않았다. CPU 요청도
진단 도구의 OptiX 부분은 GPU를 쓰므로 CPU 제품의 GPU 가속으로 표현하지 않는다.

| 장면 | 상태 | CUDA 성공/시도 | CPU small-wave 성공 | resident fallback |
|---|---|---:|---:|---:|
| Face direct | gpu_active | 2/2 | 0 | 0 |
| 확률 2회 | gpu_mixed | 1/1 | 3 | 0 |
| 갇힌 구조 10회 | gpu_mixed | 11/11 | 11 | 0 |
| TV 코너 | gpu_mixed | 4/4 | 18 | 0 |

Mixed 이유는 `gpu_cuda_hybrid_small_waves`다. Face direct의 emitter는
`face`, 다른 세 장면은 `datum_plane`이다. Hard fallback은 관찰되지 않았다.

다음 초 단위 시간은 GPU 요청의 **후보 query + 재계산 구간만**이다. 전체 해석
시간이 아니며 full oracle/coverage query/증거 쓰기 시간을 제외한다. 같은 장면의
첫 실행과 warm 3회를 기록했지만 외부 부하 격리 없는 진단값이므로 가속률을
산출하지 않는다. CPU 시간 및 전체 실행 시간도 JSON에 별도로 보존한다.

| 장면 | 첫 후보 구간 | warm 1 | warm 2 | warm 3 |
|---|---:|---:|---:|---:|
| Face direct | 0.010100 | 0.014026 | 0.010131 | 0.008379 |
| 확률 2회 | 0.207497 | 0.008173 | 0.010545 | 0.008717 |
| 갇힌 구조 10회 | 0.078501 | 0.069509 | 0.076003 | 0.074007 |
| TV 코너 | 0.052752 | 0.100231 | 0.139022 | 0.143228 |

최종 transport report SHA-256:
`b76a5b95c31be4f206b92b8e9be7f4d16296a3a01905814cf6104d290a81e6c3`.
최종 boundary report SHA-256:
`2ded05336616843fa2ed3c4959388f672f624aeb9cfa21727551c1aa3ec3a2fd`.

## 남은 채택 조건

**선택한 삼각형이 실제로 맞았다는 확인은 그것이 가장 가까운 삼각형이라는
증명이 아니다.** Flag 없는 질의에서 더 가까운 면이 후보에서 빠지는 경우까지
보장하지 못하므로 전체 oracle 검사를 제거하거나 제품 provider로 승격하지 않는다.

현재 후보는 host 복사, 임시 배열 할당, 작은 CUDA launch와 동기화 비용도
남아 있다. 선택적 BVH 재검산 행 수 감소는 그 자체로 실행시간 감소가 아니다.
진단 전체 시간에는 전체 oracle, coverage query와 원시 증거 기록도 포함된다.
외부 GPU 작업의 부하 격리는 확인하지 않았으며 정식 속도 비교로 쓰지 않는다.

다음은 보수적 후보 coverage/안전한 fallback 계약 확정, 상주형 수송 연결,
BEFORE/AFTER/CASE2 전체 시간의 고정 조건 비교 순서다. 실제 이득과 정확도
gate를 통과한 뒤에만 제품에 연결한다. Embree/5F MIS/LT 정합은 이번 범위 밖이다.

## UX 인수인계

| 항목 | 영향 |
|---|---|
| Workspace/Batch 관련 제품 파일 수정 | 없음 |
| `.bitsam` 저장·복원 schema | 변경 없음 |
| Case별 결과 연결/Batch 실행·취소 | 변경 없음 |
| Ray Path 형식 | 변경 없음 |
| 사용자 서버/원본 CAD/프로젝트 | 변경 없음 |
| 향후 확인 | 실제 제품 provider 연결 시 Workspace/Accessory/Batch 회귀 재실행 |

## 로컬 근거

`outputs/validation/perf5e-selective/` 아래에 최초 `transport-v1`, `boundaries-v1`,
수정 후 `transport-v2`, `boundaries-v2`, 각각의 native 빌드/manifest,
단위·회귀 로그와 독립 검토 snapshot을 보존한다. v1을 v2로 덮어쓰지 않는다.
보고서와 원시 증거는 일반 사용자 프로젝트 또는 해석 결과에 섞지 않는다.

최종 원시 감사는 `audit-v3.json`, `boundary-audit-v2.json`, 회귀는
`regression-v2.log`, `unit-v3.log`, `frontend-v2.json`에 있다. 실행 이후 추가한
감사기 및 문서의 변경을 해당 수송 실행의 새로운 제품 계산 변경으로 보지 않는다.
