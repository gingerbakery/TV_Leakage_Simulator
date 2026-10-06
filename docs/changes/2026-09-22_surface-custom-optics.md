# Surface Property 사용자 지정 광학 값 편집 통합

작성: 2026-09-22

기준: source checkout `main`의 `d54c08b9` 이후 작업.
수정 전 진단: `2026-09-22_surface-assignment-review.md`.
9월 22일 작업 당시에는 커밋, push, 백엔드 서버 재시작을 하지 않았다.

## 1. 입력 위치와 적용 대상

- Material은 기재와 마감 프리셋, 저장된 프로필의 선택을 담당한다.
- 사용자 지정 수치와 반사 모델 편집은 Surface Property로 이동했다.
- Material의 `Surface Property · 커스텀 편집`으로 부품 기본값 편집에 진입한다.
  10월 6일 후속 수정부터는 별도 편집 창이 열리며 Material에서 편집 중인
  기재·마감 선택도 함께 사용한다. 먼저 Apply할 필요는 없다.
- Surface Property에서 `선택 CAD 면` 또는 `부품 기본값`을 선택한다.
- 부품 기본값 변경은 별도로 지정한 면의 override를 삭제하지 않는다.
- 원본 CAD 면 단위로 적용하며 ROI 빗금 절단면은 편집 대상이 아니다.
  기존 면별 표시색 기능은 유지한다.

## 2. 세 가지 명시적인 모드

| 모드 | 동작 |
|---|---|
| 부품 기본값 사용 | 선택 면의 override를 제거하고 현재 부품 값을 따른다. |
| Preset | 현재 기재와 마감의 카탈로그 값으로 명시적으로 설정한다. |
| Custom | 반사율, 반사 모델, 거칠기, Gaussian σ, Mixed 비율을 직접 지정한다. |

Custom 상태에서 마감 프리셋을 변경하면 **반사율은 유지**하고 반사 모델과
분포 파라미터만 해당 마감 값으로 바꾼다. 부품 Custom을 상속받던 면에서
마감을 변경하는 경우에도 현재 반사율을 유지한 독립 면 Custom을 만든다.
이는 그 시점 값의 복사이며, 이후 부품 반사율을 바꾸어도 그 면의 Custom
반사율이 자동으로 따라 바뀌는 것은 아니다. 다시 상속하려면
`Use part default`를 사용한다.

명시적으로 Preset 모드를 선택하면 Custom을 해제하고 카탈로그 반사율로
돌아간다. 서로 다른 값의 면을 여러 개 선택했을 때는 설정을 명시적으로
고르기 전까지 적용을 막아 첫 번째 면의 값으로 덮어쓰는 사고를 방지한다.

## 3. 계산 계약과 표시 수정

- 미리보기와 해석 요청은 같은 `resolveAssignmentProfile`을 사용한다.
- 부품 60%를 상속받는 면에 8%로 표시되던 오류를 수정했다.
- 광학 값 미지정 상태는 반사 없음으로 표시하고, 적용 전 편집값은
  `적용 예정 광학 값`으로 구분한다.
- Loss는 현재 불투명 반사 모델의 `1 − Reflectance`로 자동 계산한다.
- 순수 Specular/Lambertian/Gaussian에서 사용되지 않는 혼합 비율 입력을
  숨기고 Mixed에서만 경면 계열/확산 비율을 편집한다.
- Mixed의 경면 계열은 σ ≤ 0.01°이면 Specular, 그보다 크면 Gaussian이다.
- `none` 모델은 반사율 0만 허용하며, 비율 범위와 σ 양수 조건을 검사한다.
- 실행 프로필 ID를 assignment별로 분리해 같은 저장 프로필 이름을 사용한
  서로 다른 설정이 해석 요청에서 덮어써지지 않게 했다.
- face override > part assignment 우선순위와 기존 ROI 원본 면 연결은 유지한다.

`Angle-dependent reflectance`의 기존 전역 설정은 바꾸지 않았다. ON이면 입력
반사율은 기본값이며 실제 반사율에 입사각 보정이 적용된다. Surface Property에
현재 ON/OFF 상태를 표시한다. LT의 고정 반사율과 비교할 때는 양쪽의 각도
보정 조건부터 맞춰야 한다.

## 4. 저장 호환

Custom에 선택적인 `scatterModel`, `roughness`, `scatterSigmaDeg` 필드를 추가했다.
기존 `.bitsam`에 해당 필드가 없으면 기존 마감의 모델/거칠기/σ를 그대로
사용한다. 과거 Custom 비율을 임의로 Mixed 모델로 재해석하지 않는다.
새 필드는 저장/재열기 및 해석 요청 전달 테스트에 포함했다.

