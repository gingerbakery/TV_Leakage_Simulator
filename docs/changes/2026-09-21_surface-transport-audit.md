# RT 정밀 검증 1차 실행 — 면 속성, 반사 광량, 산란, 종료 정책

## 요약

전체 내부 검증 완료나 LT 정합 완료를 의미하지 않는다. 기존 main
`e887d68c1b90af7534a8926c7e9c8209c17a34a0`와 미커밋 통계 수정 위에서 실행했다.
회사 CAD/LT 자료 없이 합성 geometry로 수치 계산을 분리 검증했다.

- 제품 오류 2건 수정: ROI/제외 시 면 속성 오연결, 반사율 0%의 반사 가능 광량 오기록.
- 면 속성 전달·반사 광량: CPU/GPU 250조건 통과.
- 독립 산란 분포: CPU scalar/batch/GPU 126조건 통과.
- 최소 에너지 종료: 32조건의 정책 동작 확인. 이 중 6조건은 정확한 무절단 광량과 불일치.
- 좌표 변환/BVH 대조: 24조건 통과.
- 관련 backend 회귀 198개, frontend 회귀 358개 통과. TypeScript 검사 통과.
- 서버와 브라우저 세션은 재시작하지 않았다. 커밋/push도 하지 않았다.

## 1. 실제 수정한 오류

### ROI / 부품 제외 후 면 속성 연결

API는 Viewer의 면 선택을 정밀 mesh의 원본 삼각형 번호로 확장한다. 그 다음
ROI/제외 처리에서 광원 geometry 참조는 축약 mesh 번호로 바꿔야 하지만,
optical assignment는 resolver가 찾는 `source_face_index`를 유지해야 한다.
둘 다 축약 번호로 바꾸던 것이 원인이다.

입력 1 lm, override 반사율 90%, 기본값 10%인 단일 경면의 독립 재현:

| 조건 | 수정 전 수광량 | 수정 후 수광량 | 기대값 |
|---|---:|---:|---:|
| 전체 형상 | 0.9 lm | 0.9 lm | 0.9 lm |
| ROI로 대상 면만 유지 | 0.1 lm | 0.9 lm | 0.9 lm |
| 광선과 무관한 부품 제외 | 0.1 lm | 0.9 lm | 0.9 lm |

누락뿐 아니라 인접 면에 다른 override가 잘못 붙는 경우도 재현 후 수정했다.
면광원 번호 변환은 유지했다. 변환/캐시 재사용/속성 재편집, Viewer 1면→정밀
mesh 3삼각형 확장, `.bitsam` 파일 복원 후 동일 grid와 재해석을 검사했다.
파일 검증의 원본 STEP은 fixture이며 실제 CAD 파서 전체의 검증은 아니다.

### 0% 반사율의 요약 광량

기본 반사율 0%에서는 실제 산란을 금지하지만 grazing 보정은 반사율을 올려
`potential_reflected_flux_lumen`에 반사 가능 광량을 기록했다. 1 lm, 89° 입사,
roughness=0에서 CPU/GPU 모두 약 0.881403 lm으로 잘못 기록했으며 실제 수광은
0 lm이었다. 따라서 수광 에너지가 생성된 오류가 아니라 집계 의미의 불일치다.

반사율 0은 기존의 완전 비반사 계약을 따르도록 Python, native CPU 두 경로와
CUDA의 유효 반사율 계산을 통일했다. 양수 반사율의 각도 보정식은 변경하지
않았다. 변경 전 실패 로그와 변경 후 실제 CUDA 집계 결과를 보존했다.

## 2. 면 속성 및 광량 검증

UI catalog의 모든 호환 Base Material/Surface Property 조합 58개와 사용자
60% 설정 1개를 실제 `buildRayTraceRequest`로 직렬화했다. 그 payload를 Python
resolver와 CPU/GPU 실제 반사까지 연결했다. 사용자 화면 클릭 전체를 자동화한
검사가 아니라 catalog/저장 설정/요청 생성/계산의 연결 검사다.

- PC Black: Matte 5.76%, Normal 8%, High-gloss 10.8%.
- Matte는 Lambertian, Normal/High-gloss는 Mixed이며 이름만으로 경면이라고 가정하지 않는다.
- Polished mirror는 85% Specular. 기본 재질의 표면 마감 및 사용자 override도 확인했다.
- preset 수치가 전달된다는 검증이지 실제 소재의 측정 반사율임을 인증하지 않는다.

반사 방향 전체를 받는 5개 수광면으로 입사 1 lm의 출사 반구를 포획했다.
작은 Receiver의 hit 비율을 반사율로 오해하지 않도록 설계했다. 각도 보정 OFF,
최소 에너지 0, MIS/roulette OFF에서 다음 조건을 확인했다.

