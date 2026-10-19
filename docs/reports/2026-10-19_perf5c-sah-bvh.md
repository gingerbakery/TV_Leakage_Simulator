# GPU BVH 분할 개선 후보 검증

## 정적 실행 경로 통합

검증한 16구간·cutoff 32 분할을 `gpu_cuda_pair_sah.py`의 정적 함수로 옮기고
기본 보조 tree 생성에 연결했다. 제품 경로에서 AST 변환이나 동적 compile을
사용하지 않는다. 원래 median 분할은 진단용 factory에서 명시적으로 선택할
수 있게 유지했다. 기존 leaf 크기 실험도 원래 median 기준으로 남겼다.

기본값과 실험 후보의 tree 배열 일치, 원래 면·거리 bit, cache 및 leaf 크기
검사 13개가 3.603초에 통과했다. 전체 63개 모듈·573검사도 830.729초에
통과했으며 실패·오류·skip은 0, 실행 중 source hash는 동일했다. 사용자 수동
테스트를 위해 8788 서버를 열었다. 서버 유휴 상태를 감시하며 실행한 새 기본값의
strict 검사도 40/40으로 통과했다. 절대·상대 허용오차 `1e-12`, 최대 32 ULP
기준에서 관찰된 최대값은 18 ULP였다. CUDA 성공/시도 40/40, hybrid와 resident
fallback은 0이다. 메모리·동기화·경합 검사와 고정 기준 성능 비교는 남았다.
아래 가속률은 통합 전 후보 측정값이며 아직 새 기본값의 최종 수치가 아니다.

## 현재 판정

면적 기반 BVH 분할 후보가 고정 기준 대비 CAD 100만 Ray에서 **2.217배**,
1,000만 Ray에서 **2.162배**를 기록했다. 직전 기본값 대비 추가 속도비는
각각 1.214배다. 광량·Ray 수·반사 상한·FP64 정밀도를 낮추지 않았다.

제품 실행 소스에는 정적으로 통합했고 통합 573검사와 새 strict 40조건은
통과했으나 메모리 안전성과 최종 성능 검증이 남았다.
GPU 갇힌 구조 100만 Ray의 직전 고정 기준 1.974배도 남아 있어 CPU와 GPU
각각 최소 2배라는 전체 목표는 미완료다. LT·실측 정합성은 별도다.

## 현재 GPU 탐색의 병목

현재 제품의 pair BVH 경로에 독립 thread별 정수 계수를 추가했다. 과거의
guarded BVH가 아닌 현재 기본 함수의 노드 방문·삼각형 검사·원래 BVH
재검산을 구분한다. 계측은 GPU 실행과 메모리 접근을 추가하므로 계측 시간을
성능 개선 수치로 사용하지 않는다.

CAD 10만 Ray, 상한 10에서 1,099,987회의 교차 질의를 관찰했다. 첫 계측과
반복 2회에서 모든 계수가 같았고 이산 결과와 광학 출력 절대·상대 `1e-9`를
통과했다. 쿼리·성공·재검산과 reject site 합계도 서로 일치했다.

| 처리량 | 현재 중앙 분할 | 면적 기반 분할 후보 |
| --- | ---: | ---: |
| 교차 질의 | 1,099,987 | 1,099,987 |
| 내부 노드 방문 | 51,751,498 | 32,826,760 |
| 스택 pop | 56,136,799 | 37,264,506 |
| 삼각형 검사 | 2,771,613 | 2,736,945 |
| 빠른 경로에서 확정한 질의 | 1,099,984 | 1,099,984 |
| 원래 BVH 재검산 | 3 | 3 |
| 면 판정 보증용 box 검사 | 1,472,920 | 1,472,920 |

현재 경로의 원래 BVH 재검산은 약 0.000273%였다. 과거 guarded 후보에서
관찰한 높은 재검산 비율을 현재 제품의 병목으로 취급하지 않는다. 내부 노드
방문은 질의당 약 47.05개에서 29.84개로 36.57% 줄었다. 재검산 보호를
없애지 않고 탐색할 경계를 줄이는 방향이 실제 속도 개선으로 이어졌다.

## 후보의 변경 범위

기존 보조 tree는 삼각형 중심의 가장 긴 축을 골라 개수를 반씩 나눴다. 새
후보는 축별 16개 구간에 삼각형을 모아 `자식 경계 면적 × 삼각형 개수`의
합이 작은 분할을 선택한다. 32개 미만이거나 깊이 48부터는 기존과 같은
안정적인 중앙 분할을 사용한다. 빈 자식이나 삼각형 생략은 허용하지 않는다.

변경은 보조 tree의 분할뿐이다. 원래 BVH, 삼각형 좌표·면 번호·TRACE OFF
mask·면 속성 연결과 원래 방문 순서 metadata를 유지한다. 실제 교차·근접 면
선택·애매한 경우의 원래 BVH 재검산과 GPU kernel은 그대로다. CPU 계산,
광원 분포, 난수 seed, nit 환산과 종료 정책은 바꾸지 않는다.

