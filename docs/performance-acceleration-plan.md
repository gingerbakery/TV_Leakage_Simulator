# Ray Tracing 성능 가속 계획

## 2026-10-19 PERF-5D 검증 보완 완료 범위

독립 검토에서 발견한 ULP 부호 경계, 실제 후보 실행, 같은 모드 반복 판정,
baseline 인증과 실패 기록 보존을 보완했다. 고정 소스로 TV·CASE2를 각 8회
재실행하고 160회귀 및 GPU sanitizer 3종을 통과했다. CASE2의 공개 Receiver
지표는 동일하고 heatmap 최대 차이는 1 ULP다. 전체 보조 집계의 32 ULP
반복성은 여전히 미통과이며 LT·실측·peak 통계 수렴은 별도다.

새 측정의 보조 BVH 준비 감소율은 TV 41.1%, CASE2 34.9%다. 캐시 사용 전체
추적 시간은 유지하며 추가 전체 가속으로 표시하지 않는다. 이번 작업은
검증 도구·테스트 보완이며 제품 계산은 바꾸지 않았다. 다음 5D 범위는 CPU
탐색 구조/leaf/layout 후보이며, 전체 5D 또는 PERF-5 완료는 아니다.
최종 근거·독립 검토와 메인 실행의 구분은
[검증 보완 보고서](reports/2026-10-19_perf5d-validation-repair.md)를 따른다.

## 2026-10-19 독립 검증 운영 시작

검증 담당 한 명으로 PERF-5D 근거를 읽기 전용 점검했다. TV 결과의 실행 소스
hash가 현재 버전과 달라 해당 증거 승계를 보류했고, ULP 비교기의 부호 경계와
실제 후보 경로 선택 조건을 보완할 필요를 확인했다. 새 제품 계산 오류를
확정한 것은 아니다. 다음 가속 후보보다 검증 도구 보완과 고정 버전 재검증을
우선한다. 상세는 [독립 검토 보고서](reports/2026-10-19_perf5d-independent-review.md)를 따른다.

## 2026-10-19 PERF-5D 첫 후속 적용

이미 적용한 SAH 탐색 트리의 배열과 순서를 그대로 유지하면서 준비 비용을
줄였다. TV 코너 보조 BVH 생성은 2.848→1.608초, 사용자 CASE2는
0.199→0.131초다. 캐시 사용 전체 Ray Tracing 시간은 유지하며, 전체 해석의
추가 가속률로 표시하지 않는다. 형상 무효화 시 오래된 보조 tree도 해제한다.

관련 133검사와 GPU 메모리·동기화·경합 검사를 통과했다. Receiver 비교는
최대 1 ULP였지만, 모든 보조 집계까지의 32 ULP 검사는 기존 버전 반복에서도
실패하므로 전체 bit 결정성을 확보했다고 주장하지 않는다. CPU 탐색 구조와
wide BVH는 후속 5D 범위다. 기존 최소 2배 목표를 자동 재개하지 않았다.
측정 조건과 미완료 범위는
[`PERF-5D 준비 비용과 캐시 검증`](reports/2026-10-19_perf5d-bvh-preparation.md)에 정리했다.

이하 성능 목표와 후보별 판정은 이전 개발 이력이다.

후속 정확 순서 누적 후보는 CPU 수학·실제 GPU 24검사와 8조건·384실행의
이산 exact/물리량 `1e-9` 비교를 통과했다. MIS는 현재 제품 대비 약
1.325~1.339배, 고정 기준 대비 1.962~2.004배였다. 그러나 Face 새 thread와
확률 반사 같은 thread가 제품 대비 3% 성능 회귀 기준을 넘었고 일부 조건의
최소 2배도 미달했다. 따라서 제품에 통합하지 않으며 전체 목표는 미완료다.
정확 합산의 bit-exact oracle과 실패 복구를 유지한 채 성능 회귀와 메모리
안전성을 추가 검사한다. Memcheck/synccheck/racecheck는 각 5검사에서
오류·경합 0이었다. Receiver 누적 계측은 13.394ms로 줄었지만 방출광은
18.775ms가 남았다. 아래 packet-only 결과와는 구분한다.

추가 별도 후보는 원래 광선 packet을 유지한 적응형 병합과 scratch packing이다.
같은 thread/API 방식 새 thread의 8조건·384실행은 물리량/이산 정합과 3%
성능 회귀 검사를 통과했다. 기존 7개 조건은 두 방식 모두 고정 기준 대비
2.012배 이상이나 Receiver MIS는 1.562배로 전체 목표 미완료다. 실제 GPU
24검사, Stop/실패 복구 및 memcheck 9·synccheck 3·racecheck 3도 통과했다.
제품에는 미반영이다. MIS 100만 계측에서 Receiver 순서 누적 63.208ms와
반사 방출광 누적 19.334ms가 교차·반사 6.890ms보다 컸다. 정확한 누적
순서를 보존하는 후속 개선이 필요하다. 최신 근거는
[`분할·packet 후보 검증`](reports/2026-10-19_perf5c-sah-bvh.md)을 따른다.

현재 pair BVH의 실제 계측에서 재검산은 약 0.000273%였고 경계 탐색이 주된
작업이었다. 면적 기반 분할 후보가 내부 노드 방문을 36.57% 줄여 CAD 100만
및 1,000만 Ray에서 고정 기준 대비 2.217/2.162배를 기록했다. 실제 1,000회
반사 및 TRACE OFF 보존을 포함한 관련 28검사를 통과했다. 이후 정적 코드로
기본 경로에 통합했고 핵심 13검사와 전체 573검사를 통과했다. 사용자 수동
테스트 중에는 자동 GPU 검증을 겹치지 않는다. 유휴 상태에서 새 기본값의
strict 40조건도 통과했으며 최대 18 ULP, CUDA 성공/시도 40/40이었다.
메모리 안전성과 최종 성능 검증은 남았다. 최초 후보의 tree 준비는 약 1초, 해당
workspace는 3 MiB 늘었다. 최신 후보와
검증 범위는 [`분할 개선 후보 검증`](reports/2026-10-19_perf5c-sah-bvh.md)을 따른다.

GPU 반사 방출광의 합산 순서를 고정하고 동일 광량·깊이별 반복 패턴을 빠르게
처리하는 방식을 기본값으로 반영했다. 최종 strict 검사는 40/40이며 통합
541검사와 메모리·동기화·경합 검사도 통과했다. 최종 GPU 고정 기준 비교는
CAD 100만 Ray 1.831배, 1,000만 Ray 1.778배, 갇힌 구조 100만 Ray 1.974배다.
합성 1억 Ray·10회 반사는 20.961초·2.388배지만 가변 광량 CAD의 정확도
집계 비용이 남아 CPU/GPU 각각 최소 2배 목표는 아직 미완료다. 최신 상태는
[`reports/2026-10-19_perf5c-ordered-emission-patterns.md`](reports/2026-10-19_perf5c-ordered-emission-patterns.md)를 따른다.

추가 CUDA event 계측에서 CAD의 교차·반사 구간이 계측된 전체 kernel 구간의
약 83%를 차지했다. metadata 개수 묶음과 reciprocal 재사용 후보는 정확도
검사를 통과했으나 성능 이득이 부족해 채택하지 않았다. 다음은 FP64와 원래
면 선택을 유지한 BVH 메모리 접근 개선이며, 직접 테스트할 기본 코드는
통합 541검사·strict 40/40을 통과한 상태 그대로 유지한다.

후속 BVH 직접 64/80-byte 배치도 18검사를 통과했지만 CAD 100만 Ray의
속도비가 1.000배 수준이라 채택하지 않았다. 다음 후보는 반사 단계별 실행과
GPU 작업 재정렬이며, RNG·원래 슬롯의 event·종료 정책을 보존하는 별도
실험부터 진행한다. 기본 제품 소스는 바뀌지 않았다.

현재 pair BVH를 연결한 반사 단계별 방향·위치 재정렬과 정렬 없는 슬롯
분리도 실제 검증했다. 관련 7/14/7검사는 통과했지만 CAD 100만 Ray의
같은 세션 속도비가 0.808~0.843배라 모두 채택하지 않았다. 이어 fused 순서를
유지한 traversal stack 후보 3개도 23검사를 통과했지만 CAD 100만 Ray에서
0.818~1.003배라 채택하지 않았다. 전체 2배 목표는 여전히 미완료이며 기본
테스트 버전은 유지한다. 다음은 현재 pair 경로의 노드 방문·삼각형 검사와
기존 BVH 재검산 비용을 구분하는 계측이다.

다음 문단의 속도비와 36/40 결과는 직전 atomic 기본값의 측정 이력이다.

GPU 복잡 CAD 탐색의 FP64 반복 연산을 줄였고 통합 526검사를 통과했다.
CPU 확대 7조건은 고정 기준 대비 2.141~5.763배이며 공개 결과가 bit 단위로
동일했다. GPU 제품의 CAD 100만 Ray는 2.043배, 합성 1억 Ray·10회 반사는
2.575배였다. 다만 원래 범위의 GPU 확률 반사는 1.984배, CAD 1,000만 Ray는
1.997배이며 별도 엄격 합산 검사는 36/40이다. 전체 최소 2배와 엄격 반복성의
동시 달성은 아직 미완료다. 최신 적용 상태와 남은 과제는
[`reports/2026-10-19_perf5c-conservative-fp64-slabs.md`](reports/2026-10-19_perf5c-conservative-fp64-slabs.md)를 따른다.

직전 전송 view metadata 개선과 CAD 확대 범위의 병목 진단은
[`reports/2026-10-19_perf5c-device-view-metadata.md`](reports/2026-10-19_perf5c-device-view-metadata.md)에 남겼다.

직전에 통합한 두 교차 후보 특수화는 정적 함수와 불변 형상 cache를 사용하며
복잡 CAD에서만 선택한다. 작은 모델은 기존 일반 GPU를 유지한다. 해당 내용은
[`reports/2026-10-19_perf5c-production-pair-bvh.md`](reports/2026-10-19_perf5c-production-pair-bvh.md)에 남겼다.

이하 단계별 수치는 각 실험 당시의 이력이다. 직전의 고정 배열 묶음 전송과
실험 factory 결과는
[`reports/2026-10-19_perf5c-pair-and-static-transfer.md`](reports/2026-10-19_perf5c-pair-and-static-transfer.md)에 남겼다.

