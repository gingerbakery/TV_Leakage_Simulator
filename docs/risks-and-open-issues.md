# 리스크 및 오픈 이슈

## 현재 리스크
- STP/X_T import의 정합성은 모델별 편차가 있을 수 있다.
- move/tilt gap은 근사 모델이라 실제 기구 휨과 완전히 일치하지 않는다.
- 절대 nit 값은 보정 상수 기반이므로 실측 정합이 추가로 필요하다.
- 대형 CAD 어셈블리에서 성능 저하 가능성이 있다.

## 오픈 이슈
- BSDF 실데이터 연결 방식 구체화
- material assignment의 저장 포맷 확정
- viewer에서 face 다중 선택 UX 고도화
- 사내 배포용 EXE 코드 서명과 버전 업데이트 정책
- clone 후 실행 부트스트랩 절차 정리

## 단계 리마인드

- 전체 리마인드 목록: `docs/project-reminders.md`

### V2 optical surface 고도화
- V2 phase 진입 시 `docs/v2-advanced-surface-models.md`를 반드시 재검토한다.
- 검토 대상:
  - Oren–Nayar 계열
  - Fresnel + Microfacet GGX/Beckmann
  - Anisotropic Gaussian/GGX
  - Retroreflective lobe 및 측정 BSDF

### 전체 프론트엔드/백엔드 프레임워크 전환
- React + TypeScript, Tailwind CSS, shadcn/ui, Zustand, Three.js와 FastAPI
  전환은 13단계까지 완료되었다.
- v1.0.0 경량 데스크톱 패키지에서 React production UI와 Python 계산
  서버의 통합 기동을 검증했다.
- `main` 최종 반영 후 남는 운영 과제는 사내 PC 배포 검증과 코드 서명이다.
