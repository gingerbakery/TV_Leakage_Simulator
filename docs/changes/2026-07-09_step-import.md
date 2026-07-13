# 2026-07-09 STEP/STP 실제 import 반영

## 목적
- 기존에는 `step/stp/x_t` 입력 시 모두 synthetic geometry로 대체되어 실제 CAD 확인이 불가능했다.
- 최소한 `STEP/STP`는 실제 mesh로 읽어 3D viewer와 ROI 선택에 바로 사용할 수 있도록 개선했다.

## 반영 내용
- `cadquery` 설치
- `src/leakage_simulator/importers.py`에 `STEP/STP -> CadQuery import -> tessellate -> TriangleMesh` 경로 추가
- `STEP/STP` 입력 시 synthetic fallback 대신 실제 triangle mesh 반환
- 기본 receiver 후보는 imported mesh의 centroid 분포를 이용한 heuristic으로 생성

## 현재 범위
- 실제 import 지원:
  - `obj`
  - `stl` (ascii)
  - `step`
  - `stp`
- synthetic fallback 유지:
  - `x_t`

## 검증
- 대상 파일: `C:\Users\Administrator\Downloads\MODULE_3_Z27_HELICAL_GEAR_SAG.stp`
- import 결과:
  - `synthetic=False`
  - `faces=9486`
  - `verts=7653`
- 즉, 실제 geometry가 simulator mesh로 변환되는 것 확인
