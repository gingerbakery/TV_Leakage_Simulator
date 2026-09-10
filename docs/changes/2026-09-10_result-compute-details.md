# 2026-09-10 — Ray summary 연산 장치 상세 정보 접기

- Compute device 카드의 기본 표시는 상태 아이콘·실제 실행 상태 제목·펼침 화살표만 남긴다. GPU 정상 실행의 초록 배경은 유지한다.
- GPU/CPU requested, GPU 이름, Provider, CUDA batch, Resident, CPU small waves, 샘플 계약, 상세 사유는 기본으로 접고 제목 행 클릭 시 펼친다. 다시 누르면 접힌다.
- CPU 실행·GPU 활성·CPU 보조·CPU 대체 실행 상태의 판정과 경고색은 변경하지 않는다. 진단 정보는 삭제하지 않고 상세 영역에서 그대로 확인한다.
- 키보드 Enter/Space 조작, `aria-expanded` 및 `aria-controls`를 제공한다. 다른 해석 결과로 전환하면 다시 접힌 상태로 시작한다.
- 별도의 Acceleration structure / BVH 정보 및 해석 계산은 변경하지 않는다.
- 검증: 프론트엔드 279개 테스트(41개 파일), TypeScript 및 production build 통과. 기존 Result Hook 의존성 lint 경고와 bundle/Canvas 테스트 경고는 유지된다.
- Edge headless에서 UI fixture를 사용해 라이트·다크·CPU 대체 경고 상태의 기본 접힘과 클릭/Space/Enter 동작을 확인했다. 이는 표시 기능 검증이며 실제 CUDA 실행이나 GPU 성능 검증이 아니다.
- 8788에서 새 frontend bundle 응답을 확인했다. 서버 재시작 없이 작업 저장 후 페이지 새로고침으로 적용한다.
