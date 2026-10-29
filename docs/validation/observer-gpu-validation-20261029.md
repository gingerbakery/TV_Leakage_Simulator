# Preview 역추적 GPU 구현 및 검증 — 2026-10-29

GPU 배치 경로 구현 완료. CPU 기능 및 scalar/batch 정합 통과. 실제 CUDA 실행·CPU/GPU 정합·속도는 미검증.

## 입력과 환경

- 전달 형태: Git source checkout, baseline `f1c31cf241cf8a48e3f66728777c2fafd38eb70a` + 미커밋 작업 파일.
- 실제 검사 코드의 SHA-256은 원시 JSON의 `source_sha256`에 기록했다. 보고서 작성 시 현재 파일과 모두 일치했다.
- `setup_windows_gpu.bat` 기본 읽기 전용 점검: Windows x64, Python 3.13.14 x64, Node 24.18.0/npm 11.16.0 통과.
- NVIDIA GPU 없음, driver 없음, CUDA Toolkit 13.1.1 없음. 설치·시스템 변경·재부팅·서버 재시작은 하지 않았다.
- 기존 `.venv-gpu`에서 production preflight 실행: `cuda_driver_unavailable`.
- available=false, strict_float64=false, kernel_executed=false, kernel_verified=false, scope=production_ray_bvh, provider_contract=strict_float64_bvh_v1.

## 완료한 변경

- 관찰 1차, 반사 후속 및 광원 연결 차폐 광선을 8,192개 이하 wavefront batch로 기존 production CUDA BVH에 연결했다.
- `compute_backend=gpu_cuda` 선택을 따른다. 발광/반사/측정 BSDF support 확인과 virtual Emitter 교차 및 집계는 공통 CPU 코드다.
- 광선별 독립 RNG와 표본 순서 누적으로 CPU scalar, CPU batch 크기 1/7/8192의 결과가 같음을 검사했다.
- 광원 추적과 분리된 역추적 Compute 상태, CUDA 시도/성공, CPU fallback 원인을 표시한다.
- GPU 실패/부분 실패는 CPU 대체/혼합 실행으로 표시한다. mock adapter 검사는 분기 검사용이며 실제 GPU 실행 증거로 인정하지 않았다.
- 검증 중 CAD Face 광원의 발광 앞뒤를 reverse-facing 교차 법선으로 판정하던 누락을 발견했다. 실제 CAD winding 법선으로 수정하고 회귀 테스트를 추가했다.

## 사전 선언한 통과 조건

- 정수 수와 반사 횟수 exact, 실수 좌표 상대 1e-9/절대 1e-12.
- 양성 장면: 해당 직광 또는 반사 경로 30 이상. 음성 장면: 검출 0. ALLOWED: 제외 30 이상·남은 검출 0.
- 완료 및 시도/계획 수 일치, 각도별/셀별 수 합계 일치.
- GPU: production preflight 필수 조건 + 역추적 CUDA 실제 성공 >0 + gpu_active/gpu_mixed. CPU fallback만으로 통과 금지.
- CPU/GPU 각각 첫 실행+warm 2회. 시간 범위는 BVH 준비 후 역추적 전체이며 CAD import/BVH 구축/광원 추적/UI 시간을 포함하지 않는다.

## 합성 장면 결과

CPU scalar와 CPU batch 첫 실행+warm 2회에서 17개 장면 모두 일치 및 기능 조건 통과.

| 장면 | 직광 경로 | 반사 경로 | 제외 | CPU 결과 |
|---|---:|---:|---:|---|
| rear_0 | 3656 | 0 | 0 | 통과 |
| rear_45 | 2850 | 0 | 0 | 통과 |
| rear_85 | 2199 | 0 | 0 | 통과 |
| rear_89_9 | 2048 | 0 | 0 | 통과 |
| vertical_89_9 | 2048 | 0 | 0 | 통과 |
| compound_neg85_80 | 2058 | 0 | 0 | 통과 |
| face_emitter | 3656 | 0 | 0 | 통과 |
| thin_emitter_extreme | 2048 | 0 | 0 | 통과 |
| narrow_hole | 55 | 0 | 0 | 통과 |
| mirror_once | 0 | 3656 | 0 | 통과 |
| mirror_twice | 0 | 218 | 0 | 통과 |
| diffuse | 0 | 4096 | 0 | 통과 |
| bsdf | 0 | 4096 | 0 | 통과 |
| blocked | 0 | 0 | 0 | 통과 |
| allowed | 0 | 0 | 3656 | 통과 |
| source_backside | 0 | 0 | 0 | 통과 |
| bsdf_zero_rt | 0 | 0 | 0 | 통과 |

- 해상도 32×32, 셀 반복 2, 각 장면 단일 관찰각. 격자와 광원 투영 표본을 합쳐 장면당 4,096개 경로를 탐색했다.
- 경로 수는 표본 수이며 lm/nit나 사람이 볼 수 있는 밝기 판정이 아니다.

## 실제 GPU 결과와 남은 검증

- GPU 장치명 없음, CUDA 성공/시도 측정 및 첫/warm 실행 시간 없음: preflight에서 중단하여 GPU 장면을 실행하지 않았다.
- 원시 결과: `cpu_validation_passed=true`, `gpu_validation_status=not_verified_preflight_failed`, 전체 `passed=false`.
- GPU 속도 향상과 실제 장치 정합을 통과로 보고하지 않는다. GPU가 있는 PC에서 아래 명령으로 이어서 검사한다.
- 실물 TV의 조립 틈·재질·광원·관찰 조건으로 재현 여부를 확인하는 작업도 남아 있다.

```powershell
.\.venv-gpu\Scripts\python.exe scripts\verify_observer_gpu_accuracy.py --json-out artifacts\observer-gpu-report.json
```

## 증거 파일

- `artifacts/observer-gpu-validation-20261029.json`: 수정 후 전체 검증, 입력 코드 SHA-256, CPU 첫/warm 시간, preflight 원인.
- `artifacts/observer-gpu-validation-20261029-initial-failure.json`: Face 광원 누락 수정 전 실패 기록. 통과 조건을 낮추지 않았다.
- `tests/test_observer_probe.py`: 차폐/극각/다중 반사/BSDF/Allowed/Face 방향/CPU 정합/배치 adapter/실패 및 혼합 실행/false GPU pass 방지.

커밋·푸시 및 배포는 수행하지 않았다.
