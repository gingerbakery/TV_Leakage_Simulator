# LT 검증 도구 통합 후 추가 검증

검증일: 2026-10-20 (KST).
사용자가 기존 통합 보고서를 검토하고 남은 검사를 수행한 뒤 main에 push하도록 승인했다.
앞선 보고서 `lt-validation-main-integration-2026-10-20.md`의 당시 기록을 유지하고,
그 이후 실제 실행한 검사를 이 문서에 추가한다.

## 검증 대상과 보존 범위

- 기반 main: `14c4b1fd9941382323e23fce9397314004bbdd3e`.
- 통합 제품 코드: `5952a1f7ec0799ee2b0ee656348551ae5f78186f`.
- 시작 시 원격 main: `8117761a6c2176936d1ffd95e7973285adf319f5`.
- 전달 형태: Git source checkout.
- 이번 추가 변경: GPU 저장 결과와 LT 도구를 연결하는 회귀 테스트 2개 및 이 보고서.
- RT/CPU/CUDA/BSDF/광량 산식/프로젝트 저장·복원 제품 코드는 수정하지 않았다.
  프론트엔드는 아래 원격 변경을 병합했으며 별도 수동 구현 변경은 하지 않았다.

원격 추가 변경 병합 전, 기존 추적 파일 831개의 시작 SHA-256과 검사 후 파일을
비교해 변경·누락 0건을 확인했다. 미추적 진단 파일 `scripts/test_bitsam_save_diagnostic.mjs`와
`tools/bitsam-save-diagnostic.html`도 SHA-256이 동일하며 커밋에서 제외한다.
다른 렌더링 worktree, 사용자 CAD와 해석 파일, 로컬 메모는 수정하지 않았다.

최종 fetch에서 원격 main의 새 커밋 `3a2be0bea38b0d679bbedc77d5f6ffb8fc86a041`
(`feat: extend 3d receiver heatmap controls`)을 발견했다.
`1beb5e57ef896b60bae3b0f64c12370cf2b7df9d`에서 충돌 없이 병합했다.
3D 히트맵 Existing/View Cone·Color/Mono·절단 표시·coverage 문구 변경을 보존하고,
같은 결과 패널의 LT 검증 진입점도 유지했다.
바뀐 9개 파일은 프론트엔드뿐이며, 검증한 backend 소스·테스트·Python 스크립트의
SHA-256이 병합 후에도 모두 같은지 다시 확인했다.

## 실행 환경

세 GPU 실행 지침을 읽고 `setup_windows_gpu.bat` 기본 모드로 읽기 전용 점검을
수행했다. 이어 공식 `run_web_gpu.ps1 -PreflightOnly`로 소스 환경 동기화,
production UI 빌드, 실제 Ray/BVH CUDA preflight를 수행했다.
서버를 시작·종료·재시작하지 않았으며 드라이버/Toolkit 설치나 시스템 변경도 하지 않았다.
무거운 CPU/GPU 검사와 프론트엔드 검사·빌드는 동시에 실행하지 않았다.

| 항목 | 확인 값 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 3070, compute capability 8.6 |
| NVIDIA driver | 591.59 |
| CUDA Toolkit | 13.1.1, nvcc 13.1 |
| Python | 3.13.3 x64, `.venv-gpu` |
| Node / npm | 24.14.0 / 11.9.0 |
| `available`, `strict_float64` | 모두 `true` |
| `kernel_executed`, `kernel_verified` | 모두 `true` |
| `preflight_scope` | `production_ray_bvh` |
| `provider_contract` | `strict_float64_bvh_v1` |

## 자동 검사 결과

| 검사 | 결과 | 범위 |
| --- | --- | --- |
| 고정 소스 backend 회귀 | 236 통과, 실패/오류/skip 0 | LT 파서·비교/API, 기존 CPU, 실제 CUDA, BSDF, 저장·복원 |
| 동일 실행의 소스/테스트/스크립트 hash | 불변 | `verify_perf5_regression.py` 시작·종료 대조 |
| GPU/CPU BSDF 추가 비교 | 14조건 / CUDA 실행 28회 통과 | 광량·격자·이산 판정, 독립 수치 적분 |
| 병합 전 프론트엔드 전체 | 57개 파일 / 492 통과, 79.04초 | LT UI 포함 |
| 병합 전 lint / production 빌드 | 오류 0 / 통과 | lint 경고 8, 큰 chunk 경고 유지 |
| 최신 원격 변경 병합 후 프론트엔드 전체 | 57개 파일 / 494 통과, 80.36초 | LT 및 새 3D 히트맵 UI 모두 포함 |
| 병합 후 lint / production 빌드 | 오류 0 / 통과 | 경고 종류·개수 동일; 최신 소스로 빌드 |

236개 회귀 테스트 실행 시간은 180.580초이며, 보고서 생성 등을 포함한 runner 시간은
182.007초이다. 전체 저장소의 모든 테스트를 실행했다는 의미는 아니다.
실행 모듈은 다음과 같다.