8구간·cutoff 32, 16구간·cutoff 32, 16구간·cutoff 64를 비교했다. CAD 100만
Ray의 warm 7회 중앙값은 각각 0.680800, 0.678563, 0.690804초였고 현재
기본값은 0.819473초였다. 이 중 16구간·cutoff 32를 고정 기준으로 재검증했다.

## 고정 기준 비교

전달 경로는 source checkout `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`와
미커밋 소스다. 기존 GPU launcher가 준비한 Python 3.13 x64, Numba 0.66.0,
CUDA Toolkit 13.1 및 NVIDIA GeForce RTX 3070을 사용했다. 고정 기준은 해당
revision의 수송 대조 함수·설정이며 별도 checkout 전체나 LT와의 비교가 아니다.

TV 코너 CAD 50,944개 삼각형, Datum Plane Lambertian 광원, seed 42,
Mixed 반사율 60%, 반사 상한 10의 같은 입력을 순서 교대해 실행했다. 첫
실행은 따로 기록하고 100만은 warm 7회, 1,000만은 warm 3회 중앙값을 쓴다.

| Ray 수 | 고정 기준 초 | 직전 기본값 초 | 분할 개선 후보 초 | 고정 기준 대비 | 직전 기본값 대비 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 100만 | 1.502506 | 0.822651 | 0.677716 | 2.217배 | 1.214배 |
| 1,000만 | 13.365253 | 7.506794 | 6.182568 | 2.162배 | 1.214배 |

100만의 같은 세션 첫 실행은 순서대로 6.060865, 14.260944, 9.325355초이며
1,000만은 17.407034, 21.568743, 14.846608초다. 각 모드의 JIT와 준비 비용을
포함하므로 이 첫 실행 시간을 나눠 가속률로 주장하지 않는다.

모든 측정의 production preflight는 `available`, `strict_float64`,
`kernel_executed`, `kernel_verified`가 true이고 scope는 `production_ray_bvh`,
계약은 `strict_float64_bvh_v1`이다. 모든 run은 `gpu_active`, reason 없음,
CPU hybrid와 resident fallback 0이었다. CUDA 성공/시도는 100만에서 16/16,
1,000만에서 153/153이다. source hash 유지, 이산 exact 및 광학 출력
절대·상대 `1e-9`를 통과했다. 전체 출력의 bit 결정성을 뜻하지는 않는다.

## 준비 시간과 메모리

100만 Ray 측정에서 최초 보조 tree 준비가 1.554726초에서 2.569643초로
약 1.015초 늘었다. 같은 형상은 cache로 재사용하며 반사율이나 Receiver
변경만으로 다시 만들지는 않는다. CAD import·3D viewer 로딩을 가속한 결과가
아니며 첫 GPU 해석의 형상 준비 비용은 별도로 관리해야 한다.

상주 workspace 최고 크기는 50,463,568에서 53,609,296 byte로 3 MiB 늘었다.
분할 tree의 깊이 때문에 스택 폭이 늘어난 비용이며 정확도 저하로 메모리를
줄이지 않았다. Register 148개/thread, local 40 byte/thread와 shared 0은
기존 kernel과 같다.

## 정확도 검증 상태

후보 3종의 최초 관련 24검사는 75.093초에 통과했다. 근접 면, Receiver 거리
제한, 사용자 면 속성·재료 재편집, MIS, 중단과 실패 후 미반영 구간 재실행을
포함한다. Scalar에서는 2,880개 질의의 원래 면 번호와 거리 bit를 검사했다.
추가로 SAH 분할이 실제 적용되는 큰 cavity의 1,000회 반사와 TRACE OFF
면 번호 보존까지 확대해 28검사를 77.322초에 통과했다. 큰 cavity는 멀리
떨어진 96개 면을 더한 뒤 17개 Ray가 각각 1,001회의 실제 면 교차를 수행하며
record window 재실행과 원래 결과의 일치를 확인한다. 이 후보 검사를 통합 541개에 더해
새 통합 완료 수치로 표시하지 않는다.

계측기 관련 3검사도 통과했다. 최초 GPU 검사에서 CUDA dispatcher를
`self.kernel`로 읽어 메서드처럼 결합한 실험 도구 오류가 있었다.
`type(self).kernel`로 수정하고 실제 계측 kernel 실행 및 CUDA 성공 여부를
검사하도록 했다. 제품 수송 오류를 수정한 결과가 아니며 최초 실패 로그도
보존했다.

## 작은 장면의 후속 조정

