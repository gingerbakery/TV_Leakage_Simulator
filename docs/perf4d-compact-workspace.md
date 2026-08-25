# PERF-4D Compact GPU Workspace 계약

## 목적

PERF-4C summary 실행은 전체 event geometry를 CPU로 내려받지 않지만, GPU
workspace에는 여전히 모든 Ray·모든 depth의 point/normal/distance 배열을
할당했다. PERF-4D는 Receiver/Heatmap 통계에 필요하지 않은 geometry 배열을
제거하고, 화면에 표시할 소수 경로만 두 번째 CUDA 실행으로 재추적한다.

## 실행 계약

- 기본 summary 실행: `compact_summary_sparse_path_retrace_v1`
- 진단용 전체 event 실행: `full_event_geometry_workspace_v1`
- compact 경로는 scalar event 정보와 GPU summary accumulator를 유지한다.
- point/normal/distance는 `max_stored_paths`에 필요한 후보만 sparse retrace한다.
- sparse retrace 준비·실행·검증 중 하나라도 실패하면 해당 logical chunk 전체를
  기존 정확 경로로 한 번 replay한다.
- full/compact workspace cache는 분리한다. 진단용 full 실행이 이후 compact
  실행의 VRAM을 다시 증가시키지 않아야 한다.

## 결과 증거 필드

`metrics._performance_summary`에서 다음 값을 확인한다.

- `gpu_resident_workspace_contract`
- `gpu_resident_workspace_peak_bytes`
- `gpu_resident_event_geometry_capacity`
- `gpu_summary_path_retrace_sec`
- `requested_gpu_workspace`
- `gpu_resident_wavefront_fallback_count`

## 검증 기준

```powershell
.\.venv-gpu\Scripts\python.exe scripts\benchmark_perf4d_compact_workspace.py `
  --rays 100000 --repeats 3
```

- production CUDA preflight 통과
- full/compact의 이산 결과 exact
- strict float64 `abs/rel 1e-9` 통과
- compact workspace byte가 full보다 작음
- compact geometry capacity가 full primary chunk보다 작음
- resident fallback `0`

## 2026-08-25 RTX 3070 결과

| Case | Full p50 | Compact p50 | 속도비 | VRAM workspace 감소 |
| --- | ---: | ---: | ---: | ---: |
| stochastic depth 2 | 0.06214초 | 0.06409초 | 0.970x | 46.11% |
| trapped depth 10 | 0.09581초 | 0.09598초 | 0.998x | 56.22% |

PERF-4D의 1차 효과는 wall time 단축보다 VRAM·전송 대역폭·대규모 chunk 안정성에
있다. 100k synthetic 장면에서는 속도가 거의 같거나 약 3% 느렸으므로 속도 향상으로
표현하지 않는다.

## 남은 검증

- 회사 TV ROI 장면에서 1억 Ray·10회 반사 장시간 VRAM peak 측정
- RTX A4000에서 cold 1회와 warm 3회 측정
- Stop 요청과 sparse retrace 동시 발생 회귀
- 여러 Receiver와 `max_stored_paths=0/500/대용량` 조합 측정
