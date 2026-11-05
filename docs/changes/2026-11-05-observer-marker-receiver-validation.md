# 관찰 점의 기하 검증과 시야각 Receiver 0 Ray 진단

2026-11-05 · source `db25ced` 기반 미커밋 변경.

## 사용자 사례와 판정 범위

확산판 바닥 기준 후방 균일 방출, 같은 광원·재질·Block, 1,000,000 Ray, 최대 반사 10회라는 조건을 전달받았다. 관찰 역추적에서 검출된 위치가 공중/무관한 외장 중앙에도 있었고 생성한 시야각 Receiver의 순방향 hit가 0이었다. 이 조건만으로 사용자의 해당 도면에서 원인을 확정할 수 없다. 작업 폴더의 기존 .bitsam은 9월 파일이며 이번 결과와 같은 입력이라고 간주하지 않았다. 실제 TV 도면/GPU로 재현했다는 주장은 하지 않는다.

Leak 위치를 외관의 틈·홀로 정의할 경우, 관찰자에게 보이는 내부 면이나 가상 발광면과 별개다. 실제 개구부 좌표를 확정하지 못한 상태에서 외곽 박스 교점이나 평균 좌표를 Leak 위치라고 만들면 안 된다. 이번 변경은 경로/실제 보이는 면의 기하 검증이며 자동 외관 개구부 추출기가 아니다.

## 발견·수정한 코드 문제

1. 후보 Receiver의 중심이 검출 격자 범위의 중심이 아닌 **휘도 가중한 3D 후보 중심**이었다. 밝기 분포가 치우친 연결 영역에서는 최강 대표 경로마저 Receiver 범위에서 벗어날 수 있는 합성 사례를 만들었다. 이제 관찰 grid의 실제 행/열 범위와 U/V/spacing으로 footprint 중심을 계산하고, canonical Receiver basis로 면적을 변환한다. 저장된 이전 후보에 footprint가 없으면 실제 대표 경로의 보이는 끝점을 anchor로 사용한다.
2. 가상 Datum 광원의 위치는 실제 CAD 표면이 아니다. 유효한 시야 경로라도 이 점을 외관의 홀 위치로 그릴 수 없다. 새 결과는 독립 CPU 기하 검증 후 `cad_surface`/`virtual_emitter`를 구분한다. 가상 발광면의 공중 좌표는 점으로 표시하지 않고 검출 후보/경로는 유지한다.
3. 정밀 해석에 전달된 Receiver에는 검출 당시 대표 경로가 없어 불일치를 진단할 수 없었다. `observer_reference_path`를 전달·저장하고, 현재 광원·재질·Block·최대 반사·Receiver·Acceptance·ROI로 검증한다. 이 경로는 진단 자료이며 hit/flux에 추가하지 않는다.
4. GPU counter sampler 결과를 UI가 기존 sample contract만 허용해 거절할 수 있는 연결 누락도 확인했다. resident transport 계약·성공 수와 실제 CUDA 성공 수가 모두 있는 경우에만 새 sampler를 수용한다. GPU 성공 수가 0이면 거절하는 검사를 유지한다.

## 독립 CPU 경로 검사

- 대표 source가 현재 enabled/positive source의 실제 삼각형 위에 있고 관찰 경로 쪽으로 발광 가능한지 검사한다. 균일 Sphere/Lambertian/Isotropic 및 normal flip을 현재 방출 법칙으로 판정한다.
- 모든 반사 vertex가 실제 CAD first-hit인지, 연속 경로에 기구물/Block 차폐가 있는지 다시 추적한다. 현재 forward BRDF 또는 delta 지원, 최대 반사/k_brdf도 검사한다.
- 마지막 보이는 점은 camera 쪽에서도 재검사해 실제 면과 가상 source를 구분한다. 평균으로 공중에 놓인 좌표를 대표 점으로 사용하지 않는다.
- 모든 Monte Carlo 표본을 다시 계산하지 않는다. 표시된 대표 경로만 native CPU BVH로 검사하며 불가하면 CPU reference BVH를 사용한다. 최대 경로 길이에 맞춰 검증 batch의 query 수도 제한한다. 현재 준비된 trace mesh를 사용하므로 CAD 누락 자체나 어떤 부품이 외장인지까지 자동 증명하지 않는다.
- invalid 대표 경로 셀은 새 후보에서 제외한다. 원본 집계와 거절 사유는 result에 보관하고 실패 수를 표시한다. 부분 결과/이전 저장 파일에 검증 정보가 없으면 검증 완료로 간주하지 않는다.

