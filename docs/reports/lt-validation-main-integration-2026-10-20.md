# LT 검증 모듈 main 통합 검증 기록

검증일: 2026-10-20 (KST). 사용자 승인 범위는 로컬 커밋·main 통합·검사이다.
원격 push는 수행하지 않았다. 서버 시작·종료·재시작, 시스템 설치도 수행하지 않았다.

## 버전과 통합 범위

- PERF-5 보존 기준: `0c409cad07bdc2687fd2b3c7e48ed6f2033f1b33`.
- 시작 및 검증 종료 시 다시 fetch한 원격 main:
  `8117761a6c2176936d1ffd95e7973285adf319f5` (3D receiver 히트맵).
- 작업 브랜치: `codex/lt-validation-main-integration`.
- LT 변경 정리: `33101f19`.
- 최신 main 병합: `2b71dc12`.
- 호환성·입력 검증 보완을 포함한 최종 제품 코드: `5952a1f7ec0799ee2b0ee656348551ae5f78186f`.
- 로컬 main을 위 제품 코드까지 fast-forward했다. 이 보고서는 그 이후 문서 커밋에 포함한다.
- 위 두 기준 커밋 모두 통합 이력의 조상임을 확인했다.

기존 LT 작업 worktree를 재사용했다. 원래 작업 폴더의 다른 개발 변경은 수정하지 않았다.
main의 미추적 진단 파일 두 개는 stage하지 않았고, fast-forward 전후 SHA-256을
비교해 내용 불변을 확인했다. 사용자 CAD, `.bitsam`, LT 원본 결과, 사진 및 로컬
메모는 커밋 대상에서 제외했다.

최신 main과의 충돌은 결과 패널의 아이콘 import 한 곳이었다.
`Grid3X3`와 `FlaskConical`을 모두 유지해 3D 히트맵과 LT 검증 버튼을 보존했다.
최신 main 대비 기존 backend 변경은 `api/app.py`의 독립 router 등록 두 줄뿐이다.
RT, BSDF, CPU/CUDA, 광량 계산, 결과 모델, 기존 프로젝트 저장·복원 구현은 변경하지 않았다.

## 모듈 동작과 이번 보완

- 결과 패널의 **LT 검증**에서 임의 개수의 case와 receiver를 선택한다.
- `.bitsam` 저장 결과 읽기, `.lts` 원시 mesh 추출, 조건 수동 보완,
  결과 표·정규화 히트맵·차이맵·U/V 합산 프로파일·검증 세션 저장/복원을 제공한다.
- 실제 simulator scene/job/store와 분리된 `/api/lt-validation` 경로를 사용한다.
- 최신 BSDF 설정 형식 `bitsam-project.v1-bsdf1`을 읽도록 보완했다.
  기존 `bitsam-project.v1` 및 v2 ZIP manifest 계약은 유지한다.
- LT 원본 배열과 연결된 수동 입력은 원본 치수·셀값·명시된 저장 순서를 검사한다.
  원본 배열 키가 누락된 불완전한 provenance도 차단한다.
- 확인할 수 없는 face 광원 형상은 사각 평면 광원과 같다고 가정하지 않는다.
- 수광각의 반각 설명은 기존 `(?)` 도움말에 넣었다.

총광속은 셀 flux의 합(lm), 조도는 flux/면적(m²)이다.
절대 수광량 편차와 각자 총합 1로 정규화한 분포 비교를 구분한다.
분포 정규화는 요구된 Pearson/중첩률/프로파일 계산에만 사용한다.
총광속 강제 보정, 격자 재표본화, 유사도를 높이기 위한 반전은 하지 않는다.
기존 조건 비교 허용오차는 완화하지 않았다.
새 원본 셀 대조 허용량은 단위 변환 반올림만 고려한 상대 `1e-12` / 절대 `1e-18 lm`이다.

## 실제 실행한 검사

