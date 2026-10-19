# PERF-5D 독립 검증 담당 첫 운영 결과

## 결론

방법 A의 검증 담당 한 명(`Lagrange`)으로 읽기 전용 근거 검토를 실시했다.
메인이 핵심 발견을 직접 대조한 결과, **현재 코드에 대한 전체 승인 보류**가
맞다. 이번 검토에서 새로 확정한 Ray Tracing 제품 계산 오류는 없다.
다만 검증 도구의 결함·누락 2건과 TV 실행 기록의 소스 버전 불일치를 확인했다.
이것은 제품 결과가 틀렸다는 판정도, 전체 제품이 옳다는 판정도 아니다.

새 GPU 실행, 테스트, 제품 변경, 서버 재시작, commit/push는 하지 않았다.
기존 실행 결과와 소스·검증 스크립트에 대한 정적 검토 및 JSON 재계산 결과다.

## 운영 수칙 이행

- 임무: [`2026-10-19-perf5d-review-01`](2026-10-19_perf5d-review-assignment.md).
- 전달 경로: source checkout, `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`
  및 로컬 미커밋 변경을 포함한 고정 snapshot.
- 검증 담당은 지침과 GPU 문서 3개를 읽고 읽기 전용 범위를 확인했다.
- Snapshot 310개 파일(4,982,210 bytes)의 hash·크기를 시작/끝에 확인했다.
- 메인도 종료 시 310개를 대조했다. 누락·변경 0개다.
- 메인의 제품 소스·테스트·스크립트 282개도 위임 뒤 변경하지 않았다.
- 제품 코드 실행/import, GPU 사용, 파일 쓰기, nested delegation을 허용하지 않았다.
- 검증 담당은 결과 제출 후 종료했다. 역할과 수칙은 남지만 상시 감시는 아니다.

Manifest SHA-256:
`DA8AF1758B4FD5A4EFF09EA0CC107AFF87C11E8B8F720BE7472644100CC5064F`

Snapshot은 Python 코드와 관련 근거만 포함한다. 원본 CAD, `.bitsam`, frontend,
실행 환경을 포함하지 않으므로 전체 실행 가능한 배포본으로 취급하지 않는다.
로컬 근거는 `outputs/validation/2026-10-19-perf5d-review-01/`에 보존하며
Git에서 제외한다. 메인 재확인 기록은 `main-review-checks.json`이다.

## 확인한 발견과 메인의 판단

### 1. TV 기록과 현재 소스의 연결 부족 — BLOCKED

`outputs/perf5d_bvh/preparation-and-transport.json`에 기록된
`src/leakage_simulator/gpu_cuda_pair_scene.py` SHA-256은 다음과 같다.

- TV 실행 기록: `34fb054a5d336bc1cf6d3cf33eb2f05746c784e1a32190a648a4d965691083ed`
- 현재 snapshot: `d0fe7612f03b8302f5984b090ca6b0f5c18a097680ee8cf074f17fc9d1951c60`

메인도 불일치를 확인했다. 반면 CASE2의 기록된 source hash 52개는 snapshot과
일치한다. 따라서 TV의 준비 시간 약 44% 단축은 **당시 실행 기록의 측정값**으로
남기되, 현재 소스의 검증 결과로 그대로 승계하지 않는다. 당시 소스 확보와
변경 차이 확인 또는 현재 고정 버전에서의 재측정이 필요하다.

### 2. ULP 비교기의 부호 경계 오류 — FAIL / 검증 도구

`scripts/perf4_accuracy.py:48`의 signed-bit 변환은 음수에서 양수로 넘어갈 때
단조 순서를 유지하지 못한다. `-5e-324`와 `0.0`은 인접한 float64 값이지만,
해당 수식은 거리를 `18446744073709551615`로 계산한다.

메인이 PowerShell의 bit 표현과 정수 산술로 반례를 대조했다. 프로젝트 Python
코드를 실행한 것은 아니다. 이 오류는 0 부근 부호 변경 시 잘못된 실패를
유발할 수 있다. 이번 보조 집계 32 ULP 실패의 원인으로 단정하지 않는다.
비교기 수정과 ±최소 subnormal·signed zero 경계 회귀 검사가 우선 필요하다.

### 3. 실제 후보 경로 선택이 통과 조건에서 빠짐 — FAIL / 검증 도구

`scripts/benchmark_perf5d_bvh_preparation.py:157`은 GPU 활성과 성공 batch,
hybrid/fallback을 확인하지만 `gpu_pair_bvh_selected_batches > 0`을 요구하지
않는다. 따라서 정적으로는 일반 CUDA 경로의 성공을 후보 보조 BVH 검증 성공으로
잘못 판정할 수 있다. 실제 후보 선택·계약을 필수 판정에 넣어야 한다.

