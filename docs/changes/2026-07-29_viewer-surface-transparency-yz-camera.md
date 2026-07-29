# 3D Viewer 표면 투명도 및 YZ 카메라 방향 수정

## 요청 배경

- `Surface`, `Surface + Edge` 모드가 불투명으로만 표시되어 모델 내부 구조를 확인하기 어려웠다.
- `YZ` 카메라 프리셋에서 Z축이 화면 위쪽으로 보여, 사용자가 기대하는 YZ 정면 방향과 달랐다.

## 변경 내용

### 표면 투명도

- 3D Viewer 상단에 `Transparency` 게이지를 추가했다.
- 조절 범위는 `0~85%`, 간격은 `5%`다.
- `0%`는 기존과 동일한 완전 불투명 표시다.
- `Surface`, `Surface + Edge`에서만 조절할 수 있고 `Wireframe`에서는 비활성화된다.
- 일반 CAD component 표면뿐 아니라 ROI 절단 표면과 단면 cap에도 같은 투명도를 적용한다.
- 투명도가 적용되면 depth write를 끄고 내부 형상이 겹쳐 보이도록 처리한다.
- Face material override 표면도 선택한 투명도보다 불투명해지지 않도록 연동했다.

### YZ 카메라

- `YZ`는 +X 방향에서 모델을 바라보며 화면 위쪽을 +Y로 고정한다.
- `-YZ`는 -X 방향에서 모델을 바라보며 화면 위쪽을 +Y로 고정한다.
- 따라서 두 YZ 방향 모두 Y축이 수직 위쪽으로 표시된다.

## 검증

- YZ/−YZ 방향 벡터 단위 테스트 통과
- 투명도-불투명도 변환 경계값 단위 테스트 통과
- Surface/Wireframe 모드별 게이지 활성 상태 UI 테스트 통과
- TypeScript typecheck 통과
- oxlint 통과
- Vite production build 통과