CPU 검사와 frontend 검사·빌드를 순차 실행했다. 관찰 시 다른 빛샘 해석/
benchmark 프로세스는 없었다. 기존 Python/Node 환경을 사용했고, 패키지 설치나
환경 재구성은 하지 않았다.

| 검사 | 결과 | 확인 범위 |
| --- | --- | --- |
| `test_lt_validation*.py` | 68 통과, 50.356초 | 파서, 비교 산식, 오류 입력, API, 실제 저장 결과와 모듈 연결 |
| 기존 CPU 회귀 | 111 통과, 52.866초 | 아래 명시한 해석·BSDF·저장/복원 경로 |
| frontend 전체, `npm test -- --maxWorkers=1` | 57개 파일 / 492 통과, 98.19초 | 기존 UI와 LT UI |
| 최종 누락 provenance 보완 후 backend 비교/API 재검사 | 31 통과 | 수정 영향 경로 |
| 같은 보완 후 frontend LT/진입점 재검사 | 3개 파일 / 17 통과 | 수동 입력, 결과 표시, workspace 진입 |
| `npm run lint` | 종료 0, 경고 8 | 오류 없음; 경고는 아래 별도 기록 |
| `npm run build` | 통과 | TypeScript와 production 번들; LT worktree와 통합된 main에서 각각 실행 |
| `git diff --check` | 통과 | 공백 오류 없음 |

111개 CPU 회귀 검사 대상:
`test_bitsam_package`, `test_api_fastapi`, `test_raytracer_rt1`,
`test_raytracer_rt2a`, `test_raytrace_bridge`, `test_bsdf_import`,
`test_bsdf_packed`, `test_bsdf_transport`, `test_bsdf_trace_guard`,
`test_perf5_bsdf_integration.BsdfAccelerationIntegrationTests`.
GPU 테스트 클래스는 실행하지 않았다.

새 production integration 검사는 합성 장면으로 BSDF 사용/미사용 두 경우를 실행한다.
실제 `ApiRuntime`의 해석 → `.bitsam` 내보내기 → production API의 LT import/compare를
연결해 동일 결과의 편차 0, 상관계수 1, 중첩률 100%, 차이맵 0을 확인했다.
LT 작업 중 live project 복원·재해석 호출을 금지한 상태에서도 검사가 통과한다.
원본 bytes·입력 설정은 불변이며, 작업 후 재해석과 새 runtime의 저장·복원 후
재해석에서 receiver 격자와 저장 경로가 동일하다.

의도적 차이/오류 검사에는 수광량 배율, 원시 합계와 TotalPower 불일치, 위치/법선/
수광각/광원 조건/셀 경계/해상도 차이, 조도의 m² 변환, radiometric·smoothed
입력 거부, 음수·비유한 수치·상수·0 격자, 원본 배열의 형태/셀 편집 및 누락이 포함된다.
실패를 통과시키기 위한 보정이나 기준 완화는 하지 않았다.

중간 실패도 로컬 로그에 보존했다. 최초 production integration에서 발견한 BSDF
설정 스키마 미지원은 reader에서 수정했다. 합성 fixture의 누락된 U/V 축은
명시적으로 추가했으며 제품의 비교 조건을 생략하지 않았다.

## 독립 검토와 증거

검토 담당 한 명이 읽기 전용 `evidence_only` 모드로 고정 snapshot을 검토했다.
제품 실행·수정은 메인이 수행했다. 검토자는 테스트를 실행했다고 주장하지 않았다.

- 최종 snapshot: `outputs/validation/2026-10-20-lt-main-integration/review-03`.
- Manifest SHA-256:
  `12535C3D3F71902AC1D8E2B4C1FB42D5C9298C3E45BA44F0FA4D0D50EA7DC67F`.
- 40개 파일의 시작·종료 hash/크기 일치 확인.
- 스키마, 원본 보존, 무보정·단위·좌표, runtime/저장 분리, UI 도움말의
  지정된 5개 gate PASS. 이는 소스 및 제공 로그 범위의 판단이다.
