# BitSam / LightTools 공통 STEP 검증 모델

## 목적

사내 TV 형상을 외부로 반출하지 않고, 자체 제작한 STEP와 동일한 광학 조건을
BitSam 및 사내 LightTools에 적용하여 계산을 비교한다. LT 결과는 아직 없으므로
이번 결과를 LT 또는 실측 nit 정합 완료로 해석하지 않는다.

## 전달 자료

생성 폴더: `outputs/RT_validation_kit_2026-09-20`

- STEP 10개: 1/2회 반사, 10/100/1,000회 반사 통로, 산란 평판,
  0.5/1/2 mm 출구의 곡면·차폐벽 cavity, 6면 수광 광원 검증 챔버.
- 광학 조건 25개. 이 중 공통 광원 챔버는 14개 조건을 비교한다.
- `LT_SETUP_KO.md`: 중심·u/v 축·normal·크기·광속·표면 물성·종료 조건.
- `lt_results_template.csv`: BitSam 기준값과 사내 LT 결과 입력란.
- `results/heatmaps`: 셀 중심 좌표·광속·조도 CSV.
- `REPORT.html`: 모델 그림, 광원 패턴, 초기 수렴 결과 및 미통과 항목.
- `models_manifest.json`, `SHA256SUMS.txt`: 재현 입력과 파일 식별.

STEP는 물리적 부품만 포함한다. 광원·Receiver·표면 물성은 STEP에서 자동 적용되지
않는다. 이번 묶음은 `.bitsam` 자동 설정 패키지가 아니며, LT에서도 설정표를
따라 광학 조건을 입력해야 한다. 회사 도면·실행파일은 포함하지 않는다.

## 광원 조건

| 분류 | 세부 조건 |
|---|---|
| 기본 분포 | Lambertian, isotropic, Gaussian sigma 3/12/30° |
| Aim Sphere | 전방위, 전방 반구, 15° cone, 20~40° annulus, 평행광, 후방 15° cone |
| Aim Area | 사각형, 원형, 위치 이동 + 30° 기울임 |

모든 조건은 기존 Datum 면광원에서 발광한다. 점·체적 광원은 새로 구현하지 않는다.
검증 챔버의 6개 검출면은 동시에 활성화하고, 방향별 광속과 전체 합계 1 lm을
비교한다. 광원 분포와 반사면의 산란 분포는 별개이다.

## 실행과 재현

GPU 실행 전에는 `AGENTS.md`와 GPU 실행 가이드 3개를 전부 읽고 소스 launcher의
사전 검사를 수행한다. 시스템 드라이버·Toolkit 설치는 별도 승인이 필요하다.

```powershell
.\.venv-gpu\Scripts\python.exe samples\generate_rt_validation_models.py
.\.venv-gpu\Scripts\python.exe scripts\verify_rt_validation_models.py --gpu --convergence
.\.venv-gpu\Scripts\python.exe scripts\report_rt_validation_models.py
.\.venv-gpu\Scripts\python.exe -m unittest discover -s tests -p test_rt_validation_models.py -v
```

`--gpu`를 생략하면 CPU 검증이며 GPU 검증으로 보고하지 않는다. 검증 중단 후에는
동일 commit/모델/Ray 수/장치 모드에서 `--resume`으로 완료된 조건을 재사용할 수 있다.
모델을 다시 생성하면 STEP 파일 hash가 바뀔 수 있으므로 새 모델은 재검증한다.

결과 `passed=false`이면 검사 프로그램 종료 코드가 1이다. 보고서 작성은 그 경우도
허용하지만 미통과 항목을 숨기거나 허용오차를 변경하여 통과 처리하지 않는다.
프로덕션 광학 코드·서버 상태는 이 스크립트들에서 수정하지 않는다.

## 2026-09-20 관측

기준 production commit: `e887d68c1b90af7534a8926c7e9c8209c17a34a0`.
검증 스크립트는 이번 작업에서 추가한 미커밋 파일이다.

