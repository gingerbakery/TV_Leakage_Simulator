# ROI 절단면 빗금 표시

- 날짜: 2026-09-08
- 상태: 구현, 자동 테스트, 실제 브라우저 확인 완료
- 작업 폴더: `TV leakage simulator main`
- 기준 커밋: `d991858b0626c8bd2f2b713b14d385d82ffab5c1`
- 커밋/푸시: 수행하지 않음
- 선행 기록: [ROI CAD 면 선택 수정과 절단면 점검](2026-09-08_roi-cad-face-selection.md)

## 요구사항과 구현

ROI로 새로 생긴 표시용 절단면을 실제 CAD 표면으로 오인하지 않도록 절단면에만 사선 빗금을 추가했다. 기존 하늘색 계열 바탕은 유지하고 Surface / Surface + Edge에서는 어두운 빗금, Wireframe에서는 밝은 빗금을 사용한다. 원본 CAD 면, 면 물성 오버레이, 선택 강조에는 이 재질을 적용하지 않는다.

| 항목 | 동작 |
| --- | --- |
| 절단면 표면 | 약 12 CSS px 간격, 약 1.4 CSS px 두께의 사선 패턴 |
| 확대/축소 | 화면 기준 간격을 유지하여 빗금이 지나치게 촘촘해지거나 커지지 않음 |
| 회전 | 현재 화면에서 동일한 사선 방향으로 표시. 실제 소재의 방향성/부식 패턴을 의미하지 않음 |
| 렌더 해상도 | 기기 픽셀 비율 및 대형 모델의 임시 렌더 해상도 변경을 반영 |
| 투명도 | Surface 계열의 기존 투명도와 깊이 판정을 유지. Wireframe의 기존 불투명도 0.75 유지 |
| 상단 범례 | 박스 ROI 표시 중 `빗금 · ROI 절단면` 표시 |
| 절단면 클릭 | 기존 `roi_cap` 상태를 이용해 범례를 `ROI 절단면 · 표시 전용`으로 변경 |
| 좁은 화면 | 상태 배지들이 여러 줄로 배치되도록 조정 |

빗금은 기존 cap mesh의 표시 재질에서 색상만 바꾸는 fragment shader 효과다. 추가 선 mesh, 텍스처 파일 또는 CAD 삼각형을 생성하지 않아 삼각분할 경계가 빗금으로 드러나지 않는다. 패턴 간격은 표시용이며 실제 mm 치수와 대응하지 않는다. 광학 표면 거칠기/산란 특성도 아니다.

## 변경 파일과 책임

| 파일 | 변경 내용 |
| --- | --- |
| `frontend/src/features/viewer/roi-cap-material.ts` | 전용 절단면 재질, 사선 패턴, 안티앨리어싱, 픽셀 비율 반영 |
| `frontend/src/features/viewer/three-viewer-canvas.tsx` | `roi-section-caps` mesh에만 전용 재질 적용 |
| `frontend/src/components/layout/viewer-workspace.tsx` | 빗금 범례 및 절단면 클릭 안내, 배지 줄바꿈 |
| `frontend/src/features/viewer/roi-cap-material.test.ts` | 재질 2종과 렌더 해상도 변경 테스트 3개 |
| `frontend/src/features/feature-editors.test.tsx` | ROI/절단면 클릭/원본 면 재선택/ROI 드래그 중 범례 테스트 1개 추가 |

원본 CAD mesh, 원본 면 ID, ROI 클리핑 알고리즘, Transform, Material Assignment, Emitter/Receiver, `.bitsam` 저장 계약, Ray Tracing 엔진은 이 작업에서 수정하지 않았다. 절단면은 여전히 원본 Face Override 대상이 아니다. 기존 Datum의 절단면 참조 동작도 바꾸지 않았다.

## 검증

| 항목 | 결과 |
| --- | --- |
| 전체 프론트엔드 회귀 테스트 | 35개 파일, 225개 테스트 통과, 8.46초 |
| TypeScript + 프로덕션 빌드 | 통과 |
| 정적 검사 | 오류 0, 기존 `result-window.tsx:1799`의 `liveResult` effect 의존성 경고 1개 유지 |
| Git 공백 검사 | 통과. 기존 Windows 줄바꿈 안내만 출력 |
| 실제 브라우저 | Surface + Edge / Wireframe / Surface에서 빗금 표시 확인 |
| 클릭 구분 | cap 클릭 시 `ROI 절단면 · 표시 전용`, 원본 CAD 면 선택으로 위장하지 않음 |
| 투명도/회전/테마 | Surface 투명도 50%, 회전, 밝은/어두운 테마에서 패턴 확인 |
| 브라우저 오류 로그 | 시험 탭 오류 없음. 셰이더 컴파일 오류 없음 |

실제 브라우저 시험은 `http://127.0.0.1:8788/`의 별도 임시 탭에서 실행했다. `samples/tv_leakage_roi_right_bottom_no_gap.stp`를 Import한 후 최소 `(-5.314, 23, 0)`, 최대 `(40.194, 62.978, 45)` mm ROI로 사용자가 보고한 위치를 재현했다. 사용자 작업 중인 Edge 탭은 새로고침하거나 설정을 변경하지 않았다. 시험 탭은 확인 후 닫았다.

jsdom 환경의 Canvas/WebGL 미지원 안내 및 기존 500 kB 초과 번들 경고는 유지된다. 이번 변경에 대한 GPU 해석/광학 정확도/성능 벤치마크는 실행하지 않았다. 빗금 표시를 위해 별도 CUDA 준비나 서버 재시작은 필요하지 않다.

## 적용 확인

실행 서버가 제공하는 프론트엔드 빌드를 갱신했다. 사용자는 작업 중인 설정을 먼저 Save한 뒤 기존 주소를 새로고침하고 CAD/프로젝트를 다시 불러오면 된다. 별도 도면에서 만들어진 단면 뷰(H 기능)의 cap 재질 변경은 이번 범위가 아니며, 이번 요청 대상인 ROI 절단면에만 적용한다.
