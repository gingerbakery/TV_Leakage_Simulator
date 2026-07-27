# Framework migration v1.0.0

- 날짜: 2026-07-27
- 대상 브랜치: `codex/framework-migration` → `main`

## 변경

- React + TypeScript 프런트엔드와 FastAPI 통합 서버를 정식 기본 UI로 전환했다.
- 화면 배지, API, 프런트엔드 패키지와 데스크톱 배포 버전을 `v1.0.0`으로 통일했다.
- 개발 모드는 Vite `5173`과 FastAPI `8787`, production 모드는 FastAPI 단일 포트로 실행한다.

## 소스 clone 후 실행

```powershell
python -m pip install -r requirements-dev.txt
npm --prefix frontend install
npm --prefix frontend run build
python run_web.py --port 8787 --strict-port
```

브라우저에서 `http://127.0.0.1:8787/`을 연다.
