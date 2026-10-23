# BITSAM V2 프로젝트와 Workspace 패키지

## 사용 방법

- CAD를 불러온 상태에서 **Save**를 누르면 활성 Case의 원본 도면·형상·설정·마지막 표시 결과를 하나의 `.bitsam`에 저장한다.
- **Load**에서 새 `.bitsam`을 선택하면 별도 STP 선택 없이 독립적인 Case로 복원한다. 이미 열린 다른 Case는 지우지 않는다.
- 지원 브라우저에서는 저장 위치를 선택한다. 파일은 스트리밍으로 기록하고, 전송이 끝나기 전에 실패하면 쓰기를 중단한다.
- 위치 선택을 지원하지 않는 브라우저는 서버 파일을 직접 다운로드한다. 다운로드 완료 여부는 브라우저에서 확인한다.
- 실행 중인 해석의 중간 상태를 잘못 저장하지 않도록 해당 작업이 실행/대기 중이면 Save/Load를 비활성화한다.
- 활성 Case만 저장하는 기존 V2 파일과 전체 Case를 저장하는 Workspace 파일을 모두 지원한다.

## 파일 구조

확장자는 `.bitsam`, 내부는 표준 ZIP/ZIP64 컨테이너다. 속도와 메모리를 우선하여 현재는 무압축 저장한다. 원본 CAD를 Base64 문자열로 바꾸지 않는다.

```text
simulation.bitsam
├─ manifest.json
├─ project.json
├─ cad/original.stp
├─ scene.bin
└─ trace.bin       # 이미 준비된 경우만 포함
```

| 항목 | 계약 |
|---|---|
| 컨테이너 버전 | `bitsam-project.v2` |
| 형상 캐시 계약 | `bitsam-lossless-scene.v1` |
| MIME | `application/vnd.bitsam+zip` |
| 설정 문서 | `project.json`, 기존 `bitsam-project.v1` 설정 구조 재사용 |
| 원본 형식 | 현재 importer가 지원하는 STEP/STP, OBJ, STL |
| 수치 형식 | little-endian float64 좌표/면적, uint32 삼각형·원본 면 ID |
| 무결성 | manifest의 각 엔트리 크기와 SHA-256 검증 |

`scene.bin`, `trace.bin`은 기존 `BITSAMSC` 블록 형식을 float64로 사용한다. 브라우저 전송은 종전 float32 표시용 경로를 유지하며, 저장된 정밀 좌표를 그 표시용 값으로 대체하지 않는다.

## 다중 Case Workspace 구조

Workspace도 확장자는 `.bitsam`이지만 outer schema는 `bitsam-workspace.v1`이다. 각 Case는 기존 `bitsam-project.v2`를 그대로 사용하므로 단일 Case 형식의 계약과 호환성을 바꾸지 않는다.

```text
workspace.bitsam
├─ manifest.json
├─ workspace.json
├─ cases/
│  ├─ 0001.bitsam
│  └─ 0002.bitsam
└─ assets/
   └─ 0002/
      └─ accessory-0001.step
```

`workspace.json`에는 Workspace 이름, 활성 Case, Case 순서·이름·메모·표시 상태, 주 CAD 표시명, Accessory ID·표시명·표시 상태·asset 관계를 기록한다. 각 Case의 설정과 마지막 결과는 해당 inner V2의 `project.json`에 저장한다. Case별 Leak Preview 차광 구조·허용 영역도 이 설정에 포함되며 Batch는 저장된 각 Case 조건을 그대로 사용한다. outer manifest는 `workspace.json`, 모든 inner V2, 모든 Accessory 원본의 크기와 SHA-256을 검증한다.

Case마다 저장 당시 보이던 주 CAD + Accessory 조합의 표시/정밀 형상 캐시를 inner V2에 보관한다. 동시에 Accessory CAD 원본을 별도 asset으로 보존하므로 복원 후 Components에서 Accessory를 개별 표시/숨김하고 다시 조합할 수 있다. 저장 당시 조합은 CAD를 다시 Tessellation하지 않고 복원한다. Accessory 표시 구성을 바꾸면 해당 원본을 처음 한 번 다시 읽어 새 조합을 만들 수 있다.

