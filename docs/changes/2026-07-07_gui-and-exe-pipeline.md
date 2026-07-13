## 2026-07-07: GUI 및 exe 진입 가시화

### 목적
- 단순 CLI/리포트 외에 사용자 친화적인 UI 실행 경로를 확보하고, 향후 배포형 exe 제공이 가능하도록 빌드 파이프라인 시작.

### 변경사항
- `run_gui.py` 추가
  - Tkinter 기반 최소 GUI 구현
  - CAD 경로, ray 수, max depth, seed, k_abs, k_brdf, 출력 폴더 입력
  - 실행 후 `output` 경로와 `report` 경로 표시, 리포트 자동 열기 옵션 제공
- `build_gui_exe.bat` 추가
  - `_tools\python313` 기준으로 PyInstaller 설치/실행
  - `dist\leakage-leakage-simulator-ui.exe` 생성
- `src/leakage_simulator/render.py`
  - PNG 렌더링(peak, hit, area) 가독성 강화
  - HTML 리포트 판독성 카드/quick interpretation 추가 (이전 변경 분)
- `README.md`
  - GUI 실행 및 exe 빌드 방법 문서화

### 성능/리스크
- `run_gui.py`는 별도 파이썬 GUI 의존성이 없어서 빠른 테스트 가능
- exe 변환은 첫 빌드 시 의존성 설치 시간이 큼(일회성)
- 현재는 GUI에서 공차/Gap 고급 설정은 직접 편집으로 미지원
