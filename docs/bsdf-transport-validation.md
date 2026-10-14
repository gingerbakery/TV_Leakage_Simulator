# BSDF 실제 연산과 LT 비교 절차

2026-10-14 구현 상태. 최초 단계는 파일 등록·저장만 가능했으나,
현재는 **표에서 반사 방향을 뽑고 RT 반사율을 적용하는 CPU·NVIDIA CUDA GPU 광선 추적**을 지원한다.
단일 반사, 다중 반사, 실제 API 요청, 저장된 원본 재해석을 검증했다.
**독립 수치 적분과의 검증은 통과했지만, 실제 LightTools 결과와의 직접 비교는 아직 대기 상태다.**

## 1. 사용할 수 있는 범위

| 항목 | 현재 동작 |
| --- | --- |
| 입력 | LT ASCII master + Polar 2D 상대 BSDF 산란 표 + 별도 RT 파일 |
| 반사 | 지원. RT의 R이 실제 반사 광량을 결정 |
| 투과 | 미지원. T가 있는 파일을 반사 전용으로 임의 변환하지 않음 |
| 계산 모델 | 등방성, No Shift, 공기 중 계산 |
| 방향 범위 | 법선 기준 0–90°, 방위각 0–360°의 전체 반구 |
| 입사각 | 표 사이 보간. 측정각 범위 밖은 가장 가까운 끝값 |
| 파장 | 자산별 선택 파장에서 보간. 광선별 분광 추적이나 스펙트럼 적분은 아님 |
| 면 방향 | 입사측 법선으로 정렬하고 양쪽에 동일한 표 적용 |
| 실행 | CPU 및 NVIDIA CUDA GPU, primary/bounce 모두 source sampling |
| 보존 | 원본 파일, 연결, 모델, 파장을 .bitsam에 포함 |

단일 네이티브 .bsdf, Cartesian/CCBSDF, 비공기 매질, 이방성, LT Back 데이터,
Shift Invariant/Shift with Stretch, 별도 이상적 정반사 스파이크는 아직 지원하지 않는다.
부분 각도 표는 보관만 가능하다. 미지원 조건, 누락 파일, 변경된 원본 해시,
계산 파장 범위 초과는 오류로 중단하며 기존 Mixed/Gaussian으로 바꾸어 계산하지 않는다.
BSDF가 연결된 해석의 receiver MIS 및 간이 Preview 계산은 현재 차단한다.
GPU 지원 범위와 실제 CUDA 검증은 [GPU 검증 문서](bsdf-gpu-validation.md)에 정리했다.

이는 일반적인 실측 BSDF 파일 전체에 대한 LT 호환 선언이 아니다.
특히 LT의 Front/Back 매질 경계와 이 구현의 양면 동일 표 가정은 구별해야 한다.

## 2. UI에서 사용하는 순서

1. Material Assignment 또는 Surface Property를 열고 **LT ASCII 파일 등록**에서
   master, 모든 산란 표, RT 파일을 함께 선택한다. 파일 자체는 서로 분리되어 있다.
2. **연결할 BSDF** 목록에서 등록한 자산을 선택한다.
3. **등방성 · No Shift로 계산**을 켜고 **계산 파장 (nm)**을 지정한다.
   이 두 옵션은 자산에 저장되므로 같은 파일을 쓰는 부품·면에 함께 적용된다.
4. 기존 Apply/Save 버튼으로 물성을 연결한다.
5. Ray Tracing에서 CPU 또는 NVIDIA CUDA GPU, source sampling으로 실행한다.
   GPU는 소스 체크아웃에서 `run_web_gpu.bat`으로 시작하고 CUDA 사전 점검을 통과해야 한다.
   결과에 BSDF 적용 정보와 BSDF 반사 기여가 표시된다.

BSDF 연결 시 기존 Reflectance, Specular/Diffuse 비율, Gaussian 각도,
grazing 반사율 보정은 이 표에 중복 적용되지 않는다.
Optical preview의 기존 도식도 BSDF 표를 시각화한 것이 아니다.
연결을 해제하면 기존 해석 물성으로 돌아간다.

## 3. 무엇을 계산하는가

