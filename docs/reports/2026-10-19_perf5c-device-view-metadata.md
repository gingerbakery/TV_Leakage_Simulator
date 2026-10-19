# GPU 전송 배열의 준비 비용 개선

## 적용 상태

GPU에 올린 byte buffer를 여러 수치 배열로 나눌 때, 동일한 배열 metadata를
세 번 만들던 과정을 한 번으로 줄였다. 광학 값과 Receiver 고정 배열뿐 아니라
summary 수치 배열도 같은 함수를 사용한다. 기본 소스는 직접 metadata 방식이며
기존 slice 방식은 내부 대조용으로 유지한다.

같은 세션의 고정 기준 비교에서 GPU 대표 7조건이 2.024~20.604배였다. 직전
소스와 비교한 추가 개선은 약 1.019~1.100배다. 기본 설정 독립 재측정에서도
기존 100k~1M 범위의 CPU와 GPU 각각 7조건은 2배를 넘겼다.

다만 Ray 수를 10배 늘린 확대 검사에서는 CAD 1,000,000 Ray가 1.744배였다.
**전체 2배 목표는 아직 미완료이며 큰 CAD의 후속 최적화가 필요하다.**
작은 조건에서만 통과한 결과로 범위를 좁혀 완료로 처리하지 않는다.

서버 재시작, 커밋, push 및 시스템 환경 변경은 하지 않았다.

## 변경 내용

기존 방식은 각 범위마다 byte slice, dtype view, shape reshape의 metadata를
차례로 만들었다. 새 방식은 고정된 Numba 0.66.0의 `DeviceNDArray` 생성자와
기존 할당의 `gpu_data.view`를 사용해 최종 dtype, shape, C stride를 한 번에
지정한다. GPU에 수치 배열을 따로 할당하거나 데이터를 다시 계산하지 않는다.

- byte 범위, shape와 원소 수, dtype 및 alignment를 검사한 뒤 view를 만든다.
- 원래 FP64 값과 int8, int32, int64의 byte를 그대로 유지한다. signed zero,
  NaN payload와 2의 60승을 넘는 정수도 float로 바꾸지 않는다.
- buffer의 소유권과 CUDA stream을 보존한다. 외부 root 참조를 지워도 살아 있는
  view가 할당을 유지하며, 서로 다른 field의 범위는 겹치지 않는다.
- 빈 배열과 scalar, 비연속 원본 입력을 처리한다. field마다 CUDA 메모리를
  할당하지 않는 것은 stream 및 allocation 차단 시험으로 확인한다.
- NumPy fake 배열은 기존 view 방식을 사용한다. CPU 단위 시험에서 이 함수를
  호출해도 CUDA 모듈을 import하지 않는다.

`LEAKAGE_GPU_TRANSFER_VIEWS=direct`가 기본이고 `sliced`는 직전 방식이다.
잘못된 값이나 범위는 오류로 처리하며 기존 GPU provider 실패 보고를 유지한다.
UI 제어, Ray 수, seed, 반사 상한, 종료 정책 및 광량 환산식은 바꾸지 않았다.

## 같은 세션의 GPU 비교

전달은 source checkout `main@157230c68dabaf5dca1f82b1767793f9ef835a0a`와 미커밋
소스다. RTX 3070, Python 3.13 x64, Numba 0.66.0, CUDA Toolkit 13.1에서 고정
기준, 현재 slice, 직접 metadata 순서를 교대하고 첫 실행과 warm 7회를 기록했다.

아래 고정 기준은 기존과 같은 revision의 수송 및 집계 함수를 사용하는 비교다.
개별 고정 배열 전송과 이전 slice metadata를 명시한다. 독립 checkout 전체나
LightTools와 비교한 숫자가 아니다. 시간은 warm 중앙값이며 import와 최초 CAD
준비를 포함하지 않는다.

| 조건 | 고정 기준 초 | 현재 slice 초 | 직접 metadata 초 | 고정 기준 대비 | 추가 개선 |
| --- | ---: | ---: | ---: | ---: | ---: |
| TV 코너 CAD | 0.221384 | 0.103244 | 0.100174 | 2.210배 | 1.031배 |
| Face 직접 도달 | 0.650355 | 0.263851 | 0.253249 | 2.568배 | 1.042배 |
| 확률 2회 반사 | 0.448862 | 0.203744 | 0.192934 | 2.327배 | 1.056배 |
| 갇힌 구조 상한 10 | 0.444259 | 0.221372 | 0.217207 | 2.045배 | 1.019배 |
| 흡광 상한 20 | 0.046157 | 0.025083 | 0.022809 | 2.024배 | 1.100배 |
| 흡광 상한 100 | 0.092060 | 0.025597 | 0.023376 | 3.938배 | 1.095배 |
| 흡광 상한 1,000 | 0.620421 | 0.032494 | 0.030111 | 20.604배 | 1.079배 |

