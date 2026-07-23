# Wireframe 시인성 및 Component Highlight 개선 (Web UI v0.9.19)

## 요구사항

- 선만 남긴 Wireframe은 구조를 빠르게 파악하기 어려웠다.
- 3D viewer에서 component를 클릭해 하이라이트하면 adaptive mesh의 삼각형 경계가 다시 표시됐다.

## 수정

- Wireframe 전용 `wirefill` mesh를 추가했다.
- `wirefill`은 조명 normal을 사용하지 않는 어두운 단색 `MeshBasicMaterial`로 외형을 보조한다.
- 불투명 depth write를 사용해 같은 위치의 접촉면이 반복 혼합되며 얼룩이 생기지 않도록 했다.
- 실제 선택/picking은 기존 base surface만 사용하므로 wirefill은 클릭 결과에 영향을 주지 않는다.
- component highlight surface에 polygon offset을 적용해 base surface와의 depth 충돌을 줄였다.
- component highlight edge는 고밀도 mesh의 `EdgesGeometry` 대신 subdivision 전 CAD feature edge를 사용한다.

## 적용 범위

- component 선택, material 대상 선택, component transform 적용/preview의 전체 부품 edge에 적용한다.
- local face, emitter face 등 의도적으로 face 경계를 표시해야 하는 기능은 기존 face edge 방식을 유지한다.
