# Model Import의 공통 보완 형상

Preview에 있던 BlockerEditor를 Model Import로 이동했다. 활성 CASE의 `공통 보완 형상`을 기본 닫힘으로 표시하고, 내부는 Accessory CAD 목록/추가 → Block 편집 순서다. Components 트리와 재질 라이브러리에는 변경을 가하지 않았다. 도움말은 `(?)`에 두고 활성 CASE와 연결 상태만 간결하게 표시한다.

ACC 추가 시 Model Import를 유지해 같은 영역에서 계속 작업할 수 있다. 주 CAD Import의 기존 이동 동작은 유지한다. ACC/Block의 연결된 원본 CASE와 각각의 `연동 해제` 버튼을 제공하며 기존 unlinkCaseSetup을 사용한다. 파일이나 Block 값을 지우지 않고 해당 연결만 해제한다. 여러 CASE 공유는 기존 상단 Copy Setup을 그대로 사용한다. 자동 좌표 정렬이나 신규 CAD 교체 API는 추가하지 않았다.

기존 CASE 저장 구조와 Copy/Link 동기화는 유지한다. 새 편집기는 공통 영역이 열릴 때만 마운트하며, 닫기/탭 이동 시 기존 BlockerEditor 소유자 검사를 통해 그리기 모드만 정리한다. 실제로 만든 region은 유지하고 빈 생성 placeholder 정리는 기존 정책을 따른다. 형상 준비/해석 중에는 ACC·Block 편집을 잠근다. CASE 복원은 SimulatorShell이 담당하며 이전 CASE의 live Preview identity가 남아 있으면 새 CASE에 bind하거나 그리기 상태를 정리하지 않고 복원을 기다린다.

검증: 공통 영역의 기본 접힘과 ACC→Block 순서, ACC 업로드 후 편집 위치 유지, 생성 region의 닫기/탭 이동 보존, 생성 모드/ROI box 해제, 복원 전 CASE 값 비변경, 독립 ACC unlink와 Block link 보존, legacy Allowed Area migration, 기존 BlockEditor 동작, CASE Copy/Link store 회귀를 확인했다. 관련 frontend 8개 파일 129개 검사와 production build가 통과했다. 기존 jsdom canvas 미지원 메시지와 번들 크기 경고는 남아 있다. UI 위치 이동이며 광학 계산·GPU 커널 변경 또는 실제 GPU 실행 검증이 아니다.

커밋/푸시·서버 재시작·설치는 수행하지 않았다.
