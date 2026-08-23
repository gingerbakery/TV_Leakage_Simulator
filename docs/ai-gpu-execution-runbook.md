# AI GPU execution runbook

This is the deterministic GPU/CUDA runbook for repository-aware AI assistants.
Read it and `docs/gpu-cuda-user-guide.md` completely before executing a GPU
request. If a command result conflicts with prose, fail closed and report the
observed result.

## 1. Classify what the user has

| Delivery | Correct entry point | Included dependencies |
| --- | --- | --- |
| Git source checkout | `run_web_gpu.bat` / `run_web_gpu.ps1` | Launcher creates `.venv-gpu`, syncs exact Python pins, runs `npm ci`, and builds the frontend |
| Extracted `*_gpu_cuda.zip` | `CHECK_GPU_CUDA.bat`, then `LeakageSimulator.exe` | Python 3.13 runtime, Numba, llvmlite, and frontend are bundled |
| Extracted `*_lite.zip` | `LeakageSimulator.exe` | CPU runtime only; CUDA is unavailable |

Never tell a ZIP/EXE user that `git pull` updated the extracted program. A new
GPU ZIP must be verified and extracted into a new folder without mixing it with
an older Lite or GPU folder.

## 2. Check external prerequisites

Every GPU path requires all of the following on the target Windows PC:

- a CUDA-capable NVIDIA GPU;
- an NVIDIA display driver compatible with the GPU and CUDA Toolkit 13.1;
- CUDA Toolkit 13.1.

Source checkouts also require 64-bit Python 3.13 and Node.js LTS/npm. GPU ZIP
users do not install Python, Node.js, Numba, or llvmlite separately because the
ZIP contains them. The NVIDIA driver and CUDA Toolkit are never bundled.

An AI may inspect these prerequisites. It MUST obtain explicit user approval
before installing an OS driver or CUDA Toolkit, changing system-wide settings,
or rebooting the PC.

## 3. Source-checkout workflow

From the repository root, the non-interactive setup/preflight command is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_web_gpu.ps1 -PreflightOnly
```

For normal interactive use, run:

```powershell
.\run_web_gpu.bat 8788
```

The launcher is the source of truth. It verifies Python 3.13 x64, rebuilds a
stale `.venv-gpu`, installs both pinned requirements files, runs `pip check`,
runs `npm ci`, creates the current frontend production build, and executes the
real production FP64 Ray/BVH CUDA preflight before starting the server.

Do not replace this with `run_web.bat` for a GPU request. Do not infer success
from an existing `.venv-gpu`, a successful package import, or an open browser.

## 4. GPU-ZIP workflow

1. Verify the ZIP, `.sha256`, and `.handoff.json` belong together.
2. Extract the entire ZIP into a new short path; never run the EXE inside the
   archive and never copy only the EXE.
3. Run `CHECK_GPU_CUDA.bat` from the extracted root.
4. Require the GPU name, `Real Ray/BVH CUDA kernel: PASS`, and the final `[OK]`.
5. Only after that check, start `LeakageSimulator.exe`.

The build-time `gpu_cuda_runtime_manifest.json` describes the build PC. It does
not prove that CUDA works on the current user's PC; `CHECK_GPU_CUDA.bat` must be
run there.

## 5. Fail-closed preflight contract

When reading `GET /api/gpu-cuda/status?refresh=true`, checker JSON, or console
output, GPU readiness requires all of these values:

```text
available=true
strict_float64=true
kernel_executed=true
kernel_verified=true
preflight_scope=production_ray_bvh
provider_contract=strict_float64_bvh_v1
```

If any field is false, missing, or different, say that GPU readiness was not
verified. Preserve and report the reason code and nearby `[ACTION]` text.
Package installation without this production preflight is not success.

## 6. Select GPU compute in the app or API

In the UI, open `Ray Tracing` and use the standalone `연산 장치` section at
the top to select `NVIDIA GPU`. Require the concise readiness row to show
`준비 완료 · <GPU name>`. The payload value remains
`compute_backend=gpu_cuda`.

`Acceleration structure` / `intersection_backend` is an internal advanced
setting under `Run Options > 고급 옵션`. Normal users should leave it on the
recommended automatic setting. GPU selection normalizes it to a compatible
BVH path; GPU + `brute_force` is invalid and must be corrected before
execution. A legacy `.bitsam` without `compute_backend` intentionally loads as
CPU and needs an explicit GPU selection.

## 7. Prove the completed run used CUDA

The words `BVH` and `Rebuilt` do not prove GPU execution. They describe the
intersection acceleration structure and its build state.

A run is proven to use GPU compute only when:

```text
compute_execution_state in {gpu_active, gpu_mixed}
gpu_cuda_gpu_success_count > 0
```

Treat these as CPU-only/fallback, not GPU success:

- `compute_execution_state=gpu_requested_cpu_only`;
- `gpu_cuda_gpu_success_count=0`;
- the result has no Compute row (usually a stale frontend or old ZIP).

If `gpu_cuda_gpu_success_count > 0` but a CUDA fallback reason or CPU fallback
count is also present, the GPU did execute work. Report the result as
mixed/partial fallback; do not call it CPU-only or full-GPU success.

Face and `polygon_auto` emitters can perform CPU scalar work. A run may be
legitimately `gpu_mixed`; record the CPU small-wave/hybrid/fallback counts
rather than hiding them.

## 8. Benchmark and report

Use the same scene, `.bitsam`, emitter set, ray count, depth, and app session.
Record the first run and two or three warm runs because CUDA JIT and upload can
make the first run slower. Never generalize one ray-tracing result to CAD
import, BVH build, UI work, or every scene.

Minimum AI report:

```text
Delivery: source <branch>@<commit> | GPU ZIP <file> <sha256>
Preflight: PASS/FAIL; GPU=<name>; scope=<scope>; contract=<contract>
Selection: compute_backend=<value>; intersection_backend=<value>
Run: compute_execution_state=<state>; reason=<reason-or-none>
CUDA batches: success/attempt=<n>/<n>; CPU hybrid/fallback=<counts>
Emitter types: <types>
Timing: first=<s>; warm2=<s>; warm3=<s>
Conclusion: GPU verified | mixed/partial fallback | CPU fallback | not verified
```

Do not use “GPU verified” if the preflight proof or completed-run proof is
missing.
