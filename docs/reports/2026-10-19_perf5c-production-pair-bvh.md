# 복잡 CAD의 GPU 교차 계산 기본 경로 통합

## 적용 상태와 목표

복잡 CAD용 두 후보 교차 계산을 정상 소스 실행 경로에 통합했다. 실험 시에만
사용하던 AST factory가 아니라 정적 Python device 함수와 불변 scene cache를
사용한다. 원래 면 선택 규칙과 FP64 연산을 유지하며, 판단이 애매하면 원래
GPU BVH 탐색을 실행한다. CPU로 몰래 대체하는 최적화가 아니다.

같은 세션의 warm 7회 중앙값에서 TV 코너 CAD는 고정 기준 대비 **2.091배**,
직전 일반 GPU 경로 대비 **1.386배**다. 작은 장면은 기존 일반 kernel을 유지한다.
흡광 상한 20 조건은 이번 측정에서 1.875배이므로 CPU와 GPU 최소 2배라는
전체 목표는 아직 완료하지 않았다. 갇힌 구조의 2.010배도 여유가 작아 추가
반복 확인이 필요하다.

커밋, push, 서버 재시작은 하지 않았다. 열린 8788 서버의 메모리 세션과 사용자
작업은 그대로 두었으며 현재 소스 변경이 그 세션에 반영됐다는 뜻은 아니다.

## 실행 경로와 안전 장치

`gpu_cuda_pair_scene.py`는 원래 BVH와 면 배열을 보존한 채 보조 leaf 1 tree와
원래 방문 순서를 복원할 metadata를 추가한다. 실제 원래 scene 객체의 identity로
cache를 구분한다. 형상이 달라지면 새 scene을 만들고, 반사율이나 Receiver만
바꾸면 불변 형상만 재사용하며 새 광학 값은 새 context에 전송한다.

기본 `LEAKAGE_GPU_PAIR_BVH=auto`는 유효 삼각형 4,096개 이상이고 원래 tree가
여러 node인 장면에서 선택한다. `off`는 이전 일반 GPU 대조 경로이며 `force`는
작은 다중 leaf 시험용이다. 큰 좌표, 수치적으로 가는 삼각형이나 단일 leaf는
일반 경로를 유지한다. UI 옵션과 광학 설정은 추가하거나 변경하지 않았다.

두 후보가 원래의 같은 leaf에 속하면 그 leaf 내부 순서를 사용한다. 서로 다른
leaf이면 원래 tree의 공통 조상과 box 진입 순서로 우선순위를 재현한다. 기존의
근접 tie 규칙과 leaf 진입 시점의 가지치기도 그대로 적용한다. 세 번째 근접
후보, 간섭 구간, 경계 hit, 근평행 방향, 거리 cap이나 오차 위험이 있으면 원래
GPU 탐색으로 돌아간다. Ray 수, seed, 반사 상한, 종료 정책은 바꾸지 않는다.

Metadata는 기존 `ordered_faces`의 suffix에 원래 타입으로 저장하고, 음수 magic과
정확한 길이 검사를 거쳐 선택한다. uint64 경로 code는 int64 bitcast로 보존한다.
원래 triangle, mask, 면 번호와 kernel 입력 ABI는 유지한다. CPU 및 GPU
host roundtrip 경로에는 보조 tree를 만들지 않는다.

일반 kernel과 특수 kernel의 JIT cache를 분리했다. 제품 성능 정보의
`gpu_pair_bvh_selected_batches`는 특수 provider를 선택한 batch 수이며, 모든
Ray가 단축 경로를 통과했다는 계수가 아니다. 실제 CUDA 사용은 기존 execution
state와 성공 batch 수로 판정한다.

## 같은 세션의 고정 기준 비교

