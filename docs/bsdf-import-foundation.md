# LT BSDF 가져오기: 1차 구현 범위와 계산 계약

현재는 **파일 가져오기·보존과 반사 전용 CPU·NVIDIA CUDA GPU 광선 추적**까지 구현되어 있다.
최초 기반 단계에서는 가져오기와 저장만 지원했지만, 후속 구현에서 등방성 /
No Shift 계산을 명시적으로 선택한 자산을 실제 추적에 연결했다.
상세 계산 범위, 수치 검증 결과와 LT 비교 절차는
[BSDF 연산 검증 문서](bsdf-transport-validation.md)를 따른다.
가져오기 성공만으로 모든 LT BSDF 기능이나 LT 결과 일치를 보장하지 않는다.

Material Assignment 또는 Surface Property에서 `LT ASCII 파일 등록`을 누르고
master, 산란 표, RT 파일을 함께 선택한다. 등록 후 연결할 자산을 선택하고 기존
Apply 버튼으로 부품/면에 연결한다. 계산하려면 해당 자산에서
`등방성 · No Shift로 계산`을 켜고 계산 파장을 지정한다.
등록만으로 물성이 바뀌지 않는다. 샘플은
`samples/bsdf/synthetic-lambertian/`의 세 `.txt` 파일이며 반사율 30%의 합성 데이터다.
기존 해석을 하려면 연결 목록에서 `연결 없음`을 명시적으로 선택하고 Apply한다.

원본은 ASCII/UTF-8 텍스트로 보관한다. `.bitsam`에는 원본 묶음과 파싱 데이터가
함께 들어간다. BSDF 자산·연결이 있는 파일은 내부 설정 스키마가
`bitsam-project.v1-bsdf1`이다. 예전 빌드가 이를 무시하고 기존 모델로 계산하지 않도록
읽기를 거부하게 하는 구분이며, BSDF 없는 기존 파일은 계속 호환된다.

## 지원 범위

| 항목 | 현재 지원 |
| --- | --- |
| 파일 구성 | ASCII master + 하나 이상의 2D 분포 파일 + RT 파일 |
| Master | `#UDOP AngGridMultiple 1.0` |
| 분포 | `MeshFormat = Polar`, `MeshDataType = BSDF` |
| 상호작용 | 반사만: `RAOI`, `0 <= RAOI < 90`인 LT Front 데이터 |
| 입사면·매질 | `POI = 0`, `Index = 1`, `IndexIncident = 1` |
| 파장 | 여러 파장의 데이터·메타데이터 저장 가능; 분광 계산 지원을 뜻하지 않음 |
| 격자 | `SphereMesh:`; longitude 0–360°, latitude 0–90° 안의 전체 또는 부분 영역 |
| RT | `DFAT Version 1.0`, `DATAITEMS: TAVG RAVG`, 투과율 `TAVG = 0` |
| 보존 | 원본 파일 내용, 원본 이름, 해석한 격자와 메타데이터 |
| 광선 추적 | 전체 반구 표, 등방성 / No Shift, 선택 파장, 반사 전용 CPU·CUDA GPU / source sampling |

네이티브 단일 `.bsdf`, 1D slice, Cartesian, CCBSDF, LT Back, 투과, 비공기 매질,
비영 POI 데이터는 이 단계에서 지원하지 않는다. 알 수 없는 형식이나 옵션을
유추하여 읽지 않는다. `.txt` 확장자만으로 호환성을 판정하지 않는다.

`POI = 0` 하나만으로 등방성을 증명할 수 없다. 데이터의 `symmetry`는
`unspecified`로 보존한다. 계산 옵션에서 사용자가 등방성으로 사용할 것을
명시하며, LT 비교 시 Anisotropic OFF와 No Shift를 확인해야 한다.

## 좌표와 파일 해석

- `SphereMesh: LongDim LatDim LongMin LatMin LongMax LatMax` 다음에 값이 온다.
  격자는 각 축의 양 끝점을 포함하며 균일 간격이다.
- 열은 longitude **최소→최대**, 행은 latitude **최대→최소**이다.
  LT Polar의 latitude는 법선에서 잰 각도이므로 0°가 법선, 90°가 수평이다.
- 입사광은 longitude 90° 쪽에서 들어오며, 정반사 방향은 longitude 270° 쪽이다.
  LT의 Front는 일반적인 화면의 앞면이라는 뜻이 아니다. 매뉴얼은 산란면에 닿기
  전에 고체 내부를 통과하는 측정 방향으로 정의한다. 현재 계산은 입사측으로
  법선을 정렬해 양쪽에 동일한 표를 적용한다. LT의 Front/Back 이중 물성이나
  고체 매질 경계를 재현한다는 뜻은 아니다.
- 모든 값은 유한한 0 이상의 수여야 하고, 각 격자는 0이 아닌 분포를 가져야 한다.
  격자 크기와 값 개수가 맞아야 한다. 포함된 0°/360° seam과 법선 pole의
  중복 표현도 일관되는지 검사한다.
- 부분 영역 입력은 보관만 가능하다. 실제 계산은 0–90° / 0–360° 전체 반구
  표에 한정한다. 측정 입사각 범위 밖은 가장 가까운 끝값을 사용한다.
- LT master의 `..\name.txt`는 매뉴얼상 master와 **같은 폴더**를 가리키는 표기다.
  파일 참조는 사용자가 선택한 묶음 안에서만 해결한다. 파일 내용에 적힌 경로로
  로컬 디스크나 네트워크를 탐색하지 않으며, 누락·모호한 이름은 오류로 처리한다.

