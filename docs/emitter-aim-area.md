# Emitter Aim Area — 목표 영역 한정 발광 v1

- 구현일: 2026-09-08
- 기능 버전: `aim-area.v1`
- 계산 계약: `target_only_uniform_area_v1`
- 적용 작업본: `main` / `d991858b0626c8bd2f2b713b14d385d82ffab5c1` 이후 로컬 변경. Commit/Push는 별도 요청 시 수행한다.

## 사용 방법

1. 기존 CAD Surface 또는 Datum Plane Emitter를 생성하거나 편집한다.
2. 기본적으로 닫혀 있는 **Aim / Target**을 펼치고 **Target 방향으로 발광**을 켠다.
3. Rectangle의 Width/Height 또는 Circle의 Diameter를 정한다.
4. Position X/Y/Z(mm), Tilt X/Y/Z(deg)로 목표 면을 배치한다.
5. 보라색 Target 외곽선·안내선을 확인한 뒤 기존 Add Emitter / Save Emitter 또는 Enter로 확정한다.

편집 중에는 프리뷰만 바뀌고 Cancel은 변경을 취소한다. Show Target은 표시만 끄며 해석에는 영향을 주지 않는다. Reset Target은 목표 설정만 초기화한다. Aim을 끄면 기존 normal, Lambertian/Isotropic/Gaussian 설정이 다시 사용된다. 기본 팝업 위치·이동·저장 조작은 유지한다.

## 계산 의미 — 실제 광원 조건을 바꾸는 모드

이번 기능은 원래 광원을 유지하는 중요도 샘플링이 아니다. 사용자가 지정한 목표 영역으로만 방출하는 별도의 물리적 광원 조건이다.

| 항목 | 동작 |
| --- | --- |
| 발광 위치 | 기존 CAD face, Datum/Reference 면에서 면적 균일 샘플링 |
| Target 점 | 사각형은 두 좌표 균일, 원형은 `r = R × sqrt(U)`, `angle = 2πV` |
| 초기 진행 방향 | `normalize(target_point - source_point)` |
| ray당 광속 | `emitter_flux_lm / ray_count` |
| 분포 | Target **면적** 균일. 입체각 균일 또는 Lambertian 조건부 분포가 아님 |
| Normal / 기존 각도 분포 | Aim On 동안 초기 방향에 사용하지 않음. 입력값은 보관 |
| 차폐·반사 | 기존 CAD 교차 및 표면 광특성 계산 유지. 목표까지 순간이동하지 않음 |
| Target 역할 | 표시용 가상 목표 면. 차폐체 또는 Receiver가 아니며 통과 후에도 추적 |
| 첫 반사 이후 | Target으로 다시 조준하지 않음. 기존 Specular/Lambertian/Gaussian 사용 |

입사 ray의 광속에는 거리 제곱이나 cosine을 추가로 중복 적용하지 않는다. 이 모드의 방향 분포는 Target 면적 샘플링 자체로 정의된다. 같은 flux로 작은 Target을 지정하면 더 집중된 광원을 만든다. 장애물이나 반사체가 앞에 있으면 모든 ray가 실제 Target에 도달한다는 뜻은 아니다.

## 입력 nit / lm

- Total: 기존 `power_lumen` 전체를 Target 방향에 배분한다.
- Power per area: 기존 광원 면적에 따른 총광속을 Target 방향에 배분한다.
- SET luminance: `Φ[lm] = π × L[nit] × A_source[mm²] × 10⁻⁶`의 기존 Lambertian 등가 총광속을 유지한다.
- **Target 면적을 발광면 면적 대신 넣지 않는다. nit를 자동으로 낮추지 않는다.**

SET 값은 TV 화면에서 측면 누설광까지의 전달 효율을 보정한 실측 광량이 아니다. Aim On/Off의 결과 차이는 버그가 아니라 광원 방향 조건 변경일 수 있다. LT 비교에서도 광량 기준과 목표점 분포를 맞춰야 하며, 이 구현을 LT 내부 알고리즘과 동일하다고 주장하지 않는다. 입력 밝기 보정은 별도 실측 기준으로 수행한다.

## 데이터 계약

기존 `EmitterSpec`에 선택적 `aim`을 추가한다. 구버전 프로젝트의 누락/null 값은 Off이다.

| 필드 | 값 / 단위 |
| --- | --- |
| enabled | boolean, 기본 false |
| shape | rectangle / circle |
| center | World X/Y/Z, mm |
| u_axis, v_axis | 직교 단위벡터. normal은 U×V |
| width_mm, height_mm | 사각형 전체 크기, mm |
| radius_mm | 원형 반지름, mm. UI에는 Diameter=2R |
| show_in_viewer | 표시 여부만 제어 |
| distribution | uniform_target_area, v1 고정 |
| power_reference | aim_region, v1 고정 |