별도 갇힌 구조 100만 Ray에서 원래 순서의 primary packet을 2개씩 묶는
실험은 0.225359에서 0.219210초로 1.028배였다. CUDA batch는 16개에서 8개,
workspace는 33,686,352에서 67,371,856 byte로 늘었다. 더 큰 262,144 및
524,288 요청도 실제 CUDA batch 수는 8개여서 요청 크기 전체로 실행됐다고
표현하지 않는다. 524,288 요청은 오히려 0.237759초로 느렸다.

후속 8조건의 warm 7회 비교는 다음과 같다. 광학 결과와 이산 결과는 모두
기준을 통과했지만 작은 흡광 장면에서는 실행 시간이 늘었다.

| 조건 | Ray 수 | 기존 초 | 2-packet 묶음 요청 초 | 속도비 |
| --- | ---: | ---: | ---: | ---: |
| Face 직접 도달 | 100만 | 0.255760 | 0.252804 | 1.012배 |
| 확률 2회 반사 | 100만 | 0.219549 | 0.214986 | 1.021배 |
| 갇힌 구조 상한 10 | 100만 | 0.216410 | 0.208698 | 1.037배 |
| CAD | 10만 | 0.100601 | 0.101273 | 0.993배 |
| 첫 면 흡광 상한 20 | 10만 | 0.026287 | 0.029910 | 0.879배 |
| 첫 면 흡광 상한 100 | 10만 | 0.027155 | 0.030321 | 0.896배 |
| 첫 면 흡광 상한 1,000 | 10만 | 0.029993 | 0.032410 | 0.925배 |
| 반사점 Receiver MIS | 100만 | 0.239908 | 0.231653 | 1.036배 |

8조건은 모두 source hash 유지, production preflight 여섯 항목, `gpu_active`,
reason 없음, CPU hybrid 및 resident fallback 0을 확인했다. Face 광원을 제외한
나머지는 Datum Plane 광원이다. 100만 Ray 조건은 CUDA 성공/시도 8/8, CAD
10만은 1/1, 흡광 10만 조건은 2/2였다. 큰 요청을 해도 흡광 조건에서는 실제
batch 수가 줄지 않았다. 이 결과를 전역 가속 성공으로 처리하지 않는다.

흡광을 100만 Ray로 늘려도 상한 20/100/1,000의 속도비는 각각
0.883/0.959/0.958배였고 CUDA 성공/시도는 모두 16/16이었다. 요청은 커졌지만
실제 event 메모리 예산에 묶여 batch 수가 줄지 않는 조건의 비용이다.

후속으로 요청 크기가 아닌 `event_window_batch_size`가 허용한 실제 크기로
packet 목표를 정했다. 이 경우 상한 10은 131,072, 흡광 20/100/1,000은
65,536개로 제한됐다. 8조건 및 흡광 확대 3조건은 광학 결과·이산 결과와
source hash 검사를 통과했지만, 3% 성능 회귀 gate는 통과하지 못했다.
갇힌 구조 100만은 0.217520에서 0.211367초로 1.029배였고 CAD 10만은
0.096664에서 0.099755초로 0.969배였다. 흡광 10만의 속도비는
0.881/0.880/0.913배, 흡광 100만은 0.949/0.982/0.972배였다.

흡광 상한 20의 두 run을 비교하면 실제 batch 크기와 workspace byte 수는
같지만 workspace 재사용 횟수가 2에서 1로 줄었다. 메모리 목표만 제한하면
해결된다고 판단하지 않으며 추가 workspace 준비 비용을 분리해 조사해야 한다.
JSON의 `passed=true`는 광학·입력 보존 판정이지 성능 gate 통과가 아니다.

통합 검사 소스를 고정한 상태에서 CPU mock으로 추가 원인을 확인했다.
현재 scratch workspace는 장치·배열 크기가 같아도 새 해석 context라는 이유로
27개 배열을 다시 할당한다. context만 바꾸고 다른 모든 요구조건을 유지한
재현에서 현재 코드의 새 할당은 27개, 소유자 표시만 갱신하는 별도 mock
후보는 0개였다. 더 큰 배열을 요구하면 두 방식 모두 27개를 새로 할당했다.
이것은 GPU 실행이나 성능 측정이 아니며 제품 변경도 아니다. scratch와 달리
재료·Receiver binding은 새 context에서 다시 올려야 하므로 그대로 재사용할
수 없다. 실제 GPU의 데이터 초기화·재편집·실패 복구 및 성능 검증이 필요하다.

Packet 조정은 아직 채택하지 않았다. 별도로 유의미한 개선을 보인 SAH의
정적 제품 통합·전체 회귀·sanitizer와 최종 고정
기준 비교를 통과해야 한다. 수동 테스트용 서버는 최신 source에서 실행했고
HTTP 200과 RTX 3070 production preflight 여섯 항목을 확인했다. 커밋과 push는 하지 않았다.

