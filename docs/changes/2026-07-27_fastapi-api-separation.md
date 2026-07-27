# FastAPI API 서버 분리

- 날짜: 2026-07-27
- 대상 브랜치: `codex/framework-migration`
- 로드맵: Step 12

## 전환 전 점검

- React 1~11단계 자동 테스트와 Python 계산 회귀 테스트를 다시 확인했다.
- ROI·Transform·Material·Emitter·Receiver가 공유하는 ray trace bridge
  계약에는 Step 12를 막는 누락이 없었다.
- 실행 문서가 기존 `run_web.py` 단독 UI 중심으로 남아 있고 React 전용
  Python API 진입점이 없다는 구조적 누락을 확인했다.

## 변경

- `run_api.py`에 Uvicorn 기반 FastAPI 실행 진입점을 추가했다.
- `src/leakage_simulator/api/app.py`가 다음 HTTP 계약을 소유한다.
  - `/health`, `/dev-status`, `/_ping`
  - `/api/scene`, `/api/upload`
  - `/api/raytrace/start`, `/api/raytrace/status`
  - `/api/raytrace/direct`
  - `/outputs/{name}`
- `ApiRuntime`으로 scene cache, 비동기 job, upload와 output 파일 상태를
  기존 인라인 UI에서 분리했다.
- FastAPI 자동 문서는 `/api/docs`, OpenAPI JSON은
  `/api/openapi.json`에서 확인할 수 있다.
- `run_web.py`는 13단계 전까지 기존 데스크톱과 인라인 UI 호환을 위해
  유지한다. React 개발 경로는 더 이상 이 파일의 UI 코드에 의존하지 않는다.

## 실행

```powershell
python run_api.py --port 8787 --strict-port
```

별도 터미널에서:

```powershell
cd frontend
npm run dev
```

브라우저는 `http://127.0.0.1:5173/`을 사용한다.
