# CAD Import 단계별 진단 기능

## 배경
- 회사 PC에서 작은 STEP 샘플과 실제 TV STEP 모두 Import가 오래 걸리거나 완료 여부를 판단하기 어려웠다.
- 기존 서버는 `/api/scene` 내부의 OCP 로딩, STEP 파싱, 테셀레이션, ROI 세분화, JSON 생성이 모두 끝날 때까지 단계 정보를 표시하지 않았다.
- 기존 `check_cad_import.py`는 같은 CAD를 `import_geometry`와 `build_scene_payload`에서 두 번 Import해 진단 자체가 실제보다 오래 걸렸다.

## 변경
- 서버 터미널에 파일 업로드, OCP 런타임, STEP read/transfer, tessellation, triangle extraction, feature edge, ROI subdivision, scene array, JSON 직렬화 시간을 출력한다.
- Scene metadata에 `import_timings_sec`를 추가한다.
- UI의 CAD 상태에 scene payload 생성 시간을 표시한다.
- checker가 CAD를 한 번만 Import하도록 수정하고 payload 크기와 전체 시간을 JSON에 기록한다.
- `check_cad_import.bat`가 `.venv`, `_tools`, 시스템 Python 순으로 런타임을 찾도록 수정한다.
- `--fast-import` 및 `LEAKAGE_CAD_FAST_IMPORT=1` 진단 모드를 추가했다.

## 로컬 측정
대상: `samples/tv_leakage_roi_left_bottom_no_gap.stp`

| 모드 | 원본 크기 | OCP raw face | 최종 face | Payload | 총 시간 |
|---|---:|---:|---:|---:|---:|
| 일반 | 약 43KB | 88 | 50,944 | 5.2656MB | 1.6271초 |
| Fast Import | 약 43KB | 88 | 88 | 0.0149MB | 0.7489초 |

## 판단
- 샘플 파일 전송량은 병목이 될 크기가 아니다.
- 현재 ROI 전역 세분화가 샘플 face 수를 약 579배 증가시키며 Viewer payload를 크게 만든다.
- 회사 PC에서 `OCP runtime load`가 수십 초~수분이면 endpoint security의 OCP DLL 검사 가능성이 높다.
- 장기 해결은 coarse preview와 ROI 선택 후 국부 refinement를 분리하는 방식이다.

## 502 Bad Gateway 재현 및 수정
- Vite 개발 UI `5173`은 Python API를 기존 기본값 `8787`로 proxy하고 있었지만, 현재 통합 실행 스크립트와 사내 실행 가이드는 `8788`을 사용하고 있었다.
- API가 `8788`에서 정상 실행 중이고 `8787`이 비어 있는 상태에서 `http://127.0.0.1:5173/health`가 정확히 `502 Bad Gateway`를 반환하는 것을 재현했다.
- Vite proxy, `run_api.py`, `run_web.bat`, 종료 스크립트와 현재 실행 문서를 모두 `8788`로 통일했다.
- 프론트엔드는 502 발생 시 일반적인 `API request failed` 대신 `8788` API 실행 여부를 확인하라는 안내를 표시한다.
