## 2026-07-07: 결과 판독성/차트 개선

### 목적
- 시뮬레이션 결과의 PNG가 단순 바 차트만 보여 이해가 어려운 문제를 줄이기 위해, 리시버별 위험도를 더 빠르게 읽을 수 있도록 렌더링과 보고서를 보강.
- 사용자 질문: `png`의 해석성과 UI 방식(exe vs 브라우저) 방향성 정합.

### 변경사항
- `src/leakage_simulator/render.py`
  - `export_rendering()` 강화
    - 리시버별 `peak_nit` 정렬 표기 + `rays_hit` 라인 차트 + `area_above_threshold` 보조 라인 추가
    - 상위 리시버 라벨과 전반적 scale 조정으로 빠른 위험도 스캔 가능
  - `export_html_report()` 강화
    - `receiver_count`, `hit_receivers`, `hit_ratio`, `max_peak_nit`, `mean_peak_nit`, `max_hit` 카드 추가
    - 판독 가이딩 문구(`Quick interpretation`) 자동 생성
- `README.md`
  - 결과 판독 체크리스트(평균/최대/히트수/누설경로 체크) 정리
  - UI 권장 순서: `HTML 보고서` → `GUI 보강` → `exe 패키징`으로 재정렬

### 검증
- `_tools\python313\python.exe` 실행으로 기본 시뮬레이션 재실행 확인
  - `outputs\run-result-*.html` 생성
  - `outputs\run-result-*.png` 생성(정렬/보조축 포함)
  - `outputs\run-result-*.csv`/`.json` 생성

### 비고
- exe 패키징 자체 구현은 현재 미실행. 다음 스텝에서 PyInstaller 래핑 스크립트 및 GUI 진입점을 별도 설계.
