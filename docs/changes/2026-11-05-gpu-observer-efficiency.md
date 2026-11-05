# Preview / Ray Tracing GPU 효율 점검과 개선

2026-11-05 · source checkout `db25ced` 기반 미커밋 변경.

## 관측과 한계

사용자가 보고한 700분은 180° 검색의 **예상 시간**이다. 실제 경과 시간이나 GPU 벤치마크 결과가 아니다. 사용자 도면·간격·재질·GPU 성공 수가 확보되지 않아 이 도면의 병목 비중이나 목표 시간을 확정할 수 없다. 계산량은 각도별 투영 셀 수 × 셀 반복 수의 합이다. 예를 들어 1600×900×100 mm 경계, 2 mm 검사 간격, 15° 각도 간격, 반복 2인 합성 설정은 145방향·78,816,600표본이다. 사용자 도면의 설정으로 단정하지 않는다.

읽기 전용 `setup_windows_gpu.bat` 결과: source checkout, Windows x64, Python 3.13.14 x64와 Node 24.18.0/npm 11.16.0은 확인됨. NVIDIA GPU 없음, driver 없음, CUDA Toolkit 없음. 설치나 사용자 서버 재시작은 수행하지 않았다. 따라서 이 환경에서 GPU 준비 완료, 실제 CUDA 실행, CPU/GPU 실기 정합, 속도 향상을 주장하지 않는다.

## 코드 점검 결과

| 경로 | 현재 구조 | 남는 비용 / 조건 |
|---|---|---|
| Preview 광원 추적 | 정확한 외부 유출광 집계를 위해 `host_roundtrip` 강제, CUDA BVH 사용 가능 | CPU 반사/escape 집계, 작은 wave의 CPU 보조 |
| 기존 관찰 휘도 역추적 | 교차는 CUDA batch, Ray generator·반사/광원 샘플링·MIS·휘도는 Python | 매 wave 입력/결과 전송과 Python 처리. 역추적 자체는 `allow_hybrid_cpu=False` |
| 일반 RT 자동 GPU | counter RNG, resident 반사, GPU summary accumulator, compact workspace, 선택 경로 재추적 | 최초 JIT, primary 생성·업로드, 결과 출력, 일부 상세 진단/대체 경로 |
| ROI `detection_boxes` RT | 정확한 ROI 판정을 위해 host transport/reducer 유지 | GPU 교차만으로 전체 GPU 실행이라 부르면 안 됨 |

`gpu_cuda_hybrid_small_waves`는 GPU와 작은 wave의 CPU 보조가 함께 발생했다는 진단이다. 정상적인 혼합 실행일 수 있으며, CUDA 성공 수가 0이면 GPU 사용 증거가 아니다. 모든 작은 wave를 GPU로 강제하면 실행/전송 비용으로 오히려 느려질 수 있어 임계값을 근거 없이 낮추지 않았다.

## 이번 실제 코드 변경

`gpu_cuda_observer.py`에 관찰 표본 한 개의 primary·반사·광원 NEE shadow·balance MIS·휘도 합산을 한 CUDA thread에서 처리하는 FP64 resident 경로를 추가했다. 같은 묶음의 반사마다 CPU로 돌아오지 않는다. 기존 production resident의 strict BVH device 함수를 재사용한다.

