# 최신 main 병합 및 정확도 회귀 검증

## 병합 대상과 범위

- 현재 작업 보존 커밋: `ccac20b8` — 면 속성 연결, Peak 수렴, 상대 종료 정책 및 검증 자료.
- 통합한 원격 main: `136f3b4e` — 기존 기반 `e887d68c` 이후 원격 전용 15개 커밋.
- 전체 모델 빛샘 미리보기, 차폐물, ROI 해석 형상 절단, 양면 발광, Receiver Peak 위치,
  Case 비교, Excel 보고서/히트맵 내보내기를 보존했다.
- 별도 렌더링 브랜치는 병합하지 않았다. 로컬 전용 백로그와 outputs는 커밋에서 제외했다.
- 사용자 요청 범위는 로컬 커밋·병합·통합 테스트다. 원격 push 및 서버 재시작은 하지 않았다.

## 충돌 조정

### ROI 면 속성

`raytrace_bridge.py`의 원본 면 → 해석 삼각형 연결은 원격의 일대다 목록을 유지한다.
면광원은 절단 후 실제 해석 삼각형 번호를 사용하지만, 광학 면 속성은 원본 면 번호를
유지한다. `OpticalPropertyResolver`가 `source_face_index`로 속성을 조회하기 때문이다.
ROI에서 제거된 면만 속성 목록에서 제외한다.

새 회귀 조건은 원본 면 5가 해석 삼각형 0·1로 절단되도록 구성했다. 원본 번호와
새 번호가 우연히 일치하지 않게 하여, 번호를 잘못 변환해도 테스트가 통과하는 것을
방지했다. 기본 반사율 10%, 면 지정 반사율 90% 조건에서 두 삼각형의 속성, 실제
수광 광속, 캐시 재사용 및 면광원 번호 변환을 함께 확인한다.

### 상태 저장 테스트

같은 위치에 추가된 테스트 두 개를 모두 보존했다.

- 새 작업의 상대 종료 기준 및 기존 파일의 절대 단위 유지.
- Case 이름/설명을 입력 그대로 보존하는 원격 기능.

### 자동 병합으로 발견되지 않는 보고서 단위

Excel의 `min_energy` 단위가 고정 `lm`이어서 상대 기준을 잘못 표현하는 문제가 있었다.
이제 `initial_ray_fraction`은 `ratio`, `absolute_lumen`과 필드가 없는 이전 결과는
`lm/Ray`로 기록한다. 세 조건의 실제 내보내기 호출을 검사하는 회귀 테스트를 추가했다.

## 통합 테스트 결과

| 검사 | 결과 |
|---|---|
| Backend 전체 | 440개 통과, skip 0 |
| 최종 ROI 절단/양면 발광 보완 회귀 | 10개 재검사 통과 |
| Frontend 전체 | 47개 파일, 399개 테스트 통과 |
| TypeScript 타입 검사 | 통과 |
| Production build | 통과 |
| Lint | 오류 없음, 기존 React Hook 의존성 경고 1건 유지 |
| 실제 CPU/GPU 병합 조합 검사 | 30회 통과: CPU 15 + GPU 15 |
| 종료 정책 기존 검증 재실행 | 228회 완료, 정책 판정 및 장치 정합 통과 |
| 동일 조건 CPU/GPU 종료 정책 비교 | 114쌍 통과 |

30회 조합 검사는 Face/Datum 양면 발광, 부품 제외 후 면별 반사율 90%·60%, 상대 종료,
기존 절대 종료 및 ROI 절단 후 90% 면 속성을 포함한다. 각 조건은 같은 프로세스에서
최초 실행 + warm 2회로 반복했다. Ray 수는 8,192개, 광원은 1e-12 lm이며 수광 건수,
Receiver별 광속 및 히트맵 배열을 비교했다. 허용오차는 상대 `1e-9`, 절대 `1e-30 lm`이다.

상대 종료 조합에서는 모든 8,192 Ray가 예상 Receiver에 도달하고, 반사율에 따른 광속이
유지된다. 절대 `1e-9 lm/Ray` 조합 12회는 약한 광원이 문턱에 잘려 수광 0이 되는
이전 정책을 의도적으로 재현한 대조군이다. 이 대조군을 물리 정확도 개선으로 표현하지 않는다.

228회 종료 정책 검증에서도 기존 절대 기준의 광량 실패 10조건은 의도한 음성 대조군이다.
상대 기준·종료 해제의 독립 이론 비교, 반사 상한, 32 seed Roulette 평균, MIS 0가중치
종료가 모두 정책별 판정을 통과했다. 기존 보고서의 범위와 제한을 그대로 적용한다.

## GPU 실행 증거

- 전달 방식: source checkout, 기존 고정 `.venv-gpu` 사용. 설치·환경 재구성 없음.
- 검증 소스: `ccac20b8` + `136f3b4e` 병합 및 이 문서에 기록한 조정.
- 장치: NVIDIA GeForce RTX 3070 / compute capability 8.6.
- Python 3.13.3, NumPy 2.4.6, Numba 0.66.0, llvmlite 0.48.0.
- 새 production preflight: `available`, `strict_float64`, `kernel_executed`,
  `kernel_verified` 모두 true.
- `preflight_scope=production_ray_bvh`, `provider_contract=strict_float64_bvh_v1`.
- 조합 검사: GPU 15회 모두 `gpu_active`, CUDA 성공/시도 15/15.
- 종료 정책 재검사: GPU 114회 모두 `gpu_active`, CUDA 성공/시도 180/180.
- 두 검사 모두 CPU hybrid 0, intersection/resident fallback 0, 실행 실패 사유 없음.
- Emitter 종류: Face, Datum Plane. 동일 샘플 계약 `cpu_gpu_deterministic_batch_v1` 유지.

최종 조합 검사에서 Face 양면 발광/상대 종료의 실행 시간:

| 장치 | 최초 | warm 1 | warm 2 |
|---|---:|---:|---:|
| CPU | 6.614 s | 0.190 s | 0.208 s |
| GPU | 3.553 s | 0.027 s | 0.027 s |

시간은 실행 증거다. 일부 회귀 작업을 병행했으므로 독점 장치 벤치마크나 실제 TV 속도
보장으로 사용하지 않는다. 최초 시간에는 JIT 비용이 포함될 수 있다.

## 재현과 자료

기존 source GPU 환경에서 production preflight를 통과한 뒤:

```powershell
.\.venv-gpu\Scripts\python.exe -m unittest discover -s tests -v
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run lint
.\.venv-gpu\Scripts\python.exe scripts\verify_main_integration.py --output outputs\main_integration_2026-09-21\cpu_gpu.json
.\.venv-gpu\Scripts\python.exe scripts\verify_termination_policy.py --output outputs\main_integration_2026-09-21\termination.json
```

원본 로그와 JSON은 `outputs/main_integration_2026-09-21/`에 로컬 보존한다.
테스트 환경의 Canvas/WebGL 미구현 경고와 build의 큰 청크 경고는 테스트 실패가 아니다.
실제 브라우저 수동 시각 검사는 이번 자동 통합 검증 범위에 포함하지 않는다.

이번 결과는 통합 회귀 통과다. 실제 TV/LT·실측 정합, 복잡 고심도 형상의 미해결
CPU/GPU 분기, 완전한 에너지 ledger까지 해결했다는 의미는 아니다.
