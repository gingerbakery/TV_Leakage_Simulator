# Emitter Aim Area 구현 타당성 검토

- 작성일: 2026-09-08
- 상태: 최초 검토 이력. 이후 사용자 확정에 따라 목표 영역 한정 발광 v1을 구현했다. 현재 계약은 `docs/emitter-aim-area.md`를 따른다.
- 검토 대상: `main`의 `d991858b0626c8bd2f2b713b14d385d82ffab5c1` 기반 작업본. 기존 최대 반사 1,000회 변경은 유지한다.
- 범위: 사용자 PC의 LightTools 도움말과 현재 emitter 데이터 계약·샘플링·UI 코드 검토. LT 실행 비교나 신규 CPU/CUDA 테스트는 수행하지 않았다.

## 1. 결론

**2026-09-08 구현 결정:** 아래 문서는 원래 타당성 검토를 보존한다. 사용자는 A(목표 영역 한정 발광)를 확정했다. v1은 사각형/원형 Target 면적 균일 샘플링 + 입력 총광속 전체 배분으로 정의하며, 아래 B/MIS 및 Lambertian 각도 분포 유지 제안은 이번 구현 범위가 아니다. 기존 SET nit를 임의로 낮추거나 실제 밝기가 자동 보정된다고 설명하지 않는다.

구현 가능하다. 발광 위치를 정의하는 기존 CAD/Datum/Reference emitter와 별도로, 각 emitter에 목표 영역을 지정하는 기능을 추가한다. 발광면의 점에서 목표 영역의 점을 향하는 초기 방향을 생성하고, 이후에는 기존 CAD 차폐·반사·receiver 교차 계산을 수행한다.

핵심 난점은 목표 면을 그리는 일이 아니라, 목표 방향으로 표본을 집중하더라도 입력 광량과 결과의 물리적 의미가 바뀌지 않게 하는 것이다. 단순히 모든 ray를 목표로 돌리고 기존 ray당 광량을 유지하면 실제 광원을 바꾼 결과가 될 수 있다.

## 2. LT 로컬 매뉴얼에서 확인한 내용

| 항목 | 확인 내용 |
| --- | --- |
| 설정 위치 | Source Properties의 Emittance에서 Aim Area를 선택한 뒤 별도 Aim Area 탭 설정 |
| 형상 | Circular, Rectangular, Polygonal, Surface Based |
| 위치·방향 | 전역 좌표 또는 광원 상대 좌표의 X/Y/Z 및 Alpha/Beta/Gamma |
| 크기 | 원형 반지름, 사각형 폭·높이, 다각형 꼭지점, CAD 면 기반 영역의 배율 |
| 이동 연동 | 전역 좌표 고정 또는 광원 이동 추종. LT의 좌표 고정 옵션은 기본적으로 꺼져 있음 |
| CAD 면 연동 | 목표 surface 형상·위치·방향이 바뀌면 Aim Area도 갱신 |
| 표시 | 목표 영역 표시/숨김 |
| 광량 기준 | Measured Over에서 전체 방출 영역과 Aim Region 기준을 구분 |
| 수치 정확도 | 목표의 투영 입체각 근사에 경계점을 사용하며, 정밀도와 계산량의 절충이 있음 |

근거 파일:

- `D:/LightTools/Help/illumination/liug_2.3.47.html`: Defining Aim Entities for Importance Sampling
- `D:/LightTools/Help/illumination/liug_2.3.50.html`: Aim Areas
- `D:/LightTools/Help/illumination/liug_2.3.51.html`: Boundary Points and Accuracy in Aim Area Calculations
- `D:/LightTools/Help/illumination/liug_2.3.52.html`: Defining an Aim Area for Sources
- `D:/LightTools/Help/illumination/liug_2.3.54.html`: Defining a Surface-Based Aim Area for a Source
- `D:/LightTools/Help/illumination/liug_2.3.35.html`: Source Power
- `D:/LightTools/Help/dbhlp/HIDC_SourceRayDataEmittance_MeasuredOver_3.html`: Emittance Properties for an Object Source, Disk Source, and Rectangle Source
- `D:/LightTools/Help/dbhlp/HIDD_RectangularSourceAimArea.html`: Parameters for a Rectangular Aim Area on a Source
- `D:/LightTools/Help/dbhlp/HIDD_CircularSourceAimArea.html`: Parameters for a Circular Aim Area on a Source
- `D:/LightTools/Help/dbhlp/HIDD_SurfaceBasedSourceAimArea.html`: Parameters for a Surface-Based Aim Area on a Source

