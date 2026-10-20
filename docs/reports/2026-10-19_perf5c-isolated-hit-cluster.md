# GPU 근접 교차 후보군 검증

## 결론과 적용 여부

아주 가까운 두 면에서 원래 면 선택과 물성을 유지하면서 전체 BVH 재탐색을
줄이는 실험 경로를 구현했다. TV 코너 CAD 100,000 Ray의 별도 계수 실험에서는
1,099,987개 교차 요청 중 1,099,984개를 후보군 검사로 처리하고 3개만 원래
GPU 탐색으로 되돌렸다. 이 3개는 CPU fallback이 아니다.

그러나 진단 계수를 끈 warm 7회 비교에서 CAD 가속은 **1.039~1.042배**에 그쳤다.
다른 세 조건의 이득도 일관되지 않아 **제품 기본값으로 채택하지 않는다**.
PERF 5B의 제품 개선은 유지하며, CPU와 GPU 모두 최소 2배 목표는 아직 미완료다.
새 실험 수치를 이전 고정 기준의 GPU CAD 1.470배에 곱하지 않는다.

## 기존 판정 보존

기존 교차 계산은 단순히 가장 가까운 면만 선택하지 않는다. 거리 차이가
`1e-10 mm` 이내이면 면 번호와 원래 BVH 방문 순서가 결과에 영향을 준다.
이 규칙은 비추이적이므로, 최근접 면 근처만 잘라 계산하면 mirror가 absorber로
바뀌는 오류가 생길 수 있다. 이 실험은 기존 규칙 자체를 변경하지 않는다.

새 경로의 허용 조건은 다음과 같다.

- 패딩된 보조 BVH에서 원래 FP64 삼각형 식으로 실제 최소 거리를 찾고,
  그 거리에서 `8e-10 mm` 이내의 후보를 4개 또는 8개까지 보관한다.
- 후보 수가 한 번이라도 상한을 넘으면 전체 원래 GPU 탐색으로 돌아간다.
  나중에 더 가까운 hit가 나와도 이미 발생한 overflow를 취소하지 않는다.
- 최소 거리에서 `4e-10 mm` 초과, `8e-10 mm` 이하의 간섭 후보가 있으면
  단축하지 않는다. 먼 hit의 과거 선택이 가까운 후보군에 영향을 주지 못하도록 한다.
- 원래 BVH의 경로 code와 공통 조상으로 leaf 방문 순서를 복원한다.
  같은 leaf에서는 원래 `ordered_faces` 순서를 따른다.
- leaf 진입 시점의 기존 best 거리로 원래 가지치기를 재현한다. 같은 leaf 안에서
  best가 갱신됐다고 그 leaf의 남은 삼각형을 다시 bbox로 잘라내지 않는다.
  bbox의 반올림된 진입 거리가 삼각형 거리보다 조금 큰 경우도 이 규칙대로 처리한다.
- 후보 leaf가 원래 bbox에서 도달 불가하거나, 거리·좌표 정밀도 guard,
  근평행 방향, 삼각형 가장자리, epsilon·Receiver cap 경계 등에서 확신할 수 없으면
  기존 GPU 탐색으로 돌아간다. 빠른 탐색의 miss를 그대로 확정하지 않는다.

추가 거리 범위는 **기존 반사·교차 허용오차를 완화하는 설정이 아니라**
기존 판정을 단축할 수 있는지 확인하는 내부 증명 범위다. 계산에는 원래
source face ignore, traceability, Receiver cap, 삼각형 번호와 난수 key를 유지한다.
수송·반사 방향·광량 합산 코드는 원래 경로를 사용한다.

원래 tree의 유한 bounds, 자식 포함 관계, 순환·도달성, leaf별 면 소유권,
깊이 상한도 검사한다. 원래 tree와 triangle 배열을 덮어쓰지 않는다.
부가 topology와 보조 tree는 원래 scene 객체가 바뀌면 재구성한다.

## 실제 장치와 비교 조건

전달 방식은 Git source checkout이다. `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`와
미커밋 제품 개선에 별도 실험 factory를 적용했다. RTX 3070 8GB, Python 3.13 x64,
Numba 0.66.0, CUDA Toolkit 13.1 환경이다. 서버는 재시작하지 않았다.

production preflight의 네 bool은 모두 true이며,
`preflight_scope=production_ray_bvh`,
`provider_contract=strict_float64_bvh_v1`이다. 모든 정상 비교에서
`compute_execution_state=gpu_active`, CUDA 성공/시도는 CAD 2/2,
나머지 16/16, CPU hybrid와 resident fallback은 0이다.
`monte_carlo_contract=cpu_gpu_deterministic_batch_v1`을 유지한다.

같은 프로세스에서 원본·4후보·8후보 순서를 번갈아 실행했다.
warm 표는 계측용 factory 교체 준비를 제외한 실제 ray-trace 호출 시간이다.
실험 harness의 교체 비용을 포함한 프로그램 전체 응답 시간으로 표현하지 않는다.
각 조건의 첫 실행과 warm 7개 값을 모두 남겼다. CAD는 100,000 Ray,
나머지는 1,000,000 Ray다. CAD·확률 반사·갇힌 구조는 Datum Plane,
직접 도달은 Face emitter다. 아래는 진단용 atomic 계수를 끈 warm 중앙값이다.