## 수동 테스트와 분리한 workspace 후보

사용자가 실행 중인 8788 서버의 제품 소스는 바꾸지 않았다. 제품 소스 hash는
573검사 통과 당시와 같다. 서버 유휴 상태를 감시하며 초기 후보 GPU 검사와
제품 strict 검사를 진행했고, 이후 사용자 CAD·해석 시작을 감지해 성능 측정은
시작하지 않았다. 사용자가 수동 테스트 중임을 확인했으므로 추가 GPU 작업은 하지 않는다.

새 진단 후보 `scripts/perf5_workspace_packing.py`는 27개 scratch 배열을 하나의
256-byte 정렬 버퍼의 독립 view로 만든다. 기존 dtype·shape·C/F stride와 논리
event 예산은 유지한다. 면·재료·Receiver binding과 summary session은 공유하지
않는다. AST 변경은 별도 진단 process 안에서만 사용하며 제품 실행 경로에는 없다.

CPU mock 16검사가 0.297초에 통과했다. 최초 scratch 할당 호출은 현재 27개,
packing 후보 1개였다. 입력 재기록, 논리 깊이 전체 counter 초기화, 크기 증가,
장치 변경, compact/full 분리, 실패 후 기존 cache, 1,001 depth와 부분 batch,
정확한 pointer 범위·stride 및 새 광학 binding의 독립성을 확인했다.
실제 CUDA kernel·GPU 메모리 안전성·속도 향상은 이 검사로 증명하지 않는다.

API의 해석 job은 매번 새 thread를 만든다. 같은 thread에서 context 표시만
갱신하는 재사용은 job 간 재사용을 보장하지 않는다. 이 제한을 CPU 검사에서
재현했으며 packing은 새 thread에서도 최초 할당 1개가 된다. 소규모 mock의
논리 workspace는 5,008 bytes, 정렬을 포함한 packing root는 9,232 bytes였다.
이는 작은 fixture의 padding 비용이며 전체 GPU VRAM이나 실제 TV 용량이 아니다.

초기 후보의 실제 GPU 24검사는 77.849초에 통과했다. 실제 1,000회 반사,
미소 간격, ROI 면 속성 재편집, 이전 run의 잘못된 값 초기화, 실패 복구를 포함한다.
그 뒤 재사용 cache hit에서는 packing metadata도 다시 만들지 않도록 준비 위치를
옮겼고 CPU 17검사가 0.308초에 통과했다. 이 마지막 변경의 GPU 재검증은 남았다.

Benchmark runner는 동일 thread와 API 방식의 새 thread를 따로 측정하고,
first/warm 시간·실행 장치·fallback·물리량 정합·3% 성능 회귀를 구분한다.
사용자의 5,000만 Ray·상한 100 해석 시작을 감지한 guard가 benchmark 실행을
거부했다. 이 후보의 실제 속도 향상은 아직 측정하지 않았고 기본 코드로 채택하지
않았다. 수동 테스트가 끝난 뒤 SAH의 남은 검증과 후보 GPU 재검증을 이어간다.

## 수동 테스트 종료 후 검증 재개

사용자가 수동 테스트 완료와 정확도 우선을 확인했다. 8788 서버는 재시작하거나
설정을 바꾸지 않았다. 최신 packing 후보의 실제 GPU 24검사는 79.158초에
다시 통과했다. 제품 소스는 통합 573검사 및 strict 40조건 당시와 동일하다.

Packing만 적용한 고정 기준 100만 Ray·10회 갇힌 구조에서는 같은 thread의
warm 중앙값이 2.008~2.015배였지만 새 API 방식 thread에서는 1.991~1.992배였다.
따라서 이를 안정적인 전체 2배 달성으로 처리하거나 제품에 채택하지 않았다.

다음 별도 후보는 65,536개씩 생성된 원래 광선 packet 둘을 순서 그대로 모아
131,072개 단위로 처리한다. 발광 분포, 입력 광량, ray별 면 번호와 MIS weight,
반사 RNG, 종료 기준, FP64 정밀도는 바꾸지 않는다. 전체 Ray가 100만 미만이거나
초기 event 예산이 부족하면 packet을 합치지 않고 원래 dispatch 크기를 유지한다.
현재 초기 window 정책에서는 상한 20/100/1,000이 이 제외 조건에 들어간다.

최초 테스트는 dispatch 요청 크기까지 늘리지 않으면 합친 packet이 내부에서
다시 나뉘어 여전히 5개 batch가 실행되는 것을 확인했다. 이 진단 연결을 고친 뒤
부분 tail을 포함한 262,163개가 3개 CUDA batch로 처리됐다. 두 번째 batch 실행
직후 실패를 주입해도 완료된 131,072개는 중복되지 않았고 나머지 suffix만 CPU로
복구됐다. 같은 131,072-primary 경계에서 Stop한 부분 결과도 원래 경로와 일치했다.
CPU packet 검사 4개와 GPU 수송 검사 2개는 24.702초에 통과했다.