근접 교차 후보군은 기존 면 선택과 leaf 가지치기를 재현하면서 원래 GPU 재탐색을
줄였다. 다만 CAD warm 개선은 약 1.04배이며 다른 조건은 일관되지 않아
아직 제품에 적용하지 않았다. 상세는
[`reports/2026-10-19_perf5c-isolated-hit-cluster.md`](reports/2026-10-19_perf5c-isolated-hit-cluster.md)를 따른다.

GPU 방향·위치 정렬 후보는 CAD에서 약 1.02배에 그치고 다른 조건은 느려져
제품에 적용하지 않았다. 관련 통합 회귀 393개는 통과했다.
비교 결과와 다음 교차 계산 후보는
[`reports/2026-10-19_perf5c-coherent-scheduling.md`](reports/2026-10-19_perf5c-coherent-scheduling.md)를 따른다.

## 2026-10-18 후속 검토

현재 production CPU는 Numba batch 경로를 요청하고 가용하지 않으면 fallback한다.
GPU는 상주형 반사 계산과 GPU summary 누적기를 사용한다. 아래 단계별 설명은
각 구현 당시의 이력도 포함한다.
LT 2026 설치 문서와 현재 소스를 비교한 추가 CPU/GPU 가속 후보, 높은 반사
상한의 event slot 비용 및 PERF-5A~5F 계획은
[`perf5-cpu-gpu-acceleration-review.md`](perf5-cpu-gpu-acceleration-review.md)에 정리한다.
같은 날 PERF-5A의 CPU 기준 측정과 PERF-5B 1차 구현을 진행했다. CPU 기본
batch 조정과 교차 병렬화로 샘플 TV 코너 대조군이 이전 설정 대비 약 2.08배
빨라졌고, 36조건의 공개 결과가 수정 전 기준과 일치했다. 상세 범위는
[`reports/2026-10-18_perf5a-perf5b-cpu.md`](reports/2026-10-18_perf5a-perf5b-cpu.md)를
따른다. GPU 기준 측정과 PERF-5C~5F는 후속이다.

PERF-5B 후속은 CPU 입력 검증과 ordered summary 집계 준비의 반복 비용을 줄인다.
광량 합산 순서, 독립 검산, FP64와 저장 경로 우선순위를 유지하며, 광원 분포나
반사율을 바꾸는 방식은 성능 개선으로 계산하지 않는다. 후속 결과는
[`reports/2026-10-18_perf5b-cpu-preparation.md`](reports/2026-10-18_perf5b-cpu-preparation.md)에
정리한다.

### 성능 목표와 채택 기준

최신 준비 비용 개선은
[`면 속성 연결 재사용`](reports/2026-10-19_perf5b-optical-routing-cache.md)에 기록한다.
면별 override와 재편집을 감시하면서 연결만 재사용해 CAD 준비 단계가 약
20.6ms에서 3.4ms로 줄었다. 같은 소스의 GPU CAD 전체 해석은 추가 1.111배다.
새 고정 기준 비교의 CPU 7조건은 2.143~4.910배, GPU CAD는 1.470배다.
GPU 확률 반사 1.995배, 갇힌 구조 1.967배와 흡광 상한 20의 1.668배도
아직 2배 달성으로 처리하지 않는다. 별도 GPU 경계 계산 후보는 CAD에서
1.087배였으며 기본 경로 통합과 작은 장면 비용 개선을 이어간다.

재검산 질의의 GPU queue 분리와 단계 통합을 구현한 후속 결과는
[`GPU 재검산 분리 실행`](reports/2026-10-19_perf5c-compacted-replay.md)을 따른다.
기존 318개와 새 19개를 합친 통합 337검사는 통과했지만, CAD의 약 1% 차이와
다른 장면의 회귀 때문에 기본 경로에는 적용하지 않았다. 별도 축 정렬 연산
후보도 CAD에서 느려 채택하지 않았다. 계측한 CAD 재검산 214,170질의 중
214,167개는 가까운 거리의 면 후보가 원인이었다. 이 면 판정 보호를 유지한
채 다음 개선을 진행하며 CPU/GPU 각각 2배 목표는 미완료다.

GPU 후속 후보와 다음 구현점은
[`커널 후보와 재검산 병목`](reports/2026-10-19_perf5c-kernel-replay-bottleneck.md)에
기록했다. Leaf 1은 현재 GPU보다 1.454배 빨랐지만 미세 간격에서 면 속성 오류를
재현해 채택하지 않았다. GPU 내부의 기존 BVH 재검산은 결과를 보존했으나 느려졌다.
계측에서 19.47% 질의의 재검산이 99.889% subgroup 실행으로 퍼지는 것을 확인해,
다음 5C 후보는 재검산 질의의 GPU 압축 queue와 단계별 kernel 분리다.
통합 318검사와 memcheck/synccheck는 통과했다. 기본 실행 소스는 바꾸지 않았으며
CPU/GPU 각각 2배 목표는 여전히 미완료다.

최신 후속 검증은
[`정밀 히트맵과 CAD 탐색 후보`](reports/2026-10-19_perf5c-uniform-grid-and-locality.md)를
따른다. 정밀 히트맵의 동일 광량 shortcut은 갇힌 구조에서 기존 순서 스캔보다
1.320배 빨라졌고 통합 304검사와 strict 40조건, memcheck와 synccheck를 통과했다.
다만 기본 atomic 대비 전체 시간 기준은 통과하지 못해 진단 모드를 유지한다.
CPU 자동 정책의 최신 7조건은 고정 기준 대비 2.111~4.882배다. 수동 batch
override는 자동 증가 정책을 끄므로 같은 설정으로 간주하지 않고 별도 기록한다.
삼각형 조기 제외와 광선 실행 순서 재배치는 실제 CAD에서 더 느려 기본 적용하지
않았다. GPU 최신 기준 비교는 CAD 1.309배, 확률 반사 1.761배로 전체 2배 목표는
여전히 미완료다.

2026-10-19 사용자 목표는 CPU와 GPU 각각 현재 버전 대비 최소 2배 가속을
시도하고 PERF-5B/5C를 연속해서 진행하는 것이다. 기준은 5B 입력 검증 개선을
마친 CPU와 5C 착수 전 GPU로 고정한다. 일부 장면에서만 2배를 넘거나 기존
Python fallback보다 빠르다는 이유로 전체 목표를 달성했다고 판정하지 않는다.
현재 중간 상태와 미완료 항목은
[`5B/5C 진행 보고`](reports/2026-10-19_perf5b-perf5c-progress.md)에 기록한다.

5B 후속 통합 수송과 독립 검산 개선의 직전 중간 자동 정책 측정은 CPU 직달 2.02배,
확률 2회 반사 1.95배, 갇힌 구조 1.60배, TV CAD 부하 대조군 1.63배다.
첫 JIT 실행은 더 느릴 수 있다. 당시 높은 반사 상한의 공간 회귀를 피하도록 자동
통합 수송은 상한 32 이하에만 적용했다. 반사 허용 횟수 자체는 변경하지 않는다.
CPU와 GPU 전체 2배 목표나 5B/5C 완료를 의미하지 않는다.

후속 CPU 호출/기록과 실행별 광학 palette 개선 후 CPU 직달/확률 2회 반사는
3.431/2.829배, 복잡 다회 반사/TV 부하는 2.121/2.201배였다. GPU 재현 세션의
직달/복잡 다회 반사는 2.658/2.114배, 확률 2회/TV 부하는 1.885/1.357배다.
확률 2회는 다른 동일 소스 세션에서 2.461배여서 안정적인 2배 통과로 보지 않는다.
자동 정책 통합 240개, palette 강제 재검사 39개 및 MIS 반복 8회는 통과했다.
FP64 재분할/깊이별 집계 후보는 엄격 정합 실패로 폐기했고, BVH leaf 변경은
극미소 간격의 면 ID 차이가 확인돼 적용하지 않았다. CPU 높은 상한과 GPU의
bounded window/직접 summary는 계속 개선해야 한다.
상세 결과는 [`CPU 기록과 GPU 준비 병행`](reports/2026-10-19_perf5c-primary-and-partitioned-summary.md)에 정리했다.

후속으로 상한 32 초과의 CPU 기록을 고정 예산 actual-event pool로 바꾸고,
실제 방문 깊이에 따라 batch를 조정했다. 예산 초과는 결과 반영 전에 분할 재실행한다.
CPU 7개 조건은 bit-exact이며 warm 2.020~4.874배였다. 상한 1,000의 4.874배는
첫 면에서 흡수되는 대조군 결과다. 실제 1,000회 반사는 소규모 정확도 검사로
따로 확인했다. CPU scratch 예약량과 cold JIT 증가 비용도 함께 기록한다.
GPU 재측정은 확률 2회/TV 부하가 1.836/1.339배로 여전히 목표 미달이다.
통합 245개와 강제 compact 16개는 통과했으나, 엄격 MIS 반복은 후보 7/8로
제곱 광량 34 ULP가 상한 32를 넘었다. 따라서 GPU 엄격 반복성과 bounded
window/직접 summary를 계속 개선해야 하며 전체 목표 완료로 판정하지 않는다.
최신 결과는 [`CPU 높은 반사 상한 기록 개선`](reports/2026-10-19_perf5c-bounded-cpu-events.md)을 따른다.

그 뒤 GPU Receiver 제곱 광량 총합을 GPU 내부에서 primary 순서대로 더하도록
바꾸고, CPU compact 자동 적용 범위를 상한 16 이상으로 조정했다. 최신 CPU
7조건은 2.122~4.846배, GPU 주요 합성 3조건은 2.017~2.448배이며 CAD는 1.345배다.
통합 254개와 기존 실패 seed의 엄격 24회는 통과했다. 확장 seed 16회 중 3회는
다른 Receiver Flux atomic 항목에서 33/36 ULP로 실패해 추가 수정이 필요하다.
GPU 전체 엄격 반복성이나 5C bounded window/직접 summary까지 완료한 것은 아니다.
최신 수치·비용·미완료 항목은
[`GPU 제곱 광량 합산 순서 안정화`](reports/2026-10-19_perf5c-ordered-receiver-moments.md)에 기록했다.

