# CAD Import 진단 가이드

## 목적
회사 PC에서 STEP/STP Import가 오래 걸릴 때 파일 업로드, OCP 로딩, STEP 파싱, 메시 생성, JSON 전송 중 어느 단계가 병목인지 구분한다.

## 현재 Import 흐름
1. 브라우저가 선택한 CAD 파일을 `/api/upload`로 전송한다.
2. 서버가 `_uploads` 폴더에 원본 파일을 저장한다.
3. `/api/scene` 요청이 OCP 런타임을 로드한다.
4. STEP를 읽고 shape를 transfer한 뒤 삼각 메시를 생성한다.
5. ROI 선택 편의성을 위해 평평한 큰 삼각형을 전역 세분화한다.
6. component, face normal, centroid, area, feature edge를 계산한다.
7. 전체 scene을 JSON으로 직렬화해 브라우저로 전달한다.
8. Three.js가 전달받은 scene을 GPU geometry로 구성한다.

## 서버 로그 해석
CAD Import를 실행하면 터미널에 다음 단계가 순서대로 표시된다.

| 로그 단계 | 의미 | 오래 걸릴 때 의심할 원인 |
|---|---|---|
| `upload received` | 브라우저에서 localhost 서버까지 파일 전송 | localhost 차단, 브라우저 보안, 프록시 |
| `upload saved` | `_uploads`에 파일 저장 | 보안 프로그램의 파일 검사, 쓰기 권한 |
| `OCP runtime load` | OCP DLL과 CAD 모듈 로딩 | 회사 endpoint security/백신의 DLL 검사 |
| `OCP STEP read` | STEP 원본 읽기 | 네트워크 드라이브, 파일 검사, 파일 손상 |
| `OCP transfer roots` | STEP assembly/shape 변환 | assembly 복잡도, STEP 구조 |
| `OCP tessellation` | CAD surface를 삼각형으로 변환 | 곡면 수, 형상 복잡도 |
| `triangle extraction` | OCP 삼각형을 내부 mesh로 복사 | raw triangle 수 |
| `feature edges` | CAD 경계선 추출 | triangle 수 |
| `ROI mesh subdivision` | ROI 선택용 표시 mesh 세분화 | 현재 전역 세분화 정책 |
| `scene mesh arrays` | normal, centroid, area 계산 | 세분화 후 face 수 |
| `JSON serialization` | 브라우저 전달용 JSON 생성 | scene payload 크기 |

## 회사 PC 실행 방법
프로젝트 폴더를 VS Code에서 연 뒤 PowerShell 터미널에서 실행한다.

```powershell
.\check_cad_import.bat
```

파일 선택창에서 문제가 발생한 STEP를 선택한다. `.venv`가 있으면 해당 Python을 우선 사용하고, 없으면 `_tools`, 시스템 Python 순으로 찾는다.

경로를 직접 지정하려면 다음과 같이 실행한다.

```powershell
.\.venv\Scripts\python.exe check_cad_import.py --cad "C:\CAD\model.stp" --no-dialog
```

## Fast Import 비교
Fast Import는 STEP 형상을 정상적으로 읽되 ROI용 전역 세분화만 건너뛰는 진단 모드다.

```powershell
.\.venv\Scripts\python.exe check_cad_import.py --cad "C:\CAD\model.stp" --no-dialog --fast-import
```

- 일반 모드와 Fast Import가 모두 느리고 `OCP runtime load`에서 멈추면 회사 보안 프로그램의 DLL 검사 가능성이 높다.
- 일반 모드만 느리고 Fast Import는 빠르면 ROI 전역 세분화와 대형 JSON payload가 주 병목이다.
- `OCP tessellation`부터 느리면 원본 CAD의 곡면/부품/triangle 복잡도가 주 병목이다.
- checker는 `outputs/import_check`에 단계별 시간, face 수, payload 크기가 포함된 JSON을 저장한다.

## 임시 서버 Fast Import
Viewer 로딩 비교가 꼭 필요할 때만 다음과 같이 서버를 실행할 수 있다.

```powershell
$env:LEAKAGE_CAD_FAST_IMPORT="1"
python run_web.py --port 8788
```

이 모드에서는 Viewer와 component 확인은 가능하지만 큰 평면의 ROI 선택 정밀도가 낮아질 수 있으므로 정식 해석 결과에는 사용하지 않는다. 종료 후 새 터미널을 열거나 다음 명령으로 환경 변수를 제거한다.

```powershell
Remove-Item Env:LEAKAGE_CAD_FAST_IMPORT
```

## 로컬 기준 결과
`tv_leakage_roi_left_bottom_no_gap.stp`는 원본 크기가 약 `43KB`, OCP raw mesh가 `88 faces`다.

| 모드 | 최종 face | Scene payload | 총 시간 |
|---|---:|---:|---:|
| 일반 | 50,944 | 5.27MB | 약 1.63초 |
| Fast Import | 88 | 0.015MB | 약 0.75초 |

현재 샘플에서도 ROI 전역 세분화가 face 수를 약 579배 증가시킨다. 실 TV 모델에서는 OCP raw mesh 자체도 크기 때문에 전역 세분화와 JSON 전달을 분리하는 구조 개선이 필요하다.

## 후속 최적화 방향
1. 최초 Import는 coarse preview mesh만 생성한다.
2. 사용자가 ROI를 확정한 뒤 ROI 주변만 선택적으로 세분화한다.
3. ray tracing용 mesh와 Viewer/ROI picking mesh를 분리한다.
4. 대형 scene은 verbose JSON 대신 compact typed-array 또는 binary 전송을 검토한다.
5. 동일 파일은 경로, 크기, 수정시간 기반으로 scene cache를 재사용한다.
