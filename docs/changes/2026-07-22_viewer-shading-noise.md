# 3D Viewer 셰이딩·투명 노이즈 개선 (Web UI v0.9.18)

## 증상

- `Surface + Edge`에서 직각으로 꺾이는 접촉 경계에 반복적인 어두운 삼각 패턴이 표시됐다.
- `Wireframe`에서 내부에 얼룩이나 모아레처럼 보이는 투명 면 노이즈가 표시됐다.

## 원인

- STEP 조립체의 접촉면은 같은 위치에 겹칠 수 있으며, 서로 반대이거나 크게 다른 normal을 가진다.
- 하나의 indexed mesh에서 공유 vertex normal을 평균화하면 hard edge 주변에 잘못된 밝기 보간이 발생한다.
- 기존 Wireframe은 실제 선만 표시한 것이 아니라 10% 투명 surface도 함께 그려 겹친 면의 depth 충돌을 노출했다.

## 수정

- 기본 CAD surface와 transform overlay에 `flatShading`을 적용해 triangle 내부에서 normal을 보간하지 않는다.
- Wireframe은 투명 surface를 완전히 숨기고 실제 CAD feature edge만 표시한다.
- Surface 모드는 반투명 상태에서 depth buffer 기록을 끄고, Surface + Edge는 완전 불투명 상태로 렌더링한다.

## 영향 범위

- 렌더링 표현만 변경하며 CAD 좌표, face ID, ROI, transform, ray tracing 계산에는 영향을 주지 않는다.
