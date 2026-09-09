# 2026-09-08 main 업데이트 및 렌더링 작업 분리

## 반영 결과

- 원격: `https://github.com/gingerbakery/TV_Leakage_Simulator.git`
- 비교 기준: `ed27db0d267fa969adb3f9d05ab56d698b6d6b14` — 이전 로컬 main
- 반영 완료: `d991858b0626c8bd2f2b713b14d385d82ffab5c1` — 확인 시점의 origin/main
- 신규 반영: 커밋 4개(merge 1개 포함), 37개 파일, 추가 1,010줄 / 삭제 100줄
- `git pull --ff-only origin main` 성공. 병합 충돌 없음.
- `HEAD...origin/main` 차이 0/0 확인. `git diff --check` 통과.

## 작업 폴더 구분

| 용도 | 폴더 | 브랜치 |
| --- | --- | --- |
| 렌더링 개발 | `C:\Users\Administrator\Documents\TV leakage simulator` | `codex/rendering-studio-prototype-20260908` |
| 최신 main | `C:\Users\Administrator\Documents\TV leakage simulator main` | `main` |

두 폴더는 Git worktree로 연결되어 이력을 공유하지만 작업 파일과 index는 분리되어 있다. 기존 폴더의 렌더링 작업을 이번 main에 섞지 않았다.

작업 도중 다른 대화에서 렌더링 브랜치를 생성하고 `d0853843`(`feat: add result-linked 3D leakage rendering prototype`) 커밋을 작성한 것을 확인했다. 이번 pull 작업은 해당 폴더의 파일, staging, 렌더링 커밋을 변경하지 않았다.

렌더링을 계속 개발할 때는 기존 폴더를 사용하고, 최신 main을 검토·수정할 때는 `TV leakage simulator main` 폴더를 사용한다. 렌더링 개발이 끝나면 main 작업 폴더에서 렌더링 브랜치를 병합하거나 PR로 통합할 수 있다. 두 브랜치가 같은 파일을 수정하므로 추후 병합 시 충돌 해결과 통합 테스트가 필요할 수 있다.

새 main 폴더에는 Git 추적 파일만 체크아웃했다. `.venv-gpu`, `_tools`, `node_modules`, frontend build, 업로드 CAD 등 로컬 실행 파일은 자동 복사되지 않는다. 서버 시작·패키지 설치·빌드는 이번 요청에서 수행하지 않았다.

## 변경 내용

| 구분 | 변경 및 영향 |
| --- | --- |
| Receiver 입사광량 | CPU scalar, NumPy batch, GPU resident 경로에서 Receiver 도달 ray의 광량에 입사각 cosine을 다시 곱하던 부분 제거. 수광각 조건은 유지한다. 비스듬한 입사에서 광량을 과소 평가하던 문제를 수정한다. |
| 결과 버전 비교 | 새 결과에 `receiver_flux_contract=geometric_incident_flux_v2` 기록. 필드가 없는 과거 결과는 이전 계산으로 구분하고, 서로 다른 계산 버전 간 개선 점수 비교를 제한한다. |
| GPU 0~1회 반사 | GPU resident 및 wavefront 경로의 기존 `max_depth > 1` 제약 제거. 조건이 맞으면 직접광·1회 반사에도 해당 경로를 사용할 수 있다. 결과 화면에 resident 실행 성공/시도 정보 추가. 실제 속도 향상은 이번 작업에서 측정하지 않았다. |
| 입사각별 반사율 | `Angle-dependent reflectance` ON/OFF 설정 추가. 기본 ON은 기존 입사각 의존 모델, OFF는 지정한 기본 반사율을 사용한다. CPU/GPU 관련 계산 경로에 설정 전달. |
| BITSAM 재저장 | CAD 장면이 현재 API 캐시에 없어도 불러온 프로젝트의 CAD 참조를 유지하면서 수정한 설정을 재저장할 수 있도록 보완. 직렬화 중복 제거 및 저장 대화상자 MIME 지정 수정. CAD 원본 포함 패키지를 구현한 것은 아니다. |
| Case 관리 | 동일 CAD 파일을 다시 import해도 독립 Case 생성. 결과 창이 현재 활성 Case의 결과를 따라가도록 수정. |
| 설정 복사 | 부품의 가벼운 metadata를 재사용해 전체 CAD 장면을 여러 개 동시에 불러오는 부담 완화. 진행 표시와 취소 추가. 예전 Case에 metadata가 없으면 순차적으로 장면을 읽는다. |

## 광량 결과 해석 시 주의점

Receiver 면과의 기하학적 교차 확률에 투영 효과가 이미 포함되므로, 도달한 ray packet에는 광량을 다시 cosine으로 감쇠하지 않는 방식으로 변경되었다. 따라서 과거와 같은 입력에서도 Flux, lux, nit_est가 달라질 수 있다. 입사각 분포가 다르면 변화율도 달라지므로 일괄 배율로 옛 결과를 변환하면 안 된다.

예전에 검토했던 Set Luminance 500 nit → Receiver peak 약 9.427 nit 사례도 현재 계산 기준으로 다시 실행해야 비교할 수 있다. 이번 pull에서 그 사례를 재실행하거나 새 peak 값을 계산하지 않았다. 이 수정 자체가 실측 nit 보정을 완료했다는 뜻은 아니다.

## 커밋 목록

| 커밋 | 작성일 | 내용 |
| --- | --- | --- |
| `18a40c4a` | 2026-09-03 | GPU tracing 및 해석 제어 개선 |
| `0ba923c7` | 2026-09-07 | Receiver cosine 중복 감쇠 제거 |
| `187e0984` | 2026-09-08 | 편집 가능한 프로젝트와 활성 Case 상태 보존 |
| `d991858b` | 2026-09-08 | Receiver 계산 및 프로젝트 수정 브랜치 병합 |

## 이번 작업의 검증 범위

원격 fetch, fast-forward pull, 브랜치/폴더 분리, 원격과의 커밋 일치, 변경 diff와 관련 문서 검토를 완료했다. 최신 main에서 자동 테스트, 실제 UI, 실제 CAD/GPU 해석은 실행하지 않았다. 이 보고서는 코드 변경과 Git 반영 기록이며 GPU 실행·정확도 검증 보고서는 아니다.

이 보고서는 로컬 미커밋 문서다. 이번 작업에서는 commit/push를 수행하지 않았다.