메인이 조건문과 기록을 대조했다. **기존 16회 기록은 모두 선택 batch=16**이다.
즉 이번 기록에서 후보를 사용하지 않았다는 발견이 아니라, 향후 거짓 통과를
막기 위한 검증 도구의 보완이다.

## 기록별 확인 범위

| 항목 | 판정과 한계 |
| --- | --- |
| 준비 시간 | 기존 JSON 중앙값 재계산 일치. TV 2.8478525→1.6076811초(-43.55%), CASE2 0.1992601→0.1306905초(-34.41%). TV 버전 연결은 별도 보류 |
| 전체 warm 시간 | TV +0.109%, CASE2 -1.357%. 최초 JIT 포함 비율을 가속률로 쓰지 않음 |
| 구조·이산 결과 | 저장된 tree hash와 이산 비교는 통과. 전체 tree 배열·최종 100만 Ray payload 원문은 이 결과 파일에 없어 재계산 가능 범위에 한계가 있음 |
| CASE2 Receiver | 8회 모두 219 hits, 저장된 엄격 비교 통과, 최대 1 ULP 차이. 메인도 hit 수·통과 필드를 확인 |
| TV Receiver | 8회 모두 0 hits. 0 결과 일치이며 비영 수광량 정확성의 근거가 아님 |
| 전체 보조 집계 | 32 ULP 기준 미통과 유지. 수정 전 자체 반복에서도 나타나므로 신규 BVH 변경의 오류로 단정하지 않음 |
| 회귀 검사 | 담당자가 기존 11모듈·133개 OK 및 기록된 source/test/script hash 일치를 확인. 이번에 재실행한 검사가 아님 |
| Sanitizer | 기존 로그는 각 4개 OK·오류 0. 로그 자체의 도구 버전·실행 소스 hash 연결은 보강 필요 |

GPU에 관한 다음 내용은 **기존 기록의 증거**다. 현재 PC나 서버에 대한 새
GPU 준비 완료 판정이 아니다.

- 장치: NVIDIA GeForce RTX 3070.
- 기존 preflight: `available`, `strict_float64`, `kernel_executed`,
  `kernel_verified` true, scope `production_ray_bvh`, contract
  `strict_float64_bvh_v1`.
- 16회 기록: `gpu_active`, CUDA 성공/시도 16/16, reason null,
  CPU hybrid·resident/intersection fallback 0, pair 선택 batch 16.
- 광원: 두 장면 모두 `datum_plane`; TV Lambertian, CASE2 Aim Area.
- 메인은 state·성공 batch·pair 선택 batch를 직접 재확인했다.

## 추가로 보강할 증거

- 실행 코드 hash에는 benchmark·비교기·API·대조군까지 포함하고 시작/종료를
  모두 확인한다. 일부 모듈의 `source_unchanged=true`를 전체 동결로 표현하지 않는다.
- 비교기의 수치 통과 조건은 `(절대오차 OR 상대오차 허용) AND ULP 허용`이다.
  기존 보고서의 “절대·상대” 표현과 구분하고 향후 contract 이름도 명확히 한다.
- 최종 Receiver 및 전체 비교 payload, 환경과 실행 명령을 재현 가능한 형태로
  보존한다. 사용자 형상·큰 근거는 로컬에 두고 자동 업로드하지 않는다.
- 4,096면 자동 선택 경계와 실제 GPU 업로드 후 캐시 무효화·메모리 회수는
  별도 실행이 필요하다. 기존 약한 참조 검사는 host 배열 해제 범위다.
- LT/실측 정합, 여러 seed 수렴, 전체 저장소 테스트, CPU 탐색 개선과 wide BVH는
  이번 검토 범위 밖이며 통과로 계산하지 않는다.

## 다음 순서

1. 메인이 ULP 비교기·후보 경로 통과 조건과 실행 증거 수집을 보완한다.
2. 고정 입력으로 경계 단위 검사를 수행하고 새 snapshot을 만든다.
3. 검증 담당이 변경과 실패 반례 처리를 다시 검토한다.
4. 수동 사용과 겹치지 않는 시간에 TV/CASE2를 현재 고정 소스로 재검증한다.
5. 결과와 남은 위험을 확인한 뒤 다음 가속 후보로 진행한다.

이는 후속 제안이며 이번 운영 수칙 정리 요청에서 제품·검증 코드를 수정하거나
새 GPU benchmark를 시작하지 않았다.
