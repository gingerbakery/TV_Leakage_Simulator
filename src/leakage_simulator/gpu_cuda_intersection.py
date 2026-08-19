from __future__ import annotations

"""Optional strict-float64 CUDA BVH intersection provider.

The module deliberately imports neither Numba nor its CUDA runtime at import
time.  A GPU is probed only after the caller explicitly selects
``gpu_cuda``.  The public boundary owns all host results, keeps scene/device
buffers persistent across depth batches, and turns every provider failure
into a typed error so the ray tracer can replay the complete logical batch on
the CPU exactly once.

PERF-3C currently compiles the kernel lazily with Numba CUDA.  Windows CUDA
13 moved runtime and NVVM DLLs into ``bin/x64`` directories that Numba 0.66
does not discover.  The explicit probe contains a narrowly scoped resolver
for that layout; it is never reached by the default CPU path.
"""

from dataclasses import dataclass, field
import importlib
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Optional
import warnings

import numpy as np
from numpy.typing import NDArray


FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]

PROVIDER_NAME = "gpu_cuda"
PROVIDER_CONTRACT = "strict_float64_bvh_v1"
THREADS_PER_BLOCK = 128


class GpuCudaUnavailable(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.phase = "probe"


class GpuCudaProviderError(RuntimeError):
    def __init__(self, phase: str, reason_code: str) -> None:
        super().__init__(reason_code)
        self.phase = phase
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class GpuCudaCapability:
    available: bool
    reason_code: Optional[str]
    numba_version: Optional[str]
    device_name: Optional[str]
    compute_capability: Optional[str]
    device_id: Optional[int]
    strict_float64: bool
    toolkit_layout: Optional[str]


@dataclass(slots=True)
class GpuCudaScene:
    """Immutable host BVH plus a lazily uploaded read-only device scene."""

    triangle_v0: FloatArray
    triangle_edge1: FloatArray
    triangle_edge2: FloatArray
    node_bounds_min: FloatArray
    node_bounds_max: FloatArray
    node_left: IntArray
    node_right: IntArray
    node_start: IntArray
    node_count: IntArray
    ordered_faces: IntArray
    traceable_face_mask: BoolArray
    stack_width: int
    build_sec: float
    _device_scene: Any = field(default=None, init=False, repr=False)
    _device_lock: threading.RLock = field(
        default_factory=threading.RLock,
        init=False,
        repr=False,
    )


@dataclass(frozen=True, slots=True)
class GpuCudaExecution:
    distances: FloatArray
    face_indices: IntArray
    scene_build_sec: float
    scene_upload_sec: float
    workspace_prepare_sec: float
    input_upload_sec: float
    jit_compile_sec: float
    kernel_sec: float
    output_download_sec: float
    numba_version: str
    device_name: str
    compute_capability: str
    device_id: int
    toolkit_layout: str
    reused_device_scene: bool
    reused_workspace: bool
    strict_float64: bool = True
    provider_contract: str = PROVIDER_CONTRACT


@dataclass(slots=True)
class _DeviceScene:
    triangle_v0: Any
    triangle_edge1: Any
    triangle_edge2: Any
    node_bounds_min: Any
    node_bounds_max: Any
    node_left: Any
    node_right: Any
    node_start: Any
    node_count: Any
    ordered_faces: Any
    traceable_face_mask: Any
    device_id: int
    upload_sec: float


@dataclass(slots=True)
class _Workspace:
    scene_identity: int
    device_id: int
    capacity: int
    stack_width: int
    origins: Any
    directions: Any
    minimum_t: Any
    maximum_t: Any
    ignored_faces: Any
    distances: Any
    face_indices: Any
    stack_entries: Any
    stack_nodes: Any
    overflow_flags: Any


_STATE_LOCK = threading.RLock()
_CAPABILITY: Optional[GpuCudaCapability] = None
_CUDA: Any = None
_NUMBA: Any = None
_KERNEL: Optional[Callable[..., None]] = None
_KERNEL_COMPILED = False
_WORKSPACES = threading.local()
_DLL_DIRECTORY_HANDLES: list[Any] = []
_TOOLKIT_LAYOUT: Optional[str] = None


def _candidate_cuda_roots() -> list[Path]:
    values: list[Path] = []
    for name in ("CUDA_PATH", "CUDA_HOME"):
        raw = os.environ.get(name)
        if raw:
            values.append(Path(raw))
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        base = Path(program_files) / "NVIDIA GPU Computing Toolkit" / "CUDA"
        if base.is_dir():
            values.extend(
                sorted(
                    (entry for entry in base.iterdir() if entry.is_dir()),
                    key=lambda entry: entry.name,
                    reverse=True,
                )
            )
    unique: list[Path] = []
    seen: set[str] = set()
    for value in values:
        try:
            normalized = str(value.resolve()).casefold()
        except OSError:
            continue
        if normalized not in seen:
            seen.add(normalized)
            unique.append(value)
    return unique


def _find_cuda13_windows_layout() -> Optional[tuple[Path, Path, Path, Path]]:
    if os.name != "nt":
        return None
    for root in _candidate_cuda_roots():
        runtime_dir = root / "bin" / "x64"
        nvvm_dir = root / "nvvm" / "bin" / "x64"
        libdevice_dir = root / "nvvm" / "libdevice"
        runtime = next(runtime_dir.glob("cudart64_*.dll"), None)
        nvvm = next(nvvm_dir.glob("nvvm*.dll"), None)
        libdevice = next(libdevice_dir.glob("libdevice*.bc"), None)
        if runtime is not None and nvvm is not None and libdevice is not None:
            return runtime_dir, nvvm_dir, nvvm, libdevice
    return None


def _apply_numba_cuda13_layout_compatibility() -> Optional[str]:
    """Teach an old Numba discovery cache about CUDA 13's x64 folders."""

    global _TOOLKIT_LAYOUT
    layout = _find_cuda13_windows_layout()
    if layout is None:
        return None
    runtime_dir, nvvm_dir, nvvm_path, libdevice_path = layout
    try:
        for directory in (runtime_dir, nvvm_dir):
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
        cuda_paths = importlib.import_module("numba.cuda.cuda_paths")
        paths = cuda_paths.get_cuda_paths()
        paths["nvvm"] = paths["nvvm"]._replace(
            by="PERF-3C CUDA13 compatibility resolver",
            info=str(nvvm_path),
        )
        paths["libdevice"] = paths["libdevice"]._replace(
            by="PERF-3C CUDA13 compatibility resolver",
            info=str(libdevice_path),
        )
        paths["cudalib_dir"] = paths["cudalib_dir"]._replace(
            by="PERF-3C CUDA13 compatibility resolver",
            info=str(runtime_dir),
        )
    except Exception:
        return None
    _TOOLKIT_LAYOUT = "windows_cuda13_x64_compat"
    return _TOOLKIT_LAYOUT


def probe_gpu_cuda() -> GpuCudaCapability:
    """Lazily probe Numba, toolkit and one CUDA device."""

    global _CAPABILITY, _CUDA, _NUMBA
    if _CAPABILITY is not None:
        return _CAPABILITY
    with _STATE_LOCK:
        if _CAPABILITY is not None:
            return _CAPABILITY
        try:
            numba = importlib.import_module("numba")
        except ModuleNotFoundError:
            _CAPABILITY = GpuCudaCapability(
                False,
                "numba_not_installed",
                None,
                None,
                None,
                None,
                False,
                None,
            )
            return _CAPABILITY
        except Exception:
            _CAPABILITY = GpuCudaCapability(
                False,
                "numba_import_failed",
                None,
                None,
                None,
                None,
                False,
                None,
            )
            return _CAPABILITY

        try:
            cuda = importlib.import_module("numba.cuda")
        except Exception:
            _CAPABILITY = GpuCudaCapability(
                False,
                "numba_cuda_import_failed",
                str(getattr(numba, "__version__", "unknown")),
                None,
                None,
                None,
                False,
                None,
            )
            return _CAPABILITY

        toolkit_layout = _apply_numba_cuda13_layout_compatibility()
        try:
            available = bool(cuda.is_available())
        except Exception:
            available = False
        if not available:
            driver_available = False
            toolkit_available = False
            try:
                driver_module = importlib.import_module(
                    "numba.cuda.cudadrv.driver"
                )
                driver_available = bool(driver_module.driver.is_available)
            except Exception:
                pass
            if driver_available:
                try:
                    nvvm_module = importlib.import_module(
                        "numba.cuda.cudadrv.nvvm"
                    )
                    toolkit_available = bool(nvvm_module.is_available())
                except Exception:
                    pass
            if not driver_available:
                reason = "cuda_driver_unavailable"
            elif not toolkit_available:
                reason = "cuda_toolkit_not_found"
            else:
                reason = "cuda_runtime_unavailable"
            _CAPABILITY = GpuCudaCapability(
                False,
                reason,
                str(getattr(numba, "__version__", "unknown")),
                None,
                None,
                None,
                False,
                toolkit_layout,
            )
            return _CAPABILITY
        try:
            device = cuda.get_current_device()
            raw_name = getattr(device, "name", "unknown")
            if isinstance(raw_name, bytes):
                device_name = raw_name.decode("utf-8", errors="replace")
            else:
                device_name = str(raw_name)
            capability_value = tuple(getattr(device, "compute_capability"))
            compute_capability = ".".join(str(value) for value in capability_value)
            device_id = int(getattr(device, "id", 0))
        except Exception:
            _CAPABILITY = GpuCudaCapability(
                False,
                "cuda_device_query_failed",
                str(getattr(numba, "__version__", "unknown")),
                None,
                None,
                None,
                False,
                toolkit_layout,
            )
            return _CAPABILITY

        _NUMBA = numba
        _CUDA = cuda
        _CAPABILITY = GpuCudaCapability(
            True,
            None,
            str(getattr(numba, "__version__", "unknown")),
            device_name,
            compute_capability,
            device_id,
            True,
            toolkit_layout or "numba_default",
        )
        return _CAPABILITY


def _make_kernel() -> Callable[..., None]:
    if _CUDA is None:
        raise GpuCudaProviderError("initialize", "cuda_runtime_not_initialized")
    cuda = _CUDA

    @cuda.jit(device=True, inline=True)
    def ray_box_entry(
        origin_x,
        origin_y,
        origin_z,
        direction_x,
        direction_y,
        direction_z,
        inverse_x,
        inverse_y,
        inverse_z,
        bounds_min,
        bounds_max,
        node_index,
        minimum_t,
        maximum_t,
    ):
        entry = minimum_t
        exit_distance = maximum_t
        for axis in range(3):
            if axis == 0:
                origin_value = origin_x
                direction_value = direction_x
                inverse_value = inverse_x
            elif axis == 1:
                origin_value = origin_y
                direction_value = direction_y
                inverse_value = inverse_y
            else:
                origin_value = origin_z
                direction_value = direction_z
                inverse_value = inverse_z
            if abs(direction_value) < 1e-12:
                if (
                    origin_value < bounds_min[node_index, axis]
                    or origin_value > bounds_max[node_index, axis]
                ):
                    return math.inf
                continue
            axis_entry = (
                bounds_min[node_index, axis] - origin_value
            ) * inverse_value
            axis_exit = (
                bounds_max[node_index, axis] - origin_value
            ) * inverse_value
            if axis_entry > axis_exit:
                temporary = axis_entry
                axis_entry = axis_exit
                axis_exit = temporary
            if axis_entry > entry:
                entry = axis_entry
            if axis_exit < exit_distance:
                exit_distance = axis_exit
            if exit_distance < entry:
                return math.inf
        return entry

    @cuda.jit(fastmath=False)
    def intersect_kernel(
        ray_count,
        stack_width,
        origins,
        directions,
        minimum_t_values,
        maximum_t_values,
        ignored_faces,
        triangle_v0,
        triangle_edge1,
        triangle_edge2,
        node_bounds_min,
        node_bounds_max,
        node_left,
        node_right,
        node_start,
        node_count,
        ordered_faces,
        traceable_face_mask,
        distances,
        face_indices,
        stack_entries,
        stack_nodes,
        overflow_flags,
    ):
        ray_index = cuda.grid(1)
        if ray_index >= ray_count:
            return
        distances[ray_index] = math.inf
        face_indices[ray_index] = -1
        overflow_flags[ray_index] = 0
        if node_count.shape[0] == 0:
            return
        minimum_t = minimum_t_values[ray_index]
        maximum_t = maximum_t_values[ray_index]
        if maximum_t <= minimum_t:
            return

        origin_x = origins[ray_index, 0]
        origin_y = origins[ray_index, 1]
        origin_z = origins[ray_index, 2]
        direction_x = directions[ray_index, 0]
        direction_y = directions[ray_index, 1]
        direction_z = directions[ray_index, 2]
        inverse_x = 0.0 if abs(direction_x) < 1e-15 else 1.0 / direction_x
        inverse_y = 0.0 if abs(direction_y) < 1e-15 else 1.0 / direction_y
        inverse_z = 0.0 if abs(direction_z) < 1e-15 else 1.0 / direction_z
        root_entry = ray_box_entry(
            origin_x,
            origin_y,
            origin_z,
            direction_x,
            direction_y,
            direction_z,
            inverse_x,
            inverse_y,
            inverse_z,
            node_bounds_min,
            node_bounds_max,
            0,
            minimum_t,
            maximum_t,
        )
        if math.isinf(root_entry):
            return

        base = ray_index * stack_width
        best_distance = maximum_t
        best_face_index = -1
        stack_entries[base] = root_entry
        stack_nodes[base] = 0
        stack_size = 1
        while stack_size > 0:
            stack_size -= 1
            entry = stack_entries[base + stack_size]
            current_node = stack_nodes[base + stack_size]
            if entry > best_distance:
                continue

            leaf_count = node_count[current_node]
            if leaf_count > 0:
                start = node_start[current_node]
                end = start + leaf_count
                for ordered_index in range(start, end):
                    face_index = ordered_faces[ordered_index]
                    if face_index == ignored_faces[ray_index]:
                        continue
                    if not traceable_face_mask[face_index]:
                        continue
                    edge1_x = triangle_edge1[face_index, 0]
                    edge1_y = triangle_edge1[face_index, 1]
                    edge1_z = triangle_edge1[face_index, 2]
                    edge2_x = triangle_edge2[face_index, 0]
                    edge2_y = triangle_edge2[face_index, 1]
                    edge2_z = triangle_edge2[face_index, 2]
                    cross_x = direction_y * edge2_z - direction_z * edge2_y
                    cross_y = direction_z * edge2_x - direction_x * edge2_z
                    cross_z = direction_x * edge2_y - direction_y * edge2_x
                    determinant = (
                        edge1_x * cross_x
                        + edge1_y * cross_y
                        + edge1_z * cross_z
                    )
                    if -1e-8 < determinant < 1e-8:
                        continue
                    inverse_determinant = 1.0 / determinant
                    offset_x = origin_x - triangle_v0[face_index, 0]
                    offset_y = origin_y - triangle_v0[face_index, 1]
                    offset_z = origin_z - triangle_v0[face_index, 2]
                    u = (
                        offset_x * cross_x
                        + offset_y * cross_y
                        + offset_z * cross_z
                    ) * inverse_determinant
                    if u < 0.0 or u > 1.0:
                        continue
                    offset_cross_x = offset_y * edge1_z - offset_z * edge1_y
                    offset_cross_y = offset_z * edge1_x - offset_x * edge1_z
                    offset_cross_z = offset_x * edge1_y - offset_y * edge1_x
                    v = (
                        direction_x * offset_cross_x
                        + direction_y * offset_cross_y
                        + direction_z * offset_cross_z
                    ) * inverse_determinant
                    if v < 0.0 or u + v > 1.0:
                        continue
                    distance = (
                        edge2_x * offset_cross_x
                        + edge2_y * offset_cross_y
                        + edge2_z * offset_cross_z
                    ) * inverse_determinant
                    if distance <= minimum_t or distance > best_distance:
                        continue
                    if (
                        best_face_index >= 0
                        and abs(distance - best_distance) <= 1e-10
                        and face_index >= best_face_index
                    ):
                        continue
                    best_distance = distance
                    best_face_index = face_index
                continue

            left_index = node_left[current_node]
            right_index = node_right[current_node]
            left_entry = ray_box_entry(
                origin_x,
                origin_y,
                origin_z,
                direction_x,
                direction_y,
                direction_z,
                inverse_x,
                inverse_y,
                inverse_z,
                node_bounds_min,
                node_bounds_max,
                left_index,
                minimum_t,
                best_distance,
            )
            right_entry = ray_box_entry(
                origin_x,
                origin_y,
                origin_z,
                direction_x,
                direction_y,
                direction_z,
                inverse_x,
                inverse_y,
                inverse_z,
                node_bounds_min,
                node_bounds_max,
                right_index,
                minimum_t,
                best_distance,
            )
            left_hit = not math.isinf(left_entry)
            right_hit = not math.isinf(right_entry)
            needed = 2 if left_hit and right_hit else (1 if left_hit or right_hit else 0)
            if stack_size + needed > stack_width:
                overflow_flags[ray_index] = 1
                return
            if left_hit and right_hit:
                if left_entry <= right_entry:
                    stack_entries[base + stack_size] = right_entry
                    stack_nodes[base + stack_size] = right_index
                    stack_size += 1
                    stack_entries[base + stack_size] = left_entry
                    stack_nodes[base + stack_size] = left_index
                    stack_size += 1
                else:
                    stack_entries[base + stack_size] = left_entry
                    stack_nodes[base + stack_size] = left_index
                    stack_size += 1
                    stack_entries[base + stack_size] = right_entry
                    stack_nodes[base + stack_size] = right_index
                    stack_size += 1
            elif left_hit:
                stack_entries[base + stack_size] = left_entry
                stack_nodes[base + stack_size] = left_index
                stack_size += 1
            elif right_hit:
                stack_entries[base + stack_size] = right_entry
                stack_nodes[base + stack_size] = right_index
                stack_size += 1

        if best_face_index >= 0:
            distances[ray_index] = best_distance
            face_indices[ray_index] = best_face_index

    return intersect_kernel


def _ensure_kernel() -> Callable[..., None]:
    global _KERNEL
    if _KERNEL is not None:
        return _KERNEL
    with _STATE_LOCK:
        if _KERNEL is None:
            try:
                _KERNEL = _make_kernel()
            except GpuCudaProviderError:
                raise
            except Exception as exc:
                raise GpuCudaProviderError(
                    "initialize",
                    "gpu_cuda_kernel_create_failed",
                ) from exc
        return _KERNEL


def _ensure_device_scene(
    scene: GpuCudaScene,
    capability: GpuCudaCapability,
) -> tuple[_DeviceScene, bool]:
    if _CUDA is None or capability.device_id is None:
        raise GpuCudaProviderError("initialize", "cuda_runtime_not_initialized")
    with scene._device_lock:
        existing = scene._device_scene
        if existing is not None and existing.device_id == capability.device_id:
            return existing, True
        started = time.perf_counter()
        try:
            device_scene = _DeviceScene(
                triangle_v0=_CUDA.to_device(scene.triangle_v0),
                triangle_edge1=_CUDA.to_device(scene.triangle_edge1),
                triangle_edge2=_CUDA.to_device(scene.triangle_edge2),
                node_bounds_min=_CUDA.to_device(scene.node_bounds_min),
                node_bounds_max=_CUDA.to_device(scene.node_bounds_max),
                node_left=_CUDA.to_device(scene.node_left),
                node_right=_CUDA.to_device(scene.node_right),
                node_start=_CUDA.to_device(scene.node_start),
                node_count=_CUDA.to_device(scene.node_count),
                ordered_faces=_CUDA.to_device(scene.ordered_faces),
                traceable_face_mask=_CUDA.to_device(scene.traceable_face_mask),
                device_id=capability.device_id,
                upload_sec=0.0,
            )
            _CUDA.synchronize()
        except Exception as exc:
            raise GpuCudaProviderError(
                "initialize",
                "gpu_cuda_scene_upload_failed",
            ) from exc
        device_scene.upload_sec = time.perf_counter() - started
        scene._device_scene = device_scene
        return device_scene, False


def _workspace_capacity(ray_count: int) -> int:
    capacity = 1
    while capacity < ray_count:
        capacity *= 2
    return capacity


def _ensure_workspace(
    scene: GpuCudaScene,
    capability: GpuCudaCapability,
    ray_count: int,
) -> tuple[_Workspace, bool, float]:
    if _CUDA is None or capability.device_id is None:
        raise GpuCudaProviderError("initialize", "cuda_runtime_not_initialized")
    existing = getattr(_WORKSPACES, "value", None)
    if (
        existing is not None
        and existing.scene_identity == id(scene)
        and existing.device_id == capability.device_id
        and existing.capacity >= ray_count
        and existing.stack_width >= scene.stack_width
    ):
        return existing, True, 0.0
    started = time.perf_counter()
    capacity = _workspace_capacity(max(1, ray_count))
    try:
        workspace = _Workspace(
            scene_identity=id(scene),
            device_id=capability.device_id,
            capacity=capacity,
            stack_width=scene.stack_width,
            origins=_CUDA.device_array((capacity, 3), dtype=np.float64),
            directions=_CUDA.device_array((capacity, 3), dtype=np.float64),
            minimum_t=_CUDA.device_array(capacity, dtype=np.float64),
            maximum_t=_CUDA.device_array(capacity, dtype=np.float64),
            ignored_faces=_CUDA.device_array(capacity, dtype=np.int64),
            distances=_CUDA.device_array(capacity, dtype=np.float64),
            face_indices=_CUDA.device_array(capacity, dtype=np.int64),
            stack_entries=_CUDA.device_array(
                capacity * scene.stack_width,
                dtype=np.float64,
            ),
            stack_nodes=_CUDA.device_array(
                capacity * scene.stack_width,
                dtype=np.int64,
            ),
            overflow_flags=_CUDA.device_array(capacity, dtype=np.uint8),
        )
    except Exception as exc:
        raise GpuCudaProviderError(
            "initialize",
            "gpu_cuda_workspace_allocation_failed",
        ) from exc
    _WORKSPACES.value = workspace
    return workspace, False, time.perf_counter() - started


def intersect_gpu_cuda(
    scene: GpuCudaScene,
    origins: FloatArray,
    directions: FloatArray,
    minimum_t: FloatArray,
    maximum_t: FloatArray,
    ignored_faces: IntArray,
) -> GpuCudaExecution:
    capability = probe_gpu_cuda()
    if not capability.available:
        raise GpuCudaUnavailable(capability.reason_code or "gpu_cuda_unavailable")
    ray_count = len(origins)
    if ray_count == 0:
        empty_float = np.empty(0, dtype=np.float64)
        empty_int = np.empty(0, dtype=np.int64)
        empty_float.setflags(write=False)
        empty_int.setflags(write=False)
        return GpuCudaExecution(
            distances=empty_float,
            face_indices=empty_int,
            scene_build_sec=scene.build_sec,
            scene_upload_sec=0.0,
            workspace_prepare_sec=0.0,
            input_upload_sec=0.0,
            jit_compile_sec=0.0,
            kernel_sec=0.0,
            output_download_sec=0.0,
            numba_version=capability.numba_version or "unknown",
            device_name=capability.device_name or "unknown",
            compute_capability=capability.compute_capability or "unknown",
            device_id=capability.device_id or 0,
            toolkit_layout=capability.toolkit_layout or "unknown",
            reused_device_scene=True,
            reused_workspace=True,
        )

    kernel = _ensure_kernel()
    device_scene, reused_device_scene = _ensure_device_scene(scene, capability)
    workspace, reused_workspace, workspace_prepare_sec = _ensure_workspace(
        scene,
        capability,
        ray_count,
    )
    upload_started = time.perf_counter()
    try:
        workspace.origins[:ray_count].copy_to_device(origins)
        workspace.directions[:ray_count].copy_to_device(directions)
        workspace.minimum_t[:ray_count].copy_to_device(minimum_t)
        workspace.maximum_t[:ray_count].copy_to_device(maximum_t)
        workspace.ignored_faces[:ray_count].copy_to_device(ignored_faces)
        _CUDA.synchronize()
    except Exception as exc:
        raise GpuCudaProviderError(
            "input_prepare",
            "gpu_cuda_input_upload_failed",
        ) from exc
    input_upload_sec = time.perf_counter() - upload_started

    global _KERNEL_COMPILED
    was_compiled = _KERNEL_COMPILED
    kernel_started = time.perf_counter()
    try:
        block_count = (ray_count + THREADS_PER_BLOCK - 1) // THREADS_PER_BLOCK
        try:
            performance_warning = importlib.import_module(
                "numba.core.errors"
            ).NumbaPerformanceWarning
        except Exception:
            performance_warning = Warning
        # Small late-depth wavefronts are expected.  Numba's generic launch
        # warning would otherwise print once for many different grid sizes in
        # a normal max-depth-10 run.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", performance_warning)
            kernel[block_count, THREADS_PER_BLOCK](
                ray_count,
                scene.stack_width,
                workspace.origins,
                workspace.directions,
                workspace.minimum_t,
                workspace.maximum_t,
                workspace.ignored_faces,
                device_scene.triangle_v0,
                device_scene.triangle_edge1,
                device_scene.triangle_edge2,
                device_scene.node_bounds_min,
                device_scene.node_bounds_max,
                device_scene.node_left,
                device_scene.node_right,
                device_scene.node_start,
                device_scene.node_count,
                device_scene.ordered_faces,
                device_scene.traceable_face_mask,
                workspace.distances,
                workspace.face_indices,
                workspace.stack_entries,
                workspace.stack_nodes,
                workspace.overflow_flags,
            )
        _CUDA.synchronize()
    except Exception as exc:
        raise GpuCudaProviderError("execute", "gpu_cuda_kernel_failed") from exc
    kernel_elapsed = time.perf_counter() - kernel_started
    _KERNEL_COMPILED = True

    download_started = time.perf_counter()
    try:
        distances = np.ascontiguousarray(
            workspace.distances[:ray_count].copy_to_host(),
            dtype=np.float64,
        )
        face_indices = np.ascontiguousarray(
            workspace.face_indices[:ray_count].copy_to_host(),
            dtype=np.int64,
        )
        overflow_flags = np.ascontiguousarray(
            workspace.overflow_flags[:ray_count].copy_to_host(),
            dtype=np.uint8,
        )
    except Exception as exc:
        raise GpuCudaProviderError(
            "result_validation",
            "gpu_cuda_output_download_failed",
        ) from exc
    output_download_sec = time.perf_counter() - download_started
    if np.any(overflow_flags):
        raise GpuCudaProviderError(
            "result_validation",
            "gpu_cuda_bvh_stack_overflow",
        )
    if not distances.flags.owndata or not face_indices.flags.owndata:
        distances = distances.copy(order="C")
        face_indices = face_indices.copy(order="C")
    if np.shares_memory(distances, face_indices):
        raise GpuCudaProviderError(
            "result_validation",
            "gpu_cuda_result_alias",
        )
    distances.setflags(write=False)
    face_indices.setflags(write=False)
    return GpuCudaExecution(
        distances=distances,
        face_indices=face_indices,
        scene_build_sec=scene.build_sec,
        scene_upload_sec=(0.0 if reused_device_scene else device_scene.upload_sec),
        workspace_prepare_sec=workspace_prepare_sec,
        input_upload_sec=input_upload_sec,
        jit_compile_sec=kernel_elapsed if not was_compiled else 0.0,
        # The first synchronized launch includes NVVM/JIT work.  Keep the
        # public timing fields non-overlapping so callers may sum them.
        kernel_sec=kernel_elapsed if was_compiled else 0.0,
        output_download_sec=output_download_sec,
        numba_version=capability.numba_version or "unknown",
        device_name=capability.device_name or "unknown",
        compute_capability=capability.compute_capability or "unknown",
        device_id=capability.device_id or 0,
        toolkit_layout=capability.toolkit_layout or "unknown",
        reused_device_scene=reused_device_scene,
        reused_workspace=reused_workspace,
    )


def _reset_gpu_cuda_provider_for_tests() -> None:
    """Reset lazy process state.  Intended only for isolated provider tests."""

    global _CAPABILITY, _CUDA, _NUMBA, _KERNEL, _KERNEL_COMPILED
    with _STATE_LOCK:
        _CAPABILITY = None
        _CUDA = None
        _NUMBA = None
        _KERNEL = None
        _KERNEL_COMPILED = False
        if hasattr(_WORKSPACES, "value"):
            delattr(_WORKSPACES, "value")


__all__ = [
    "GpuCudaCapability",
    "GpuCudaExecution",
    "GpuCudaProviderError",
    "GpuCudaScene",
    "GpuCudaUnavailable",
    "PROVIDER_CONTRACT",
    "PROVIDER_NAME",
    "intersect_gpu_cuda",
    "probe_gpu_cuda",
]