고정 기준과 현재 기본값, 별도 결합 후보를 번갈아 측정한 warm 11회는 다음과 같다.

| 실행 수명 | 고정 기준 p50 | 현재 기본값 p50 | 결합 후보 p50 | 고정 기준 대비 | 기본값 대비 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 같은 thread | 0.456189s | 0.225090s | 0.216121s | 2.111배 | 1.041배 |
| API 방식 새 thread | 0.455395s | 0.229744s | 0.217008s | 2.099배 | 1.059배 |

위 수치는 CPU 대 GPU가 아니라 고정 GPU 버전 대 후보 GPU 버전의 비교다.
Source checkout `main@157230c6`와 미커밋 변경, RTX 3070, production preflight
여섯 항목 통과를 기록했다. Datum Plane 광원, `gpu_active`, reason 없음,
CUDA 성공/시도 8/8(기준·현재 16/16), CPU hybrid와 resident fallback 0이었다.
최초 실행과 각 warm 시간, source hash, 모든 실행의 물리량 비교와 Compute
증거는 JSON에 따로 남겼다. 물리량 절대·상대 `1e-9`와 이산 exact를 통과했다.

이 모델만으로 Receiver heatmap 정확도나 모든 workload의 2배 달성을 판정하지
않는다. 이어 Face 광원, 확률 반사, CAD, 높은 상한 흡광, Receiver MIS의 8조건을
같은 thread와 새 API 방식 thread로 각각 비교했다. First 1회와 warm 7회,
총 384개 실행의 source hash·이산 exact·물리량 절대/상대 `1e-9`를 통과했고,
기본값 대비 3% 이상 느려지는 조건은 없었다.

| 조건 | 같은 thread의 고정 기준 대비 | 새 thread의 고정 기준 대비 |
| --- | ---: | ---: |
| Face 직달 100만 Ray | 3.089배 | 2.762배 |
| 확률 2회 반사 100만 Ray | 2.516배 | 2.486배 |
| 갇힌 구조 100만 Ray | 2.025배 | 2.012배 |
| CAD 부하 10만 Ray | 2.664배 | 2.620배 |
| 첫 면 흡광·상한 20 | 2.088배 | 2.305배 |
| 첫 면 흡광·상한 100 | 3.880배 | 3.568배 |
| 첫 면 흡광·상한 1,000 | 17.730배 | 17.797배 |
| 반사점 Receiver MIS 100만 Ray | 1.573배 | 1.562배 |

높은 상한의 흡광 대조군은 실제 1,000번 반사하는 속도 측정이 아니다. CAD
부하와 확률 반사 대조군의 Receiver hit는 0이므로 이 두 조건만으로 Receiver
정확도를 증명하지 않는다. Face/갇힌 구조/MIS의 hit는 각각 717,307/1,000,000/
499,992였고 heatmap을 포함한 결과 비교를 통과했다. MIS 조건의 관찰된 최대
상대 차이는 약 `7.49e-15`다. 수송 입력과 Ray 수를 줄여 얻은 가속이 아니다.

기존 7개 성능 대조군은 두 thread 방식 모두 2배를 넘었으나 Receiver MIS는
아직 미달이다. 이를 제외해 전체 목표를 완료했다고 판정하지 않는다. 새 thread
MIS p50의 0.251796초 중 GPU summary 누적 kernel 계측은 약 0.106038초였다.
이 누적 비용을 정확도 계약을 유지하며 더 줄이는 것이 다음 조사점이다.

Compute Sanitizer memcheck는 제품 SAH, 실제 1,000회 반사, 미소 간격, 광학
재편집, 이전 workspace의 잘못된 값, coalesced 부분 tail·Stop·실패 복구를
포함한 9검사가 67.638초에 통과했고 `ERROR SUMMARY: 0 errors`였다. Child
process 전체 추적과 실제 CUDA 초기화를 요구했다. Synccheck 3검사는 46.045초에,
racecheck 3검사는 47.537초에 통과했고 각각 error 0, hazard/error/warning 0이었다.
확대 부하와 최종 제품 통합은 남았으며 후보를 제품에 반영하지 않았다.

## 남은 MIS 누적 병목 계측

계측 CLI에 별도의 `--mis-rays`와 후보 선택을 추가해 실제 100만 Ray를 확인했다.
기존 MIS 계측은 65,536개를 사용했으므로 100만 표본의 비용으로 오인하지 않는다.
CLI 연결을 확인한 CPU 검사를 포함해 관련 22검사가 0.322초에 통과했다.

