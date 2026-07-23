# TV Leakage Simulator Frontend

TV 빛샘 시뮬레이터의 차세대 프론트엔드 작업 공간입니다.

## 현재 구성

- Vite
- React
- TypeScript
- Tailwind CSS
- shadcn/ui (Radix 기반 Nova 프리셋)
- Oxlint

이 단계에서는 기존 `run_web.py` UI 및 Python API와 독립적으로 동작합니다.
상태 관리, API 연결, Three.js Viewer 이전은 후속 단계에서 추가합니다.

## UI 구성 원칙

- `src/index.css`: shadcn 의미 토큰과 시뮬레이터 도메인 토큰
- `src/components/ui/`: 프로젝트가 소유하는 shadcn UI 컴포넌트
- `src/lib/utils.ts`: Tailwind 클래스 병합 유틸리티
- 기본 테마: WebView2 시뮬레이터에 맞춘 dark theme
- shadcn CLI는 상시 의존성으로 두지 않고 필요할 때 `npx`로 실행

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
npm run build
```
