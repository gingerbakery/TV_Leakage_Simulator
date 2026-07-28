# TV Leakage Simulator Frontend

TV 빛샘 시뮬레이터의 차세대 프론트엔드 작업 공간입니다.

## 현재 구성

- Vite
- React
- TypeScript
- Tailwind CSS
- shadcn/ui (Radix 기반 Nova 프리셋)
- TanStack Query
- Zustand
- Oxlint
- 공통 TypeScript API client

기존 `run_web.py` 화면과는 독립적으로 동작하며, `run_api.py`의 FastAPI
서버를 통해 CAD upload와 scene query,
Component Tree, ROI, Material assignment, Transform rule, Emitter·Receiver,
비동기 Ray tracing job이 Python API와 React 작업 상태에 연결되어 있습니다.
실제 Three.js Viewer는 CAD mesh와 선택, ROI solid, 광원·수광부 overlay를
표시합니다.

## UI 구성 원칙

- `src/index.css`: shadcn 의미 토큰과 시뮬레이터 도메인 토큰
- `src/components/ui/`: 프로젝트가 소유하는 shadcn UI 컴포넌트
- `src/components/common/`: 공통 Dialog와 Component Context Menu
- `src/components/layout/`: workflow sidebar와 Viewer workspace App Shell
- `src/features/`: CAD, ROI, Components, Material, Transform, Viewer,
  Ray tracing 기능 모듈
- `src/lib/utils.ts`: Tailwind 클래스 병합 유틸리티
- 기본 테마: WebView2 시뮬레이터에 맞춘 dark theme
- shadcn CLI는 상시 의존성으로 두지 않고 필요할 때 `npx`로 실행

현재 App Shell은 legacy 화면의 Model import, ROI, Components, Transform,
Material, Ray tracing, Result workflow와 Viewer toolbar 구조를 반영합니다.
CAD를 Import하면 `ScenePayload.components`가 Component Tree와 Viewer
상태 bridge에 표시되며 선택·숨김·Traceability·이름 변경을 공유합니다.
Material과 Transform은 공통 Dialog에서 편집하고 각 관리 패널에서 다시
열거나 제거할 수 있습니다. Ray tracing 패널에서는 CAD/Datum Emitter와
Datum/Current view Receiver를 배치하고 Python 비동기 job 진행률을 확인합니다.

## API 구성

- `src/api/types/`: scene, ray tracing, system API 요청·응답 계약
- `src/api/http.ts`: JSON·텍스트 응답, 오류, 취소를 처리하는 공통 fetch 계층
- `src/api/client.ts`: CAD upload, scene, ray trace, 상태 확인 함수
- 개발 서버의 `/api`, `/health` 요청은 기본적으로 `127.0.0.1:8788`에 프록시
- 다른 Python 서버 주소는 `.env.local`의 `VITE_API_PROXY_TARGET`으로 지정
- Python API 문서: `http://127.0.0.1:8788/api/docs`

## 상태 관리 원칙

- `src/app/`: QueryClient와 애플리케이션 provider
- `src/api/query-options.ts`: scene 및 ray trace server-state query 정책
- `src/api/hooks.ts`: API query/mutation React hook
- `src/stores/`: CAD 작업 세션과 선택·표시 상태를 관리하는 Zustand store
- Component 이름, Material assignment, Transform rule도 scene 범위
  Zustand 상태로 관리
- Python API 응답과 Ray Trace job/result는 Zustand에 복제하지 않고 Query
  cache에서 관리
- scene을 벗어나면 client query cache를 제거해 복귀 시 새 `scene_token` 요청
- queued/running Ray Trace job만 300ms 간격으로 polling

## 개발 명령

터미널 1에서 Python API를 실행합니다.

```powershell
python run_api.py --port 8788 --strict-port
```

터미널 2에서 React 개발 서버를 실행합니다.

```powershell
cd frontend
npm install
npm run dev
```

React 개발 주소는 `http://127.0.0.1:5173/`입니다.

production 단일 서버는 다음과 같이 확인합니다.

```powershell
npm run build
cd ..
python run_web.py --port 8788 --strict-port
```

이 경우 FastAPI가 `frontend/dist`와 `/api`를 동일한
`http://127.0.0.1:8788/`에서 제공합니다.

## 검증 명령

```powershell
npm run typecheck
npm run lint
npm test
npm run build
```
