# PERF 5B 면 속성 준비 비용 개선

면별 속성의 적용 대상을 매번 다시 찾는 비용을 줄였다. 변경 감시가 가능한
형상에서는 면과 속성의 연결만 재사용하며, 반사율 등 광학 값은 실행마다
현재 설정에서 읽는다. 이 개선은 CPU와 GPU 해석에 기본 적용했다.

**CPU와 GPU 각각 2배 목표 전체는 아직 미완료다.** 고정 기준 `157230c6`와
같은 세션에서 재측정한 CPU 자동 7조건은 모두 2배를 넘었지만, GPU CAD는
1.470배다. 반사 모델과 난수, Ray 수 또는 해석 결과를 바꾸는 방식으로
가속비를 만들지 않았다.

## 면 속성 연결 재사용과 변경 감시

이전에는 같은 CAD를 다시 해석할 때도 모든 triangle의 원본 면 번호,
component와 재질을 읽어 속성 적용 대상을 다시 구성했다. TV 코너 대조군은
50,944 triangle이 35개의 metadata dictionary를 공유하므로, 같은 내용을
여러 번 해석하는 비용이 컸다.

새 경로는 다음 조건을 확인한 뒤 면과 속성의 연결을 재사용한다.

- 면 수, metadata dictionary의 실제 객체 목록과 재질 지정이 같은지 확인한다.
- 공유 dictionary마다 원본 면 번호와 component 등의 변경을 검사한다.
- 면별 override, 부품 기본값, 누락된 profile과 우선순위 선택을 유지한다.
- Profile 객체나 반사율을 연결 정보에 저장하지 않는다. 현재 실행의 profile과
  assignment 이름을 다시 연결하므로 값과 객체를 교체해도 반영된다.
- 같은 dictionary 내용으로 객체를 교체하거나, source face 번호 등을
  수정하면 연결을 다시 계산한다. 일반 숫자와 같다고 응답하는 임의 객체도
  다른 정수 변환 결과를 숨길 수 없도록 타입을 함께 검사한다.
- 반환한 slot 배열은 매번 독립된 read-only 사본이다. 호출자가 이를 강제로
  수정해도 다음 실행의 연결을 오염시키지 않는다.
- 비표준 metadata 또는 mesh 메서드 override는 기존 해석으로 돌아간다.
  잘못된 원본 면 번호는 이전과 같이 오류로 처리한다.

캐시는 mesh마다 한 개만 둔다. CAD 대조군의 추가 숫자 배열은 611,328 bytes이며,
35개 공유 dictionary의 변경 감시 자료와 참조는 별도다. 이를 전체 메모리
사용량으로 표현하지 않는다. 기본 적용 대상은 256면 이상이다.
진단용 `LEAKAGE_OPTICAL_ROUTING_CACHE=off`로 기존 반복 해석과 비교할 수 있다.

## 동일 소스에서 캐시 사용 전후

같은 장면을 같은 프로세스에서 교대로 실행한 warm 7회 중앙값이다.
CAD 대조군은 100,000 Ray, 반사 상한 10이다.

| 항목 | 기존 반복 준비 | 연결 재사용 | 속도비 |
| --- | ---: | ---: | ---: |
| CPU 면 속성 준비 | 20.627ms | 3.479ms | 5.928배 |
| GPU 해석의 면 속성 준비 | 20.568ms | 3.432ms | 5.993배 |
| CPU 전체 해석 | 0.726919초 | 0.705705초 | 1.030배 |
| GPU 전체 해석 | 0.161021초 | 0.144931초 | 1.111배 |

준비 단계는 약 83% 줄었고, GPU 전체 CAD 해석 시간은 약 10% 줄었다.
약 6배는 준비 단계의 비율이며 전체 해석의 가속비가 아니다.
첫 호출과 모든 warm 원본은 JSON에 구분해 기록했다.

