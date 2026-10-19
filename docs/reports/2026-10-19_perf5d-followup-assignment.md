# PERF5D-REVIEW-04: 발견 사항 수정 재검토

REVIEW-02의 동일 검증 담당 `Dalton / gpt-6.1-sol / high`에게 맡긴다.
기존 위임의 읽기 전용 권한, 금지 사항, 판정 기준, 총 5단계 진행 표시와
15분 상한은 동일하다. 메인은 실제 GPU 측정과 회귀 검사를 직렬 실행한다.

- 저장소: `C:\Users\Administrator\Documents\TV leakage simulator main`
- Source checkout: `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`, 미커밋 변경 포함.
- Snapshot: 저장소 아래 `outputs/validation/2026-10-19-perf5d-review-04/snapshot`.
- Manifest: 같은 review-04 폴더의 `manifest.json`.
- Manifest SHA-256: `3eb87abd169c80401ebedf59a391b0f6621137f1207617515277fe54586f3cb3`.
- 302개 파일의 소스 검토 묶음이며 실행 환경과 사용자 CAD는 포함하지 않는다.
- 쓰기 권한 없음. 소스 실행/import, GPU·API·테스트·benchmark는 금지한다.

## 수정 확인

1. 지침, 시작 hash, 권한 확인.
2. 같은 모드 최초 실행 대비 Receiver strict 및 전체 physical 반복 판정이
   최종 필수 gate인지 확인한다. 전체32ULP 보조 집계는 계속 별도 미통과다.
3. 사전 고정한 두 baseline 파일의 SHA-256 인증과 변조·누락 거부를 확인한다.
   기준 hash는 review-02 snapshot의 실제 pre-5D 파일과 대조한다.
4. tree 불일치와 뒤늦은 transport 예외에서 arrays·이전 performance·gate·
   payload 참조가 보존되는지 확인한다. 새 orchestration 반례 테스트도 읽는다.
5. 수정으로 생긴 명백한 오판정 위험, 남은 실행 증거와 최종 hash를 보고한다.

이전 판정은 그대로 보존한다. 새 판정은 코드 수정 범위에 한하며 메인이
실행 중인 새 GPU 결과를 직접 확인하지 않았다면 NEEDS_RUN으로 표시한다.
사용자 형상이나 실행 근거를 외부로 보내지 않는다.

REVIEW-03 snapshot과 실행 실패 기록은 별도로 보존한다. 추가한 제외면
기록이 존재하지 않는 mesh 속성을 참조해 입력 보존 중 중단됐고, 수정본은
실제 `face_metadata.trace_excluded`를 보존한다. 실제 TriangleMesh를 사용하는
직렬화 회귀 검사를 추가했다. REVIEW-03을 통과 근거로 승계하지 않는다.
