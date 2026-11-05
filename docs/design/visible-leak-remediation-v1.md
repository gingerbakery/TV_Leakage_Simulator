# 실제 관찰 기준 빛샘 검출 보완 설계 v1

작성일: 2026-11-05 · 상태: **설계 완료 / 제품 구현 전**

## 목표와 검증 범위

TV 전원을 켜고 관찰 위치를 바꾸며 보이는 빛샘을 찾는 것을 목표로 한다. 사용자가 빛샘 각도를 미리 지정할 필요 없이 현재 카메라 또는 후면 반구 탐색으로 후보를 찾는다. 기하학적으로 가능한 경로의 개수는 밝기가 아니다. 기존 `observer_path_support_v1` 결과를 유지하되 새로운 관찰 밝기 결과와 구분한다. 기존 count를 nit로 환산하지 않는다.

기준 증거는 [기존 합성 검증 보고서](../validation/observer-fp-fn-2026-11-05.md)와 해당 JSON의 16개 사례다. 이 사례에서 확인한 과검출 4건, 미검출 3건은 합성 장면의 분류 결과이며 실물 TV의 오검출률이 아니다. 고객 도면·재질·광원·실측 영상의 대응 검증은 아직 수행하지 않았다.

## 1. 밝기 계산과 광원 계약

새 결과의 `measure`는 `luminance_cd_m2`로 고정한다. 광원 휘도, 반사율, BSDF, 입출사각, 샘플 확률, 픽셀 점유율을 반영해 카메라에 도달하는 선형 휘도를 추정한다. 화면 노출·톤 매핑은 계산 뒤에 적용하고 각도 비교에서는 노출을 고정한다. 카메라가 멀어졌다는 이유로 모든 표면 휘도에 거리 제곱 감쇠를 곱하지 않는다. 원근과 작은 틈의 픽셀 점유율은 별도로 반영한다.

광원은 다음 두 모델을 명시적으로 구분한다.

- **실제 LED:** LED 출광 위치·면적·방향·광속·배광을 사용한다. LED가 세 줄이면 실제 LED 패치를 줄별로 묶을 수 있으나 PCB 전체를 동일 휘도 면으로 바꾸지 않는다. Cell 방향의 실제 방출을 유지하며 검출 효율을 위해 물리적 방향을 뒤집지 않는다.
- **보정된 등가 광원:** 확산판 하부의 위치별·각도별 후방 방출과 광속을 별도 입력한다. LED 총광속을 그대로 후방으로 보내는 설정과 구분한다. 하부 구조 변경으로 재순환광이 달라지면 등가 광원 보정도 다시 필요할 수 있다.

`source_basis`와 보정 유무를 저장한다. 보정되지 않은 등가 광원도 탐색에는 사용할 수 있지만 결과에 모델 가정을 표시하며 실물 정확도 검증 완료로 표시하지 않는다. 현재 Sphere는 World Alpha/Beta를 사용하고 Reverse를 따르지 않으며 기본 Isotropic은 4π 분포다. 기존 파일의 이 의미를 조용히 변경하지 않는다. 광원 미리보기와 실제 샘플 방향을 동일한 계약으로 검증한다.

현재 반사 중심 모델로 확산판 투과·BTDF·광 재순환까지 설명할 수 없다. 필요한 재질의 투과가 미지원이면 `unsupported_transport`로 알리고 완전한 광학 모델로 판정하지 않는다. 미지원 투과를 흡수체로 대체한 뒤 ‘빛샘 없음’으로 결론 내리지 않는다.

## 2. 관찰 카메라와 각도

현재 `ViewerCameraFrame`의 target/축만으로는 원근 관찰을 재현할 수 없다. 다음을 불변 입력으로 저장한다: 카메라 위치(mm), target, up, projection, vertical FOV, aspect, 이미지 크기, TV Datum 원점·축, 각도 규약 버전. 지원하지 않는 projection은 명시적으로 거절한다.

관찰 위치의 H/V는 고정 TV Datum에서 카메라 위치로 향하는 벡터로 계산한다. 카메라 look direction과 구분한다. 화면 pan으로 target이 바뀌거나 roll을 해도 관찰 위치 각도는 바뀌지 않는다. 후면 기준 w=-Z일 때 기존 H+는 World +X이고 V+는 +Y다. 후면 카메라 화면의 오른쪽과 H+가 같다고 가정하지 않는다. 180° 탐색은 후면 **반구**의 H/V 범위와 고유 pole 규칙을 저장한다.

각도별 영상·휘도·경로를 따로 보관한다. 여러 각도의 후보를 그룹으로 묶어도 각도 범위와 최대 기여 각도를 잃지 않는다. 최초 경로를 최대 밝기 경로라고 표시하지 않는다.

## 3. 과검출 방지

