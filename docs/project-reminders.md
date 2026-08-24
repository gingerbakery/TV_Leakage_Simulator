# 프로젝트 리마인드 목록

## 목적
- 사용자가 “나중에 다시 알려달라”고 명시한 항목을 한곳에서 관리한다.
- 단순 보류 항목과 명시적 리마인드 요청을 구분한다.
- 각 phase 진입 시 관련 문서를 다시 검토할 수 있도록 조건을 기록한다.

## 명시적으로 요청된 리마인드

### 1. 전체 프레임워크 전환 시점 재평가

사용자 요청:
- ray tracing 핵심 기능을 갖춘 뒤 전체 프레임워크 전환 시점을 반드시 다시 알려줄 것.

전환 후보:
- Next.js(React) + TypeScript
- Tailwind CSS + shadcn/ui
- Zustand
- Three.js 또는 React Three Fiber
- Supabase를 포함한 backend/database 구조

현재 상태:
- 프레임워크 전환 1~11단계가 완료되었다.
- React + TypeScript UI와 Three.js Viewer가 현재 개발 화면으로 사용된다.
- 12단계에서 React가 사용하는 Python API를 FastAPI 계층으로 분리했다.
- 13단계에서 React production build, FastAPI와 WebView2 데스크톱 패키지를
  통합해 전체 프레임워크 전환을 완료했다.
- 기존 인라인 UI는 `run_web_legacy.py`에 참조용으로만 보존하며 배포물에는
  포함하지 않는다.

다시 검토할 조건:
- RT-2C/RT-2D가 완료되어 ray tracing 핵심 workflow가 동작한다.
- Emitter, Receiver, Material, Result 데이터 계약이 안정화된다.
- `run_web.py` 단일 파일 유지보수 비용이 기능 개발 속도를 저하시킨다.
- 다수 개발자가 frontend/backend를 병렬 개발해야 한다.
- UI 컴포넌트와 상태 관리 복잡도가 현재 구조의 안전 범위를 넘는다.

### 2. V2 고급 표면 광학 모델 검토

사용자 요청:
- V2 phase부터 더욱 상세한 표면 특성을 반영하기 위해 고급 반사·산란 모델을 반드시 다시 검토할 것.

검토 대상:
- Oren–Nayar 계열
- Fresnel + Microfacet GGX/Beckmann
- Anisotropic Gaussian/GGX
- Retroreflective lobe
- 측정 BSDF/BRDF

다시 검토할 조건:
- V2 phase 계획을 시작한다.
- V1 표면 모델과 실측 분포의 차이가 설계 우열에 영향을 준다.
- 절대 밝기 정합 목표가 강화된다.

상세 문서:
- `docs/v2-advanced-surface-models.md`

## 중요 보류 백로그

다음 항목은 명시적인 “알람 요청”과는 구분되지만, 사용자가 향후 반영을 언급한 중요 백로그다.

### 분광·시감도 고도화
- M2/M3는 현재 보류한다.
- 색온도와 색좌표는 당장 범위에서 제외한다.
- 향후 절대 밝기 정확도와 광원 종류 구분이 중요해지면 시감도/분광 모델을 재검토한다.

### 측정 BSDF 연결
- V1에서는 파일 등록과 데이터 계약 중심으로 유지한다.
- 측정 파일 포맷, 좌표계, normalization, interpolation 방식은 후속 구현한다.

### Transform preview 표시 제어
- ray tracing 실행 중 또는 결과 확인 시 기존/이동 후 객체가 함께 보여 혼동되지 않도록 preview overlay를 사용자가 끌 수 있게 한다.