후속으로 Receiver total/direct/reflected/lobe/depth Flux와 제곱 광량 총합을
GPU 안에서 함께 순서 집계했다. 확장 40조건의 Receiver 기여도는 CPU와 bit-exact이며
전체 strict 검사는 반사 emitted Flux 37/34 ULP로 38/40이다. 통합 261개는 통과했다.
동일 세션 재측정 CPU 7조건은 2.106~4.937배다. GPU 직달/갇힌 구조는
2.290/2.006배지만 확률 반사/CAD는 1.911/1.353배이며 전체 목표는 미완료다.
실행 블록 크기 변경도 목표를 충족하지 못해 채택하지 않았다. 최신 결과와 남은
bounded window/직접 summary 과제는
[`Receiver 광량 합산 안정화`](reports/2026-10-19_perf5c-ordered-receiver-flux.md)를 따른다.

GPU 후속은 큰 primary batch에 32-depth 기록 window를 적용하고, 기록 초과 시
summary 반영 전에 같은 primary를 나눠 재실행한다. 이미 full-depth 예산에
들어가는 작은 batch는 재시도하지 않는다. 실제 1,000회 반사·Stop·late emitter·
suffix CPU 복구를 검증했고 통합 269개와 memcheck 2조건(0 errors)을 통과했다.
같은 최신 코드의 window off 대비 상한 1,000 흡광/TV CAD는 6.696/3.548배다.
고정 GPU 기준 대비 일반 CAD 상한 10은 1.344배로 여전히 목표 미달이며, 확장 strict는
반사 방출광 38 ULP로 39/40이다. 현재 CPU 7조건은 2.154~4.701배다.
최신 결과·한계·다음 병목은
[`GPU 반사 기록 공간과 재실행`](reports/2026-10-19_perf5c-bounded-gpu-depth-window.md)을 따른다.

후속으로 GPU 수송의 수광 여부를 기존 작은 전송에 함께 싣고, 수광 없는 batch의
Receiver 전용 집계 호출을 생략했다. 별도 host probe 후보는 수광 조건에서 약 6%
느려져 폐기했다. 최종 같은 소스 off/auto warm 11회는 7조건 회귀 3% 이내이며
확률 반사 부하가 1.108배다. 통합 277개와 memcheck 3조건(0 errors)을 통과했다.
고정 GPU 대비 직달/확률/갇힌 구조/TV CAD는 2.588/2.017/1.924/1.351배다.
CPU 재측정 7조건은 2.114~4.915배지만 전체 strict는 반사 방출광 36 ULP로 38/40이다.
Receiver 기여도는 40/40 CPU bit-exact이며 GPU 전체 2배/정밀 검증 완료는 아니다.
최신 결과는 [`수광 없는 GPU 집계 생략`](reports/2026-10-19_perf5c-zero-hit-receiver-stage.md)을 따른다.

후속으로 Receiver 집계의 원래 FP64 덧셈 결과를 보존하는 정수 ULP fold를 추가했다.
직달광의 작은 kernel을 분리한 뒤 같은 소스 warm 11회에서 반사 MIS는 1.119~1.223배,
6개 대조군은 회귀 3% 안이었다. 최신 CPU 재측정은 7조건 bit-exact/2.143~5.068배다.
고정 GPU 대비 갇힌 구조/TV CAD는 1.907/1.352배로 전체 목표는 아직 미완료다.
통합 289종류와 memcheck 5조건, 수정 후 racecheck/synccheck를 통과했다.
기본 방출광 atomic의 엄격 검사는 36/40이며 Receiver 기여도는 40/40 bit-exact다.
방출광 순서 집계 진단 후보는 40/40이지만 반사 부하 비용이 늘어 기본 적용하지 않았다.
정확한 비교 기준, cold 비용과 미완료 항목은
[`Receiver 순서 집계 가속과 방출광 검산`](reports/2026-10-19_perf5c-exact-warp-fold.md)을 따른다.

후속 검사는 셀별 atomic 합산 때문에 Peak 유효 표본 수가 33 ULP로 흔들리는
경우를 추가로 확인했다. 셀 Flux/제곱 광량의 GPU 순서 집계 후보는 확장 40/40,
최대 2 ULP를 통과했지만 warm 11회에서 5/6조건이 시간 회귀 3%를 넘어 기본값은
atomic으로 유지했다. 활성 warp 대표 lane의 flag 갱신과 관련 통합 299개,
memcheck 4조건/synccheck 2조건은 통과했다. 최신 CPU는 2.154~4.824배,
GPU CAD/흡광 상한 20은 1.347/1.798배로 전체 목표는 미완료다.
요청한 GPU batch가 source packet 65,536개에 막히는 점도 확인했다. 기존 광선을
병합한 탐색에서는 실제 호출이 줄었지만 다회 반사 회귀가 있어 일괄 적용하지 않았다.
최신 수치와 보류한 후보는
[`히트맵 반복성과 GPU 가속 후보 비교`](reports/2026-10-19_perf5c-grid-order-and-kernel-probes.md)에 기록한다.

| 구분 | 기준 | 현재 의미 |
|---|---|---|
| 개별 최적화 채택 | 대표 장면 warm 중앙값 20% 이상 시간 단축, 작은 대조군 회귀 3% 이내 | 기존 계획의 채택 기준이며 모든 장면의 향상 보장은 아님 |
| CPU/GPU 진행 목표 | 각각 현재 production 대비 같은 물리 입력과 Ray 수에서 총 계산 시간 50% 이상 단축, 즉 2배 이상 처리 속도 | 사용자가 요청한 진행 목표이며 두 장치의 기준을 따로 유지 |
| 대규모 사용 목표 | 1억 Ray, 반사 10회 이상에서 목표 통계 품질까지의 시간을 단축 | 대표 모델·장치·허용 오차 정의와 실제 장시간 실행 후 절대 시간 목표 확정 |

GPU 속도비의 분모는 기존 GPU 버전으로 고정한다. CPU 대비 GPU 비율, 이전
CPU 대비 새 CPU 비율과 섞지 않는다. 같은 장치와 장면에서 첫 실행과 warm을
분리하고 CUDA 실행 증거, 최종 집계/전송까지의 시간과 Flux·Peak 영역의 통계
품질을 함께 기록한다. 광량, sampling 분포, 정밀도 또는 수렴 판정을 바꿔 얻은
차이는 같은 계산의 가속으로 보고하지 않는다. GPU 중기 목표는 이번 CPU
검증 결과나 LT의 발표 수치로 달성되었다고 판단하지 않는다.

## 목적
- 계산 정확도와 데이터 계약을 유지하면서 반복 설계에 필요한 실행 시간을 줄인다.
- GPU가 없는 PC에서도 프로그램 전체 기능을 사용할 수 있도록 CPU 경로를 항상 유지한다.
- 특정 가속 라이브러리에 종속되지 않도록 계산 백엔드를 단계적으로 교체한다.

## 백엔드 계층

### 1. CPU 기준 경로
- 이름: `reference_cpu`
- 역할: 결과 정합성 검증, 개발 디버깅, 가속 라이브러리가 없는 PC의 안전한 대체 경로
- 특징: 순수 Python 기반으로 가장 이식성이 높지만 대형 CAD와 많은 ray에서 느리다.

### 2. 최적화 CPU 경로
- 이름: `python_numpy_cpu`
- 현재 기본 경로
- 적용 내용:
  - 가상 평면 광원의 NumPy batch sampling
  - 저장 대상 ray path에만 `RayHit` 객체 생성
  - receiver 좌표계와 판정 상수 사전 계산
  - face별 optical property 사전 캐시
  - 반사·산란 벡터 계산의 Python 호출과 중복 정규화 감소
  - batch surface point/normal materialization
  - Receiver 우선 stored-path quota의 O(1) 판정

### 3. CAD 교차 가속 경로
- 예정 이름: `accelerated_cpu`
- 현재 prototype: runtime-only `numba_cpu` opt-in
- 적용 후보:
  - 자체 BVH
  - Intel Embree
  - Open3D `RaycastingScene`
- 목적: 실제 STEP/X_T에서 생성된 수십만~수백만 triangle에 대한 ray-scene intersection 병목 제거
- 원칙: 교차점의 `face_index`, 거리, 위치, normal이 현재 데이터 계약과 동일해야 한다.

### 4. GPU 경로
- 현재 이름: `gpu_cuda`
- 현재 구현: Numba CUDA strict-float64 custom BVH kernel
- 후속 후보: NVIDIA OptiX 또는 더 넓은 device-resident pipeline
- 목적: 대량 ray와 다중 반사 계산의 처리량 확대
- 조건: 지원 GPU, 드라이버, CUDA runtime 또는 배포 가능한 GPU 실행 환경 필요

## GPU가 없는 PC의 동작
- 프로그램을 사용할 수 있다.
- CAD import, 3D viewer, ROI, Transform, Material, Emitter, Receiver 기능은 GPU ray tracing 지원 여부와 무관하게 동작한다.
- ray tracing은 자동으로 CPU 백엔드를 선택한다.
- 차이는 주로 ray tracing 실행 시간이다.
- 동일한 설정에서 CPU와 GPU 결과는 허용 오차 범위 내에서 동일한 통계 경향과 에너지 합계를 유지해야 한다.
- GPU 전용 기능 때문에 프로젝트 파일을 열 수 없거나 결과를 확인할 수 없는 구조는 허용하지 않는다.

## 자동 선택 정책
1. 사용자가 특정 백엔드를 강제로 지정한 경우 해당 백엔드의 사용 가능 여부를 확인한다.
2. GPU 실행 환경이 정상이라면 `gpu_cuda`를 선택한다.
3. CPU 교차 가속 라이브러리가 있으면 `accelerated_cpu`를 선택한다.
4. 그 외에는 `python_numpy_cpu`를 선택한다.
5. 실행 실패 시 한 단계 낮은 백엔드로 안전하게 대체하고 결과에 실제 사용 백엔드를 기록한다.

위 항목은 최종 목표 정책이다. PERF-3C 시점에도 project 기본
`compute_backend="cpu"`는 기존 Python CPU를 유지하며 optional provider를
probe하지 않는다. 사용자가 project를 `gpu_cuda`로 선택한 경우에만 GPU stack의
`auto`가 CUDA/Numba와 hybrid policy를 선택한다. 모든 PC의 global 자동 GPU
승격은 cold start, VRAM/Stop, clean-PC package와 다양한 driver/toolkit gate를
통과한 뒤 별도로 판단한다.

