# ROI 이후 CAD 면 선택 및 하이라이트 범위 수정

- 날짜: 2026-09-08
- 상태: 구현, 회귀 테스트, 실제 브라우저 확인 완료
- 작업 폴더: `TV leakage simulator main`
- 기준 커밋: `d991858b0626c8bd2f2b713b14d385d82ffab5c1`
- 커밋/푸시: 수행하지 않음
- 관련 원인 검토: [ROI 면 선택 검토](../roi-face-selection-review.md)

## 원인과 변경

일반 Viewer 클릭이 부모 부품 ID와 삼각형 하나를 함께 저장하고, ROI 오버레이가 부모 부품의 모든 면을 강조하는 불일치를 수정했다. CAD 파서, 원본 형상, Ray Tracing 엔진은 이번 작업에서 수정하지 않았다.

| 동작 | 수정 후 계약 |
| --- | --- |
| 일반 CAD 면 클릭 | 클릭한 삼각형의 원본 CAD 면을 찾아 해당 면의 삼각형 집합을 선택한다. 다른 원본 면이나 부모 부품 전체로 확대하지 않는다. |
| 같은 면 다시 클릭 | 단일 선택을 해제한다. 다른 면 클릭은 선택을 교체한다. |
| Ctrl/Shift 다중 선택 | CAD 면 단위로 추가/제거한다. 동일 부품의 두 번째 면을 추가해도 부모 부품이 토글되어 빠지지 않는다. |
| Component tree 클릭 | 면 선택과 구분되는 부품 전체 선택으로 전환한다. 첫 클릭은 부품 선택, 다음 클릭은 해제다. |
| ROI 하이라이트 | 선택 면의 ROI 내 잘린 영역만 강조한다. 미니맵의 ROI 위치 표시와 선택 하이라이트는 별개다. |
| ROI 내 Emitter/Material 면 선택 | 같은 원본 CAD 면이라도 활성 ROI 삼각형 후보 밖으로 새 선택을 확장하지 않는다. |
| ROI 인공 절단면 클릭 | 원본 면으로 가장하지 않고 `roi_cap` 상태로 구분한다. 선택 면/부품 ID를 비우며, Transform 편집 대상도 자동 하이라이트하지 않는다. |
| 상태 배지 | 부모 소속은 `Part`, 실제 면 선택 개수는 `CAD Face · N`으로 표시한다. 부품 선택 시에는 `Component`를 유지한다. |

## 구현 추적

| 파일 | 변경 책임 |
| --- | --- |
| `frontend/src/features/viewer/viewer-selection.ts` | 원본 CAD 면 해석, ROI 후보 필터, 면 집합 토글, 면/부품 강조 범위, CAD 면 개수 계산 |
| `frontend/src/features/viewer/three-viewer-canvas.tsx` | 일반/Emitter/Material 클릭에 공통 면 선택 함수 적용, ROI 및 전체 Viewer 강조 정책 통일 |
| `frontend/src/stores/workspace-store.ts` | 일시적인 `selectionKind`, 원자적 `setFaceSelection`, `setRoiCapSelection`, 부품 선택 시 면 선택 초기화 |
| `frontend/src/components/layout/viewer-workspace.tsx` | CAD 면 개수와 부모 소속 배지 표시 |
| `frontend/src/features/viewer/viewer-selection.test.ts` | 면 식별·ROI·부품 선택·절단면·변환 후 ID 유지 회귀 테스트 10개 |
| `frontend/src/features/feature-editors.test.tsx` | 배지 변경 반영, 면 물성 Apply 및 해석 요청까지 면 ID 유지 검증 1개 추가 |
| `frontend/src/stores/workspace-store.test.ts` | CAD case 전환/초기화 시 선택 종류 초기화 검증 1개 추가 |

`selectionKind`는 저장 프로젝트 계약에 포함되지 않는 UI 임시 상태다. `.bitsam` 형식과 기존 저장된 물성/Emitter/Transform 설정은 변경하지 않는다. 기존에 저장된 넓은 면 그룹을 자동 축소하거나 재작성하지도 않는다.

## 자동 검증

| 항목 | 결과 |
| --- | --- |
| 초기 관련 테스트 | 4개 파일, 60개 테스트 통과 |
| 최종 전체 프론트엔드 테스트 | 34개 파일, 221개 테스트 통과, 8.54초 |
| TypeScript 및 프로덕션 빌드 | 통과 |
| 정적 검사 | 오류 0개, 기존 `result-window.tsx`의 `liveResult` effect 의존성 경고 1개 |
| Git 공백 오류 검사 | 통과. Windows 줄바꿈 변환 안내만 출력됨 |

Face Override 통합 테스트는 ROI 후보 `[0, 2]`에서 원본 CAD 면을 클릭했을 때 `[0]`만 선택되고, Apply 후 `targetType=faces`, 해석 요청에서도 `target_type=faces`, `face_indices=[0]`임을 검증한다. 인접면 `[2]`나 ROI 밖의 같은 CAD 면 삼각형 `[1]`로 확대되지 않는다.

ROI 형상 테스트는 선택된 면만 잘라서 렌더링하며, 좌표 이동 후에도 선택 geometry의 `sourceFaceIds`가 `[0, 1]`에 한정되고 새 cap을 만들지 않음을 확인한다. 정확한 경계 절단은 기존 ROI clipping 기능을 사용한다. 이번 작업이 광학 결과 정확도나 GPU 성능을 새로 검증했다는 의미는 아니다.

