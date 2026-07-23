# Frontend API 타입 및 공통 fetch 계층

## 목적

React 화면과 Viewer 기능을 이전할 때 각 컴포넌트가 Python API의 URL,
요청 형식, 오류 처리를 중복 구현하지 않도록 통신 경계를 먼저 고정한다.

## 적용 내용

- `mesh-scene.v1` scene payload TypeScript 계약 정의
- emitter, receiver, optical profile, transform, ray trace 요청·결과 계약 정의
- queued/running/completed/failed ray trace job을 판별 가능한 union으로 정의
- CAD upload, scene load, 비동기 ray trace, 직접 ray trace, 서버 상태 API client 추가
- JSON 오류와 upload의 일반 텍스트 오류를 `ApiError`로 통합
- `AbortSignal`을 모든 장시간 요청과 polling 요청에 전달 가능하게 구성
- `VITE_API_BASE_URL` 기반 배포 주소 설정 추가
- Vite 개발 서버에서 Python 서버로 전달하는 same-origin proxy 추가
- 공통 fetch와 endpoint query/body 동작 단위 테스트 추가

## 개발 기본값

- React 개발 서버: Vite 기본 주소
- Python API proxy target: `http://127.0.0.1:8787`
- 변경이 필요하면 `frontend/.env.local`에서
  `VITE_API_PROXY_TARGET`을 덮어쓴다.

## 경계

- 기존 `run_web.py` 화면 및 백엔드 동작은 변경하지 않는다.
- 새 React 화면에서 CAD import나 ray trace를 실제로 실행하는 UI 연결은
  상태 관리 단계 이후에 진행한다.
- 이번 단계의 TypeScript 타입은 현재 Python dataclass와 실제
  `run_web.py` wire payload를 기준으로 한다.

## 검증

- `npm run typecheck`
- `npm run lint`
- `npm test`
- `npm run build`
- `npm audit --audit-level=high`