## 0 Ray의 해석과 진단

같은 물리 조건이어도 평행 관찰 radiance estimator와 유한 면적 Receiver의 무작위 forward hit 수는 같은 양이 아니다. 특히 radiance 역추적은 결정적인 에너지 컷오프 없이 roulette/MIS를 쓰며, forward는 설정에 따라 packet 에너지 컷오프로 경로를 종료한다. 100만 Ray/10회라는 숫자만으로 표본 부족, 종료 정책, 위치/방향 오류 중 하나를 단정하지 않는다.

생성 Receiver는 원래 대표 경로의 마지막 segment가 실제 Receiver 판정 함수(위치·면적·방향·normal flip·Acceptance·ROI)에 들어오는지 검사한다. Receiver까지 연장한 경로를 현재 geometry/source/material에 다시 검사한다. 결과에는 다음을 구분해 남긴다.

- 광원 위치/방출/출력 불일치, 차폐, 공중 반사점, 반사 재질/방향 불일치, 최대 반사 초과, Receiver 조건 불일치
- 대표 경로는 유효하지만 실제 forward hit는 0인 경우
- 현재 forward 에너지 컷오프 횟수/종료 정책. 이는 그 대표 경로가 잘렸다는 직접 증거는 아니며 원인 분석 자료다.

## 저장된 결과를 다시 전체 계산하지 않고 검사

후보의 **경로 검증**은 `/api/raytrace/validate-observer`로 현재 정밀해석 조건과 생성될 Receiver를 검사한다. full Preview나 Monte Carlo worker를 시작하지 않고 중단 checkpoint를 취소하지 않는다. 같은 형상은 준비 cache를 재사용한다. 실행 중 해석이 있으면 진단 요청을 거절하며, 검증용 native 기하 query를 실제 simulation Ray hit로 세지 않는다.

geometry-only 응답은 forward hit/에너지 컷오프 횟수를 `null`로 반환한다. 실제로 순방향 0 Ray를 다시 실행했다는 뜻이 아니다. 기존 저장 후보의 대표 경로만 검사하며, 모든 저장 점의 외관 위치를 소급 확정하지 않는다. 현재 source와 Preview source가 다르면 그 불일치도 검사 결과에 드러날 수 있다.

## 검증

합성 검사는 불투명 벽 통과 경로 거부, 공중 반사 vertex 거부, 실제 mirror 경로, source flip/최대 반사/재질 불일치, virtual source와 외관 개구부의 구분, Receiver miss, valid reference+0 forward의 분리 및 grid 불변을 포함한다. API 검사는 tracer가 호출되거나 job이 생성되지 않는 것을 확인한다. UI 검사는 치우친 밝기 centroid가 대표 경로를 놓치는 사례를 재현하고 수정 후 footprint 수용을 확인하며, 저장 경로 검증이 새 Preview를 시작하지 않는지 검사한다.

실제 사용자 사례의 원인을 확정하려면 해당 저장 결과에서 경로 검증을 실행하거나 현재 .bitsam/결과가 필요하다. CPU 합성 회귀 통과는 실측 TV 정확성이나 실제 GPU 검증의 증거가 아니다. 설치·서버 재시작·커밋·푸시는 수행하지 않았다.

최종 검증: 관련 backend 123건, API client/Preview model·panel·store/저장 직렬화/일반 RT UI/Shell 등 frontend 172건과 TypeScript 포함 production build 통과. 후방 Uniform Sphere·100만 Ray 설정·최대 10회 조건의 합성 API 검사에서는 Monte Carlo runner/job을 생성하지 않고 geometry-only 판정을 반환하는 것을 확인했다. 100만 Ray를 실제 실행해서 사용자 도면의 0 hit를 재현한 검사가 아니다. 마지막 source-kind 구분 후 신규 기하/API 검사 6건 재실행 통과. `git diff --check` 통과. 기존 큰 bundle 안내와 jsdom canvas 경고는 남아 있다.
