# 긴급 CAD Import Viewer 복구

## 증상

- `tv_leakage_*.stp` 파일을 Import CAD로 불러온 뒤 Three.js viewer에 모델이 표시되지 않는 현상이 보고됐다.

## 점검 결과

- 원본 샘플 STEP 3개는 모두 OCP direct reader로 정상 파싱됐다.
- 전체 모델은 116 faces, 64 vertices, 4 components로 변환됐다.
- 좌/우 하단 ROI 모델은 각각 88 faces, 51 vertices, 4 components로 변환됐다.
- 사용자가 업로드한 `_uploads` 내부의 최신 STEP 파일 3개도 `/api/scene`에서 동일하게 정상 변환됐다.
- 따라서 STEP 파일 손상이나 CAD importer 실패가 아니라, scene 응답 후 viewer 갱신 순서가 취약한 문제로 범위를 좁혔다.

## 긴급 수정

- Web UI 버전을 `v0.9.3`으로 갱신했다.
- scene 응답에 vertices/faces가 실제로 존재하는지 검증하도록 보강했다.
- mesh를 수신하면 Component/ROI/Emitter/Receiver 보조 UI 초기화보다 먼저 Three.js viewer에 즉시 전달한다.
- CAD 로드 직후 `Fit` camera preset을 적용하고, 다음 animation frame에서 한 번 더 viewer와 Fit을 동기화한다.
- scene API 오류 내용을 숨기지 않고 Model Import 상태와 Result에 표시한다.
- 로드 성공 상태에 face 수와 component 수를 함께 표시한다.
- `Load demo CAD`가 실제 TV STEP 샘플인 `tv_leakage_full_assembled_no_gap.stp`를 불러오도록 변경했다.

## 검증

- Web UI `v0.9.3`에서 TV STEP 샘플을 직접 로드했다.
- 화면에 116 faces, 4 components, OCP tessellation 성공 상태가 표시되는 것을 확인했다.
- Full CAD View와 ROI View 모두에서 실제 TV STEP mesh가 렌더링되는 것을 확인했다.
- 브라우저 warning/error 로그가 없는 것을 확인했다.
- 세 샘플 STEP의 backend import와 scene API 응답을 확인했다.
- Python 문법 검사와 전체 단위 테스트 10개가 통과했다.