작은 장면들은 기존 per-triangle 경로를 사용하므로 이 캐시 설정을 읽지
않는다. 같은 비교에서 관측된 작은 장면의 약 3~6% 등락을 캐시 효과로
해석하지 않는다. 고정 Git 기준 비교는 아래 표처럼 별도로 수행했다.

추가 CPU 진단에서는 50,000면의 metadata 공유 정도를 1개, 35개, 50,000개로
달리했다. 준비만 비교한 warm 15회 중앙값의 속도비는 각각 4.025배, 3.737배,
1.308배였다. 이 값은 전체 해석 가속비가 아니며, triangle마다 별도 metadata가
있어도 준비 경로가 느려지는지 확인하기 위한 진단이다.

## 고정 기준 재측정

기준은 앞선 목표와 같은 `157230c6`이다. 새 결과를 이전 세션의 가속비와
곱하지 않고, 이번 세션에서 기준 구현과 수정본을 교대로 실행했다.
첫 실행 뒤 warm 3회 중앙값이다. 앞의 세 가지 조건은 1M Ray, CAD와
흡광 대조군은 100K Ray다. CPU는 기존 4 worker와 현재 자동 정책의
8 worker를 비교하며, 수동 batch override는 사용하지 않았다.

| 조건 | CPU 기준 | CPU 수정본 | CPU 속도비 | GPU 기준 | GPU 수정본 | GPU 속도비 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Face 직달 | 1.388305초 | 0.409784초 | 3.388배 | 0.649692초 | 0.268634초 | 2.419배 |
| 확률 2회 반사 | 1.477084초 | 0.511567초 | 2.887배 | 0.451860초 | 0.226542초 | 1.995배 |
| 갇힌 구조 10회 반사 | 8.962804초 | 4.182483초 | 2.143배 | 0.473075초 | 0.240492초 | 1.967배 |
| TV 코너 CAD | 1.588898초 | 0.699072초 | 2.273배 | 0.217608초 | 0.148060초 | 1.470배 |
| 흡광 상한 20 | 0.132146초 | 0.053459초 | 2.472배 | 0.045199초 | 0.027092초 | 1.668배 |
| 흡광 상한 100 | 0.131349초 | 0.059059초 | 2.224배 | 0.090264초 | 0.027905초 | 3.235배 |
| 흡광 상한 1,000 | 0.297082초 | 0.060507초 | 4.910배 | 0.530249초 | 0.032599초 | 16.266배 |

GPU 1.995배와 1.967배를 반올림해 2배 달성으로 처리하지 않는다. CAD와
상한 20 대조군도 아직 목표 미달이다. 흡광 상한 1,000 조건은 실제로
1,000회 반사하는 장면이 아니라 첫 면에서 끝나는 대조군이다.

CPU 공개 결과는 모두 기준과 bit 단위로 같았다. GPU의 이산 결과와
strict float64 물리량 허용오차도 통과했다. 기본 atomic 집계의 합산 순서
차이까지 bit 단위로 일치한다는 뜻은 아니다.

## GPU 경계 계산 추가 후보

별도 후보는 BVH와 triangle 순서, 동률 면 선택을 유지하고, 평행하지 않은
광선의 box 경계 계산을 단순화한다. 원래 FP64 빼기와 곱하기 순서를 유지하며,
거의 평행한 광선은 기존 식을 사용한다. 조기 종료를 단계마다 하는 방식과
모든 축 계산 후 판정하는 방식을 비교했다.

현재 기본 GPU와 교대 warm 7회 비교다. 이 세션은 위 고정 기준 비교와
별개이며, 후보는 아직 기본에 적용하지 않았다.

