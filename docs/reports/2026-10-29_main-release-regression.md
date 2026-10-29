# 최신 main 소스 배포 전 회귀 검증

## 범위와 판정

2026-10-29, Git 소스 사용자 평가용 업데이트를 준비했다. 기준은
`6c9b88f708dafce08a29fb75d6bcbd1e5c42a4e4`, 대상은 최신 main
`f1c31cf241cf8a48e3f66728777c2fafd38eb70a`의 추가 8개 커밋과 아래 회귀 수정이다.
OptiX 및 보류한 PERF-5 후보는 포함하지 않았다. 실행 중인 8788 서버,
다른 개발 worktree, 사용자 CAD/프로젝트는 변경하지 않았다.

자동 검사, 실제 CUDA 확인, 독립 검토 4개 항목이 모두 통과했다. 아래 한계 안에서
**실사용자 평가용 소스 배포 준비 가능**으로 판단했다. 최초 검증은 로컬 커밋
`ae4347d1`까지였으며, 이후 별도 push 요청에 따른 최신 main 통합 검사는 마지막 절에 기록한다.

추가 main 변경은 방향·각도 기반 Leak Preview, 기존 광원/BSDF Preview,
연결된 Case의 Accessory/차광·허용 영역 동기화, 저장 대화상자 호출 순서,
뷰어 단축키·가림 처리·Emitter clipping 등이다. 이번 검증은 이 변경들의 회귀
검사이지 LT 물리 정합성이나 전체 PERF-5 완료 판정이 아니다.

## 발견 및 수정

| 항목 | 재현 및 영향 | 수정과 재검증 |
| --- | --- | --- |
| 경계 광원의 비스듬한 탈출 Ray 누락 | 면 법선으로 epsilon 이동한 광원이 경계 밖에 놓일 때, ray 거리와 공간 허용오차를 비교하여 80/89.9도 광선을 누락 | epsilon 공간 범위 안의 origin만 실제 경계로 clamp한 후 slab 판정. 6방향 × 5각도와 실제 Face/Datum CUDA 검사 추가. 실제 외부 광원 거부 유지 |
| 연결 Accessory 복원 후 결과 소실 | 같은 CAD를 Case별 다른 경로로 추출하여 링크 동기화가 물리 변경으로 인식하고 결과/캐시 연결을 초기화 | 검증된 SHA-256, accessory ID, 확장자, 표시명이 같은 복원 파일은 같은 경로 사용. 응답과 combined cache 등록에 동일 경로 적용. 내용이 다른 파일은 분리 |
| 표시만 변경해도 연결 Case 결과 소실 | 차광/허용 영역의 표시 토글에도 무조건 결과 초기화 | Case별 실제 해석 입력과 적용 옵션을 비교. 표시 변경 및 비적용 조건 변경은 보존하고, 적용 중인 실제 형상 변경은 결과/해석 hash 무효화 |
| 새 composite 테스트 실행 불가 | 공식 GPU 환경에 없는 pytest를 새 테스트가 import하여 원래 166개 실행 중 1개 ERROR | 기존 검증문과 eager/deferred 두 조건을 stdlib unittest/TemporaryDirectory로 유지. 패키지 설치 요구 추가 없음 |

원래 실패 로그를 보존했다. 별도 재현에서는 backend 3개 검사 중 경계 subcase
12개와 공유 경로 1개 실패, frontend 활성/비활성 대상 2개 실패를 확인한 뒤
수정했다. 기존 blocker 링크 테스트는 실제 형상 변경 무효화를 검사하도록
`apply_preview_blockers=true`를 명시했다. 새 true/false 검사가 표시·비적용
변경 보존도 확인하므로 실패 기준을 삭제하거나 완화하지 않았다.

저장 schema, Batch 실행 API/Case 결과 연결, Ray Path 결과 형식은 변경하지 않았다.
복원 시 검증된 CAD 식별 정보만 재사용하며 재해석을 추가하지 않는다.

## 최종 검사

