# PERF-4C GPU 결과 누적기 계약

## 목적

PERF-4B는 Ray 상태와 다회 반사를 GPU에 상주시켰지만, 모든 surface/Receiver
event tape를 CPU로 내려받아 Python ordered reducer가 다시 집계했다. 반사 depth와
Ray 수가 커질수록 이 전송량과 CPU 집계 시간이 전체 실행 시간을 제한했다.

PERF-4C는 다음 수치 결과를 CUDA에서 직접 누적하고, CPU에는 최종 요약과 실제로
표시할 소수의 path만 전달한다.

- optical profile별 hit 수, 입사 flux, 잠재 반사 flux
- 반사 lobe/depth별 hit 수와 flux
- Receiver별 direct/reflected/total flux와 lobe/depth 기여도
- Receiver heatmap의 셀별 flux, 제곱 flux, hit 수
- surface/Receiver/termination count와 최대 도달 depth

## 데이터 흐름

1. PERF-4B resident kernel이 primary Ray를 최대 depth까지 추적한다.
2. event 배열은 device에 유지한다.
3. `strict_float64_gpu_summary_accumulator_v1` kernel이 run-local 누적 상태를
   갱신한다.
4. 다음 chunk는 기존 device 누적 상태를 재사용한다.
5. CPU에는 compact summary와 path quota가 선택한 경로만 내려온다.
6. 기존 결과 스키마는 `_stage_ordered_summary_result` 경계에서 그대로 생성한다.

따라서 일반 summary 실행에서는 전체 event tape 다운로드와 CPU ordered numeric
reducer가 제거된다. `detailed_contributions=true` 또는 명시적
`gpu_accumulator="host"` 진단 실행은 PERF-4B full-tape 경로를 유지한다.

## 선택 계약

`run_direct_ray_trace()`의 runtime-only `gpu_accumulator` 값은 다음과 같다.

| 값 | 동작 |
| --- | --- |
| `auto` | GPU resident + run accumulator 조건이면 PERF-4C를 자동 선택 |
| `gpu` | 지원 조건에서 PERF-4C를 명시적으로 요청 |
| `host` | PERF-4B event tape + CPU ordered reducer 기준선 사용 |

이 값은 성능 진단용이며 `.bitsam` 저장 계약에는 포함하지 않는다. 일반 사용자는
`auto`를 유지한다.

## Path 저장

렌더링용 path는 전체 Ray를 내려받지 않는다. GPU가 기존 Receiver 우선 quota와
dead-end 대체 규칙을 적용해 필요한 primary path index만 선택하고, 선택된 event만
compact tape로 전송한다. `max_stored_paths=500`이면 수백만 Ray를 추적해도 최대
500개 path payload만 CPU로 전달한다.

## 정확도 계약

CUDA atomic add는 thread 실행 순서에 따라 부동소수점 덧셈 순서가 달라질 수 있다.
따라서 PERF-4C는 CPU ordered reducer와 bit-exact float 일치를 요구하지 않는다.

- 이산 결과: count, key, Receiver cell, lobe, depth가 exact
- 수치 결과: absolute/relative tolerance `1e-9`
- 에너지 증가와 NaN/Inf: 허용하지 않음
- 실제 대표 benchmark의 최대 absolute error: `5.239e-10`

ULP는 0 부근 또는 파생 error 지표에서 크게 보일 수 있으므로 진단값으로만 남기고,
이산 exact와 물리량 absolute/relative tolerance를 통과 기준으로 사용한다.

## 실패와 원자성

- allocation, upload, kernel, download, result validation 또는 선택 path 적용 실패 시
  해당 primary chunk의 GPU 결과를 publish하지 않는다.
- 실패 chunk는 변경되지 않은 입력으로 기존 host-roundtrip 경로에서 정확히 한 번
  재실행한다.
- 첫 실패 뒤에는 run-local circuit breaker로 resident/accumulator 경로를
  비활성화한다.
- 이전에 성공한 누적 상태는 flush한 뒤 fallback 결과와 순서대로 결합한다.

## 실행 증거

PERF-4C 사용 여부는 다음 결과 필드로 확인한다.

```text
gpu_resident_wavefront_success_count > 0
gpu_resident_wavefront_fallback_count = 0
gpu_summary_accumulator_contract = strict_float64_gpu_summary_accumulator_v1
gpu_summary_accumulator_success_count > 0
```

전송량과 path 선택은 다음 필드로 확인한다.

- `gpu_summary_accumulator_input_bytes`
- `gpu_summary_accumulator_output_bytes`
- `gpu_summary_accumulator_reused_state_count`
- `gpu_summary_selected_path_count`
- `gpu_summary_skipped_path_count`
- `gpu_summary_path_select_sec`
- `gpu_summary_path_download_sec`

## 검증 명령

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf4c_gpu_accumulator.py `
  --rays 100000 --repeats 3
```

`passed=true`, `discrete_exact=true`, `float64_tolerance_passed=true`, accumulator
success 1 이상, resident fallback 0을 모두 요구한다.

## 남은 한계

- 실제 회사 TV ROI·1억 Ray를 지속 실행한 열/VRAM/드라이버 검증은 아직 필요하다.
- GPU accumulator는 summary 중심이며 상세 face 단위 raw contribution export는
  full-tape 경로를 사용한다.
- PERF-4D는 traversal/shading kernel과 workspace를 더 깊게 결합한다.
- PERF-4E는 Importance Sampling, Next Event Estimation, MIS와 sample 재사용으로
  목표 error에 필요한 Ray 수 자체를 줄인다.
