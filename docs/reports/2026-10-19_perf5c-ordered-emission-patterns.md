# GPU 방출광 합산 순서 안정화와 반복 패턴 가속

## 적용 상태

GPU 반사 방출광을 원래 광선 순서로 합산하는 방식을 기본값으로 바꿨다.
같은 광량이나 깊이별 광량 패턴이 반복되면 FP64 덧셈 결과를 그대로 유지하면서
반복 계산을 건너뛴다. 일반 데이터는 기존 GPU 순서 집계로 처리한다.

직전 기본 atomic 집계가 36/40이었던 엄격 반복 검사는 최종 기본값에서
40/40으로 통과했다. 통합 541검사도 실패·오류·건너뜀 없이 통과했다. 다만
GPU CAD 100만 Ray는 1.831배, 1,000만 Ray는 1.778배여서 CPU와 GPU 각각
최소 2배 목표는 아직 미완료다. 합성 갇힌 구조의 1억 Ray·10회 반사는
20.961초, 고정 기준 대비 2.388배였다.

전달 경로는 source checkout `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`와
미커밋 소스다. 기존 GPU launcher로 준비한 Python 3.13 x64, Numba 0.66.0,
CUDA Toolkit 13.1 환경과 NVIDIA GeForce RTX 3070을 사용한다. 시스템 설치,
서버 재시작, 커밋과 push는 하지 않았다. CPU 수송 소스는 이번 변경 대상이 아니다.

## 합산 순서를 보존하는 방법

동시에 더하는 atomic 방식은 실행 순서에 따라 FP64 마지막 몇 bit가 달라진다.
이 변화가 반사 방출광에서 33~41 ULP로 재현돼 기존 32 ULP 검사를 넘었다.
새 방식은 덧셈 순서를 바꾸거나 광량을 반올림해 맞추는 대신, 원래 순서의 결과를
GPU에서 재현한다. Ray 수, seed, 반사율, 종료 정책과 nit 환산식은 바꾸지 않는다.

출력 그룹별로 첫 광량의 bit, 다른 광량이 존재하는지, 현재 chunk의 방출 수를
기록한다. 첫 값은 정수 CAS로 한 번만 정하고, 서로 다른 값이 발견됐다는 표시는
해당 chunk에서 다시 지우지 않는다. GPU partition 안에서 같은 값만 나왔어도
partition 사이의 첫 값이 다르면 전체를 비균일 데이터로 처리한다.

광량이 모두 같은 그룹은 기존 누적값에 그 값을 반복해서 더한 FP64 결과를
빠르게 계산한다. 모든 광선의 깊이별 광량과 반사 유형까지 같으면 반복되는
여러 값의 순서도 같은 방식으로 처리한다. 이를 단순한 `광량 × 개수`로 바꾸지는
않는다. 서로 다른 초기 누적값을 가진 reflection과 contribution은 독립적으로
계산한다.

FP64의 같은 지수 구간에서는 표현 가능한 값 사이 간격이 일정하다. 알고리즘은
두 번의 반복을 실제 FP64로 계산해 정수 단위 이동량과 반올림 parity를 확인하고,
다음 지수 경계를 넘지 않는 반복만 건너뛴다. 경계를 넘거나 조건이 맞지 않으면
원래 덧셈을 수행한다. 0, subnormal, 반올림 중간값, 서로 다른 초기값과 overflow를
별도 검사한다. NaN이나 잘못된 방출광을 정상 상수로 숨기지 않는다.

초기 누적값은 block 전체가 함께 읽은 뒤에만 결과를 갱신한다. 일부 thread가
overflow 후 다른 분기로 들어가 동기화에서 멈추지 않도록 했다. 모든 shortcut은
GPU 내부에서 동작하며 CPU로 event를 내려받아 합산하지 않는다.

## 메모리와 기본 설정