| 검사 | 결과 |
| --- | --- |
| 전체 frontend Vitest | 64 files, 599 tests PASS |
| backend 변경 및 주변 RT/API/BSDF/LT/Workspace 검사 | 170 tests PASS, 0 skip |
| 공식 GPU launcher `run_web_gpu.ps1 -PreflightOnly` | 새 격리 환경 준비, production build, 실제 FP64 BVH preflight PASS |
| 수정 후 `npm run build` | TypeScript + production bundle PASS |
| `npm run lint` | 0 errors, 기존 8 warnings |
| 일반 RT 변경 전후 | 동일 3모델 × CPU/GPU 6조건 입력 및 전체 semantic result exact 일치 |
| 방향 Preview CUDA | 기존 6조건 + 경계 2조건, 조건마다 first + warm 2회, 24회 PASS |
| 저장/복원/재해석 | CPU/GPU 설정·결과·경로 보존, 캐시 사용, 복원 후 광량 변경 및 Case 격리 PASS |
| Batch | 순차 실행/Case별 결과 연결/실패 후 계속/취소/오래된 결과 방지 관련 frontend 검사 PASS |

일반 RT 전후 비교는 `ordinary_revision_probe.py`로 각 revision을 별도 프로세스에서
실행했다. Face 직접광, mixed 2회 반사, 합성 BSDF 각 16,384 rays이며 상세 집계와
최대 50개 경로를 포함한다. 입력 payload/hash도 일치한다. run ID, 실행시간,
성능 메타데이터를 제외하고 `compute_backend` 필드만 정규화했다. Receiver hits는 각각
11,684 / 2 / 16,384이다. **2-hit fixture는 코드 회귀 검사일 뿐 수렴 검증이 아니다.**

Backend 명령은 `python -m unittest`로 다음 모듈들을 실행했다:
`test_escape_collection`, `test_preview_allowed_areas`, `test_composite_scene`,
`test_bitsam_package`, `test_bitsam_workspace_package`, `test_perf5_workspace_compatibility`,
`test_api_fastapi`, `test_raytrace_bridge`, `test_lt_validation_bitsam`,
`test_lt_validation_integration`, `test_lt_validation_api`, `test_raytracer_rt1`,
`test_raytracer_rt2a`, `test_multibounce_rt3`, `test_bsdf_trace_guard`,
`test_bsdf_transport`, `test_lt_validation_gpu_integration` (각 `tests.` 접두사).
`PERF5_GPU_COMPATIBILITY=1`, `PYTHONPATH=src;scripts;tests;.`로 실제 GPU Workspace
검사를 활성화했다. 테스트의 정답/허용오차는 유지했다.

## 실제 GPU 근거

전달 형태는 **source checkout**이며 EXE/ZIP 검증은 아니다. 장치는 RTX 3070,
드라이버 591.59, Toolkit 13.1.1, Python 3.13.3 x64이다. 공식 production preflight:
`available=true`, `strict_float64=true`, `kernel_executed=true`,
`kernel_verified=true`, `preflight_scope=production_ray_bvh`,
`provider_contract=strict_float64_bvh_v1`.

`verify_escape_direction_accuracy.py --rays 16384`는 seed 20261029, depth 3,
6개 enclosure 24×24 grids를 사용한다. 이산 수치 exact, float 상대 1e-9 또는
절대 1e-12, count 보존, sample contract, CUDA 성공 및 30개 이상 선택을 검사한다.
경계 Lambertian 조건은 추가로 **16,384개 모두 선택, 입력 1 lm 보존**을 검사한다.

| 조건 | 선택 Ray | 선택 flux lm | CUDA 성공/시도 | state | first / warm / warm 초 |
| --- | ---: | ---: | --- | --- | --- |
| rear 0 | 16384 | 1 | 2/2 | gpu_active | .270811 / .265754 / .272236 |
| rear 45 | 16384 | 1 | 2/2 | gpu_active | .268255 / .267505 / .274722 |
| rear 89.9 | 15077 | .920227 | 2/2 | gpu_active | .258122 / .263683 / .262481 |
| reflected | 16384 | .6 | 4/4 | gpu_active | .642988 / .648835 / .646265 |
| face source | 16384 | 1 | 2/2 | gpu_active | .280319 / .270346 / .274807 |
| BSDF reflected | 16384 | .3 | 4/4 | gpu_mixed | .973249 / .948239 / .956213 |
| boundary Datum | 16384 | 1 | 2/2 | gpu_active | .278436 / .273874 / .273620 |
| boundary Face | 16384 | 1 | 2/2 | gpu_active | .284996 / .279464 / .283304 |