| 검증 | 독립 기준 | 결과 |
|---|---|---|
| Specular/Lambertian/Gaussian/Mixed × 0/3/14/60/95/99.9/100% | 전체 수광량 = 입력 lm × 반사율 | 통과 |
| 정확히 2회 반사 | 60% × 60% = 0.36 lm; 60% × 30% = 0.18 lm | 통과 |
| 광원 power 0/0.1/1/2 lm | 출력 비례, 0 입력은 0 출력 | 통과 |
| 광원 1 lm + 2 lm 합산 | 60% 반사 후 1.8 lm | 통과 |
| 각도 보정 ON, 0/60/89°, roughness 0/1 | 명시된 경험식; 0%는 비반사 예외 | 통과 |

각도 보정 ON에서는 입력 60%가 모든 입사각에서 60%라는 뜻이 아니다. 실제
Black PC의 BSDF 적합성은 별도이며, 이 실험은 구현된 식의 계산을 검증한다.

## 3. 산란 방향의 독립 기준

경면축과 입사각을 정확히 고정하는 Aim Sphere의 0/0° 평행광을 사용했다.
각 조건 8,192 Ray × seed 42/123/20260921, CPU scalar/batch와 실제 GPU를
검사했다. 모든 경로를 저장해 표시용 Ray 부분 표본과 구분했다.

- Specular: 정면/60/89°에서 반사 법칙과 단위 벡터.
- Lambertian: `CDF(cos θ)=cos² θ`, 방위각 균일성. 별도 단위 검사에서는 평균 cosine 2/3도 확인.
- Gaussian: sigma 3/12/30° × 입사 0/60/89°. Rayleigh 극각 표본의 표면 아래
  방향 제거를 독립 65,536점 적분으로 정규화하고 32회 재시도 fallback 질량도 포함.
- Mixed: Gaussian/Lambertian 선택 비율 35/65%. 별도 단위 검사에서 0/25/60/100%도 확인.

실행 전 고정한 CDF 허용값은 약 0.029064이다. 512회 비교와 family bound
0.001을 둔 DKW 기준이며 실제 최대 오차는 약 0.024534였다. 경면 방향은
저장 좌표의 epsilon 영향을 고려해 절대 1e-5 기준을 별도로 사용했다.
같은 이름의 LT Gaussian과 분포가 동일하다는 인증은 아니다. 극단적으로 큰
sigma의 fallback 집중, 복잡 곡면의 다중 산란은 추가 검증이 필요하다.

초기 실험 도구에서 Aim Sphere의 방향을 area용 u/v 축으로 입력한 오류가
있었다. 해당 실행은 검증 증거에서 제외해 별도 보존했다. sphere beta 입력으로
수정하고 실제 발광 방향을 먼저 assert한 뒤 전체 조건을 다시 실행했다.

## 4. 중요 잔여 위험 — 최소 에너지에 의한 광량 손실

현재 `min_energy=1e-9`는 전체 입력에 대한 상대 비율이 아니라 **Ray 하나의 lm**
이다. 같은 입력 광속에서 Ray 수를 늘리면 한 Ray가 약해져 더 빨리 잘릴 수 있다.

입력 0.00001 lm, 반사율 60%인 경면에서 정확히 2회 반사하는 동일 구조:

| Ray 수 | 무절단 기대 수광량 | 기본 임계값의 CPU 결과 | 기본 임계값의 GPU 결과 |
|---|---:|---:|---:|
| 2,048 | 0.0000036 lm | 0.0000036 lm | 0.0000036 lm |
| 4,096 | 0.0000036 lm | 0 lm | 0 lm |

공통 종료 정책의 절단 편향이며 GPU 고장이나 표본 노이즈가 아니다. 실제 TV
모델에서도 같은 크기의 손실이 발생했다고 단정하지는 않는다. 총 입력 lm,
Ray 수, 실제 누적 반사율에 따라 달라진다.

- 32조건 중 정책 자체는 모두 예상대로 동작했지만, 6조건은 무절단 기준에 실패했다.
- `min_energy=0` 조건은 이론값을 유지했다. 별도로 반사 상한 손실은 남을 수 있다.
- Russian roulette는 3 seed 예비 실험에서 6 표준오차 범위에 들었으나,
  개별 결과의 상대 오차는 최대 약 6.67%였다. 이것만으로 5% 정확도를 인증하지 않는다.
- 기존 설정 파일과 결과 의미가 바뀌므로 임계값 단위/기본 정책을 몰래 바꾸지 않았다.