- 지원: Lambertian, 정반사, Gaussian, Mixed, 비반사 재질. Gaussian의 96점 구적 정규화·32회 legacy fallback mass·연속 proposal PDF·balance MIS를 기존 역추적 모델과 맞췄다. 아주 좁은 Mixed 로브의 delta 정의도 유지한다. 가상 사각/다각 광원과 Face 광원, Lambertian/Isotropic, 유한 Sphere Uniform/Lambertian을 지원한다. 방출 방향·custom normal·flip·면적/광속 법칙을 보존한다.
- 측정 BSDF는 아직 미지원이다. 다른 재질로 근사하지 않고 기존 교차 GPU/transport CPU 경로로 돌아가며 사유를 기록한다. 사용자 scene이 이 조건이면 새 resident 가속 효과를 기대할 수 없다. Gaussian proposal의 1,024회 안전 한도를 넘으면 오류로 처리하고 잘못된 표본을 집계하지 않는다.
- GPU 선택에서만 시도한다. 기존 production preflight의 FP64/실제 kernel 검증에 더해, 새 observer kernel의 직광·차폐·정반사·Lambertian·Gaussian·Mixed 실제 CUDA/CPU counter 검사도 통과해야 한다. 직접광 L·차폐 0·mirror ρL의 해석해를 확인하고 CPU 대체는 실패로 판정한다. 이 광학 guard는 장치/kernel별로 최초 한 번만 수행한다. 실제 도면의 최초 묶음 최대 32표본도 같은 counter 샘플로 독립 CPU geometry traversal과 비교하고 실패한 GPU 결과는 집계하지 않는다.
- 실행 실패 시 같은 counter 샘플의 CPU equation 경로로 해당 묶음을 한 번만 재실행하고 이후 GPU 호출을 중지한다. CPU 대체 사유·묶음 수와 실제 CUDA 성공 수를 분리한다. 이 대체 경로가 빨라졌다고 주장하지 않는다.
- `observer_counter_latin_v1`은 기존 Python RNG와 다른 샘플 생성 계약이다. 기존 CPU 기본 결과와 Ray별 값이 같다고 주장하지 않는다. 같은 counter 샘플의 CPU equation oracle을 실기 비교 대상으로 제공한다. 두 샘플러는 유한 표본에서 다른 검출 결과가 가능하다.
- workspace는 최대 깊이에 따라 묶음 수를 제한하며 전체 180° grid를 GPU에 할당하지 않는다. Allowed region이 없으면 표본을 NumPy로 셀 단위 집계하고 셀별 최강 대표 경로만 실제 깊이로 GPU에서 압축해 내려받는다. 부분 셀은 다음 묶음과 합치고 같은 세기의 경로는 첫 표본을 유지한다. Allowed region이 있으면 샘플별 실제 segment 판정을 위해 기존 개별 표본 경로를 유지한다. threshold/display 제한은 기존 규칙을 유지한다. NumPy 합산은 순차 합산과 수 ULP 차이가 가능해 휘도는 엄격한 float64 허용 오차로, 횟수·경로는 이산 일치로 판정한다.
- Stop은 CUDA 묶음 완료·검증·집계 경계에서 보관한다. 같은 worker/device 상태를 이어가고 이미 집계한 표본을 반복하지 않는다. 실행 중인 동기 kernel 자체를 강제 중단하지 않는다.
- UI는 역추적의 샘플 계약과 일반 RT의 동일 샘플 계약을 구분한다. 계약 필드가 없다는 사실만으로 ‘이전 결과’라고 단정하던 문구를 수정했다. 역추적 전체 실기 정합은 여전히 미검증으로 표시한다.
- 공통 준비 경로에서 B-rep source face → 정밀 삼각형 인덱스를 도면 세션에 보관한다. 같은 도면의 재실행 때 전체 정밀 face 배열을 다시 순회하지 않는다. 인덱스는 Python 정수 목록 대신 compact 64-bit 배열로 보관한다. 새 face 배열로 교체되면 다시 구축한다. 서버가 관리하는 도면 배열은 세션 중 불변이라는 기존 수명 규칙을 따른다. 취소된 부분 인덱스는 저장하지 않는다.
- 기존 CPU 역추적의 Latin 셀 배치는 셀 반복마다 동일 seed의 MT/shuffle을 다시 만들지 않고 제한된 cache로 재사용한다. legacy RNG 값은 그대로다. 반복 8의 합성 검사에서 shuffle 호출 수가 1/8이고 최종 물리 결과는 정확히 같다. 전체 해석 시간 8배 개선이라는 뜻은 아니다.
- API 결과에 준비 시간과 geometry cache hit를 root/observer performance 모두 기록한다. Compute 상세에 형상·BVH 재사용 여부와 준비 시간을 표시하고, Preview 준비 중에는 실제 단계 문구를 사용한다. 준비와 순수 계산의 비용을 혼동하지 않기 위한 운영 표시다.

GPU 초기화/컴파일/전송 시각 분리와 실측 튜닝은 추가 작업이다. 현재 첫 `kernel_sec`에는 최초 JIT가 포함될 수 있다. 32표본 runtime 일치 guard는 전체 실기 정확도 검증의 대체물이 아니다. CPU equation oracle 시간은 기존 production CPU tracer 성능 기준으로 사용하면 안 된다.

## 추가 개선 순서