- 검토에서 찾은 직접 편집·불완전 provenance·face 형상 문제는 수정하고 재검사했다.

아래 로그는 Git에서 제외된 같은 `outputs/validation/2026-10-20-lt-main-integration`
폴더에 보관한다. 로그의 SHA-256은 다음과 같다.

| 로그 | SHA-256 |
| --- | --- |
| `lt-with-integration.log` | `71751A2DAA21561B464D0CDB343634CE40651816DD048937F915650970B59F5D` |
| `cpu-regression.log` | `C1B03A3A9BF5C7F8EB0232A38DFECF1792EF35BCFC4EBC9E69AD6D7FE8A494A2` |
| `frontend-test.log` | `C272D26D661E3A3562661F7BE7BD74EEFF07881786D4C550C4941CA8D32C1A7B` |
| `lt-final-guard.log` | `5930E49B13AFBB34681651382FF9486F07A02279155F5851AD3F3840E1C9026A` |
| `frontend-lt-final.log` | `C101D43A3A04811CBC985A893B2BEE31C42550CF9D487B78F2FBB99E57FDD0BB` |
| `main-production-build.log` | `5F50895A8485205E4FBDA18B283F6D5DBE1E689DEE0F25158051D170CF5B7692` |

## 별도 판정과 남은 제한

| 대상 | 판정 | 한계 / 다음 검증 |
| --- | --- | --- |
| LT 검증 도구 구현 및 통합 | PASS, 위 합성/회귀 범위 | 실제 모든 LT 버전·장면 지원을 뜻하지 않음 |
| 기존 CPU 해석 및 저장·복원 후 재해석 | PASS, 명시한 fixture 범위 | 전체 CAD/물리 모델의 보편적 정확성을 뜻하지 않음 |
| CPU/GPU 일치 | NEEDS_RUN | 이번 통합 코드에서 새로운 GPU parity 검사는 수행하지 않음 |
| 실제 LT 정합 | NEEDS_RUN | 같은 조건의 2025.03 원시 결과 및 검증된 좌표/수광 조건이 필요 |

GPU 전달 형태는 source checkout이며 제품 코드 식별자는 위 `5952a1f7`이다.
GPU 실행 지침 세 문서는 읽었지만 이번에는 GPU preflight/해석/benchmark를 실행하지
않았다. 따라서 GPU명, production preflight 필드, 실행 state/reason, CUDA
시도·성공 수, CPU hybrid/fallback 수, GPU timing은 이번 실행 기록에 없다.
기존 PERF-5 이력 보존이나 CPU 검사 통과를 새로운 GPU 성공으로 판정하지 않는다.

제공된 LT 사진의 셀 표는 Smoothed였다. Unsmoothed 총광속만으로 원시 셀 분포를
복원하거나 실제 LT 정합을 확정하지 않는다. 2025.03 실제 `.lts`의 저장 순서·좌표
규약은 이번 검사로 확인되지 않았다. 정보가 부족한 업로드는 조건 보완 또는 비교
불가 상태로 남는 것이 정상 동작이다. provenance는 일관성 정보이며 입력 파일의
진위를 인증하는 기능은 아니다.

Frontend 전체 검사에는 jsdom의 canvas `getContext()` 미구현 경고가 있었다.
실제 브라우저 GPU 렌더링의 시각 검증은 이번 실행 범위가 아니다.
Lint 경고 8개 중 LT 모듈의 `formatMetric` 혼합 export 경고 1개가 포함된다.
나머지는 기존 BSDF 정규식 및 React hook 의존성 경고다.
Production 번들은 500 kB 초과 chunk 경고가 있으며 빌드 실패는 아니다.
성능 기준을 조정하거나 이 경고를 숨기지 않았다.

원격 main push는 이 보고서 전달 후 사용자 승인을 받아 별도로 수행한다.