전달 방식은 source checkout, 기준은 `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`,
후보는 미커밋 기본 소스다. RTX 3070, Python 3.13 x64, Numba 0.66.0,
CUDA Toolkit 13.1에서 순서를 교대해 첫 실행과 warm 7회를 기록했다. 기준은
기존 고정 수송 benchmark와 동일하게 해당 revision의 수송 함수, 샘플링 및
집계 대조 경로를 현재 공통 보조 함수와 조합한다. 별도 checkout 전체 비교나
LightTools와의 속도 비교는 아니다.

| 조건 | 고정 기준 초 | 현재 일반 GPU 초 | 현재 기본 선택 초 | 고정 기준 대비 |
| --- | ---: | ---: | ---: | ---: |
| TV 코너 CAD | 0.222081 | 0.147148 | 0.106201 | 2.091배 |
| Face 직접 도달 | 0.633670 | 0.255730 | 0.265347 | 2.388배 |
| 확률 2회 반사 | 0.475037 | 0.226338 | 0.229566 | 2.069배 |
| 갇힌 구조 상한 10 | 0.444113 | 0.221971 | 0.220909 | 2.010배 |
| 흡광 상한 20 | 0.048070 | 0.026344 | 0.025640 | 1.875배 |
| 흡광 상한 100 | 0.092966 | 0.025394 | 0.025713 | 3.616배 |
| 흡광 상한 1,000 | 0.630609 | 0.032704 | 0.032932 | 19.149배 |

CAD와 흡광은 100,000 Ray, 나머지는 1,000,000 Ray다. 흡광은 첫 충돌에서
끝나므로 상한 1,000의 큰 속도비를 실제 1,000회 반사 속도로 해석하지 않는다.
기본 선택은 CAD에서만 특수 kernel을 사용했다. 작은 Face 조건은 같은 일반
kernel인데도 off/auto 중앙값이 약 3.8% 달라 별도 반복 측정으로 재확인했다.

작은 장면 두 개를 같은 세션에서 off/auto로 교대한 warm 15회 재측정은 다음과
같다. Face는 일반 0.182634초, 자동 0.186400초로 약 2.06% 시간 증가이며,
흡광 상한 20은 일반 0.035651초, 자동 0.034618초로 약 2.90% 시간 감소였다.
둘 다 특수 kernel 선택 batch는 0이고, 이 반복에서는 3% 이내 회귀 기준과
동일 광학 결과 기준을 통과했다. 기록은 `native-pair-small-auto-recheck.json`이다.
이 별도 세션의 절대 시간을 위 고정 기준 시간으로 나누어 가속비를 만들지 않는다.

첫 기본 CAD 해석은 6.467초이며 보조 tree 준비 1.709초를 이미 포함한다.
warm 2.091배를 최초 로딩 속도로 표현하지 않는다. CAD import와 사전 가속 구조
준비는 이 trace 시간에 포함되지 않는다. 같은 세션에서 앞선 경로가 컴파일한
공통 kernel도 있으므로 모든 첫 값을 독립적인 cold 실행으로 해석하지 않는다.

일반 kernel은 thread당 register 114개와 local memory 40 bytes, 특수 kernel은
130개와 40 bytes다. 후보 배열 없이 scalar 두 개를 사용하며, prototype에서
관측했던 자원 사용량을 정적 구현에서도 유지했다.

원시 결과는 `outputs/perf5_transport/native-pair-fixed.json`, 정상 완료 로그는
`native-pair-fixed-final.log`다. 초기 자원 정보 직렬화 오류로 JSON을 완성하지
못한 실행은 `native-pair-fixed.log`에 별도로 남겼고 최종 성능 표에는 사용하지 않았다.

## 실제 CUDA와 결과 보존

production preflight의 `available`, `strict_float64`, `kernel_executed`,
`kernel_verified`는 모두 true이며 `preflight_scope=production_ray_bvh`,
`provider_contract=strict_float64_bvh_v1`이다. 모든 정상 benchmark는
`compute_backend=gpu_cuda`, `compute_execution_state=gpu_active`다.

