# Portable STEP/X_T Web Package

## Summary
- 회사 PC 테스트용으로 `STEP/STP/X_T` 직접 import가 가능한 포터블 실행 폴더를 생성했다.
- 정적 HTML만으로는 STEP/X_T를 처리할 수 없으므로, embedded Python + local web UI를 더블클릭 실행하는 방식으로 구성했다.

## Package path
- `release/portable_step_web_v0.1`

## Included files
- `launch_portable_step_web.bat`
- `portable_launcher.py`
- `check_step_import_portable.bat`
- `run_web.py`
- `check_cad_import.py`
- `src/...`
- `_tools/python313/...`
- `samples/demo_tv_frame.obj`

## Usage
1. 폴더 전체 복사
2. `launch_portable_step_web.bat` 더블클릭
3. 브라우저 자동 오픈 후 테스트

## Notes
- 이 패키지는 로컬 서버를 내부적으로 사용하지만, 사용자는 BAT 더블클릭만 하면 된다.
- 목적은 설치 없이 회사 PC에서 실제 CAD import와 UI 흐름을 바로 검증하는 것이다.