| 조건 | 현재 GPU | 단계별 조기 종료 | 속도비 | 모든 축 후 판정 | 속도비 |
| --- | ---: | ---: | ---: | ---: | ---: |
| CAD 100K | 0.149490초 | 0.140319초 | 1.065배 | 0.137504초 | 1.087배 |
| 확률 2회 반사 1M | 0.156882초 | 0.168452초 | 0.931배 | 0.160389초 | 0.978배 |
| 갇힌 구조 10회 반사 1M | 0.222270초 | 0.221328초 | 1.004배 | 0.219128초 | 1.014배 |
| Face 직달 1M | 0.247014초 | 0.247749초 | 0.997배 | 0.238741초 | 1.035배 |

큰 CAD에는 개선 여지가 있지만 작은 장면의 속도와 기본 경로 통합을 추가로
확인해야 한다. 6,000개 scalar 교차 질의와 4,000개 box 질의에서 면 번호와
거리 비트가 같았고, 실제 GPU의 미세 간격, CAD 속성 재편집, 표시 경로,
1,000회 반사와 MIS 부분 tail 검사도 통과했다. 큰 좌표에서 생기는 FP64
overflow 경고를 포함해 원래 경계 판정과 비교했으며, 경고를 숨기거나 FP32로
바꾸지 않았다.

## 실행 증거와 다음 작업

통합 357검사가 401.502초에 통과했다. 기존 수송, 물성, 종료, Aim, 경로와
GPU 집계 검사에 이번 캐시 및 경계 계산 후보 검사를 함께 포함한다.
별도 실제 장치 memcheck 2검사, 30.802초와 synccheck 1검사, 26.421초에서도
오류 0건이다. 네 개 최종 성능 결과의 source와 script hash가 현재 파일과
일치함을 다시 확인했다.

캐시의 추가 검사는 원본 metadata의 제자리 수정과 객체 교체, 누락된 profile,
assignment 재지정, 사용자 객체의 다른 정수 변환, 잘못된 원본 면 번호,
반환 배열의 강제 수정, profile 객체 교체와 이전 객체의 해제 등을 포함한다.
ROI 면 속성과 여러 반사율의 CPU/GPU 공개 결과, 재편집과 표시 경로도 비교했다.

Source checkout `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`의 로컬 작업본,
RTX 3070, driver 591.59, CUDA Toolkit 13.1, Python 3.13 x64와 Numba 0.66.0
환경이다. 각 최종 비교는 현재 source hash와 script hash를 기록한다.

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

정상 GPU 비교는 `compute_backend=gpu_cuda`, BVH, `gpu_active`, CPU hybrid와
resident fallback 0이다. 광원은 Face 또는 Datum Plane이며 CAD 100K는 CUDA
성공/시도 2/2, 1M 조건은 16/16이다. CAD import, BVH build나 UI 전체가
GPU 가속됐다는 뜻은 아니다.

다음은 GPU 경계 계산 후보의 기본 경로 통합과 작은 장면의 반복 준비 비용
개선이다. 복잡 CAD에는 방향이 비슷한 광선을 GPU에서 묶어 탐색하는 후보도
검토한다. 이 경우 원래 primary 번호와 난수, 면 판정 순서를 보존하고 GPU 밖으로
광선을 내려받아 정렬하지 않는 것이 조건이다. CPU의 달성 상태를 유지하며
GPU 2배 목표를 계속 검증한다.
서버 재시작, 환경 설치, 커밋과 push는 하지 않았다.

## 원본 결과

- `outputs/perf5_transport/optical-routing-paired.json`
- `outputs/perf5_cpu/optical-routing-density-diagnostic.json`
- `outputs/perf5_cpu/optical-routing-auto-fixed.json`
- `outputs/perf5_transport/optical-routing-fixed-gpu.json`
- `outputs/perf5_transport/nonparallel-slabs-paired.json`
- `outputs/perf5_transport/optical-routing-targeted.log`
- `outputs/perf5_transport/nonparallel-slabs-tests.log`
- `outputs/perf5_transport/optical-routing-full-regression.log`
- `outputs/perf5_transport/optical-routing-slab-memcheck.log`
- `outputs/perf5_transport/optical-routing-slab-synccheck.log`