각 반복의 비교 차이는 0, hybrid count와 intersection fallback count는 0이다.
그러나 BSDF 방향 수집은 `bsdf_reflection_cpu_host_roundtrip`로 **GPU+CPU 혼합**이다.
방향 Preview는 CUDA BVH 교차 계산과 CPU 수송/집계를 사용하므로 전체 GPU
가속이라고 표현하지 않는다. 89.9도 광원의 일부 샘플은 선택 방향 반구 밖이므로
전체 광량과 선택 광량을 구분한다. 위 시간은 실행 증거이며 속도 향상 주장이 아니다.

별도 일반 production analytic/BSDF 저장·복원 검사는 각각 first+warm 2회와
복원/광량 수정 후 실행까지 `gpu_active`, CUDA 1/1, fallback/hybrid 0을 확인했다.
첫/반복 analytic 시간은 .044263/.029339/.035620초, BSDF는
.040764/.034479/.037819초였다. 복원 결과 비교 및 광량 0.5배의 선형 응답도 통과했다.

## 근거와 남은 범위

독립 검토 R1은 실제 제품 문제 3건과 테스트 의존성 문제를 보고했다. 메인이 직접
재현/수정했다. R2는 아래 고정 snapshot과 실행 로그를 검토하여 4개 필수 항목
PASS, 미해결 배포 차단 결함 없음으로 판정했다. 940개 입력 hash는 시작/종료 시
모두 유지됐다. 메인도 이 근거 및 907개 live source와 snapshot의 일치를 확인했다.
검토자는 제품 수정이나 새 GPU 실행 없이 기존 실행 증거를 독립 검토했다.

- 로컬 근거: `outputs/validation/main-release-20261029/`
- R1 source manifest SHA-256: `b23201a25700d471bf4a866cc9e974039f403881e5389780ec0ed867372665a9`
- R2 source+evidence manifest SHA-256: `43ccea0dfbf9f72a732ef3e889b7e0ea329c466d619f6b5ef7b883cffbb8a3a9`, 940 files
- 핵심 근거: `backend-final.log`, `frontend-final.log`, `build-final.log`,
  `lint-final.log`, `escape-gpu-fixed.json`, `ordinary-revision-comparison.json`,
  `ordinary-baseline.json`, `ordinary-current.json`, `reviewer-source.md`, `reviewer-r2.md`.
- 초기 실패/중간 재현 로그도 유지하며 로컬 raw evidence는 Git에 올리지 않는다.

방향별 전체 cell 비교는 고정된 comparator와 실행 결과로 검증했으며 JSON에는
원시 cell 배열 전체가 없다. 전체 source manifest는 실행 후 수집한 것으로,
최종 검사부터 snapshot까지 코드가 바뀌지 않았다는 작업 이력을 함께 사용한다.
일반 RT 전후 검사는 원시 semantic payload 전체를 보존하여 독립 비교했다.

기존 lint 8건, bundle 500KB 경고, JSDOM canvas 미구현 경고는 남아 있다.
Windows inventory helper의 드라이버 N/A 진단은 실제 nvidia-smi/CIM 정보 및
성공한 production preflight와 불일치했다. 설치 문제로 간주하거나 driver를
변경하지 않았으며 helper 자체 진단 개선은 별도 과제로 남긴다.

실제 사용자 GUI/네이티브 저장창, 장시간 대규모 모델, 다중 seed 수렴, LT/실측
정합, 다른 PC의 최초 GPU 설치/ZIP 배포는 이번 통과 범위에 포함하지 않는다.
사용자 평가 시 원본 프로젝트를 보존하고 사본으로 저장·다중 Case·Preview를
확인한다. Git 업데이트 후 기존 실행 프로세스는 그대로이므로, 배포 승인 후
공식 launcher로 최신 frontend를 빌드하여 재실행해야 한다. Git pull은 이미
풀어 둔 EXE/ZIP을 갱신하지 않는다.

## 후속 push 요청과 최신 main 병합 검사 2026-10-30

Push 직전 fetch에서 원격 main이 `1b9bbd97612081c8e8faeecb0b61d360bbf28dfb`
(GPU Observer Preview 및 ROI 해석 범위 적용)로 전진한 것을 확인했다.
`ae4347d1`과 이 커밋을 일반 merge로 통합했다. 5개 공통 변경 파일은 자동 병합됐고
텍스트 충돌은 없었다. 타 개발자의 기능과 기존 회귀 수정 모두 유지했다.
OptiX/보류 실험과 서버 재시작은 여전히 포함하지 않는다.