```text
test_lt_validation_api test_lt_validation_bitsam test_lt_validation_comparison
test_lt_validation_integration test_lt_validation_lts test_lt_validation_gpu_integration
test_bitsam_package test_api_fastapi test_raytracer_rt1 test_raytracer_rt2a
test_raytrace_bridge test_bsdf_import test_bsdf_packed test_bsdf_transport
test_bsdf_trace_guard test_perf5_bsdf_integration test_perf5_gpu_pair_production
test_perf5_gpu_event_window test_gpu_face_emitter_batch test_perf5_pair_sah
```

재현 명령은 준비된 GPU 소스 환경에서 `scripts/verify_perf5_regression.py`의
`--modules`에 위 목록, `--expected-tests 236`, `--output <결과 JSON 경로>`를 전달한다.
별도 BSDF 검사는 `tools/validate_bsdf_gpu.py --rays 100000 --output <결과 폴더>`이다.
기존 테스트의 실제 1,000회 반사, Face emitter, 미세 간극, ROI/면속성 재편집,
GPU event window, pair BVH, 강제 실패 후 미반영 구간 복구 검사도 포함했다.

## 추가한 GPU 저장·LT 도구 연결 검사

`tests/test_lt_validation_gpu_integration.py`는 해석용 합성 장면을 사용한다.
일반 경면과 BSDF 두 조건 각각에서 datum-plane Lambertian 광원 8,193 rays로
CPU 기준 해석, 같은 세션의 GPU 첫 실행 및 warm 2회, 저장·복원 후 GPU 재해석,
광원 광량을 절반으로 바꾼 GPU 재해석을 수행한다.

- GPU 결과를 실제 portable `.bitsam`으로 내보내고 production LT import/compare API로 읽는다.
- CPU/GPU 결과의 비교 가능 조건, 총광속 편차, 상관계수, 중첩률, 차이맵을 검사한다.
  여기서 비교 기준은 합성 CPU 결과이며, LightTools에서 얻은 데이터가 아니다.
- LT 도구 실행 중 live project 복원과 재해석 함수를 금지해 기존 세션과의 분리를 검사한다.
- 입력 요청과 저장 파일 bytes는 변하지 않는다.
- 새 runtime에서 복원된 저장 결과는 원래 결과와 정확히 같다.
  내장 형상을 재사용하며 CAD parser를 호출하지 않는다.
- 복원 후 동일 조건 재해석의 결과 계약을 검사하고, 광원 광량 절반 변경 시
  도달 ray 수는 유지되고 셀 flux는 절반이 되는지 `1e-12` 절대/상대 기준으로 검사한다.

두 조건의 총 10회 GPU 실행은 모두 `gpu_active`, reason `null`이다.
각 실행의 CUDA 시도/성공은 1/1, GPU resident 성공은 1이며 CPU hybrid,
intersection/resident/planner/reducer fallback은 모두 0이다.
같은 세션의 wall time은 아래와 같다. 작은 fixture이며 제품 전체 가속비가 아니다.

| 조건 | 첫 GPU 실행 | warm 1 / warm 2 |
| --- | --- | --- |
| 일반 경면 | 11.5095초 | 0.0389 / 0.0364초 |
| BSDF | 0.0610초 | 0.0542 / 0.0646초 |

첫 조건에는 resident kernel 준비가 포함되며 뒤 조건은 같은 프로세스에서 실행했다.
임의로 첫 실행 시간을 제외하거나 CPU/GPU 속도비로 일반화하지 않았다.

## BSDF 추가 검사와 엄격 수치 판정

기존 BSDF 검증 도구의 기준을 수정하지 않고 실행했다.
6개 기본 조건은 각각 100,000 rays와 GPU 첫 실행 + warm 2회,
8개 추가 조건은 20,000 rays로 검사했다.
반사율 0/1, 혼합 다회반사·roulette, detailed 집계, 두 BSDF 소재, Face emitter,
파장/AOI 보간, chunk 변경을 포함한다.

- 28회 모두 `gpu_active`, reason `null`.
- CUDA 시도/성공 합계 52/52, GPU resident 성공 52.
- CPU hybrid 및 intersection/resident/planner/reducer fallback 합계 모두 0.
- 이산 값과 ray 판정은 정확히 일치하고, 모든 실수 필드는 기존 절대/상대
  `1e-12` 기준을 만족했다.
- 독립 적분 대비 최대 광속 편차 0.182681%, 최저 상관계수 0.999579,
  최저 분포 중첩률 98.2248%로 기존 기준 1% / 0.99 / 95%를 만족했다.
- 기존 추가 8 ULP 감사는 **5/28회 실패**했다. 이를 통과로 바꾸지 않았으며
  개별 차이와 경로를 원본 JSON에 보존했다. 모든 필드의 최대 절대 차이는
  약 `4.87e-13`이다. 서로 다른 단위를 가진 필드를 합친 최대치이므로 광량 오차율이 아니다.