CUDA event를 삽입한 별도 계측은 일반 시간 benchmark가 아니다. 반복 3회의
장치 구간 중앙값은 ordered Receiver Flux가 63.208ms, ordered 반사 방출광이
19.334ms, 교차·반사 kernel이 6.890ms였다. 누적 단계가 주된 비용이므로
교차만 추가 가속해 2배 목표를 해결했다고 주장하지 않는다. 계측 반복의
이산 exact와 물리량 `1e-9` 비교도 통과했다.

다음은 광량 덧셈 순서를 유지한 누적 개선이다. 단순 병렬 tree sum이나 FP32,
반사 상한·Ray 수·수광 해상도 축소로 정확도·입력 계약을 바꾸지 않는다.
후보는 source가 고정된 별도 경로에서 엄격 정합·failure/Stop·sanitizer와
대표/확대 workload를 통과한 뒤에만 제품 통합을 검토한다.

## 순서 보존 parity-map 누적 후보

정확도를 낮추지 않는 별도 Receiver 누적 후보를 만들었다. 양의 정상 FP64
구간에서 각 덧셈을 가수의 짝수/홀수 상태 두 개에 대한 정수 이동으로 나타내고,
원래 Ray 순서대로 이 변환을 결합한다. 부동소수점 덧셈을 tree sum으로
재배열하는 방식이 아니다. 반올림 tie도 보존하며 지수 경계·비정상값 등
안전 조건이 충족되지 않으면 기존 순차 warp 합산으로 돌아간다. 이 fallback은
GPU 안의 정확 경로이며 CPU 대체 실행과는 다르다.

CPU 수학 검사 4개와 실제 GPU 기본 검사 2개를 통과했다. GPU helper는
522개 입력 조건의 일반 합/제곱 합, 총 1,044개 출력을 CPU 순차 합산과
bit 단위로 비교했다. 이후 다중 Receiver, 깊이/반사 종류별 합, 서로 다른
기존 누적값, 부분 tile, 빈 packet, device 상태 변경 후 의도적 실패와 CPU
재처리를 포함한 15검사가 50.557초에 통과했다. 기존 합계 검사의 bit-exact
조건과 MIS의 절대·상대 `1e-12`, 32 ULP 기준을 완화하지 않았다.

Receiver만 바꾼 결합 후보를 같은 세션에서 고정 기준·현재 제품·직전 packet
후보와 번갈아 실행했다. 100만 Ray MIS, 같은 thread/새 thread 각각 first
1회와 warm 7회, 총 64실행에서 이산 exact·물리량 `1e-9`·source 고정을
통과했다.

| 실행 수명 | 고정 기준 대비 | 현재 제품 대비 | 후보 p50 |
| --- | ---: | ---: | ---: |
| 같은 thread | 1.998648배 | 1.353670배 | 0.196870초 |
| 새 thread | 1.950654배 | 1.326370배 | 0.206375초 |

반올림한 2.00이라는 숫자로 최소 2배 목표 달성을 주장하지 않는다. 다음은
같은 정확 합산 방식을 반사 방출광에도 적용한 별도 후보 검증이다. 신규
후보의 sanitizer, 대표/확대 부하, 최종 제품 통합은 아직 남아 있다. 제품
소스와 실행 중인 수동 테스트 서버에는 이 후보를 반영하지 않았다.

반사 방출광까지 확장한 후보의 24검사는 60.310초에 통과했다. CPU/GPU
엄격 MIS 반복 비교, 반사 종류/깊이별 독립 순차 oracle, 1,001개 깊이 출력
공간, overflow·subnormal·tie, NaN 검출과 실패 재처리를 포함한다. 추가로
262,163개 Face/MIS 표본을 원래 순서대로 병합했을 때 표시용 path와 부분
tail이 유지됐다. 병합이 실제 선택됐는지 확인하는 assertion을 추가한 뒤의
안전성 검증과 대표 workload 측정은 후속 실행으로 구분한다. 전역 metadata로
확인된 동일 방출광과 반복 깊이 패턴의 기존 단축 경로는 유지했다.

반사 방출광을 포함한 후보도 8조건×같은/새 thread×3버전×first 1회/warm
7회, 총 384실행을 마쳤다. Ray hit 같은 이산 결과는 일치했고 물리량
`1e-9`와 source 고정을 통과했다. 비교의 최대 상대 차이는 약
`1.433e-10`이며, 이는 모든 결과가 bit-exact하다는 뜻은 아니다. 원래
광선 표본과 정밀도를 유지했고 각 실행은 `gpu_active`, CUDA 성공 > 0,
CPU hybrid/resident fallback 0을 요구했다.