자원 한도는 파일 64개, 파일당 2 MiB, 총 8 MiB, 격자 128개,
격자당 200,000개 값, 전체 400,000개 값이다. 초과 입력은 잘라서 수용하지 않는다.

## 상대 분포와 실제 반사율은 분리한다

LT ASCII master의 BSDF 분포는 **상대적인 방향 분포**다. RT 파일이 입사각과
파장에 따른 실제 반사율·투과율을 지정한다. 표 값 자체를 반사율로 해석하거나,
RT 반사율에 기존 surface property 반사율을 다시 곱하면 안 된다.

구현된 반사 샘플러의 기본 관계는 다음과 같다.

```text
상대 BRDF 모양: s(입사 방향, 출사 방향)
정규화 적분:   Z = ∫반구 s(ωo) cos(θo) dωo
출사 확률:     p(ωo) = s(ωo) cos(θo) / Z
실제 BRDF:     fr(ωo) = R_RT × s(ωo) / Z
```

각 표의 polar bilinear 보간에 위 적분을 적용해 방향을 추출한다.
CCBSDF는 이미 cosine이 곱해진 값이므로 같은 처리로 읽을 수 없다.

RT의 `ABSORBING: YES`는 `A = 1 - R - T`를 사용한다.
`ABSORBING: NO`는 LT 규칙대로 R과 T를 정규화하여 `R + T = 1`로 만든다.
따라서 이 단계의 반사 전용 데이터에서 NO와 양의 R을 지정하면 최종 반사율은
1이다. R과 T가 모두 0인 경우처럼 정규화가 불가능한 입력은 거부한다.

## 같은 파일이어도 LT 계산 옵션을 맞춰야 한다

LT measured BSDF의 기본값은 **Shift with Stretch**다. 실제 입사각과 측정
입사각이 다르면 cosine 공간에서 분포를 이동한 뒤 늘린다. **Shift Invariant**는
`sin(MeasuredAOI) - sin(RayAOI)`에 따른 이동을 사용하며, **No Shift**는 그 이동을
하지 않는다. 이는 단순한 표 보간 여부와 별도로 확인할 설정이다.

영역 밖 방향의 처리도 다르다. 기본 **Narrowed Specular**는 분포를 좁혀
재생성한다. **Regenerate**는 최대 10회 다시 뽑은 뒤 흡수하고, **Absorb**는 바로
흡수한다. 이 옵션들은 master 파일만으로 확정되지 않으므로 현재 가져오기가
LT 기본 추적 방식까지 재현했다고 표시해서는 안 된다.

## 구현·검증 단계의 상태

1. 완료: parser, 좌표축, seam/pole, 상대 정규화, RT, 입력 오류, 프로젝트 보존.
2. 완료: CPU 반사 샘플러, 측정각/파장 보간, 끝값 처리, 실제 반사 광량 계산.
   RT가 기존 반사율과 grazing 보정을 대체한다.
3. 완료: 합성 Lambertian 및 방향성 분포를 실제 광선 추적하고 별도 수치 적분과
   비교했다. 0/30/60/80° 및 보간각 45°를 포함한 여섯 조건을 검증했다.
4. 완료: CUDA 반사율·산란 추출, CPU/GPU 배치 난수 일치 및 실제 장치 검증.
   범위와 수치는 [GPU 검증 문서](bsdf-gpu-validation.md)를 참조한다.
5. 대기: 실제 LT 실행 결과로 동일 표·RT·No Shift 조건의 직접 비교.
   검증용 STEP, 여섯 .bitsam, 원본 ASCII와 CSV 비교 도구를 준비했다.
6. 후속: LT 단일 평면 비교 후 실제 조립체에서 수광량·분포·설계 감소율 검증.

이 절차는 두 엔진의 일치 여부를 검사한다. 실제 소재 정확도는 별도의 근거가
필요하며, 합성 BSDF가 실제 소재 측정을 대신하지는 않는다.

## 확인한 LT 공식 자료

설치 경로의 HTML은 한 줄로 저장되어 있으므로 절 제목과 anchor를 기록한다.

- `D:/LightTools/Help/core/lug9.10.080.html#ww367423` — Using Your Own Measured BSDF Data; 1D→2D 재구성과 equal-magnitude rays
- `D:/LightTools/Help/core/lug9.10.082.html#ww453344` — Formatting the Data; `.bsdf` 또는 scatter/master/RT 구성
- `D:/LightTools/Help/core/lug9.10.085.html#ww367553` — Format for 2D Data Files; 좌표·대칭
- `D:/LightTools/Help/core/images/lug9.10.085.1.png` — Polar `SphereMesh` 헤더와 행·열 순서 도해
- `D:/LightTools/Help/core/lug9.10.090.html#ww411282` — Master File Format for 2D Measured BSDF Data
- `D:/LightTools/Help/core/lug9.10.091.html#ww367872` — Formatting an RT File; 상대 분포·절대 R/T/A
- `D:/LightTools/Help/core/lug9.10.092.html#ww467933` — Shift Type, Regeneration, Anisotropic 옵션
- `D:/LightTools/Help/core/lug9.10.097.html#ww476784` — 공기 측정, IndexIncident/Index와 굴절 처리

실제 형식은 `D:/LightTools/ExamplesLibrary/2DMesh_Measured_BSDF_Diffuser/`의
master, `SphereMesh` 및 RT 파일로 교차 확인했다. 네이티브 `.bsdf` 내부 문법은
이 조사에서 확인하지 못했으며, ASCII 지원을 네이티브 `.bsdf` 지원으로 부르지 않는다.
