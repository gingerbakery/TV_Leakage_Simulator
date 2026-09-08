# 2026-09-07 가로 틈 시연 자료와 명시적 진입 링크

시연 링크는 `http://127.0.0.1:8788/?demo=horizontal-seam`입니다. 프런트엔드는 이 명시적인 시연 링크에서만 고정된 개발용 모델과 설정을 불러오고, 실제 CPU 해석을 완료한 뒤 3D 빛샘 결과로 진입합니다.

## 시연 자료

기존 `/outputs/{basename}` 제공 경로를 그대로 사용합니다. 새 백엔드 엔드포인트나 경로 권한 변경, 서버 재시작을 추가하지 않았습니다.

- Manifest: `/outputs/horizontal-seam-demo.json`
- 계약: `schema=horizontal-seam-demo.v1`, `defaultCase=16x`, `orientation=right`
- 모델: 오른쪽 측면의 실제 가로 틈, 높이0.3 mm, 깊이 방향 길이12 mm
- 기본 광원: 총16 lm. 시연에서 빛샘을 확인하기 위한 상대 광원 조건이며,16 nit를 뜻하지 않습니다.
- 기본 자료: `demo-horizontal-gap-0p3-16x.step`, `demo-horizontal-gap-0p3-16x.bitsam`
- 추가 자료: off/1x/4x의 동일 틈과0/1/4 lm 광원 설정

Manifest의 각 Case는 `id,label,sourceLumen,cadName,cadUrl,projectUrl`을 갖습니다. STEP을 업로드할 때는 `cadName`을 사용하므로 프로젝트에 저장된 도면 이름과 일치합니다.

## 실제 실행

BITSAM은 설정 전용이며 과거 해석 결과를 넣지 않았습니다. 자동 진입은 기존 업로드, 도면 가져오기, BITSAM 호환성 검사, 설정 복원과 새CPU 해석 경로를 사용합니다. 광선 수는500,000개이며 현재 개발환경의 검증 실행은 약28초였습니다. 컴퓨터와 첫 실행 준비 비용에 따라 시간은 달라집니다.

시연 결과를 그림이나 임의의 광량으로 대신하지 않습니다. 완료된 해석과 현재 CAD를 연결한 후 기존 결과 기반3D 렌더링을 사용합니다.

## 재준비

`scripts/stage_horizontal_seam_demo.mjs`가 `samples/side_seam_horizontal`의 정해진4종 STEP과 설정 전용BITSAM만 출력 폴더에 복사하고 Manifest를 생성합니다. 브라우저를 새로고침하거나 기존Case·결과를 수정하지 않습니다.

Manifest와8개 파일에 대해 실제HTTP200 및 STEP/BITSAM 형식을 확인했습니다. 기록은 `outputs/local-demo-server/demo-file-access.json`입니다. HTTP 접근 확인은 실제 브라우저 화면 검증과 구별합니다.
