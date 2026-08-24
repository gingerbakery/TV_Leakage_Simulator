# Windows NVIDIA GPU 설치 및 AI 자동화 가이드

> 대상 PC: 64-bit Windows, NVIDIA RTX A4000
>
> 프로젝트 기준: CUDA Toolkit 13.1 Update 1, Python 3.13 x64, Node.js LTS x64
>
> 기준일: 2026-08-24

이 문서는 사람과 저장소에 접근할 수 있는 사내 AI가 같은 절차로 GPU 실행
환경을 준비하기 위한 기준 문서다. RTX A4000은 CUDA Compute Capability 8.6을
지원하므로 이 프로젝트의 strict-FP64 CUDA 경로와 하드웨어 수준에서
호환된다. 단, 실제 준비 완료 판정은 반드시 이 PC에서 production Ray/BVH
preflight를 실행한 결과로 내린다.

## 1. 먼저 전달 형태를 구분한다

| 받은 것 | PC에 별도 필요한 항목 | 실행 진입점 |
| --- | --- | --- |
| Git source checkout | NVIDIA driver, CUDA Toolkit 13.1, Python 3.13 x64, Node.js LTS/npm | `run_web_gpu.bat` |
| 압축 해제한 `*_gpu_cuda.zip` | NVIDIA driver, CUDA Toolkit 13.1 | `CHECK_GPU_CUDA.bat`, 이후 `LeakageSimulator.exe` |
| 압축 해제한 `*_lite.zip` | 없음 | CPU 전용이며 CUDA GPU 가속 불가 |

GPU ZIP에는 Python·Numba·llvmlite·frontend build가 포함된다. 따라서 GPU ZIP
사용자는 Python이나 Node.js를 별도 설치하지 않는다. 모든 형태에서 NVIDIA
driver와 CUDA Toolkit은 프로그램에 포함되지 않는다.

`git pull`은 이미 압축 해제한 ZIP/EXE를 갱신하지 않는다. 반대로 새 ZIP을
받아도 source checkout은 바뀌지 않는다.

## 2. AI가 자동화할 수 있는 범위와 승인 경계

사내 AI가 이 저장소, PowerShell, 네트워크에 접근할 수 있다면 점검부터
프로젝트 preflight까지 자동화할 수 있다. 하지만 UAC, 관리자 자격 증명,
사내 보안 정책을 우회할 수는 없다.

| 작업 | 기본 처리 | 필요한 승인 |
| --- | --- | --- |
| GPU·OS·설치 버전 조회 | AI가 바로 실행 가능 | 없음 |
| 프로젝트 package 동기화·build·preflight | AI가 실행 가능 | 일반적으로 없음 |
| Python·Node.js·CUDA Toolkit 설치 | `setup_windows_gpu.ps1 -Install`을 명시한 경우에만 | 설치 전 명시적 승인 |
| NVIDIA driver 설치 | 공식/사내 IT 승인 installer 식별과 실행 보조 | 관리자 승인과 설치 화면 확인 |
| 시스템 PATH·보안 설정 변경 | 자동 변경 금지 | 별도 명시적 승인/IT 검토 |
| 재부팅 | 자동 실행 금지 | 실행 직전 별도 승인 |

AI는 명령이 없다는 사실을 설치 허가로 해석하면 안 된다. 먼저 읽기 전용
점검 결과와 누락 항목, 정확한 설치 제품·버전, 관리자 권한 여부, 화면 영향,
재부팅 가능성을 보고한 뒤 승인을 기다린다. 드라이버 설치 중 화면이
깜빡이거나 일시적으로 꺼질 수 있으므로 사용자는 작업을 먼저 저장한다.

`setup_windows_gpu.bat`은 기본적으로 조회만 한다. 설치를 승인한 경우에만
다음과 같이 명시적으로 실행한다.

```powershell
.\setup_windows_gpu.bat -Install
```

이 자동화는 정확한 CUDA/Python/Node 패키지를 설치하고 다시 점검한다. NVIDIA
driver가 없거나 580 미만이면 임의의 드라이버를 내려받지 않고 중단한다. AI는
아래 드라이버 절차에 따라 RTX A4000용 공식 또는 사내 IT 승인 installer를
준비해야 한다. 자동 재부팅과 CPU 대체 성공 처리는 하지 않는다.

## 3. 공식 요구사항과 다운로드

