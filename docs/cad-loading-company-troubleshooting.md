# 회사 PC CAD 로딩 지연 진단

## 증상의 의미

3D Viewer에 `Loading CAD scene`이 표시된다는 것은 다음 상태를 의미한다.

- CAD 파일 업로드와 `_uploads` 저장은 완료되었다.
- Frontend는 `/api/scene` 응답을 기다리고 있다.
- 병목은 일반적으로 Python/OCP 로딩, STEP 제품구조 해석, Tessellation, ROI mesh 세분화 또는 JSON 생성 중 하나다.

따라서 이 화면만으로 API 서버가 끊겼다고 판단하면 안 된다.

## 서버 창의 마지막 `[CAD]` 줄로 판정

| 마지막으로 보이는 단계 | 주된 원인 |
|---|---|
| `OCP runtime load START` | OCP DLL을 회사 보안 프로그램이 검사하거나 차단 |
| `OCP product structure START` | STEP 이름·색상·Assembly metadata 해석 지연 |
| `OCP tessellation START` | 부품 수, 곡면 수 또는 형상 복잡도에 따른 삼각형 생성 지연 |
| `triangle extraction START` | OCP mesh를 Python 데이터로 복사하는 단계의 대용량 처리 |
| `feature edges START` | 원본 삼각형 수가 매우 많음 |
| `ROI mesh subdivision START` | ROI 선택용 전역 mesh 세분화 |
| `scene mesh arrays START` | Face normal, centroid, area 배열 생성 |
| `JSON serialization START` | Browser로 전달할 scene payload가 지나치게 큼 |
| `API scene total ... request complete` | Backend 완료 상태이며 Browser JSON 해석 또는 Three.js 생성 단계 확인 필요 |

## 직접 진단

실행 중인 서버를 `Ctrl+C`로 종료한 뒤 다음 명령을 사용한다.

```powershell
.\.venv\Scripts\python.exe check_cad_import.py --cad "C:\CAD\model.stp" --no-dialog
```

ROI 전역 세분화만 제외하려면 다음과 같이 실행한다.

```powershell
.\.venv\Scripts\python.exe check_cad_import.py --cad "C:\CAD\model.stp" --no-dialog --fast-import
```

제품 이름·색상 해석까지 제외해 OCP/XCAF 지연을 구분하려면 다음과 같이 실행한다.

```powershell
.\.venv\Scripts\python.exe check_cad_import.py --cad "C:\CAD\model.stp" --no-dialog --fast-import --skip-product-metadata
```

## 긴급 실행 우회

회사 보안 프로그램으로 XCAF 제품구조 단계가 과도하게 느린 경우에만 다음 환경 변수를 설정하고 서버를 실행한다.

```powershell
$env:LEAKAGE_CAD_SKIP_PRODUCT_METADATA="1"
$env:LEAKAGE_CAD_FAST_IMPORT="1"
.\run_web.bat
```

이 모드는 실제 STEP 형상은 불러오지만 다음 제한이 있다.

- CAD 원본 Component 이름과 색상 대신 일반 이름을 사용할 수 있다.
- 전역 ROI mesh 세분화를 생략하므로 아주 큰 평면에서 ROI Face 선택 정밀도가 낮아질 수 있다.
- Ray tracing 형상은 native OCP Tessellation을 그대로 사용한다.

정상 모드로 되돌리려면 새 PowerShell 창을 열거나 다음 명령을 실행한다.

```powershell
Remove-Item Env:LEAKAGE_CAD_SKIP_PRODUCT_METADATA -ErrorAction SilentlyContinue
Remove-Item Env:LEAKAGE_CAD_FAST_IMPORT -ErrorAction SilentlyContinue
```

## 코드 보호장치

- 동일 CAD에 대한 동시 `/api/scene` 요청은 하나의 Import 작업으로 합친다.
- 완료된 scene payload는 서버 메모리에 캐시하여 같은 CAD 재요청 시 재계산하지 않는다.
- Native mesh가 이미 50,000 faces 이상이면 전역 ROI 세분화를 자동 생략한다.
- 사용자가 강제로 세분화해야 할 때만 `LEAKAGE_CAD_FORCE_ROI_SUBDIVISION=1`을 사용한다.
- Viewer는 CAD 로딩 경과시간을 표시하고 30초 이후 서버 단계 확인을 안내한다.
