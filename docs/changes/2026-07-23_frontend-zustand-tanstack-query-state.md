# Frontend Zustand + TanStack Query 상태 계층

## 목적

React 기능 이전 전에 Python 서버 상태와 브라우저 UI 상태의 소유권을
분리한다. 같은 API 응답을 여러 store에 복제해 생기는 동기화 오류를 막고,
CAD 선택과 Ray Trace polling의 생명주기를 한곳에서 관리한다.

## 상태 경계

### TanStack Query

- CAD scene payload와 `scene_token`
- Python 서버 개발 상태
- queued/running/completed/failed Ray Trace job
- Direct Ray Trace 결과
- upload 및 Ray Trace mutation

### Zustand

- 현재 CAD 경로와 표시 이름
- 선택된 face/component ID
- 숨김, 해석 제외, 삭제된 component ID
- 현재 추적 중인 Ray Trace job ID

Emitter, Receiver, Material, Transform 편집 상태는 각 기능을 React로 실제
이전할 때 도메인 slice로 추가한다.

## 적용 내용

- 애플리케이션 공통 `QueryClientProvider` 구성
- API query key factory와 scene/system/ray trace query option 정의
- CAD upload, scene load, 비동기·직접 Ray Trace React hook 정의
- queued/running job에만 300ms polling 적용
- query 취소 신호를 공통 API client까지 전달
- 400/404 응답은 재시도하지 않고 network/5xx 오류만 한 번 재시도
- scene query observer가 사라지면 client query cache를 제거해 token 재사용 방지
- 정렬·중복 제거된 ID 배열을 사용하는 독립 생성 가능 Zustand store 구현
- CAD 변경 시 이전 scene의 선택·표시·job 상태 자동 초기화
- Step 06 안내 화면과 애플리케이션 provider 연결

## 경계

- 기존 `run_web.py` 상태와 화면은 변경하지 않는다.
- API 응답과 대형 mesh를 Zustand에 복제하지 않는다.
- 브라우저 저장소 persistence는 데이터 계약과 복원 정책이 정해진 뒤 추가한다.
- 실제 CAD import 및 Viewer 기능 연결은 후속 기능 이전 단계에서 진행한다.

## 검증

- `npm run typecheck`
- `npm run lint`
- `npm test`
- `npm run build`
- `npm audit --audit-level=high`
- 1280px desktop 및 390px mobile 브라우저 렌더링
- console error 및 horizontal overflow 확인
