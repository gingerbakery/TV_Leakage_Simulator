from __future__ import annotations

"""Validate the optional packaged PERF-3C CUDA runtime.

The import-only mode is safe on CPU-only build hosts.  Device mode explicitly
selects the provider, probes the CUDA driver/toolkit and executes one real JIT
kernel.  Importing this module performs no CUDA probe.
"""

import argparse
from importlib import metadata
import json
from pathlib import Path
import platform
import sys
from typing import Any, Sequence

import numpy as np


EXPECTED_NUMBA = "0.66.0"
EXPECTED_LLVMLITE = "0.48.0"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"missing_dependency:{distribution}") from exc


def verify_imports() -> dict[str, Any]:
    numba_version = _version("numba")
    llvmlite_version = _version("llvmlite")
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

    # The provider owns the explicit CUDA 13 Windows layout compatibility
    # resolver.  Calling it here validates the same path used by the app.
    from leakage_simulator.gpu_cuda_intersection import probe_gpu_cuda

    capability = probe_gpu_cuda()
    if not capability.available:
        raise RuntimeError(
            f"gpu_cuda_unavailable:{capability.reason_code or 'unknown'}"
        )

    from numba import cuda

    @cuda.jit
    def add_one(values: Any) -> None:
        index = cuda.grid(1)
        if index < values.size:
            values[index] += 1.0

    expected = np.arange(257, dtype=np.float64) + 1.0
    device_values = cuda.to_device(np.arange(257, dtype=np.float64))
    add_one[128, 128](device_values)
    cuda.synchronize()
    observed = device_values.copy_to_host()
    if not np.array_equal(observed, expected):
        raise RuntimeError("gpu_cuda_kernel_result_mismatch")

    result.update(
        {
            "mode": "device",
            "cuda_device_executed": True,
            "device_name": capability.device_name,
            "device_id": capability.device_id,
            "compute_capability": capability.compute_capability,
            "strict_float64": capability.strict_float64,
            "toolkit_layout": capability.toolkit_layout,
            "kernel_result_sha": int(np.sum(observed)),
        }
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("imports", "device"),
        default="device",
        help="imports checks packaged binaries; device also runs a CUDA kernel",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional strict-JSON manifest path",
    )
    return parser


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
        print(payload, file=sys.stderr)
        return 1

    payload = json.dumps(result, sort_keys=True, allow_nan=False)
    print(payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