## 단계

### PERF-1: Python hot path 최적화
- 상태: 완료
- 범위:
  - 객체 생성 최소화
  - NumPy 광원 batch sampling
  - optical property 캐시
  - 반사·receiver 수치 계산 단순화
  - 반복 가능한 100만 ray benchmark

### PERF-2: CAD intersection 가속
- 상태: 1차 완료
- 우선순위:
  1. brute-force reference와 flat BVH 결과 정합성 테스트 완료
  2. 사전 계산 triangle + flat BVH CPU backend 연결 완료
  3. TV 샘플과 9,486 triangle STEP 성능 비교 완료
  4. 실제 회사 TV ROI 도면의 end-to-end 측정 필요
  5. 필요 시 Embree/Open3D adapter를 후속 비교

### PERF-3: batch 병렬화와 GPU

#### PERF-3A: 단일 반사 fast path
- 상태: 완료
- 가상 평면 emitter의 NumPy sampling과 depth 0~1 전용 경로를 적용했다.

#### PERF-3B-0: 기준 측정과 batch 계약
- 상태: 완료 (2026-08-18)
- 최신 main `86eaa4b`에서 scalar BVH micro/end-to-end 기준을 측정했다.
- `RayBatch`, `RayHitBatch`, `TriangleMesh.intersect_rays()` 계약을 추가했다.
- 최초 구현은 기존 scalar 교차를 row별 호출하는 CPU reference adapter다.
- `RayTraceConfig`와 실제 ray tracing 실행 경로는 아직 변경하지 않았다.
- 이 단계 자체는 속도 향상이 아니라 이후 backend의 정합성 기준이다.

#### PERF-3B-1: wavefront batch 연결
- 상태: 완료 (2026-08-18)
- NumPy primary ray가 이미 준비되는 virtual-plane fast path를 연결했다.
- receiver 거리를 ray별 `max_t`로 전달하고 primary/secondary ray를 각각 batch query한다.
- 결과 누적과 stored path는 원 primary row 순서로 commit해 scalar 결과를 exact 보존한다.
- 기존 65,536 sampling batch와 당시 기본 4,096 intersection chunk를 분리했다.
- Stop/progress는 시작한 intersection chunk를 원자적으로 완료한 뒤 경계에서 처리한다.
- face/polygon emitter와 `max_depth >= 2`는 기존 scalar 경로를 유지한다.
- runtime dispatch/chunk 인자는 프로젝트 파일에 저장하지 않는다.
- PERF-3B-1 완료 시점의 교차 구현은 Python row-loop CPU reference이며
  `native_batch=false`였다.
- 따라서 기본 `auto`는 scalar를 유지하고 reference batch는
  테스트/benchmark에서만 명시적으로 요청했다.

#### PERF-3B-2: native CPU prototype
- 상태: prototype 완료 (2026-08-18), 기본 자동 선택은 보류
- strict-float64 Numba BVH provider를 runtime-only opt-in으로 연결했다.
- provider import/JIT, immutable scene pack, capability와 whole-query fallback을
  기존 Python CPU 경로와 분리했다.
- 실제 50,944-triangle CAD intersection micro는 독립 실행에서 약
  `48.98~50.45x`였지만, 100,000-ray synthetic end-to-end는
  `0.961~1.009x`의 baseline 수준이어서 자동 선택 gate `1.20x`를 통과하지
  못했다.
- 기본 `auto`는 Numba를 probe/import하지 않고 기존 scalar 경로를 유지한다.
- 기존 PERF-3B-1과 같은 scalar workload를 교대 13회 측정한 결과 runtime
  중앙값 차이는 `+0.42%`로 3% 회귀 gate 안의 측정 잡음 수준이었다.
- 측정된 Numba/llvmlite module directory 약 149.8 MiB와 cold JIT 비용 때문에
  lightweight package에는 아직 포함하지 않는다.

#### PERF-3B-2A: multi-bounce wavefront와 후처리 batch
- 상태: 구현 및 canonical 반복 benchmark 완료 (2026-08-19), 명시적 opt-in
- 명시적 `batch`와 fast virtual-plane emitter의 `max_depth >= 2` 실행을
  depth별 compact wavefront로 전환했다.
- Receiver 후보는 NumPy batch로 계산하고 active origin/direction/energy/이전
  face만 다음 depth로 compact한다.
- 결과는 원 primary 순서로 commit해 contribution 누적과 stored-path quota
  정책을 보존한다. 저장될 수 없는 시각화 path의 `RayHit` materialization도
  생략한다.
- random draw가 없는 specular 경로는 legacy scalar와 exact하다. Stochastic
  scatter/Russian roulette는 `per_primary_seeded_v1`로 chunk/provider/repeat
  exact를 보장하며 legacy scalar와는 statistical parity로 비교한다.
- 기본 `auto`, face/polygon emitter는 legacy scalar/Python CPU를 유지하고
  Numba를 probe하지 않는다.
- 실제 45,167-triangle, depth 10, stored-path 500 workload에서 권장 1,024
  chunk는 중앙값 `7.0649초`, p95 `7.3970초`로 Python scalar 대비 `3.71x`,
  native scalar 대비 `1.59~1.62x`다. 4,096보다 처리량이 `3.48%` 높아 명시적
  batch runtime 기본 chunk를 1,024로 조정했다. PERF-3B-2B stable-source 실제
  ROI 교대 재측정에서도 1,024/4,096 p50 차이는 약 `0.51%`로 동률 범위였고
  1,024가 근소하게 빨라 현재 기본을 1,024로 유지한다.
- 세 seed stochastic 비교의 hit/flux 평균 차이는 약 `-0.9%`였고 95% CI가
  0을 포함했지만 표본이 작다. Bias 부재 확정과 자동 선택 승격은 더 많은 seed
  또는 1M 통계 gate 뒤로 보류한다.
- 교차 외 plan/ordered commit이 다음 병목이며, PERF-3B-2A만으로 백만 ray
  목표를 달성했다고 판단하지 않는다.

#### PERF-3B-2B: surface/path compaction과 compiled reflection planner

- 상태: 구현 및 canonical benchmark 완료 (2026-08-19), 기본 자동 선택은 보류
- `RayHitBatch`가 point/normal을 row-aligned NumPy batch로 materialize해 surface
  hit마다 scalar `HitRecord`와 tuple을 만들지 않게 했다.
- Stored-path quota는 오래된 dead-end index queue로 기존 Receiver 우선 교체
  순서를 O(1)에 보존한다.
- Runtime-only `wavefront_planner`에 `auto`, `python_cpu`, `numba_cpu`를 추가했다.
  기본 `auto`는 Python planner이며 Numba를 import/probe하지 않는다.
- Native planner는 strict-float64 `deterministic_reflection_v1` 계약으로
  `threshold`의 `none`/`specular` row만 처리한다. Stochastic/Russian-roulette
  row는 기존 `per_primary_seeded_v1` Python sidecar를 사용한다.
- `input_prepare`, `initialize`, `execute`, `result_validation` hard failure는 같은
  depth의 deterministic candidate 전체를 Python으로 replay하고 circuit breaker를
  연다. Fallback row count는 stochastic sidecar가 아니라 실패한 native candidate
  수다.
- 실제 mixed ROI final canonical `5.2553초`는 planner `auto`라 native attempt가
  `0`이다. 복원
  profile도 전부 mixed라 explicit native에서도 Python sidecar 대상이다.
  `5.2553초` 개선은 compiled planner에 귀속하지 않으며, 개선 원인은 surface
  geometry batch와 O(1) path quota다.
- Deterministic 10,000-ray synthetic 네 scenario에서는 Python planner 대비
  Numba planner가 `1.078~1.241x`, semantic mismatch `0`을 기록했다.
- Surface/path/planner fallback을 포함한 전체 Python suite `160`개가 통과했다.
- 실제 ROI의 1,000,000-ray 선형 환산은 약 `52.6초`라 최종 목표 달성을
  주장하지 않는다. SoA state와 event tape는 PERF-3B-2C에서 이어서 구현하며
  compiled ordered reducer는 이어진 PERF-3B-2C-2에서 완료했다.

#### PERF-3B-2C/2C-1: SoA state와 ordered event tape v2

- 상태: experimental 구현 및 exact regression 완료 (2026-08-19), 자동 선택 보류
- Runtime-only `wavefront_pipeline`에 `auto`, `object_reference`,
  `soa_event_tape`를 추가했다.
- `stable_active_soa_v1`은 active ray의 primary slot/index, origin/direction,
  power, source face, ray kind와 reflection seed를 owned 배열로 유지하고 stable
  row 순서로 compact한다.
- `ordered_primary_event_tape_v3`는 depth-major 계산 결과를 실제 surface event
  비례 primary-major CSR로 seal한다. Core 정량 column과 optional path geometry를
  분리해 paths-off와 quota 0은 `omitted_v1`, path 저장이 필요하면
  `full_path_v1`을 사용한다.
- Public `seal()`은 vectorized `strict_v1` validation을 유지한다. Private
  `_seal_trusted()`의 `trusted_structural_v1`은 future compiled producer/benchmark
  전용이며 일반 runtime은 선택하지 않는다. 정상 strict/trusted 결과는 byte
  exact다.
- Sealed 배열은 owned/C-contiguous/read-only다. Validation/copy/payload/peak scope를
  별도 metric으로 기록하며 byte 계측을 process RSS나 GPU memory로 해석하지 않는다.
- `python_ordered_v1` reducer는 primary 순서로 grid, flux, summary/detailed
  contribution, reflection과 stored-path quota를 replay한다. 저장 가능한 path만
  materialize한다.
- Deterministic depth 2/10과 stochastic mixed/Gaussian/Russian-roulette에서
  object-reference 대비 float bit와 dict key 순서, chunk/provider/repeat 정합성을
  exact 검증했다.
- 기본 `auto`는 `object_reference`다. v2 actual ROI p50은 object-reference
  `5.232795초`, SoA `5.121246초`로 `1.021782x`, wall `2.132%` 개선됐지만 자동
  승격 gate `>= 1.05x`에는 못 미쳤다.
