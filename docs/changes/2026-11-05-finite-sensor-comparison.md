# 동일 센서 순방향·역추적 광량 비교

미커밋 소스 변경. GPU 실기기 검증 미완료. 실제 TV 교정·과검출/미검출 해결을 증명한 결과가 아니다.

## 두 검증의 차이

- 대표 경로 검사: 저장 경로 하나의 광원·반사·차폐·Receiver 도달 여부를 확인한다. 기하 검사는 독립 CPU BVH를 사용한다. 표본 hit를 주입하지 않는다.
- 광량 비교: 같은 유한 Receiver의 면적·normal/flip·수용각·ROI에서 순방향과 역추적이 추정하는 **광량 lm**을 비교한다. 평행 관찰 Preview의 nit를 lm과 직접 비교하지 않는다.

## 실행과 판정

Preview 관찰 후보 → `광량 비교` → `비교 시작`. 독립 반복 4/8/16, 반복당 F/R 표본, 허용 차이를 설정한다. 기본값 8회·F 16,384·R 1,024·5%. 한 작업만 실행하며 진행/중지/오류가 표시된다. 창을 닫아도 작업은 유지되고 재개방하면 최근 작업이 표시된다. 새 비교 작업의 중단 후 이어 실행은 지원하지 않는다. 기존 일반 해석/Preview의 일시 중단 세션은 삭제하지 않는다.

입력은 시작 시 JSON 복사본으로 고정하며 물리 입력·실제 삼각형/trace mask·면별 재질 경로 및 소스 코드의 SHA-256을 기록한다. 기존 프로젝트 광원·재질·Block·ROI·최대 반사는 보존한다. 검증 복사본에서만 GPU/BVH, source proposals, min_energy=0, roulette, 자동 수렴 꺼짐, 경로 저장 꺼짐을 적용한다. 종료 정책 차이를 보기 위해 첫 F seed·같은 검증 예산으로 기존 min_energy/termination을 한 번 추가 실행한다. 이 보조 결과는 원래 100만 Ray 생산 실행의 재현이 아니며 동등성 통계에 포함하지 않는다.

F/R은 서로 다른 salted seed를 사용한다. 역추적 카메라는 Receiver 면적 및 수용 원뿔에 균일 표본을 생성하고 `A[m²]·Ω·cosθ` 가중치를 적용한다. ROI 밖 표본은 0이며 적분 분모에 포함된다. 표본의 광학 경로·BRDF/광원 연결·MIS·roulette는 CUDA resident 경로에서 계산한다. 유한 센서 카메라 제안과 ROI 마스크 생성·통계 집계·독립 첫 chunk 검사 등은 호스트 작업이다. 전체 UI/CAD/BVH 빌드가 GPU화된 것은 아니다.

반복별 F/R 평균의 차이에 보수적 근사 t95 구간을 계산한다. 전체 차이 구간이 ±허용 차이 안이면 `일치`, 완전히 밖이면 `불일치`, 나머지는 `미확정`. 0 hit, 유효 표본 30 미만, 검출 반복 부족, 중지/미완료는 일치로 처리하지 않는다. 구간 중첩만으로 통과하지 않는다. 4~16 반복의 t 근사는 formal coverage 보장이 없으며 희귀 경로의 미관측 꼬리를 배제하지 못한다. 실물 보정은 별도다.

## GPU 계약

API/UI 비교는 NVIDIA CUDA를 요청한다. private CPU 경로는 단위 테스트 수식 oracle용이며 API 선택지로 노출하지 않는다. 시작 시 production preflight의 available/strict_float64/kernel_executed/kernel_verified 및 scope/provider 계약을 모두 요구한다. 양쪽 **각 반복**에 `gpu_active` 또는 `gpu_mixed`, CUDA 성공 >0을 요구한다. 미충족은 미확정이며 GPU 성공으로 표시하지 않는다. CPU hybrid/대체·fallback 이유·시간·장치·샘플 수를 JSON에 남긴다. CPU 보조는 혼합 실행으로 표시한다. 2026-11-06 보완: 측정 BSDF는 기존 CPU 광학 평가와 CUDA BVH 교차 검사의 혼합 경로를 사용한다. 각 반복의 실제 CUDA 성공 증거는 계속 요구하며 CPU-only는 미확정이다. 그 외 resident 준비·CUDA 실패는 명시적 오류로 중단한다. 다른 BRDF로 근사하지 않는다.

정량 비교는 현 반사 전용 transport 범위이며 자동 aperture 귀속·실물 휘도 교정·guided estimator를 추가한 것이 아니다. 외관 개구부 미확정 표시는 유지한다.

## 구현 API

- POST `/api/raytrace/compare-sensor/start`: `{request, options}`.
- GET `/api/raytrace/compare-sensor/status?job_id=...`: 생략 시 최근 작업.
- POST `/api/raytrace/compare-sensor/stop?job_id=...`: cooperative 중지, 미완료 반복 제외.
- 결과 JSON: 공통 센서, 입력/geometry/code SHA, seed, 반복별 flux/2차 모멘트/hit/유효 표본, 오차 구간, 기존 종료 조건 probe, F/R CUDA 증거.

## 확인한 것과 남은 것

작업 환경: Git source main@db25ced + 미커밋 변경. 읽기 전용 `setup_windows_gpu.bat` 결과 NVIDIA GPU 없음, driver 없음, Toolkit 없음. Python 3.13.14 x64·Node 24.18.0/npm 11.16.0 확인. 설치·서버 재시작은 수행하지 않았다.

CPU 테스트로 Uniform/Lambertian 직광 해석해, specular ρ배 해석해, 완전 차폐 0→미확정, 독립 seed, 설정 불변, 종료/경로 검사, GPU preflight 미충족 시 계산 차단, CUDA 커널의 유한 센서 카메라 수식 oracle, API 시작/조회/중지/작업 충돌을 확인했다. GPU 커널의 CPU 수식 실행은 실제 GPU 컴파일·실행·속도 증거가 아니다.

관련 backend 119개 + 동일 ROI 절반 센서 적분 추가 1개 통과(총 120개), frontend 9개 파일 141개 통과, production build 통과. 기존 jsdom canvas 미지원 메시지와 번들 크기 경고는 남아 있다. 기본 화면의 설명은 닫힌 `(?)`에 두고, 모달 자동 포커스로 도움말이 저절로 열리지 않도록 확인했다.

GPU PC의 source 실행은 `run_web_gpu.bat`을 사용한다. production preflight 통과 후 같은 환경에서:

```powershell
.\.venv-gpu\Scripts\python.exe scripts\verify_sensor_gpu_comparison.py --output sensor-gpu-validation.json
```

이 gate는 같은 장면의 첫 실행과 warm 2회, Uniform/Lambertian/mirror/blocked 해석해 및 GPU 실행 증거를 기록한다. 실패 gate를 완화하지 않는다. 현재 PC에서는 실기기 gate를 실행하지 않았고 가속 배수도 측정하지 않았다. 실제 사용자 도면의 0 Ray 원인, BSDF GPU 확대, 실물 TV 가시/비가시 검증은 남아 있다.