## 운영 원칙
- phase 계획을 시작할 때 이 문서를 확인한다.
- 리마인드 조건을 충족하면 관련 작업을 신규 phase 또는 backlog로 제안한다.
- 완료된 항목은 삭제하지 않고 상태와 완료 날짜를 기록한다.
- 새로운 리마인드 요청은 이 문서와 해당 기능 문서에 함께 기록한다.
## 성능 관련 리마인드
- PERF-1 Python hot path 최적화는 완료되었다.
- PERF-2 flat BVH CAD intersection 1차 가속은 완료되었다.
- 실제 회사 TV ROI CAD의 end-to-end 시간을 측정한 뒤 Embree/Open3D/GPU 필요성을 다시 판단한다.
- GPU 경로를 추가하더라도 GPU가 없는 PC에서 CPU fallback이 반드시 동작해야 한다.
- 전체 프레임워크 전환 시점은 RT-2D와 계산 백엔드 경계가 안정화된 뒤 다시 알린다.
- 전체 프레임워크 전환과 데스크톱 패키징은 13단계까지 완료되었다.
- 다음 재검토 대상은 main 병합, 사내 PC 배포 검증과 코드 서명이다.
- 다회 반사는 RT-3에서 구현됐으며 V1 허용 범위는 `max_depth=0~20`이다. 권장값은 빠른 검사 1회, 일반 비교 3회, 갇힌 고반사 구조 10회, 수렴 확인 20회다.
- PERF-3A 단일 반사 Fast Path는 완료되었으며, Fast summary 기준 백만 ray `23.19초`를 기록했다.
- PERF-3B 진입 기준 측정과 batch 교차 계약/CPU reference adapter는 2026-08-18 완료했다.
- virtual-plane fast path의 primary/secondary wavefront batch 연결은 2026-08-18 완료했다.
- native provider의 실제 ROI end-to-end 성능 gate를 통과하기 전까지 기본
  `auto` dispatch/provider는 기존 scalar/Python CPU를 유지한다.
- PERF-3B-2 optional Numba CPU provider prototype은 2026-08-18 완료했다.
- 실제 CAD intersection micro는 독립 실행에서 약 `48.98~50.45x`였지만
  단일 반사 synthetic end-to-end는 `0.961~1.009x`의 baseline 수준이어서
  기본 `auto` 승격을 보류했다.
- 기본 `auto`는 Numba를 import/probe하지 않으며 GPU·Numba가 없는 PC의 기존
  Python CPU 실행을 유지한다.
- 측정된 optional Numba/llvmlite module directory는 약 149.8 MiB이며 실제
  배포 증가는 더 클 수 있으므로 lightweight package에는 아직 포함하지 않는다.
- PERF-3B-2A multi-bounce depth wavefront는 2026-08-19 구현했다. 명시적
  `batch`, fast virtual-plane emitter와 `max_depth >= 2`에서만 사용한다.
- 기본 `auto`와 face/polygon emitter는 legacy scalar/Python CPU를 유지하며
  Numba를 probe하지 않는다.
- Random draw가 없는 specular wavefront는 legacy scalar와 exact하다.
  Stochastic/Russian-roulette wavefront는 `per_primary_seeded_v1`로
  chunk/provider/repeat exact지만 legacy scalar와는 statistical parity로
  검증한다. 구조 후보를 비교할 때 dispatch를 혼용하지 않는다.
- 실제 45,167-triangle, 100,000-ray, depth 10, stored-path 500 workload에서
  권장 1,024 wavefront는 중앙값 `7.0649초`, p95 `7.3970초`다. Python scalar
  대비 `3.71x`, native scalar 대비 `1.59~1.62x`다.
- Stored-path quota 밖 경로의 materialization을 생략해 같은 4,096 측정을
  `8.8017초`에서 `7.4763초`로 줄였다. materialized `931`, skipped `99,069`다.
- 세 seed stochastic legacy 비교는 hit/flux 평균 약 `-0.9%`이고 95% CI가
  0을 포함했지만 표본이 작다. `auto` 승격 전 더 큰 통계 gate를 수행한다.
- PERF-3B-2A의 백만 ray 선형 환산은 약 `70.7초`였으며 최종 목표를 달성하지
  못했다. PERF-3B-2B에서 surface geometry와 path quota 병목 일부를 후속
  최적화했다.
- PERF-3B-2B는 batch surface point/normal materialization, O(1) ordered
  stored-path quota와 optional compiled reflection planner를 2026-08-19 구현했다.
- 기본 `auto`와 scalar는 Numba runtime을 import하거나 native provider를
  probe하지 않는다. Native planner opt-in은 threshold의 `none`/`specular`만
  지원하고 mixed/stochastic와 Russian roulette는 `per_primary_seeded_v1`
  Python sidecar를 사용한다.
- Planner hard failure phase는 `input_prepare`, `initialize`, `execute`,
  `result_validation`이다. 같은 depth의 deterministic native candidate 전체를
  Python으로 replay하고 circuit breaker를 연다. Fallback row count에는
  stochastic sidecar를 포함하지 않는다.
- 실제 `50,944 -> 45,167` triangle ROI, 100,000 ray, depth 10, seed 42,
  summary, paths 500, runtime 기본 chunk 1,024 canonical 중앙값은
  `5.2553초`다. 같은 1,024 조건 2A parent `7.0649초` 대비 `1.344x`, Python
  scalar `26.193초` 대비 `4.984x`, 역사적 Numba scalar 약 `11.42초` 대비 약
  `2.173x`다.
