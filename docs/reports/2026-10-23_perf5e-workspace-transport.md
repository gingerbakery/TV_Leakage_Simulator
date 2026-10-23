# PERF-5E Workspace 기준 재개와 다회반사 관찰 검증

## 결론

Workspace/Batch 인수인계 버전 위에서 PERF-5E의 다음 검증 단계를 구현했다.
OptiX 후보 계산을 실제 다회반사의 batch 질의에 연결하되, 최종 hit와 광학
결과는 기존 CPU/CUDA 판정을 그대로 사용한다. 제품 provider 전환이나 신규
속도 향상을 완료한 것은 아니다. 기존 미채택 MIS/정밀 교차 실험은 통합하지 않았다.

- 기준: `codex/ux-project-batch@373659d16bfb58190f8790c89ff7d71114acc887`.
- 확인 당시 `origin/main=b3371b89`; 위 UX 커밋은 main에 아직 포함되지 않았다.
- `src/`와 `frontend/`는 기준 커밋에서 변경하지 않았다.
- 기존 main 작업 폴더의 미커밋 실험, 실행 서버, 원본 사용자 프로젝트는 유지했다.
- 커밋, push, 서버 재시작, SDK/드라이버/Toolkit 설치는 하지 않았다.

## 구현 범위

이전 로컬 OptiX probe의 C++/CUDA/ABI-2 wrapper와 빌드 스크립트를 별도
`tools/perf5e_native` 경로로 가져왔다. 기본 앱에서 import하거나 선택하지 않는다.

새 `transport_validation.py`는 canonical batch dispatch의 결과를 받은 후
같은 ray를 OptiX에 전달한다. 원래 결과 객체를 반환하므로 candidate의 면 선택,
거리, flags를 제품 광학 계산에 사용하지 않는다. scene identity가 바뀌면
이전 native probe를 해제하고, 동일 scene의 반복 질의는 재사용한다.
예외/취소 시 dispatch를 복원하고 자원을 닫는다. 중첩·다른 thread의 관찰은
거부하며 독립 CLI 프로세스에서만 실행한다.

- `scripts/verify_perf5e_transport.py`: 첫 실행과 warm 3회, 결과·원시 query·형상
  배열·hash·실행 장치·반복성을 저장한다. 실패한 출력 경로는 덮어쓰지 않는다.
- `scripts/audit_perf5e_transport.py`: 저장한 NPZ/JSON을 다시 읽어 결과 보존,
  후보 누락, 면 선택 차이, hash를 재계산한다. 빈/중단/중복 실행은 통과시키지 않는다.
- API 필드, `.bitsam` schema, Batch 스케줄링, Ray Path 형식은 바꾸지 않았다.

## 실행 환경

Source checkout의 기존 `run_web_gpu` 준비 환경을 사용했다. requirements hash는
환경 marker와 일치했다. 최신 production preflight를 실제 실행했다.

- NVIDIA GeForce RTX 3070, driver 591.59, CUDA 13.1.
- Python 3.13.3, NumPy 2.4.6, Numba 0.66.0, llvmlite 0.48.0.
- 설치되어 있던 OptiX SDK 9.1.0와 MSVC로 probe를 새로 빌드했다.
- `available`, `strict_float64`, `kernel_executed`, `kernel_verified`: 모두 true.
- `preflight_scope=production_ray_bvh`.
- `provider_contract=strict_float64_bvh_v1`.
- 빌드 중 CUDA 헤더의 C4819 코드페이지 경고가 있었으며 빌드는 성공했다.

## 실제 수송 관찰 결과

직접 Face 광원, 확률 2회 반사, 갇힌 구조 10회 반사, 예제 TV 코너 CAD의
4장면을 사용했다. 각 8,193 primary ray, CPU/GPU 각각 첫 실행 + warm 3회다.
Ray Path는 최대 32개 저장했다. 이것은 production fused 실행 대신 기존
`host_roundtrip` 진단 경로를 사용한 **32개 기준/관찰 비교, 64번 해석**이다.