상대 BRDF 표를 s, 출사각을 θ, 출사 입체각을 dΩ라 하면:

```text
Z = ∫반구 s(θ, φ) cosθ dΩ
방향 확률밀도 p(θ, φ) = s(θ, φ) cosθ / Z
실제 BRDF fr(θ, φ) = R_RT × s(θ, φ) / Z
반사 광선 광속 = 입사 광선 광속 × R_RT
```

표는 빛이 어느 방향으로 퍼지는지, RT는 빛이 얼마나 남는지를 정한다.
예를 들어 1 lm가 닿고 RT 반사율이 30%면 반사되는 총량은 0.3 lm다.
표 전체 숫자를 두 배로 해도 정규화 후 방향과 반사 총량은 동일하다.
표가 모든 방향에서 일정하면 cosθ 가중에 의해 Lambertian 반사가 된다.

각 격자 안에서는 θ·φ bilinear 보간을 하고 cosθ sinθ dθ dφ를 적분해
그 격자의 선택 확률을 구한다. 선택된 격자 안에서도 같은 분포로 방향을 추출한다.
반사 방향을 몇 번 뽑다가 실패하면 정반사로 바꾸는 편의 처리는 없다.

입사각/파장이 중간값이면 인접 표를 **각각 정규화한 뒤 가중 혼합**한다.
RT의 R은 별도로 선형 보간한다. No Shift에서는 분포를 정반사 방향에 맞춰
추가로 이동·늘리지 않는다. 이 보간은 명시된 시뮬레이터 계산 계약이며,
LT의 내부 세부 보간과 동일하다고 아직 확인한 것은 아니다.
그래서 측정각 0/30/60/80°와 중간각 45°를 따로 비교한다.

사용하는 난수는 동일 입력·동일 seed·동일 실행 경로에서 재현된다.
seed를 바꾸면 유한한 광선 수 때문에 bin별 값이 달라질 수 있다.
Sobol을 사용하는 LT와 개별 광선이 같을 필요는 없으며,
충분한 광선 수에서 총량과 분포가 같은 값으로 수렴하는지 확인한다.

## 4. 완료한 수치 검증

자체 합성 표 두 종류를 사용했다. 실제 소재 측정값이나 LT 설치 예제의
측정값을 복사한 데이터가 아니다.

- Lambertian: 상대 BRDF가 모든 방향에서 1.
- 방향성 표: 정반사 축 방향 단위벡터 m과 출사 방향 ω에 대해
  s = 0.02 + exp[25(m·ω − 1)]. 2.5° × 5° 간격으로 표 작성.

표의 AOI는 0/30/60/80°, 파장은 550 nm, RT 반사율은 모두 30%다.
GPU 지원 이전의 독립 CPU 기준 경로에서 각 조건 100,000 rays를 추적한 기록이다.
현재 기본 배치 경로는 CPU와 GPU가 같은 counter RNG를 사용하므로 이때와 난수열은 다르다.
현재 배치 경로의 결과는 [GPU 검증 문서](bsdf-gpu-validation.md)를 참조한다.
기준값은 production 정규화/CDF 코드를 사용하지 않고 SciPy 보간,
반구 midpoint 적분, 수광면 Gauss 면적 적분으로 별도 계산했다.
적분 해상도를 두 배로 높여 적분 자체의 수렴도 확인했다.

| 조건 | 수광량 편차 | 정규화 분포 겹침 | 상관계수 |
| --- | ---: | ---: | ---: |
| Lambertian 0° | +0.1250% | 98.5087% | 0.99984 |
| 방향성 0° | +0.0177% | 99.0583% | 0.99996 |
| 방향성 30° | +0.0443% | 98.8949% | 0.99995 |
| 방향성 60° | +0.0119% | 98.7557% | 0.99992 |
| 방향성 80° | +0.0295% | 98.3373% | 0.99970 |
| 방향성 45° 보간 | +0.0181% | 98.6078% | 0.99991 |

사전 판정 기준은 수광량 오차 ≤1%, 분포 겹침 ≥95%, 상관계수 ≥0.99였다.
여섯 조건 모두 통과했다. 반사 총량은 0.3 lm이며 부동소수점 누적 오차는
2×10⁻¹³ lm 수준이었다. 기준 적분 해상도 변경의 최대 L1 차이는 0.0088%였다.