현재 기본 소스의 CUDA 성공/시도는 CAD 및 흡광 2/2, 나머지 16/16이며 CPU
hybrid와 resident fallback은 0이다. 직접 도달은 Face emitter, 나머지는 Datum
Plane이다. 고정 기준과 모든 비교에서 이산 결과 및 광량과 heatmap의 FP64
절대/상대 `1e-9` 기준을 통과했다. GPU atomic 집계를 bit exact라고 표현하지 않는다.

전용 12검사는 52.668초에 통과했다. CPU scalar 검산 4,000질의는 원래 다단계
tree와 정적 helper의 면 번호와 거리 bit를 비교했다. 실제 장치 시험은 미세
틈과 Receiver cap, 근접 후보 overflow, CAD 저장 경로, 반사율 재편집, ROI 면
속성의 네 가지 반사 분포, MIS와 partial tail, 실제 1,000회 반사 및 retry와
Stop, 실패 후 미확정 suffix만 복구하는 동작을 포함한다. CPU 실행 및 작은
자동 장면이 보조 kernel을 선택하지 않는 것도 검사했다.

최종 통합 회귀는 **56개 모듈 452검사, 546.298초, 실패와 오류 및 skip 0개**로
통과했다. 제품 소스, 시험 파일 및 검증 script 전체의 시험 전후 hash가 같았다.
기록은 `outputs/perf5_transport/native-pair-full-regression-final.json`과 같은
이름의 `.log`에 남겼다. 앞선 미완료 검증은 기존 실험 factory의 무인자 호출
가정 때문에 6개 class가 시작하지 못한 것이며, 선택적 교차 함수 주입 인자를
허용하도록 실험 도구를 수정한 뒤 관련 55검사와 전체 452검사를 다시 통과했다.

Compute Sanitizer memcheck는 실제 장치 4검사 26.368초, synccheck는 2검사
21.422초에 통과했고 error summary는 모두 0이었다. 미세 틈과 Receiver cap,
후보 overflow, 실제 1,000회 반사와 재시도, ROI 면 속성 및 MIS와 부분 chunk를
포함한다. 로그는 `native-pair-memcheck.log`, `native-pair-synccheck.log`다.

## CPU 성능 재확인

동일한 최종 제품 소스에서 CPU 고정 기준 비교도 다시 실행했다. 기존 4 worker와
현재 자동 경로 8 worker를 같은 세션에서 교대하고 첫 실행과 warm 3회를 기록했다.
Ray 수는 위 GPU 표와 같으며 CPU와 GPU의 속도를 서로 나눈 값이 아니다.

| 조건 | 기존 CPU 대비 |
| --- | ---: |
| Face 직접 도달 | 3.378배 |
| 확률 2회 반사 | 2.836배 |
| 갇힌 구조 상한 10 | 2.159배 |
| TV 코너 CAD | 2.276배 |
| 흡광 상한 20 | 2.391배 |
| 흡광 상한 100 | 2.245배 |
| 흡광 상한 1,000 | 4.965배 |

7조건 모두 공개 결과 bit exact 및 source hash 불변을 확인했다. 기록은
`outputs/perf5_cpu/native-pair-cpu-fixed.json`과 같은 이름의 `.log`다.
CPU 대표 조건의 2배 목표는 유지되지만 GPU 잔여 조건의 판정을 대신하지 않는다.

## 남은 성능 확인

짧은 흡광 workload의 고정 준비와 전송 view 생성 비용을 추가로 줄이는 것이
다음 대상이다. 작은 모델의 변동과 갇힌 구조의 여유가 작은 2배 결과도 계속
확인한다. 모든 대표 조건이 안정적으로 2배를 넘기기 전까지 목표를 완료로
처리하지 않는다. LT 및 실측
광학 정합은 별도 검증이며 이 성능 개선의 통과 기준으로 대체하지 않는다.