추가 metadata는 출력 그룹당 24 byte이며 원래 `chunk_counts`의 첫 5개 정수는
보존한다. partition별 metadata도 같은 정수 저장소에 두고, 새 metadata 크기를
포함해 기존 8 MiB partition 예산을 적용한다. Ray 수와 최대 반사 횟수를 곱한
새 광량 배열은 만들지 않는다. 예를 들어 깊이 10에서는 global metadata가
336 byte, partition 128개의 추가 metadata가 43,008 byte다.

기본값은 순서 집계와 반복 패턴 검사를 함께 사용하며 내부 tile 폭은 2,048이다.
`LEAKAGE_GPU_EMITTED_FLUX=atomic`은 이전 비결정적 합산을 비교하는 진단용이고,
`LEAKAGE_GPU_EMITTED_UNIFORM=off`는 반복 최적화 없는 순서 집계 대조군이다.
새 UI 단추나 설명문은 추가하지 않았다.

## 채택 전 같은 소스 비교

첫 실행과 warm 7회를 분리하고 모드 순서를 교대했다. 아래는 tile 폭 4,096의
후보 비교이며 시간은 warm 중앙값이다. import와 첫 CAD 준비 시간은 제외한다.
이 표는 고정 revision 대비 전체 가속률이나 새 기본값의 최종 성능 표가 아니다.

| 조건 | 기존 atomic 초 | 원래 순서 집계 초 | 반복 최적화 초 |
| --- | ---: | ---: | ---: |
| Face 직접 도달 100만 Ray | 0.239955 | 0.240143 | 0.243361 |
| 확률 2회 반사 100만 Ray | 0.149292 | 0.168068 | 0.156153 |
| 갇힌 구조 100만 Ray | 0.221001 | 0.303629 | 0.225312 |
| TV 코너 CAD 10만 Ray | 0.097750 | 0.104740 | 0.109019 |

갇힌 구조는 느린 순서 집계보다 1.348배 빨라져 시간 증가가 atomic 대비 약
1.95%로 줄었다. CAD는 약 11.53%의 비용이 남는다. 정확도 개선만으로 전체
최소 2배 가속까지 달성했다고 처리하지 않는다.

별도 tile 비교에서 2,048은 4,096 대비 CAD 속도비 1.024배, 확률 반사
1.041배였고 갇힌 구조는 약 0.05% 느렸다. 이 작은 조정만으로 큰 가속을
주장하지 않는다. CAD 100만 Ray의 작업 묶음 32·64·256은 모두 128보다
느려 기존 128을 유지했다.

모든 정상 후보는 production preflight의 `available`, `strict_float64`,
`kernel_executed`, `kernel_verified`가 true이고 scope는 `production_ray_bvh`,
provider 계약은 `strict_float64_bvh_v1`이었다. 결과는 `gpu_active`, reason 없음,
CPU hybrid와 resident fallback 0이다. Face·확률 반사·갇힌 구조는 CUDA 성공/시도
16/16, CAD는 2/2였다. `cpu_gpu_deterministic_batch_v1` 샘플 계약과 이산 exact,
광학 출력 절대·상대 `1e-9` 검사도 통과했다.

반복 최적화 후보의 같은 세션 첫 실행과 첫 3개 warm 원본 시간은 다음과 같다.
각 모드의 첫 실행이지 별도 프로세스의 완전한 cold 시작은 아니다. 앞 표의
중앙값은 warm 7개 전체를 사용한다.

| 조건 | 해당 모드 첫 실행 초 | warm 1 초 | warm 2 초 | warm 3 초 |
| --- | ---: | ---: | ---: | ---: |
| Face 직접 도달 | 0.204868 | 0.204039 | 0.243361 | 0.242854 |
| 확률 2회 반사 | 0.151459 | 0.207961 | 0.149164 | 0.148687 |
| 갇힌 구조 | 0.230917 | 0.229230 | 0.225312 | 0.226574 |
| TV 코너 CAD | 0.106340 | 0.109286 | 0.109019 | 0.111891 |

## 정확도 검사

독립 순차 FP64 oracle과 CPU 및 실제 CUDA의 반복 패턴 계산은 각각 2,005개
수치 표본에서 bit 단위로 일치했다. 지수 범위, subnormal, 반올림 중간값,
반복 0회와 overflow를 포함한다. 실제 event를 사용하는 검사에서는 단일·분할
partition, 남은 일부 chunk, 서로 다른 초기 누적값, NaN, 실패 후 미반영 구간
재실행과 workspace 재사용을 확인했다.