| 조건 | 원본 초 | 4후보 초 | 8후보 초 | 4후보 속도비 | 8후보 속도비 |
| --- | ---: | ---: | ---: | ---: | ---: |
| TV 코너 CAD | 0.167135 | 0.160925 | 0.160474 | 1.039배 | 1.042배 |
| 확률 2회 반사 | 0.169208 | 0.165441 | 0.174447 | 1.023배 | 0.970배 |
| 갇힌 구조 상한 10 | 0.231721 | 0.235006 | 0.238013 | 0.986배 | 0.974배 |
| Face 직접 도달 | 0.228358 | 0.237335 | 0.243564 | 0.962배 | 0.938배 |

CAD 최초 해석 시간은 원본 5.419초, 4후보 5.533초, 8후보 5.572초다.
후보의 별도 초기 topology·tree·factory 준비는 각각 1.861초와 1.709초이며,
이를 합친 최초 시간은 약 7.394초와 7.281초다. 첫 해석에는 JIT·업로드도
포함되므로 warm 수치와 구분한다. CAD import 시간 단축으로 해석하지 않는다.

컴파일 자원은 원본 114 registers·40 local bytes에서 후보 123 registers,
4후보 104 local bytes 또는 8후보 168 local bytes로 늘었다. 추가 local 배열과
정렬·메타데이터 조회 비용이 재탐색 감소의 이득을 상쇄하는 후보 원인이다.
이 수치만으로 실제 local memory 접근 횟수나 단일 원인을 확정하지 않는다.

## 수치 검증과 복구 경로

warm 비교의 모든 이산 결과와 FP64 절대/상대 `1e-9` 물리량 기준이 통과했다.
별도 scalar 검사는 전체 생성 함수를 원래 BVH oracle과 비교하며 거리 bit와
면 번호를 확인한다. 최근접 잘라내기의 반례, 720개 순열과 10,000개 무작위
먼 hit 이력, 간섭 구간·후보 overflow·Receiver cap을 포함한다.

실제 GPU 전용 검사는 미세 간격의 면 물성, ROI별 반사 재편집, MIS,
CAD 표시 경로, 실제 1,000회 반사와 사건 window 재시도·재추적을 포함한다.
다중 leaf tree에서 실제 `17 × 1,001`개 surface hit를 단축 경로가 처리하는지도
독립 확인한다. GPU 실행 뒤 오류를 주입하는 검사는 미확정 suffix만 CPU로
복구하며, 의도한 오류 조건은 `gpu_mixed`로 보고한다. 이를 full GPU 성공으로 세지 않는다.
Stop 검사는 독립 workspace를 사용해 실제 overflow가 발생하는 상황에서
부분 사건이 확정되지 않는지 확인한다.

## 기록과 다음 후보

최종 소스의 전용 검사는 **23개 통과, 82.078초**다.
앞선 정렬 후보를 포함한 393개 통합 회귀와는 별도 실행이다.
실험의 두 JSON에 기록한 모든 제품·벤치마크 의존 파일 hash가 현재 파일과
일치하는지도 확인했다. 전용 로그는
`outputs/perf5_transport/isolated-cluster-targeted-tests.log`다.

Compute Sanitizer memcheck 4조건은 39.800초, synccheck 2조건은 22.678초에
통과했고 두 error summary는 0이다. 4후보의 CAD 재편집·간섭 구간·overflow와
8후보의 다중 leaf 실제 1,000회 반사·미세 간격 Receiver cap을 포함했다.
원시 로그는 같은 폴더의 `isolated-cluster-memcheck.log`,
`isolated-cluster-synccheck.log`다.

- `outputs/perf5_transport/isolated-cluster-paired.json`: warm 7회 비교,
  첫 실행과 별도 준비 시간, 물리량 정합, CUDA 증거와 source hash.
- `outputs/perf5_transport/isolated-cluster-diagnostic.json`: atomic 계수를 켠
  100,000 Ray의 별도 진단. 계수용 실행 시간을 성능 채택 근거로 사용하지 않는다.
- `scripts/benchmark_perf5_gpu_isolated_cluster.py`: 제품과 분리된 실험 factory.
- `scripts/perf5_isolated_hit_cluster.py`: 원래 tree 검증과 CPU 기준 판정.

다음은 흔한 1~2 hit를 local 배열 없이 scalar slot으로 처리하고, 더 많은
후보는 원래 GPU 탐색에 맡기는 특수화다. 여기에 원래 연산 순서를 유지한
slab 최적화의 결합을 별도로 검토한다. 둘 다 아직 성능 또는 제품 적용이
확정된 기능은 아니다. LT 물리 정합 검증과 통계적 표본 부족 해소도 별도다.