1. **실기 수치/성능 gate**: 같은 도면/설정/세션으로 최초 1회 + warm 2~3회. CUDA 성공/시도, CPU hybrid/fallback, GPU/host 처리 시간, emitter/재질 종류를 함께 기록한다. CPU fallback을 가속 성공으로 보지 않는다.
2. **관찰 GPU coverage 확대**: Gaussian/Mixed는 이번에 구현했다. 남은 측정 BSDF의 forward BRDF 평가를 그대로 이식한다. 각도 방향을 임의 전치하거나 Lambertian으로 대체하지 않는다.
3. **Preview 광원 escape 집계 GPU화**: Block·실제 경계·Allowed segment 판정을 device accumulator로 옮긴다. 현재 이 부분은 CPU 구조를 유지한다.
4. **RT primary와 출력 최적화**: 기존 계측에서 primary 생성/업로드가 큰 경우에만 같은 counter 계약의 GPU primary 생성을 도입한다. 상세 contribution/event tape가 큰 경우 compact 경로 확대를 검토한다. 이미 GPU 반사·집계가 동작하는 일반 RT를 새로 GPU화했다고 표현하지 않는다.
5. **장치별 batch / 각도 묶음**: 실제 kernel/전송 비중으로 묶음 크기·CPU hybrid 임계값을 선택한다. VRAM·최대 깊이와 Stop 응답성을 함께 제한한다. coarse 검색에서 0이었다는 이유로 정밀 공간/각도를 생략하면 미검출 위험이 있어 별도의 근사 모드로만 검토한다.

Ray 수·검사 간격·각도·최대 반사를 몰래 줄이지 않았다. 현재 700분이 몇 분으로 줄어드는지는 실기 측정 전까지 미확인이다.

## 검증 / 실기 인계

CPU 방정식 검사에는 직접광 L, mirror ρL, diffuse 균일 반구 ρL, 불투명 차폐, Numba 숫자 helper typing/수치, 묶음 분할 및 재개, GPU 실패 시 같은 counter replay, CPU-only 진단을 포함한다. 이는 CUDA kernel 컴파일이나 GPU 실행 성공 증거가 아니다.

Gaussian/정밀 면 인덱스/셀 집계 확장 후 최종 회귀 검사 결과는 아래 기록한다. 실기용 스크립트는 `--help` 진입만 확인했고, GPU 실행 gate는 이 환경에서 실행하지 않았다. 기존 큰 bundle 안내는 남아 있다. 커밋·푸시는 수행하지 않았다.

- 관련 backend 116건 통과. 마지막 optical-preflight CPU 대체 거부 검사를 추가한 뒤 신규 GPU 방정식 검사 13건을 재실행해 통과했다(서로 다른 backend 검사 총 117건).
- Compute/Preview/일반 RT UI 58건, TypeScript 포함 production build 통과.
- 준비 인덱스는 같은 source 배열을 두 번 순회하지 않고, 새 배열에서는 재구축하는지 확인했다. legacy CPU 셀 RNG 배치 cache는 비캐시 결과와 정확히 같다.
- compact 셀 집계는 raw 표본과 횟수·경로를 정확 비교하고, 휘도·최댓값은 상대/절대 `1e-12` 기준으로 비교했다. Gaussian BRDF/PDF/정규화는 독립 기존식과 정상·45°·89.9°에서 비교했다. 이 CPU 검사는 CUDA 실행 증거가 아니다.
- 실기 보고의 `initialization_and_optical_preflight_sec`와 `first_run_with_initialization_sec`에는 최초 optical guard 비용을 구분해 기록한다. warm 값은 `warm_timing_sec`다. equation oracle 시간은 production CPU 성능과 비교하지 않는다.

실기에서는 source launcher `run_web_gpu.bat`의 production preflight를 먼저 통과해야 한다. 이후 같은 checkout에서 다음 gate를 실행한다.

```powershell
.\.venv-gpu\Scripts\python.exe scripts\verify_observer_resident_gpu.py --rays 8192 --repeats 3
```

각 case의 `passed=true`, CUDA 성공 > 0, CPU 대체 없음과 첫/warm 시간을 확인한다. 출력에 commit·관련 파일 SHA-256을 기록해 미커밋 입력도 식별한다. 직접광·정반사·차폐의 해석해 gate와 동일 counter의 CPU/GPU 경로·휘도 gate를 함께 적용한다. 실패하거나 GPU가 없으면 통과로 보고하지 않는다. 합성 장면 gate는 실제 TV 광학 보정이나 180° 실도면 성능의 증거가 아니다.

일반 RT 실기는 기존 `verify_gpu_cpu_accuracy.py`, `benchmark_perf4b_resident_wavefront.py`, `benchmark_perf4c_gpu_accumulator.py`, `benchmark_perf4d_compact_workspace.py` 절차와 acceptance gate를 유지한다. CPU/GPU 정확도·수광 hit 충분성·히트맵 품질·시간은 별도로 판정한다.
