from __future__ import annotations

"""Validate the optional packaged PERF-3C CUDA runtime.

The import-only mode is safe on CPU-only build hosts.  Device mode explicitly
selects the provider and verifies the same production Ray/BVH CUDA path used
by the app.  Importing this module performs no CUDA probe.
"""

import argparse
from importlib import metadata
import json
from pathlib import Path
import platform
import sys
from typing import Any, Sequence

EXPECTED_NUMPY = "2.4.6"
EXPECTED_NUMBA = "0.66.0"
EXPECTED_LLVMLITE = "0.48.0"
LEGACY_KERNEL_RESULT_SHA = 33153


ACTIONABLE_ERRORS = {
    "missing_dependency:numpy": (
        "NumPy is missing from this Python runtime.",
        "Source checkout: run run_web_gpu.bat. Packaged app: extract a fresh GPU CUDA ZIP.",
    ),
    "missing_dependency:numba": (
        "Numba is missing from this Python runtime.",
        "Source checkout: run run_web_gpu.bat. Packaged app: use the GPU CUDA ZIP, not Lite.",
    ),
    "missing_dependency:llvmlite": (
        "llvmlite is missing from this Python runtime.",
        "Source checkout: run run_web_gpu.bat. Packaged app: extract a fresh GPU CUDA ZIP.",
    ),
    "numba_not_installed": (
        "Numba is not installed, so the CUDA provider cannot load.",
        "Run run_web_gpu.bat for source, or use the GPU CUDA ZIP instead of the Lite ZIP.",
    ),
    "numba_import_failed": (
        "Numba could not be imported.",
        "Run run_web_gpu.bat again; if it still fails, remove .venv-gpu and retry.",
    ),
    "numba_cuda_import_failed": (
        "Numba's CUDA module could not be imported.",
        "Run run_web_gpu.bat again and review the preceding DLL/import error.",
    ),
    "cuda_driver_unavailable": (
        "The NVIDIA display driver is missing, incompatible, or hidden from this session.",
        "Install a driver compatible with the GPU and CUDA Toolkit 13.1, reboot, then retry.",
    ),
    "cuda_toolkit_not_found": (
        "CUDA Toolkit 13.1 (NVVM/libdevice/runtime files) was not found.",
        "Install CUDA Toolkit 13.1 in its default path, or set CUDA_PATH to its root, then retry.",
    ),
    "cuda_runtime_unavailable": (
        "The driver and Toolkit were found, but the CUDA runtime could not initialize.",
        "Reboot, close other GPU jobs, and verify driver/Toolkit compatibility before retrying.",
    ),
    "cuda_device_query_failed": (
        "CUDA initialized, but the NVIDIA device could not be queried.",
        "Confirm that this Windows session can access the NVIDIA GPU, then retry.",
    ),
    "gpu_cuda_kernel_result_mismatch": (
        "A real FP64 CUDA kernel ran but returned an incorrect result.",
        "Do not use GPU results on this machine; update the driver and report the full output.",
    ),
    "gpu_cuda_scene_upload_failed": (
        "The production BVH scene could not be uploaded to the GPU.",
        "Close other GPU jobs, reboot, rerun the check, and keep the full output if it repeats.",
    ),
    "gpu_cuda_input_upload_failed": (
        "The production Ray/BVH inputs could not be uploaded to the GPU.",
        "Close other GPU jobs, reboot, rerun the check, and keep the full output if it repeats.",
    ),
    "gpu_cuda_kernel_failed": (
        "The production Ray/BVH CUDA kernel failed during execution.",
        "Do not report GPU acceleration as working; update the driver and rerun the check.",
    ),
    "gpu_cuda_output_download_failed": (
        "The production Ray/BVH result could not be downloaded from the GPU.",
        "Close other GPU jobs, reboot, and rerun the check before using GPU results.",
    ),
    "gpu_cuda_preflight_contract_invalid": (
        "The production CUDA provider returned an invalid strict-FP64 contract.",
        "Do not use GPU results on this build; reinstall or rebuild the GPU edition.",
    ),
    "gpu_cuda_preflight_result_mismatch": (
        "The production Ray/BVH CUDA hit/miss result was incorrect.",
        "Do not use GPU results on this machine; update the driver and report the full output.",
    ),
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"missing_dependency:{distribution}") from exc


def verify_imports() -> dict[str, Any]:
    numpy_version = _version("numpy")
    numba_version = _version("numba")
    llvmlite_version = _version("llvmlite")
    if numpy_version != EXPECTED_NUMPY:
        raise RuntimeError(
            f"unsupported_numpy_version:{numpy_version}:expected:{EXPECTED_NUMPY}"
        )
    if numba_version != EXPECTED_NUMBA:
        raise RuntimeError(
            f"unsupported_numba_version:{numba_version}:expected:{EXPECTED_NUMBA}"
        )
    if llvmlite_version != EXPECTED_LLVMLITE:
        raise RuntimeError(
            "unsupported_llvmlite_version:"
            f"{llvmlite_version}:expected:{EXPECTED_LLVMLITE}"
        )

    import llvmlite.binding as llvm
    import numba
    import numpy as np

    # This exercises llvmlite.dll instead of accepting metadata-only installs.
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()
    return {
        "status": "ok",
        "mode": "imports",
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "numpy": np.__version__,
        "numba": numba.__version__,
        "llvmlite": llvmlite_version,
        "llvmlite_native": True,
        "cuda_device_executed": False,
    }


