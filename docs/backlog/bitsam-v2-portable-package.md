# `.bitsam V2` 휴대형 프로젝트 패키지 백로그

## 상태

- 현재 상태: 단일 Case V2 및 다중 Case Workspace MVP 완료 / 공통 캐시·대형 도면 검증 진행 중
- 최초 등록일: 2026-08-26
- 구현 여부: 모든 Case의 원본 CAD, Accessory 원본·표시 상태, Lossless Scene, 선택적 Trace Geometry, Case별 설정·Leak Preview·마지막 결과의 원자적 저장·복원 완료. 복원 직후 Viewer Scene은 활성 Case만 요청하고 비활성 Case는 전환 시 로딩한다.
- 남은 핵심 범위: 공통 자산 중복 제거, 서버 추출 디렉터리 수명 관리, Case 간 BVH 공유/직렬화, 실제 대형 도면 성능 검증
- 관련 구현 문서: `docs/bitsam-portable-v2.md`, `docs/bitsam-project-format.md`

### 상태 변경 이력

- 2026-08-26: `3D viewer 및 UI 개선` 작업 중 후순위·미구현 백로그로 최초 등록
- 2026-10-23: 단일 Case V2 완료 상태를 반영하고 다중 Case Workspace를 진행 중 범위로 전환
- 2026-10-23: 다중 Case Workspace MVP, Accessory 원본 보존, Case별 Preview/결과 복원, 프론트 활성 Case 우선 로딩과 손상 패키지 검증을 구현

> 이력 보존 메모: 아래 Phase 0~7은 최초 백로그의 전체 로드맵이다. Phase 0~5의 단일 Case 범위는 구현되었으며, 현재 개발 초점은 Phase 6과 Phase 7의 다중 Case 및 검증이다. BVH 구조 자체는 아직 직렬화하지 않고 Trace Geometry를 저장한 뒤 필요 시 BVH를 재구성한다.

## 목표

원본 CAD 파일을 별도로 보관하지 않아도 `.bitsam` 파일 하나로 모델과 시뮬레이션 상태를 복원한다. 단순히 STP/X_T 원본만 포함하는 것이 아니라 렌더링 및 Ray tracing 캐시를 함께 저장하여 CAD 재해석 시간을 최대한 줄인다.

## 사용자 결정 사항

- 패키지 용량은 수백 MB까지 허용한다.
- 용량보다 저장·불러오기 및 CAD 재해석 시간이 더 중요하다.
- 원본 CAD 보안은 사내 사용 조건에서 허용한다.
- 이전 `.bitsam V1` 호환은 권장하지만 최우선 요구사항은 아니다.
- 다중 CAD Case도 추후 포함하되 최초 로딩 시간 증가를 최소화해야 한다.
- 대용량 `.bitsam` 파일은 Git에 업로드하지 않는다.

## 권장 내부 구조

확장자는 `.bitsam`을 유지하고 내부는 ZIP 기반 바이너리 패키지로 구성한다.

```text
project.bitsam
├─ manifest.json
├─ cad/original_model.stp
├─ scene/render_mesh.bin
├─ scene/component_metadata.json
├─ trace/geometry.bin
├─ trace/bvh_cache.bin
├─ state/simulator_state.json
├─ results/result.json
└─ checksums.json
```

`results/result.json`과 대용량 Ray path 데이터는 선택 저장 항목으로 처리한다.

## 구현 원칙

1. Base64 JSON에 CAD나 Mesh를 넣지 않는다.
2. 저장 시 현재 메모리에 존재하는 Mesh와 메타데이터를 재사용하며 CAD를 다시 Tessellation하지 않는다.
3. 대용량 파일은 스트리밍으로 저장하고 불러온다.
4. 저장은 임시 파일에 완료한 후 최종 파일로 교체하여 기존 파일 손상을 방지한다.
5. 정상 캐시가 있으면 STEP/X_T 재해석을 생략한다.
6. 캐시가 호환되지 않으면 Mesh 재구성, 최종적으로 원본 CAD 재해석 순서로 복구한다.
7. GPU 메모리 상태는 저장하지 않는다. GPU에 업로드할 기하 및 BVH 데이터만 저장한다.
8. Component/Face ID, 이름, Material assignment, Face override가 저장 전후 동일하게 유지되어야 한다.
9. ZIP 경로 조작, 비정상 파일 크기, 체크섬 오류를 검증한다.
10. `.bitsam`에 원본 CAD가 포함된다는 사실을 UI에 명시한다.

## 단계별 구현 순서

### Phase 0 — 데이터 계약

- `bitsam-project.v2` Manifest 정의
- 패키지, Mesh, BVH, importer 및 tessellation 버전 필드 정의
- Component/Face ID 보존 규칙 정의
- 기존 JSON 기반 V1과 ZIP 기반 V2 자동 판별 규칙 정의

### Phase 1 — 패키지 기반