import 응답의 `cad.path`는 항상 주 CAD 원본만 가리킨다. Viewer는 이 path 뒤에 `accessory_cads` 중 `visible=true`인 path를 저장 순서대로 붙여 scene을 요청한다. 서버는 이 정확한 path 조합에만 저장 composite cache를 사용하므로 주 CAD에 Accessory geometry가 두 번 합쳐지지 않는다.

## 보존하는 내용

- 원본 CAD 바이트와 변환 전 기본 형상
- Component ID, 원본 CAD Face ID, 삼각형 순서 및 연결 정보
- 부품명, 부품/면 표시 색상, 표시·추적·삭제 상태
- 재료 지정, 면별 Surface property, 사용자 광학 프로파일
- ROI 범위, 이동·회전 규칙, Emitter/Target 및 Receiver
- Ray tracing 설정과 Ray 표시 필터
- Case별 Leak Preview Source·품질·방향·표시 상태와 Allowed Area·Blocker
- 활성 Case 이름/메모
- 저장 당시 분석 결과 전체 JSON: Receiver grid, 지표, 기여도 및 저장된 Ray 경로

이동 규칙은 기본 형상에 다시 적용하므로 이중 이동하지 않는다. ROI 절단면은 표시용으로 재생성하며 물리적 반사면으로 저장하지 않는다.

## 해석 전후 저장의 차이

### 정밀 해석 형상이 준비된 경우

이미 계산한 `trace.bin`을 저장하고 그대로 복원한다. CAD 재변환은 실행하지 않는다. 새 서버 세션 토큰을 발급하고 기존 Ray tracing 경로로 연결한다.

BVH, 실행 중인 Job, Python/OCP 객체, 장치 메모리는 직렬화하지 않는다. 불러온 후 첫 해석에서 현재 형상 조건에 맞는 BVH를 준비한다. 이후 해석은 기존 기하 캐시를 재사용한다. 광원/재료만 바꾸면 이를 재사용할 수 있고 ROI/Transform을 바꾸면 필요한 기하를 재구성한다.

### 정밀 해석 형상이 아직 없는 경우

Save가 오래 걸리지 않도록 저장을 위해 정밀 Tessellation을 강제로 실행하지 않는다. 표시용 형상과 원본 CAD를 저장하고 첫 해석에서만 포함된 원본을 사용해 정밀 형상을 준비한다.

재변환된 표시 형상의 좌표·삼각형·원본 면 ID 등을 원래 캐시와 비교한다. 달라졌다면 기존 면 지정이 잘못 연결되지 않도록 해석을 중단한다. 다른 면에 조용히 연결하거나 표시용 저정밀 mesh로 대신 해석하지 않는다.

## API 및 I/O

- `POST /api/projects/export`: 현재 `scene_token`과 설정 스냅샷으로 패키지를 만든다. 브라우저가 임의 파일 경로를 지정해 패키징하지 않는다.
- `POST /api/projects/export-workspace`: 1~64개 Case를 순서대로 검증하고 하나의 Workspace 패키지를 만든다. 각 Case 입력은 `case_id`, `order`, 이름/메모/표시 상태, `cad_path`, `accessory_paths`, 기존 project 설정이다. Accessory 항목에는 path와 함께 ID, 표시명, 표시 상태를 전달할 수 있다.
- `GET /api/projects/download/{id}`: 생성한 파일을 직접 전송하고 임시 다운로드 파일을 정리한다.
- `POST /api/projects/import`: manifest schema로 단일 V2와 Workspace를 구분한다. Workspace는 모든 outer/inner 항목과 Case fingerprint를 먼저 검증한 뒤 전체를 한 번에 등록한다. 한 Case라도 실패하면 일부 Case를 남기지 않는다. 응답의 각 Case에는 복원된 주 CAD와 `accessory_cads` 관계가 포함된다.
- `/api/scene?cad=...&format=binary`: 복원된 캐시를 기존 Viewer 경로로 전달한다.