| 조건 | 같은 thread: 고정 기준 대비 | 새 thread: 고정 기준 대비 |
| --- | ---: | ---: |
| Face 직달 100만 | 3.152배 | 2.864배 |
| 확률 2회 반사 100만 | 2.385배 | 2.276배 |
| 갇힌 구조 100만 | 2.049배 | 1.968배 |
| CAD 10만 | 2.663배 | 2.605배 |
| 첫 면 흡광·상한 20 | 2.643배 | 1.923배 |
| 첫 면 흡광·상한 100 | 3.992배 | 3.579배 |
| 첫 면 흡광·상한 1,000 | 18.379배 | 15.357배 |
| 반사점 Receiver MIS 100만 | 1.962배 | 2.004배 |

MIS는 현재 제품보다 1.325/1.339배 빨랐다. 그러나 Face 새 thread는 제품
대비 0.928배, 확률 반사 같은 thread는 0.939배여서 3% 성능 회귀 gate를
통과하지 못했다. benchmark의 exit 1은 이 성능 gate 실패이며 수치 정합
실패가 아니다. 유리한 조건만 골라 완료로 판정하지 않고 제품 미반영을
유지한다. 높은 상한 흡광과 Receiver hit 0인 부하의 해석 범위 제한은
앞선 표와 동일하다.

새 후보 memcheck는 실제 병합·표시 path/부분 tail, 독립 다중 Receiver 합,
device mutation 후 실패 재처리, 반사 방출광의 primary-major 순차 합을
포함한 5검사가 43.159초에 통과했고 error 0이었다. Synccheck도 같은 5검사가
41.207초에 통과했고 error 0이었다. Child process 전체와 실제 CUDA 초기화를
요구했다. Racecheck도 같은 5검사가 59.176초에 통과했고 hazard/error/warning
모두 0이었다. AST로 만든 별도 진단 kernel의 source line 정보 경고는 있었으나
sanitizer 검사를 생략하거나 초기화 실패를 통과로 처리하지 않았다. 제품에
넣기 전 대표 부하의 성능 회귀 조정과 확대 부하·최종 정적 통합이 남았다.

실제 100만 Ray의 별도 CUDA event 계측도 완료했다. 계측 반복 3회는 source
고정과 물리량 비교를 통과했으며 Receiver 순서 누적 구간 중앙값은
13.394ms였다. 앞선 packet-only 계측 63.208ms보다 줄었으나, 서로 다른
계측 세션이며 event 구간에 제출 간격이 포함되므로 이를 프로그램 전체의
가속 배수로 표현하지 않는다. 반사 방출광은 18.775ms로 앞선 19.334ms와
비슷해 이 부분의 추가 효과는 아직 입증되지 않았다. 다음은 동일 방출
stream의 중복 합산 제거와 성능 회귀 원인 분리다. FP32나 합산 순서 변경은
대안으로 삼지 않는다.

마지막으로 수학/packet 선택/packing metadata의 CPU 검사 26개가 0.891초에
통과했고 검사 중 source·test·script hash가 고정돼 있었다. 제품 49개 파일을
573검사 snapshot과 다시 비교해 변경 0개를 확인했다. 수동 테스트 서버를
재시작하거나 메모리의 CAD 세션을 지우지 않았다. 전체 목표는 미완료이며,
커밋·push도 하지 않았다.

## 동일 방출 stream 중복 합산 후보

별도 `perf5_emission_aliases.py`는 현재 chunk에서 반사 종류가 하나이고
방출 깊이도 하나일 때만 중복 합산을 생략한다. 이 경우 lobe의
primary-major stream과 해당 depth의 primary 순서는 같다. 단순히 누적
Ray 수가 같다는 이유로 alias하지 않으며, reflection/contribution 양쪽의
기존 합계가 각각 bit 단위로 같아야 한다. metadata가 없거나 깊이/종류가
여러 개이거나 기존 합계가 다르면 원래 kernel을 유지한다.

초기값 판정은 별도 kernel에서 합산 전에 끝낸다. 서로 다른 block에서
원래 초기값과 이미 갱신된 합계를 섞어 읽는 race를 피하고, 해당 depth를
원래 FP64 순서로 계산한 뒤 별도 단계에서 lobe 합계를 복사한다. 광선
샘플·반사 방향·광량 가중치나 tree sum 정책을 바꾸지 않는다.

CPU guard 2개, GPU 계약 10개, 결합 수송 검사 3개를 통과했다. GPU 계약은
독립적인 CPU 순차 bit oracle, tie/0/서로 다른 초기값, metadata 없음과
잘못된 metadata 거부, 실제 MIS alias 선택 및 엄격 CPU/GPU 비교를 포함한다.
GPU 계약 10검사는 47.711초, CPU guard를 포함한 수송 5검사는 31.764초였다.
수송은 262,163개 표본의 부분 tail·표시 path, 131,072개에서 Stop, 두 번째
packet의 device 합계 변경 후 실패와 미반영 suffix만 CPU 재처리를 검사했다.
의도적 실패는 `gpu_mixed`로 보고했고 성공 1/fallback 1을 숨기지 않았다.
성능 비교와 sanitizer는 별도 후속 gate이며 제품에는 아직 반영하지 않는다.