새 기본값의 관련 19검사는 통과했다. 최종 기본값의 seed 7169~7173 ×
8,192/65,536 Ray × 4회는 strict absolute/relative `1e-12`와 최대 32 ULP
기준으로 40/40, 최대 17 ULP였다. 큰 값에서 최대 절대 차이는 약
`7.73e-12`이므로 모든 절대 오차가 `1e-12` 이내라는 뜻은 아니다. Receiver
셀은 여전히 atomic 방식이므로 전체 출력이 항상
bit 단위로 결정적이라는 의미는 아니다. LT·실측 정합은 별도 검증 대상이다.

최종 기본 소스의 61개 모듈·541검사는 793.858초에 통과했고 실패·오류·건너뜀은
모두 0이었다. 실행 중 제품·test·script hash가 유지됐다. 실제 1,000회 반사,
GPU record window 재실행, Stop, 표시용 Ray path 선택, 사용자 면 속성 및 CPU 수송 검사도
기존 통합 모듈에 포함된다.

최종 기본값의 Compute Sanitizer도 통과했다. memcheck 15검사는 49.402초에
오류 0, synccheck 3검사는 16.439초에 오류 0, racecheck 2검사는 7.466초에
hazard·오류·경고가 모두 0이었다. 반복 패턴, overflow 분기와 chunk metadata
재사용을 포함하며 전체 앱의 모든 입력을 증명하는 검사는 아니다.

## 최종 기본값의 고정 기준 성능

동일 세션에서 원래 reference 설정과 최종 기본값을 교대로 실행했다. 일반
범위는 warm 11회, 확대 범위는 7회, CAD 1,000만 및 갇힌 구조 1억 Ray는
3회의 중앙값이다. 첫 실행의 JIT 비용은 중앙값에서 제외하고 원본 JSON에
별도로 기록했다. 실제 설치 모델의 측정이나 LT와의 속도 비교는 아니다.

| 조건 | Ray 수 | 기준 초 | 최종 기본값 초 | 속도비 |
| --- | ---: | ---: | ---: | ---: |
| CAD | 10만 | 0.220079 | 0.096721 | 2.275배 |
| Face 직접 도달 | 100만 | 0.642270 | 0.267744 | 2.399배 |
| 확률 2회 반사 | 100만 | 0.469281 | 0.227756 | 2.060배 |
| 갇힌 구조·10회 반사 | 100만 | 0.453820 | 0.229951 | 1.974배 |
| CAD | 100만 | 1.507577 | 0.823287 | 1.831배 |
| Face 직접 도달 | 1,000만 | 6.307247 | 2.319444 | 2.719배 |
| 확률 2회 반사 | 1,000만 | 4.475914 | 1.989986 | 2.249배 |
| 갇힌 구조·10회 반사 | 1,000만 | 4.665771 | 1.980768 | 2.356배 |
| CAD | 1,000만 | 14.821743 | 8.334612 | 1.778배 |
| 갇힌 구조·10회 반사 | 1억 | 50.056757 | 20.961073 | 2.388배 |

흡광 모델은 상한 20/100/1,000에서 10만 Ray가 각각 2.027/3.997/20.555배,
100만 Ray가 3.078/5.728/33.260배였다. 이 모델은 첫 충돌에서 흡수되므로
실제 1,000회 반사 속도로 소개하지 않는다. CPU 소스는 이번에 바뀌지 않았고,
CPU 확대 7조건의 직전 2.141~5.763배 결과를 새 측정으로 표시하지 않는다.