XYZ Tilt는 기존 `planeAxesFromRotation`과 같은 순서를 쓰고 저장 시 U/V 축으로 변환한다. 다시 열 때는 U/V의 외적으로 normal을 복원하여 Tilt 숫자를 계산한다. 기존 파일에도 저장된 축이 있으면 방향에 해당하는 각도를 표시하며 파일 형식 변경은 없다. Euler 각도의 주기성 및 ±90도 특이점에서는 같은 방향을 나타내는 동등한 숫자로 정규화될 수 있다. .bitsam 및 결과의 Emitter 설정에도 이 객체를 포함한다. 결과 Compare는 Aim On/Off, 형상, 위치, 방향, 크기가 다르면 동일 조건으로 점수화하지 않는다. 표시 여부는 비교 조건에서 제외한다.

## 계산 경로 / 책임 경계

- `types.py`: 계약·유효성 검사·총광속 계산.
- `aim_sampling.py`: Target 생성, 초기 방향, 발광면/Target 중첩 검사.
- `fast_sampling.py`: 기존 Face/가상면 batch의 발광점에 Aim을 적용.
- `raytracer.py`: scalar/batch/CUDA 초기 ray 연결. 광속 집계·반사 모델은 유지.
- `emitter-aim-editor.tsx`: 닫힌 하위 메뉴, 좌표·크기·tilt 입력.
- `emitter-aim-overlay.ts`: 비선택형 Target 렌더. 물리 메시에는 추가하지 않음.
- `bitsam-project.ts`: 저장 파일의 새 필드 검증.

GPU 모드에서도 초기 샘플 배열 준비는 기존 CPU/NumPy 경로이고, CUDA BVH/지원 resident wavefront로 전달한다. 모든 작업이 GPU가 되는 것은 아니다. 작은 wave의 CPU hybrid 정책도 유지한다.

기존 **primary receiver MIS**는 Aim Emitter에 한해 사용하지 않는다. 목표 반사면 대신 Receiver로 첫 ray를 돌리면 사용자 의도와 달라지기 때문이다. 결과에 `aim_area_overrides_receiver_mis` 사유를 기록한다. 후속 반사의 bounce MIS는 기존 계약을 유지한다. Aim Off Emitter에는 기존 MIS가 그대로 적용된다.

## v1 제한 및 후속 작업

- 사각형/원형 World 고정 Target만 지원한다. CAD 면 직접 선택·부품 Transform 추종·Emitter 상대 좌표·Polygon Target은 후속 단계다.
- 2026-09-10부터 Target의 **실제 사각형/원형 영역**과 발광 삼각형의 중첩·접촉을 검사한다. 무한 연장 평면만 교차하거나, 같은 평면 위라도 두 실제 영역이 분리되어 있으면 허용한다. 서로 떨어진 CAD 면들을 하나의 채워진 덩어리로 간주하지 않는다.
- 수치 여유 `max(1e-9, 2 × epsilon_mm)`를 둔 국부 영역이 닿으면 거절하며 오류에 해당 mm 값을 표시한다. 단순히 중심 간 거리가 짧다는 이유로 거절하지 않는다. 예를 들어 epsilon `1e-6 mm`에서 분리된 평행면 간격 `1e-5 mm`는 허용되지만 접촉·중첩은 거절된다. 이는 기하 계산 검증 범위이며, 이 간격에서 기하광학이 실제 현상까지 정확하다는 뜻은 아니다.
- Target은 물리적 충돌체가 아니다. 중첩 금지는 0 길이 방향과 발광 시작점 보정의 모호성을 피하기 위한 이 광원 모델의 안전 조건이다. 매우 얕은 방향, 실제 CAD 두께·틈새가 epsilon 수준인 배치는 별도로 수렴성 및 self-hit를 검토해야 한다.
- ROI/Transform으로 원래 발광면이 바뀌어도 Target은 World에 고정된다. Source face ID는 기존 ROI remap을 사용한다. Target에 해당하는 CAD 반사면이 ROI에서 제외되면 그 반사는 계산되지 않는다.
- Reference polygon 발광면은 계산·파일 계약에서 지원한다. 현재 React의 기존 생성 버튼(CAD/Datum)을 이 변경에서 확장하지는 않는다.
- Lambertian/Gaussian을 유지하는 목표 제한 분포 및 원래 광원 유지 MIS는 별도 모드로 설계·검증한다.

## 검증

구현·시험·실행 증거는 `docs/changes/2026-09-08_emitter-aim-area.md`에 기록한다. 재현 도구는 `tests/test_emitter_aim.py`, `scripts/verify_emitter_aim.py`이다. LT 및 실측 정합성 시험은 별도로 남아 있다.