별도 검사에서는 0%/100% 반사율, 파장/RT 보간, 회전 좌표,
다중 반사 0.3 → 0.09 → 0.027 lm의 에너지 상한, API의 seed 재현,
원본 위변조 거부와 저장 복원을 확인했다.
실제 Chrome 화면에서도 STEP을 포함한 60° .bitsam을 열어 실행한 뒤
Save의 다운로드 경로로 저장하고, 화면을 새로 열어 복원·재실행했다.
전후 400개 bin의 광속이 정확히 같았고 총 수광량은 0.294207 lm였다.
위 표의 분포 겹침은 **LT 매칭률이 아니라 독립 적분과의 비교**다.
실제 소재의 물리적 정확도나 상호성까지 증명하는 시험은 아니다.

## 5. 다음 단계: 회사 LT에서 동일한 교정 시험

시험 묶음에는 test-plane.step, 여섯 .bitsam, lambertian/ 및 directional/
폴더의 master·산란 표·rt.txt, 시뮬레이터 결과 CSV가 있다.
.bitsam은 이번 BSDF 지원 빌드에서 열어야 한다.

우선 **Lambertian 0° 한 조건**을 완료한 뒤 방향성 60°로 확장한다.
두 조건이 맞으면 나머지 측정각과 중간각 45°를 비교한다.

1. LT에서 test-plane.step을 가져온다. 반사면은 z=0, 법선 +Z, x/y ±100 mm다.
   한 번 반사한 광선만 비교한다.
2. Measured BSDF로 해당 폴더의 master.txt와 rt.txt를 각각 지정한다.
   **No Shift**, **Anisotropic OFF**, 반사만, 550 nm로 설정한다.
   LT의 기본 Shift with Stretch를 그대로 두면 다른 모델을 비교하게 된다.
3. 광원을 총 1 lm의 collimated beam으로 둔다. 중심은
   (-tanα, 0, 1) mm, 진행 방향은 (sinα, 0, -cosα)다.
   광원 크기는 10⁻⁶ × 10⁻⁶ mm다. 해당 크기를 LT에서 허용하지 않으면
   동등한 점 collimated source를 쓰고 변경 사항을 기록한다.
4. 리시버는 중심 (0,0,2) mm, 법선 -Z, 크기 20×20 mm, 20×20 bins.
   표의 local U는 +X, V는 -Y다. 전체 입사 반구를 수광한다.
5. **Illuminance Mesh의 Unsmoothened Incident Flux**를 비교한다.
   Spatial Luminance의 제한된 시야각/휘도 값을 대신 사용하지 않는다.
   Smoothing을 끄고 bin별 값을 내보낸다.
6. 먼저 100,000 rays로 확인하고, seed/난수 방식을 기록한다.
   판정선 근처이거나 변동이 크면 광선 수를 늘리고 3회 반복한다.
   시뮬레이터도 같은 변경 광선 수로 재실행한다.

| α | 광원 중심 X (mm) | 방향 X | 방향 Z |
| --- | ---: | ---: | ---: |
| 0° | 0 | 0 | -1 |
| 30° | -0.5773502692 | 0.5 | -0.8660254038 |
| 45° | -1 | 0.7071067812 | -0.7071067812 |
| 60° | -1.7320508076 | 0.8660254038 | -0.5 |
| 80° | -5.6712818196 | 0.9848077530 | -0.1736481777 |

LT에서 면 방향/Front 적용 방향을 확인한다. 첫 시험에서 산란 전 반사 총량이
0.3 lm인지 먼저 확인한다. 축이 다르면 실제 좌표를 기준으로 변환하고,
그림을 비슷하게 만들기 위한 임의 회전·반전·크기 보정은 하지 않는다.
두 엔진의 총 방출 광량을 억지로 맞추는 보정계수도 적용하지 않는다.

## 6. LT 결과 파일과 자동 비교

LT 결과를 다음 이름의 CSV로 준비한다:
lambertian-aoi0.csv, directional-aoi0.csv, directional-aoi30.csv,
directional-aoi60.csv, directional-aoi80.csv, directional-aoi45.csv.