1. 약한 광원·낮은 반사율·BSDF 꼬리의 작은 기여를 실제 가중치로 계산한다. BSDF의 값이 양수라는 사실만으로 검출 판정하지 않는다.
2. 광원 투영 영역을 겨냥한 샘플에도 정확한 PDF와 픽셀 필터 가중치를 적용한다. 작은 틈을 맞힌 한 샘플을 전체 픽셀 휘도로 처리하지 않는다.
3. 탐색과 확인에 독립 seed를 쓴다. 탐색 중 큰 값 한 건은 잠정 후보로 보관하고 별도 확인 샘플로 재평가한다. 선택에 쓴 샘플만 재사용해 신뢰도를 과장하지 않는다.
4. 휘도 임계값은 사용자·실측 조건에 맞춰 입력하고 단위를 표시한다. 합성 검증의 1 nit는 테스트 기준일 뿐 보편적 가시성 기준이 아니다. 임계값 미설정 시 원시 휘도와 불확실성만 제공한다.
5. 검출한 대표 경로의 모든 구간을 동결된 장면에서 다시 차폐 검사한다. 카메라→첫 면, 반사면 사이, 마지막 면→광원 구간을 모두 확인한다. 광원 앞의 불투명 부품도 검사한다.

전체 Main + 활성 ACC + 활성 Block을 같은 물리 장면으로 사용한다. Hide는 표시만 바꾸고, ACC OFF는 물리 장면을 바꾼다. ROI는 관찰 영역을 제한하되 주변 차폐·반사 부품을 제거하지 않는다. Block은 지정 반사 재질을 가진 폐곡면이며 흡수체로 강제하지 않는다. 홀을 막는 Block의 틈·두께·변환·경계 epsilon과 반사 후 탈출을 별도 검증한다.

대표 경로에는 hit face/source/material ID, 실제 표면점, 구간별 PDF·기여도·장면 hash를 남긴다. 선택된 경로는 BVH 외 독립 CPU 삼각형 기준으로도 확인한다. 동일 BVH끼리의 일치만으로 관통이 없다고 결론 내리지 않는다. 점은 실제 경로의 발생/관찰 표면에 표시하고 AABB 외곽으로 투영하지 않는다. 관찰 방향 가이드는 실제 광선 구간과 구분한다.

## 4. 미검출 방지

검출된 곳만 세분하면 최초 샘플 사이의 좁은 시야각과 작은 gap을 계속 놓친다. 적응형 예산은 다음 두 경로로 배정한다.

- **전역 탐색 예산:** 결과가 모두 0인 각도·화면 영역에도 반드시 배정한다. 각도 cell의 중점·화면 subpixel을 순차 탐색하고 지정된 최소 각도/공간 해상도까지 coverage를 기록한다.
- **국소 확인 예산:** 밝은 후보, 큰 분산, 기하 경계, 작은 틈 주변에 추가 샘플을 배정한다. 광원·개구부 유도 proposal에는 전역 지지를 가진 proposal을 혼합하고 정확한 확률을 사용한다.

예를 들어 H=[45,60], V=[0,15]의 모서리가 모두 0이어도 중점 (52.5,7.5)를 탐색한다. 단, 중점 하나로 임의의 좁은 경로를 모두 찾는다는 보장은 없다. 각도 cell 크기, 픽셀 footprint, 샘플 수, 미탐색 영역, 시간·메모리 예산 중단 여부를 함께 기록한다. 후면 반구를 적분할 때는 H/V 격자를 동일 입체각으로 간주하지 않는다.

다중 반사는 적절한 Russian roulette와 생존 가중치를 사용한다. 최대 깊이·시간·예산에 잘린 경로는 미해결로 기록하며 빛이 없다는 증거로 쓰지 않는다. 현재 고정 RNG 대신 설정 seed + view/pixel/sample/depth/lane의 안정적인 식별자로 샘플을 생성한다.

## 5. 판정과 중단 상태

계산 상태와 검출 판정을 별도 필드로 관리한다.

| 축 | 상태와 의미 |
|---|---|
| 실행 | running / completed / stopped / budget_limited / failed / unsupported_transport / stale |
| 관찰 | detected_model / below_threshold_estimate / uncertain / not_observed |
| 물리 보정 | uncalibrated / calibrated / reference_validated |

샘플이 전부 0이라 평균·표본분산이 0이 되어도 신뢰구간 상한 0 또는 빛샘 없음으로 처리하지 않는다. 좁은 경로와 미지원 모델은 통계 오차 밖의 문제다. `completed`는 요청한 계산 완료를 뜻하며 모든 빛샘 발견을 보증하지 않는다. 예산·깊이·모델 제한은 별도 `limitations`로 유지한다. 중단·실패 시 상태와 이유를 UI 및 서버 로그에 남기고 부분 결과로 표시한다.

## 6. 구현 경계와 저장