모든 최종 측정은 source hash 유지, 이산 exact, 광학 출력 절대·상대 `1e-9`
검사를 통과했다. production preflight의 필수 여섯 필드는 모두 통과했고
GPU는 RTX 3070, 결과는 `gpu_active`, reason 없음, CPU hybrid와 resident
fallback은 0이었다. 표의 일반 CAD는 CUDA 성공/시도 2/2, 100만 CAD와
100만 일반 모델은 16/16, 확대 일반 모델과 1,000만 CAD는 153/153,
1억 모델은 1,526/1,526이었다. emitter는 Face 직접 도달만 `face`, 나머지는
`datum_plane`이고 동일 Monte Carlo 샘플 계약을 유지했다.

1억 모델은 실제 surface hit 10억 회의 합성 시험이다. 최종 기본값의 첫 실행은
31.437621초, warm은 20.961073/21.327096/20.911053초다. 복잡 TV의 1억 Ray나
1,000회 반사에서도 같은 시간이 나온다는 보장은 아니다.

## 남은 GPU 병목

최종 검증 뒤 두 가지 추가 후보를 실제 GPU에서 비교했지만 제품에는 넣지
않았다. 정확히 길이 1인 벡터의 정규화 생략은 30,026개 수치 표본에서 NaN
mask와 나머지 FP64 bit를 보존했고 관련 2검사를 통과했다. 하지만 CAD 100만
Ray 속도비는 현 기본값 대비 0.995배, 갇힌 구조는 0.993배로 개선되지 않았다.
확률 반사의 1.075배만 보고 모든 장면에 적용하지 않는다.

정수 tile 집계를 warp 단위로 나누는 후보는 관련 12검사를 통과하고 block
공유 메모리를 12,304에서 400 byte로 줄였다. 하지만 register가 thread당
64에서 71개로 늘었고 CAD는 0.984배, MIS는 0.987배, 확률 반사는 0.983배였다.
갇힌 구조의 1.017배만으로 채택하지 않는다. 두 비교 모두 같은 세션의 첫 실행과
warm 7회를 기록했으며 기본값의 전체 2배 목표와 혼동하지 않는다. 광량 집계가
없는 Face 직접광 조건에서도 시간 변동이 있어 작은 차이는 추가 반복이 필요하다.

출력 그룹이 같은 warp의 metadata 기록을 묶는 후보도 관련 12검사를 통과했다.
그러나 summary kernel의 register가 102에서 175개로 늘었고 CAD는 현 기본값의
0.978배, MIS는 0.923배, 확률 반사와 갇힌 구조는 각각 약 0.973배였다. 정수
atomic 횟수가 줄었다는 사실만으로 성능이 좋아진 것으로 처리하지 않고 이
후보도 제품에는 넣지 않았다.

기존 65,536개 단위의 광선 생성 순서를 유지하고 생성된 묶음만 연결하는 후속
실험도 진행했다. CAD 100만 Ray의 같은 세션 warm 7회 중앙값은 기본
0.888546초, 131,072개 요청 0.869319초, 262,144개 요청 0.879601초,
524,288개 요청 0.873177초였다. 속도비는 각각 1.022/1.010/1.018배로 충분하지
않아 채택하지 않았다. CUDA 성공 수는 16에서 8로 줄었고 event window retry는
모두 0이었다. 요청한 묶음 크기를 실제 단일 kernel의 처리 크기로 소개하지
않는다. 기존 event 슬롯 상한이 실제 분할 크기를 계속 제한한다.

이 네 후보의 수치 보존 검사는 통과했지만 성능이 일관되게 좋아지지 않아
최종 기본 소스는 통합 541검사와 strict 40/40을 통과한 상태 그대로 유지했다.
후보 검사와 기본 소스 검사를 합쳐 모든 경로가 새로 통합 검증된 것으로
표시하지 않는다. 커밋·push·설치·서버 재시작은 하지 않았다.

새 기본값의 별도 계측에서 방출광 집계 단계는 확률 반사 100만 Ray 약
0.00294초, 갇힌 구조 100만 Ray 약 0.00308초였다. 두 조건은 반복 shortcut을
사용했다. CAD 10만 Ray는 약 0.00903초였고 shortcut 0개, 정수 구간 tile 1,457개,
순차 tile 109개였다. 이 시간은 host launch와 GPU 대기를 포함한 단계 계측이며
전체 해석 시간이나 순수 device 시간으로 해석하지 않는다.