- 생성/입력 계약·독립 적분식 단위 테스트 6개 통과.
- 조건당 8,192 Ray, CPU/GPU 각각 첫 실행 + 2회 warm 반복.
- 25개 조건 중 **22개 strict 교차 검증 통과**, **3개 미통과**.
- 광원 14개 조건 전부 통과. 1/2/10/100/1,000회 경면 반사 광속이 이론값과 일치.
- CPU 동일 seed 반복 및 batch 257 비교는 25개 조건 모두 통과.
- GPU는 NVIDIA RTX 3070. Production FP64 Ray/BVH 사전 검사 필수 6개 필드 통과.
  개별 실행의 실제 CUDA 성공 횟수·혼합/대체 실행·시간은 원본 JSON에 기록했다.

### 미통과: 복잡한 고반사 cavity

| 틈 | CPU Receiver hit | GPU Receiver hit | 총광속 상대 차이(약) |
|---:|---:|---:|---:|
| 0.5 mm | 1,879 | 1,913 | 0.000182% |
| 1 mm | 3,762 | 3,800 | 0.000070% |
| 2 mm | 5,652 | 5,686 | 0.000026% |

추가 진단에서 1 mm 모델은 상한 20/50/100에서는 이산 결과와 격자가 허용오차
내 일치했고, 300에서 차이를 재현했다. 최대 셀 광속 차이는 약 3.5e-9 lm이다.
현재 근거만으로 난수 문제, 경계 판정, 부동소수점 누적 중 하나를 원인으로
확정하지 않는다. 작아진 광속에 대한 경로 차이일 가능성은 추가 조사 대상이다.
총광속 차이가 작더라도 strict 동등성 미통과를 통과로 바꾸지 않는다.

### 초기 수렴 — 1 mm cavity, 독립 seed 5개

| Ray 수 | 반사 상한 | 평균 Receiver flux(lm) |
|---:|---:|---:|
| 8,192 | 20 | 0.001744686 |
| 8,192 | 50 | 0.003968697 |
| 8,192 | 100 | 0.004335037 |
| 8,192 | 300 | 0.004343751 |
| 8,192 | 1,000 | 0.004343751 |
| 32,768 | 1,000 | 0.004651202 |

이 합성 모델에서는 같은 seed의 반사 상한 300→1,000 증가 효과가 매우 작았다.
그러나 Ray 수를 늘린 평균과 Peak에는 변동이 남았다. 따라서 **반사 상한의 안정화**와
**충분한 Monte Carlo 표본 수/heatmap 품질**을 따로 판정해야 한다. 5개 seed만으로
5% 목표 달성이나 작은 편향 부재를 보증하지 않는다.

## 검증 범위와 후속 작업

1. 분석 가능한 반사·분포 기준값: 이번 모델 범위에서 검사.
2. CPU/GPU/반복/batch 일관성: 복합 고반사 3개 조건 추가 조사 필요.
3. 복합 형상·재질·틈: 이번 합성 사례 검사. ROI 경계 절단 UI 및 CAD/Reference
   면 선택 생성의 전체 조합은 별도 회귀시험 대상.
4. 수렴: 초기 반사 상한/Ray 수 sweep 수행. 더 많은 Ray·seed·물성 조합 필요.
5. LT 및 실측: 사용자 제공 결과 후 수행.

첫 LT 비교는 총광속(lm)과 평균 조도(lux)를 사용한다. 현재 Gaussian 광원의
극각은 half-normal을 90°에서 clamp한 정의이므로 LT의 Gaussian/BSDF 정의와
동일한지 확인해야 한다. `nit_est`를 LT의 휘도와 직접 동일시하지 않는다.

소스 launcher는 이번 도구 세션에서 Python 탐색을 완료하지 못했다. 기존 설치된
고정 버전 런타임으로 의존성 일치 검사와 공식 production GPU 검사기를 직접
실행했다. launcher 성공·신규 설치 완료로 보고하지 않았으며, OS 설정·드라이버·
Toolkit 설치 및 서버 재시작은 수행하지 않았다.