별도 100만 Ray MIS 비교에서는 고정 기준·현재 제품·Receiver 누적만 개선한
후보·방출광 중복 제거 결합 후보를 번갈아 실행했다. 같은/새 thread 각각
first 1회와 warm 7회, 총 64실행의 source 고정·이산 exact·물리량 `1e-9`와
3% 성능 회귀 gate를 통과했다.

| 실행 수명 | 결합 후보 p50 | 고정 기준 대비 | 현재 제품 대비 |
| --- | ---: | ---: | ---: |
| 같은 thread | 0.172603초 | 2.195662배 | 1.450162배 |
| 새 thread | 0.180985초 | 2.103835배 | 1.357308배 |

이 결과는 CPU 대비 GPU 속도비가 아니라 고정 GPU 버전 대비 새 후보의
속도비다. Source checkout `main@157230c6`+미커밋 진단 후보, RTX 3070에서
production preflight 여섯 항목을 통과했다. 실제 Compute state·CUDA
시도/성공·hybrid/fallback·first 및 warm 시간은 JSON에 보존했다. 다른
모델의 성능 회귀 여부, sanitizer 및 확대 부하는 후속 gate로 남긴다.

## 측정 자료

- `outputs/perf5_transport/current-pair-counters-targeted-fixed.log`
- `outputs/perf5_transport/current-pair-counts-100k-final.json`
- `outputs/perf5_transport/sah16-pair-counts-100k.json`
- `outputs/perf5_transport/pair-sah-targeted.log`
- `outputs/perf5_transport/pair-sah-expanded-targeted.log`
- `outputs/perf5_transport/pair-sah-cad1m.json`
- `outputs/perf5_transport/pair-sah-fixed-cad1m.json`
- `outputs/perf5_transport/pair-sah-fixed-cad10m.json`
- `outputs/perf5_transport/ordered-emission-coalesced-trapped1m.json`
- `outputs/perf5_transport/ordered-emission-coalesced-broad.json`
- `outputs/perf5_transport/ordered-emission-coalesced-absorber1m.json`
- `outputs/perf5_transport/ordered-emission-budget-packets-broad.json`
- `outputs/perf5_transport/ordered-emission-budget-packets-absorber1m.json`
- `outputs/perf5_transport/workspace-context-gate-cpu-probe.json`
- `outputs/perf5_transport/sah-product-full-regression.json`
- `outputs/perf5_transport/workspace-packing-cpu-tests.log`
- `outputs/perf5_transport/workspace-packing-cpu-validation.json`
- `outputs/perf5_transport/sah-product-strict.json`
- `outputs/perf5_transport/workspace-packing-gpu-tests.err.log`
- `outputs/perf5_transport/workspace-packing-cpu-tests-lazy.log`
- `outputs/perf5_transport/workspace-packing-gpu-validation.json`
- `outputs/perf5_transport/workspace-packing-gpu-tests-lazy.err.log`
- `outputs/perf5_transport/workspace-packing-trapped-fixed.json`
- `outputs/perf5_transport/adaptive-packets-transport-tests.err.log`
- `outputs/perf5_transport/adaptive-packets-trapped-fixed.json`
- `outputs/perf5_transport/adaptive-packets-broad-fixed.json`
- `outputs/perf5_transport/adaptive-packets-memcheck.log`
- `outputs/perf5_transport/adaptive-packets-memcheck.err.log`
- `outputs/perf5_transport/adaptive-packets-synccheck.log`
- `outputs/perf5_transport/adaptive-packets-racecheck.log`
- `outputs/perf5_transport/adaptive-packets-mis1m-stages.json`
- `outputs/perf5_transport/adaptive-packets-phase-summary.json`
- `outputs/perf5_transport/parity-maps-gpu-tests-no-min.err.log`
- `outputs/perf5_transport/parity-maps-contract-tests.err.log`
- `outputs/perf5_transport/parity-maps-mis1m-fixed.json`
- `outputs/perf5_transport/parity-maps-expanded-contract-tests.err.log`
- `outputs/perf5_transport/parity-maps-broad-fixed.json`
- `outputs/perf5_transport/parity-maps-memcheck.log`
- `outputs/perf5_transport/parity-maps-synccheck.log`
- `outputs/perf5_transport/parity-maps-racecheck.log`
- `outputs/perf5_transport/parity-maps-mis1m-stages.json`
- `outputs/perf5_transport/parity-maps-cpu-regression-final.json`
- `outputs/perf5_transport/parity-maps-phase-summary.json`
- `outputs/perf5_transport/emission-alias-contract-tests.err.log`
- `outputs/perf5_transport/emission-alias-transport-tests.err.log`
- `outputs/perf5_transport/emission-alias-mis1m-fixed.json`
