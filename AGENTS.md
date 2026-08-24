# Repository agent instructions

These instructions apply to every AI agent working in this repository.

## GPU/CUDA work is fail-closed

When a user asks to install, start, test, benchmark, diagnose, package, or hand
off NVIDIA GPU acceleration, you MUST read these files completely before
running commands or making a GPU-success claim:

- `docs/ai-gpu-execution-runbook.md`
- `docs/gpu-cuda-user-guide.md`
- `docs/WINDOWS_GPU_SETUP.md`

Then follow these repository rules:

1. Identify the delivery path first.
   - Source checkout: use `run_web_gpu.bat` or `run_web_gpu.ps1`.
   - Extracted GPU ZIP: run `CHECK_GPU_CUDA.bat`, then
     `LeakageSimulator.exe`.
   - Lite ZIP: CPU-only; it cannot provide CUDA acceleration.
   - A Git pull does not update an already extracted ZIP or EXE.
2. Do not claim that Python packages alone enable GPU mode. The target PC also
   needs a CUDA-capable NVIDIA GPU, a compatible NVIDIA display driver, and
   CUDA Toolkit 13.1. A source checkout additionally needs 64-bit Python 3.13
   and Node.js LTS/npm. The source launcher prepares the virtual environment,
   pinned Python packages, frontend packages, and production build.
3. For Windows prerequisite setup, run `setup_windows_gpu.bat` without
   arguments first; its default mode is read-only inventory. Report the exact
   missing items and planned mutations before asking for approval. Use
   `setup_windows_gpu.bat -Install` only after explicit install authorization.
   Never install an OS driver or CUDA Toolkit, change system settings, or
   reboot without explicit user authorization. Reboot requires its own
   just-in-time approval. Do not bypass UAC or company security policy.
4. For a GPU request, do not substitute `run_web.bat` and do not hand-roll a
   `pip`/`npm` setup while `run_web_gpu` is available.
5. Preflight is successful only when all of these fields are true or exact:
   `available=true`, `strict_float64=true`, `kernel_executed=true`,
   `kernel_verified=true`, `preflight_scope=production_ray_bvh`, and
   `provider_contract=strict_float64_bvh_v1`. Otherwise, do not call the PC
   GPU-ready and do not silently describe CPU fallback as GPU success.
6. GPU selection means `compute_backend=gpu_cuda`. Acceleration structure
   `auto` or `bvh` is compatible; GPU + `brute_force` is invalid. A legacy
   `.bitsam` file without `compute_backend` opens in CPU mode until the user
   explicitly selects NVIDIA CUDA GPU.
7. `BVH` or `Rebuilt` is acceleration-structure information, not proof of GPU
   execution. A completed run proves GPU use only when
   `compute_execution_state` is `gpu_active` or `gpu_mixed` AND
   `gpu_cuda_gpu_success_count > 0`. If the state is
   `gpu_requested_cpu_only` or successful CUDA batches are zero, report the run
   as CPU-only/fallback. If CUDA success is greater than zero but a fallback
   reason or CPU fallback count is also present, report GPU use as
   mixed/partial fallback, not CPU-only and not full-GPU success. Face and
   `polygon_auto` emitters may include CPU scalar work; report mixed execution
   honestly.
8. Benchmark the same scene in the same session and record the first run plus
   two or three warm runs. The first run may include CUDA JIT and upload cost.
   Do not claim that CAD import, BVH build, the whole UI, or every workload is
   GPU-accelerated.
9. Record the delivery path and commit/ZIP handoff, GPU name, production
   preflight fields, result `compute_execution_state` and reason, CUDA
   attempt/success counts, CPU hybrid/fallback counts, timing, and emitter
   types in any test report.
10. Do not commit, push, publish, or create a release unless the user asks for
    that external change.
