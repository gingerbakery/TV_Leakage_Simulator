# Frontend Tailwind CSS + shadcn/ui 디자인 기반

## 목적

React 프론트엔드에서 화면과 Viewer 기능을 일관된 시각 언어로 이전할 수
있도록 UI 컴포넌트와 시뮬레이터 전용 디자인 토큰을 구성한다.

## 적용 내용

- Tailwind CSS v4 Vite 플러그인 구성
- shadcn/ui Radix 기반 Nova 프리셋 초기화
- TypeScript `@/*` import alias 구성
- Button, Card, Badge, Separator, Tooltip 컴포넌트 추가
- Geist Variable 폰트와 dark theme 적용
- Viewer, Selection, Direct ray, Reflected ray, Receiver 의미 색상 정의
- 새 React 시작 화면에 Tailwind와 shadcn/ui 실제 적용
- shadcn CLI는 앱 의존성에서 제외하고 생성된 UI 소스만 프로젝트가 소유

## 경계

- 기존 `run_web.py` UI는 변경하지 않는다.
- 기존 Python API와 Three.js Viewer는 아직 새 프론트엔드에 연결하지 않는다.
- 신규 화면은 기능 이전 전까지 독립 개발 서버에서 검증한다.

## 검증

- `npm run typecheck`
- `npm run lint`
- `npm run build`
- `npm audit --audit-level=high`