def verify_device() -> dict[str, Any]:
    result = verify_imports()

    source_root = _project_root() / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

    # This is the exact production provider preflight used by the app: a real
    # one-triangle BVH upload plus known hit/miss intersection execution.
    from leakage_simulator.gpu_cuda_intersection import (
        PREFLIGHT_SCOPE,
        PROVIDER_CONTRACT,
        preflight_gpu_cuda,
    )

    preflight = preflight_gpu_cuda(refresh=True)
    if not preflight.available:
        raise RuntimeError(
            f"gpu_cuda_unavailable:{preflight.reason_code or 'unknown'}"
        )
    if preflight.preflight_scope != PREFLIGHT_SCOPE:
        raise RuntimeError(
            "gpu_cuda_preflight_scope_invalid:"
            f"{preflight.preflight_scope}:expected:{PREFLIGHT_SCOPE}"
        )
    if not preflight.kernel_executed:
        raise RuntimeError("gpu_cuda_preflight_kernel_not_executed")
    if not preflight.kernel_verified:
        raise RuntimeError(
            "gpu_cuda_preflight_not_verified:"
            f"{preflight.reason_code or 'unknown'}"
        )
    if preflight.strict_float64 is not True:
        raise RuntimeError("gpu_cuda_preflight_contract_invalid")

    result.update(
        {
            "mode": "device",
            "cuda_device_executed": True,
            "available": preflight.available,
            "reason_code": preflight.reason_code,
            "device_name": preflight.device_name,
            "device_id": preflight.device_id,
            "compute_capability": preflight.compute_capability,
            "strict_float64": preflight.strict_float64,
            "toolkit_layout": preflight.toolkit_layout,
            "kernel_executed": preflight.kernel_executed,
            "kernel_verified": preflight.kernel_verified,
            "preflight_scope": preflight.preflight_scope,
            "provider_contract": PROVIDER_CONTRACT,
            # Preserve the legacy manifest field/value for consumers that
            # compare older CHECK_GPU output.  Production readiness is now
            # represented by preflight_scope + kernel_verified above.
            "kernel_result_sha": LEGACY_KERNEL_RESULT_SHA,
        }
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("imports", "device"),
        default="device",
        help="imports checks packages; device runs the production Ray/BVH CUDA path",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional strict-JSON manifest path",
    )
    parser.add_argument(
        "--human",
        action="store_true",
        help="print an operator-facing diagnosis instead of strict JSON",
    )
    return parser


def _actionable_error(error: str) -> tuple[str, str]:
    for code, messages in ACTIONABLE_ERRORS.items():
        if code in error:
            return messages
    if any(
        code in error
        for code in (
            "unsupported_numpy_version",
            "unsupported_numba_version",
            "unsupported_llvmlite_version",
        )
    ):
        return (
            "The installed GPU Python package versions do not match this source revision.",
            "Run run_web_gpu.bat so .venv-gpu is synchronized to the pinned requirements.",
        )
    if "dll load failed" in error.lower():
        return (
            "A required native Python/CUDA DLL could not be loaded.",
            "Source checkout: rerun run_web_gpu.bat. Packaged app: extract a fresh GPU CUDA ZIP.",
        )
    return (
        "The CUDA preflight failed before a verified GPU result was produced.",
        "Keep this output, rerun run_web_gpu.bat, and inspect the detailed error below.",
    )


def format_human_success(result: dict[str, Any]) -> str:
    if result["mode"] == "imports":
        return "\n".join(
            (
                "[GPU RUNTIME VERIFIED] Python GPU packages and native "
                "llvmlite loaded.",
                f"[GPU RUNTIME VERIFIED] Numba {result['numba']} | "
                f"llvmlite {result['llvmlite']}",
                "[STATUS] Import-only mode did not execute a GPU kernel.",
            )
        )
    return "\n".join(
        (
            f"[GPU VERIFIED] Device: {result['device_name']}",
            "[GPU VERIFIED] Compute capability: "
            f"{result['compute_capability']} | strict FP64: {result['strict_float64']}",
            "[GPU VERIFIED] CUDA Toolkit layout: "
            f"{result['toolkit_layout']}",
            "[GPU VERIFIED] Real Ray/BVH CUDA kernel: PASS | scope "
            f"{result['preflight_scope']}",
        )
    )


def format_human_error(error: str) -> str:
    summary, action = _actionable_error(error)
    return "\n".join(
        (
            f"[GPU FAILED] {summary}",
            f"[DETAIL] {error}",
            f"[ACTION] {action}",
            "[STATUS] GPU server/test must not be reported as working until this preflight passes.",
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = verify_device() if args.mode == "device" else verify_imports()
    except Exception as exc:
        result = {
            "status": "error",
            "mode": args.mode,
            "error": f"{type(exc).__name__}:{exc}",
            "cuda_device_executed": False,
        }
        payload = json.dumps(result, sort_keys=True, allow_nan=False)
        if args.human:
            print(format_human_error(result["error"]), file=sys.stderr)
        else:
            print(payload, file=sys.stderr)
        return 1

    payload = json.dumps(result, sort_keys=True, allow_nan=False)
    print(format_human_success(result) if args.human else payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
