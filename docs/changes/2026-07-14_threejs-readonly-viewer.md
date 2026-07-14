# 2026-07-14 Three.js read-only viewer 적용

## 배경
- Canvas 2D 기반 viewer는 CAD 조작, 확대/회전, 표면 시인성, 향후 face picking 확장에 한계가 있었다.
- 전체 프론트엔드 프레임워크 전환 전, 3D viewer만 먼저 Three.js로 교체하여 조작성과 확장성을 검증한다.

## 변경 사항
- `run_web.py`에 Three.js 기반 read-only viewer를 추가했다.
- `web/static/vendor/three.module.min.js`, `web/static/vendor/OrbitControls.js`를 local vendor로 포함했다.
- `/static/` 경로를 Python web server에서 제공하도록 추가했다.
- viewer engine 전환 UI를 추가해 `Three.js`와 기존 `Canvas` fallback을 선택할 수 있게 했다.
- `mesh-scene.v1` payload를 Three.js `BufferGeometry`로 변환해 surface/edge/surface+edge 렌더링을 지원한다.
- 기존 camera preset, fit view, render mode 흐름을 Three.js viewer와 동기화했다.

## 현재 범위
- 지원:
  - CAD mesh 표시
  - orbit/pan/zoom
  - camera preset
  - surface / wireframe / surface+edge 표시
  - Canvas fallback
- 보류:
  - Three.js raycaster 기반 face picking
  - component picking 동기화
  - ROI highlight 이관
  - transform/material overlay 이관
  - ray path / heatmap overlay

## 검증
- `python -m py_compile run_web.py`
- `_build_html_form(...)` HTML 생성 확인
- `/static/vendor/three.module.min.js` local static path 확인
- 추출된 inline JavaScript module/script `node --check` 통과
- `MODULE_3_Z27_HELICAL_GEAR_SAG.stp` 직접 import 확인
  - vertices: `7653`
  - faces: `9486`
  - import note: `STEP parsed with OCP direct reader and tessellated into triangle mesh.`

## 추가 수정
- 증상:
  - STP import와 scene payload 생성은 정상이지만 3D viewer에 모델이 보이지 않았다.
- 원인:
  - Three.js renderer의 buffer size는 크게 설정됐지만 canvas CSS 표시 크기가 초기 `1px` 상태로 남아 있었다.
- 조치:
  - `.three-viewer canvas`에 `width: 100%`, `height: 100%`, `display: block`을 추가했다.
  - `renderer.setSize(w, h, true)`로 canvas style size도 함께 갱신하도록 수정했다.
- 결과:
  - Helical Gear STP가 Three.js viewer에 정상 표시됨을 자동 브라우저 캡처로 확인했다.

## 다음 단계
1. Three.js raycaster로 face/component picking을 연결한다.
2. component tree 선택과 viewer highlight를 동일 ID 계약으로 묶는다.
3. transform preview/applied overlay를 Three.js layer로 옮긴다.
4. Canvas fallback 제거 시점을 별도 판단한다.
