# Result 리포트 Ray summary 탭에 Ray Section View 이미지 추가

- 날짜: 2026-08-05
- 대상 브랜치: `feat/ray-tracing-datum-plane-and-viewer-ux`

## 배경

3D 뷰어에서는 ray path와 ROI 단면을 인터랙티브하게 볼 수 있지만, Result
리포트에는 수치 통계만 있고 "빛이 실제로 어떻게 Receiver에 도달하는지"를
한눈에 보여주는 정적 이미지가 없었다. Receiver 중심을 지나면서 Receiver의
normal(보는 방향) 벡터를 포함하는 수직 단면으로 CAD를 잘라 보여주고, 그
위에 그 Receiver에 실제로 도달한 광선(direct + 반사)만 겹쳐 그린 이미지를
Ray summary 탭에 추가해 달라는 요청.

## 단면 평면 정의

Receiver의 world-space `center`/`normal`이 주어졌을 때:

- `worldUp = (0,0,1)` (이 앱의 Z-up 컨벤션)
- `n = normalize(receiver.normal)`
- `viewNormal = normalize(cross(n, worldUp))` - 이 벡터가 (a) 자르는
  평면의 normal이자 (b) 카메라가 바라보는 방향이 된다. `viewNormal`은
  구성상 `n`, `worldUp` 둘 다에 수직이므로, `receiver.center`를 지나고
  이 normal을 가진 평면은 정확히 "boresight와 world-vertical을 모두
  포함하는 수직 단면"이 된다.
- 축퇴 케이스(정확히 수직으로 위/아래를 보는 Receiver, 즉 `cross(n,
  worldUp)`가 0에 가까움): `cross(n, worldX)`로 대체. 그것도 축퇴면
  `null` 반환 - UI에는 "이 Receiver 방향에서는 section view를 생성할 수
  없습니다" 안내만 표시.

## 구현

- **`frontend/src/features/results/ray-section-view.ts`** (신규)
  - `computeSectionPlaneBasis(receiver)` - 위 수학, 순수 함수라 단위
    테스트 용이.
  - `renderRaySectionImage({ scene, receiver, storedPaths, width, height })`
    - 메인 뷰어와 별개인 **오프스크린** `<canvas>` + `WebGLRenderer`를
      만들어 1회 렌더링 후 `toDataURL('image/png')`로 반환.
    - CAD 지오메트리는 `frontend/src/features/viewer/scene-geometry.ts`의
      `createFaceGeometry`를 재사용(단, 원점 재배치 없이 절대 좌표
      유지). 이 저장소 최초로 Three.js clipping plane
      (`material.clippingPlanes` + `renderer.localClippingEnabled`)을
      사용해 단면 절개 - 기존 ROI 절단 기능은 axis-aligned 평면 전용
      CPU 폴리곤 클리핑이라 임의 평면에는 그대로 못 쓴다.
    - 광선: `storedPaths` 중 마지막 hit의 `receiver_id`가 그 Receiver와
      일치하는 것만 필터링한 뒤, 기존 `buildRayPathVisualization` +
      `rayPathStyles`(`frontend/src/features/results/ray-paths.ts`)를
      그대로 재사용 - 인터랙티브 뷰어와 동일한 색상(direct=초록,
      반사=노랑).
    - 카메라: `OrthographicCamera`, `viewNormal` 방향에서
      `receiver.center`를 바라봄. Frustum은 씬 전체 AABB와
      `receiver.center`를 함께 투영해 비대칭으로 계산 - Receiver가
      모델에서 멀리 떨어져 있어도 모델과 Receiver 둘 다 프레임 안에
      들어오게 함(처음엔 대칭 frustum으로 했다가 Receiver가 멀리
      있을 때 모델이 가장자리로 밀리는 문제를 보고 수정).
    - WebGL 미지원 환경(헤드리스 테스트 등)에서는 try/catch로 `null`
      반환, 렌더러/geometry/material은 사용 후 전부 dispose.
  - **`frontend/src/features/results/ray-section-image.tsx`** (신규) -
    `useMemo`로 1회 렌더링해 `<img>`로 표시하는 컴포넌트.
  - **`result-window.tsx`**: `scene` prop 추가, Ray summary 탭에
    활성화된 Receiver마다 `RaySectionImage` 하나씩 표시.
  - **`viewer-workspace.tsx`**: `RayTraceResultWindow`에 `scene` prop
    전달.