- 각 모드의 기준/관찰 결과와 같은 모드 반복 결과: **32/32 동일**.
- Receiver grid, 광량 지표, stored paths를 포함하며 runtime/run ID와 성능
  metadata만 비교에서 제외했다. CPU 결과와 GPU 결과가 서로 동일하다는 판정은 아니다.
- 반복 포함 **1,574,728개 교차 질의**를 관찰했다.
- reference hit가 있는 24개 기록에서 기준 면의 후보 방문 누락은 0개였다.
- 직접광 8개 기록은 CAD 교차가 없는 대조군이므로 coverage 미평가다.
  따라서 보고서의 전체 `observed_candidate_coverage_passed`는 false로 유지했다.
- 원시 증거 재검산: **2,733개 검사, 실패 0**.

### 남은 OptiX 면 선택 차이

예제 TV에서 후보의 원시 면 ID가 canonical 결과와 달랐던 질의는 CPU 기준
26,968개, CUDA/혼합 기준 27,140개, 총 **54,108개**다. 모두 probe의
근접/경계 flag가 있는 행이며 flag 없는 불일치와 reference 후보 누락은 0개였다.
각각 4회 반복을 포함한 개수다.

작은 거리 차이라고 광학적으로 동등하다고 판단하지 않는다. 현재 구현은
이 후보를 결과에 쓰지 않으므로 해석 결과에 영향이 없다. 이 증거만으로
flag 없는 질의의 보수성을 증명하거나 재검산을 제거할 수도 없다.
겹친 면의 물성 선택/엄격한 고정밀 교차 과제 역시 이번에 해결한 것으로 보지 않는다.

### Canonical GPU 실행 증거

아래는 관찰 실행 각 회차의 canonical dispatch다. OptiX launch는 별도
관찰 작업이며 제품 CUDA 성공 수에 섞지 않는다.

| 장면 | 상태 | CUDA 성공/시도 | CPU small-wave 성공 | fallback |
|---|---|---:|---:|---:|
| Face direct | gpu_active | 2/2 | 0 | 0 |
| 확률 2회 반사 | gpu_mixed | 1/1 | 3 | 0 |
| 갇힌 구조 10회 | gpu_mixed | 11/11 | 11 | 0 |
| 예제 TV 코너 | gpu_mixed | 4/4 | 18 | 0 |

혼합 실행 이유는 `gpu_cuda_hybrid_small_waves`다. CPU 요청은 canonical CPU
계산이고 별도로 OptiX 관찰을 실행한 것이므로 CPU 제품이 GPU 가속됐다고 하지 않는다.
직접광 emitter는 face이며 나머지는 datum-plane 기반 검증 광원이다.

### 시간 기록의 제한

아래는 **관찰 및 원시 증거 파일 쓰기까지 포함한 진단 시간(초)**이다.
정식 속도 비교를 위한 외부 부하 격리는 확인하지 않았다. 첫 행도 process 전체의
완전 cold 시작이 아닌 해당 장면의 첫 관찰이다. 가속률 산출에는 사용하지 않는다.

| 장면/기준 | 첫 관찰 | warm 1 | warm 2 | warm 3 |
|---|---:|---:|---:|---:|
| Face/CPU | 0.434 | 0.141 | 0.147 | 0.142 |
| Face/GPU | 0.158 | 0.157 | 0.146 | 0.155 |
| 2회/CPU | 0.268 | 0.256 | 0.259 | 0.259 |
| 2회/GPU | 0.185 | 0.181 | 0.180 | 0.175 |
| 10회/CPU | 1.770 | 1.686 | 1.691 | 1.677 |
| 10회/GPU | 0.764 | 0.773 | 0.775 | 0.770 |
| TV/CPU | 2.897 | 2.847 | 2.842 | 2.855 |
| TV/GPU | 0.940 | 0.975 | 0.954 | 0.976 |

## Workspace 및 Batch 보호 검사

- Backend/저장·복원/API/관찰 회귀: **64개 통과**, skip 없음.
- 원시 감사기의 실패/빈 실행 방지: **4개 추가 통과**.
- 전체 frontend: **540개 통과**, 실패/보류 없음.
- 기존 Batch의 Case별 결과 연결, 실패 후 계속, 취소, stale-result 방지,
  Workspace/store/Leak Preview 회귀를 포함한다. 실제 브라우저 화면 조작 검사는 아니다.
