# STEP component 선택 범위 진단

## 확인 대상

- 화면에서 선택된 파일: `_uploads/tv_leakage_roi_left_bottom_no_gap_15.stp`
- 원본 샘플: `samples/tv_leakage_roi_left_bottom_no_gap.stp`
- 화면 선택 항목: `STEP Solid 3`

## 결론

- 이번 현상은 importer가 서로 다른 solid를 잘못 합친 문제가 아니다.
- 업로드본과 원본 샘플의 SHA-256이 동일하다.
- 원본 STEP은 총 4개 solid이며, importer 결과도 4개 component로 유지된다.
- `STEP Solid 3`은 `Frame_Middle_FMB`에 해당하며, mesh 연결성 검사에서도 하나의 연결된 island로 확인된다.

## 측정 결과

| Component | Face 수 | 연결 island 수 | Bounding box (mm) |
|---|---:|---:|---|
| STEP Solid 1 | 24,192 | 1 | `(0,0,0) ~ (60,60,30)` |
| STEP Solid 2 | 4,608 | 1 | `(24,38,30) ~ (60,60,33)` |
| STEP Solid 3 | 7,808 | 1 | `(0,18,18) ~ (60,60,30)` |
| STEP Solid 4 | 14,336 | 1 | `(0,0,33) ~ (60,60,45)` |

## STEP Solid 3이 여러 형상처럼 보이는 이유

- 샘플 생성 시 `Frame_Middle_FMB`는 수평 rail과 corner 지지 구조를 `union`하여 하나의 부품으로 만들었다.
- 두 구조는 접합 영역에서 실제로 연결되어 있으므로 STEP에서도 하나의 BRep solid다.
- 관측 각도에 따라 서로 떨어진 판처럼 보일 수 있지만, CAD topology 기준으로는 한 부품이므로 함께 선택되는 것이 정상이다.

## ROI 통합 영향

- ROI 통합은 STEP mesh에 adaptive subdivision을 추가했다.
- subdivision 과정은 원래 `step_component_id`와 `step_component_name` metadata를 그대로 복사한다.
- 따라서 ROI 통합이 서로 다른 component를 하나로 병합하지는 않는다.
- 최근 발생했던 삼각형 선, edge pattern, 투명 노이즈는 subdivision triangle을 렌더링 경계선처럼 표시하면서 발생한 시각화 문제였고 component topology 문제와는 별개다.

## 현재 importer의 실제 한계

- 현재 component tree는 STEP assembly/part occurrence가 아니라 OCP의 BRep `SOLID` 단위를 기준으로 한다.
- STEP의 원래 부품명과 assembly hierarchy를 완전히 복원하지 못해 `STEP Solid 1` 같은 일반 이름을 사용한다.
- 양산 CAD에서 한 part가 여러 body를 포함하거나 export 과정에서 body가 fuse되면 사용자가 기대하는 assembly tree와 다르게 보일 수 있다.

## 후속 개선 제안

- STEP XDE/STEPCAF 기반으로 `Assembly > Part occurrence > Body/Solid > Face` hierarchy를 읽는다.
- component tree에 Part 선택과 Body/Solid 선택을 구분한다.
- 각 항목에 body 수와 연결 island 수를 표시해 선택 범위를 사용자가 미리 알 수 있게 한다.
- 현재 샘플의 FMB를 독립 이동해야 한다면 원본 샘플을 별도 solid로 재작성하거나 기존 local face move를 사용한다.