CAD와 흡광은 100,000 Ray, 나머지는 1,000,000 Ray다. 흡광은 첫 면에서 끝나므로
상한 1,000의 큰 속도비를 실제 1,000회 반사의 속도로 해석하지 않는다. CAD는
이미 통합한 두 후보 BVH 기본 경로를 사용하며, 작은 모델은 일반 kernel이다.
계산 장치나 sampling 및 실제 경로를 바꿔 만든 차이가 아니다.

48개 field의 metadata만 만드는 별도 CPU 시간은 slice 2.885ms에서 직접 방식
0.659ms로 줄었다. 100회 warm 중앙값이며 GPU 수송이나 전송 bandwidth의
4.38배 개선을 뜻하지 않는다. 이것이 짧은 해석의 고정 준비 비용을 줄인다.

모든 정상 비교에서 `compute_execution_state=gpu_active`, CUDA 성공 1 이상,
CPU hybrid 및 resident fallback 0을 확인했다. 현재 소스의 CUDA 성공/시도는
CAD와 흡광 2/2, 나머지는 16/16이다. 이산 결과와 FP64 절대/상대 `1e-9` 기준의
광량, heatmap 및 요약 비교를 통과했다. GPU atomic 누적을 bit exact라 부르지 않는다.

production preflight의 `available`, `strict_float64`, `kernel_executed`,
`kernel_verified`는 모두 true이며 `preflight_scope=production_ray_bvh`,
`provider_contract=strict_float64_bvh_v1`이다. Face 직접 도달은 Face emitter,
나머지는 Datum Plane이다. 첫 실행과 모든 warm 값은 원시 JSON에 함께 기록했다.
현재 두 방식은 공통 kernel을 재사용하므로 각 첫 값을 독립 cold JIT라고 해석하지 않는다.

원시 기록은 `outputs/perf5_transport/direct-view-probe.json`과 `.log`다.
이 실험은 metadata 방식을 명시한 후보 비교이며, 기본값 적용 후의 독립 비교와
구분한다.

## 검증 상태

전용 4검사가 최초 통과했고, 기본 적용 후 기존 고정 전송 검사와 합친 12검사는
18.125초에 통과했다. 범위 초과와 잘못된 shape 및 정렬 오류, 실제 CUDA view의
byte 보존, 소유권, stream과 무할당 동작을 검사한다.

최종 통합 회귀는 **57개 모듈 456검사, 656.726초, 실패와 오류 및 skip 0개**로
통과했다. 제품 소스, 시험 및 script의 전후 hash가 같았다. 기록은
`outputs/perf5_transport/direct-view-full-regression.json`과 같은 이름의 `.log`다.

Compute Sanitizer memcheck는 실제 장치 6검사 31.924초에 통과했고 error summary는
0이다. 새 view의 소유권과 stream, 홀수 면 수 및 설정 재편집, 미세 틈의 Receiver
cap, 실제 1,000회 반사와 재시도, ROI 면 속성과 MIS 및 부분 chunk를 포함한다.

synccheck 2검사도 21.289초에 통과했고 error summary는 0이다. 로그는 같은
폴더의 `direct-view-memcheck.log`와 `direct-view-synccheck.log`다.

## 기본 설정 독립 재측정

기본값 적용 후 별도 프로세스에서 고정 기준과 자동 선택을 교대로 측정했다.
GPU는 첫 실행과 warm 11회, CPU는 첫 실행과 warm 3회다. 아래는 각 장치의
자기 고정 기준 대비 속도비로 CPU와 GPU를 서로 나눈 값이 아니다.

| 조건 | CPU | GPU |
| --- | ---: | ---: |
| TV 코너 CAD | 2.244배 | 2.145배 |
| Face 직접 도달 | 3.358배 | 2.599배 |
| 확률 2회 반사 | 2.841배 | 2.089배 |
| 갇힌 구조 상한 10 | 2.140배 | 2.021배 |
| 흡광 상한 20 | 2.418배 | 2.044배 |
| 흡광 상한 100 | 2.340배 | 3.958배 |
| 흡광 상한 1,000 | 5.049배 | 20.880배 |

