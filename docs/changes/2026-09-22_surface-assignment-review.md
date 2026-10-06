# Material / Surface Property 적용 우선순위 및 사용자 지정 값 검토

검사일: 2026-09-22

이 문서는 **수정 전 진단 기록**이다. 이후 승인받아 적용한 수정과 검증은
`2026-09-22_surface-custom-optics.md`에 별도로 기록한다.

- 검사 기준: main `d54c08b9` source checkout.
- 요청 범위: 두 입력 경로가 충돌하는지, 실제 반사 계산에 올바르게 전달되는지 검토.
- 제품 코드 및 물성 계약은 변경하지 않았다. 아래는 재현 결과와 수정 제안이다.
- 이번 추가 실행은 CPU scalar/batch와 프런트엔드 자동 테스트다. GPU 실기 또는 LT 검증을 새로 실행했다는 의미가 아니다.
- 현재 사용 중인 8788 서버, 사용자의 CAD 세션과 입력값은 변경하지 않았다.

## 1. 결론

**부품 기본값과 면별 지정값의 우선순위 자체는 정상이다. 다만 사용자 지정
수치를 정확히 적용하려는 관점에서는 표시 불일치와 입력 계약 공백이 있다.**

실제 적용 순서:

1. 활성 Face override — 해당 component의 지정된 원본 면에만 적용.
2. 활성 Part assignment — override가 없는 면의 기본 광학 프로필.
3. mesh material과 일치하는 광학 프로필.
4. `default` 광학 프로필.
5. 어느 것도 없으면 반사율 0의 안전 흡수체.

두 프로필을 이중으로 곱하거나 혼합하지 않고, 해당 면의 최종 프로필 하나를
선택한다. 부품 설정을 나중에 저장해도 면 override의 우선순위는 유지된다.
같은 면을 새로 지정하면 기존 면 그룹에서 겹친 면을 제거하고, `Use part default`는
해당 면의 override만 제거한다. 다만 기재(Base Material)를 바꾸면 기존 면 마감은
유지하면서 새 기재 기준으로 그 면의 기본 수치를 다시 계산한다.

근거: `src/leakage_simulator/optics.py:66`,
`frontend/src/stores/workspace-store.ts:1463`,
`frontend/src/features/raytracing/ray-tracing-model.ts:929`.

## 2. 재현 결과

### A. 면 속성 변경 시 부품의 사용자 지정 반사율을 이어받지 않음

PC Black 부품에 Custom Reflectance=0.6, Loss=0.4를 적용하고 특정 CAD 면만
Surface Property에서 Matte로 지정했다.

| 대상/조작 | 최종 기본 반사율 | 입사 광속 1 lm을 1회 반사 후 모두 포획한 광속 |
|---|---:|---:|
| 부품 기본값을 따르는 면 | 60% | 0.6 lm |
| Matte로 따로 지정한 면 | 5.76% | 0.0576 lm |
| 해당 면에 Use part default 적용 | 60% | 0.6 lm |

면 설정에는 부품의 `opticalOverride`를 복사/상속하지 않는다. PC Black 카탈로그
반사율 8% × Matte 배율 0.72로 다시 만들어진다. 따라서 60% × 0.72도 아니며,
부품 60%와 면 5.76%를 두 번 감쇠하는 것도 아니다. 선택하지 않은 같은 부품의
인접 면은 60%로 유지됐다. 전체 형상 및 ROI에서 동일하게 재현했다.

이는 현재의 **완전한 프로필 교체** 동작이다. 별도 마감을 선택했을 때 부품의
사용자 지정 수치를 유지해야 하는지는 명시적인 정책 결정이 필요하다.
자동 상속과 자동 초기화 중 어느 쪽이든 암묵적으로 적용하면 LT 비교 입력을
잘못 구성하기 쉽다. 두 UI의 면 설정 경로 모두 같은 제한을 갖는다.

근거: `frontend/src/features/materials/surface-property-dialog.tsx:66`,
`frontend/src/features/materials/material-editor-dialog.tsx:460`,
`frontend/src/features/raytracing/ray-tracing-model.ts:941`.

### B. Surface Property 미리보기가 실제 유효값과 다름 — 표시 오류

위의 부품 Custom 60% 상태에서 아직 면 override를 만들지 않고 Surface Property
창을 열면 `Compiled optical preview`는 0.080을 표시한다. 실제 적용값은 0.600이다.
미리보기에는 기재/마감만 전달되고 부품의 사용자 지정 값이 전달되지 않기 때문이다.

아무 Material도 적용하지 않은 새 상태에서도 Surface Property 창은 PC Black /
Normal, 0.080을 표시한다. 그러나 요청에 광학 프로필이 하나도 없고 mesh/default
프로필도 없는 재현에서는 실제로 반사율 0이 적용됐다. 화면의 초기 편집값은
적용된 값이 아니므로, `미지정` 상태와 `현재 적용값`을 구분해야 한다.

근거: `frontend/src/features/materials/surface-property-dialog.tsx:34`,
`frontend/src/features/materials/surface-property-dialog.tsx:146`,
`src/leakage_simulator/optics.py:49`.

### C. Custom Specular / Diffuse가 모든 모델에서 유효하지 않음 — 입력 계약 불일치