- Canonical planner가 `auto`라 native attempt는 `0`이다. 실제 모델도 전부
  mixed라 explicit native에서 Python sidecar 대상이다. 위 개선은 batch surface
  geometry와 O(1) quota의 효과이며 compiled planner speedup으로 기록하지
  않는다.
- 실제 canonical 결과는 Receiver `12,652`, surface `225,482`, terminated
  `87,348`, flux `0.040176617410112817`, query row `309,119`, path `500`으로
  현재 wavefront 반복과 `auto`/`python_cpu` planner 사이 exact다. Legacy/Numba
  scalar는 mixed stochastic 계약상 timing reference다. Path
  materialized/skipped는 `931/99,069`다.
- Deterministic synthetic 네 scenario의 compiled planner speedup은
  `1.078~1.241x`, mismatch `0`이지만 실제 mixed ROI 및 배포 gate가 남아
  기본 `auto` 승격을 보류한다.
- PERF-3B-2B 완료 시점 전체 Python suite는 `160`개가 통과했다.
- Stable-source compact paths-on 교대 재측정은 1,024 `5.1601초`, 4,096
  `5.1863초`로 약 `0.51%` 차이의 동률 범위였다. Buffer scaling은 별도
  synthetic depth-10 `tracemalloc` 기준 약
  `9.65 -> 37.64 MiB`, Stop 원자 단위는 4배다. 이는 실제 ROI process RSS가
  아니다. 현재 runtime 기본은 메모리/응답성이 유리한 1,024다.
- PERF-3B-2B의 백만 ray 선형 환산도 약 `52.6초`라 목표 달성을 주장하지
  않는다. SoA state와 compact event tape는 2C에서 구현했으며 후속 순서는
  compiled ordered reducer, `counter_rng_v2`, CUDA다.
- PERF-3B-2C/2C-1은 `stable_active_soa_v1` active state와 실제 surface event 비례
  `ordered_primary_event_tape_v3` CSR을 사용한다. v2는 2026-08-19 구현했고,
  v3는 2026-08-24 Face emitter initial source-face 보존을 추가했다. Runtime-only
  `wavefront_pipeline="soa_event_tape"`를 명시할 때만 사용하는 experimental
  경로다.
- Tape v3 core는 정량 field를 항상 유지하고 initial source face와 path geometry는
  `store_ray_paths && max_stored_paths > 0`일 때만 `full_path_v1`, 아니면
  `omitted_v1`이다. Public runtime `seal()`은 vectorized `strict_v1`이다. Private
  `_seal_trusted()`의 `trusted_structural_v1`은 future compiled producer/benchmark
  전용이며 external data나 일반 runtime에서 선택하지 않는다.
- `python_ordered_v1`은 primary 순서로 Receiver/contribution/reflection/path를
  replay하며 object-reference 대비 deterministic/stochastic float bit와 dict
  order, chunk/provider exact를 보존한다.
- PERF-3B-2C-1 완료 시점 전체 Python suite는
  `184 passed, 154 subtests passed`다. 성능 threshold는 unit test가 아니라
  canonical benchmark에서만 판정한다.
- SoA v2 actual p50이 object-reference보다 빨랐지만 자동 승격 gate `>= 1.05x`를
  통과하지 못했으므로 `wavefront_pipeline="auto"`는 `object_reference`를
  유지한다. 기본 scalar와 GPU·Numba가 없는 PC의 no-probe CPU 경로에는 변화가
  없다.
- 실제 ROI 100,000-ray, depth-10 counterbalanced 측정 p50은 object-reference
  `5.232795초`, SoA `5.121246초`로 `1.021782x`, wall `2.132%` 개선됐다. P95는
  `5.288968 / 5.130226초`다. Semantic/grid/contribution/path hash는 여섯 run에서
  exact했고 event/reducer count는 `225,482`, paths-on tape peak는
  `680,048 bytes`, copy 회계는 `29,407,112 bytes`였다. 이 exact는 같은
  stochastic wavefront stream의 pipeline 비교이며 legacy scalar에는 statistical
  parity를 적용한다.
- 별도 10k paths-off/on A/B의 tape peak는 `271,080 / 643,800 bytes`, copy 회계는
  `1,131,996 / 2,952,580 bytes`였다. 이는 tape-owned byte 회계이며 process RSS가
  아니다.
