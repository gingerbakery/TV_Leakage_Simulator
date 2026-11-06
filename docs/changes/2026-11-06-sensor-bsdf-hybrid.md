# 동일 센서 광량 비교의 측정 BSDF 혼합 경로

사용자 보고: 첫 반복 6%에서 `GPU 센서 역추적 준비 실패: observer_resident_bsdf_requires_host`. 순방향 반복 뒤 역추적 초기화에서 측정 BSDF가 resident 커널 미지원이라는 이유로 중단됐다. 대표 경로 실패나 두 광량의 불일치 판정이 아니다.

수정: 센서 모드에서 `ObserverGpuUnsupported("observer_resident_bsdf_requires_host")`만 기존 host optical wavefront를 사용하도록 연결했다. BSDF 원본과 forward 방향 BRDF/NEE/MIS·continuation을 보존한다. 각 primary/continuation/shadow CAD query는 `allow_hybrid_cpu=False`인 CUDA BVH 배치로 요청한다. 임의 Lambertian 대체·BSDF 제거·CPU-only로의 GPU 성공 처리는 하지 않는다.

실제 CUDA 성공이 있고 host optics를 사용하면 `gpu_mixed / observer_transport_cpu_host_roundtrip`, `observer_bsdf_evaluation_backend=cpu`를 보고한다. JSON에 기존 resident 미지원 이유와 CUDA attempt/success, CPU fallback 정보를 남긴다. UI에는 `BSDF CPU 평가 · CAD 교차 GPU`를 표시한다. 성공 batch 0이면 기존 광량 비교 gate가 미확정 처리한다. GPU 초기화 실패 등 다른 예외는 계속 명시적 오류다.

이는 BSDF 광학 평가의 전체 CUDA 구현이 아니라 GPU 교차 검사 + CPU 광학 평가의 혼합 실행이다. 성능 배수와 실제 사용자 BSDF 파일의 물리 정확도는 검증하지 않았다.

회귀 검사: 합성 측정 BSDF를 유지한 동일 유한 센서에 대해 혼합 routing의 flux/2차 모멘트가 CPU 기준과 동일하고, 모든 query가 CUDA 요청으로 전달되는지 확인했다. GPU 성공 계수는 테스트 대체 함수의 가상 값이며 GPU 실행 증거가 아니다. CPU-only는 GPU gate 불통과, 임의 장치 오류는 hybrid로 우회하지 않는 검사도 추가했다. 실제 NVIDIA 장치 실행 검증은 미실시다.

사용자는 수정 버전을 실행한 뒤 BSDF·광원·Receiver 조건을 유지하고 같은 후보의 `광량 비교 → 비교 시작`으로 재검사한다. 실패한 이전 작업은 이어 실행 대상이 아니다. 결과의 혼합 실행 표시 및 검증 JSON을 확인한다.