- 두 경로의 semantic/grid/contribution/path hash는 여섯 measured run에서
  exact했다. 100k paths-on tape peak는 primary chunk당 최대 `680,048 bytes`였다.
  별도 10k A/B의 paths-off/on peak는 `271,080 / 643,800 bytes`, copy 회계는
  `1,131,996 / 2,952,580 bytes`였다. 이는 process RSS가 아니다.
- Strict/trusted/payload/fallback/no-probe 회귀를 포함한 전체 Python suite는
  `184 passed, 154 subtests passed`다. Unit test에는 wall-time threshold를 두지
  않는다.
- Tape를 직접 소비하는 compiled ordered reducer는 이어진 2C-2에서 완료했다.
  그 뒤 `counter_rng_v2`와 같은 SoA/tape 기반 CUDA backend를 진행한다.

#### PERF-3B-2C-2: compiled ordered summary reducer

- 상태: optional CPU native 구현, exact regression과 actual canonical 완료
  (2026-08-19), 기본 자동 선택은 Python 유지
- Runtime-only `wavefront_reducer`는 `auto`, `python_cpu`, `numba_cpu`다.
  `auto`는 Python/no-probe이고 explicit native는 SoA summary에서만 지원한다.
  Detailed contribution은 정상 Python 선택이며 native attempt/fallback이 없다.
- `ordered_summary_reducer_v1`은 primary-major tape를 serial strict `float64`,
  `fastmath=False`로 처리해 Python의 primary/event 덧셈 순서를 보존한다.
- Native output은 owned/read-only/no-alias이며 provider/consumer validation과 digest,
  staged dict/grid/path 복원을 모두 통과한 뒤 한 번만 publish한다.
- Unavailable/initialize/execute/result-validation/apply 실패는 같은 tape 전체를
  Python으로 한 번 replay하고 run-local circuit breaker를 연다. Logical count는
  native attempt와 replay를 중복 집계하지 않는다.
- Deterministic/stochastic/depth/chunk/multi-emitter, terminal-only, quota 교체,
  Stop, fallback과 concurrent run에서 object/SoA Python/native ordered JSON,
  모든 float bit와 dict insertion order가 exact다.
- Actual warm p50은 Python `5.094436초`, native `4.643004초`로 `1.097228x`,
  wall `8.861%` 개선됐다. Replay는 `2.3968x`, commit은 `1.7126x`다.
- Cold JIT `2.382357초`, optional Numba/llvmlite 배포와 단발 실행 손익 때문에
  기본 `auto`는 Python/no-probe를 유지한다. Native는 명시적 opt-in이다.
- 다음은 prepare/result-validation/apply overhead 축소, `counter_rng_v2`, CUDA
  backend 순서다.

#### PERF-3B-3: CUDA GPU backend
- 상태: strict-float64 CUDA intersection과 GPU project stack 1차 구현 완료
  (2026-08-20)
- `compute_backend="gpu_cuda"`는 batch 65,536, CUDA BVH, SoA,
  `counter_rng_v2`, Numba planner/reducer를 선택한다. `<8,192` active row는
  Numba CPU hybrid로 처리한다.
- Prepared host/device scene과 thread-local workspace를 재사용하고 capability,
  upload/kernel/download, device와 hybrid/GPU별 logical count를 기록한다.
- Face/count/grid/summary는 exact, distance/path는 abs/rel `1e-12`다.
- GPU Face emitter는 vectorized primary batch와 row별 source-face ID를 만들고
  CUDA BVH `ignore_faces`에 연결한다. 최초 Face wave는 작은 batch도 CUDA를
  직접 호출하며 이후 작은 reflection wave는 기존 CPU hybrid 정책을 따른다.
- GPU 부재는 정상 CPU 선택이다. Initialize/execute/result-validation hard failure는
  logical batch 전체 CPU replay 한 번과 run-local circuit breaker로 처리한다.
- CPU project가 기본이며 CUDA/Numba import/probe가 없다. GPU acceleration
  dependency와 NVIDIA driver/toolkit은 optional 배포 범위다.
- 실제 ROI 1M source-freeze isolated warm 3-run p50/p95는
  `7.277951 / 8.004967초`, `137,401 primary ray/s`였다.
  이는 LightTools 비교가 아니며 VRAM/Stop/clean-PC 배포 gate는 후속 검증한다.

#### PERF-3D: host overhead 제거와 run-retained ordered accumulator

- GPU project의 `wavefront_reducer_commit="auto"`는
  `run_accumulator`를 선택해 tape별 Python accumulator build/hydrate를 마지막
  1회로 줄인다. CPU project의 `auto`는 기존 `per_tape`를 유지한다.
- Reflection seed는 `numpy_splitmix64_batch_v1`, Receiver 후보는
  `numpy_numeric_batch_v2` numeric batch를 사용한다.
- Stored-path quota가 receiver-only로 포화된 뒤에는 path payload를 단조롭게
  생략한다. Dead-end replacement 가능성이 남아 있으면 full payload를 유지한다.
- Retained result는 run-local이고 성공한 native result만 저장한다. 실패 시 이전
  성공 state를 flush한 뒤 failing tape 전체를 Python으로 한 번 replay하며 Stop과
  concurrent run의 원자성은 기존 계약을 유지한다.
- 이번 단계의 retained/resident는 CPU numeric reducer accumulator만 뜻한다.
  전체 ray state GPU residency나 fused CUDA depth kernel은 후속이다.

#### PERF-4: 1억 Ray·10회 반사 목표

- 2026-08-25 RTX 3070의 1M primary·depth 10 all-survive warm 측정은
  `13.14~14.48초`, 약 `69k~76k primary ray/s`였다.
- 현재 구조의 1억 Ray 선형 환산은 약 `21.9~24.1분`이다. 10분 이하는
  device-resident wavefront, GPU Receiver/Heatmap accumulator와 fused
  traversal/shading이 필요하다.
- 5% Error 달성에 필요한 Ray 자체를 줄이기 위해 Receiver-directed importance
  sampling/Next Event Estimation과 Auto convergence sample 재사용을 병행한다.
- 상세 타당성 및 단계별 Gate는
  `docs/reports/2026-08-25_gpu-100m-ray-feasibility.md`를 따른다.

##### PERF-4A: 고정 성능 계약

- 상태: 완료 (2026-08-25)
- Face direct, stochastic two-bounce, trapped corridor depth 10을 scene hash가 있는
  고정 workload로 등록했다.
- 기준선은 `host_roundtrip`으로 고정하고 cold/warm, logical intersection,
  Receiver hit, CUDA 증거, 1억 Ray 선형 환산을 기록한다.
- 상세 계약은 `docs/perf4a-benchmark-contract.md`를 따른다.

##### PERF-4B: GPU 상주형 Wavefront

- 상태: 1차 완료 (2026-08-25)
- primary Ray 상태, Receiver 판정, CUDA BVH, optical lookup, 반사 방향, 감쇄와
  종료 판정을 한 CUDA kernel 안에서 처리한다.
- Provider 계약은 `strict_float64_resident_wavefront_v1`이며 실패한 chunk는 기존
  host-roundtrip으로 정확히 한 번 replay한다.
- RTX 3070 100k warm p50에서 stochastic depth 2는 `1.41x`, all-survive depth 10은
  `1.57x` 개선했다. Depth-10 1억 Ray 선형 환산은 `21.6분 -> 13.8분`이다.
- 공개 결과는 GPU host-roundtrip과 exact했고 fallback은 0회였다.
- CPU/CUDA 확률 수학함수 차이는 이산 exact + abs/rel `1e-12` + ULP `8` 계약으로
  검증한다. 실제 8,192-Ray 회귀 최대 ULP는 2였다.
- event tape 다운로드와 CPU ordered reducer는 남아 있으며 PERF-4C 대상이다.
- 상세 계약과 측정은 `docs/perf4b-device-resident-wavefront.md`,
  `docs/reports/2026-08-25_perf4a-perf4b-benchmark.md`를 따른다.

##### PERF-4C: GPU Receiver/Heatmap·결과 누적기

- 상태: 1차 완료 (2026-08-25)
- Provider 계약은 `strict_float64_gpu_summary_accumulator_v1`이다.
- PERF-4B event 배열을 device에 유지한 채 optical/reflection/contribution,
  Receiver flux와 heatmap을 CUDA에서 직접 누적한다.
- 일반 summary 실행은 전체 event tape 대신 compact summary와 path quota가 선택한
  경로만 CPU로 전송한다. 진단용 `gpu_accumulator="host"`는 4B 기준선을 유지한다.
- RTX 3070 100k warm p50에서 stochastic depth 2는 `1.858x`, trapped depth 10은
  `7.715x` 개선했고 전송량은 `99.925~99.978%` 감소했다.
- 이산 결과는 exact이며 GPU atomic 합산에 따른 최대 absolute error
  `5.239e-10`은 strict `1e-9` 계약을 통과했다. fallback은 0회였다.
- 100k p50 단순 선형 환산은 1억 Ray에서 약 `60.9초`와 `102.0초`지만, 실제 TV
  CAD·열·VRAM을 포함한 실측값이 아니므로 목표 달성 증거로 사용하지 않는다.
- 상세 계약과 측정은 `docs/perf4c-gpu-accumulator.md`,
  `docs/reports/2026-08-25_perf4c-gpu-accumulator.md`를 따른다.

##### PERF-4D: Compact GPU workspace

- 상태: 1차 완료 (2026-08-25)
- summary 실행의 전체 event geometry workspace를 compact scalar workspace와
  선택 path sparse retrace로 교체했다.
- RTX 3070 100k에서 workspace는 depth 2 `46.11%`, depth 10 `56.22%` 감소했다.
- wall time은 `0.970x`, `0.998x`로 동등하거나 소폭 느렸으므로 속도 개선으로
  표현하지 않는다.
- 이산 exact, strict float64 통과, fallback 0을 확인했다.
- 실제 TV ROI 장시간 VRAM·열 검증은 남아 있다.
- 상세 계약은 `docs/perf4d-compact-workspace.md`를 따른다.

##### PERF-4E: 필요한 Ray 수 감소

- 상태: primary MIS·Lambertian bounce MIS·표본 재사용 완료 (2026-08-25)
- PERF-4E-A: Lambertian/isotropic CAD face·datum Emitter의 Receiver-directed
  primary MIS를 구현했다. Gaussian/scalar-only는 source sampling으로 fail-safe
  fallback한다.
