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