- 여섯 measured run 모두 effective `numba_cpu`, `native_used=true`, intersection
  attempt/success `1,078/1,078`, success row `309,119`, fallback `0`이었다.
  Planner `auto`는 effective `python_cpu`, logical/Python-sidecar
  `225,482/225,482`, native attempt/fallback `0`이었다.
- SoA의 1M 단순 선형 환산은 `51.21초`, object-reference는 `52.33초`다. 둘 다
  이번 단계에서 백만 ray/LightTools 이상 목표 달성을 주장할 근거가 아니다.
- Actual-event CSR의 구조적 memory 개선과 read-only validation은 2C-2 compiled
  reducer와 후속 CUDA용 데이터 경계다. Strict/trusted/payload 회귀에 wall-time
  threshold를 넣지 않고 canonical benchmark에서만 성능 gate를 판정한다.
- Canonical artifact SHA256은
  `ef2ad80346d7e1ea44c00fc9cd19be0cfb75c9da00362231920782c486c9ad5e`,
  benchmark script SHA256은
  `89b223a2c128f83d1cfc76c5f9dee1e9aa8aee7cf5f1fb41f2ad5859c10cb783`다.
- PERF-3B-2C-1에서 예고한 compiled ordered reducer는 2C-2에서 완료했다.
- Runtime-only `wavefront_reducer=auto|python_cpu|numba_cpu`를 추가했다. Explicit
  native는 SoA summary만 지원하고 detailed는 정상 Python 선택이다. 기본
  `auto`는 Python/no-probe라 GPU·Numba가 없는 PC의 CPU 경로를 바꾸지 않는다.
- `ordered_summary_reducer_v1`은 serial strict `float64` 순서를 보존한다. Result는
  owned/read-only/no-alias이고 검증과 staged commit 뒤에만 publish한다. Native
  실패는 whole-tape Python replay 한 번과 run-local circuit breaker로 처리한다.
- Actual ROI warm p50은 Python/native reducer `5.094436 / 4.643004초`, p95
  `5.128807 / 4.697531초`다. P50 `1.097228x`, wall `8.861%` 개선이고 reducer
  replay는 `2.3968x`, commit은 `1.7126x` 빨라졌다.
- Native attempt/success `98/98`, fallback `0`이며 seven semantic/hash family,
  count와 ordered float bits가 exact하다. 최종 suite는
  `193 passed, 180 subtests passed`다.
- Cold reducer JIT `2.382357초`와 optional package/단발 손익 때문에 warm gate를
  통과했어도 기본 `auto`는 Python/no-probe를 유지한다. Native는 opt-in이다.
- PERF-3B-2C-2 artifact SHA256은
  `04bb4514a3a5909a5f8afbc551cecd4de3c84b70c11cada6d9335f7ec5dcf648`, final
  audit SHA256은
  `feacdb1acbb7e757d4690147bea8bf0e9a6b75439cc81b4573faa43e1877846a`다.
- 다음 성능 단계는 native prepare/result-validation/apply overhead 축소,
  `counter_rng_v2`, 같은 SoA/tape와 whole-batch fallback을 공유하는 CUDA
  backend 순서다.
- 실제 사용자 `.bitsam`은 성능 smoke 측정에만 사용했으며 repository fixture로 추가하지 않는다.
- Multi-bounce native intersection provider 실패는 현재 depth logical batch
  전체를 Python CPU로 다시 실행한다. GPU backend에서도 GPU 부재·초기화
  실패·실행 실패 시 같은 whole-depth-batch CPU BVH fallback을 유지한다.
- PERF-3C strict-float64 CUDA wavefront stack은 2026-08-20 1차 구현했다.
  Project `compute_backend="gpu_cuda"`가 명시된 경우만 GPU policy를 적용하고,
  기본 CPU와 legacy `.bitsam`은 CUDA/Numba no-import/no-probe를 유지한다.
- GPU 기본은 batch 65,536, CUDA BVH, SoA, `counter_rng_v2`, Numba planner와
  reducer다. Active wave `<8,192`는 Numba CPU hybrid이며 `8,192` 자체는 GPU다.
- GPU unavailable은 정상 CPU 선택이다. Input/initialize/execute/result-validation
  hard failure는 logical batch 전체 CPU replay 한 번과 run-local circuit breaker를
  사용한다. Provider별 GPU/hybrid count, failure phase와 timing을 결과에 남긴다.