열은 `u_mm,v_mm,flux_lm`이며, 400개 bin의 중심은 각 축 -9.5에서 +9.5까지
1 mm 간격이다. flux_lm는 **그 bin의 광속**이다.
LT가 lux를 내보내면 각 bin 면적 1 mm² = 10⁻⁶ m²를 곱해 lm으로 변환한다.
W 출력이면 광원 스펙트럼에 맞는 변환이 필요하므로 가급적 photometric lm로 내보낸다.
현재 도구는 누락 bin, 좌표 불일치, 중복 좌표, 음수/비유한 값을 거부한다.

동일 폴더에 provenance.json을 함께 둔다:

```json
{
  "engine": "LightTools",
  "version": "실제 사용 버전",
  "shift_type": "No Shift",
  "anisotropic": false,
  "smoothing": false,
  "rays": 100000,
  "source_power_lm": 1,
  "random_method": "Sobol 또는 실제 선택 값",
  "notes": "좌표/Front 설정, 반복 실행, 예외 사항"
}
```

회사 파일을 외부로 옮길 필요는 없다. ZIP을 푼 회사 PC에서 LT CSV를 현지 폴더에
보관하고 다음 표준 Python 도구로 비교한다. 추가 패키지나 시뮬레이터 설치 없이
검증 묶음의 시뮬레이터 기준 CSV와 비교할 수 있다.

```powershell
python compare_bsdf_lt_csv.py --sim-directory . --lt-directory "LT_CSV_폴더"
```

lt-comparison-summary.json에는 원본 mesh 없이 총량·분포 지표와 입력한 실행 조건만
기록된다. 사내 AI에서 이 JSON의 수치 요약을 확인하거나 전달하면 된다.
우선 두 조건만 넣으면 partial이며, 여섯 조건 전체가 기준을 만족해야 passed다.
종료 코드는 passed=0, failed=1, partial=2다. 원본이 없는 조건을 통과로 간주하지 않는다.

시뮬레이터 코드와 의존성이 있는 개발 환경에서 재계산까지 하려면:

```powershell
python tools/validate_bsdf_transport.py --rays 100000 --lt-directory "LT_CSV_폴더"
python tools/build_bsdf_validation_projects.py
```

첫 명령은 실제 시뮬레이터도 다시 실행하고 수치 적분 및 제공된 LT CSV와 비교한다.
LT 데이터가 없으면 report.json의 lt_status는 not_run이며, 매칭 통과로 표시하지 않는다.
기본 산출 위치는 outputs/bsdf-validation이다.
누락된 LT 케이스가 있으면 partial로 기록한다.

LT 직접 비교의 잠정 기준도 총량 ≤1%, 겹침 ≥95%, 상관 ≥0.99로 두되,
이를 제품 전체의 정확도 보증으로 사용하지 않는다. 저광량 셀의 난수 변동,
격자 해상도와 수렴을 먼저 확인한다. 특히 45°만 어긋나면 보간 방법,
큰 입사각만 어긋나면 좌표/표 이동/면 방향 처리를 우선 조사한다.

이 PC의 LT COM 자동 실행은 0x80080005 (CO_E_SERVER_EXEC_FAILURE)로 실패했다.
원인은 확정하지 못했으며 실제 LT 수치 결과를 생성하지 못했다.
이 때문에 현재 결과를 LT 매칭 검증 완료라고 부르지 않는다.
단일면 비교 후 BEFORE/AFTER/CASE2 등의 조립체를 동일 광원·리시버로 검증하는 순서다.

## 구현 파일과 공식 근거

- src/leakage_simulator/bsdf_import.py: 원본 파일 파싱과 검증
- src/leakage_simulator/bsdf_transport.py: 정규화, 방향 추출, RT/표 보간
- src/leakage_simulator/bsdf_guard.py: 해석 경로 검증, 누락 데이터 차단
- tests/test_bsdf_transport.py: 실제 엔진/API/에너지 검사
- tools/validate_bsdf_transport.py: 독립 적분과 LT CSV 비교
- [LT 파일 형식 조사](bsdf-import-foundation.md#확인한-lt-공식-자료): 설치 매뉴얼의 정확한 절과 위치
