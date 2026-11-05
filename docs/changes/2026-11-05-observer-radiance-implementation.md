# 관찰 휘도 기반 빛샘 검출 — 1차 코드 구현

2026-11-05. [보완 설계](../design/visible-leak-remediation-v1.md)의 일부를 실제 Preview 계산과 UI에 연결했다. 이 문서는 설계 전체 구현 또는 실물 TV 검증 완료를 의미하지 않는다. 기존 고객 파일·실행 중 서버는 변경하지 않았다.

## 실제 동작 변경

- `observer_radiance.py`에 평행 카메라의 선형 휘도 추정기를 추가했다. 광원 광속/면적/배광, 반사율, 측정 BSDF의 정규화·입사각 보간, 샘플 PDF를 반영한다. 기존 `observer_path_support_v1` count 결과는 그대로 유지하고 새 결과는 `observer_radiance_parallel_v1` / `luminance_cd_m2`로 구분한다.
- 광원 연결(NEE)과 반사 방향 샘플을 balance MIS로 결합했다. 직접광과 이상 정반사 δ 사건을 별도로 처리하며 같은 기여를 두 번 합산하지 않는다. 연속 BSDF는 실제 광원→관찰자 순서로 평가하고 상반성을 가정하지 않는다.
- Lambertian·Specular·측정 BSDF 및 기존 Gaussian/Mixed forward 모델을 지원한다. Gaussian의 32회 거절 후 축 복귀 질량을 연속 산란과 분리한다. 수치 정규화는 96점 적분을 사용한다. 매우 작은 radial tail의 기존 clamp 질량(약 1e-12)은 연속 참조 근사에서 제외한다. 이 모델 호환은 재질의 실물 정확성 보증이 아니다.
- 작은 Gaussian 로브와 측정 BSDF를 위한 중요도 proposal에 넓은 proposal을 혼합했다. 깊은 경로에는 생존 가중치를 보존한 Russian roulette를 적용하고 deterministic min-energy cutoff로 어두운 경로를 제거하지 않는다. 최대 반사 깊이로 잘린 경우 제한 정보를 남긴다.
- 휘도 모드의 공간 샘플은 셀 전체에 배정하는 무작위 Latin hypercube다. 전체 정밀 검색은 셀당 8개를 사용하며 샘플 0인 영역도 검사한다. 광원 투영 위치를 겨냥한 기존 경로 표본을 픽셀 평균 휘도로 합산하지 않는다. seed와 scalar/batch 묶음 크기의 재현성을 검사한다.
- 각도 중간 탐색을 켜면 기존 각도 cell의 중점을 검출값과 무관하게 추가한다. 기본 15° 후면 반구는 145방향에서 577방향이 된다. 방향·공간 예산 초과는 실행 전에 알리며 정밀 검사 간격을 조용히 늘리지 않는다.
- 휘도 모드 후보는 셀 평균 추정 휘도로 필터·강도·중심·대표 경로를 계산한다. 가까운 유사 경로 그룹에서는 탐색된 최고 추정 휘도의 대표를 유지한다. 필터 뒤에 정방향 탈출 count 후보를 다시 합치지 않는다. 표시 한도 초과 시 밝은 셀을 우선하고 표시 제한 상태를 남긴다.
- Preview에서도 역추적 진행량을 전체 진행량에 포함하고 중단 시 `stopped`와 부분 결과를 보존한다. 전체 정밀 검색만 중단 상태를 인식하던 API 분기를 일반 역추적에도 적용했다.

## 사용 방법과 호환성

시야각 역추적 ON → 검출 기준 **관찰 휘도** → 최소 휘도와 검사 간격/예산 설정 → 실행. 새 프로젝트는 관찰 휘도와 각도 중간 탐색을 기본 선택한다. 기존 저장 파일은 경로 탐색 설정으로 유지하므로 사용자가 검출 기준을 바꿔야 한다. 새 화면이 구형 서버에서 count 결과를 받으면 휘도 결과로 받아들이지 않는다.

기본 최소 휘도 0.01 nit는 변경 가능한 탐색 필터이며 육안 가시성 표준이 아니다. 낮추면 약한 후보를 더 보존하고 높이면 약한 후보를 제외한다. 후보·방향 표의 nit는 픽셀/셀 평균 추정값이며 순간 표본 최대값이나 실측값이 아니다. 설명은 `(?)`에 넣고 미보정·투과 미포함 상태는 보이게 했다.

