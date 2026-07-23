# Current View Receiver 카메라 기준 수정 (Web UI v0.9.24)

## 문제

- ROI가 메인 3D View로 승격된 상태에서도 `Current view receiver`가 항상 작은 `Full CAD View`의 카메라를 기준으로 생성되었다.
- 사용자가 보고 있는 방향과 다른 위치 및 방향에 Receiver가 배치될 수 있었다.
- 신규 Current View Receiver의 기본 크기 `100 x 30 mm`가 국부 빛샘 관찰용으로 너무 컸다.

## 변경

- 현재 메인 화면이 `Full CAD View`인지 `ROI View`인지 `primaryViewerKey`로 명시적으로 관리한다.
- Current View Receiver 생성 및 `Update from current view` 실행 시 현재 메인 3D View의 Three.js 카메라를 사용한다.
- 신규 Current View Receiver의 기본 크기를 `30 x 30 mm`로 변경했다.
- Datum/Reference Receiver의 기존 기본 폭 `100 mm`와 저장된 Receiver 크기는 그대로 유지한다.
- 팝업 안내 문구를 특정 Full CAD View가 아닌 `현재 메인 3D View` 기준으로 수정했다.

## 기대 결과

- ROI View가 메인 화면이면 ROI 카메라 시점과 수평축을 따라 Receiver가 생성된다.
- Full CAD View가 메인 화면이면 기존과 같이 Full CAD 카메라를 사용한다.
- 신규 Current View Receiver 면적은 기본 `900 mm²`이다.