이번 통합은 메인의 추가 소스 검토 및 재실행 결과이며 앞선 R2 독립 검토가
새 원격 변경까지 검토했다고 주장하지 않는다. 소스 916개 파일의 실행 전후 hash가
일치했다. 입력 manifest SHA-256은
`383a1db7e4390c7df780d2aed63b86a19cc9369f9397d1bd3bf9839fb3da1716`이다.
초기 manifest의 Git-quoted 한글 문서 경로 누락은 제품 변경 없이 수정했으며
불완전한 초기 manifest도 로컬에 남겼다.

| 통합 검사 | 결과 |
| --- | --- |
| Backend 기존 회귀 + Observer/ROI 추가 모듈 | 203 tests PASS, 0 skip |
| 전체 frontend | 65 files, 614 tests PASS |
| TypeScript/production build, lint | PASS; 기존 lint 8 warnings 및 bundle 크기 경고 유지 |
| 기존 Escape 방향 검증 | 8조건 × first/warm/warm, 24회 PASS |
| 새 Observer 실제 CUDA 검증 | 17조건 × first/warm/warm, 51회 PASS |
| ROI crop 실제 CUDA 검증 | Datum/Face/disjoint 3조건 × 3회, 9회 PASS |
| ROI 없는 일반 RT 보존 | 직광/혼합반사/BSDF × CPU/GPU 6조건에서 ae4347d1과 입력/semantic 결과 exact 일치 |

공식 launcher가 준비한 기존 source 환경을 사용했다. 이번 production preflight도
RTX 3070에서 네 boolean 모두 true, `production_ray_bvh`,
`strict_float64_bvh_v1`이었다. GPU Observer는 모든 반복 `gpu_active`,
CPU hybrid/fallback 0, 비교 차이 0이었다. CUDA 성공/시도는 보통 2/2,
1회 반사 3/3, 2회 반사 5/5, BSDF-zero 3/3이었다. 각도/Face/좁은 틈/차폐/
Allowed/앞뒤 발광/반사/BSDF의 양성·음성 조건을 검사했다.
Observer의 표본 수는 lm/nit가 아니며, CAD 교차만 CUDA이고 수송/BSDF/집계는
공통 CPU 코드다. 속도 비율은 이번 배포의 통과 조건이나 성능 향상 주장으로 사용하지 않았다.

| 대표 Observer 장면 | first / warm / warm 초 |
| --- | --- |
| rear 0 | .102229 / .141539 / .100251 |
| Face emitter | .098597 / .134290 / .094644 |
| mirror twice | .205450 / .165167 / .159358 |
| BSDF | .224494 / .221999 / .263612 |

ROI 검사는 각 8,192 rays, seed 17, depth 3, CPU/GPU 이산 exact 및 float
상대 1e-9 또는 절대 1e-12를 요구했다. 모든 반복 Receiver 8,192 hits,
잘린 Datum/Face 4 lm, 겹침을 제거한 disjoint ROI 8 lm로 예상 광량을 보존했다.
모두 `gpu_active`, CUDA 1/1, hybrid/fallback 0이었다. first/warm/warm은
Datum .049870/.049748/.046432초, Face .056140/.047360/.048053초,
disjoint .049810/.047400/.047820초였다.

새 main의 ROI 계약은 광원 절단 면적 비율 및 Receiver 투영 범위를 실제 계산에
적용한다. 따라서 **기존 ROI 해석값과 달라질 수 있으며**, 이는 원격 커밋의
의도된 범위 수정이다. ROI에서는 CPU 수광 판정/집계 경로를 사용하므로 기존
fused GPU 경로와 동일한 속도를 보장하지 않는다. 이번 검사는 합성 조건의
정확도/실행/호환성 검사이지 실사용 대형 ROI 성능이나 LT 정합 인증은 아니다.

추가 근거는 `outputs/validation/main-release-20261029/push-integration/`의
`input-manifest.json`, `backend.log`, `frontend.log`, `build.log`, `lint.log`,
`observer-gpu.json`, `escape-gpu.json`, `roi-gpu.json`, `ordinary-comparison.json`에 있다.
전체 first/warm 시간, 장치 실행 정보, source hash 및 입력은 해당 JSON에 보존했다.
