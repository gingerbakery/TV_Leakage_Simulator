# Workspace / Batch main 통합 검증

## 배포 범위

`origin/main@3e6d5ed55073efb0cabc9e4a1dd94925b721fc46`에
`373659d16bfb58190f8790c89ff7d71114acc887`의 Workspace 저장·복원 및
다중 Case 순차 Batch 해석 기능을 통합한다. 재료 라이브러리 공유와 선택적
Copy Setup 등 최신 main 기능도 보존한다.

- 원래 성능 개발 checkout은 그대로 보존하고 별도 detached worktree에서 통합했다.
- 충돌은 `simulator-shell.tsx`의 타입 import 1곳이었다. `CopySetupSection`과
  `WorkspaceSnapshot`을 모두 유지했다.
- 미완료 OptiX commit `1af25f07`, 후속 경계 실험, pair 선택 기준 후보와 원시
  사용자 형상·측정 자료는 배포하지 않는다. 새 GPU 가속률을 주장하지 않는다.
- 기존 안정화된 CPU/CUDA 수송 kernel 및 선택 기준은 main과 동일하게 유지한다.
- 이전 성능 작업의 저장 호환성 검사만 `tests/test_perf5_workspace_compatibility.py`로
  가져왔다. 이는 제품 가속 전환이 아닌 CPU/GPU 저장·복원·재해석 회귀검사다.
- 서버 재시작, driver/Toolkit 설치, 원본 프로젝트 수정은 하지 않는다.

## 검증 기준

전체 frontend 회귀와 production build, Workspace/Accessory/portable package/API/
LT 연계 회귀, CPU/GPU 실제 저장·복원·재해석을 검사한다. Batch의 Case별 결과
귀속, 실패 후 계속, 취소 및 stale-result 방지는 frontend 검사와 독립 소스
검토로 확인한다. 누락/실패를 통과로 바꾸거나 광학 허용 오차를 완화하지 않는다.

GPU 검사는 기존 공식 source launcher가 준비한 `.venv-gpu`를 재사용한다.
requirements fingerprint가 같으며 실제 import 경로는 통합 worktree의 `src`다.
외부 프로그램/서버는 사용하지 않고 합성 fixture의 임시 프로젝트로 실행한다.

## 검사 기록

- 첫 통합 frontend: 61 files / 559 tests 통과, 43.19초.
- TypeScript + Vite production build 통과. 500 kB 초과 chunk 경고는 남아 있다.
- RTX 3070 / CC 8.6 / driver 591.59 / CUDA Toolkit 13.1.1 / Python 3.13.3.
- 실제 production preflight: available, strict_float64, kernel_executed,
  kernel_verified 모두 true; production_ray_bvh / strict_float64_bvh_v1.
- Backend / portable package / Workspace / LT / API / CPU·GPU 저장 호환성:
  55 tests 통과, skip 없음, 68.598초. GPU 전용 회귀도 실제로 실행했다.
- BSDF 통합 수정 후 frontend: 62 files / 565 tests 통과, skip 없음, 24.62초.
  직전 수정 대상 3개 파일 묶음의 72 tests도 통과했다.
- 수정 후 TypeScript + Vite production build 통과. lint 오류 0개, 기존 경고
  8개이며 chunk 크기 경고와 함께 별도 후속 항목으로 유지한다.
- 최종 Stop 보완 및 test fixture 수정 후 frontend: 62 files / 566 tests 통과,
  skip 없음, 19.83초. TypeScript + Vite build와 lint도 다시 통과했다.
- GPU 원본/복원/조건 변경 후 재해석 각각 `gpu_active`, CUDA 성공/시도 1/1,
  hybrid CPU 0, resident fallback 0, reason null. 저장 결과와 경로를 보존하고
  새 해석에서 광원 power를 절반으로 바꾸면 광량도 절반이 되는지 검사했다.
  이 GPU Workspace fixture는 `datum_plane`, 8,193 rays 조건이다. 반복별
  성능 측정을 위한 실행이 아니며 전체 회귀 소요시간만 기록했다.
- CPU에서도 같은 저장·복원·광학 변경 후 재해석을 검사했다. 합성 fixture
  범위이며 실제 TV 전체의 물리 정확성 또는 성능 배수를 증명하지 않는다.

## 의미 충돌 보완

텍스트 충돌은 import 한 곳뿐이었지만, 독립 검토 R1에서 공유 BSDF와 Case별
복원·결과 관리의 의미 충돌 3개를 발견했다. R1 판정은 FAIL로 유지한다.

1. 같은 BSDF ID에 다른 계산 옵션/데이터가 있으면 마지막 Case가 앞선 Case의
   광학 조건을 덮어썼다. 자동 덮어쓰기를 거부하고 전체 Workspace는 임시 store에서
   검증한 뒤 교체한다. 단일 portable 및 legacy 프로젝트도 적용 전 검사한다.
   충돌 시 기존 Workspace는 유지된다. 서로 다른 정의를 자동 분리하는 마이그레이션은
   하지 않으며, 해당 Case의 BSDF 정의를 일치시킨 뒤 다시 불러와야 한다.