후속은 가변 광량의 순서 집계에서 block 동기화와 GPU 활용률을 개선하는 것이다.
덧셈 순서와 반올림 조건을 유지한 채 처리량을 높이며, 기존 실패 기준을 완화해
성능 목표를 통과시키지 않는다.

## 후속 단계 계측과 후보 판정

metadata의 개수 증가만 warp 단위로 묶고 기존 광량 검사는 유지하는 별도 후보도
12검사를 통과했다. CAD 100만 Ray는 0.930532에서 0.918532초로 1.013배였지만
MIS는 0.943배, 확률 반사는 0.988배, 갇힌 구조는 0.978배였다. register가
102에서 161개로 늘어 이 후보도 채택하지 않았다.

CAD에서 무엇이 시간을 차지하는지 CUDA event로 각 kernel의 실행 구간을
분리했다. 새 계측기를 넣은 첫 실행은 별도 warmup으로 보관하고 이후 3회의
중앙값을 사용했다. 계측은 kernel마다 기다리므로 원래 실행의 overlap과
host 제출 간격이 달라진다. event 구간에 host 제출 대기가 포함될 수 있어
순수 연산 시간이나 일반 실행 wall time의 가속률로 소개하지 않는다.

| 조건 | 교차·반사 kernel 구간 | 일반 summary 구간 | 방출광 순서 집계 구간 |
| --- | ---: | ---: | ---: |
| CAD 100만 Ray | 590.808 ms | 35.342 ms | 84.458 ms |
| MIS 65,536 Ray | 0.557 ms | 0.270 ms | 1.818 ms |
| 갇힌 구조 100만 Ray | 32.500 ms | 34.220 ms | 0.239 ms |

CAD의 교차·반사 구간은 계측된 전체 kernel 구간 합의 약 83%였다. MIS에는
추가 Receiver 집계 구간 약 4.29 ms가 있고, 갇힌 구조는 일반 summary 비용도
커서 서로 같은 병목으로 취급하지 않는다. 모든 계측 결과는 원래 payload와
이산 exact 및 절대·상대 `1e-9`를 통과했고 source hash도 유지했다. preflight는
RTX 3070에서 필수 여섯 항목을 통과했으며 모든 run은 `gpu_active`, reason 없음,
CPU hybrid와 resident fallback 0이었다. CAD와 갇힌 구조는 CUDA 16/16,
MIS는 1/1이고 emitter는 모두 `datum_plane`이다.

이 결과를 바탕으로 CAD의 빠른 BVH 후보 검사와 원래 BVH 재검산 사이에서
같은 방향의 reciprocal 3개를 재사용하는 실험을 추가했다. 미세 간격, Receiver
거리 제한, 실제 1,000회 반사·재시도·Stop, ROI 면 속성과 실패 후 suffix
재실행을 포함한 7검사는 통과했다. 그러나 같은 세션 warm 7회 중앙값이
0.878233에서 0.879740초로 0.998배여서 채택하지 않았다. register 148개와
thread당 local 메모리 40 byte도 변하지 않았다.

다음 우선순위는 작은 summary 변경보다 CAD BVH의 실제 메모리 접근과 탐색
작업량이다. 이전 node 배치 실험은 80-byte 행의 strided view였으므로 새로운
64-byte 정수·FP64 혼합 행과 직접 인덱싱의 가능성을 별도 검토한다. FP64 경계와
기존 face 선택·재검산 계약은 그대로 유지해야 하며 아직 구현·채택된 내용은
아니다. 현재 기본 제품 소스와 2배 미달 판정은 바뀌지 않았다.

## BVH 직접 배치 실험

위의 후속으로 strided view가 아닌 직접 인덱싱 방식의 80-byte 및 64-byte
node 행을 실제 구현해 비교했다. 64-byte 행은 FP64 경계 좌표 여섯 개를 그대로
보관하고 signed int32 index 네 개를 bit 단위로 묶는다. index가 int32 범위를
벗어나면 잘라 넣지 않고 거부한다. 원래 host 형상은 바꾸지 않으며 작은
장면과 CPU·일반 GPU 경로는 원래 배치를 유지한다.

