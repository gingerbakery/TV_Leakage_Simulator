# CAD Import 성능 개선 (Web UI v0.9.17)

## 확인된 병목

- feature edge 계산은 세분화 전 수십~수백 개 triangle만 처리하므로 성능 영향이 미미했다.
- 실제 병목은 ROI 선택 정밀도를 위해 작은 STEP도 과도하게 재분할하는 과정이었다.
- 테스트 모델은 원본 88 triangles에서 532,480 triangles로 증가했다.
- 브라우저는 import 직후 전체 face adjacency를 만들고 Full/ROI viewer에 같은 전체 mesh를 각각 생성했다.

## 변경

- ROI 재분할 기본 edge 목표를 `0.5 mm`에서 `1.5 mm`로 조정했다.
- 최대 재분할 예산을 `750,000`에서 `150,000` faces로 낮췄다.
- face adjacency는 import 시 계산하지 않고 Local Face 군집 선택을 처음 사용할 때만 계산한다.
- ROI가 아직 없으면 ROI viewer에 전체 CAD mesh를 중복 생성하지 않는다.
- CAD 로드 중 불필요하게 수행되던 중간/중복 viewer draw를 제거했다.

## 정확도 영향

- STEP 원본 tessellation과 실제 CAD 형상은 변경하지 않는다.
- ray 교차 및 gap/transform 좌표 정밀도는 변경되지 않는다.
- 영향 범위는 ROI 박스 경계가 face 단위로 선택되는 공간 해상도이며 기본 약 `1.5 mm`이다.
- 현재 주요 ROI가 `50 × 50 × 50 mm` 이내인 사용 조건에서는 충분한 초기 해상도로 판단한다.

## 기준 모델 측정

`tv_leakage_roi_left_bottom_no_gap_9.stp` 기준:

| 항목 | 변경 전 | 변경 후 |
|---|---:|---:|
| 생성 faces | 532,480 | 50,944 |
| scene payload 생성 | 8.875 s | 1.35~1.62 s |
| JSON 생성 | 1.270 s | 0.116 s |
| JSON 크기 | 60.38 MB | 5.27 MB |

- 웹 UI에서 파일 선택부터 `Loaded` 표시까지 실제 측정: 약 `2.14 s`.

측정값은 현재 개발 PC의 단독 백엔드 측정이며 실제 웹 체감 시간은 브라우저 렌더링 시간을 추가로 포함한다.
