# 종료 정책과 절단 손실 검증

## 결론

**Ray 수를 늘리거나 광원을 어둡게 했다는 이유만으로 약한 빛이 사라지는 문제를
재현했고, 광원별 초기 Ray 광속 대비 상대 종료 기준을 추가했다.**
기존 파일의 단위를 자동 변경하지 않는다. 상대 종료도 작은 기여를 자르는
근사이며, 반사 횟수 상한 손실이나 LT 정합성까지 해결했다는 의미는 아니다.

- 검사 대상: source checkout `main`, `e887d68c1b90af7534a8926c7e9c8209c17a34a0` + 미커밋 변경.
- 기존 `.venv-gpu`, Python 3.13.3, NumPy 2.4.6, Numba 0.66.0, llvmlite 0.48.0.
- 장치: NVIDIA GeForce RTX 3070, compute capability 8.6.
- 서버 재시작·현재 frontend 배포 교체·커밋·push는 하지 않았다.
- 실행 자료: `outputs/termination_policy_2026-09-21/`의 JSON, CSV, PNG, HTML 및 회귀 로그.

## 1. 재현과 수정 전후

독립 기준: 입력 10 µlm, 반사율 60% 경면 2회, 충분한 Receiver.
이론값은 `10 × 0.6 × 0.6 = 3.6 µlm`이다. 각도 보정과 MIS는 끈다.

| Ray 수 | 기존 절대 기준 1e-9 lm/Ray | 상대 기준 초기 Ray의 1e-9 | 에너지 종료 해제 |
|---:|---:|---:|---:|
| 2,048 | 3.6 µlm | 3.6 µlm | 3.6 µlm |
| 4,096 | **0 µlm** | 3.6 µlm | 3.6 µlm |
| 8,192 | **0 µlm** | 3.6 µlm | 3.6 µlm |

원인은 GPU 오작동이 아니라 CPU/GPU 공통의 절대 lm/Ray 종료 정책이다.
총광속을 동일하게 유지하고 Ray 수를 늘리면 각 Ray가 더 약해져 기존 문턱에
걸린다. 확률 노이즈와 다른 체계적인 절단 편향이다.

## 2. 데이터 계약과 호환성

새 필드 `RayTraceConfig.min_energy_basis`:

| 값 | min_energy 의미 | 실행 시 실제 문턱 |
|---|---|---|
| `absolute_lumen` | lm/Ray, 기존 방식 | min_energy |
| `initial_ray_fraction` | 무차원 비율 0~1 | min_energy × 해당 광원의 유효 총광속 / 해당 광원의 Ray 수 |

- 새 UI 작업은 상대 기준 `1e-9`로 시작한다. 비율이므로 `1e-9 lm`와 다르다.
- 필드가 없는 기존 `.bitsam`/API 입력/Python 설정은 기존 절대 기준을 유지한다.
  기존 해석을 개선하려면 사용자가 `Energy threshold basis`에서 상대 기준을 선택한다.
- `0`은 에너지 종료만 해제한다. 최대 반사 횟수는 그대로 적용된다.
- CPU scalar, CPU batch, GPU resident 및 fallback에 동일하게 환산된 문턱을
  전달한다. 원래 사용자 설정은 결과와 저장 파일에 그대로 남긴다.
- 문턱은 계산 배치 크기가 아니라 **해당 Emitter 전체 Ray 수**로 계산한다.
  밝기가 다른 여러 광원에도 각자의 기준을 사용한다. Luminance/면적당 광속은
  기존 유효 총광속 환산 이후의 값을 사용한다.
- 독립 segment가 각자 동일한 총광속을 대표하고 Ray 수로 가중 누적되는 기존
  auto-convergence 계약에서도 상대 감쇠 문턱은 동일하다. 종료 기준/값/방식/
  반사 상한이 중간에 바뀐 결과는 누적하지 않는다.

## 3. 절단 진단의 범위

Result → Multi-bounce → **종료 정책 · 절단 광량**을 기본 접힘 상태로 추가했다.