CPU의 모든 경계 bit·signed index 왕복 검사, 실제 CUDA의 NaN bit 패턴을 가진
packed word 복원, 미세 간격, 면 속성 재편집, 실제 1,000회 반사, Stop과 실패 후
suffix 재실행을 포함한 18검사를 80.047초에 통과했다. 그러나 같은 세션의
CAD 100만 Ray warm 7회 중앙값은 기존 0.822462초, 80-byte 0.822547초,
64-byte 0.822130초로 각각 0.99990배와 1.00040배였다. 이 차이는 유의한
성능 개선으로 처리하지 않으며 두 배치 모두 기본 코드에는 넣지 않았다.
80-byte의 register는 기존과 같은 148개, 64-byte는 149개였고 local 메모리는
모두 thread당 40 byte였다.

이산 exact 및 절대·상대 `1e-9`, source hash 유지와 production preflight 여섯
항목을 모두 확인했다. RTX 3070의 source checkout에서 모든 run은
`gpu_active`, CUDA 16/16, reason 없음, CPU hybrid와 resident fallback 0이었다.
emitter는 `datum_plane`이며 최초 실행도 원본 JSON에 별도로 남겼다. 첫 CAD
import와 GPU JIT를 줄인 결과나 실제 TV 전체 모델의 성능 보장으로 해석하지 않는다.

추가 hardware counter profiler인 `ncu`와 Nsight 설치 폴더는 현재 PC에서 찾지
못했다. 임의 설치나 시스템 설정 변경은 하지 않았다. 기존 CUDA event 진단과
실제 수송 검사는 계속 사용할 수 있으므로 이것을 작업 중단 사유로 삼지 않는다.

다음 검토는 단순 node 배치가 아닌 광선 작업의 분기·탐색 재정렬이다. fused
kernel을 반사 단계별로 이어 실행하고 GPU에서 작업을 묶는 후보를 검토한다.
원래 primary별 RNG key, global 반사 깊이, 원래 슬롯의 event 순서와 종료·실패
계약을 유지해야 한다. 새 구조 역시 먼저 별도 후보로 검증하며 현재 기본 코드와
기존 가속 수치는 바뀌지 않았다.

## 현재 BVH를 연결한 단계별 실행

과거에도 단계별 방향·위치 정렬 실험은 있었지만 원래 canonical BVH만
사용했다. 이번에는 그 큐·반사 처리 구조에 현재의 pair BVH와 원래 BVH
재검산을 연결했다. 원래 primary 슬롯의 event와 global 깊이별 RNG key를
그대로 유지했다. 방향 1/8/64그룹과 위치·방향 512/4,096그룹을 비교했다.

방향 8그룹의 관련 7검사는 64.702초, 두 위치·방향 후보의 14검사는
85.328초에 통과했다. 검사는 미세 간격, Receiver 거리 제한, 사용자 면 속성
재편집, 실제 1,000회 반사, record window 재시도·선택 path 재추적, Stop,
실패 후 미반영 suffix 재실행과 작은 장면·CPU 분기를 포함한다.

| 같은 세션 CAD 100만 Ray 비교 | 기존 기본값 초 | 후보 초 | 기본값 대비 속도비 |
| --- | ---: | ---: | ---: |
| 방향 1그룹·단계 분리 | 0.819052 | 1.013052 | 0.8085배 |
| 방향 8그룹 | 0.819052 | 1.007941 | 0.8126배 |
| 방향 64그룹 | 0.819052 | 0.971691 | 0.8429배 |
| 위치·방향 512그룹 | 0.824732 | 0.984637 | 0.8376배 |
| 위치·방향 4,096그룹 | 0.824732 | 0.982575 | 0.8394배 |

모두 첫 실행을 별도로 기록한 warm 7회의 중앙값이다. query register는
114개, shade는 96개로 원래 fused kernel의 148개보다 줄었지만 전체 실행은
느려졌다. register 감소나 작업 정렬만으로 가속을 달성했다고 판단하지 않는다.
이 다섯 후보는 기본 코드에 채택하지 않았다.

