# Company PC Test Package

## Why
- 전체 작업 폴더는 업로드 파일 수와 용량이 커서 전달성이 떨어졌다.
- 회사 PC에서 `실행 테스트`만 필요한 상황이라 최소 파일만 묶은 배포 패키지가 필요했다.

## What is included
- `run_web.bat`
- `run_web.py`
- `src\leakage_simulator\...`
- `_tools\python313\...`
- `samples\demo_tv_frame.obj`
- `README.md`
- `COMPANY_PC_QUICK_START.md`

## What is excluded
- `.git`
- `outputs`
- `_uploads`
- `docs`
- `run_web_backup_before_replace.py`
- 개발 보조 스크립트와 캐시 파일

## Packaging principle
- `실제 STEP/STP import`가 깨지지 않도록 embedded Python과 CAD 관련 런타임은 안전하게 유지
- UI/실행 테스트에 불필요한 작업 산출물과 이력 폴더만 제거
