# 2026-07-07 V1 Environment Bootstrap (python/pip/png)

## 수행 항목
- `C:\Users\Administrator\Documents\TV leakage simulator\_tools\python313` 에 Python 3.13.3 임베디드 런타임 배치
- 사용자/시스템 PATH에 Python 루트 및 Scripts 경로 등록
- `python313._pth`의 `import site` 활성화로 pip/import 패키지 동작 확인
- `pip` 부트스트랩 설치 완료 (`pip 26.1.2`)
- `matplotlib 3.11.0` 설치 완료 (`numpy`, `pyparsing`, `pillow`, `fonttools` 등 의존성 포함)

## 검증
- `python --version` → `Python 3.13.3`
- `python run.py --rays 120 --max-depth 2 --seed 4 --output outputs`
- `outputs\run-result-*.json / csv / heatmap.png` 생성 확인

## 비고
- Windows 권한/세션 갱신 후 PATH 반영이 즉시 보이지 않을 수 있으므로, 기존 터미널을 닫고 새 터미널에서 테스트 권장