1. **에너지 종료 광량 상한**: 실제 threshold 종료 건수 × 실행 광원 중 최대
   lm/Ray 문턱. 각 종료 Ray의 가중 반사광속은 문턱 미만이므로 버린 packet 합의
   보수적 상한이다. 광원별 문턱이 다르면 느슨한 상한이다. 반사 상한 손실은 제외한다.
2. **반사 후 미전파 광량**: 모든 표면의 잠재 반사광속 합 − 실제 다음 경로로
   보낸 광속 합. 에너지·반사 상한·반사 불가 종료를 포함하는 **집계 차 추정치**다.
   종료 사건이 없으면 엄밀히 0이므로 합산 순서 차이를 손실로 표시하지 않는다.
3. 위 값은 실제 Receiver에서 놓친 광량이나 Peak 오차율이 아니다. 수광면에
   도달하지 않을 경로도 포함된다. 작은 총손실도 희미한 빛샘의 상대 오차는 클 수 있다.
4. 합계 차 방식은 부동소수점 상쇄의 영향을 받는다. 극미량 손실의 정밀 에너지
   ledger로 보장하지 않는다. 이벤트별 잔여 광속 직접 누적은 후속 과제다.
5. 실제 MIS 가중 샘플링이나 Roulette를 사용하면 단순 합계 차를 물리 손실로
   표시하지 않는다(`null`). 요청했지만 미지원으로 source 방식으로 실행된 경우는
   실제 실행 경로를 기준으로 판단한다. Roulette 종료를 흡수로 계산하지 않는다.
6. segment 누적에서는 광량 진단은 Ray 수 가중 평균, 건수는 합산한다. 이전
   segment 진단이 없거나 산정 불가이면 이를 0으로 바꾸지 않는다.

## 4. 추가로 수정한 종료 오류

Lambertian bounce MIS에서 Receiver proposal이 반사 반구 밖이면 가중치가
0이 될 수 있다. 이 경우 경로 종료 자체는 정상이다. 그러나 기존 CPU reference,
CPU native와 CUDA는 `ATTEMPTED`만 남겼고, 상세 event tape는 이를 유효한 종료로
인정하지 않아 `surface event status is not a valid planner outcome`로 실패했다.

세 경로 모두 `ATTEMPTED | DISABLED`로 기록하도록 수정했다. Roulette 생존 후
이 경우가 발생하면 생존 bit도 유지한다. 별도의 MIS zero-weight 진단은 유지한다.
검증기를 느슨하게 만들거나 Receiver 광량을 임의 조정하지 않았다.

## 5. 실행 결과

### 검증 설계

- 결정적 광량: 상대 허용오차 `1e-9`, 절대 `1e-30`.
- 광속 1/1e-5/1e-12 lm × Ray 2,048/4,096/8,192 × 기존/상대/해제 × CPU/GPU.
- 60% 20회, 95% 100회, 99.9% 300·1,000회 경면 통로와 상한 sweep.
- 배치 128/2,048/8,192, 독립 segment 광량 및 다중 광원 단위 회귀.
- Roulette는 CPU/GPU 각각 32개의 독립 seed와 사전 지정한 이론 평균의
  `4.5 × 표준오차` 기준. 같은 seed의 CPU/GPU는 독립 표본으로 세지 않는다.
- MIS 0가중치: threshold/Roulette × summary/detailed × CPU/GPU 8조건.

### 집계

| 항목 | 결과 |
|---|---|
| 실제 해석 | 228회 (CPU 114 + GPU 114) |
| 동일 조건 CPU/GPU 수광 광속·반사 도달수 | 114쌍 통과 |
| 상대 기준 및 종료 해제 광속 sweep | 36조건 모두 독립 이론값 일치 |
| 기존 절대 기준 광량 실패 | 10조건에서 의도한 음성 대조 재현; 정상 물리 정확도 통과로 세지 않음 |
| 반사 상한 및 미전파 광량 | 26조건 이론 비교 통과 |
| Roulette 평균 | 4그룹 모두 사전 통계 기준 통과 |
| MIS 0가중치 종료 | 8조건 정상 완료, CPU/GPU 수광 결과 일치 |
| backend 관련 회귀 | 208개 통과, skip 0 |
| frontend 전체 회귀 | 42개 파일, 362개 통과; typecheck 통과 |