사용자 매뉴얼은 기능·광량 기준을 확인하는 자료이며, LT 내부의 모든 난수 생성·정규화 알고리즘을 공개한 것은 아니다. 아래 권장 알고리즘을 LT 내부 구현과 동일하다고 표현하지 않는다.

## 3. 광학 조건과 계산 효율 옵션을 구분

### A. 목표 영역 한정 발광

- 초기 방출 방향을 목표 영역으로 한정한다.
- 입력 lm이 전체 원광원의 광량인지, 목표 영역 방향에 배정된 광량인지 명시한다.
- 목표 영역 광량을 고정하는 경우, 영역을 작게 하면 광량이 더 좁은 방향에 집중될 수 있다.
- 원래 광원의 목표 밖 방출 및 그 방향에서 시작되는 간접 경로를 제외한 모델이다.
- 단순히 목표의 면적을 균일 샘플링하고 모든 ray에 같은 에너지를 부여하는 방식은 원래 Lambertian 각도 분포를 자동으로 유지하지 않는다.

### B. 원래 광원 유지 + 목표 방향 집중 샘플링

- 물리적인 광원·반사율·총광속은 유지한다.
- 계산 표본을 목표 영역 방향에 더 배정하고, 각 ray의 대표 광량을 확률밀도에 맞춰 보정한다.
- 목표 밖에서 출발해 여러 번 반사된 뒤 receiver에 들어오는 빛도 놓치지 않도록 일반 방출 표본을 혼합한다.
- 목표는 원래 해석과 같은 기대값을 더 효율적으로 추정하는 것이다. 모든 장면에서 분산·실행시간 개선을 보장하지 않는다.
- LT/실측 정합성 검토가 주목적이라면 이 모드를 권장한다. A와 B는 UI에서 서로 다른 의미임을 설명해야 한다.

### SET luminance와의 관계

현재 `src/leakage_simulator/types.py:281`은 다음과 같이 계산한다.

`source_flux_lm = pi × luminance_nit × emitter_area_mm2 × 1e-6`

이는 선택한 발광면의 Lambertian 등가 총광속 환산이다. TV 화면에서 측면 누설광까지의 전달 효율을 자동으로 계산하는 모델은 아니다. Aim Area 크기를 이 식의 발광면 면적에 대입해서도 안 된다.

SET luminance 입력은 우선 기존 전체 방출 기준을 유지한다. 목표 영역에 전체 광속을 재배정하는 옵션을 제공하려면 별도 명시적 선택과 경고가 필요하다. Gaussian/Isotropic에서 현재 SET luminance는 이미 Lambertian 등가 입력이므로, 정밀 비교에서는 분포별 광량 기준도 확인해야 한다.

## 4. 권장 샘플링 원리

이 절은 평면 목표와 원래 광원 유지 모드에 대한 설계식이다. 발광 위치의 원래 샘플링 분포는 유지한다고 가정한다.

- 발광점 `s`, 목표점 `t`: `direction = normalize(t - s)`.
- 목표를 면적 균일 샘플링할 때 방향 확률밀도는 `q_target = distance² / (target_area × abs(target_normal · direction))`이다. 거리와 면적 단위를 일치시킨다.
- 원래 방출 방향 PDF를 `p_source`, 목표 표본 비율을 `alpha`라 하면 `q_mix = (1-alpha) × p_source + alpha × q_target`이다.
- 각 ray의 대표 광속은 `(source_flux / ray_count) × p_source / q_mix`로 계산한다.
- `0 < alpha < 1`로 일반 방출 방향도 유지한다. 원래 분포가 0인 방향에 물리적 에너지를 만들어 넣지 않는다.
- 입체각 변환의 거리·cosine 항을 receiver hit 집계에서 다시 곱하지 않는다. 수광 면적에 대한 lux 계산은 별개로 유지한다.
- 목표 한정 발광을 구현할 때는 선택한 광량 기준에 맞는 영역 적분과 정규화가 추가로 필요하다. 발광점마다 목표 입체각이 다르므로 단순 상수 재배정으로 처리하지 않는다.

