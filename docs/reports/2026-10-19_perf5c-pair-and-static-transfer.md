# GPU 후보군 특수화와 고정 데이터 전송 개선

## 적용 상태와 목표

GPU의 광학 값·Receiver 고정 배열을 모아서 전송하도록 소스 기본 경로를 바꿨다.
배열마다 따로 하던 전송과 할당을 줄였으며, 계산식과 데이터 타입은 그대로다.
동일 소스 비교에서 확률 반사 약 1.061배, 흡광 상한 20 조건 약 1.145배의
추가 개선을 확인했다. CAD와 갇힌 구조는 약 2% 이내의 시간 증가로,
비대상 조건의 3% 이내 회귀 기준에 들어간다. 모든 조건이 더 빨라졌다는 뜻은 아니다.

별도의 1~2 hit 특수화는 CAD에서 현재 일반 GPU보다 1.380배 빨랐고,
고정 기준과 비교한 최신 결합 실험에서는 2.103배를 기록했다. 이 경로는 아직
실험 factory이며 제품 기본 실행에는 넣지 않았다.

**CPU와 GPU 각각 최소 2배라는 전체 목표는 미완료다.** 제품 기본 GPU의
CAD는 최신 비교에서 1.506배, 갇힌 구조는 1.981배다. 1.981배를 반올림해
2배 통과로 처리하지 않는다. CPU는 앞선 대표 7조건의 2.143~4.910배 결과를
유지하고 있으며 이번 제품 변경은 GPU 전송 경로에 한정했다.

서버를 재시작하거나 커밋·push하지 않았다. 현재 열린 서버의 메모리 세션에
새 소스가 이미 반영됐다는 의미는 아니다.

## 제품 고정 데이터 전송

기존 GPU 수송은 triangle normal, 면별 반사율·거칠기·분포와 Receiver 축·크기 등
17개 배열을 따로 올렸다. 이제 한 byte buffer 안에 각각의 원래 dtype·shape를
유지하는 독립 view를 만들고 한 번에 올린다. 각 view의 시작을 256 byte에
맞춰 기존 개별 CUDA 할당의 정렬 특성을 보존한다.

Summary의 고정 face/profile/Receiver 정보 4개도 별도의 한 buffer로 묶었다.
변하는 광량 집계 buffer와는 분리했다. 따라서 매 batch의 초기화·download에
변하지 않는 면 속성 정보를 덧붙여 보내지 않는다. 전송 수는 수송 고정 배열
17→1, summary 고정 배열 4→1이다. 기존 summary 숫자 buffer는 별도로 유지한다.

- FP64 값은 그대로 byte 복사하며 int8·int32·int64도 원래 타입을 유지한다.
  큰 정수 ID를 float로 바꾸지 않는다.
- 입력 배열은 수정하지 않는다. GPU view와 전체 buffer의 소유권을 함께
  보존하고, 서로 겹치지 않는 정렬된 범위를 사용한다.
- 같은 불변 context를 재사용하면 다시 올리지 않는다. 새 context의 반사율이나
  Receiver 설정은 다시 복사하므로 이전 값이 새 실행에 섞이지 않는다.
- 빈 Receiver·빈 배열, 홀수 면 수와 partial chunk도 기존대로 처리한다.
- 실패 시 기존 provider 오류 계약과 미확정 chunk 복구를 유지한다.
- 내부 대조용 `LEAKAGE_GPU_STATIC_TRANSFER=legacy`는 개별 전송을 사용한다.
  기본은 `packed`이며 잘못된 값은 검증 오류로 처리한다. UI 설정은 추가하지 않았다.

정렬 padding만큼 논리 전송 byte가 늘 수 있어 실제 buffer 크기로 계수한다.
변하는 summary의 출력 buffer 크기와 집계 순서는 바꾸지 않는다.

## 같은 세션의 고정 기준 비교

전달 방식은 source checkout이다. `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`와
미커밋 개선 소스를 비교했다. RTX 3070, Python 3.13 x64, Numba 0.66.0,
CUDA Toolkit 13.1에서 원본과 후보 순서를 교대하고 첫 실행 및 warm 7회를 기록했다.
아래 시간은 warm 중앙값이다. CAD와 흡광 조건은 100,000 Ray,
나머지는 1,000,000 Ray다. 흡광 조건은 첫 충돌에서 종료되므로 실제
100·1,000회 반사의 처리 속도로 해석하지 않는다.