이어 큐·정렬을 없애 원래 슬롯 순서를 유지하는 별도 단계 후보를 만들었다.
query와 shade 사이의 중복 Receiver 교차 계산도 없앴다. query는 최종적으로
도달하는 Receiver 정보만 기존 terminal 배열에 기록하며, shade가 그 값을
읽어 최종 상태를 만든다. 형상에 먼저 충돌하면 Receiver scratch를 쓰지 않아
blocked 상태의 기본값을 보존한다. 추가 배열은 Ray당 distance와 face의
16 byte이며 Ray 수와 깊이를 곱한 새 배열은 만들지 않는다.

이 슬롯 후보도 관련 7검사를 62.712초에 통과했지만 같은 세션 warm 7회가
0.824685에서 1.009393초로 0.8170배였다. query 130개, shade 78개 register를
사용했다. 정렬을 제거하고 Receiver 계산을 재사용해도 전체 속도 이득이
없으므로 채택하지 않았다.

이 절의 모든 성능 후보는 source hash 유지, 이산 exact 및 절대·상대 `1e-9`를
통과했다. source checkout·RTX 3070의 production preflight 여섯 항목을 통과하고
모든 run이 `gpu_active`, reason 없음, CUDA 16/16, CPU hybrid와 resident
fallback 0이었다. emitter는 `datum_plane`이다. 관련 후보 검사를 기존 기본값의
통합 541검사나 strict 40/40과 합쳐 새 통합 완료 수치로 표시하지 않는다.

## Fused 탐색 스택 후보

기존 fused 실행과 광선 순서를 유지하면서 fast pair 탐색 스택의 앞 4칸 또는
8칸을 shared 메모리에 두는 후보를 검사했다. thread마다 독립된 칸을 쓰고
더 깊은 스택과 기존 BVH 재검산은 원래 global 저장소를 유지한다. FP64 거리와
int64 노드 번호를 줄이지 않았으며 global 배열도 남으므로 VRAM 절감은 아니다.
다른 후보는 가까운 자식을 바로 방문하고 먼 자식만 스택에 넣는다. 원래 탐색
순서, 거리 여유값과 overflow 시 재검산 조건을 유지했다.

관련 23검사는 96.668초에 통과했다. 첫 검사에서는 scalar fixture가
`(center, normal)`을 좌표 하나로 읽어 호출 인자 수 오류가 났다. fixture를
수정한 뒤 별도 scalar 2검사와 전체 23검사를 다시 통과했다. 제품 오류를
수정한 결과가 아니며 실패 로그도 보존했다. scalar 검사는 근접 면, 양방향
입사, 거의 평행한 방향, Receiver 거리 제한과 제외 면에서 거리 bit·면 번호·
재검산 상태를 원래 구현과 비교한다. 실제 GPU 검사는 1,000회 반사,
재실행·재추적·Stop과 ROI 면 속성 변경을 포함한다.

| 같은 세션 CAD 100만 Ray 비교 | warm 중앙값 초 | 기본값 대비 속도비 | register/thread | shared/block |
| --- | ---: | ---: | ---: | ---: |
| 현재 기본값 | 0.824135 | 1.000000배 | 148 | 0 byte |
| Shared 스택 4칸 | 0.825360 | 0.998517배 | 160 | 8,192 byte |
| Shared 스택 8칸 | 0.821421 | 1.003304배 | 160 | 16,384 byte |
| 가까운 자식 즉시 방문 | 1.007725 | 0.817818배 | 149 | 0 byte |

최초 실행과 warm 7회를 분리하고 순서를 교대했다. 첫 실행은 순서대로
13.477439, 7.394579, 7.400322, 7.162505초이며 각 모드의 JIT 등 준비 비용을
포함하므로 가속률로 사용하지 않는다. local 메모리는 모두 thread당 40 byte다.
0.33% 차이는 유의미한 개선으로 채택하지 않았고 세 후보 모두 기본 코드에서
제외했다.