- PERF-4E-C: Auto convergence를 독립 구간 누적으로 바꿨다. `1→2→4→8배`는
  기존 15배 재실행 대신 8배 Ray만 처리한다.
- RTX 3070 직접 가시 synthetic 장면에서 seed 간 Flux 분산은 약 `7,460x`
  감소했고 CPU/GPU strict 정합성을 통과했다.
- PERF-4E-B: 순수 Lambertian 반사점에서 원래 cosine 분포와 Receiver 면적
  proposal을 혼합하는 단일 continuation-ray MIS를 구현했다. Receiver 방향 Ray도
  기존 BVH를 통과하므로 중간 차폐물은 그대로 판정된다.
- 반사광 synthetic 장면에서 20,000 Ray×12 seed 기준 Flux 분산은 약 `3,256x`
  감소했고, 작은각 근사 기준 bias는 `-0.092%`였다. CPU/GPU 이산 결과 exact와
  strict float64 허용오차를 통과했다.
- Specular는 기존 delta 경로를 유지하고 Gaussian·Mixed는 정확한 PDF 계약이
  없으므로 source sampling으로 fail-closed fallback한다.
- 실제 TV ROI 여러 seed 검증 전까지 primary sampling 기본값은 `source`다.
- 상세 계약은 `docs/perf4e-receiver-importance-sampling.md`를 따른다.

## 정합성 기준
- Random draw가 없는 같은-seed wavefront는 legacy scalar와 exact해야 한다.
- Stochastic wavefront는 같은 seed에서 chunk 크기, 반복 실행과 provider가
  달라도 exact해야 한다.
- Stochastic wavefront와 legacy scalar는 Monte Carlo stream이 다르므로
  receiver flux, hit ratio와 error estimate를 여러 seed의 통계 허용 오차로
  비교한다.
- 에너지 증가가 발생해서는 안 된다.
- face/component/material id 연결이 가속 전후 동일해야 한다.
- 성능 개선 때문에 optical assignment 우선순위가 달라져서는 안 된다.

## 현재 측정
- 장면: RT-2C 단일 평면 반사 synthetic scene
- Python: 3.13.3
- Gaussian 100,000 ray:
  - 초기: `5.126초`
  - PERF-1: `2.262초`
  - 개선: 약 `2.27배`
- Gaussian 1,000,000 ray:
  - PERF-1: `22.980초`
  - 처리량: 약 `43,515 ray/s`
- 실제 CAD에서는 triangle 수에 따라 교차 계산 비중이 크게 증가하므로 PERF-2 효과가 더 중요하다.

## PERF-2 측정
- TV 샘플 116 triangle:
  - 기존 recursive BVH: 약 `21,767 ray/s`
  - flat BVH: 약 `38,983 ray/s`
  - 개선: 약 `1.79배`
- Helical Gear 9,486 triangle:
  - brute-force: 약 `219 ray/s`
  - 기존 recursive BVH: 약 `4,972 ray/s`
  - flat BVH: 약 `19,099 ray/s`
  - 기존 BVH 대비 약 `3.84배`
- reference mismatch: `0`

위 PERF-2 TV 수치는 과거 116 triangle tessellation 기준이다. 최신 adaptive
tessellation에서는 full STEP이 106,352 triangle이므로 직접적인 전후 비교
기준으로 사용하지 않는다.

## PERF-3B 진입 기준 측정 (2026-08-18)

측정 환경:
- CPU: Intel Core i7-10700, 8 core / 16 thread
- Python: 3.13.3
- 기준 commit: `86eaa4b`
- seed: `20260717`

교차 micro baseline:
- CAD: `tv_leakage_roi_right_bottom_no_gap.stp`
- triangle: 50,944
- warm scalar flat BVH, 50,000 ray, 5회 중앙값
- 실행 시간: `2.3079초`
- 처리량: `21,664.6 ray/s`
- cold import: `0.9828초`
- BVH build: `0.9147초`
- brute-force reference mismatch: `0`

실제 저장 프로젝트 smoke baseline:
- 활성 ROI triangle: 45,167 / 50,944
- datum-plane Lambertian emitter, depth 10, 10,000 ray, 3회 중앙값
- 실행 시간: `2.5735초`
- 처리량: `3,885.8 primary ray/s`
- receiver hit: 1,276
- surface hit: 22,291
- 세 실행의 결과가 동일했다.

프로파일링에서는 Python BVH traversal, 특히 ray-AABB 판정이 교차 시간의
대부분을 차지했다. 따라서 batch 경계를 고정한 뒤 native/GPU traversal로
교체하는 개발 순서가 타당하다.

계약 구현 후 동일 조건 재현 benchmark:
- scalar BVH: `22,864.8 ray/s`
- CPU reference batch, size 256: `20,079.1 ray/s` (`0.878x`)
- CPU reference batch, size 4,096: `20,451.3 ray/s` (`0.894x`)
- CPU reference batch, size 50,000: `20,271.0 ray/s` (`0.887x`)
- scalar/batch face mismatch: `0`
- scalar/batch distance mismatch: `0`
- brute-force/BVH mismatch 50-ray sample: `0`

현재 adapter는 Python row loop와 배열 변환/결과 할당 비용 때문에 scalar보다
약 10~12% 느리다. 이는 예상된 reference 비용이며 성능 개선으로 계산하지
않는다. 이후 native/GPU 구현은 같은 계약과 mismatch `0`을 유지하면서 이
기준을 넘어야 한다.

## PERF-3B-1 wavefront 측정 (2026-08-18)

장면:
- RT-2C Gaussian 단일 반사 synthetic scene
- 100,000 primary ray, 200,000 CAD intersection query
- stored path OFF, summary contribution
- 3회 실행 중앙값

결과:
- scalar dispatch: `2.7691초`, `36,112 primary ray/s`
- batch 256: `3.9080초`, `25,588 ray/s`, `0.709x`
- batch 4,096: `3.8938초`, `25,682 ray/s`, `0.711x`
- batch 65,536: `3.9964초`, `25,022 ray/s`, `0.693x`
- receiver/surface/terminated/flux 전체 exact 일치
- semantic mismatch: `0`

PERF-3B-1 측정 당시 batch dispatch는 sampler의 ray별 generator/yield를
없애고 chunk 단위
dispatch 경계를 만들었지만 Receiver/plan/commit과 `intersect_rays()` 내부는
`python_cpu` reference의 Python scalar 처리를 사용했으므로 end-to-end로는
최선의 4,096 chunk도 scalar 대비 처리량이 약 28.9% 낮고 실행시간은 약
40.6% 길다. 이는 PERF-3B-2 native kernel이 제거해야 할 dispatch overhead
기준이다. PERF-3B-1 후보 중 4,096 chunk가 가장 빨랐으며 Stop 응답성과
향후 GPU launch 비용을 함께 고려해 유지한다.

## PERF-3B-2 native CPU 측정 (2026-08-18)

환경:
- Windows 10, Python 3.13.3
- Numba 0.66.0, llvmlite 0.48.0
- strict `float64`, `fastmath=false`, serial native kernel
- 동일 seed와 frozen ray 배열, 3회 warm 실행 중앙값

실제 CAD intersection micro:
- CAD: `tv_leakage_roi_right_bottom_no_gap.stp`
- triangle: 50,944, ray: 100,000
- Python scalar BVH: `4.4195초`, `22,627 ray/s`
- native scalar 호출: `1.0627초`, `94,101 ray/s`, `4.16x`
- native batch 256: `0.1173초`, `852,575 ray/s`, `37.68x`
- native batch 4,096: `0.0907초`, `1,102,531 ray/s`, `48.73x`
- native batch 65,536: `0.0876초`, `1,141,467 ray/s`, `50.45x`
- face mismatch: `0`, distance bit mismatch: `0`

준비 비용:
- 기존 BVH build: `0.9063초`
- immutable native scene pack: `0.0706초`
- 최초 JIT compile: `1.5052초`
- 최초 native execute: `0.0858초`
- 최초 native cold wall: `1.9288초`

100,000-ray 단일 반사 synthetic end-to-end(모든 case에서 비교를 위해
`intersection_backend="bvh"` 강제):
- Python scalar: `3.1828초`, `31,419 primary ray/s`
- native scalar: `4.6694초`, `0.682x`
- Python reference batch: `4.4098초`, `0.722x`
- native batch 4,096: `3.1531초`, `1.009x`
- receiver/surface/flux semantic mismatch: `0`

별도 `--no-write` 독립 재실행에서는 최대 CAD micro `48.98x`, native batch
end-to-end `0.961x`였다. 즉 wall-time 변동을 포함해도 교차 kernel은 약
49~50배였지만 전체 파이프라인은 baseline 수준이며 `1.20x` gate와는 충분히
떨어져 있다.

교차 커널의 큰 개선이 전체 실행에서 바로 나타나지 않은 이유는 이 synthetic
장면의 교차가 매우 싸고 Receiver 계산, reflection plan, Python 객체 복원과
row-order commit이 실행시간 대부분을 차지하기 때문이다. 더 중요한 실제
PERF-3B-2 측정 당시 사용 조건인 `max_depth >= 2`는 PERF-3B-1 wavefront
대상이 아니었으므로, 그 단계만으로 “백만 ray 수 분” 문제를 해결했다고
판단하지 않았다. 이 범위는 후속 PERF-3B-2A에서 별도로 연결했다.

따라서 native provider는 명시적 개발/benchmark opt-in으로 유지한다. 자동
선택 승격 조건은 실제 ROI end-to-end mismatch `0`, warm 성능 최소 `1.20x`,
기본 Python CPU 회귀 3% 이내, cold start와 배포 크기 수용이다. 현재 optional
module directory 크기는 Numba 약 33.2 MiB, llvmlite 약 116.6 MiB로 합계 약
149.8 MiB다. dist-info, 추가 native library와 archive overhead를 포함한 실제
배포 증가는 이보다 조금 클 수 있다.

별도 수동 기본 CPU 회귀 검증은 동일 프로세스에서 PERF-3B-1 parent와 현재 코드를
순서 교대해 각각 13회 확인했다. 100,000-ray scalar 중앙값은 parent
`2.7708초`, 현재 `2.7824초`였고 runtime 차이는 `+0.42%`, paired mean 차이는
`+0.03%`였다. 결과 payload는 exact 일치했고 Numba import/probe/JIT/native
scene pack은 한 번도 발생하지 않았다.

