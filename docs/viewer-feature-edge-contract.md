# Viewer Feature Edge 데이터 계약

## 목적

ROI 정밀도를 높이기 위한 mesh 재분할선과 사용자가 봐야 하는 실제 CAD 형상 경계를 분리한다.

## 필드

`mesh.feature_edge_segments`는 다음 객체의 배열이다.

```json
{
  "start": [0.0, 0.0, 0.0],
  "end": [10.0, 0.0, 0.0],
  "component_id": 0
}
```

- `start`, `end`: 원본 CAD 좌표계의 edge 양 끝점이며 단위는 `mm`이다.
- `component_id`: `components[].component_id`와 동일한 ID이며, 연결할 수 없으면 `null`이다.

## 생성 규칙

- STEP/STP는 adaptive subdivision 전 원본 tessellation mesh에서 계산한다.
- 동일 평면을 구성하는 두 삼각형의 공용 대각선은 제외한다.
- 외곽선, 열린 경계, 설정 각도보다 크게 꺾인 경계는 포함한다.
- 삼각형 winding이 반대여도 같은 평면이면 내부선으로 처리한다.

## Viewer 적용 규칙

- Full CAD View는 `feature_edge_segments`를 우선 사용한다.
- 컴포넌트 전체가 숨겨지면 해당 `component_id`의 edge도 숨긴다.
- ROI View는 선택 범위의 절단 경계가 필요하므로 ROI mesh에서 edge를 다시 계산한다.
- 필드가 없는 이전 payload는 Three.js `EdgesGeometry` 방식으로 자동 fallback한다.