다음 Phase C 작업은 종료된 잔여 광속을 집계하고, 입력/N/segment에 의존하지
않는 종료 제어 및 표시를 설계·검증하는 것이다. LT 기준 비교를 할 때는 우선
최소 에너지 0으로 절단 편향을 분리하고 충분한 반사 상한을 사용한다.

## 5. 다른 단계의 확인 범위

- D: 크기 0.01/1/100배, 평행 이동 0/1,000,000 mm, 회전 유무, CPU BVH/전수검사
  조합 24조건에서 128 Ray가 모두 정확히 2회 반사하고 0.18 lm에 도달했다.
- E: 이전 복잡 cavity의 CPU/GPU 엄격 경로 불일치 원인은 여전히 미해결이다.
  이번 간단한 표면 검증 통과로 이를 해결했다고 주장하지 않는다.
- F/G/H: 기존 다회반사/1,000회 상한, MIS, GPU 집계/fallback, 수광각/오차 통계,
  Aim Area/Sphere 회귀를 새 코드에서 다시 실행했다. 복합 조건 전수 sweep은 아니다.
- I: 캐시/정밀 mesh/프로젝트 파일 복원 후 면 속성 재편집을 확인했다. 저장된 과거
  해석 결과는 자동 재계산하지 않으므로 이번 ROI 오류가 영향을 준 결과는 다시 해석해야 한다.

기존 GPU accumulator 회귀에서 2개 subtest가 계산값이 아니라
`config.compute_backend`의 cpu/gpu 문자열 차이만으로 실패했다. 입력 장치는
별도 assert하고 비교 payload에서만 정규화했다. 광선 개수/경로/수치의 허용
오차는 변경하지 않았으며 최종 198개 회귀는 skip 없이 통과했다.

## 6. 증거와 재실행

`outputs/rt_accuracy_audit_2026-09-21/`의 JSON에는 조건별 물성, 광량, 시간,
실행 경로, CUDA 성공/시도, hybrid/fallback, emitter 종류를 보관했다.
source checkout의 기존 Python 3.13.3 / NumPy 2.4.6 / Numba 0.66.0 /
llvmlite 0.48.0, NVIDIA RTX 3070 / compute capability 8.6에서 실행했다.

세 실행 가이드를 읽고, 기존에 준비된 런타임으로 공식
`scripts/verify_gpu_cuda_runtime.py --mode device`를 실행했다. 일반 실행 진입점은
`run_web_gpu.bat`이며 이번에는 서버를 시작하거나 환경을 재설치하지 않았다.
preflight의 available/strict_float64/kernel_executed/kernel_verified가 모두 true,
scope=`production_ray_bvh`, contract=`strict_float64_bvh_v1`임을 확인했다.

실제 계산의 `gpu_active`와 양수 CUDA 성공 횟수를 JSON으로 확인했다. geometry
선행 캐시를 공유한 첫 실행+warm 2회 시간도 transport 기록에 있다. 일부 검사
프로세스는 병렬 실행되어 **성능 benchmark나 대형 TV 속도 예측으로 쓰지 않는다**.

| 새 전용 실험의 GPU 증거 | 결과 |
|---|---:|
| 실제 GPU 실행 | 183건, 모두 `gpu_active` |
| CUDA 시도 / 성공 batch | 226 / 226 |
| hybrid CPU 성공 batch | 0 |
| 교차 / resident fallback | 0 / 0 |

동일 capture scene 4,096 Ray의 첫 실행/warm 1/warm 2는 CPU
6.4492/0.0705/0.0711초, GPU 3.9131/0.0328/0.0360초였다. 첫 실행에는 JIT가
포함되고, 이 수치를 1억 Ray 실행 시간으로 선형 외삽하지 않는다.
광량 실험의 최대 절대 오차는 약 5.18e-14 lm으로 사전 기준 1e-10 lm 이내였다.
`summary.json`, `execution_evidence.csv`, `audit_overview.png`도 함께 생성했다.

재실행 도구:

- `frontend/src/features/raytracing/optical-payload-audit.test.ts`: 전체 호환 catalog
  및 요청 전달. `BITSAM_OPTICAL_AUDIT_PAYLOAD` 환경변수로 JSON 저장 경로를 지정.
- `scripts/verify_surface_transport_audit.py --output <결과 폴더> --catalog <payload JSON>`
- `scripts/verify_surface_angular_audit.py --output <angular.json>`
- `scripts/verify_termination_audit.py --output <termination.json>`
- 새 회귀: `test_optical_transport_audit`, `test_surface_distribution_audit`,
  `test_optical_portable_audit`, `test_geometry_optical_audit`.

전체 A~I 종료가 아니라, 확인된 오류를 수정하고 다음 위험을 재현한 중간 결과다.