- ZIP 기반 저장·불러오기 구현
- 체크섬 및 안전한 파일 추출 구현
- 임시 파일 기반 원자적 저장 구현
- 저장·불러오기 진행률과 실패 메시지 구현
- V1 Reader 유지

### Phase 2 — 원본 CAD 포함

- 현재 활성 CAD의 원본 STP/X_T를 패키지에 스트리밍 저장
- 외부 CAD 파일이 없어도 패키지에서 원본을 복원
- 초기 범위는 활성 CAD Case 한 개로 제한

### Phase 3 — 렌더링 Mesh 캐시

- Vertex, Normal, Index를 바이너리로 저장
- Component/Face ID 및 모델 메타데이터 저장
- 캐시가 유효하면 CAD Tessellation 없이 Three.js Viewer 복원
- Viewer 표시 후 Ray tracing 준비를 분리하여 단계적으로 로딩

### Phase 4 — Ray geometry 및 BVH 캐시

- Ray tracing용 삼각형 배열 저장
- CPU/GPU 공통으로 사용할 수 있는 BVH 입력 또는 BVH 캐시 저장
- GPU 사용 시 장치 메모리 업로드만 다시 수행
- 캐시 버전 불일치 시 Mesh 기반 재생성

### Phase 5 — 전체 시뮬레이션 상태

- ROI
- Component 이름, 색상 및 가시성
- Transform
- Material 및 Face override
- Emitter/Receiver
- Ray tracing 옵션
- 카메라 및 Viewer 상태
- 선택적 분석 결과와 Receiver heatmap

### Phase 6 — 다중 CAD Case

- 단일 Case이며 Accessory가 없으면 기존 단일 V2를 저장
- 복수 Case 또는 Accessory가 있으면 모든 Case를 Workspace로 저장
- 프론트 Viewer는 활성 Case Scene만 먼저 요청하고 비활성 Case는 전환 시 로딩
- Case별 원본 CAD와 캐시를 독립 Entry로 관리하고 Accessory 원본도 별도 보존
- 서버 패키지 추출 자체의 진정한 Lazy Load와 공통 자산 중복 제거는 후속 범위

### Phase 7 — 성능 및 안정성 검증

- 외부 CAD 없이 `.bitsam` 단독 복원
- 저장 전후 Component/Face ID 일치
- ROI, Material, Transform, Emitter, Receiver 동일 복원
- 손상 패키지 및 오래된 캐시 복구 검증
- 실제 사내 TV 도면으로 저장·불러오기 벤치마크

## 성능 목표

실제 수치는 사내 PC에서 검증하며 아래를 초기 목표로 사용한다.

- 캐시 로딩은 원본 STEP import보다 최소 3배 이상 빠를 것
- 중형 모델 첫 Viewer 표시: 5초 이내 목표
- 대형 TV 모델 첫 Viewer 표시: 15초 이내 목표
- Ray tracing 준비 완료: 30초 이내 목표
- 모델 크기와 사내 보안 검사 때문에 목표를 넘길 경우 단계별 시간을 UI에 표시할 것

## 최초 예상 일정

- Phase 0~3, 단일 CAD MVP: 2~4 집중 개발일
- Phase 4~5, Ray/BVH 및 전체 상태 복원: 3~5 집중 개발일
- Phase 6~7, 다중 Case 및 대형 도면 검증: 2~4 집중 개발일
- 전체 예상: 약 7~13 집중 개발일

## 완료 판정 기준

1. 원본 CAD를 이동하거나 삭제한 뒤에도 `.bitsam` 하나로 모델이 열린다.
2. 정상 캐시에서는 CAD Tessellation이 실행되지 않는다.
3. 저장 전후 형상, Component/Face ID 및 시뮬레이션 설정이 동일하다.
4. 캐시가 손상되거나 오래되었을 때 원본 CAD 기반 복구가 가능하다.
5. 대형 파일 저장·불러오기 중 UI가 멈춘 것처럼 보이지 않고 진행 상태가 표시된다.

## 다음 작업

1. 이 문서와 `docs/bitsam-portable-v2.md`, `docs/bitsam-project-format.md`를 전체 확인한다.
2. 단일 Case V2 저장·복원 회귀 테스트를 유지한다.
3. 공통 CAD/Scene/Trace 자산 중복 제거와 참조 수명 관리 정책을 구현한다.
4. Workspace 교체·앱 종료·시작 시 서버 추출 디렉터리를 안전하게 정리한다.
5. Case 간 BVH 공유 또는 버전 고정 BVH 직렬화의 실효성을 측정한다.
6. 실제 사내 대형 도면의 저장·복원 I/O, 보안 검사 영향, 캐시 적중 시간을 검증한다.
7. 기능별 테스트와 성능 측정 결과를 `docs/changes/`에 기록한다.

## 현재 제외 범위

- 파일 암호화 또는 DRM
- GPU 장치 메모리 상태 직렬화
- 대용량 `.bitsam` 파일의 Git 저장
