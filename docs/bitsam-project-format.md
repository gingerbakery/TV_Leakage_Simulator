# BITSAM 시뮬레이션 프로젝트 파일

## 목적

- TV Leakage Simulator에서 구성한 분석 조건을 저장하고 다시 불러오기 위한 전용 프로젝트 파일이다.
- 파일 확장자는 `.bitsam`을 사용한다.
- V1은 빠른 저장·공유를 위해 원본 CAD 형상을 포함하지 않는 경량 JSON 형식이다.

## 기본 정보

| 항목 | 값 |
|---|---|
| 파일 확장자 | `.bitsam` |
| 내부 형식 | UTF-8 JSON |
| MIME 형식 | `application/vnd.bitsam+json` |
| 현재 스키마 | `bitsam-project.v1` |

확장자는 전용 형식임을 분명히 하기 위한 것이며, 내부 데이터는 사람이 읽고 버전 관리하기 쉬운 JSON이다.

## 저장되는 항목

- CAD 참조 정보
  - 원본 파일명과 확장자
  - 면, 정점, 부품 개수
  - 부품 ID, 면 개수, Bounding Box 기반 형상 지문
- Component 상태
  - 표시 제외, Ray tracing 제외, 삭제 처리
  - 사용자가 변경한 부품명
- ROI scope와 절단 범위
- Component/Face별 Material assignment
- Component/Face별 Transform rule
- Emitter와 Receiver 정의
- Ray tracing 설정
- Ray path 표시 필터

## 저장되지 않는 항목

- 원본 STEP/STP/X_T CAD 파일
- 서버가 생성한 임시 업로드 경로
- 현재 선택한 면 또는 부품
- 편집 중인 Preview와 팝업 상태
- 실행 중인 Ray tracing Job ID
- 완료된 Ray tracing 결과

로컬 절대 경로를 저장하지 않으므로 사내 경로 정보가 프로젝트 파일에 포함되지 않는다.

## 저장과 불러오기

### 저장

1. CAD 파일을 Import하고 3D scene 로딩을 완료한다.
2. 화면 상단의 `Save`를 누른다.
3. `<CAD 이름>.bitsam` 파일이 다운로드된다.
4. 원본 CAD 파일과 `.bitsam` 파일을 함께 보관한다.

### 불러오기

1. 화면 상단의 `Load`를 누르고 `.bitsam` 파일을 선택한다.
2. 동일한 CAD가 이미 열려 있으면 형상 지문을 확인한 뒤 즉시 복원한다.
3. CAD가 열려 있지 않으면 안내된 원본 CAD를 `Import CAD`로 불러온다.
4. 3D scene 로딩 후 호환성이 확인되면 설정이 자동 복원된다.

브라우저 보안 정책상 프로젝트 파일이 사용자의 로컬 CAD를 임의로 다시 열 수 없다. 따라서 CAD를 포함하지 않는 V1에서는 사용자가 원본 CAD를 한 번 선택해야 한다.

## 호환성 판정

다음 값이 모두 일치해야 설정을 복원한다.

- Scene 스키마 버전
- 전체 면 개수
- 전체 정점 개수
- 전체 부품 개수
- 부품 ID, 부품별 면 개수와 Bounding Box로 계산한 형상 지문

파일명만 변경되고 형상이 동일하면 경고만 표시하고 복원을 허용한다. 형상 지문이 다르면 잘못된 Face/Component ID 적용을 방지하기 위해 복원을 차단한다.

## 버전 정책

- 스키마 변경 시 `bitsam-project.v2`처럼 버전을 올린다.
- 새 버전 구현 시 이전 버전 마이그레이션 함수를 별도로 제공한다.
- 원본 CAD와 결과까지 포함하는 단일 휴대형 패키지는 V2 이후 별도 옵션으로 검토한다.