면적 PDF와 입체각 PDF 변환 및 중요도 샘플링 원리 참고: [PBRT — Sampling Light Sources](https://www.pbr-book.org/3ed-2018/Light_Transport_I_Surface_Reflection/Sampling_Light_Sources).

## 5. 현재 코드에서 재사용 가능한 부분

| 코드 | 확인한 내용 및 적용 방향 |
| --- | --- |
| `src/leakage_simulator/types.py:161` | EmitterSpec에 발광면·normal·분포·광량이 정의됨. 별도 aim 정의는 없음 |
| `frontend/src/api/types/raytrace.ts:12` | UI/API EmitterSpec 역시 aim 정의 없음 |
| `src/leakage_simulator/fast_sampling.py:471` | receiver 방향을 추가 샘플링하고 혼합 PDF로 가중치를 구하는 기존 기반 있음 |
| `src/leakage_simulator/fast_sampling.py:545` | 해당 경로의 source PDF는 Lambertian/Isotropic만 지원 |
| `src/leakage_simulator/fast_sampling.py:561` | 기존 receiver 사각형 방향 PDF 계산. 목표 전용 기하·양면 규칙과 분리 필요 |
| `src/leakage_simulator/raytracer.py:2422` | 기존 receiver MIS 활성 조건과 fallback 판정. Aim과 중첩 적용되지 않도록 명시적 정책 필요 |
| `frontend/src/features/raytracing/ray-tracing-panel.tsx:530` | Emitter properties의 Power mode/Direction distribution 주변에 Aim 설정을 배치 가능 |
| `frontend/src/features/projects/bitsam-project.ts` | 새 설정의 저장·검증·복원 및 구버전 기본값 추가 필요 |

기존 receiver MIS를 이름만 바꿔 그대로 쓰지 않는다. Aim 영역은 검출기가 아니므로 receiver의 수광면 방향·acceptance angle·검출 종료 조건을 상속해서는 안 된다. 초기 구현에서는 기존 receiver MIS와 새 Aim 샘플링 중 하나만 명시적으로 선택하게 하는 편이 안전하다.

## 6. UI 및 데이터 설계 초안

기존 광원 생성 방식은 그대로 유지하고, Emitter properties 안에 기본적으로 닫힌 `Aim / Target` 영역을 둔다.

| 입력 | 제안 |
| --- | --- |
| 사용 여부 | Off가 기본. 기존 프로젝트 동작 유지 |
| 목표 정의 | Rectangle / Circle / CAD surface |
| 크기 | Width·Height 또는 Diameter. 내부 반지름과 단위 변환 명확화 |
| 위치 | X/Y/Z, mm |
| 회전 | Rx/Ry/Rz, deg. 기존 앱 회전 순서 유지·문서화 |
| 좌표 기준 | World 고정 / Emitter 상대. LT의 기본값과 다를 경우 명시 |
| CAD 선택 | 3D viewer에서 목표 face 선택, 선택된 부품·면 이름 표시 |
| 광량 처리 | 원래 밝기 유지 / 목표 영역 한정 발광 및 광량 기준 |
| 적용 | 편집 중 preview, Apply 또는 Enter로 확정, Reset/삭제 |
| 표시 | 목표 외곽선·방향 안내선 On/Off. emitter/receiver와 구별 |

`EmitterSpec.aim`에 enabled, shape, coordinate_frame, center, u_axis, v_axis, width/height/radius, target component/face 참조, transport mode, power reference, sampling fraction 등을 모아 저장하는 구조를 검토한다. 필드명은 아직 확정 계약이 아니다.

목표 안내 영역은 렌더링 overlay이며 CAD 차폐체·receiver로 등록하지 않는다. CAD 면을 대상으로 선택해도 원래 CAD 면의 반사·차폐는 그대로 계산한다. 장애물이 사이에 있으면 ray는 목표 도착 전에 막히거나 반사된다.

## 7. 권장 구현 순서

| 단계 | 범위 | 완료 기준 |
| --- | --- | --- |
| AIM-0 | 광량 기준·PDF·좌표 계약 확정 | 기존 입력 의미와 A/B 모드 구분 문서화 |
| AIM-1 | Rectangle/Circle UI, 좌표·tilt, preview, 저장/복원 | CAD 없는 목표 배치와 .bitsam 왕복 검증. 계산 미지원 상태를 명시 |
| AIM-2 | Lambertian/Isotropic 목표 샘플링과 광량 보정 | 차폐 없는 기준문제·차폐·간접 반사에서 기존 해석과 통계적 일치 |
| AIM-3 | 배치·CPU/CUDA 실행 경로 연동 및 진단 | 지원 경로별 가중치·집계 일치와 실제 실행 증거 확보 |
| AIM-4 | CAD surface 목표, transform/ROI 연동 | 삼각형 하나가 아닌 원래 CAD 면 집합을 지정하고 이동·삭제 처리 |
| 후속 | Polygon 및 Gaussian 중요도 샘플링 | 분포 PDF와 정규화까지 검증한 뒤 지원 |

사용자가 CAD surface 목표를 우선 요구하면 AIM-4 UI·기하 작업을 앞당길 수 있으나, 광량 보정 검증을 건너뛰지 않는다. Gaussian은 기존 각도 샘플러의 PDF와 경계 처리까지 검토해야 하므로 Lambertian PDF를 대신 사용하지 않는다.

## 8. 필수 검증과 위험

- Off 상태에서 기존 해석 결과와 파일 호환 유지.
- 중심·크기·tilt·목표 앞/뒤·발광면과 겹침·면적 0·접선 방향 검증.
- 실제 목표점 생성 분포와 계산에 쓰는 PDF가 일치하는지 검증.
- 작은 Lambertian 발광점과 정면 원형 목표의 기준 비율 `radius²/(distance²+radius²)`로 직접광 검증. 유한 면광원에는 이 점광원 식을 그대로 사용하지 않음.
- 원래 밝기 유지 모드에서 일반 샘플링과 목표 혼합 샘플링의 flux/lux를 여러 seed·오차 범위로 비교. raw hit 수는 동일할 필요 없음.
- 목표 밖 첫 반사로 receiver에 도달하는 케이스를 포함해 간접 경로 누락 확인.
- 목표 한정 모드는 동일한 제한 방출·광량 기준의 기준해와 비교. 전체 방출 모델과 같아야 한다고 요구하지 않음.
- 가중치에 대한 제곱합·오차 추정·유효 표본 수를 반영하고, hit/cell 증가만으로 수렴 성공을 판정하지 않음.
- ray당 가중치가 작아지는 경우 기존 절대 에너지 종료 기준으로 유효 기여가 과도하게 잘리지 않는지 검증.
- CPU scalar/batch 및 지원 CUDA 경로의 출력·가중치·집계를 비교. 실제 CUDA 검증 전에 GPU 지원 완료라고 표현하지 않음.
- CAD 원본 면 ID와 tessellation triangle ID를 구분하고, ROI 절단 가상 면의 참조 방식·변형 후 좌표·삭제된 목표 참조를 검증.
- LT 비교 시 source angular distribution, Measured Over, source flux, Aim geometry, receiver 수광 조건과 단위를 함께 기록.

## 9. 변경 이력

| 날짜 | 변경 | 구현/검증 상태 |
| --- | --- | --- |
| 2026-09-08 | LT 로컬 매뉴얼과 코드에 근거한 최초 타당성·광량 기준·단계별 계획 정리 | 문서만 추가. 코드 변경·신규 시뮬레이션·성능 측정 없음. Commit/Push 없음 |
| 2026-09-08 | 사용자 확정 후 A 모드의 사각형/원형 Target, UI·저장·CPU/CUDA 경로 구현 | 최신 계약과 검증은 `emitter-aim-area.md`, `changes/2026-09-08_emitter-aim-area.md` 참조. CAD 연동 Target은 후속 |
