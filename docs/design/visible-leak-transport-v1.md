# 관찰 휘도 수송 계약 — 설계 v1

상태: 제품 코드에 아직 적용하지 않은 CPU 기준 구현 명세. 새로운 물리 재질을 검증 없이 기존 모델에 덮어쓰지 않는다.

## 단위와 광원

좌표 입력은 mm이나 면적 PDF는 m⁻², 거리 항은 m로 변환한다. 광속 Φ는 lm, 휘도 L은 cd/m²다. 정규화된 면적 분포 pA와 방출 입체각 분포 pΩ에 대해 한쪽 면의 방출은 다음과 같다.

`Le(x,ω) = Φ pA(x) pΩ(ω) / abs(n·ω)`

균일 면적 A와 Lambertian 배광 pΩ=cosθ/π이면 `Le=Φ/(πA)`다. 균일 반구 pΩ=1/(2π)는 일정 휘도가 아니며 grazing에서 별도 수치 처리가 필요하다. 이 특성을 임의 clamp로 바꾸고 광속을 보존했다고 표시하지 않는다. 양면/4π 방출은 각 면의 광속 분할과 방향 지지를 명시한다. 비 Lambertian에서 ‘휘도 설정’을 사용한다면 정면 기준 등 별도 계약이 필요하며 무조건 Φ=πLA로 변환하지 않는다.

## 방향, 추정기와 MIS

wi는 표면에서 광원 쪽으로, wo는 표면에서 관찰자 쪽으로 향한다. 기존 forward API에 전달할 실제 입사 진행 방향은 -wi다.

`Lo(wo) = Le(wo) + ∫ f_forward(-wi,wo) Li(wi) abs(n·wi) dωi`

카메라 경로의 연속 사건 throughput은 `f_forward(-wi,wo) abs(n·wi) / q(wi)`를 곱한다. 측정 BSDF가 상반적이라고 가정해 입력/출력 축을 뒤집지 않는다. proposal은 평가 가능한 PDF와 필요한 방향의 지지를 가져야 한다. 최초 기준 구현은 cosine/균일 혼합 proposal도 가능하다.

면광원 NEE의 입체각 PDF는 `p_select * pA * distance_m² / abs(n_light·(-wi))`다. 광원 샘플과 연속 BSDF 샘플은 MIS로 결합한다. 직접 또는 BSDF 경로에서 광원을 맞힐 때도 대응 가중치를 적용해 이중 합산하지 않는다. 이상 정반사 δ 사건은 연속 BRDF/PDF와 분리하고 δ 이후 광원 도달을 올바르게 처리한다. 알려진 경로 기여만 더하는 deterministic shortcut은 영상 적분의 대체물이 아니다.

## 기존 BSDF/분석 재질 호환

현재 테이블 모델은 전체 반사율 R의 보간과 정규화된 각도 shape의 혼합을 구분한다. 연속 성분의 계약은 `f = R_interp * Σ a_k relative_k(wo)/Z_k`, `Z_k=∫relative_k cos_out dω`다. bare relative 값 또는 `Σ a_k R_k relative_k/Z_k`로 몰래 바꾸지 않는다. eval/pdf 인터페이스를 forward와 camera가 공유하고 정규화 및 보간 회귀 검사를 둔다.

현재 Gaussian 샘플러는 단순 Phong이 아니다. θ=σ sqrt(-2 log U), 균일 φ를 사용하고 θ≥π/2 또는 표면 밖 조건을 거절하며 32회 실패하면 축 방향으로 되돌아간다. 따라서 정확히 호환하려면 연속 성분과 fallback δ 질량이 모두 필요하다.

원시 입체각 밀도는 `q0(θ)=θ exp(-θ²/(2σ²))/(2πσ² sinθ)`다. 유효 영역의 적분을 A라 하면 32회 시도의 연속 밀도는 `q0 * (1-(1-A)^32)/A`, fallback 질량은 `(1-A)^32`다. A=0의 극한은 연속 0, fallback 1로 처리한다. A는 축-법선 각 γ와 각 θ에서 `cosγ cosθ + sinγ sinθ cosφ > epsilon`인 φ 비율을 적분해 구할 수 있다. 축이 법선인 특수 경우에는 Rayleigh 누적분포와 독립 비교한다.

혼합 재질은 이 성분과 cosine diffuse를 기존 비율로 결합한다. 작은 σ의 정반사 전환도 유지한다. 기존 flux kernel K로부터 연속 BRDF를 정의하면 `f=R K/cos_out`이며 임의의 대칭 BRDF로 가정할 수 없다. grazing 처리와 에너지 적분을 검증해야 한다. 정확한 eval/pdf를 구현할 수 없으면 해당 재질을 미지원으로 표시하거나 양쪽 엔진을 새 버전으로 함께 변경하고 재검증한다. Lambertian으로 조용히 대체하지 않는다.

## 차폐와 불확실성

각 경로 구간은 양 끝의 epsilon 규칙을 일관되게 적용하고 유효 구간 안의 다른 불투명 면을 검사한다. epsilon은 장면 크기와 최소 gap 대비 검증하며 큰 고정 offset으로 작은 틈을 제거하거나 벽을 뛰어넘지 않는다. 양면 충돌 여부와 뒤집힌 법선도 검사한다.

샘플 분산은 재질 보정·기하 오차·탐색 누락의 신뢰도를 대표하지 않는다. 0 hit는 드문 경로가 없다는 증거가 아니다. hard depth, unsupported transport, budget cutoff는 별도 제한으로 보존한다. 세부 추정기의 정확성은 독립 해석해와 사전 고정한 MC 검증 계획을 통해 확인한다.