2. 공유 BSDF 변경 시 참조하는 활성/비활성 Case의 과거 결과와 결과 job 참조를
   무효화한다. 실행 중 Batch job 및 단일 실행 lock은 종료/취소 추적을 위해 유지하고,
   입력이 바뀐 Batch 결과는 기존 stale-result 검사를 통해 저장을 거부한다.
3. Batch 설정 hash는 활성 assignment가 참조하는 BSDF만 포함하도록 변경했다.
   사용하지 않는 라이브러리 자산의 등록/변경은 유효한 결과를 버리지 않는다.

새 회귀 6개는 수정 전 모두 실패했다. 수정 후 모두 통과하며 전체 검사도 통과했다.
UI 검사의 최초 fixture는 정상 초기 렌더가 추가하는 Component metadata까지 변경으로
판정했으므로, 불러오기 직전 상태를 비교 기준으로 잡았다. 저장/복원 보존 조건이나
수치 허용 오차는 완화하지 않았다.

R2 재검토에서는 BSDF 편집으로 결과 연결이 해제되면 진행 중 단일 해석의 Stop
버튼도 사라지는 회귀를 발견했다. 결과 표시용 ID와 실행 추적 ID를 분리하여,
`singleRayTraceRun`의 ID로 polling/Stop을 계속 수행한다. 중단 후 반환되는 과거
조건의 partial result는 저장하지 않고 실행 lock을 해제한다. 새 재현 검사는
수정 전 실패, 수정 후 관련 UI 49 tests에서 통과했다. 이후 전체 566 tests는
통과했지만 새 running-job fixture의 타입 오류가 production build에서 발견되어,
fixture에서 completed 결과 필드를 제거하고 전체 검사와 빌드를 다시 실행했다.

## 기존 기능과의 접점

- 제품 변경은 Workspace/Batch의 frontend 관리와 package/API 연동이다.
  최신 main의 공유 Material Library, 선택적 Copy Setup을 유지한다.
- CPU/GPU 수송, 교차 계산, 반사, Receiver 집계 kernel은 변경하지 않는다.
- Workspace 포맷은 `373659d1`의 계약을 유지한다. 추가 BSDF 충돌 검사로
  모호한 파일을 실패 처리하며 정상 파일 데이터 구조는 바꾸지 않는다.
- Batch는 여러 Case를 **순차** 실행한다. 병렬/동시 GPU 실행을 추가하지 않는다.
- Ray Path 결과 형식은 그대로다. 자동 차광 구조 설계는 문서만 포함한다.

## 독립 검토

검증 담당 Heisenberg (상위 모델 상속 / high)는 evidence_only로 소스·입력
계약을 검토한다. 직접 테스트/GPU/서버/API 실행이나 파일 수정을 하지 않는다.
특히 공유 Material Library와 Case별 Workspace 복원·Batch 조건의 상호작용을
검토한다. 메인이 실행 근거와 핵심 결론을 별도로 대조한다.

R1 snapshot: 842 files, manifest SHA-256
`253578d79d2798d75350728cf842b247897c02c2cd5709a66f4785036ca225d1`.
R2 source snapshot: 843 files, manifest SHA-256
`f457958adcfe9ac6ec3a2ff9628bb244289f8fb37648271a555e6e5eda568ab8`.
R2 runtime/문서 근거: 10 files, manifest SHA-256
`fbab6d3050ebf1cf443d024d35e969063bf191e0c5f796a7ec4358103b2bf021`.
R2는 최초 세 문제를 해결했지만 단일 실행 Stop 회귀가 남아 FAIL로 기록한다.
R3 source snapshot: 843 files, manifest SHA-256
`823e40876c841c8a16ffbd32bb0de92730d8431e032d1de5dd537f14faac3c59`.
R3 runtime 및 최종 test fixture overlay: 11 files, manifest SHA-256
`e75a481ff9f557dab015a93e49780a1ffa654eb22b2e914e71d689be0f2e04e5`.
R3 최종 독립 판정은 **PASS (이번 통합 범위 한정)**이다. 소스 843개와 runtime/
fixture overlay 11개의 manifest를 시작·종료 시 대조했고, 수정 전 재현 및 최종
실행 근거를 확인했다. 메인도 기록과 현재 소스 identity를 대조한 뒤 채택했다.
R1~R3에서 발견된 배포 차단 항목은 모두 해소했다. 원래 R1/R2 FAIL 기록은
그대로 보존하며, 이 판정이 모든 모델의 물리 정확성/새 가속률을 뜻하지 않는다.
로컬 근거는 `outputs/validation/workspace-main-integration-20261026/`이며
사용자 데이터 및 검증 snapshot은 Git에 올리지 않는다.

원래 개발 checkout의 미완료 실험 파일 26개와 성능 계획 문서의 SHA-256을
사전 기록과 대조하여 변경되지 않았음을 확인했다. 성능 실험 재개, 서버 재시작,
설치 및 다른 main checkout 갱신은 이번 작업에서 수행하지 않는다.

실제 브라우저 수동 조작, 새 성능 측정, 다중 seed 수렴 및 LT/실측 정합은
이번 통합 검사의 범위 밖이다.