전달은 source checkout `main@157230c6`와 미커밋 소스, 장치는 RTX 3070이다.
production preflight 여섯 항목과 source hash 유지, 이산 exact 및 절대·상대
`1e-9`를 통과했다. 모든 run은 `gpu_active`, reason 없음, CUDA 성공/시도
16/16, CPU hybrid와 resident fallback 0이며 emitter는 `datum_plane`이다.
기존 통합 검사 manifest의 제품 파일 48개 hash도 그대로다. 새 실험 23검사를
통합 541검사에 더해 새 통합 완료 수치로 표현하지 않는다.

현재 직접 테스트할 기본값은 그대로이며 GPU 복잡 CAD의 고정 기준 속도비는
100만 Ray 1.831배, 1,000만 Ray 1.778배다. 이 두 조건에서 2배에 도달하려면
현재 시간에서 각각 약 8.44%, 11.08%를 더 줄여야 한다. 이는 목표와의 수치적
간격이지 남은 개발 기간이나 달성 보장이 아니다. 다음 계측은 이전 guarded
후보가 아닌 현재 pair 경로의 노드 방문·삼각형 검사·기존 BVH 재검산을 구분한다.
서버 실행, 커밋과 push는 하지 않았다.

## 측정 자료

- `outputs/perf5_transport/uniform-emissions-cas-benchmark.json`
- `outputs/perf5_transport/uniform-emissions-tile-benchmark.json`
- `outputs/perf5_transport/uniform-emissions-strict.json`
- `outputs/perf5_transport/ordered-emission-default-targeted.log`
- `outputs/perf5_transport/ordered-emission-pair-blocks.json`
- `outputs/perf5_transport/ordered-emission-stage-profile.json`
- `outputs/perf5_transport/ordered-emission-full-regression.json`
- `outputs/perf5_transport/ordered-emission-default-strict.json`
- `outputs/perf5_transport/ordered-emission-memcheck.log`
- `outputs/perf5_transport/ordered-emission-synccheck.log`
- `outputs/perf5_transport/ordered-emission-racecheck.log`
- `outputs/perf5_transport/ordered-emission-default-fixed.json`
- `outputs/perf5_transport/ordered-emission-scaled-fixed.json`
- `outputs/perf5_transport/ordered-emission-cad10m-fixed.json`
- `outputs/perf5_transport/ordered-emission-trapped100m-fixed.json`
- `outputs/perf5_transport/unit-normalization-targeted.log`
- `outputs/perf5_transport/unit-normalization-probe.json`
- `outputs/perf5_transport/warp-emission-targeted.log`
- `outputs/perf5_transport/warp-emission-probe.json`
- `outputs/perf5_transport/warp-metadata-targeted.log`
- `outputs/perf5_transport/warp-metadata-probe.json`
- `outputs/perf5_transport/ordered-emission-coalesced-cad1m.json`
- `outputs/perf5_transport/warp-metadata-count-targeted.log`
- `outputs/perf5_transport/warp-metadata-count-probe.json`
- `outputs/perf5_transport/ordered-emission-device-stages-warm.json`
- `outputs/perf5_transport/shared-inverse-targeted.log`
- `outputs/perf5_transport/shared-inverse-cad1m.json`
- `outputs/perf5_transport/packed-nodes-targeted.log`
- `outputs/perf5_transport/packed-nodes-cad1m.json`
- `outputs/perf5_transport/pair-coherent-targeted.log`
- `outputs/perf5_transport/pair-coherent-cad1m.json`
- `outputs/perf5_transport/pair-spatial-targeted.log`
- `outputs/perf5_transport/pair-spatial-cad1m.json`
- `outputs/perf5_transport/pair-slot-targeted.log`
- `outputs/perf5_transport/pair-slot-cad1m.json`
- `outputs/perf5_transport/pair-stack-targeted.log`
- `outputs/perf5_transport/pair-stack-scalar-recheck.log`
- `outputs/perf5_transport/pair-stack-targeted-fixed.log`
- `outputs/perf5_transport/pair-stack-cad1m.json`