- Face/count/grid/summary는 exact, CUDA distance/path는 strict FP64와 abs/rel
  `1e-12` gate를 사용한다. `counter_rng_v2`는 chunk/provider exact이고 legacy
  stream과는 statistical parity로 비교한다.
- GPU Face emitter primary는 vectorized batch로 생성해 source face를 CUDA BVH
  `ignore_faces`로 넘긴다. 최초 Face wave는 `<8,192`라도 CPU hybrid를 우회해
  CUDA를 직접 호출한다. `polygon_auto`는 계속 CPU scalar다.
- Actual CAD 100k intersection micro의 CUDA 65,536 처리량은 `7.743M ray/s`,
  Numba CPU 대비 `6.920x`다. Fully-active synthetic end-to-end는 `0.983175x`로
  근소하게 느렸으므로 모든 장면의 자동 speedup을 주장하지 않는다.
- Actual ROI 1M source-freeze isolated warm 3-run p50/p95는
  `7.277951 / 8.004967초`, `137,401 primary ray/s`다. Logical 176 batch는
  CUDA 92와 hybrid CPU 84로 분리되며 fallback은 `0`이다. Requested/effective
  provider는 `gpu_cuda / mixed`다. Artifact SHA256은
  `13ca76ce6c4e8129ae7b5dfefbadaca8c20d06884b7264d0c60a5e65812fef2e`다.
  LightTools 이상이라는 표현은 같은 조건의 독립 비교 전까지 사용하지 않는다.
- GPU chunk 65,536은 launch/transfer에는 유리하지만 memory와 Stop 원자 단위를
  키운다. Clean CPU-only/GPU package, VRAM peak, 실제 Stop latency, 다양한 GPU와
  NVIDIA driver/CUDA toolkit 조합을 배포 전 검증한다.
- PERF-3C 최종 repository test는 Python `226 passed, 256 subtests passed`,
  frontend `20 files / 128 tests passed`다.
- PERF-3D host-overhead 단계는 seed/Receiver numeric batch, run-retained ordered
  accumulator와 path-quota payload suppression을 구현했다. Actual ROI 1M p50은
  `7.277951 -> 5.541795초`(`1.3133x`, `-23.855%`), 처리량은
  `180,447 primary ray/s`다. Count/flux/path/ordered semantic은 exact다.
- GPU `auto`만 `run_accumulator`를 사용하고 CPU `auto`는 기존 `per_tape`와
  Numba/CUDA no-probe를 유지한다. CPU paired actual/synthetic는 `3%`
  no-regression gate 안이었다.
- PERF-3D의 retained/resident는 run-local CPU reducer accumulator다. 전체 ray
  state/scene GPU residency 또는 fused CUDA depth kernel 완성으로 표현하지 않는다.
  다음 병목은 depth 사이 host 왕복과 intersection/planner를 합치는 device kernel이다.
- PERF-3D 최종 repository test는 Python `237 passed, 279 subtests passed`,
  focused matrix는 `60 passed, 126 subtests passed`다. 상세는
  `docs/changes/2026-08-20_perf3d-host-overhead-run-accumulator.md`를 따른다.

### 2026-08-25 이후 GPU/CPU 정확도 기준

- 이전 bullet의 “CPU 기본 auto는 scalar/no-probe” 정책은 역사 기록이며 현재
  프로덕션 full-auto에는 적용하지 않는다.
- CPU/GPU 모두 `cpu_gpu_deterministic_batch_v1`을 사용해야 한다. CPU는 Numba
  unavailable 시 Python으로 원자 fallback하며 CUDA는 probe하지 않는다.
- GPU 완료 판정은 production preflight, GPU success batch, 동일 샘플 계약을 모두
  요구한다.
- `scripts/verify_gpu_cpu_accuracy.py --rays 100000` 결과의 모든 case가 exact여야
  한다.
- Receiver hit가 30개 미만이면 Flux 오차도 신뢰 부족이다. Heatmap은 별도로 평균
  `5 hit/cell` 이상을 1차 usable 기준으로 확인한다.
- Auto convergence `1→2→4→8배`는 누적 `15배` Ray를 처리한다. 1,200만 Ray 지연
  분석 시 마지막 Ray 수뿐 아니라 반복 실행, triangle 수, depth별 active ray를 함께
  기록한다.
- 다음 대규모 희귀-event 성능 과제는 Importance Sampling/Next Event Estimation과
  누적 sample 재사용이다.
