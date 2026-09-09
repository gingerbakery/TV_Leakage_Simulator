# ROI 이후 CAD 면 선택 범위 검토

- 검토일: 2026-09-08
- 상태: 최초 원인 검토 후 수정 및 회귀 검증 완료. [구현 이력](changes/2026-09-08_roi-cad-face-selection.md) 참조.
- 작업 위치: `TV leakage simulator main` 작업 폴더
- 기준: `d991858b0626c8bd2f2b713b14d385d82ffab5c1` 및 로컬 미커밋 Aim/Target 변경 사항
- 아래 내용은 수정 전 원인 검토 기록이다. 후속 수정은 선택 처리에 한정하고 기존 Aim/Target, 반사 횟수 확장 및 별도 렌더링 작업은 보존했다.

## 보고된 증상

ROI로 잘린 모델에서 오른쪽 CAD 면을 클릭했지만 같은 Cover Deco 부품의 여러 면이 함께 강조된다. 화면에는 `Face selected`, 하단에는 `Viewer picking · Component 1 · face selected`가 표시된다. 사용자는 클릭한 CAD 면의 Surface Property만 변경하려 한다.

## 확인된 코드 경로

| 구간 | 현재 동작 | 문제점 |
| --- | --- | --- |
| `frontend/src/features/viewer/three-viewer-canvas.tsx` 일반 클릭 처리 | 부모 `selectedComponentIds`와 클릭한 삼각형 하나의 `selectedFaceIds`를 동시에 기록한다. | CAD 면 선택과 부품 선택이 명확하게 구분되지 않는다. 하나의 CAD 면을 구성하는 모든 삼각형도 선택하지 않는다. |
| 같은 파일의 ROI 선택 오버레이 | Emitter/Material/Datum 면 선택 모드가 아니면 선택된 부품의 ROI 면 전체를 강조한다. | 일반 클릭이 `face selected`라고 표시되어도 부품 전체 강조로 확대된다. 첨부 상태 문구와 일치하는 증상 경로다. |
| `frontend/src/features/viewer/scene-geometry.ts`의 `findCadSurfaceFaceIds` | `face_source_ids`가 같고 같은 부품에 속한 삼각형들을 원본 CAD 면으로 묶는다. 메타데이터가 없으면 기하학적 평면 패치로 대체한다. | 필요한 원본 CAD 면 식별 도구는 이미 있으나 일반 클릭 경로는 사용하지 않는다. |
| 명시적인 Emitter/Material 면 선택 | 위 도구로 원본 CAD 면을 선택한다. 후보는 해당 부품의 전체 삼각형이다. | 일반 클릭과 동작이 다르며 ROI 내부 적용과 전체 원본 면 적용의 범위를 정리해야 한다. |
| `frontend/src/features/roi/roi-clipped-geometry.ts` | 잘린 원본 표면의 각 삼각형에 원본 삼각형 인덱스를 유지한다. 새 절단면은 별도 cap geometry와 부품 ID를 가진다. | 현재 증상만으로 원본 CAD의 부품/면 ID가 손상되었다고 판단할 근거는 없다. 인공 절단면과 원본 면은 별도로 취급해야 한다. |
| `frontend/src/features/materials/material-editor-dialog.tsx` | 부품 적용은 `targetType=part`, 면 적용은 `targetType=faces` 및 `targetFaceIds`를 저장한다. 새 면 그룹 진입 시 기존 선택을 비운다. | 부품 전체 하이라이트만으로 물성이 전체 면에 적용되었다고 단정할 수 없다. Apply 후 저장 데이터 및 실제 해석 입력을 별도로 검증해야 한다. |

여기서 `selectedFaceIds`는 원본 B-rep 면 ID 자체가 아니라 해석 메시에 속한 삼각형 인덱스 집합이다. 원본 CAD 면 선택은 이 집합을 `face_source_ids`로 확장해서 표현한다. ROI 렌더 geometry의 `sourceFaceIds` 역시 원본 삼각형 인덱스이므로 두 메타데이터의 의미를 혼동하지 않아야 한다.

## 권장 수정 기준

1. 일반 3D 클릭의 선택 대상은 삼각형 하나나 부모 부품 전체가 아닌 원본 CAD 면으로 통일한다. 부모 부품 ID는 소속 정보로 유지하되 그것만으로 부품 전체 강조를 켜지 않는다.
2. Component tree의 부품 선택과 Whole Component Transform은 기존처럼 부품 전체 선택/강조를 유지한다. 면 개수로 선택 종류를 추정하지 않고 선택 의도를 구분한다.
3. ROI에서는 선택한 원본 CAD 면의 잘린 표시 영역만 강조한다. 다른 CAD 면, 인접한 면, 반대쪽 면으로 선택을 확대하지 않는다.
4. ROI 내 면 물성 적용 범위와 Emitter 발광 범위는 화면 표시 범위와 일치하도록 검증한다. 삼각형 목록 필터만으로 ROI 경계에서 잘린 삼각형의 부분 적용까지 해결되는 것은 아니므로 기존 ROI 해석 계약과 함께 확인한다.
5. 인공 절단면은 `ROI section cap`으로 구분하고 원본 면이나 부품 전체로 자동 치환하지 않는다. 원본 CAD 면 물성 편집 대상과 섞지 않는다.
6. 상태 문구, 강조 영역, Surface Property Apply 대상이 같은 선택을 뜻하도록 맞춘다. 새로운 긴 도움말이나 메뉴 증설은 필요하지 않다.

## 수정 후 필수 검증

- ROI 없음/있음 각각에서 일반 클릭으로 원본 CAD 면 하나만 선택되는지 확인한다.
- 같은 부품의 인접면, 반대쪽 면 및 곡면이 서로 잘 구분되는지 확인한다.
- Shift 다중 선택, 같은 면 선택 해제, 빈 영역 클릭 해제 및 Tree 부품 선택을 확인한다.
- 면 선택과 부품 전체 Transform이 서로 하이라이트 범위를 오염시키지 않는지 확인한다.
- ROI 인공 절단면 클릭이 원본 면 또는 전체 부품 선택으로 대체되지 않는지 확인한다.
- 면 Surface Property Apply 후 저장되는 면 ID 및 해석용 Face Override 범위가 선택된 원본 면과 ROI 계약에 일치하는지 확인한다.
- Emitter CAD 면 선택, Datum 기준면 선택, Aim/Target 프리뷰 및 `.bitsam` 저장/불러오기에 회귀가 없는지 확인한다.

## 검토 한계

최초 검토는 첨부 화면과 당시 소스의 제어 흐름을 대조한 결과였다. 이후 별도 코너 샘플로 선택, ROI 표시, 물성 Apply 및 해석 요청의 면 ID를 검증했다. 사용자의 해당 실행 세션 자체를 복제하거나 광학 해석을 재실행하지는 않았다. 수정 이후 검증 범위와 결과는 연결된 구현 이력을 따른다. 커밋/푸시는 하지 않았다.
