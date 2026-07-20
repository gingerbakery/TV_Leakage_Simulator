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
- release 패키지 경량화 전략
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
- ray tracing 핵심 기능과 데이터 계약이 안정화되는 시점에 전환 필요성을 다시 평가한다.
- 고려 중인 구성:
  - Next.js(React) + TypeScript
  - Tailwind CSS + shadcn/ui
  - Zustand
  - Three.js 또는 React Three Fiber
  - backend/database 구조 재검토
- Three.js viewer는 전체 전환보다 먼저 적용되어 현재 사용 중이다.