추출 데이터는 `_uploads/project_*` 또는 `_uploads/project_workspace_*` 아래에 남는다. Workspace 복원 시 검증이 끝난 inner ZIP은 제거하고, 실제 복원 파일과 Accessory 원본만 유지한다. 같은 세션에서 Case를 전환해 메모리 캐시가 교체되어도 디스크에서 복원할 수 있다. 현재는 중복 패키지의 디스크 캐시 자동 제거/중복 제거를 하지 않는다. 프로그램 종료 후 불필요한 `_uploads`를 정리할 수 있지만 실행 중인 Case가 사용하는 파일은 삭제하지 않는다.

## 안전성과 호환성

- 파일별 크기/SHA-256, 누락·중복 엔트리, 허용 경로, 수치 배열 길이·정밀도·참조 범위를 검증한다.
- Workspace의 모든 활성·비활성 Case는 Component ID·Face 수·경계 상자 기반 `geometry_signature`를 형상 캐시와 대조한다. 개수만 같고 형상이 다른 Case도 전체 복원 전에 거부한다.
- Workspace export는 현재 runtime의 upload 디렉터리 안에 있거나 실제로 이 runtime에서 불러 등록한 CAD만 읽는다. import 파일 안의 조작된 원래 경로 문자열로 로컬 임의 파일을 신뢰 목록에 추가하지 않는다.
- 패키지/해제 데이터 상한은 8 GiB, 프로젝트 JSON은 128 MiB다. 실제 사용 가능한 크기는 PC 메모리/디스크에 따라 더 작을 수 있다.
- 추출은 새 임시 디렉터리에서 진행한다. 실패 시 기존 화면의 프로젝트 상태를 교체하지 않는다.
- 파일 생성은 `.writing` 임시 파일에서 완료 후 교체한다.
- 해석 정밀도나 면 연결을 보장할 수 없는 캐시 버전은 거부한다. 임의 버전의 캐시를 자동으로 재해석하고 면 지정을 추측하지 않는다.
- 기존 순수 JSON `.bitsam V1`은 계속 읽는다. V1에는 CAD가 없으므로 처음에는 원본 CAD를 선택해야 한다. 이후 CAD가 열린 상태에서 Save하면 V2가 된다.
- CAD 없이 예전 설정 파일만 재저장하는 예외 경로는 V1을 유지한다. 원본을 포함했다고 표시하지 않는다.
- 원본 파일 자체가 서버에서 없어졌거나 CAD 세션이 만료되면 CAD 포함 저장을 실패로 처리하고 다시 불러오도록 안내한다.
- 원본 도면을 포함하므로 `.bitsam` 공유는 도면 공유와 동일하다. `.gitignore`에서 제외한다.

## 현재 제외 범위 / 다음 단계

- 여러 Case가 같은 Geometry를 사용하더라도 inner V2와 복원 디렉터리는 현재 Case별로 유지한다. 공통 CAD/scene 자산 중복 제거와 Case 간 BVH 공유는 후속 범위다.
- 저장 당시 Accessory 조합은 캐시로 즉시 복원하지만, 표시 상태를 바꿔 새 조합을 만들 때의 첫 Tessellation은 생략하지 않는다.
- 복원 직후 Viewer Scene은 활성 Case만 요청하며 비활성 Case는 전환 시 지연 로딩한다. 다만 서버 패키지 추출과 Case 간 공통 자산의 실제 공유·중복 제거는 후속 범위다.
- 장기 디스크 캐시 수명/정리 UI
- BVH 구조 자체 저장과 캐시 계약별 마이그레이션
- 실제 대형 사내 도면 및 대상 PC의 I/O·보안 검사 포함 벤치마크
- 현재 선택/팝업/카메라 위치 등 일시적인 조작 상태
- BSDF를 별도 ZIP 엔트리로 저장하는 기능 (현재 지원되는 LT ASCII 원본은
  `project.json`의 `workspace.bsdfAssets`에 텍스트로 포함된다. BSDF가 있으면 내부
  설정은 `bitsam-project.v1-bsdf1`; 패키지 외부 구조는 V2 유지.)
- X_T 파서 지원 추가: 패키징으로 지원 형식이 늘어나지는 않는다.

기존 해석 결과는 그대로 보관하지만, 입력 조건을 바꾼 뒤에는 재해석 결과와 구분해야 한다. 저장된 결과가 바뀐 입력 조건에서 새로 계산된 결과라는 뜻은 아니다.
