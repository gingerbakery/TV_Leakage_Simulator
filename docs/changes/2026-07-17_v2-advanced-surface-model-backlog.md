# V2 고급 표면 모델 백로그 정리

## 변경 목적
- RT-2C에서 사용하는 Specular, Gaussian, Lambertian 모델로 표현하기 어려운 표면 거동을 V2 백로그로 분리한다.
- 향후 표면 광학 고도화 시 검토 항목이 누락되지 않도록 V2 진입 게이트와 리마인드를 문서화한다.

## 추가 문서
- `docs/v2-advanced-surface-models.md`

## 정리한 네 가지 케이스
1. 거친 확산면의 방향 의존성
   - 후보: Oren–Nayar, Energy-preserving Oren–Nayar
2. 입사각에 따른 반사율과 광택 변화
   - 후보: Fresnel–Schlick, Microfacet GGX/Beckmann
3. 방향성이 있는 표면
   - 후보: Anisotropic Gaussian/GGX
4. 역반사·후방산란 및 비대칭 lobe
   - 후보: Retroreflective lobe, analytic lobe mixture, 측정 BSDF

## 문서 연동
- `docs/ray-tracing-design.md`에 V2 표면 광학 고도화 게이트를 추가했다.
- `docs/risks-and-open-issues.md`에 V2 optical surface 및 프레임워크 전환 리마인드를 추가했다.
- `docs/project-reminders.md`에 명시적 리마인드와 중요 보류 백로그를 구분해 기록했다.

## 적용 원칙
- V1은 Specular, Gaussian, Lambertian 혼합 모델로 우선 검증한다.
- V2 모델은 기능 수를 늘리기 위해 추가하지 않는다.
- 실측 오차가 설계 우열, Receiver 분포 또는 절대 밝기 정합에 영향을 줄 때 우선 적용한다.
