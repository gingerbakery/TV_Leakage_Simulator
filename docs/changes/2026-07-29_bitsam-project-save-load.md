# BITSAM 프로젝트 Save/Load

## 변경 목적

- 사용자가 구성한 ROI, Transform, Material, Emitter, Receiver와 Ray tracing 조건을 파일로 저장하고 다시 불러올 수 있도록 한다.
- 기존 검토명 `.tlsim` 대신 제품 전용 확장자 `.bitsam`을 사용한다.

## 구현 내용

- 화면 상단에 `Save`, `Load` 버튼 추가
- `.bitsam` V1 직렬화·역직렬화 구현
- CAD 원본 경로를 제외한 경량 JSON 저장
- CAD 면·정점·부품 수와 부품 Bounding Box 기반 형상 지문 생성
- 현재 CAD와 저장 CAD가 일치할 때만 Workspace 상태 복원
- CAD가 아직 없으면 프로젝트를 대기 상태로 유지하고, 해당 CAD Import 완료 후 자동 복원
- 손상 파일, 다른 스키마 버전, 다른 CAD 형상에 대한 오류 안내
- 복원 시 선택 상태, Preview, 실행 중 Job ID를 초기화하여 오래된 편집·결과 상태가 섞이지 않도록 처리

## 저장 범위

- Component 표시/해석/삭제 상태와 이름
- ROI scope
- Material assignment
- Transform rule
- Emitter와 Receiver
- Ray tracing 설정
- Ray path 표시 필터

## 제외 범위

- 원본 CAD 바이너리
- 로컬 절대 경로
- 현재 선택과 팝업 Preview
- Ray tracing 실행 결과

## 검증

- `.bitsam` 직렬화 후 동일 상태 복원
- 로컬 CAD 경로 미포함 확인
- 동일 형상 및 파일명 변경 형상 호환성 판정
- 형상 불일치 차단
- 손상 JSON 및 미지원 스키마 차단
- Workspace 복원 시 transient 상태 초기화
