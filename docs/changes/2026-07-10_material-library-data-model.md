# Material Library 데이터 구조 설계 정리

## Summary
- `material`과 `surface finish`를 분리하는 구조로 설계 방향을 고정했다.
- `Part assignment`와 `Face override`를 모두 지원하는 데이터 모델을 정의했다.
- `corrosion preset`, `BSDF asset`, `flattened optical profile` 개념을 포함해 후속 구현 경로를 정리했다.

## Updated documents
- `docs/material-library.md`

## Key decisions
- 엔진 런타임은 당분간 `flattened optical profile` 중심으로 유지
- UI/저장 계층은 `base material + surface finish + assignment` 구조로 확장
- `BSDF`는 V1에서 파일 등록/연결만 우선 지원
- `corrosion`은 자유 입력보다 `preset` 중심으로 운영

## Follow-up implementation direction
1. `types.py`에 `BaseMaterial`, `SurfaceFinish`, `MaterialAssignment` 계층 추가
2. 기존 `MaterialProfile`과 호환되는 flatten 단계 추가
3. Material library UI를 preset/assignment 중심으로 개편
4. Part assignment와 Face override 적용 우선순위 구현
