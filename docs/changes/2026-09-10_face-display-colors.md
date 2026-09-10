# 2026-09-10 · CAD 면별 Display Color

## 요청 및 동작
- Components 색상은 부품 기본색으로 유지한다.
- Surface Property의 CAD Face 목록 오른쪽에 색상 버튼을 추가했다. 클릭하면 기존과 동일한 소형 팔레트 및 사용자 지정 색상 입력이 펼쳐진다.
- 색은 선택 즉시 반영한다. 광학 속성의 Apply 버튼과 별개이며 색상 클릭으로 면 선택이 바뀌지 않는다.
- 개별 면색은 부품색보다 우선한다. 팔레트의 되돌리기는 해당 면만 현재 부품 기본색으로 복원한다.
- ROI 밖 원본 면은 기존 목록 정책대로 비활성이다. ROI에 걸쳐 보이는 원본 면은 원본 면 전체를 동일 색으로 관리한다. 가상 절단면 빗금은 유지한다.

## 데이터와 충돌 방지
- 광학 assignment와 별도 `faceColorOverrides: { componentId, faceIds, color }[]`를 사용한다.
- 동일 부품/색은 묶고 중복 지정은 정리하여 한 삼각형 ID에 여러 표시색이 남지 않도록 한다.
- CAD face/source ID, geometry, normal, Transform 및 광학 속성을 바꾸지 않는다. 결과/진행 중 job도 표시색 변경으로 초기화하지 않는다.
- Full CAD는 기존 surface vertex color를 사용하고, ROI는 기존 단일 skin material에 색을 합성한다. 추가 투명 면을 겹쳐 경계 노이즈를 만들지 않는다.
- Full CAD 색상 버퍼는 재사용하며, 색 변경/복원마다 geometry를 복제하지 않는다.
- `.bitsam` 저장/복원 및 Case 전환에 포함한다. 구버전은 면색 미지정 상태로 호환한다.
- 부품 삭제와 새 CAD로 교체 시 해당 면색을 제거한다. 다른 모델의 삼각형 ID로 오적용되는 것을 막기 위해 Copy Setup/다른 CAD 설정-only 불러오기는 면색을 제외한다.

## 검증
- 자동 테스트: 색상 우선순위, 같은 CAD 면 삼각형 전체 적용, 색상 초기화, Surface Property와 독립성, 결과 유지, Case 분리, Copy Setup 제외, 부품 삭제, 새 CAD, BITSAM 왕복/구버전/잘못된 색 거부.
- 렌더링 단위 테스트: indexed mesh 및 ROI에서 좌표/법선/삼각형 순서/source ID 유지, 원본 CAD 면과 부품 기본색 구분, 선택 하이라이트, 색상 버퍼 재사용.
- 전체 프론트엔드 39개 파일 / 258개 테스트 통과. TypeScript 및 production build 통과.
- lint 오류 없음. 기존 `result-window.tsx`의 `liveResult` hook dependency 경고 1건은 이번 범위 밖이다. jsdom의 canvas 미지원 경고 및 기존 bundle 크기 경고는 남아 있다.
- `http://127.0.0.1:8788/`의 별도 검증 탭에서 `tv_leakage_roi_right_bottom_no_gap.stp`를 import: Cover_Deco 면별 빨강/초록 지정, 선택 해제 후 유지, 부품 기본색을 파랑으로 바꿔도 면색 유지, ROI 생성 후 색 보존 및 ROI 안에서 다시 색 변경 확인. 빗금 절단면은 그대로 유지됨.
- 사용자의 기존 작업 탭은 새로고침하거나 변경하지 않았다. 실제 광학/GPU 해석은 이번 표시 전용 변경에서 실행하지 않았다.

## 실행 반영
- main 작업 폴더의 frontend production build에 반영했다. 사용 중인 모델을 먼저 저장한 뒤 브라우저 새로고침 시 새 UI를 사용할 수 있다.
- 구현 검증 후 사용자가 커밋 및 main push를 요청했다. ROI 렌더링·면별 Surface Property 변경과 함께 이력을 관리한다.
