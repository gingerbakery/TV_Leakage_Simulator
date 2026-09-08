# 완전 차폐와 코너 틈 시험 샘플

## 목적과 범위

사내 CAD를 반출하지 않고 닫힌 조립체의 의도적인 LCD/데코 틈과 인위적인 절단 경계를 구분해 검증할 수 있도록 로컬 STEP/BITSAM 샘플을 추가했다. 기존 ROI, ray tracer, 프런트엔드 동작은 변경하지 않았다. 암실 관측 렌더러는 아직 구현하지 않았다.

## 추가 자료

- `scripts/generate_closed_enclosure_samples.py`: CadQuery로 불투명 차폐 조립체 4종 생성, 실제 STEP importer와 CPU ray tracer 실행, 틈 통과 진단과 수광 검증, JSON·그림 출력.
- `scripts/closed_enclosure_project_helper.mjs`: 기존 workspace와 BITSAM 생성·파서·장면 호환·복원 코드를 사용한 설정/저장 결과 패키징.
- `samples/closed_enclosure/`: `closed`, `gap_0p2`, `gap_0p5`, `roi_cut_open_demo` 각각의 STEP·BITSAM·검증 JSON 및 사용 안내.
- `docs/darkroom-rendering-prototype-plan.md`: 전체 광학 형상과 관측 ROI를 분리하는 계약, Receiver 필터만으로 절단 경계를 해결할 수 없는 이유 반영.

## 검증

합성 1 lm 광원, 20,000 primary rays, seed 20260903, 최대 깊이 4, 반사율 0.2, `compute_backend=cpu`, `intersection_provider=python_cpu`, BVH 조건을 사용했다. 이는 CPU 기하·수광 검증이다.

| 장면 | 외부 도달 수 | 총 수광 광속 |
| --- | ---: | ---: |
| closed | 0 | 0 lm |
| gap_0p2 | 12 | 0.000292840530 lm |
| gap_0p5 | 118 | 0.002161932618 lm |
| roi_cut_open_demo | 13,152 | 0.082671231610 lm |

최종 배포 파일은 `summary` 기여 통계 모드다. 먼저 실행한 상세 모드와 수광 도달 수·광속은 동일했다. 4개 BITSAM의 합계는 2,764,311 bytes이며 파서 재읽기, 대응 scene 호환, 원본 결과의 격자·경로·통계 보존, CPU 설정, ROI 비활성을 검사했다. 틈 샘플의 저장된 수광 경로가 실제 의도한 전면 틈을 통과하는지 확인했다. 저장 경로는 개수가 제한된 진단 표본이다.

로컬 프로그램에서 `gap_0p5.step`을 가져와 4개 부품이 표시되는 것을 확인하고 대응 BITSAM을 복원했다. 결과 창에 20,000 rays, 118 hits, CPU 실행이 표시되고 viewer에 118/120 저장 경로가 표시되는 것을 확인했다. 자동화 도구의 숨겨진 파일 input 클릭은 chooser를 열지 못했으며 실제 `Import CAD`와 상단 `Load` 버튼을 통한 업로드로 확인했다.

복원 설정으로 UI의 `Run Ray Tracing`도 실행했다. `rt3-4315faa4afb3`가 Complete이며 CPU/`numba_cpu`, 20,000 rays, 113 receiver hits, 표시 해석 시간 10.114 s였다. 생성기의 `python_cpu` 결과(118 hits)와 일치하지 않았고, 이는 실행 경로 차이를 포함한 수치 동등성 미검증 사항으로 README에 명시했다. 원인을 표본 잡음만으로 단정하지 않았다. 배포 BITSAM은 기준 생성기 결과를 그대로 유지한다.

`samples/closed_enclosure_samples.zip`에는 네 STEP/BITSAM 쌍과 안내·검증 요약·그림을 포함한다. 생성 중간 JSON과 실행 도구는 저장소의 원래 경로에 남긴다.

검증 그림은 직접 열어 틈 표시와 축/주석의 겹침을 확인하고 수정했다. 기존 제품 코드의 변경이 없어 전체 회귀 테스트는 실행하지 않았다.

## 해석 한계와 기존 동작

- 절단 예시는 CAD를 명시적으로 Boolean 절단한 음성 대조군이다. 현재 프런트엔드 ROI face 선택을 정확히 재현하지 않는다.
- 작은 틈의 표본이 적으므로 현재 광속 비율을 정량적인 제품 밝기 비교로 사용하지 않는다. 실제 nit와 육안 외관은 별도 관측 계산과 사내 실측 보정이 필요하다.
- 현재 BITSAM fingerprint는 0.2/0.5 mm 틈 파일을 구분하지 못한다. 반드시 같은 이름 STEP/BITSAM을 짝지어 연다.
- BITSAM 결과 복원 후 별도 결과 창·경로에는 저장 결과가 보이지만 Step 05 상태는 `not run`으로 남는 기존 표시 차이를 관찰했다. 샘플 안내에 기록했으며 제품 코드 변경은 하지 않았다.

커밋·푸시·릴리스는 수행하지 않았다.

## 결과 연계 3D 미리보기의 첫 데이터 작업

절대 nit 보정을 후속으로 두고 실제 틈의 위치·형태·가림을 우선하는 사용자 범위에 맞춰, `scripts/extract_closed_enclosure_leakage_samples.py`와 개발용 `leakage_preview_samples.json`을 추가했다. 기존 저장 경로의 마지막 선분과 알려진 합성 외면을 교차시켜 출구점·방향·상대 가중치·경로 ID를 기록했다. 해석 재실행 없이 닫힌 샘플 0개, 0.2 mm 틈 12개, 0.5 mm 틈 118개가 추출되었고 모든 좌표·방향·선분·틈 포함 검사가 통과했다. 일반 CAD 자동 출구 검출이나 완전한 방향별 밝기 재구성은 제공하지 않는다. 제품 UI와 Ray Tracing 엔진은 변경하지 않았다.