| 구현 단위 | 책임 / 기존 연결점 |
|---|---|
| camera contract | 실제 viewer pose와 고정 Datum, 각도 변환, pixel ray |
| emission contract | 기존 광원 정의의 방출 휘도·지원 방향·PDF·단위 |
| transport eval/pdf | optics/compiled BSDF와 동일한 forward 모델 평가, 연속/δ 구분 |
| CPU visibility reference | 카메라 path tracing, NEE/MIS, 선형 휘도, first-hit 기록 |
| adaptive scheduler | 전역/국소 예산, 독립 확인, coverage와 중단 이유 |
| path audit | raytrace_bridge의 전체 장면 + 독립 차폐 검사 |
| result integration | 기존 leak-preview 그룹과 각도별 휘도/경로 연결 |

기존 observer probe를 단순 수정해 count 의미를 바꾸지 않는다. 새 visibility job registry/API를 분리하고 기존 해석 작업과 자원 사용을 조정한다. 카메라 이동은 사용자가 live 탐색을 켰을 때만 debounce 실행하며 별도의 전체 각도 작업을 취소하지 않는다. `pose_seq`가 오래된 결과는 최신 화면에 덮어쓰지 않는다.

입력 snapshot/hash에는 geometry·Block·ACC 상태·source·material·camera·seed·모델 버전을 포함한다. 노출과 Hide는 물리 계산 cache를 바꾸지 않는다. 저장 파일에는 새 optional namespace와 명시적 버전을 두고, 구형 build가 새 결과를 기존 count로 읽지 못하게 한다. 중단 작업을 파일 열기로 자동 재개하지 않는다.

UI는 현재 시점/후면 탐색, 임계값·단위, 예산, 실행/중단, 간결한 상태만 기본 노출한다. 설명은 기존 `(?)` HelpTooltip/popover에 넣는다. 각도 선택 시 해당 각도 결과를 보여주고 nit·불확실성·부분 결과 상태를 유지한다.

## 7. 단계 및 출고 합격 기준

P0: 광원·카메라·BRDF eval/pdf 계약과 단위 확정 → P1: CPU 기준 렌더러 및 아래 해석해 검증 → P2: 전역/적응형 탐색과 독립 확인 → P3: UI·저장·취소·그룹 통합 → P4: 실물 TV 대응 보정. GPU 변경·측정은 이번 설계 범위에 없으며 별도 GPU runbook과 승인 범위를 따른다.

| 검증 대상 | 사전 합격 기준 |
|---|---|
| 광원/반사율 스케일 | 이상적인 해상된 직접 관찰과 정반사 fixture에서 해석해와 일치. 1e-6 vs 100 nit, R=1e-6 vs .9를 count가 아닌 휘도로 구별 |
| BSDF | 정규화·보간·약한 tail 기여·비상반성 방향을 독립 적분과 비교. 양수 여부 판정 금지 |
| 각도/공간 미검출 | 기존 52.5°/7.5° 및 offset .08 mm gap fixture에서 실제 검출. 못 찾으면 uncertain/coverage 제한으로 보고하며 검출 성공으로 통과시키지 않음 |
| 차폐/Block | 닫힌 불투명 벽·ACC cover에서 직접 투과 0. 반사 Block은 반사 기여 >0, 직접 관통 0. 기울어진 Reverse Block 표시/계산 좌표 일치 |
| 다중 반사 | 기존 2회 반사 fixture의 경로·기여 일치. 깊이 제한 시 미해결 표시 |
| 카메라/표시 | 저장/재열기 pose·각도 재현, pan/roll 구분, 점이 실제 표면에 위치, 대표 경로 독립 차폐 검사 통과 |
| 실행/파일 | stop·예산·미지원·stale·오류를 숨기지 않음. legacy count를 nit로 읽지 않음. 실제 ACC import/export 통합 검사 포함 |
| 실물 정확도 | 동일 도면·광원·재질·카메라/노출에서 실측 영상과 발생 위치·각도·밝기를 비교. 합성 통과만으로 실물 검증 완료 표시 금지 |

결정론적 수학 단위 검사는 절대 오차 1e-10을 기준으로 한다. MC 적분 검증은 실행 전에 fixture별 seed·예산·해석해·허용 오차를 고정해 기록하고 독립 seed 반복의 수렴을 확인한다. 결과를 본 뒤 gate를 완화하지 않는다. 기존 감사 스크립트의 실패를 이 설계 문서나 설계 검사 통과로 덮지 않는다.

구현에 앞서 fixture별 MC 예산/오차와 실측 임계값은 별도 검증 계획에 고정해야 한다. 이 문서는 완성된 제품이나 물리적 정확성의 증거가 아니다.

## 근거 및 부속 문서

- [수송 수학·기존 모델 호환 설계](visible-leak-transport-v1.md)
- [기계 판독 설계 계약](visible-leak-contract-v1.json)
- [PBRT: Path Tracing](https://pbr-book.org/4ed/Light_Transport_I_Surface_Reflection/Path_Tracing)
- [PBRT: A Better Path Tracer](https://pbr-book.org/4ed/Light_Transport_I_Surface_Reflection/A_Better_Path_Tracer)
- [PBRT: Path-Space Measurement Equation](https://www.pbr-book.org/3ed-2018/Light_Transport_III_Bidirectional_Methods/The_Path-Space_Measurement_Equation)