기준은 기존 `157230c6` 수송 함수와 샘플링·집계 대조 경로다. 기준 실행은
개별 고정 배열 전송을 명시하고, 현재 소스의 공통 보조 함수를 사용하는
기존 고정 수송 benchmark 계약을 따른다. 새 checkout 전체의 독립 실행 비교는 아니다.

| 조건 | 고정 기준 초 | 현재 개별 전송 초 | 제품 묶음 전송 초 | 고정 기준 대비 |
| --- | ---: | ---: | ---: | ---: |
| TV 코너 CAD | 0.223852 | 0.145410 | 0.148633 | 1.506배 |
| Face 직접 도달 | 0.645720 | 0.267710 | 0.262905 | 2.456배 |
| 확률 2회 반사 | 0.411329 | 0.197881 | 0.186436 | 2.206배 |
| 갇힌 구조 상한 10 | 0.442377 | 0.219689 | 0.223336 | 1.981배 |
| 흡광 상한 20 | 0.063392 | 0.031630 | 0.027634 | 2.294배 |
| 흡광 상한 100 | 0.112362 | 0.031079 | 0.027626 | 4.067배 |
| 흡광 상한 1,000 | 0.671142 | 0.037365 | 0.035049 | 19.149배 |

같은 소스에서 전송만 바꾼 속도비는 CAD 0.978배, Face 1.018배,
확률 반사 1.061배, 갇힌 구조 0.984배, 흡광 세 조건 1.145·1.125·1.066배다.
다른 세션의 수치를 곱해 가속비를 만들지 않는다.

첫 CAD 실행은 기준 4.549초, 현재 개별 전송 5.101초, 묶음 전송 0.160초다.
같은 세션에서 개별 전송이 먼저 현재 kernel을 컴파일했으므로 묶음 전송의
첫 값은 독립 cold JIT 시간이 아니다. 다른 조건의 첫 실행·warm 원시 값과
실행 순서도 JSON에 남겼다.

특수화 CAD의 첫 해석은 4.643초에 별도 tree·factory 준비 1.830초가 더해져
약 6.473초다. 따라서 2.103배는 warm 속도이며 최초 준비까지 포함한 가속이 아니다.

원시 기록은 `outputs/perf5_transport/static-transfer-aligned-pair-fixed.json`이다.
초기 8-byte 정렬 실험은 `static-transfer-pair-fixed.json`에 별도 보존했으며,
최종 256-byte 경로의 성능 근거와 혼합하지 않는다.

## 실제 CUDA와 수치 계약

production preflight에서 `available`, `strict_float64`, `kernel_executed`,
`kernel_verified`가 모두 true이며, `preflight_scope=production_ray_bvh`,
`provider_contract=strict_float64_bvh_v1`이다.

모든 정상 비교는 `compute_backend=gpu_cuda`, `compute_execution_state=gpu_active`다.
현재 소스의 세 전송·특수화 조건은 CUDA 성공/시도가 CAD·흡광 2/2,
나머지 16/16이다. 고정 기준은 흡광 상한 100에서 7/7, 1,000에서 49/49이며,
나머지는 현재와 같다. CPU hybrid와 resident fallback은 모두 0이고
`monte_carlo_contract=cpu_gpu_deterministic_batch_v1`을 유지한다.
CAD·확률 반사·갇힌 구조·흡광은 Datum Plane, 직접 도달은 Face emitter다.
이산 결과와 광량·heatmap의 FP64 절대/상대 `1e-9` 기준을 전부 통과했다.
GPU atomic 집계를 bit-exact라고 표현하지 않는다.

단위 시험은 signed zero·NaN payload의 byte 보존, 2의 60승보다 큰 int64,
비연속 입력, 빈 배열, view 정렬·비중첩, 할당 실패와 불변 context 재사용을
검사한다. 실제 GPU에서는 홀수 면 수·Receiver 없는 장면·설정 재편집·부분
chunk의 값을 확인했고, ordered host 집계의 공개 결과와 경로도 exact였다.

## 임시 배열 없는 교차 후보군

기존 4·8 hit 실험의 local 후보 배열을 두 개의 scalar slot으로 바꿨다.
세 번째 근접 후보가 한 번이라도 나타나면 원래 GPU BVH로 돌아간다.
원래 방문 순서, 비추이적 근접 tie 규칙, source ignore, Receiver cap,
leaf 진입 시점 가지치기와 간섭 구간 검사는 그대로 유지한다.