새 연결 테스트의 최초 실행에서도 CPU/GPU 전필드 ULP 조건에 의해 2건 실패했다.
주 원인은 0인 경로 좌표와 약 `1e-16 mm` 좌표의 큰 ULP 거리이며, 전필드 최대
절대 차이는 일반 경면 `3.55e-15`, BSDF `7.11e-15`였다.
초기 실패 로그 `gpu-lt-smoke.*`를 그대로 남겼다.

검사 목적을 구분하여 CPU/GPU 전필드 ULP 결과는 실패 상태 그대로 로그에 남기고,
CPU/GPU의 실제 단위 오차는 기존 `validate_bsdf_gpu.physical_float_parity`를 재사용했다.
새 허용오차를 늘리거나 계산 결과를 반올림·보정하지 않았다.
이산 값은 정확히 같아야 하고 Receiver 비교는 기존 `1e-12` 및 32 ULP 조건을 유지한다.
동일 GPU 반복/복원 재해석에는 기존 `transport_accuracy_gates`를 그대로 적용했다.
따라서 **전필드 bit/ULP 완전 일치 판정과 물리량 허용오차 통과는 별도**이다.

## 남은 검증과 전달 한계

- 이번 범위에서 새 제품 코드 수정이 필요한 회귀 오류는 발견하지 않았다.
- 이전 보고서의 GPU 미실행 항목은 위 조건에 한해 보완했다.
  모든 CAD/재료/장면의 보편적 정확성을 입증한 것은 아니다.
- **실제 LT 정합은 여전히 미검증**이다. 동일 조건의 원시 unsmoothed 결과와
  좌표·저장 순서·수광각 확인이 필요하다. CPU 결과를 LT 결과라고 표시하지 않는다.
- 프론트엔드 자동 검사는 실제 브라우저 GPU 렌더링의 육안 검사를 대체하지 않는다.
- 기존 jsdom canvas 미구현, lint 경고 8개, 500 kB 초과 번들 경고를 숨기지 않았다.
  LT 모듈의 혼합 export 경고 1개가 포함되며 나머지는 기존 BSDF/React hook 경고이다.
- 서버와 사용자의 열려 있는 세션을 변경하지 않았다.

## 증거 위치

전체 로그와 JSON은 Git에서 제외된
`outputs/validation/2026-10-20-lt-main-push/`에 보관한다.
`source-before.json`, `source-preservation.json`, `local-files-before.json`,
`inventory.log`, `preflight-launcher.log`, `integration-regression.*`,
`bsdf-gpu/report.json`, `post-merge-source-identity.json` 및 frontend 로그를 함께 남긴다.

추가 테스트 파일 SHA-256:
`B6B6C642614FA4A41B0B05FE25CC37536A4CEA3476C8BED3C837AA080C15234A`.

| 증거 | SHA-256 |
| --- | --- |
| `integration-regression.json` | `7BF85DAF66FBD86C125A77F09C9DB63168F8937612DB34C3166BEAD131361727` |
| `integration-regression.log` | `A88EFADCC84299A89E35DF15DA6E49DE37857CEA3FF99B13EDCC90581F4D53A3` |
| `bsdf-gpu/report.json` | `A1BA2BF61A15E2931183498B53262448825C2056025D7A52DCB94A2C4C90515C` |
| `source-preservation.json` | `53048CFF9A2478A5783B132D7F63640BA096CCC017ED7FE0FAB65D40099A3E24` |
| `post-merge-source-identity.json` | `CE8C8C553B8CE2745190AF2B3E66B49C3DA76F61BE4D10AD7720246FDF078227` |
| `frontend-final-test.log` | `23F0252632E80912696B059F4E3A8B67DC3C10A310517896C7608A3D7ADA424E` |
| `frontend-final-lint.log` | `D9D982461D3E60AAFD77EAB60C0843AF70D1E6AF856D9832E7FFE1C0A8948D9A` |
| `frontend-final-build.log` | `7D065E9A6CE77800BE94E119881AAC52DBA1AF569E3C120D1A63A393270FF24B` |

## 최종 통합 상태

최종 UI 재검사 후 다시 fetch했으며 원격 main은 `3a2be0be`로 변동이 없었다.
병합 commit `1beb5e57`은 해당 커밋을 포함하며 원격 대비 behind 0이다.
LT 진입점과 3D 히트맵 기능을 모두 보존했고 충돌 표식·공백 오류가 없다.
추가 테스트와 본 보고서만 선택적으로 커밋한 뒤 일반 main push를 수행한다.
실제 push 성공 여부는 이후 Git 응답과 원격 HEAD 대조로 확인한다.
강제 push, 이력 재작성, 사용자 파일 포함은 하지 않는다.
