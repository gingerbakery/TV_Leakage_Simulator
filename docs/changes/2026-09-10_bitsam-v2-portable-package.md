# 2026-09-10 — BITSAM V2 단일 CAD 패키지

## 목표 및 구현 범위

원본 STP를 별도로 선택하지 않고 모델·면 지정·결과를 한 번에 복원하고 재해석한다. 첫 구현은 활성 Case 한 개를 대상으로 한다. 기존 다른 Case는 유지한다.

1. 버전/무결성/면 식별 계약 정의
2. 원본 CAD + 표시 형상 + 설정 + 저장 결과 패키징
3. 준비된 정밀 해석 형상 저장 및 재해석 연결
4. CPU 자동 검증과 샘플 STEP 벤치마크

전체 Case 묶음·BVH 자체 저장·대형 사내 CAD 검증은 후속 단계로 남긴다.

## 주요 변경

- 표준 ZIP/ZIP64 `.bitsam`, 무압축·청크 I/O
- float64 형상 캐시로 좌표와 원본 Face/Component 매핑 보존
- 저장 시 CAD를 다시 변환하지 않음
- 정밀 형상이 없을 때만 첫 해석에서 원본 CAD로 지연 생성
- 새 scene token으로 API에 재등록: 단순 Viewer 복원에 그치지 않음
- Receiver grid/지표/Stored paths 저장 결과 그대로 복원
- 면 속성·면 색상·ROI·Target·Transform·Case 이름/메모 보존
- 기존 JSON V1 읽기 유지 및 V2 저장 경로 연결
- Save/Load 단계 표시, 쓰기 오류 시 저장 성공으로 표시하지 않음
- `*.bitsam` Git 제외

## 검증

- 외부 원본을 삭제한 뒤 패키지만으로 복원
- 좌표 float64, Face/Component 식별 정보 및 저장된 설정/결과 일치
- 새 API runtime에서 CAD importer를 호출하지 않고 캐시 복원/CPU 재해석
- 고정 seed 반사 장면에서 Receiver grid·Stored paths·Receiver 지표 일치
- 광원 power와 면 반사율 변경 후 광량 변화, Transform 변경 후 기하 재구성
- 정밀 형상 미생성 패키지의 첫 해석 1회 준비 및 그 다음 저장의 trace cache 포함
- 메모리 scene cache 교체 후 디스크 복원
- 손상/변조 체크섬, 잘못된 경로/버전/정밀도/인덱스 및 CAD 누락 저장 거부
- HTTP 파일 전송 및 native save 스트리밍/취소/불완전 전송 검사
- 기존 JSON 프로젝트 및 독립 Case 복원 UI 회귀 검사

자동 테스트는 백엔드 45개, 프론트엔드 267개(40개 파일)가 통과했다. TypeScript 검사와 production build도 통과했다. 기존 Canvas jsdom 경고와 큰 bundle 경고는 남아 있다.

GPU 재해석 정합성이나 GPU 성능은 이번 변경에서 검증하지 않았다. 저장 형상은 CPU/GPU와 독립적인 데이터이며 새 세션에서 선택한 연산 장치의 기존 준비·검증 절차를 그대로 거친다.

## 샘플 성능 측정

대상: `samples/tv_leakage_roi_right_bottom_no_gap.stp`, 45,187 bytes, 표시 88 triangles / 정밀 해석 50,944 triangles. CPU, 200 rays, 최대 반사 2회. 이는 패키지 I/O와 재해석 연결 검증이지 대규모 Ray 성능 벤치마크가 아니다.

| 항목 | 측정값 |
|---|---:|
| STEP import | 0.875초 |
| 패키지 저장 | 0.058초 |
| 패키지 크기 | 약 1.72 MB |
| 새 runtime 복원 3회 | 0.048 / 0.039 / 0.037초 |
| 원본의 준비 후 재해석 2회 | 0.071 / 0.210초 |
| 복원 후 BVH 준비 포함 첫 해석 | 1.925 / 1.812 / 1.902초 |
| 복원 후 준비된 상태의 재해석 6회 | 0.066~0.211초 |

좌표·연결·저장 결과·동일 seed 재해석 결과가 일치했다. 복원 과정에서 CAD importer 호출이 없음을 Mock으로 강제 검증했다. 기존 BVH를 저장하지 않으므로 복원 직후 첫 해석은 준비된 상태보다 느리다.

이 수치는 백엔드 기준이며 브라우저 파일 선택·네트워크 전송·화면 그리기를 포함하지 않는다. OS 파일 캐시 및 런타임 초기화의 영향을 받으며 사내 48 MB급 TV 도면의 성능을 보장하지 않는다.

재현: `python scripts/benchmark_bitsam_package.py samples/tv_leakage_roi_right_bottom_no_gap.stp`

세부 측정 JSON은 Git 제외 경로인 `outputs/bitsam-package-benchmark.json`에 생성된다.

## 운영 유의

사용자의 작업 저장 완료 및 재시작 승인을 확인한 뒤, 기존 8788 서버만 종료하고 source checkout의 `run_web_gpu.ps1 -Port 8788`로 재시작했다. 시스템 설정은 변경하지 않았다. 실행 시 Codex PowerShell 7에서 상속된 모듈 경로가 Windows PowerShell과 충돌하여, 새 실행 프로세스에만 Windows PowerShell 모듈 경로를 전달했다.

- `http://127.0.0.1:8788/health` 정상 응답
- 새 프로젝트 export/download/import API 등록 확인
- `/` HTTP 200 및 새 production bundle 확인
- NVIDIA GeForce RTX 3070 production 사전 검사: `available=true`, `strict_float64=true`, `kernel_executed=true`, `kernel_verified=true`, `preflight_scope=production_ray_bvh`, `provider_contract=strict_float64_bvh_v1`

이 사전 검사는 GPU 실행 준비 확인이며 패키지 복원 후 GPU 해석 결과의 정합성 검증을 대체하지 않는다. 기존 JSON `.bitsam`은 CAD를 한 번 연결한 뒤 다시 저장하면 V2 패키지가 된다. 커밋/푸시는 수행하지 않았다.