초기 회귀에서 진단값의 차이 3건도 수정했다. 미지원 MIS 요청은 실제 source
fallback 여부를 기준으로 진단하고, 종료 사건이 없는 경우에는 합산 오차를
손실로 표시하지 않는다. 숫자 허용오차를 넓히지 않고 재검사했다.

상대 기준 Roulette는 의도적으로 큰 문턱 `0.5`로 생존/가중치 경로를 활성화했다.
32 seed 평균 오차는 **+0.1581%**(0.918 표준오차), 기존 절대 기준 Roulette는
**−0.6771%**(−0.761 표준오차)다. 서로 다른 실제 문턱이므로 두 결과의 분산
차이를 상대 방식 자체의 분산 감소 효과로 해석하면 안 된다. 한 seed 오차는
더 클 수 있으며 모든 모델/Peak의 5% 정확도를 인증한 결과가 아니다.

99.9% 통로에서 정확히 1,000회 반사한 수광 광속은 `0.999^1000 ≈ 0.367695 lm`과
일치한다. 300회가 필요한 통로를 20/100회에서 끊으면 Receiver에 도달하지 못하고,
상한을 300/1,000으로 올리면 `0.999^300 ≈ 0.740707 lm`으로 같다.
이는 통제된 경면 통로 실험이며 실제 TV의 고심도 수렴을 대신하지 않는다.

### GPU 실행 증거

새 production preflight의 `available`, `strict_float64`, `kernel_executed`,
`kernel_verified`가 모두 true이고 scope는 `production_ray_bvh`, contract는
`strict_float64_bvh_v1`이다. 114개 GPU run 모두 `gpu_active`, CUDA 성공/시도
**180/180**, CPU hybrid 0, intersection/resident fallback 0이다.

동일한 8,192 Ray/2회 반사 scene을 동일 프로세스에서 첫 실행 + warm 2회 측정:

| 실행 | 첫 실행 | warm 1 | warm 2 |
|---|---:|---:|---:|
| CPU | 6.718 s | 0.232 s | 0.235 s |
| GPU | 3.742 s | 0.033 s | 0.029 s |

첫 실행에는 JIT 비용이 포함된다. 이는 장치 실행 증거이며 이전 정책 대비
성능 개선 벤치마크나 실제 TV 속도 보장은 아니다. 기존 정책이 빛을 일찍
버렸던 장면은 정확도를 높인 만큼 계산량과 시간이 늘어날 수 있다.

## 6. 사용 권고와 남은 범위

- 이전 파일로 비교 시 절대 기준인지 확인한다. 새 상대 기준으로 바꾼 결과는
  계산 정책이 달라진 것이므로 같은 설정의 과거 결과로 취급하지 않는다.
- 정밀 검증: 같은 상한에서 문턱 감소/0 비교 → 상한 증가 비교 → 독립 seed
  Flux/Peak 비교. 임계값 `0`도 반사 상한 손실은 제거하지 않는다.
- 상대 threshold는 Ray 수/광원 배율에 따른 불합리한 절단 차이를 줄이지만
  모든 약한 경로를 보존하지는 않는다. 필요하면 Roulette를 비교한다.
- 완전한 광속 보존 ledger, MIS/Roulette 복합 모델의 잔여 광량, 고심도 복잡
  형상의 최초 CPU/GPU 분기, 실제 TV/LT·실측 정합은 아직 별도 검증 대상이다.

## 재현

GPU 가이드의 preflight를 통과한 기존 source 환경에서 실행한다.

```powershell
.\.venv-gpu\Scripts\python.exe scripts\verify_termination_policy.py --output outputs\termination_policy_2026-09-21\validation.json
.\.venv-gpu\Scripts\python.exe scripts\report_termination_policy.py outputs\termination_policy_2026-09-21\validation.json
```

그래프 생성 스크립트는 Matplotlib이 있는 분석 환경용이다. UI/서버 실행에
새 의존성을 추가하지 않았다. 내부 단위 검사는 `tests/test_termination_policy.py`,
UI·파일·segment 회귀는 관련 frontend 테스트에서 재현한다.
