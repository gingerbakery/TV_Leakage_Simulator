# ROI 경계 렌더링 및 면별 Surface Property 관리

## 요청과 작업 기준
- 기준: `main`의 `0b7a6dfb` 이후 변경. 이전 Target/Aim 위치·Tilt 수정과 구분한다.
- 작업 폴더: `TV leakage simulator main`. 별도 렌더링 worktree는 수정하지 않는다.
- 구현·검증 후 사용자가 면별 표시색 기능까지 확인하고, main 커밋·push를 별도로 요청했다. 면별 표시색 후속 검증은 `2026-09-10_face-display-colors.md`에 기록한다.

## 1. ROI 경계 렌더링
- 원인: ROI 원본 표면 위에 재질, 선택 하이라이트, CAD 면 Emitter 색상용 mesh를 여러 겹 올리고 서로 다른 polygon offset을 적용했다. 접촉면과 절단 경계에서 깊이 충돌 및 색 번짐이 발생할 수 있었다.
- 수정: ROI skin을 한 번만 그리면서 원본 face ID별 material group에 재질·선택·Emitter 색상을 합성한다. 해당 중복 표면 overlay와 강제 depth offset을 제거했다.
- 보존: 삼각형 순서, 원본 face ID, 좌표, 보간 normal, 실제 gap 크기, ROI cap/빗금, picking 계약. 곡면을 다시 flat shading으로 바꾸지 않는다.
- 면 광원의 경계/방향 화살표는 유지한다. 선택 중 화살표는 잘린 영역 중심 및 부품 변환 방향을 따른다.
- 이번 단일 표면 처리의 대상은 메인 ROI skin이다. Full View 미니창의 주황색 ROI 위치 안내 overlay 및 비-ROI 화면의 기존 overlay 구조는 유지한다.

## 2. 면별 속성 관리
1. Components에서 부품 아래 **Surface property** 아이콘을 누른다.
2. CAD 면 목록의 체크박스 또는 **뷰어에서 CAD Face 선택**으로 대상을 선택한다.
3. 마감을 선택하고 **Apply to selected faces**를 누른다.
4. **Use part default**는 선택한 면만 기본값 상속으로 복원한다.

- Viewer 우클릭 메뉴에도 Surface Property를 추가했다. 면 선택 상태를 부품 전체 선택으로 자동 변환하지 않는다.
- 같은 부품의 여러 면을 함께 지정할 수 있다. 다른 부품으로 armed picking이 넘어가지 않도록 검사한다.
- 목록은 CAD 원본 면 단위이며, ROI의 표시용 빗금 cap은 관리 대상이 아니다. ROI 밖 면은 비활성이다.
- ROI에 일부만 남은 CAD 면을 적용하면 같은 원본 CAD 면 전체에 마감을 저장한다. 다른 CAD 면은 바뀌지 않는다. 기존 Material 창의 면 그룹 편집도 동일하다.
- Base Material은 부품에서 상속하고, Surface Property만 면별로 덮어쓴다. 해석 요청에서 최신 부품 Base Material과 면 마감을 조합한다.
- 중복 face assignment를 정리하여 재지정·초기화 후 예전 override가 다시 드러나는 상황을 방지한다.
- 작은 화면의 새 팝업은 화면 안에 배치하고, 내용은 내부 세로 스크롤로 처리한다. 도움말은 제목의 (?)에 유지한다.

## 3. 실제 저장·불러오기에서 발견한 연관 오류
- 좌표로 만든 ROI는 `plane = xyz`를 저장하지만 `.bitsam` 검사기는 `xy/yz/zx`만 허용했다.
- 실제 테스트 파일이 “필수 데이터가 없거나 손상” 오류로 로드되지 않는 것을 재현했다.
- `xyz`를 허용하고 잘못된 plane 문자열은 계속 거부하는 회귀 테스트를 추가했다.
- 동일 파일을 다시 읽고 원래 STEP를 Import하여 ROI 및 개별 면 마감이 복원되는 것을 확인했다. CAD 포함 저장으로 형식을 바꾼 것은 아니다.

## 검증
- 프런트엔드 전체 테스트: 37 파일, 250개 테스트 통과.
- 백엔드 CPU 테스트: `python -m unittest tests.test_optics_rt2b tests.test_raytrace_bridge`, 21개 통과.
- TypeScript/production build 통과. lint 오류 0개; 기존 `result-window.tsx` effect dependency 경고 1개는 별도 사안으로 유지.
- jsdom의 Canvas/WebGL 미구현 경고, 기존 대형 Vite chunk 경고가 있다. 실제 브라우저 표시 검증을 별도로 수행했다.
- 실제 브라우저: `http://127.0.0.1:8788/`, `tv_leakage_roi_right_bottom_no_gap.stp`, 좌표 ROI `(0,10,-1) ~ (60,45,50)`.
- Cover_Deco의 Face 1에 High-gloss, Face 2에 Matte를 별도 적용하고 Face 2만 기본값으로 복원했다. Face 1 지정은 유지되었다.
- ROI 원본 면 좌클릭 → 같은 위치 우클릭 → Surface Property에서 해당 단일 CAD 면 선택을 확인했다.
- `.bitsam` 실제 저장 → 페이지 갱신 → Load → 동일 STEP Import 후 Face 1의 High-gloss 지정 및 ROI 복원을 확인했다.
- 이번 변경에서 실제 CUDA kernel 실행은 검증하지 않았다. 프런트엔드 GPU 관련 테스트는 mock 기반이며 GPU 성능·실행 성공을 의미하지 않는다.

## 개발 경계
- Ray tracing 광학 우선순위와 CPU/GPU 계산 알고리즘은 변경하지 않았다. 공통 요청 조립에서 face base material 상속을 보완했다.
- 광학 물성은 단순 표시색이 아니다. 화면 material은 확인용이고 해석은 `optical_profiles/optical_assignments`를 사용한다.
- 대형 실도면·모든 투명도와 시점에서의 시인성은 사용자 후속 확인이 필요하다. 이번 검증만으로 모든 CAD의 경계 노이즈가 없어졌다고 일반화하지 않는다.

## 후속 확인: Emitter와 Target 교차 제한 유지
- 사용자가 보고한 설정: Emitter 중심 `(30,70,10)`, X tilt 45°, 크기 10×10mm; Target 중심 `(30,70,10)`, tilt 0°, 크기 20×20mm.
- 같은 중심을 공유하는 두 영역의 실제 교차이며, 단순히 중심 사이 거리가 작은 경우와 구분한다.
- 현재 `validate_emitter_aim`의 교차·접촉/수치 여유 거리 검사를 유지하기로 확인했다. 표시된 0.0002mm는 수치 안전 거리이며 물리적 한계값은 아니다.
- 겹침 허용은 이번 범위에 추가하지 않았다. 기존 Target 검사 및 Tilt 복원 수정은 기준 커밋 `0b7a6dfb`에 포함되어 있다.