- [NVIDIA CUDA GPU 목록](https://developer.nvidia.com/cuda/gpus): RTX A4000의 Compute Capability는 8.6이다.
- [NVIDIA 공식 드라이버 다운로드](https://www.nvidia.com/en-us/drivers/): RTX A4000용 Windows 64-bit RTX Enterprise Production Branch 또는 Studio driver를 선택한다.
- [NVIDIA RTX Enterprise driver 이력](https://www.nvidia.com/en-us/drivers/rtx-enterprise-and-quadro-driver-branch-history/)
- [CUDA Toolkit 13.1 Update 1 다운로드](https://developer.nvidia.com/cuda-13-1-1-download-archive)
- [CUDA 13.1 Windows 설치 가이드](https://docs.nvidia.com/cuda/archive/13.1.0/cuda-installation-guide-microsoft-windows/index.html)
- [CUDA 13.1 릴리스 노트](https://docs.nvidia.com/cuda/archive/13.1.0/cuda-toolkit-release-notes/index.html)
- [Python 3.13.15 Windows 다운로드](https://www.python.org/downloads/release/python-31315/)
- [Node.js 공식 다운로드](https://nodejs.org/en/download)
- [WinGet install 명령](https://learn.microsoft.com/windows/package-manager/winget/install)

CUDA 13.x minor-version compatibility에는 NVIDIA driver 580 이상이 필요하다.
CUDA 13.1부터 Windows Toolkit에 display driver가 포함되지 않으므로 driver와
Toolkit을 각각 설치해야 한다. 이 프로젝트 실행에는 cuDNN이 필요하지 않으며,
실행만을 위해 Visual Studio를 별도로 설치할 필요도 없다.

현재 검증된 고정 조합은 다음과 같다.

| 항목 | 고정/최소값 | 비고 |
| --- | --- | --- |
| GPU | NVIDIA RTX A4000 | Compute Capability 8.6 |
| NVIDIA driver | 580 이상 | 이미 충족하면 재설치·downgrade하지 않음 |
| CUDA Toolkit | 13.1 Update 1 | 기본 경로 `...\CUDA\v13.1` |
| Python | installer는 3.13.15 x64로 고정 | 기존 3.13.x x64도 source에서 유지 |
| Node.js | installer는 24.19.0 LTS x64로 고정 | 기존 24.11 이상 24.x LTS도 유지 |

WinGet의 `Nvidia.CUDA` 최신판은 기준일 현재 13.1보다 높다. 이 프로젝트에는
반드시 `--version 13.1`을 지정해야 CUDA 13.1 Update 1 installer가 선택된다.

## 4. 설치 전 읽기 전용 점검

프로젝트 루트에서 다음 중 하나를 실행한다.

```powershell
.\setup_windows_gpu.bat
```

또는 직접 점검하려면 PowerShell에서 다음 명령을 실행한다.

```powershell
Get-CimInstance Win32_OperatingSystem |
    Select-Object Caption, Version, OSArchitecture
Get-CimInstance Win32_VideoController |
    Select-Object Name, DriverVersion

nvidia-smi
nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader
where.exe nvcc
nvcc --version
$env:CUDA_PATH

py -0p
py -3.13 -c "import sys; print(sys.version); print(sys.maxsize > 2**32)"
node --version
node -p "process.arch"
npm --version
git --version
```

명령을 찾을 수 없다는 결과도 정상적인 점검 결과다. 그 결과만으로 설치
허가가 생기지는 않는다. AI는 결과를 다음처럼 먼저 요약한다.

| 항목 | 관측값 | 판정 | 필요한 변경 |
| --- | --- | --- | --- |
| GPU | 예: NVIDIA RTX A4000 | 통과/실패 | 없음 또는 IT 확인 |
| Driver | 예: 582.78 | `>= 580` | 없음 또는 driver 설치 |
| CUDA Toolkit | 예: release 13.1 | 통과/실패 | Toolkit 13.1 설치 |
| Python | 예: 3.13.15 x64 | source만 판정 | Python 설치 |
| Node/npm | 예: 24.19.0/x64 | source만 판정 | Node 설치 |

`nvidia-smi`의 `CUDA Version` 표시는 driver가 지원하는 CUDA 상한이며 Toolkit
설치 버전이 아니다. 실제 Toolkit은 `nvcc --version`, `CUDA_PATH`, 설치 파일로
확인한다.

## 5. 사용자 승인 문구

점검 결과를 확인한 뒤 필요하면 다음 문장을 사내 AI에 전달할 수 있다.

```text
이 PC에서 점검 결과 누락된 CUDA Toolkit 13.1 Update 1, Python 3.13.15 x64,
Node.js 24.19.0 LTS 설치와 필요한 관리자 권한 상승을 승인한다. 공식 출처와
정확한 고정 버전만 사용하고 기존 정상 설치는 변경하지 마. NVIDIA driver가
580 미만이거나 없으면 임의 설치하지 말고 RTX A4000용 공식/사내 IT 승인
installer를 먼저 제시해. 재부팅은 자동으로 하지 말고 실행 직전에 다시 물어봐.
```

이 승인은 보안 설정 해제, 인증서/SSL 검증 우회, 기존 driver 강제 제거,
다른 CUDA 버전 제거, Git 변경 삭제를 허용하지 않는다.

## 6. NVIDIA RTX A4000 driver

### 이미 정상인 경우

다음 명령에서 GPU가 RTX A4000으로 보이고 driver가 580 이상이면 driver를
재설치하거나 downgrade하지 않는다.

```powershell
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
```

### 설치가 필요한 경우

1. [NVIDIA 공식 드라이버 페이지](https://www.nvidia.com/en-us/drivers/) 또는 사내 Software Center에서 RTX A4000, 현재 Windows 64-bit용 driver를 선택한다.
2. 사내 안정성을 우선하면 RTX Enterprise Production Branch 또는 승인된 Studio driver를 사용한다.
3. IT가 요청하지 않았다면 DDU, 기존 driver 강제 제거, Clean Installation을 임의로 수행하지 않는다.
4. 실행 전에 파일 서명을 확인한다.

```powershell
Get-AuthenticodeSignature "C:\승인된-경로\NVIDIA-driver.exe" |
    Select-Object Status,
        @{Name="Signer"; Expression={$_.SignerCertificate.Subject}}
```

`Status`가 `Valid`이고 signer가 NVIDIA인지 확인한다. AI가 installer를 실행해도
UAC secure desktop에서는 사용자 또는 IT 담당자가 승인해야 한다. 회사 정책이
visible installer를 요구하면 silent 옵션을 사용하지 않는다.

사내 IT가 이 signed installer의 무인 실행까지 승인한 경우에만 다음처럼
helper에 절대 경로를 전달할 수 있다. Helper는 display driver만 `-s -n`으로
설치하고 자동 재부팅하지 않는다. Visible installer가 필요한 회사에서는 이
명령을 쓰지 말고 승인된 installer를 직접 실행한다.

```powershell
.\setup_windows_gpu.bat -Install "C:\IT-approved\NVIDIA\setup.exe"
```

설치 프로그램이 재부팅을 요구하면 AI는 멈추고 별도로 승인받는다. 재부팅 후
새 PowerShell에서 GPU와 driver를 다시 점검한다.

## 7. CUDA Toolkit 13.1 Update 1

### 자동 설치

`setup_windows_gpu.bat -Install`은 WinGet에서 package ID와 exact version을
확인한 뒤 다음과 동등한 설치를 수행한다.

```powershell
winget show --exact --id Nvidia.CUDA --source winget --versions
winget install --exact --id Nvidia.CUDA --source winget --version 13.1 `
    --architecture x64 --scope machine --silent `
    --accept-source-agreements --accept-package-agreements `
    --disable-interactivity
```

`--ignore-security-hash`, `--force` 또는 자동 재부팅 옵션은 사용하지 않는다.

### 수동 설치

[CUDA 13.1 Update 1 archive](https://developer.nvidia.com/cuda-13-1-1-download-archive)에서
Windows, x86_64, 현재 OS, `exe (local)`을 선택하고 기본 경로에 설치한다.

```text
C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1
```

다른 CUDA 버전을 제거할 필요는 없다. 설치 후 새 PowerShell을 열고 확인한다.

```powershell
nvcc --version
$env:CUDA_PATH

$cudaRoot = $env:CUDA_PATH
if (-not $cudaRoot) {
    $cudaRoot = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1"
}
Get-ChildItem "$cudaRoot\bin\x64\cudart64_*.dll"
Get-ChildItem "$cudaRoot\nvvm\bin\x64\nvvm*.dll"
Get-ChildItem "$cudaRoot\nvvm\libdevice\libdevice*.bc"
```

세 종류의 파일이 모두 있어야 한다.

## 8. Python과 Node.js — source checkout만 해당

GPU ZIP 사용자는 이 절을 건너뛴다.

### Python 3.13.15 x64

```powershell
winget show --exact --id Python.Python.3.13 --source winget --versions
winget install --exact --id Python.Python.3.13 --source winget `
    --version 3.13.15 --architecture x64 --scope machine --silent `
    --accept-source-agreements --accept-package-agreements `
    --disable-interactivity
```

수동 설치는 Python 공식 `Windows installer (64-bit)`를 사용하고 Python
Launcher를 활성화한다. 설치 후 새 PowerShell에서 확인한다.

```powershell
py -3.13 -c "import sys; print(sys.version); print(sys.maxsize > 2**32)"
```

마지막 값이 `True`여야 한다.

### Node.js 24.19.0 LTS x64

```powershell
winget show --exact --id OpenJS.NodeJS.LTS --source winget --versions
winget install --exact --id OpenJS.NodeJS.LTS --source winget `
    --version 24.19.0 --architecture x64 --scope machine --silent `
    --accept-source-agreements --accept-package-agreements `
    --disable-interactivity
```

수동 설치는 Node.js 공식 LTS Windows x64 installer를 사용한다. 확인 결과의
architecture는 `x64`여야 한다.

```powershell
node --version
node -p "process.arch"
npm --version
```

## 9. Source의 production GPU preflight와 실행

설치 후 새 PowerShell을 열고 프로젝트 루트에서 다음을 실행한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
    -File .\run_web_gpu.ps1 -PreflightOnly
```

이 launcher는 `.venv-gpu`, 고정 Python package, `npm ci`, frontend production
build를 동기화한 뒤 실제 FP64 Ray/BVH CUDA kernel을 실행한다. 다음 항목이 모두
확인되어야 한다.

```text
[GPU VERIFIED] Device: NVIDIA RTX A4000
[GPU VERIFIED] Compute capability: 8.6
[GPU VERIFIED] Real Ray/BVH CUDA kernel: PASS | scope production_ray_bvh
[GPU VERIFIED] Source setup and production Ray/BVH CUDA preflight passed.
```

기계 판정 필드는 모두 다음과 같아야 한다.

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

하나라도 다르면 GPU 준비 완료가 아니다. 통과한 뒤에만 실행한다.

```powershell
.\run_web_gpu.bat 8788
```

앱의 `Ray Tracing > 연산 장치`에서 `NVIDIA GPU`를 선택하고
`준비 완료 · NVIDIA RTX A4000`을 확인한다. 실제 ray tracing 결과는 다음 두
조건을 다시 충족해야 한다.

```text
compute_execution_state = gpu_active 또는 gpu_mixed
gpu_cuda_gpu_success_count > 0
```

`BVH`, `Rebuilt`, 브라우저가 열림, package import 성공만으로 GPU 성공을
판정하지 않는다.

## 10. GPU ZIP의 확인과 실행

1. ZIP, `.sha256`, `.handoff.json`이 같은 전달 묶음인지 확인한다.
2. 새 짧은 폴더에 전체 압축 해제한다. EXE만 복사하거나 ZIP 안에서 실행하지 않는다.
3. `CHECK_GPU_CUDA.bat`을 실행한다.
4. RTX A4000 이름, production Ray/BVH kernel PASS, 마지막 `[OK]`를 확인한다.
5. 그 뒤에만 `LeakageSimulator.exe`를 실행한다.

## 11. 실패 시 중단 규칙

| 증상 | 조치 |
| --- | --- |
| 회사 정책/UAC가 설치를 차단 | 우회하지 말고 사내 IT에 요청 |
| GPU가 없거나 RTX A4000이 보이지 않음 | driver·장치 관리자·원격 세션 정책 확인 |
| driver가 580 미만 | 승인된 RTX A4000 driver 설치 요청 |
| `nvcc` 없음 또는 release가 13.1이 아님 | Toolkit 13.1 설치·새 PowerShell 확인 |
| `CUDA_PATH`가 비었음 | 기본 `v13.1` 경로 확인; 임의 시스템 변경 전 승인 |
| Python 3.13 x64 없음 | source 사용자만 Python 설치 |
| Node/npm 없음 | source 사용자만 Node.js LTS 설치 |
| proxy/SSL/package 설치 실패 | 검증을 끄지 말고 원래 오류를 IT에 전달 |
| `cuda_driver_unavailable` | driver·재부팅·세션 GPU 접근 확인 |
| `cuda_toolkit_not_found` | Toolkit/NVVM/libdevice 파일 확인 |
| kernel 불일치 | 결과를 사용하지 말고 전체 로그 보존 |

GPU 요청이 실패해도 AI는 `run_web.bat`으로 바꿔 성공 처리하거나 CPU fallback을
GPU 성공이라고 보고하면 안 된다. 백신·AppLocker·proxy·SSL 검증을 끄거나,
기존 설치를 강제 제거하거나, 사용자 동의 없이 재부팅해서도 안 된다.

사내 IT 요청문:

```text
NVIDIA RTX A4000용 Windows 64-bit RTX Enterprise Production Branch/승인된
Studio driver(580 이상)와 CUDA Toolkit 13.1 Update 1 x86_64를 기본 경로에
설치해 주세요. Source checkout에는 Python 3.13.15 x64와 Node.js 24.19.0 LTS
x64/npm도 필요합니다. 설치 후 nvidia-smi, nvcc --version, 프로젝트의 실제
production Ray/BVH CUDA preflight로 검증할 예정입니다.
```

## 12. 사내 AI용 복사·붙여넣기 요청문

`<프로젝트 경로>`만 실제 경로로 바꾼다.

```text
<프로젝트 경로>의 TV Leakage Simulator GPU 환경을 NVIDIA RTX A4000 기준으로
설정해줘. 먼저 AGENTS.md, docs/WINDOWS_GPU_SETUP.md,
docs/ai-gpu-execution-runbook.md, docs/gpu-cuda-user-guide.md를 처음부터 끝까지
읽고 그대로 따라.

처음에는 .\setup_windows_gpu.bat만 실행해서 읽기 전용 점검을 해. GPU/driver,
CUDA Toolkit과 CUDA_PATH, Python 3.13 x64, Node.js/npm, delivery path,
Git branch/commit/status를 표로 보고해. 명령이 없다는 결과를 설치 허가로
해석하지 마.

누락 항목마다 정확한 제품·고정 버전·공식 출처·변경 범위·관리자 권한·화면
영향·재부팅 가능성을 설명하고 내 승인을 기다려. 승인 후에는
.\setup_windows_gpu.bat -Install을 사용해. 기존 정상 driver나 Toolkit을
제거·downgrade하지 말고, --ignore-security-hash/--force, 보안 정책·SSL 우회,
자동 재부팅을 하지 마. RTX A4000 driver가 없거나 580 미만이면 임의 driver를
설치하지 말고 공식/사내 IT 승인 installer를 먼저 제시해. UAC나 재부팅이
필요하면 멈추고 나에게 요청해.

설치 후 새 PowerShell에서 모든 버전을 다시 확인하고, source라면
powershell -NoProfile -ExecutionPolicy Bypass -File
.\run_web_gpu.ps1 -PreflightOnly을 실행해. available, strict_float64,
kernel_executed, kernel_verified가 모두 true이고 preflight_scope가
production_ray_bvh, provider_contract가 strict_float64_bvh_v1일 때만 GPU 준비
완료라고 말해.

preflight 통과 후에만 .\run_web_gpu.bat 8788을 실행해. run_web.bat으로
대체하지 마. 앱에서 Ray Tracing > 연산 장치 > NVIDIA GPU와
‘준비 완료 · NVIDIA RTX A4000’을 확인하고 테스트를 실행해. 최종 보고에는
compute_execution_state, gpu_cuda_gpu_success_count, CUDA attempt/success,
CPU hybrid/fallback, 첫 실행과 warm 실행 시간을 포함해. 실패하면 첫 [ACTION]과
원래 오류를 보존하고 중단해.
```

재부팅 후 같은 AI 작업을 새로 열어야 하면 다음 문장으로 이어간다.

```text
이전 RTX A4000 설치 작업의 재부팅 후 검증 단계야. 저장소의 AGENTS.md와
docs/WINDOWS_GPU_SETUP.md를 다시 읽고, 설치를 반복하지 말고 읽기 전용 점검부터
재개해. 그 뒤 production Ray/BVH preflight와 GPU 실행 증명까지 완료해.
```

## 13. 완료 체크리스트

- [ ] `nvidia-smi`에 NVIDIA RTX A4000 표시
- [ ] NVIDIA driver 580 이상
- [ ] `nvcc --version`에 release 13.1
- [ ] CUDA runtime, NVVM, libdevice 파일 확인
- [ ] Source만 Python 3.13 x64 확인
- [ ] Source만 Node.js LTS x64와 npm 확인
- [ ] 올바른 delivery path, branch/commit 또는 ZIP handoff 확인
- [ ] production Ray/BVH preflight의 모든 필드 통과
- [ ] 앱에 `준비 완료 · NVIDIA RTX A4000`
- [ ] 실제 결과의 CUDA 성공 batch 수가 1 이상

모든 해당 항목이 충족된 경우에만 “GPU 가속 설치 및 실행 확인 완료”라고
보고한다.