같은 세션의 별도 warm 7회 비교는 다음과 같다. 이 표는 묶음 전송 변경 전의
특수화 단독 실험이며 제품 속도와 합산하지 않는다.

| 조건 | 일반 GPU 초 | 2 hit 특수화 초 | 특수화와 slab 결합 초 | 특수화 속도비 |
| --- | ---: | ---: | ---: | ---: |
| TV 코너 CAD | 0.149752 | 0.108478 | 0.128720 | 1.380배 |
| 확률 2회 반사 | 0.242270 | 0.238707 | 0.239435 | 1.015배 |
| 갇힌 구조 상한 10 | 0.230772 | 0.225810 | 0.235495 | 1.022배 |
| Face 직접 도달 | 0.280299 | 0.267287 | 0.275408 | 1.049배 |

후보 local memory는 원래 4 hit의 104 bytes 또는 8 hit의 168 bytes에서
기준과 같은 40 bytes로 줄었다. 진단 계수 실행에서 CAD 1,099,987개 질의 중
1,099,984개를 단축했고 3개만 원래 GPU BVH를 실행했다. CPU 대체 실행이 아니다.
Slab 결합은 CAD에서 느려져 채택하지 않는다.

최종 묶음 전송과 특수화를 결합한 CAD 실험은 0.106439초로 고정 기준 대비
2.103배였다. 반면 작은 흡광 조건에서는 1.905배에 그쳤다. 기본 경로로
전면 적용하지 않고 복잡도에 따른 선택과 배포 가능한 정적 구현을 검토한다.
실험은 source AST를 이용하므로 이를 그대로 제품 실행에 의존시키지 않는다.

특수화 전용 16검사는 82.612초에 통과했다. 별도 현재 전송·workspace·summary
17검사는 57.869초, 최종 256-byte 전송 8검사는 15.756초에 통과했다.

최종 관련 통합 회귀는 **55개 모듈 440검사, 548.095초, 실패·오류·skip 0개**다.
시험 전후 제품 소스와 모든 시험 파일 hash가 같음을 manifest로 확인했다.
Compute Sanitizer memcheck 4조건은 39.326초, synccheck 2조건은 22.825초에
통과했고 error summary는 모두 0이다. 실제 장치의 홀수 면 수·부분 chunk·빈
Receiver·다중 leaf 실제 1,000회 반사와 미세 간격 Receiver cap을 포함한다.

- `outputs/perf5_transport/static-transfer-full-regression.json`: preflight,
  source/test hash, 모듈 목록과 440검사 결과.
- 같은 폴더의 `static-transfer-full-regression.log`: 통합 원시 로그.
- `static-transfer-pair-memcheck.log`, `static-transfer-pair-synccheck.log`:
  실제 장치 sanitizer 결과.

추가 CPU scalar oracle은 무작위 배치한 384개 삼각형, 원래 127개 BVH node와
7단계 경로에서 40,000질의를 비교했다. 0·`5e-11`·`1.5e-10`·`5e-10`·`1e-6 mm`
간격, 양방향 hit, ignored face, Receiver 거리 cap과 근평행 방향을 포함하며
면 번호·거리 bit 차이는 0이었다. 간섭 구간 조건은 단축률이 아니라 원래
탐색으로 돌아가는 동작을 검사했다. 이는 GPU 실행 증거와 구분한 CPU 식 검산이다.
기록은 `outputs/perf5_transport/pair-cluster-multilevel-scalar.json`이다.

## 후속 완료 조건

복잡 CAD의 특수화를 source·EXE 모두 가능한 정상 kernel factory와 scene cache로
옮기고, 작은 장면은 기존 일반 GPU를 유지하도록 검증해야 한다. 이후 고정
7조건을 다시 측정해 모든 CPU·GPU 조건의 최소 2배와 비대상 회귀 기준을 확인한다.
현재 1.981배인 갇힌 구조도 추가 개선 대상이다. 전송 view 생성 비용과 kernel
자원 사용량을 다음 병목으로 검사한다. Ray 수·정밀도·반사율·종료 정책을
바꾸는 방법은 가속으로 인정하지 않는다. LT·실측 정합 검증은 별도다.