광원은 기존 설정을 그대로 사용한다. 지원 범위는 기본 Lambertian/Isotropic, 유한 각도 범위의 Sphere Uniform/Lambertian이다. Sphere의 World Alpha/Beta와 Reverse 무관 동작 및 `set_luminance → πLA` 광속 계약을 유지한다. 균일 반구는 일정 휘도가 아니므로 정면에서 Lambertian과 같은 nit가 나오지 않을 수 있다. Area Aim·기본 Gaussian 배광·Sphere Gaussian·완전 평행광은 이번 휘도 참조 구현에서 명시적 오류를 반환한다. 기존 경로 탐색에서는 기존 지원 범위를 유지한다.

## 검증

CPU 합성 장면으로 직접광 L, 정반사 RL, 두 번 정반사 R²L, 약한 RT·BSDF 꼬리 필터, 독립 사각 면광원 view-factor 해석해와 MIS 적분, Gaussian 연속/δ 에너지, 비상반 입사축, 닫힌 벽·반사 Block 차폐, offset gap, seed/scalar/batch 재현성, 공개 해석 함수의 정방향 flux 보존과 진행량을 검사한다. API 중단 검사와 frontend 모드·휘도 중심/대표·저장 호환 검사도 포함한다.

`tests/test_observer_visibility_audit.py`는 **기존 count 모드의 실패를 재현하는 감사**다. 그 테스트 통과는 기존 FP/FN 7건이 해결되었다는 뜻이 아니다. 합성 감사 fixture의 임계값 1 nit를 제품 기본값이나 인간 가시성 기준으로 전환하지 않았다. 기존 감사 보고서/JSON을 새 모드의 합격 보고서로 덮어쓰지 않았다.

검증 결과: 관련 backend/API 36건 통과(새 휘도 검사 17건 포함). 이후 대표 기여도 선택·BSDF 깊이 제한 표시 수정 뒤 새 휘도 검사 17건을 재실행해 통과했다. Frontend 관련 5개 파일의 100건 통과, TypeScript 검사와 production build 통과. Build에는 500 kB 초과 chunk 안내가 남아 있으며 build 실패는 아니다. 범위는 관련 회귀 검사이며 전체 저장소·실물·GPU 성능 검증이 아니다.

```powershell
$env:PYTHONPATH='src;tests'
.\.venv\Scripts\python.exe -m unittest test_observer_radiance test_observer_full_detail test_observer_visibility_audit test_blocker_transport test_api_fastapi.FastApiLayerTests.test_full_detail_stop_after_forward_dispatch_keeps_observer_partial_progress test_api_fastapi.FastApiLayerTests.test_radiance_preview_stop_keeps_observer_partial_progress -v
# frontend 폴더
npm.cmd test -- src/features/leak-preview/leak-preview-model.test.ts src/features/leak-preview/observer-sampling.test.ts src/features/leak-preview/leak-preview-panel.test.tsx src/features/leak-preview/leak-preview-store.test.ts src/features/projects/bitsam-project.test.ts
npm.cmd run build
```

## 아직 남은 범위

원근 카메라·거리/FOV에 따른 영상, 이미지 기반 지역 적응형 세분/독립 확인 샘플과 신뢰도 판정, 투과·BTDF·확산판 재순환 모델, 대표 경로의 별도 독립 geometry audit, 고객 TV 실측 보정은 구현/검증 완료 범위가 아니다. 각도 중점과 공간 계층화만으로 임의의 좁은 빛샘을 모두 찾을 수 없다. 특히 매우 작은 광원·gap은 현재 간격/픽셀보다 작으면 놓치거나 평균 휘도가 낮아질 수 있다. 실물에서 보인 지점이 계속 미검출될 때 이를 ‘빛샘 없음’으로 결론 내리면 안 된다.

CPU 기준 관련 검사만 수행한다. GPU 가속의 성능·실행 성공을 주장하지 않으며 사용자 서버를 재시작하지 않는다. 이번 코드 변경은 보고 전 커밋·푸시하지 않는다.
