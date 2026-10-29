# Preview 유출 방향 집계

Preview의 `Detection Direction > 유출 방향 · Source sampling`은 최종 유출광을 선택 축 기준으로 집계한다. 후면은 `후면 -Z`를 선택한다. 새 Workspace는 이 방식이 기본이며, 이전 프로젝트에 방식 정보가 없으면 기존 평면 검출을 유지한다.

## 집계 기준

- 광선이 더 이상 기구물과 교차하지 않는 최종 구간에서만 집계한다. 중간 경계 통과나 재진입을 중복 집계하지 않는다.
- 모델 전체의 변환된 경계 상자에서 최종 출구 위치를 계산한다. 이 위치는 실제 홀 표면 위치와 다를 수 있다. 경계 상자를 통과하지 않는 외부 광원 경로는 제외한다.
- 선택 축과 광선 진행 방향 사이의 각도를 `0–30`, `30–60`, `60–80`, `80–90°`로 나눈다. 각 구간은 아래 경계 포함·위 경계 제외이며 마지막 구간만 90°를 포함한다. 부동소수점 각도 경계는 1e-10도 단위로 정리한다.
- 90° 접선 광선도 집계한다. 실제로 시뮬레이션한 광선 중 에너지·반사 횟수 제한으로 종료된 경로는 최종 유출로 간주하지 않는다.
- ALLOWED AREA는 개별 최종 출구에 적용한 후 집계한다. 기존 평면의 투영 영역 의미와 5 mm 가장자리 여유를 유지한다. 여러 유출 경로가 같은 칸에 들어가도 대표 위치 평균으로 제외 여부를 판단하지 않는다.
- 표의 광량은 전체 시뮬레이션 광선으로 집계하며 저장 경로 4,000개 제한에 의존하지 않는다. 후보 강조·순위에는 기존 상대 강도 임계값과 후보 수 제한이 적용된다. 관찰자 위치의 nit 계산은 아니다.

## CPU와 GPU

CPU와 GPU는 동일한 Source sampling 및 counter-RNG 배치 경로를 사용한다. GPU 선택 시 CUDA BVH가 교차 판정을 수행하고, 반사 진행·유출 방향 및 광량 누적은 공통 CPU 코드가 수행한다. GPU 전체 계산이나 속도 향상을 검증 없이 주장하지 않는다. 실제 실행 상태는 Preview 결과의 Compute 항목에서 확인한다. CUDA 성공 batch가 0이면 GPU 실행 성공이 아니다.

CPU scalar 기준, CPU 배치의 광량 보존·차폐·극각·BSDF 경로는 단위 테스트에서 점검한다. 실제 CUDA 정합은 GPU가 있는 PC에서 별도 검증해야 한다.

## 실제 CUDA 비교 검증

Git source는 `run_web_gpu.bat` / `run_web_gpu.ps1`으로 준비한 `.venv-gpu`를 사용한다. 필요한 GPU·드라이버·Toolkit 기준과 설치 승인 규칙은 `ai-gpu-execution-runbook.md`, `gpu-cuda-user-guide.md`, `WINDOWS_GPU_SETUP.md`를 따른다. 이 스크립트는 설치·서버 재시작·시스템 설정 변경을 하지 않는다. 다른 CPU/GPU 성능 측정이나 Ray Tracing과 동시에 실행하지 않는다.

```powershell
.\.venv-gpu\Scripts\python.exe scripts\verify_escape_direction_accuracy.py --rays 16384 --json-out artifacts\escape-direction-gpu-report.json
```

스크립트는 production FP64 Ray/BVH preflight의 available/strict_float64/kernel_executed/kernel_verified와 scope/contract를 확인한다. 하나라도 통과하지 않으면 계산을 시작하지 않고 실패 결과를 기록한다.

동일 장면·seed로 CPU와 GPU의 0°·45°·89.9°·반사·Face emitter·BSDF 반사 사례를 비교한다. GPU는 같은 장면 객체와 프로세스에서 첫 실행과 warm 2회를 기록한다. 각도별 수, 전체 수, 셀 위치·광량 등 전체 집계를 대조한다. 정수는 exact, 실수는 상대 1e-9/절대 1e-12 허용 오차를 적용한다. 각 실행의 CUDA 성공 수가 0이거나 유효 표본이 30 미만이면 통과하지 않는다. GPU 실행 증명과 물리 정확도·실측 보정은 별개다.

## 저장 호환성

이 방식의 `.bitsam`은 `bitsam-project.v1-escape1`로 저장한다. 이전 빌드가 방향 집계를 평면 검출로 조용히 바꾸지 않도록 별도 schema를 사용한다. frontend와 backend를 함께 갱신해야 한다. Git pull은 이미 추출한 ZIP/EXE를 갱신하지 않는다.