## 후속 수정: WebGL context 고갈로 메인 뷰어가 깨지는 문제

실사용 중 "section view가 검은 화면만 나오고, 심지어 메인 3D 뷰어의 ray
선까지 안 보인다"는 리포트가 있었다. `renderRaySectionImage`가 Receiver
하나당(그리고 React StrictMode가 dev에서 두 번 호출하므로 사실상 그
두 배) 별도의 `WebGLRenderer`를 새로 만드는데, `renderer.dispose()`만으로는
브라우저가 실제 WebGL context를 즉시 반환한다는 보장이 없다. Result
창을 여러 번 열고 닫는 과정에서 이 반환 타이밍이 GC에 맡겨진 채 계속
쌓이면, 브라우저가 동시 WebGL context 개수 제한에 걸려 **가장 오래된
context(=메인 뷰어)를 강제로 잃어버린다.**

재현 및 수정 확인:
- 수정 전, Result 창을 40회 열고 닫는 스트레스 테스트 → 콘솔에 `WARNING:
  Too many active WebGL contexts. Oldest context will be lost.`가
  반복 출력되고, 실제로 메인 뷰어 캔버스의 `gl.isContextLost()`가
  `true`로 확인됨 - 정확히 사용자가 겪은 증상과 일치.
- `renderer.dispose()` 직전에 `renderer.forceContextLoss()`를 추가해
  context를 즉시 강제 반환하도록 수정.
- 수정 후 동일한 40회 반복 테스트 → context loss 경고 없음, 메인 뷰어
  `isContextLost()`는 계속 `false`, section 이미지도 매번 정상 렌더링
  확인.

## 후속 수정 2: 클리핑 방향이 잘못되어 화면 자체가 텅 비는 문제

첫 수정(WebGL context) 이후에도 여전히 "RAY도 기구 도면도 아예 안 나오고
검정 화면만 보인다"는 리포트가 있었다. 원인은 별개의 버그였다:

- 클리핑 평면이 항상 "카메라 쪽(고정된 +viewNormal 방향)"을 잘라내도록
  고정되어 있었는데, 실제 CAD 형상 전체가 하필 그 잘려나가는 쪽에
  있으면 결과적으로 **모델 전체가 사라져** 배경색만 남았다. Receiver를
  모델 표면 위/근처에 배치하는 실사용 패턴(이번 세션에서 만든 "CAD
  Face 선택"으로 배치하는 경우 등)에서 특히 자주 발생.
  - 수정: 자르기 전에 씬 전체 bounding box 중심이 section origin 기준
    `viewNormal`의 어느 쪽에 있는지 미리 계산해서, **형상이 적은 쪽에서
    카메라가 바라보고 그쪽을 잘라내** 형상이 많은 쪽이 항상 남도록
    동적으로 결정하게 바꿨다(`bulkSide`/`cameraSide`).
- 위 수정과 맞물려, frustum(U/V) 계산에 쓰던 `right` 벡터가 카메라가
  반대쪽에서 바라볼 때(`cameraSide = -1`) Three.js가 실제로 사용하는
  카메라 축과 부호가 어긋나는 문제도 같이 있었다 - 비대칭 frustum
  경계가 실제 렌더링 축과 안 맞아 화면이 잘못된 위치를 잘라내는
  원인이 될 수 있었다. `right`를 카메라의 실제 forward 방향(`-viewNormal
  * cameraSide`)에서 유도하도록 고쳐 항상 Three의 `lookAt()` 내부
  좌표계와 일치하게 만들었다.

검증: 모델 상판의 실제 CAD face를 "뷰어에서 CAD Face 선택"으로 골라
5mm 띄운 Receiver(현실적인 배치 - 이전엔 이 케이스가 정확히 실패
케이스였다) + Emitter 조합으로 실제 브라우저에서 재현 → 4,810 hits,
Ray Section View에 초록색 ray 다발과 CAD 단면이 정상적으로 함께
렌더링되는 것을 확인. 여기에 20회 열고 닫기 스트레스까지 같이 돌려도
메인 뷰어 context와 section 이미지 둘 다 계속 정상.

## 후속 수정 3: Receiver가 넓은 평판 중심 근처에 있을 때 여전히 텅 비는 문제

수정 2 이후에도 "RAY도 기구 형상도 안 나오고 선 하나만 나온다"는
리포트가 있었다(Cover_Deco 컴포넌트의 넓은 면 위, ROI 활성 상태).
원인: "형상이 많은 쪽을 남긴다"는 판단을 **모델 전체 bounding box의
중심점 하나**로만 했는데, Receiver가 넓은 평판 표면 한가운데 근처에
있으면 중심점과 Receiver 위치가 사실상 같아져서 어느 쪽이 많은지
판단이 사실상 랜덤(부동소수점 오차 수준)해진다.

- 수정: AABB 중심점 부호 대신, **AABB의 8개 꼭짓점을 실제로
  section origin 기준 `viewNormal` 방향으로 투영해 양쪽으로 각각
  얼마나 뻗어있는지(extent)를 측정**해서 더 많이 뻗은 쪽을 남기도록
  변경. Receiver가 중심에 있어도 왜곡 없이 판단됨.
- 추가 안전장치: 만약 자르는 평면이 모델의 넓은 면과 거의 평행하면
  (양쪽 extent가 모델 전체 크기 대비 아주 작으면) 애초에 클리핑을
  적용하지 않고 전체 모델을 그대로 보여주도록 폴백 추가 - 잘라봤자
  edge-on 실선 하나만 남는 상황을 방지.

검증: Receiver를 모델 자체의 bbox 중심 좌표에, normal은 수평
방향(Rotation X=90 - 넓은 상판과 거의 평행하지 않는 방향)으로
배치해서 재현 - 정확히 "Receiver가 모델 중심 근처"인 케이스. 320
hits, Ray Section View에 CAD 단면과 초록색 ray 다발이 선명하게 함께
렌더링되는 것을 확인.

## 후속 수정 4: 근본 원인 - CAD 원본 색상이 거의 검정이라 형상이 안 보이던 문제

수정 1~3 이후에도 사용자가 정확한 재현 파라미터(Center 99.4,36,39 /
Offset -70,0,0 / Rotation 60,0,180 / 30×30mm / Acceptance 90)를 캡처
이미지로 공유하며 "여전히 검은 화면만 나온다"고 재보고했고, 이어서
ROI가 활성화된 상태에서도 같은 증상("갑자기 ray 선들도 안 보이고,
단면이면 기구 도면 정도는 나와야 하는데 검정 화면만 보인다")이
보고됐다. 사용자 지시대로("지금 현 요청 작업에서 제대로 기능하지
못하는 이유가 뭔지부터 확인해서 말해줘") 코드를 고치기 전에 원인부터
철저히 격리했다.

**조사 과정** (Playwright로 실제 브라우저에서 `console.log` 계측 +
`gl.readPixels()`로 프레임버퍼 직접 검사):

1. 카메라 행렬이 `lookAt()` 직후 `updateMatrixWorld(true)`를 호출하지
   않으면 stale(단위행렬)한 상태로 `Vector3.project()`에 쓰이고 있던
   실제 버그를 하나 발견 - 렌더 루프 밖에서 쓰는 parent 없는 카메라는
   자동으로 갱신되지 않음. 수정했지만 증상은 그대로였다(별개 버그였음).
2. ROI 활성 시 `renderRaySectionImage`가 ROI로 필터링된 face 대신 씬
   전체를 기준으로 framing/bounds를 계산하던 문제도 발견해 `roiFaceIds`를
   `viewer-workspace.tsx`의 기존 `activeRoiFaceIds` → `RayTraceResultWindow`
   → `RaySectionImage` → `renderRaySectionImage`까지 threading하고,
   `computeFaceSetBounds`로 ROI-scoped face만으로 bounds를 계산하도록
   고쳤다. 필요한 수정이었지만 이 역시 "검은 화면" 증상의 근본 원인은
   아니었다.
3. 진짜 원인을 찾기 위해 알려진-정상 geometry(`BoxGeometry` 테스트
   박스)를 동일 scene/camera/renderer에 추가해 A/B 비교 → 테스트
   박스는 항상 정상 렌더링되는데 실제 컴포넌트 mesh만 매번 렌더링
   결과가 0 픽셀. Clipping 완전 비활성화, frustum culling 비활성화,
   MeshBasicMaterial(무조명)로 교체, geometry를 raw position
   배열에서 통째로 재생성 - 전부 시도해도 동일. `renderer.info.render`
   기준 triangle 개수/draw call은 정상 제출되고 GL 에러도 0.
4. 최종적으로 실제 렌더링되는 mesh의 `MeshBasicMaterial({color:
   component.color ?? 0x8896a8, ...})`에서 `component.color`를
   직접 로그로 찍어보니 **`"#000000"`, `"#010101"`, `"#07080b"`,
   `"#02060e"` 등 STEP 파일에 저장된 실제 컴포넌트 색상이 전부 거의
   순수 검정**이었다. `component.color`는 `null`이 아니라 유효한(단,
   거의 검정인) 문자열이라 `?? 0x8896a8` 폴백이 전혀 작동하지 않았고,
   그 결과 CAD 형상이 배경색(`0x0b1220`, 역시 어두운 남색)과 거의
   구별되지 않는 색으로 그려지고 있었다 - "렌더링이 안 되는" 게
   아니라 "보이지 않는 색으로 렌더링되고 있었다."

**수정**: 이 리포트용 단면 이미지는 실제 인터랙티브 뷰어와 달리
색상 정확도보다 가독성이 우선이므로, `component.color`를 아예 쓰지
않고 항상 고정된 중립 회색(`sectionGeometryColor = 0x8896a8`)으로
CAD 형상을 그리도록 변경. 곁들여 단면(cutaway)은 조명이 잘 닿지
않는 내부 면을 주로 보여주므로, 단일 방향 key light 하나로는 여전히
거의 검게 나올 수 있어 HemisphereLight 강도/ground color를 올리고
반대 방향 fill light를 추가해 어떤 면 방향이든 어느 정도는 밝게
보이도록 보강했다.

검증: 사용자가 캡처로 공유한 정확한 파라미터로 재현 → CAD 단면이
뚜렷한 회색 실루엣으로, 그 위에 도달한 ray(초록색)가 함께 렌더링되는
것을 실제 스크린샷 픽셀 값(RGB)까지 확인(배경 `(11,18,32)` vs 형상
`(136,150,168)` 근방 - 이전엔 형상도 `(7~8,8~9,10~11)`로 배경과 거의
동일했음). ROI 활성 시나리오도 콘솔 에러 없이 정상 동작 확인(다만
이번 재현에 쓴 ROI 박스는 넓은 평판의 안쪽 면만 골라 선택되어, 그
단면 자체가 시야 방향과 거의 평행한 우연한 케이스라 CAD 실루엣이
거의 안 보이는 것은 별도 버그가 아니라 기하학적으로 타당한 결과 -
ray 경로는 정상 표시됨).

## 후속 개선 5: Receiver 위치/방향을 이미지 안에 직접 표시

색상 문제를 고친 뒤에도 사용자가 "여전히 섹션뷰를 믿을 수가 없다"고
했다. 원인은 버그가 아니라 **누락된 정보**였다: 이미지엔 CAD 단면과
ray만 그려질 뿐 Receiver 자체(위치·크기·바라보는 방향)가 전혀 표시되지
않아서, ray가 실제로 Receiver 위치에서 끝나는 건지 눈으로 대조할
방법이 없었다.

**수정**: `renderRaySectionImage`에 Receiver의 실제 사각형(`center ±
u_axis*width/2 ± v_axis*height/2`로 계산한 4개 꼭짓점을 잇는 테두리)과
boresight(`normal`, `normal_flip` 반영) 방향으로 뻗는 짧은 stub
라인을 시안색(`0x22d3ee` - 메인 3D 뷰어의 Receiver 오버레이 색과
동일하게 맞춰 두 화면에서 같은 색이 같은 의미를 갖게 함)으로 추가
렌더링했다. CAD 클리핑 평면의 영향을 받지 않도록(`clippingPlanes`
미지정) 별도 `LineSegments`로 그려서, 주변 CAD가 잘려나가도 Receiver
표시는 항상 온전히 보인다. Receiver가 CAD 프레이밍 범위 밖으로 벗어나
있는 경우를 대비해, frustum U/V 범위 계산에 Receiver 꼭짓점과
boresight 끝점도 포함시켰다. `ray-section-image.tsx`에는 색상 범례
(Receiver/Direct/반사광/CAD 단면)를 이미지 하단에 추가.

**"정확히 Receiver 정센터를 지나고 normal 벡터를 포함하는 단면이
맞냐"는 질문에 대한 확인**: `computeSectionPlaneBasis`에서
`origin = receiver.center`(정확히 그 점을 지남)이고, 단면의 normal은
`viewNormal = cross(receiver.normal, worldUp)`인데, 벡터의 외적은
정의상 두 입력 벡터 모두와 수직이므로 `viewNormal`은
`receiver.normal`과도, `worldUp`과도 수직이다. 즉 `viewNormal`을
normal로 갖는 평면(바로 이 단면)은 **`receiver.normal`(boresight)과
`worldUp`을 둘 다 포함하는 평면**이 되어, 애초에 사용자가 확정했던
"Receiver의 normal 벡터를 포함하는 수직 단면" 정의와 정확히 일치한다.

검증(실제 Result 리포트 화면, Playwright): Receiver를 Rotation X=90
(수평 방향을 바라보도록) + 300×80mm로 배치해 재현 → 이 배치에서는
Receiver 평면이 이 단면 시야 방향에서 정확히 옆에서 보이는(edge-on)
케이스라, 시안색 테두리가 CAD 표면 위의 짧은 가로선으로, 그 아래로
boresight stub이 수직선으로 뻗어 나오는 게 보였다. 초록색 ray들이
정확히 그 지점(테두리와 stub이 만나는 점)으로 수렴하는 것을 확인 -
ray 종점과 Receiver 표시가 일치함을 눈으로 바로 대조할 수 있게 됨.

## 후속 개선 6: 해상도가 낮아 CAD 윤곽선이 계단처럼 삐쭉삐쭉 보이던 문제

사용자가 실사용 캡처를 공유하며 CAD 단면 윤곽선이 계단식으로 거칠게
보인다("삐쭉삐쭉")고 지적했다. 렌더링 자체는 640×400 고정 해상도인데,
Result 창의 실제 카드 폭은 900~1900px대까지 넓어질 수 있어 `<img
className="block w-full">`가 이를 최대 3배 가까이 업스케일하고
있었다 - 소스 해상도 부족으로 인한 블록/계단 현상.

**수정**: `defaultWidth`/`defaultHeight`를 640×400 → 1600×1000으로
올렸다. 정적 이미지 1회 렌더링이라 비용 부담이 없고, 실제 카드 폭보다
항상 크게 렌더링되므로 브라우저가 업스케일 대신 다운스케일하게 되어
윤곽선이 매끈해진다.

검증: Playwright로 `naturalWidth/Height`(1600×1000)와
`clientWidth/Height`(917×573, 실제 표시 크기)를 직접 비교해 다운스케일
방향임을 확인. 동일 재현 시나리오에서 CAD 윤곽선이 깔끔한 직선으로
렌더링되는 것을 확인. 다만 사용자가 본 정확한 위치(계단형 프로파일이
있던 지점)는 재현 파라미터를 알 수 없어 그대로 재현하지는 못했다 -
해상도를 올린 뒤에도 특정 위치에서 여전히 계단 형태가 남는다면, 그건
캔버스 해상도가 아니라 STEP 메시 자체의 작은 챔퍼/필렛 테셀레이션이
단면에 그대로 드러나는 것이거나, 현재 쓰는 "GPU 클리핑 평면(뚫린 단면)"
방식이 겹쳐진 여러 얇은 면을 동시에 보여주면서 생기는 현상일 수 있다 -
애초 계획에서 "우선 open cut으로 시작하고, 깔끔하지 않으면 filled cap
방식으로 재검토"하기로 했던 바로 그 케이스.

## 후속 개선 7: "선택 면이 전부 절단 방향과 평행"할 때 조용히 텅 빈 이미지가 나오던 문제

하루 종일 여러 버그를 고쳤는데도 사용자가 "결과 REPORT에 SECTION뷰가
제대로 나오지 않는다"고 재차 지적했다. 표준 재현 케이스(Datum plane
Emitter/Receiver)는 정상 렌더링됐지만, 그날 실제로 반복해서 썼던
조합 - **ROI 활성 + CAD surface Emitter(멀티 패치) + ROI 절단면에
face-pick으로 배치한 Receiver** - 를 그대로 재현하자 즉시 재현됐다:
Receiver 표시(시안색)만 보이고 CAD 단면도 ray도 전혀 안 보이는 거의
텅 빈 이미지.

`console.log` 계측으로 확인한 결과: `allRenderedFaceIds`(23,195개),
`meshCount`(2), `triangles`(23,195), `glError`(0) 전부 정상이었다 -
GPU에 제출된 삼각형은 있는데 화면 픽셀 히스토그램을 찍어보면 배경색과
Receiver 마커 색상 외에는 아무것도 없었다. 이건 오전에 고쳤던 "후속
수정 4"와 **완전히 같은 근본 원인**이었다: ROI로 선택된 면 집합이
얇은 패널의 윗면/아랫면(수평면)만 포함하고 옆면(수직 벽)은 하나도
없는 경우, 그 면들의 법선은 전부 이 단면의 시야 방향(`viewNormal`)과
거의 수직이라, **어느 방향에서 보든** 투영 면적이 0에 가까워진다.
아침엔 X축 방향에서, 이번엔 Y축 방향에서 발생했을 뿐 - 시야 방향을
바꾼다고 해결되는 문제가 아니라, 애초에 그 면 집합 자체가 이 절단
방식으로는 아무것도 보여줄 게 없는 경우다.

**문제는 버그가 아니라 이 경우를 조용히 방치했다는 것**이었다 - CAD가
안 보여도 아무 설명 없이 텅 빈 이미지만 나오니 "고장났다"고 느껴질
수밖에 없었다.

**수정**: bounding-box 기반 extent 체크(`clippingIsUseful`)는 이
케이스를 못 잡아낸다 - bounding box 자체는 정상적인 깊이를 갖고
있기 때문이다(3D 깊이는 있지만 개별 삼각형이 전부 edge-on). 대신
렌더링된 모든 face의 **법선 방향**을 직접 확인하는 체크를 추가했다:
`sum(face_area * |dot(face_normal, viewNormal)|) / sum(face_area)`
- 각 face의 실제 투영 면적 비율의 가중 평균이다. 이 비율이 2% 미만이면
"이 각도에서는 CAD가 사실상 안 보인다"고 판단한다. 이 경우 렌더링을
포기하는 대신, WebGL 캔버스를 2D 캔버스로 복사한 뒤 상단에 설명
배너("이 각도에서는 CAD 단면이 거의 보이지 않습니다 / 선택된 면이 이
절단 방향과 거의 평행합니다")를 합성해서 반환한다 - ray/Receiver 표시는
그대로 유지되고, 왜 CAD가 안 보이는지 사용자가 바로 알 수 있다.

검증: 문제를 재현했던 정확한 조합(ROI + CAD surface 멀티패치 Emitter +
ROI 절단면 Receiver)으로 재확인 → 설명 배너가 정상적으로 이미지 위에
합성되어 나오는 것을 확인. 정상 케이스(Datum plane Emitter/Receiver)로
재확인 → 배너 없이 기존과 동일하게 정상 렌더링되는 것도 확인(false
positive 없음). `tsc -b`, `vitest run` 100/100 통과.

## 후속 개선 8: 진짜 filled-cap 단면(NX 스타일) 구현 시도 - 부분 성공

사용자가 "단면을 잘랐는데 CAD가 안 보일 수 있냐"고 정당하게 반문했다.
맞는 지적이었다 - 지금까지 쓰던 "GPU 클리핑 평면" 방식은 진짜 단면이
아니라 기존 표면 삼각형을 특정 각도에서 잘라 보여줄 뿐이라, 그 표면이
시야와 거의 평행하면 안 보일 수 있다. 진짜 CAD Section View는 절단
평면과 솔리드의 교차 다각형을 직접 계산해서 채우기 때문에 이런 문제가
원천적으로 없다. 사용자 동의를 받아 이 방식을 구현했다.

**구현** (`frontend/src/features/results/section-cap-geometry.ts`, 신규):
- `computeSectionCapTriangles(scene, faceIds, origin, planeNormal, up, right)`
  - 각 삼각형을 평면과 비교해 교차 segment 추출
  - 교차점들을 근접도 기반으로 용접(weld)해서 닫힌 루프로 체이닝
  - `ShapeUtils.isClockWise` + point-in-polygon으로 hole 중첩 판정
  - `ShapeUtils.triangulateShape`(three.js 내장 earcut 래퍼, public export
    확인됨)로 hole 포함 삼각분할
  - `ray-section-view.ts`에 통합: 기존 GPU 클리핑 렌더링 위에 이 필드
    캡을 추가로 그림(하나가 실패해도 다른 하나가 보완)

**디버깅 과정에서 발견한 실제 버그 2개** (박스 단위 테스트로는 못
잡았던 것들 - 실제 STEP 메쉬(수백 개 교차점, 여러 루프)에서만 드러남):
1. 루프 체이닝 시 이미 방문한 edge를 영구적으로 "소모"하는데, dead-end
   점(진짜 열린 체인)에서 시작한 실패한 walk가 도중에 멀쩡한 루프의
   edge까지 방문 처리해버려서 그 루프도 영원히 못 닫히게 되는 문제 -
   "leaf pruning"(degree-1 점을 반복적으로 제거)을 먼저 하고 나서 walk를
   돌리도록 수정.
2. 좌표 반올림(grid snapping) 기반 용접은 두 점이 grid 경계선을
   사이에 두고 있으면 실제로 거의 붙어있어도 다른 버킷으로 갈려서
   실패하는 고전적 버그 - 실제 거리 비교 기반 클러스터링(`PointWelder`)
   으로 교체.

**한계 (해결 못함)**: 이 두 버그를 고친 뒤에도, 실제 샘플 STEP 파일의
평범한 케이스(ROI 없음, Datum plane Emitter/Receiver)에서 여전히 캡이
채워지지 않는다 - segment는 254개나 나오는데 루프가 0개. 원인을 끝까지
추적한 결과: STEP→메쉬 변환 시 인접한 면(예: 윗면과 옆벽)이 각자 다른
파라메트릭 패치에서 독립적으로 테셀레이션되면서, 물리적으로는 같은
경계선인데 메쉬 정점이 정확히 안 맞는("T-junction crack") 경우가
흔했다. weld tolerance를 0.02mm → 1mm → 5mm까지 올려봐도: 작은
tolerance에서는 진짜 벌어진 틈을 못 잇고, 큰 tolerance에서는 오히려
무관한 점들이 잘못 뭉쳐 degree-4 같은 가짜 분기점을 만들어 더 나쁜
결과가 나왔다(단일 tolerance로 이 실제 파일의 들쭉날쭉한 틈 크기를
전부 커버할 수 없었다). 이런 가짜 분기점을 만나면 채우지 않고 버리도록
방어 코드를 추가했지만, 그 경우 역시 캡 없이 폴백된다.

**결론**: `computeSectionCapTriangles`는 상자 같은 깨끗한 메쉬에서는
정확히 동작(유닛 테스트 3개로 검증됨 - 정확한 넓이 일치, 평면이 형상을
벗어나면 null, 옆면 없는 면 집합은 닫히지 않아 null)하지만, 이번 실제
STEP 파일처럼 패치 경계가 지저분한 메쉬에서는 못 닫는 경우가 있다.
다행히 실패 시 완전히 안전하게 폴백된다 - 캡이 안 생기면 그냥 기존
GPU 클리핑 렌더링(또는 그마저 edge-on이면 설명 배너)으로 돌아갈 뿐,
새로운 회귀는 없다. 그리고 애초 이 세션에서 반복 재현됐던 원래
버그(ROI로 옆면 없이 윗면/아랫면만 선택된 케이스)는 애초에 필요한
메쉬 데이터 자체가 없는 경우라 filled-cap으로도 원천적으로 못 채우는
케이스였다 - 그 경우는 여전히(그리고 앞으로도) 설명 배너가 담당한다.
메쉬를 사전에 전역적으로 용접/정리하는 전처리 단계를 추가하면 더 많은
케이스에서 캡이 채워지겠지만, 그건 오늘 범위를 넘는 별도 작업이다.

검증: `computeSectionCapTriangles` 유닛 테스트 3개 통과(박스 절반 절단
→ 정확히 넓이 100 일치, 평면이 형상 완전히 벗어남 → null, 옆면 누락으로
닫히지 않는 케이스 → null). 실제 브라우저로 기존 두 시나리오(정상
케이스, ROI edge-on 케이스) 모두 재검증 → 정상 케이스는 기존과 동일하게
계속 정상 렌더링(캡 미적용, 회귀 없음), edge-on 케이스는 설명 배너가
여전히 정상 표시됨(문구를 "옆면도 없고 채울 만큼 연결된 면도 없다"로
갱신). `tsc -b`, `vitest run` 20 files / 103 tests 통과.

## 후속 개선 9: 배경 흰색 + Component 지정 색상 + Receiver 시인성

filled-cap 작업은 실제 파일에서 완전히 검증되지 않아 잠시 보류하고
(다른 도면으로 별도 테스트 예정), 이번엔 스타일을 요청대로 변경:

- **배경을 흰색으로 변경** (`0x0b1220` 어두운 남색 → `0xffffff`).
- **기구 부품 색상을 Component에 지정된 실제 색상으로** - 기존엔 STEP
  원본 색상이 거의 검정이라 어두운 배경에서 안 보이는 문제 때문에
  고정 회색(`sectionGeometryColor`)으로 덮어썼는데, 이제 배경이
  흰색이라 어두운 색도 오히려 잘 보인다. 메인 3D 뷰어와 완전히 같은
  색상 판정 로직(`resolveComponentColor` - CAD-authored color 우선,
  없으면 팔레트 순환)을 `three-viewer-canvas.tsx`에서
  `frontend/src/features/viewer/scene-geometry.ts`로 추출해 공유 -
  같은 컴포넌트가 메인 뷰어와 리포트 이미지에서 항상 같은 색으로
  보이도록 함.
- **Ray/Receiver 색상을 흰 배경용으로 교체**: 기존 `rayPathStyles`의
  연한 초록(Direct)·연한 노랑(반사광)은 어두운 배경 전용 팔레트라
  흰 배경에서는 거의 안 보임 - 리포트 전용의 진한 초록(`#15803d`)·
  진한 amber(`#b45309`)로 교체. Receiver 마커도 옅은 시안(`#22d3ee`)
  대신 흰 배경에서 또렷한 진한 cyan(`#0e7490`)으로 교체.
- 경고 배너도 어두운 테마 전용 스타일(어두운 바탕 + 밝은 글씨)에서
  흰 배경에 맞는 연한 빨강 경고 박스 스타일로 변경.
- 범례에서 고정 "CAD 단면" 회색 스와치는 더 이상 의미가 없어 제거하고,
  "기구 부품은 Component에 지정된 색상 그대로 표시됩니다" 안내 문구로
  대체.

검증: 실제 브라우저로 재확인 → 흰 배경, 실제 컴포넌트 색상(이번
샘플에서는 마침 STEP 원본이 거의 검정이라 검정 패널로 나오지만, 흰
배경 위라 뚜렷이 보임), 진한 초록 Direct ray, 진한 cyan Receiver 표시
모두 정상 렌더링 확인. `tsc -b`, `vitest run` 20 files / 103 tests
통과.

## 검증

- 프런트 `tsc -b` 통과. `vitest run` 19 files / 100 tests 통과
  (`computeSectionPlaneBasis` 신규 테스트 4개 - 일반 케이스, 대각선
  케이스, 수직 축퇴 폴백 케이스, zero-normal null 케이스).
- 실제 브라우저(Playwright + 로컬 Chrome)로 STEP 샘플 import 후 Emitter/
  Receiver를 마주보게 배치(150×150mm, 179° acceptance)해 ray trace 실행
  → 12,677 hits(63.4%) 확인. Result 창 Ray summary 탭에서 Ray Section
  View 섹션에 640×400 PNG가 정상 렌더링되고, 그 Receiver에 도달한 광선만
  (초록색, direct) CAD 단면 위에 겹쳐 그려지는 것을 확인. 첫 시도에서는
  Receiver가 모델에서 멀 때 프레이밍이 한쪽으로 쏠리는 문제가 있어
  비대칭 frustum 계산으로 수정 후 재검증.
