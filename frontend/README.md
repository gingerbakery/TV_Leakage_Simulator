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

기존 `run_web.py` 화면과는 아직 독립적으로 동작하지만, 새 프론트엔드가
Python API를 호출할 수 있는 타입과 통신 계층은 준비되어 있습니다.
Three.js Viewer와 기능별 화면 이전은 후속 단계에서 추가합니다.

## UI 구성 원칙

- `src/index.css`: shadcn 의미 토큰과 시뮬레이터 도메인 토큰
- `src/components/ui/`: 프로젝트가 소유하는 shadcn UI 컴포넌트
- `src/components/common/`: 공통 Dialog와 Component Context Menu
- `src/components/layout/`: workflow sidebar와 Viewer workspace App Shell
- `src/lib/utils.ts`: Tailwind 클래스 병합 유틸리티
- 기본 테마: WebView2 시뮬레이터에 맞춘 dark theme
- shadcn CLI는 상시 의존성으로 두지 않고 필요할 때 `npx`로 실행

현재 App Shell은 legacy 화면의 Model import, ROI, Components, Transform,
Material, Ray tracing, Result workflow와 Viewer toolbar 구조를 반영합니다.
표시되는 Component는 공통 Context Menu와 Dialog 검증용 preview이며 실제
CAD·Component 데이터 연결은 다음 기능 이전 단계에서 진행합니다.

## API 구성

- `src/api/types/`: scene, ray tracing, system API 요청·응답 계약
- `src/api/http.ts`: JSON·텍스트 응답, 오류, 취소를 처리하는 공통 fetch 계층
- `src/api/client.ts`: CAD upload, scene, ray trace, 상태 확인 함수
- 개발 서버의 `/api`, `/health` 요청은 기본적으로 `127.0.0.1:8787`에 프록시
- 다른 Python 서버 주소는 `.env.local`의 `VITE_API_PROXY_TARGET`으로 지정

## 상태 관리 원칙

- `src/app/`: QueryClient와 애플리케이션 provider
- `src/api/query-options.ts`: scene 및 ray trace server-state query 정책
- `src/api/hooks.ts`: API query/mutation React hook
- `src/stores/`: CAD 작업 세션과 선택·표시 상태를 관리하는 Zustand store
- Python API 응답과 Ray Trace 결과는 Zustand에 복제하지 않고 Query cache에서 관리
- scene을 벗어나면 client query cache를 제거해 복귀 시 새 `scene_token` 요청
- queued/running Ray Trace job만 300ms 간격으로 polling

## 개발 명령

```powershell
cd frontend
npm install
npm run dev
```

## 검증 명령

```powershell
npm run typecheck
npm run lint
npm test
npm run build
```