## PERF-3B-2A multi-bounce wavefront 측정 (2026-08-19)

아래 값은 실제 프로젝트를 같은 seed/geometry로 복원한 warm 반복 측정이다.
기본 `auto` 승격 근거가 아니라 명시적 multi-bounce batch 후보의 성능 gate다.

조건:

- 활성 ROI triangle: `45,167`
- primary ray: `100,000`
- `max_depth=10`
- stored path quota: `500`
- 같은 프로젝트, seed와 geometry

| 실행 | 시간 | Python scalar 대비 | Native scalar 대비 |
| --- | ---: | ---: | ---: |
| Legacy Python scalar 재측정 | `26.1930초` | `1.00x` | `0.43~0.44x` |
| Explicit Numba native scalar | `11.2558~11.4236초` | `2.29~2.33x` | `1.00x` |
| Numba wavefront batch 4,096 | `7.3109초` | `3.58x` | `1.54~1.56x` |
| Numba wavefront batch 1,024 | `7.0649초` | `3.71x` | `1.59~1.62x` |

Stored-path 객체 생성 최적화 전 같은 4,096 wavefront는 `8.8017초`, 최적화
후는 `7.4763초`였다. 실행시간은 약 `15.1%` 줄었고, 100,000개 완료 경로 중
`931`개만 실제 materialize했으며 저장소에 들어갈 수 없는 `99,069`개는
생략했다. 정량 Receiver/contribution 결과와 bounded stored-path 정책은 이
최적화의 영향을 받지 않는다.

현재 wavefront 결과는 같은 seed에서 chunk 크기와 반복 실행에 대해 exact하게
재현됐다. Random draw가 없는 specular 합성 장면은 legacy scalar와 Receiver
grid, flux, contribution, reflection summary와 stored path가 exact 일치했다.
Stochastic/Russian-roulette 장면은 `per_primary_seeded_v1` wavefront 내부에서
chunk/provider exact를 확인했다. Legacy depth-first scalar와 seed 3개씩 비교한
평균 차이는 Receiver hit `-0.92%`, surface hit `+0.33%`, flux `-0.93%`였고
95% CI가 모두 0을 포함했다. 유의한 bias 증거는 없지만 표본이 작아 더 큰
통계 검증 전까지 `auto`는 승격하지 않는다.

최종 결과는 실제 multi-bounce workload에서 native intersection을 wavefront로
연결하는 방향이 유효함을 보여준다. 그러나 1,000,000-ray 선형 환산은 약
`70.7초`다. 따라서 사용자가 요구한 백만 ray와 LightTools 이상 속도를
달성했다고 주장하지 않는다.
Wavefront timing에서 확인한 reflection planning과 ordered commit은 이후
2B/2C/2C-2에서 배열화와 compiled summary reducer로 이어졌다. 남은
prepare/validation/apply 비용과 stochastic planning 범위를 줄인 뒤 같은
active-ray buffer를 CUDA backend에 재사용한다.

## PERF-3B-2B compiled wavefront 측정 (2026-08-19)

실제 ROI canonical은 2A 역사적 표와 별개의 동일 조건 parent 재측정으로
비교했다.

조건:

- 원본/활성 ROI triangle: `50,944 / 45,167`
- primary ray `100,000`, `max_depth=10`, seed `42`
- contribution `summary`, stored path quota `500`
- runtime 기본 chunk `1,024` (batch-size 인자 생략)
- explicit Numba intersection, 기본 `auto` reflection planner
- warm wall-time 중앙값

| 비교 경로 | Wall 중앙값 | PERF-3B-2B speedup |
| --- | ---: | ---: |
| Legacy Python scalar | `26.193초` | `4.984x` |
| 역사적 Numba intersection scalar | 약 `11.42초` | 약 `2.173x` |
| PERF-3B-2A parent 동일 1,024 조건 | `7.0649초` | `1.344x` |
| PERF-3B-2B final default 1,024 | `5.2553초` | `1.000x` |

최종 2B canonical과 2A parent 모두 1,024 chunk이므로 `7.0649초`를 direct
speedup 분모로 사용한다. Final-source 5회 sweep의 1,024 p50 `5.1601초`와
final-default 3회 p50 `5.2553초`의 차이 `1.84%`는 실행 변동 범위다.

PERF-3B-2B 결과는 Receiver hit `12,652`, surface hit `225,482`, terminated
`87,348`, Receiver flux `0.040176617410112817`, stored path `500`, intersection
logical query row `309,119`이다. 현재 2B wavefront의 반복 실행과
`auto`/`python_cpu` planner 사이 ordered payload는 exact 일치했다. Path는
`931`개를 materialize하고 quota에 들어갈 수 없는 `99,069`개를 생략했다.
Mixed stochastic 장면의 legacy/Numba scalar 값은 timing reference이며 기존
statistical parity 계약상 이 exact 비교 범위에 포함하지 않는다.

Canonical planner가 `auto`라 native planner attempt는 `0`이다. 실제 모델의
모든 surface도 mixed scatter라 explicit native에서 Python sidecar 대상이다.
따라서 parent 대비 `1.344x`는 batch surface geometry materialization과 O(1)
stored-path quota의 효과이며 compiled planner의 실제 ROI speedup으로 해석하지
않는다.

Stable-source paths-on 교대 재측정에서 p50은 1,024 `5.1601초`, 4,096
`5.1863초`로 차이가 약 `0.51%`뿐이었다. 처리량은 동률로 판단하고,
별도 synthetic depth-10 `tracemalloc`의 Python allocation peak는 약
`9.65 MiB`에서 `37.64 MiB`로 늘고 Stop 원자 단위가 4배 커진다. 이 값은 실제
ROI process RSS가 아니라 scratch scaling 참고값이다. 따라서 runtime 기본은
memory와 Stop 응답성이 유리한 1,024를 유지한다.

Deterministic specular depth-10 synthetic는 10,000 primary ray, chunk 4,096,
warm p50로 Python/Numba planner를 비교했다.

| Scenario | Python planner | Numba planner | Speedup |
| --- | ---: | ---: | ---: |
| Summary, paths off | `1.304850초` | `1.113563초` | `1.172x` |
| Summary, paths on | `1.476219초` | `1.189662초` | `1.241x` |
| Detailed, paths off | `1.316303초` | `1.205550초` | `1.092x` |
| Detailed, paths on | `1.390839초` | `1.290724초` | `1.078x` |

네 scenario 모두 semantic mismatch `0`이다. 지원 범위에서는 compiled planner가
이득이지만 실제 mixed ROI, optional Numba/JIT와 배포 크기 gate를 함께 만족하지
않았으므로 기본 `auto`는 Python planner/scalar CPU를 유지한다.

현재 100,000-ray `5.2553초`의 1,000,000-ray 단순 선형 환산은 약
`52.6초`다. PERF-3B-2C에서 Python ray-state object를 대체할 SoA와 compact
event tape 경계를 구현했지만 기본 승격 기준은 통과하지 못했다. 다음 단계는
exact ordered reducer를 compile하고, `counter_rng_v2`로 stochastic planner
범위를 넓힌 뒤 같은 buffer와 whole-depth fallback 계약을 CUDA에 재사용하는
것이다.

## PERF-3B-2C-1 Event-tape v2 측정 (2026-08-19)

이번 단계는 같은 depth-major reflection plan을 `object_reference`와
`soa_event_tape` pipeline으로 counterbalanced 측정한다. 비교 시 seed, chunk,
intersection provider, planner, contribution mode와 path quota를 고정하며
semantic mismatch와 event/tape peak bytes를 함께 기록한다.

조건:

- 원본/활성 ROI triangle `50,944 / 45,167`
- primary ray `100,000`, `max_depth=10`, seed `42`
- summary, stored paths `500`, chunk `1,024`
- explicit Numba intersection, planner `auto`
- pipeline별 warmup 1회 뒤 3회 측정, 순서 `O,S,S,O,O,S` counterbalanced,
  source hash stable

| Pipeline | Wall p50 | Wall p95 | Primary ray/s p50 | 1M 선형 환산 |
| --- | ---: | ---: | ---: | ---: |
| `object_reference` | `5.232795초` | `5.288968초` | `19,110.25` | `52.33초` |
| `soa_event_tape` | `5.121246초` | `5.130226초` | `19,526.50` | `51.21초` |

SoA v2는 object-reference 대비 `1.021782x`, wall `2.132%` 개선됐다. State init
`0.084956초`와 advance `0.024213초`는 object state build `0.493646초`보다
작았다. Tape append `0.209180초`, seal `0.102317초`, 그 안의 public strict
validation `0.060045초`, Python reducer replay `1.052167초`와 stored-path hydrate
`0.024338초`가 추가됐다. Plan 전체는 `2.588661 -> 2.960907초`, commit은
`0.859558 -> 1.094071초`였다. State init은 state build와 같은 구간이고,
state advance/append/seal은 plan에, validation은 seal에, replay/hydrate는
commit에 포함되는 nested metric이므로 합산하지 않는다. Intersection/native
execute도 `0.476005/0.382826초`와 `0.482072/0.388773초`로 같은 범위였다.

두 pipeline은 Receiver `12,652`, surface `225,482`, terminated `87,348`, flux
`0.040176617410112817`, query `309,119`, path `500`, materialized/skipped
`931/99,069`와 semantic/grid/contribution/path hash가 exact했다. SoA
event/reducer count는 각각 `225,482`, primary-chunk paths-on tape peak 최대값은
`680,048 bytes`, run copy 회계는 `29,407,112 bytes`다. 별도 source-stable 10k
one-run A/B에서 paths-off/on peak는 `271,080 / 643,800 bytes`, copy 회계는
`1,131,996 / 2,952,580 bytes`였다. 이 tape-owned byte를 process RSS나 object
graph 전체 memory와 직접 비교하지 않는다. 실제 ROI는 mixed stochastic이므로 exact 범위는 같은
`per_primary_seeded_v1`의 두 wavefront pipeline 사이이며 legacy scalar에는
기존 statistical parity를 적용한다.

Provider는 여섯 measured run 모두 effective `numba_cpu`, `native_used=true`,
attempt/success `1,078/1,078`, native success row `309,119`, intersection fallback
`0`이었다. Planner `auto`는 effective `python_cpu`, logical/Python-sidecar row
`225,482/225,482`, native attempt `0`, fallback `0`이었다. 따라서 교차 성능은
실제 native intersection 실행이지만 compiled reflection planner의 speedup은
포함하지 않는다.