- 두 Case와 Accessory CAD가 있는 Workspace를 저장·복원한 뒤 실제 재해석했다.
  저장된 결과와 Ray Path가 유지되고, 광량을 절반으로 변경하면 해당 Case의
  재해석 광량만 절반이 되며 기존 저장 결과는 그대로 남았다. CAD 재파싱 없이
  캐시를 사용했다.
- 실제 GPU Case의 저장 전/복원 후/조건 편집 후는 모두 `gpu_active`, CUDA 1/1,
  CPU hybrid 0, resident fallback 0이었다. CPU/GPU 설정의 Case별 보존도 검사했다.
- frontend 테스트 중 jsdom Canvas 미구현 경고는 발생했으며 실제 3D 시각 검증을
  대신하는 증거로 사용하지 않았다.

## UX 담당자에게 전달할 사항

| 요청 항목 | 이번 변경 |
|---|---|
| 겹쳐 수정한 제품 파일 | 없음. `src/`, `frontend/`는 `373659d1`과 동일 |
| 저장·복원 데이터 구조 | 변경 없음 |
| Batch 실행/결과 연결 | 변경 없음, 기존 회귀검사 통과 |
| Ray Path 결과 형식 | 변경 없음, 관찰/저장·복원 비교에 포함 |
| 추가 확인 | 실제 fused OptiX 후보를 연결할 때 같은 검사 재실행 필요 |

자동 차광 생성 기능은 여전히 설계 단계다. 현재 Ray Path에 없는 미래의 안정적
Path ID/Emitter ID 등은 추가하지 않았다. `ray-path-mitigation-automation.md`의
별도 광학/출처 관리 이슈도 이번 속도 작업에서 해결했다고 간주하지 않는다.

## 다음 단계와 채택 기준

1. 원래 면 선택을 유지하면서 OptiX 후보 재확인 비용을 줄일 수 있는지 별도 후보로 검증.
   미소 gap/겹친 면/ROI/transform/traceability에서 누락·오지정을 허용하지 않는다.
2. 보수성 및 면 선택 기준을 통과한 후보만 상주형 다회반사/집계에 연결한다.
   현재 host 왕복 관찰 시간을 production fused 가속률로 쓰지 않는다.
3. BEFORE/AFTER/CASE2와 작은 대조군의 실제 전체 시간을 고정 조건에서 비교한다.
   기존 20% 개선/작은 장면 3% 이내 회귀 기준과 정확도 기준을 바꾸지 않는다.
4. Workspace/Accessory/Batch 취소·실패·stale 결과 검사와 실제 GPU 저장·재해석을 재실행한다.

PERF-5E 전체, CPU Embree, 5F MIS, LT/실측 정합, 100~1,000회 반사 확대 검증은
이번 단계 완료 범위가 아니다. 사용자 원본 BEFORE/AFTER/CASE2 파일은 이번에 열지 않았다.

## 근거 경로

로컬 ignored 경로: `outputs/validation/perf5e-ux-resume/`.

- `preflight.json`, `native/build-manifest.json` 및 DLL/PTX.
- `transport-v1/report.json`, 장면별 `baseline.json`/`shadow.json`/query·scene NPZ.
- `transport-audit-v1.json`: 최초 원시 재검산; `transport-audit-v2.json`: 빈 실행 방지 보강 후 재검산.
- `regression-v1.log`: 63검사; `regression-v2.log`: 실제 GPU Workspace 포함 64검사.
- `frontend-tests.json` 및 `frontend-tests.log`.

Transport report SHA-256:
`e611883148d8d635bd75c9b81ed0a3b359d18ed87f8f39c257d9e10c1123df8e`.
해당 실행 중 입력 코드 hash는 변하지 않았다. 이후 추가된 감사기 테스트와
설명 문서는 그 실행의 제품 계산 코드 변경이 아니다. 독립 subagent 심사는
이번 단계에서 실행하지 않았으며 모든 실행·재검산은 주 에이전트 작업이다.
