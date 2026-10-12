# Aim Area / Sphere angular distributions

Aim을 켰을 때 Direction distribution이 균일로 고정되던 제한을 해제했다. 기존 드롭다운에서 균일, Lambertian, Gaussian을 고르고 Gaussian sigma를 편집할 수 있다. 설정은 실제 초기 광선 생성과 `.bitsam` 저장/복원에 반영된다.

## 계산 계약

- 기존 Area `uniform_target_area` / Sphere `uniform_solid_angle`은 변경하지 않는다. 과거 파일을 열었다고 Lambertian으로 자동 전환하지 않는다.
- Area의 새 분포는 발광 면적과 방향의 공동 분포를 Target에 조건화한다. 소스 위치마다 따로 정규화하지 않는다. Lambertian은 cosine 세기, Gaussian은 `exp(-theta²/(2sigma²))`의 입체각당 세기를 사용한다. 물리 광원 법선과 Forward/Reverse/Both 설정을 반영한다.
- Sphere의 새 분포는 World 중심축과 지정한 Upper/Lower 범위를 사용한다. Lambertian은 전방 반구와의 교집합이며, Gaussian은 해당 각도 범위 전체를 사용한다. 0/0은 평행광이다.
- 입력 총광속 전체를 선택한 Aim 영역에 배분한다. 모든 채택 광선의 초기 에너지는 동일하며, 차폐와 반사 이후에는 기존 transport 계산을 따른다.
- Area는 source/direction rejection 또는 안전한 거리 상한을 이용한 Target proposal을 사용한다. 발광 불가능한 방향은 사전 거절하고, 극히 낮은 acceptance는 유한 예산 후 설명 가능한 오류로 종료한다. 균일 분포로 대체하지 않는다.
- `aim.gaussian_sigma_deg`는 기본값 12°인 독립 필드다. Aim Off의 원래 분포와 sigma를 보존한다. 결과의 조건 비교와 성능 메타데이터에 선택한 Aim 분포를 기록한다.
- 새 계약 이름: `aim_angular_intensity_conditioned_region_v1`.

## 검증 (CPU)

- 기존 Aim/근접 기하 회귀: 30 tests passed.
- 독립 각도/공동 분포 샘플링: 9 tests passed.
- 실제 receiver 수광, scalar/batch, face/datum/polygon, 차폐/반사, 총광속 보존, portable save/reload/retrace, 입력 검증: 9 tests passed.
- Frontend 관련 3개 suite: 70 tests passed. 분포 선택, sigma, 저장/재편집, Aim Off 복귀, 비교 조건 및 Sphere 가이드 포함.
- TypeScript typecheck 및 production build 통과.
- 독립 코드 검토에서 수정이 필요한 결함은 발견하지 않았다.

회사 광원 배치를 사용한 production virtual-plane sampler 확인에서 Lambertian 1,000,000 rays를 6.05초에 생성했다. 모든 광선이 Target 범위에 들어갔으며 독립 물리 적분과의 분포 overlap은 99.27%, 상관계수는 0.99487이었다. 이는 표본 계산의 검증이며 LT 정합성 점수가 아니다. 세부 로컬 증거는 `outputs/lt-source-validation-20261008/aim_angular_sampler_check_20261012.json`에 있다.

검증 명령:

```powershell
$env:PYTHONPATH='src;tests'
& '.venv-gpu/Scripts/python.exe' -m unittest test_emitter_aim test_emitter_aim_sphere test_emitter_aim_proximity test_aim_angular_sampling test_aim_distribution_transport
npm.cmd --prefix frontend test -- src/features/raytracing/emitter-aim.test.ts src/features/raytracing/ray-tracing-auto-convergence.test.tsx src/features/results/result-ui.test.tsx
npm.cmd --prefix frontend run build
```

`127.0.0.1:8789`의 실행 서버를 갱신했고 `/health`, `/`, 새 production JS bundle의 HTTP 200 응답을 확인했다. 브라우저 자동 파일 선택은 in-app browser에서 chooser 이벤트를 반환하지 않아 수동 창 조작 검증은 완료하지 못했다. UI 동작은 위 frontend 테스트로 검증했다.

GPU 실행과 LT 수치 정합성은 이번 변경의 검증 범위에 포함하지 않았다.