측정된 `1.021782x`는 자동 승격 gate `>= 1.05x`에 못 미치므로
`wavefront_pipeline="auto"`는 `object_reference`를 유지한다. Actual-event CSR은
2C-2 compiled reducer와 후속 CUDA의 메모리·데이터 경계이며, 1M 선형 환산
`51.21초`로 목표 달성을 주장하지 않는다. 재현 결과는 git-ignored
`outputs/perf3b2c_soa_event_tape/actual_roi_summary.json`에
`perf3b2c_actual_roi_comparison_v1`로 기록했다. Artifact SHA256은
`ef2ad80346d7e1ea44c00fc9cd19be0cfb75c9da00362231920782c486c9ad5e`,
benchmark script SHA256은
`89b223a2c128f83d1cfc76c5f9dee1e9aa8aee7cf5f1fb41f2ad5859c10cb783`다.

## PERF-3B-2C-2 Compiled reducer 측정 (2026-08-19)

2C-1과 같은 actual ROI, SoA tape, intersection/planner 조건에서 reducer만
`python_cpu`와 `numba_cpu`로 바꿨다. Reducer별 10k warmup 뒤 3회씩
`P,N,N,P,P,N` counterbalanced 측정했고 source hash는 전후 동일했다.

| Reducer | Wall p50 | Wall p95 | Primary ray/s p50 | 1M 선형 환산 |
| --- | ---: | ---: | ---: | ---: |
| `python_cpu` | `5.094436초` | `5.128807초` | `19,629.26` | `50.94초` |
| `numba_cpu` | `4.643004초` | `4.697531초` | `21,537.78` | `46.43초` |

Native는 p50 `1.097228x`/wall `8.861%`, p95 `1.091809x`/wall `8.409%`
개선했다. Reducer replay는 `1.062883 -> 0.443459초`(`2.3968x`), commit은
`1.101820 -> 0.643344초`(`1.7126x`)였고 plan은
`2.959407 / 2.962245초`로 같은 범위였다.

Native p50 내부는 prepare `0.174618초`, dispatch `0.310282초`, kernel execute
`0.021806초`, result validation `0.237018초`, apply `0.133177초`, path stage
`0.023353초`다. 이 값들은 nested timing이며 서로 단순 합산하지 않는다. 현재
kernel보다 prepare/validation/public object apply가 더 큰 후속 최적화 대상이다.

Receiver/surface/terminated `12,652 / 225,482 / 87,348`, flux
`0.040176617410112817`, path `500`, materialized/skipped `931/99,069`는 같았다.
Native tape/primary/event는 `98 / 100,000 / 225,482`, attempt/success `98/98`,
fallback `0`이었다. Seven semantic/hash family, grid/contribution/path와 ordered
float bits는 warmup/measured 전체에서 exact했다. Paths-off 10k quick도
`1.081299x`, exact였고 detailed synthetic은 정상 Python 선택/attempt `0`였다.

Sampled RSS peak delta p50 `4,210,688 / 3,735,552 bytes`는 allocator와 측정
순서에 민감하므로 memory 우위로 주장하지 않는다. Tape peak/copy는 두 reducer
모두 `680,048 / 29,407,112 bytes`로 같다.

Warmup에서 reducer cold JIT는 `2.382357초`였다. 따라서 warm gate
`>= 1.05x`를 통과했어도 짧은 단발 실행, optional Numba/llvmlite 배포와
GPU·Numba가 없는 PC의 CPU 무회귀 원칙 때문에 `wavefront_reducer="auto"`는
Python/no-probe를 유지한다. Explicit native는 반복 실행에서 사용할 수 있다.

Actual artifact SHA256은
`04bb4514a3a5909a5f8afbc551cecd4de3c84b70c11cada6d9335f7ec5dcf648`, final
audit SHA256은
`feacdb1acbb7e757d4690147bea8bf0e9a6b75439cc81b4573faa43e1877846a`다. 실제
사용자 input은 hash만 기록하고 repository fixture로 추가하지 않는다.
Actual 전용 harness SHA256은
`f11985a62911d0eb47312adb46990dd8c0bee6c11dc503c78667748ba49123e8`, repository
benchmark SHA256은
`0c7308f1a7effffde4b3efb534181d4dfbd369247ae5625731eb2acc7e688834`다.

## PERF-3C CUDA 측정 (2026-08-20)

Actual CAD 50,944 triangles와 frozen 100,000 rays의 warm intersection micro에서
CUDA 65,536 p50은 `0.012916초`(`7.743M ray/s`)로 Numba CPU 8,192
`0.089381초`보다 `6.920x` 빨랐다. Face/tolerance mismatch는 모두 `0`, 최대
distance absolute error는 `2.8422e-14`다. CUDA 8,192/16,384 speedup은
`2.650x / 4.094x`였다. 최초 cold 8,192 실행은 `0.942792초`이고 JIT
`0.852086초`를 포함한다.

모든 ray가 depth 10까지 유지되는 synthetic 100,000-primary control은 Numba
CPU/GPU p50 `1.787338 / 1.817925초`, GPU `0.983175x`였다. 즉 actual CAD
intersection micro 가속만으로 모든 end-to-end workload가 빨라진다고 주장하지
않는다. Count/grid/summary exact, path tolerance mismatch와 fallback은 `0`이다.

45,167 active-triangle actual ROI, depth 10, summary, paths 500의 실제
1,000,000-primary source-freeze isolated warm raw는
`7.277951 / 7.270346 / 8.085747초`, p50/p95는
`7.277951 / 8.004967초`, p50 처리량은 `137,401 primary ray/s`다.
Receiver/surface/terminated는 `126,609 / 2,250,471 / 873,391`, flux는
`0.03998454755283727`, path는 `500`이며 세 run outcome이 exact했다.
Representative p50 run의 logical intersection은 `176 / 3,085,763` batch/ray다.
그중 CUDA는 `92 / 2,710,197`, hybrid Numba CPU는 `84 / 375,566`이고 모두
attempt=success, hard fallback은 `0`이다. Planner `176/176`, reducer `16/16`
성공과 fallback `0`을 기록했다. Requested/effective provider는
`gpu_cuda / mixed`이며, 이는 정상 hybrid를 반영한다. Representative
intersection/GPU kernel/hybrid CPU는 `1.578990 / 0.822200 / 0.507024초`,
plan/commit/wavefront는 `2.078457 / 1.387784 / 6.988246초`다.

GPU 기본 chunk 65,536은 launch/transfer를 줄이지만 memory와 Stop 원자 단위를
늘린다. Actual 1M sampled RSS delta는 `57,720,832 bytes`, tape-owned peak/copy는
`40,527,016 / 293,678,488 bytes`다. Tape 회계는 VRAM peak가 아니며 CUDA
workspace도 run 중 자동 축소하지 않는다. `<8,192` hybrid가 작은 tail wave의
launch를 피하지만, VRAM peak와 실제 Stop latency는 후속 계측 대상이다.

8 seed × 512-ray `counter_rng_v2` statistical gate는 legacy stream 대비 Gaussian
fraction absolute delta `0.0167253`(gate `0.05`), emitted-flux relative delta
`5.3583%`(gate `10%`)로 통과했다. Counter stream은 chunk/provider/reorder exact지만
legacy stream과 개별 ray bit-exact가 아니라 통계 비교 대상이다.

재현 script는 `scripts/benchmark_perf3c_gpu_cuda.py`, actual CAD micro/control
artifact는 `outputs/perf3c_gpu_cuda/summary.json`이다. 각각 SHA256은
`a9eb801d1931ba3cb5ff9549d3631a001e56f7884bdaf6291c17a390dac952f5`,
`e2ced20f63ea8a654b3db4cae65d32b35790e6aa73b4c96b917cc293e8ffb527`다.
Actual ROI 1M artifact SHA256은
`13ca76ce6c4e8129ae7b5dfefbadaca8c20d06884b7264d0c60a5e65812fef2e`다.
상세 계약과 해석 제한은
`docs/changes/2026-08-20_perf3c-strict-fp64-cuda-wavefront.md`를 따른다.

## PERF-3D Host-overhead 측정 (2026-08-20)

PERF-3C와 같은 actual ROI 1M, depth 10, paths 500의 source-frozen warm raw는
`5.715687 / 5.541795 / 5.208224초`, p50/p95는
`5.541795 / 5.698298초`, 처리량은 `180,447 primary ray/s`다. PERF-3C p50
`7.277951초` 대비 `1.3133x`, latency `23.855%` 개선됐다.

Receiver/surface/terminated `126,609 / 2,250,471 / 873,391`, flux
`0.03998454755283727`, stored paths `500`과 ordered semantic hash는 PERF-3C와
세 run 모두 exact했다. Intersection/planner/reducer hard fallback은 `0`이다.

Representative Receiver/plan/commit/wavefront는
`0.216118 / 1.576959 / 1.145978 / 5.274202초`다. Intersection은
`1.985698초`로 PERF-3C representative `1.578990초`보다 불리했으므로 CUDA
intersection 개선으로 해석하지 않는다. Host overhead 감소가 전체 wall 개선을
만들었다.

Run accumulator는 `16 tape / 1,000,000 primary / 2,250,471 event`를 유지하고
마지막에 `1회 / 0.000637초` flush했다. Payload는 첫 65,536-primary tape만 full,
뒤 15개 tape는 omitted였고 copy accounting은
`293,678,488 -> 124,395,352 bytes`(`-57.642%`)다.

100k counterbalanced p50은 PERF-3C parent/PERF-3D per-tape/retained
`0.831689 / 0.772222 / 0.734558초`로 retained가 parent 대비 `1.1322x`, 같은
PERF-3D per-tape 대비 `1.0513x`였다. 성능 threshold는 report-only다.

CPU default paired p50은 actual `-1.80%`, synthetic `+0.812%`로 `3%`
no-regression gate 안이었다. Semantic/count/path exact, fresh-process
Numba/CUDA/native no-probe도 통과했다. 최종 Python suite는
`237 passed, 279 subtests passed`다.

상세 계약, artifact와 해석 제한은
`docs/changes/2026-08-20_perf3d-host-overhead-run-accumulator.md`를 따른다.