Ray 수는 앞의 표와 같다. CPU는 기존 4 worker와 현재 자동 수송 8 worker를
비교했고 모든 공개 결과가 bit exact이며 실제 native 실행과 fallback 0을 확인했다.
GPU는 모든 조건의 이산 결과 및 `1e-9` 물리량 기준, 실제 CUDA 성공 및 CPU
hybrid와 resident fallback 0을 통과했다. CAD와 흡광은 2/2, 나머지는 16/16
성공/시도다. 두 장치 모두 source hash가 측정 전후 같았다.

첫 CAD는 기존 GPU 4.473초, 현재 자동 8.810초다. 현재 값에는 독립적인 특수
kernel JIT와 보조 tree 준비가 포함되어 있다. 첫 실행과 CAD import가 2배
빨라졌다는 뜻은 아니며 위 속도비는 준비된 장면의 warm 해석에 한정한다.

기록은 `outputs/perf5_transport/direct-view-default-fixed.json`과
`outputs/perf5_cpu/direct-view-cpu-fixed.json`이다. 기존 100k~1M 대표 범위에서는
두 장치 모두 2배를 넘겼으나, 큰 Ray 수로 확대하는 검사를 추가로 진행한다.
작은 범위 통과만으로 전체 완료나 1억 Ray 성능을 주장하지 않는다.

추가 기본 GPU 정밀 진단은 seed 7169~7173, 8,192와 65,536 Ray, 각 4회로
**40/40 통과**했다. `1e-12` 절대/상대 및 32 ULP 기준을 그대로 사용했고 최대
ULP는 31이었다. 현재 기본 atomic 합산의 시험 결과이지 bit exact나 무한 반복
결정성을 증명한 것은 아니다. 원시 기록은 `direct-view-default-strict.json`이다.

## 확대 검증

같은 7조건의 Ray 수를 10배로 늘려 고정 기준과 기본 소스를 교대하고 첫 실행과
warm 3회를 기록했다. 모든 비교의 이산 결과, `1e-9` 물리량, CUDA 실제 실행과
source hash 불변을 통과했다. 기존 작은 조건의 절대 시간과 섞지 않은 새 세션
내 속도비다.

| 조건 | Ray 수 | 고정 GPU 초 | 현재 GPU 초 | 속도비 |
| --- | ---: | ---: | ---: | ---: |
| TV 코너 CAD | 1,000,000 | 1.509886 | 0.865828 | 1.744배 |
| Face 직접 도달 | 10,000,000 | 6.297745 | 2.185003 | 2.882배 |
| 확률 2회 반사 | 10,000,000 | 4.517307 | 1.977225 | 2.285배 |
| 갇힌 구조 상한 10 | 10,000,000 | 4.608482 | 1.908699 | 2.414배 |
| 흡광 상한 20 | 1,000,000 | 0.536177 | 0.169069 | 3.171배 |
| 흡광 상한 100 | 1,000,000 | 0.823234 | 0.150832 | 5.458배 |
| 흡광 상한 1,000 | 1,000,000 | 6.324912 | 0.202213 | 31.278배 |

CAD와 흡광의 CUDA 성공/시도는 16/16, 나머지는 153/153이며 CPU hybrid 및
resident fallback은 0이다. 이 수치는 준비 비용 감소만으로 큰 CAD의 수송
처리량까지 2배가 되었다고 볼 수 없음을 보여준다. 기록은
`outputs/perf5_transport/direct-view-scaled-fixed.json`과 `.log`다.

CAD 100만 Ray를 별도로 계측하면 일반 warm은 0.869초였고, 계측 실행의
전체는 약 0.94초, resident kernel 구간은 약 0.76초였다. 기본 primary 준비는
약 0.024초였다. 계측 구간에는 host dispatch 및 동기화 대기가 포함되므로 이를
순수 GPU device 시간이나 더해서 쓸 수 있는 독립 구간으로 해석하지 않는다.
준비 비용보다 수송 kernel이 다음 개선 대상이라는 근거다.

특수 kernel은 thread당 register 130개와 local memory 40 bytes를 사용했다.
후속으로 register 사용과 inline 경계의 영향을 실제 CAD에서 비교한다. 아직
register 제한이 빨라진다고 확인한 것은 아니다. 기록은
`outputs/perf5_transport/direct-view-scaled-cad-profile.json`과 `.log`다.
이 진단의 사전 실행 목록 첫 값 10.457초는 JIT 및 보조 tree 준비를 포함하며,
두 번째 0.869초만 warm이다. 1억 Ray의 속도나 전체 RT 정밀 검증 완료는
아직 주장하지 않는다.
