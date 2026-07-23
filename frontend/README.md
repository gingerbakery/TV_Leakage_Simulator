# TV Leakage Simulator Frontend

TV 빛샘 시뮬레이터의 차세대 프론트엔드 작업 공간입니다.

## 현재 구성

- Vite
- React
- TypeScript
- Tailwind CSS
- shadcn/ui (Radix 기반 Nova 프리셋)
- Oxlint
- 공통 TypeScript API client

기존 `run_web.py` 화면과는 아직 독립적으로 동작하지만, 새 프론트엔드가
Python API를 호출할 수 있는 타입과 통신 계층은 준비되어 있습니다.
상태 관리와 Three.js Viewer 이전은 후속 단계에서 추가합니다.

## UI 구성 원칙

- `src/index.css`: shadcn 의미 토큰과 시뮬레이터 도메인 토큰
- `src/components/ui/`: 프로젝트가 소유하는 shadcn UI 컴포넌트
- `src/lib/utils.ts`: Tailwind 클래스 병합 유틸리티
- 기본 테마: WebView2 시뮬레이터에 맞춘 dark theme
- shadcn CLI는 상시 의존성으로 두지 않고 필요할 때 `npx`로 실행

## API 구성

- `src/api/types/`: scene, ray tracing, system API 요청·응답 계약
- `src/api/http.ts`: JSON·텍스트 응답, 오류, 취소를 처리하는 공통 fetch 계층
- `src/api/client.ts`: CAD upload, scene, ray trace, 상태 확인 함수
- 개발 서버의 `/api`, `/health` 요청은 기본적으로 `127.0.0.1:8787`에 프록시
- 다른 Python 서버 주소는 `.env.local`의 `VITE_API_PROXY_TARGET`으로 지정

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