테스트 환경인 jsdom에는 실제 WebGL context가 없어 일부 기존 테스트에서 Canvas/WebGL 안내가 출력된다. 이에 따라 실제 브라우저 검증을 별도로 수행했다. 프로덕션 빌드의 500 kB 초과 청크 안내도 남아 있으며 이번 선택 기능 수정 범위에서 번들 구조는 재편하지 않았다.

## 실제 브라우저 확인

- 주소: `http://127.0.0.1:8788/`
- 별도 임시 탭에서 `samples/tv_leakage_roi_right_bottom_no_gap.stp`를 Import했다. 사용자 기존 탭은 새로고침하지 않았다.
- ROI: `(10, 8, 0)`부터 `(60, 50, 45)` mm.
- 전체 모델에서 Cover Deco의 원본 CAD 면 하나만 선택/강조되는 것을 확인했다.
- ROI 상태에서 Cover Deco 전면을 클릭한 뒤 인접 측면을 클릭했을 때 이전 면 강조가 사라지고 측면만 강조되었다. 두 경우 모두 `CAD Face · 1`이었다.
- 인공 절단면 클릭 시 `ROI section cap (원본 CAD 면 아님)` 표시와 선택 해제를 확인했다.
- Tree 클릭은 면 선택에서 부품 전체 강조로 전환되었다.
- Material의 면 선택 모드에서 ROI 원본 면 하나를 선택하고 Apply한 후 `CAD 면 1개`인 Surface Property 그룹이 등록되었다.
- 확인한 시험 탭의 브라우저 오류 로그는 없었다. 시험 탭은 확인 후 닫았다.

## 사용자 확인 방법

작업 중인 설정이 있다면 먼저 Save로 `.bitsam`을 저장한다. 기존 주소에서 새로고침하고 저장 파일을 다시 불러온 뒤 ROI 원본 면을 클릭한다. 면 선택은 `CAD Face · 1`, 부품 전체 선택은 `Component`로 구분하면 된다. 새 인공 절단면에 물성을 따로 부여하는 기능은 이번 수정에 포함하지 않는다.

기존 실행 서버를 중단하지 않고 프로덕션 프론트엔드 빌드를 갱신했다. 설치 패키지, 시스템 설정, CUDA 환경, 광학 보정값은 변경하지 않았다.

## 후속 실사용 점검: 하늘색 면을 선택할 수 없는 현상

- 사용자 자료: `simulator 객체 선택 안됨 오류.JPG`.
- 현재 실행 중인 Edge의 `http://127.0.0.1:8788/` 탭에서 같은 위치를 직접 클릭했다. 새로고침하거나 CAD/ROI/물성 설정을 변경하지 않았다.
- 모델: `tv_leakage_roi_right_bottom_no_gap.stp`.
- 화면에 표시된 ROI 좌표: 최소 `(-5.314, 23.000, 0.000)`, 최대 `(40.194, 62.978, 45.000)` mm. 좌표는 UI의 소수점 셋째 자리 표시값이다.
- 표시한 하늘색 면 클릭 결과: `Component 1 · ROI section cap (원본 CAD 면 아님)`, 선택된 부품/원본 CAD 면 없음.
- 바로 옆 검은 원본 면 클릭 결과: `Part · Cover_Deco`, `CAD Face · 1`. 해당 원본 면의 선택은 정상 동작했다.
- 점검 후 하늘색 절단면을 다시 클릭하여 기존 미선택 상태로 되돌렸고, Components 메뉴를 열어둔 원래 화면 상태로 복원했다.

샘플 생성 코드에서 오른쪽 Cover Deco의 측벽은 X=30~60 mm, Z=33~45 mm 구간이다. 현재 ROI의 X 최대값 약 40.194 mm는 이 부품 내부를 가르므로 해당 경계에는 원본 CAD 외부 면이 아니라 새 단면이 생긴다. Viewer는 이 단면을 속이 찬 부품처럼 보이도록 별도 cap geometry로 채운다. 원본 삼각형 ID를 보존하는 잘린 CAD 표면과 달리 cap에는 소속 부품 ID만 있고 원본 CAD 면 ID는 없다.

따라서 이번에 표시한 위치는 원본 면 ID가 소실되어 선택되지 않는 사례가 아니라, 앞선 수정에서 원본 면과 인공 절단면을 분리한 결과다. 인공 절단면을 원본 면으로 치환하거나 소속 부품 전체에 Face Override를 적용하는 우회 처리는 하지 않는다. 표시용 단면을 반사/차폐 면으로 추가하는 것도 실제로 없는 광학 경계를 만드는 별도의 해석 변경이므로 이번 점검에서 수행하지 않았다.

남은 UX 개선 후보는 절단면 클릭 시 Viewer 상단에 짧은 `ROI 절단면 · 표시 전용` 안내를 표시하는 것이다. 현재 하단 상태 표시만으로는 선택 실패처럼 보일 수 있다. 절단면 전용 선택/물성 기능을 추가하려면 시각화용 단면과 실제 가공된 절단면의 해석 계약을 먼저 구분해야 한다. 이번 후속 점검은 브라우저 재현과 코드 검토 및 문서 기록이며, 실행 코드 수정이나 신규 광학 검증은 포함하지 않는다.