Material에서 Matte를 선택하고 Custom Reflectance=0.6,
Specular=1, Diffuse=0을 지정했다. 프런트엔드는 그 비율을 전달하지만
`scatter_model=lambertian`을 그대로 유지한다. 백엔드는 순수 Lambertian 모델이면
비율보다 모델 분기를 우선하므로 1,024개 방향 표본 모두 Lambertian이었다.

이 비율은 현재 `mixed` 모델에서만 방향 분포를 선택하는 데 사용된다.
순수 Specular/Lambertian/Gaussian은 각 모델의 방향을 그대로 사용한다.
따라서 Custom 입력창에 Specular 100%라고 쓰는 것만으로는 경면 반사가 되지 않는다.

수정 시에는 순수 모델에서 무효인 비율 편집을 막거나, 사용자가 명시적으로
Mixed 모델을 선택하게 해야 한다. 단순히 모든 입력을 Mixed로 바꾸는 것은
기존 계산 의미를 바꾸므로 피한다. Mixed의 Specular 항도 sigma가 0.01도보다
크면 이상 경면이 아니라 경면 축 중심 Gaussian lobe라는 점을 구분해야 한다.

근거: `frontend/src/features/materials/material-editor-dialog.tsx:94`,
`frontend/src/features/raytracing/ray-tracing-model.ts:947`,
`src/leakage_simulator/reflection.py:77`.

### D. 각도 보정 ON이면 입력 반사율이 고정 계수가 아님 — 현행 모델의 의미

`Angle-dependent reflectance` 기본값은 ON이다. 입력 60%, roughness=0.45에서
현재 근사식은 입사각 89도에 약 83.357%를 적용한다. OFF이면 60%로 유지된다.
이는 Material/Surface 우선순위 충돌은 아니지만, LT의 고정 반사율 모델과 비교할 때
같은 입력 조건이라고 오해할 수 있는 부분이다. 근사식의 구현 일치와 실제 소재의
측정 반사율 일치는 별개다.

고정 반사율 비교 실험에서는 양쪽 모델을 고정 계수 조건으로 맞추고, 각도 의존
모델 비교는 별도 실험으로 분리해야 한다. 이번 검토에서 사용자의 설정은 바꾸지 않았다.

근거: `src/leakage_simulator/reflection.py:27`,
`frontend/src/features/raytracing/ray-tracing-panel.tsx:2124`.

## 3. 확인된 정상 범위

- Part / Face 우선순위, 면 그룹 중첩 제거, Part default 복귀.
- 부품 기재 변경 후 면 마감 유지 및 새 기재 기준 재계산.
- ROI 필터링/삼각형 절단 후 원본 면 ID 연결, 인접 면 오연결 방지.
- 부품 제외, transform, 준비된 형상 캐시 재사용 후 면 반사율 유지.
- 정밀 해석 mesh 확장 및 합성 `.bitsam` 복원 후 재편집/재해석.
- Specular/Lambertian/Gaussian/Mixed의 기존 단일 면 광속 및 분포 회귀.

프런트엔드 5개 파일 **123개 테스트 통과**; 그중 이번 진단 5개는 현재 동작을
기록하는 테스트이지 표시 오류가 수정됐다는 테스트가 아니다. 백엔드 기존 관련
**14개 테스트 통과**. 프런트엔드에서 실제 생성한 프로필을 별도 합성 형상에 넣어
6조건 × ROI 유무 × CPU scalar/batch = **24회** 실제 수송을 실행했다.
입사 1 lm, 각도 보정 OFF, 최소 에너지 종료 0, 128 Ray의 1회 반사 포획값이
각 요청의 유효 반사율과 일치했다. 반복 무작위 수렴성 또는 성능 벤치마크는 아니다.

재현 자료(로컬, Git 제외):

- `frontend/outputs/surface_property_review_2026-09-22/probe.diagnostic.tsx` (수정 전 동작 기록)
- 같은 폴더의 `frontend-tests.log`, `frontend-evidence.json`
- `backend_probe.py`, `backend-evidence.json`, `backend-tests.log`

실제 복잡 TV 전체/다중 Case/UI 수동 전수 검증, 새로운 GPU 실기 검증,
LT/실측의 절대 정합성은 이번 검토 범위가 아니다.

## 4. 권장 수정 순서

1. 현재 유효 프로필을 계산하는 공통 함수를 UI 미리보기와 해석 요청이 함께 사용.
   미지정/부품 상속/면별 지정 상태 및 실제 반사율·모델을 동일하게 표시.
2. 면별 Custom 입력 지원: 반사율, 모델, Gaussian sigma, Mixed 비율을 명시적으로
   편집하고 저장. 부품 상속 또는 면 독립값 여부를 표시하고, 마감 변경으로 값이
   바뀌는 경우 적용 전에 최종 수치를 보여줌.
3. 순수 모델의 비율 입력 제한과 저장/복원/Case 복사 계약 검증.
   기본 preset은 시작점이며 측정 BSDF와 동일한 데이터라는 의미가 아님을 구분.
4. 같은 두 면에 서로 다른 반사율/분포를 지정한 LT 공통 장면에서 광속(lm)과
   조도(lx)를 먼저 대조. 각도 의존성과 추정 nit 환산은 분리해서 확인.

제품 수정·서버 재시작·커밋·push는 이번 검토에서 수행하지 않았다.