## 5. 9월 22일 검증 결과

| 검증 | 결과 |
|---|---|
| 프런트엔드 전체 회귀 | 49개 파일, 414개 테스트 통과 |
| 관련 백엔드 회귀 | 광학 수송·portable·분포·좌표 변환 14개 통과 |
| 실제 프런트엔드 요청을 사용한 CPU 수송 | 32조건 통과 |
| TypeScript / production build | 통과 |
| Lint | 오류 0; 기존 Result 창 hook 의존성 경고 1개 |
| 브라우저 UI | 실제 STEP import 후 면별 Custom 적용·다른 면 보존·재선택 복원 확인 |

32조건은 4개 모델(Specular/Lambertian/Gaussian/Mixed), 부품/면 지정,
전체/ROI, CPU scalar/batch의 조합이다. 입사 광속 1 lm을 1회 반사 후 모두
포획하는 합성 구조에서 부품 반사율 0.6134567 및 면 반사율 0.3712345와
수광 광속이 절대 오차 1e-12 이내로 일치했다. 이 실험은 입사각 보정 OFF다.
Gaussian σ=7.321°와 거칠기 0.13의 실제 전달도 확인했다.

UI 검증은 별도 임시 탭에서 `01_single_mirror.stp`를 사용했다. Face 1에
반사율 0.6134567 / Gaussian σ=7.321°를 지정하고, Face 2는 미지정 상태로
유지되며 Face 1 재선택 시 값이 복원됨을 확인했다. 1280×720 화면에서
입력과 하단 적용 버튼이 사용 가능하고 브라우저 오류 로그는 없었다.
사용자가 작업 중인 탭은 새로고침하지 않았다.

실행 로그와 수송 실험 산출물은 로컬 비추적 경로
`frontend/outputs/surface_property_review_2026-09-22/`에 둔다.
자동 회귀 테스트는 `optical-profile.test.ts`,
`surface-property-dialog.test.tsx`, `feature-editors.test.tsx`에 남겼다.

이번 작업은 새 GPU 실기 RT 실행이나 LT 정합 검증을 포함하지 않는다.
전체 RT 정밀 검증 완료를 의미하지 않으며, 이전 감사 계획의 미완료 항목은
계속 별도 추적한다. 새 프런트엔드 production build는 기존 8788 서버에서
제공되므로 사용자 작업을 저장한 뒤 페이지를 새로고침하면 적용된다.

## 6. 10월 6일 미리보기와 별도 편집 창

- Compiled optical preview를 Reflectance / Loss / Specular / Diffuse 네 항목으로
  표시한다. Gaussian은 이 요약에서 Specular 계열로 표시하며 실제 모델과 σ는
  보조 설명에 유지한다. 산란 방향을 계산하는 백엔드 알고리즘은 변경하지 않는다.
- 별도 창의 Save는 부품 기본값을 저장하고 열린 Material Assignment의 미리보기와
  편집값을 즉시 갱신한다. 이후 Apply를 눌러도 이전 값으로 되돌아가지 않는다.
- Cancel 또는 닫기는 별도 창의 변경을 버린다. 면별 override는 그대로 보존한다.
- 유효하지 않은 수치는 저장을 막는다. Enter는 숫자 입력 확정에만 사용하고,
  별도 Save 버튼으로 저장하여 미완성 입력이 이전 값으로 저장되는 일을 방지한다.
- 넓은 Material 창이 화면 오른쪽에서 잘리지 않도록 실제 너비로 위치를 계산한다.

관련 세 테스트 파일의 53개 테스트와 production build가 통과했다. 별도 브라우저
탭에서 실제 STEP을 불러와 R=0.3, Specular=0, Diffuse=1 저장 후 미리보기 반영과
1280×720 화면 배치를 확인했다. 이 후속 UI 검증은 8789 일반판에서 수행했으며
LT 매칭 실험의 Gaussian 정의·방향 추첨·절단/에너지 보존 변경은 포함하지 않는다.
향후 LT 매칭을 도입하더라도 현재 입력 항목과 편집·저장 흐름을 유지하는 것을
기본 원칙으로 한다.

커밋 전 추가 검토에서 Surface Property의 Apply에도 같은 미완성 입력/Enter
경로가 있어 명시적 Apply로만 저장하도록 수정하고 회귀 테스트를 추가했다.
원격 main `00f7abb2`와 충돌 없이 통합한 뒤 프런트엔드 전체 51개 파일,
432개 테스트와 production build가 통과했다. Lint는 오류 없이 기존
Result 창의 `liveResult` hook 의존성 경고 1개를 보고했다. 원격에 이미 반영된
결과 복원 및 View Cone 변경은 유지했으며 이번 커밋의 백엔드 변경은 없다.
