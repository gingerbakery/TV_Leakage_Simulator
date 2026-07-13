# 시나리오 템플릿

## 목적
- 기능 검증과 회귀 테스트를 위한 공통 시나리오를 정의한다.

## 시나리오 1: gap 0 vs gap 증가
- 조건:
  - 동일 ROI
  - 동일 emitter
  - 동일 material
- 비교:
  - gap = 0
  - gap = 0.1 mm
  - gap = 0.3 mm
- 기대:
  - gap이 커질수록 leak score 증가

## 시나리오 2: material 변경
- 조건:
  - 동일 geometry
  - 동일 gap
- 비교:
  - black powder aluminum
  - black PC resin
  - white reference
- 기대:
  - 반사율이 높을수록 receiver 누적 광량 증가 가능

## 시나리오 3: component move / tilt
- 조건:
  - 동일 component
- 비교:
  - x/y/z move
  - Rx/Ry/Rz tilt
- 기대:
  - preview와 요약 gap 지표가 일관되게 변함

## 시나리오 4: before / after 설계 비교
- 조건:
  - baseline vs 개선안
- 기대:
  - delta map과 peak/mean nit 비교 가능
