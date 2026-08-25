from __future__ import annotations

"""Strict-float64 CUDA-resident multi-bounce wavefront provider.

PERF-4B keeps one primary ray on the device from depth zero through its
terminal event. Receiver tests, BVH traversal, optical lookup, counter RNG,
reflection sampling and energy termination execute in one CUDA launch per
primary chunk. PERF-4C optionally reduces receiver, heatmap and optical
summary data on the device and downloads only compact aggregate state plus
quota-selected visualization paths. PERF-4D stops writing full 3D event
geometry for every summary ray and deterministically retraces only the small
visualization quota. The established ordered-summary contract remains the
host-facing semantic boundary.

The module is deliberately optional.  Importing it never probes CUDA.  Every
failure is typed so the caller can replay the untouched logical chunk through
the established host-roundtrip implementation exactly once.
"""

from dataclasses import dataclass, field
import importlib
import math
import threading
import time
from typing import Any, Callable, Optional
import warnings

import numpy as np
from numpy.typing import ArrayLike, NDArray

from . import gpu_cuda_intersection as cuda_backend
from .gpu_cuda_summary_accumulator import (
    GpuSummaryAccumulatorExecution,
    GpuSummaryAccumulatorRequest,
    GpuSummaryAccumulatorSession,
    GpuSummaryDeviceEvents,
    accumulate_resident_summary_gpu_cuda,
)
from .native_cpu_counter_wavefront import (
    LANE_GAUSSIAN_AZIMUTH_BASE,
    LANE_GAUSSIAN_RADIAL_BASE,
    LANE_BOUNCE_MIS_RECEIVER,
    LANE_BOUNCE_MIS_SELECT,
    LANE_BOUNCE_MIS_U,
    LANE_BOUNCE_MIS_V,
    LANE_LAMBERTIAN_AZIMUTH,
    LANE_LAMBERTIAN_RADIAL,
    LANE_MIXED_LOBE,
    LANE_ROULETTE,
    MAX_GAUSSIAN_ATTEMPTS,
)
from .native_cpu_wavefront import (
    LOBE_GAUSSIAN,
    LOBE_LAMBERTIAN,
    LOBE_NONE,
    LOBE_SPECULAR,
    SCATTER_GAUSSIAN,
    SCATTER_LAMBERTIAN,
    SCATTER_MIXED,
    SCATTER_NONE,
    SCATTER_SPECULAR,
    STATUS_ATTEMPTED,
    STATUS_BELOW_ENERGY,
    STATUS_DEPTH_LIMITED,
    STATUS_DISABLED,
    STATUS_EMITTED,
    STATUS_ROULETTE_SURVIVED,
    STATUS_ROULETTE_TERMINATED,
    TERMINATION_RUSSIAN_ROULETTE,
    TERMINATION_THRESHOLD,
)
from .wavefront_event_tape import (
    PrimaryMajorEventTape,
    PrimaryMajorEventTapeBuilder,
    RAY_KIND_DIRECT,
    RAY_KIND_GAUSSIAN,
    RAY_KIND_LAMBERTIAN,
    RAY_KIND_SPECULAR,
    TERMINAL_BLOCKED,
    TERMINAL_ESCAPED,
    TERMINAL_RECEIVER,
)


FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
UIntArray = NDArray[np.uint64]

PROVIDER_NAME = "gpu_cuda_resident_wavefront"
PROVIDER_CONTRACT = "strict_float64_resident_wavefront_v1"
MONTE_CARLO_CONTRACT = "cpu_gpu_deterministic_batch_v1"
STATE_LAYOUT = "primary_thread_resident_masked_v1"
COMPACT_WORKSPACE_CONTRACT = "compact_summary_sparse_path_retrace_v1"
FULL_WORKSPACE_CONTRACT = "full_event_geometry_workspace_v1"
THREADS_PER_BLOCK = 128
MAX_SUPPORTED_DEPTH = 32

_MASK64 = (1 << 64) - 1
_DEPTH_SALT = 0xD2B74407B1CE6E93
_LANE_SALT = 0xCA5A826395121157
_STREAM_SALT = 0xA0761D6478BD642F
_TWO_POW_NEG_53 = 1.0 / float(1 << 53)
_TAU = 2.0 * math.pi


class GpuResidentWavefrontUnavailable(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.phase = "probe"


class GpuResidentWavefrontProviderError(RuntimeError):
    def __init__(self, phase: str, reason_code: str) -> None:
        super().__init__(reason_code)
        self.phase = phase
        self.reason_code = reason_code


def _owned_array(
    values: ArrayLike,
    dtype: Any,
    name: str,
    *,
    shape_tail: tuple[int, ...] = (),
) -> np.ndarray:
    array = np.array(values, dtype=dtype, order="C", copy=True)
    expected_dimensions = 1 + len(shape_tail)
    if array.ndim != expected_dimensions or array.shape[1:] != shape_tail:
        expected = "(N,)" if not shape_tail else f"(N, {', '.join(map(str, shape_tail))})"
        raise ValueError(f"{name} must have shape {expected}")
    if np.issubdtype(array.dtype, np.floating) and not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain finite values")
    array.setflags(write=False)
    return array


@dataclass(slots=True)
class GpuResidentWavefrontContext:
    scene: cuda_backend.GpuCudaScene
    triangle_normals: FloatArray | ArrayLike
    face_reflectance: FloatArray | ArrayLike
    face_roughness: FloatArray | ArrayLike
    face_scatter: NDArray[np.int8] | ArrayLike
    face_specular_ratio: FloatArray | ArrayLike
    face_gaussian_sigma_deg: FloatArray | ArrayLike
    receiver_centers: FloatArray | ArrayLike
    receiver_normals: FloatArray | ArrayLike
    receiver_u_axes: FloatArray | ArrayLike
    receiver_v_axes: FloatArray | ArrayLike
    receiver_half_widths: FloatArray | ArrayLike
    receiver_half_heights: FloatArray | ArrayLike
    receiver_inverse_widths: FloatArray | ArrayLike
    receiver_inverse_heights: FloatArray | ArrayLike
    receiver_minimum_cosines: FloatArray | ArrayLike
    receiver_columns: NDArray[np.int32] | ArrayLike
    receiver_rows: NDArray[np.int32] | ArrayLike
    _device_bindings: Any = field(default=None, init=False, repr=False)
    _summary_session: GpuSummaryAccumulatorSession = field(
        default_factory=GpuSummaryAccumulatorSession,
        init=False,
        repr=False,
    )
    _device_lock: threading.RLock = field(
        default_factory=threading.RLock,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        face_count = len(self.scene.triangle_v0)
        self.triangle_normals = _owned_array(
            self.triangle_normals,
            np.float64,
            "triangle_normals",
            shape_tail=(3,),
        )
        self.face_reflectance = _owned_array(
            self.face_reflectance,
            np.float64,
            "face_reflectance",
        )
        self.face_roughness = _owned_array(
            self.face_roughness,
            np.float64,
            "face_roughness",
        )
        self.face_scatter = _owned_array(
            self.face_scatter,
            np.int8,
            "face_scatter",
        )
        self.face_specular_ratio = _owned_array(
            self.face_specular_ratio,
            np.float64,
            "face_specular_ratio",
        )
        self.face_gaussian_sigma_deg = _owned_array(
            self.face_gaussian_sigma_deg,
            np.float64,
            "face_gaussian_sigma_deg",
        )
        face_arrays = (
            self.triangle_normals,
            self.face_reflectance,
            self.face_roughness,
            self.face_scatter,
            self.face_specular_ratio,
            self.face_gaussian_sigma_deg,
        )
        if any(len(values) != face_count for values in face_arrays):
            raise ValueError("resident face tables must align with the mesh")
        if np.any((self.face_reflectance < 0.0) | (self.face_reflectance > 1.0)):
            raise ValueError("face_reflectance values must be within [0, 1]")
        if np.any((self.face_roughness < 0.0) | (self.face_roughness > 1.0)):
            raise ValueError("face_roughness values must be within [0, 1]")
        if np.any(
            (self.face_scatter < SCATTER_NONE)
            | (self.face_scatter > SCATTER_MIXED)
        ):
            raise ValueError("face_scatter contains an unsupported code")
        if np.any(
            (self.face_specular_ratio < 0.0)
            | (self.face_specular_ratio > 1.0)
        ):
            raise ValueError("face_specular_ratio values must be within [0, 1]")
        if np.any(self.face_gaussian_sigma_deg <= 0.0):
            raise ValueError("face_gaussian_sigma_deg values must be positive")

        self.receiver_centers = _owned_array(
            self.receiver_centers,
            np.float64,
            "receiver_centers",
            shape_tail=(3,),
        )
        self.receiver_normals = _owned_array(
            self.receiver_normals,
            np.float64,
            "receiver_normals",
            shape_tail=(3,),
        )
        self.receiver_u_axes = _owned_array(
            self.receiver_u_axes,
            np.float64,
            "receiver_u_axes",
            shape_tail=(3,),
        )
        self.receiver_v_axes = _owned_array(
            self.receiver_v_axes,
            np.float64,
            "receiver_v_axes",
            shape_tail=(3,),
        )
        self.receiver_half_widths = _owned_array(
            self.receiver_half_widths,
            np.float64,
            "receiver_half_widths",
        )
        self.receiver_half_heights = _owned_array(
            self.receiver_half_heights,
            np.float64,
            "receiver_half_heights",
        )
        self.receiver_inverse_widths = _owned_array(
            self.receiver_inverse_widths,
            np.float64,
            "receiver_inverse_widths",
        )
        self.receiver_inverse_heights = _owned_array(
            self.receiver_inverse_heights,
            np.float64,
            "receiver_inverse_heights",
        )
        self.receiver_minimum_cosines = _owned_array(
            self.receiver_minimum_cosines,
            np.float64,
            "receiver_minimum_cosines",
        )
        self.receiver_columns = _owned_array(
            self.receiver_columns,
            np.int32,
            "receiver_columns",
        )
        self.receiver_rows = _owned_array(
            self.receiver_rows,
            np.int32,
            "receiver_rows",
        )
        receiver_count = len(self.receiver_centers)
        receiver_arrays = (
            self.receiver_normals,
            self.receiver_u_axes,
            self.receiver_v_axes,
            self.receiver_half_widths,
            self.receiver_half_heights,
            self.receiver_inverse_widths,
            self.receiver_inverse_heights,
            self.receiver_minimum_cosines,
            self.receiver_columns,
            self.receiver_rows,
        )
        if any(len(values) != receiver_count for values in receiver_arrays):
            raise ValueError("resident receiver tables must have equal row counts")
        if np.any(self.receiver_columns <= 0) or np.any(self.receiver_rows <= 0):
            raise ValueError("receiver grid dimensions must be positive")


@dataclass(frozen=True, slots=True)
class GpuResidentWavefrontBatch:
    origins: FloatArray | ArrayLike
    directions: FloatArray | ArrayLike
    initial_power_lumen: FloatArray | ArrayLike
    source_faces: IntArray | ArrayLike
    reflection_seeds: UIntArray | ArrayLike
    max_depth: int
    epsilon_mm: float
    min_energy: float
    termination_mode: int
    include_path_payload: bool = False
    bounce_receiver_mis_enabled: bool = False
    bounce_receiver_importance_fraction: float = 0.5

    def __post_init__(self) -> None:
        origins = _owned_array(
            self.origins,
            np.float64,
            "origins",
            shape_tail=(3,),
        )
        directions = _owned_array(
            self.directions,
            np.float64,
            "directions",
            shape_tail=(3,),
        )
        powers = _owned_array(
            self.initial_power_lumen,
            np.float64,
            "initial_power_lumen",
        )
        faces = _owned_array(self.source_faces, np.int64, "source_faces")
        seeds = _owned_array(
            self.reflection_seeds,
            np.uint64,
            "reflection_seeds",
        )
        ray_count = len(origins)
        if any(len(values) != ray_count for values in (directions, powers, faces, seeds)):
            raise ValueError("resident batch arrays must have equal row counts")
        if ray_count:
            length_squared = np.einsum("ij,ij->i", directions, directions)
            if not np.allclose(length_squared, 1.0, rtol=1e-7, atol=1e-9):
                raise ValueError("directions must be normalized")
        if np.any(powers < 0.0):
            raise ValueError("initial_power_lumen must be non-negative")
        if np.any(faces < -1):
            raise ValueError("source_faces values must be -1 or a face index")
        max_depth = int(self.max_depth)
        if max_depth < 0 or max_depth > MAX_SUPPORTED_DEPTH:
            raise ValueError(
                f"max_depth must be within [0, {MAX_SUPPORTED_DEPTH}]"
            )
        epsilon = float(self.epsilon_mm)
        min_energy = float(self.min_energy)
        if not math.isfinite(epsilon) or epsilon <= 0.0:
            raise ValueError("epsilon_mm must be finite and positive")
        if not math.isfinite(min_energy) or min_energy < 0.0:
            raise ValueError("min_energy must be finite and non-negative")
        termination = int(self.termination_mode)
        if termination not in {
            TERMINATION_THRESHOLD,
            TERMINATION_RUSSIAN_ROULETTE,
        }:
            raise ValueError("termination_mode is unsupported")
        object.__setattr__(self, "origins", origins)
        object.__setattr__(self, "directions", directions)
        object.__setattr__(self, "initial_power_lumen", powers)
        object.__setattr__(self, "source_faces", faces)
        object.__setattr__(self, "reflection_seeds", seeds)
        object.__setattr__(self, "max_depth", max_depth)
        object.__setattr__(self, "epsilon_mm", epsilon)
        object.__setattr__(self, "min_energy", min_energy)
        object.__setattr__(self, "termination_mode", termination)
        object.__setattr__(self, "include_path_payload", bool(self.include_path_payload))
        bounce_receiver_mis_enabled = bool(self.bounce_receiver_mis_enabled)
        bounce_fraction = float(self.bounce_receiver_importance_fraction)
        if not math.isfinite(bounce_fraction) or not 0.0 < bounce_fraction < 1.0:
            raise ValueError(
                "bounce_receiver_importance_fraction must be within (0, 1)"
            )
        object.__setattr__(
            self,
            "bounce_receiver_mis_enabled",
            bounce_receiver_mis_enabled,
        )
        object.__setattr__(
            self,
            "bounce_receiver_importance_fraction",
            bounce_fraction,
        )

    def __len__(self) -> int:
        return len(self.origins)


@dataclass(frozen=True, slots=True)
class GpuResidentPathSelection:
    existing_path_count: int
    existing_dead_end_count: int
    max_paths: int

    def __post_init__(self) -> None:
        existing_path_count = int(self.existing_path_count)
        existing_dead_end_count = int(self.existing_dead_end_count)
        max_paths = int(self.max_paths)
        if max_paths < 0:
            raise ValueError("max_paths must be non-negative")
        if existing_path_count < 0 or existing_path_count > max_paths:
            raise ValueError("existing_path_count is outside the path quota")
        if (
            existing_dead_end_count < 0
            or existing_dead_end_count > existing_path_count
        ):
            raise ValueError("existing_dead_end_count is invalid")
        object.__setattr__(self, "existing_path_count", existing_path_count)
        object.__setattr__(
            self,
            "existing_dead_end_count",
            existing_dead_end_count,
        )
        object.__setattr__(self, "max_paths", max_paths)


@dataclass(frozen=True, slots=True)
class GpuResidentWavefrontExecution:
    tape: Optional[PrimaryMajorEventTape]
    summary_execution: Optional[GpuSummaryAccumulatorExecution]
    selected_path_tape: Optional[PrimaryMajorEventTape]
    selected_path_count: int
    skipped_path_count: int
    path_select_sec: float
    path_retrace_sec: float
    path_download_sec: float
    workspace_contract: str
    workspace_bytes: int
    event_geometry_capacity: int
    active_ray_count_by_depth: tuple[int, ...]
    logical_intersection_rows: int
    stochastic_primary_ray_count: int
    scene_upload_sec: float
    bindings_upload_sec: float
    workspace_prepare_sec: float
    input_upload_sec: float
    jit_compile_sec: float
    kernel_sec: float
    output_download_sec: float
    tape_build_sec: float
    total_sec: float
    numba_version: str
    device_name: str
    compute_capability: str
    device_id: int
    toolkit_layout: str
    reused_device_scene: bool
    reused_device_bindings: bool
    reused_workspace: bool
    bounce_importance_eligible_count: int
    bounce_importance_directed_count: int
    bounce_importance_zero_weight_count: int
    bounce_importance_unsupported_count: int
    bounce_importance_weight_sum: float
    bounce_importance_weight_square_sum: float
    bounce_importance_weight_min: float
    bounce_importance_weight_max: float
    strict_float64: bool = True
    provider_contract: str = PROVIDER_CONTRACT
    state_layout: str = STATE_LAYOUT


@dataclass(slots=True)
class _DeviceBindings:
    context_identity: int
    device_id: int
    triangle_normals: Any
    face_reflectance: Any
    face_roughness: Any
    face_scatter: Any
    face_specular_ratio: Any
    face_gaussian_sigma_deg: Any
    receiver_centers: Any
    receiver_normals: Any
    receiver_u_axes: Any
    receiver_v_axes: Any
    receiver_half_widths: Any
    receiver_half_heights: Any
    receiver_inverse_widths: Any
    receiver_inverse_heights: Any
    receiver_minimum_cosines: Any
    receiver_columns: Any
    receiver_rows: Any
    upload_sec: float


@dataclass(slots=True)
class _Workspace:
    context_identity: int
    device_id: int
    workspace_contract: str
    capacity: int
    depth_capacity: int
    geometry_capacity: int
    stack_width: int
    origins: Any
    directions: Any
    powers: Any
    source_faces: Any
    reflection_seeds: Any
    event_faces: Any
    event_distances: Any
    event_points: Any
    event_normals: Any
    event_incoming_power: Any
    event_reflected_power: Any
    event_emitted_power: Any
    event_status: Any
    event_lobes: Any
    event_incoming_kinds: Any
    terminal_kind: Any
    terminal_depth: Any
    terminal_power: Any
    terminal_ray_kind: Any
    terminal_receiver: Any
    terminal_row: Any
    terminal_column: Any
    terminal_received_power: Any
    terminal_point: Any
    terminal_normal: Any
    terminal_distance: Any
    terminal_incoming_power: Any
    stochastic_primary: Any
    active_by_depth: Any
    stack_entries: Any
    stack_nodes: Any
    overflow_flags: Any
    bounce_importance_counts: Any
    bounce_importance_weight_stats: Any


_STATE_LOCK = threading.RLock()
_KERNEL: Optional[Callable[..., None]] = None
_KERNEL_COMPILED = False
_PATH_SELECT_KERNEL: Optional[Callable[..., None]] = None
_PATH_GATHER_KERNEL: Optional[Callable[..., None]] = None
_WORKSPACES = threading.local()


def _workspace_capacity(ray_count: int) -> int:
    capacity = 1
    while capacity < max(1, ray_count):
        capacity *= 2
    return capacity


def _ensure_device_bindings(
    context: GpuResidentWavefrontContext,
    capability: cuda_backend.GpuCudaCapability,
) -> tuple[_DeviceBindings, bool]:
    cuda = cuda_backend._CUDA
    if cuda is None or capability.device_id is None:
        raise GpuResidentWavefrontProviderError(
            "initialize",
            "gpu_resident_cuda_runtime_not_initialized",
        )
    with context._device_lock:
        existing = context._device_bindings
        if existing is not None and existing.device_id == capability.device_id:
            return existing, True
        started = time.perf_counter()
        try:
            bindings = _DeviceBindings(
                context_identity=id(context),
                device_id=capability.device_id,
                triangle_normals=cuda.to_device(context.triangle_normals),
                face_reflectance=cuda.to_device(context.face_reflectance),
                face_roughness=cuda.to_device(context.face_roughness),
                face_scatter=cuda.to_device(context.face_scatter),
                face_specular_ratio=cuda.to_device(context.face_specular_ratio),
                face_gaussian_sigma_deg=cuda.to_device(
                    context.face_gaussian_sigma_deg
                ),
                receiver_centers=cuda.to_device(context.receiver_centers),
                receiver_normals=cuda.to_device(context.receiver_normals),
                receiver_u_axes=cuda.to_device(context.receiver_u_axes),
                receiver_v_axes=cuda.to_device(context.receiver_v_axes),
                receiver_half_widths=cuda.to_device(context.receiver_half_widths),
                receiver_half_heights=cuda.to_device(context.receiver_half_heights),
                receiver_inverse_widths=cuda.to_device(
                    context.receiver_inverse_widths
                ),
                receiver_inverse_heights=cuda.to_device(
                    context.receiver_inverse_heights
                ),
                receiver_minimum_cosines=cuda.to_device(
                    context.receiver_minimum_cosines
                ),
                receiver_columns=cuda.to_device(context.receiver_columns),
                receiver_rows=cuda.to_device(context.receiver_rows),
                upload_sec=0.0,
            )
            cuda.synchronize()
        except Exception as exc:
            raise GpuResidentWavefrontProviderError(
                "initialize",
                "gpu_resident_bindings_upload_failed",
            ) from exc
        bindings.upload_sec = time.perf_counter() - started
        context._device_bindings = bindings
        return bindings, False


def _ensure_workspace(
    context: GpuResidentWavefrontContext,
    capability: cuda_backend.GpuCudaCapability,
    ray_count: int,
    depth_count: int,
    geometry_count: int,
    workspace_contract: str,
) -> tuple[_Workspace, bool, float]:
    cuda = cuda_backend._CUDA
    if cuda is None or capability.device_id is None:
        raise GpuResidentWavefrontProviderError(
            "initialize",
            "gpu_resident_cuda_runtime_not_initialized",
        )
    workspace_cache = getattr(_WORKSPACES, "values", None)
    if workspace_cache is None:
        workspace_cache = {}
        _WORKSPACES.values = workspace_cache
    existing = workspace_cache.get(workspace_contract)
    if (
        existing is not None
        and existing.context_identity == id(context)
        and existing.device_id == capability.device_id
        and existing.capacity >= ray_count
        and existing.depth_capacity >= depth_count
        and existing.geometry_capacity >= geometry_count
        and existing.stack_width >= context.scene.stack_width
    ):
        return existing, True, 0.0
    started = time.perf_counter()
    capacity = _workspace_capacity(ray_count)
    depth_capacity = max(1, depth_count)
    geometry_capacity = _workspace_capacity(max(1, geometry_count))
    geometry_capacity = min(capacity, geometry_capacity)
    event_shape = (capacity, depth_capacity)
    geometry_event_shape = (geometry_capacity, depth_capacity)
    try:
        workspace = _Workspace(
            context_identity=id(context),
            device_id=capability.device_id,
            workspace_contract=workspace_contract,
            capacity=capacity,
            depth_capacity=depth_capacity,
            geometry_capacity=geometry_capacity,
            stack_width=context.scene.stack_width,
            origins=cuda.device_array((capacity, 3), dtype=np.float64),
            directions=cuda.device_array((capacity, 3), dtype=np.float64),
            powers=cuda.device_array(capacity, dtype=np.float64),
            source_faces=cuda.device_array(capacity, dtype=np.int64),
            reflection_seeds=cuda.device_array(capacity, dtype=np.uint64),
            event_faces=cuda.device_array(event_shape, dtype=np.int32),
            event_distances=cuda.device_array(
                geometry_event_shape,
                dtype=np.float64,
            ),
            event_points=cuda.device_array(
                (*geometry_event_shape, 3),
                dtype=np.float64,
            ),
            event_normals=cuda.device_array(
                (*geometry_event_shape, 3),
                dtype=np.float64,
            ),
            event_incoming_power=cuda.device_array(event_shape, dtype=np.float64),
            event_reflected_power=cuda.device_array(event_shape, dtype=np.float64),
            event_emitted_power=cuda.device_array(event_shape, dtype=np.float64),
            event_status=cuda.device_array(event_shape, dtype=np.uint16),
            event_lobes=cuda.device_array(event_shape, dtype=np.int8),
            event_incoming_kinds=cuda.device_array(event_shape, dtype=np.int8),
            terminal_kind=cuda.device_array(capacity, dtype=np.int8),
            terminal_depth=cuda.device_array(capacity, dtype=np.int16),
            terminal_power=cuda.device_array(capacity, dtype=np.float64),
            terminal_ray_kind=cuda.device_array(capacity, dtype=np.int8),
            terminal_receiver=cuda.device_array(capacity, dtype=np.int32),
            terminal_row=cuda.device_array(capacity, dtype=np.int32),
            terminal_column=cuda.device_array(capacity, dtype=np.int32),
            terminal_received_power=cuda.device_array(capacity, dtype=np.float64),
            terminal_point=cuda.device_array(
                (geometry_capacity, 3),
                dtype=np.float64,
            ),
            terminal_normal=cuda.device_array(
                (geometry_capacity, 3),
                dtype=np.float64,
            ),
            terminal_distance=cuda.device_array(
                geometry_capacity,
                dtype=np.float64,
            ),
            terminal_incoming_power=cuda.device_array(capacity, dtype=np.float64),
            stochastic_primary=cuda.device_array(capacity, dtype=np.uint8),
            active_by_depth=cuda.device_array(depth_capacity, dtype=np.int64),
            stack_entries=cuda.device_array(
                capacity * context.scene.stack_width,
                dtype=np.float64,
            ),
            stack_nodes=cuda.device_array(
                capacity * context.scene.stack_width,
                dtype=np.int64,
            ),
            overflow_flags=cuda.device_array(capacity, dtype=np.uint8),
            bounce_importance_counts=cuda.device_array(4, dtype=np.int64),
            bounce_importance_weight_stats=cuda.device_array(
                4,
                dtype=np.float64,
            ),
        )
    except Exception as exc:
        raise GpuResidentWavefrontProviderError(
            "initialize",
            "gpu_resident_workspace_allocation_failed",
        ) from exc
    workspace_cache[workspace_contract] = workspace
    return workspace, False, time.perf_counter() - started


def _workspace_bytes(workspace: _Workspace) -> int:
    arrays = (
        workspace.origins,
        workspace.directions,
        workspace.powers,
        workspace.source_faces,
        workspace.reflection_seeds,
        workspace.event_faces,
        workspace.event_distances,
        workspace.event_points,
        workspace.event_normals,
        workspace.event_incoming_power,
        workspace.event_reflected_power,
        workspace.event_emitted_power,
        workspace.event_status,
        workspace.event_lobes,
        workspace.event_incoming_kinds,
        workspace.terminal_kind,
        workspace.terminal_depth,
        workspace.terminal_power,
        workspace.terminal_ray_kind,
        workspace.terminal_receiver,
        workspace.terminal_row,
        workspace.terminal_column,
        workspace.terminal_received_power,
        workspace.terminal_point,
        workspace.terminal_normal,
        workspace.terminal_distance,
        workspace.terminal_incoming_power,
        workspace.stochastic_primary,
        workspace.active_by_depth,
        workspace.stack_entries,
        workspace.stack_nodes,
        workspace.overflow_flags,
        workspace.bounce_importance_counts,
        workspace.bounce_importance_weight_stats,
    )
    return sum(int(array.size) * int(array.dtype.itemsize) for array in arrays)


def _make_kernel() -> Callable[..., None]:
    cuda = cuda_backend._CUDA
    if cuda is None:
        raise GpuResidentWavefrontProviderError(
            "initialize",
            "gpu_resident_cuda_runtime_not_initialized",
        )

    @cuda.jit(device=True, inline=True)
    def normalize(x_value, y_value, z_value):
        magnitude_squared = (
            x_value * x_value + y_value * y_value + z_value * z_value
        )
        if magnitude_squared <= 1e-30:
            return 0.0, 0.0, 0.0
        inverse = 1.0 / math.sqrt(magnitude_squared)
        return x_value * inverse, y_value * inverse, z_value * inverse

    @cuda.jit(device=True, inline=True)
    def orient(in_x, in_y, in_z, normal_x, normal_y, normal_z):
        normal_x, normal_y, normal_z = normalize(
            normal_x,
            normal_y,
            normal_z,
        )
        if in_x * normal_x + in_y * normal_y + in_z * normal_z > 0.0:
            return -normal_x, -normal_y, -normal_z
        return normal_x, normal_y, normal_z

    @cuda.jit(device=True, inline=True)
    def splitmix64(value):
        value = value + np.uint64(0x9E3779B97F4A7C15)
        value = (value ^ (value >> np.uint64(30))) * np.uint64(
            0xBF58476D1CE4E5B9
        )
        value = (value ^ (value >> np.uint64(27))) * np.uint64(
            0x94D049BB133111EB
        )
        return value ^ (value >> np.uint64(31))

    @cuda.jit(device=True, inline=True)
    def uniform(key, depth, lane):
        counter = (
            key
            ^ np.uint64(_STREAM_SALT)
            ^ (np.uint64(depth + 1) * np.uint64(_DEPTH_SALT))
            ^ (np.uint64(lane + 1) * np.uint64(_LANE_SALT))
        )
        mixed = splitmix64(counter)
        return float(mixed >> np.uint64(11)) * _TWO_POW_NEG_53

    @cuda.jit(device=True, inline=True)
    def basis(w_x, w_y, w_z):
        if abs(w_z) > 0.95:
            u_x, u_y, u_z = normalize(w_z, 0.0, -w_x)
        else:
            u_x, u_y, u_z = normalize(-w_y, w_x, 0.0)
        v_x = w_y * u_z - w_z * u_y
        v_y = w_z * u_x - w_x * u_z
        v_z = w_x * u_y - w_y * u_x
        return u_x, u_y, u_z, v_x, v_y, v_z

    @cuda.jit(device=True, inline=True)
    def specular(in_x, in_y, in_z, normal_x, normal_y, normal_z):
        incidence = in_x * normal_x + in_y * normal_y + in_z * normal_z
        return (
            in_x - 2.0 * incidence * normal_x,
            in_y - 2.0 * incidence * normal_y,
            in_z - 2.0 * incidence * normal_z,
        )

    @cuda.jit(device=True, inline=True)
    def effective_reflectance(
        in_x,
        in_y,
        in_z,
        normal_x,
        normal_y,
        normal_z,
        base_reflectance,
        roughness,
    ):
        cosine_incidence = -(
            in_x * normal_x + in_y * normal_y + in_z * normal_z
        )
        if cosine_incidence < 0.0:
            cosine_incidence = 0.0
        elif cosine_incidence > 1.0:
            cosine_incidence = 1.0
        grazing_coordinate = (0.7 - cosine_incidence) / 0.7
        if grazing_coordinate < 0.0:
            grazing_coordinate = 0.0
        grazing_term = math.pow(grazing_coordinate, 5.0)
        gloss_response = 0.25 + 0.75 * (1.0 - roughness)
        result = (
            base_reflectance
            + (1.0 - base_reflectance) * grazing_term * gloss_response
        )
        if result < 0.0:
            return 0.0
        if result > 1.0:
            return 1.0
        return result

    @cuda.jit(device=True, inline=True)
    def lambertian(key, depth, normal_x, normal_y, normal_z):
        u_x, u_y, u_z, v_x, v_y, v_z = basis(
            normal_x,
            normal_y,
            normal_z,
        )
        radial = uniform(key, depth, LANE_LAMBERTIAN_RADIAL)
        azimuth = uniform(key, depth, LANE_LAMBERTIAN_AZIMUTH)
        radius = math.sqrt(radial)
        phi = _TAU * azimuth
        x_value = radius * math.cos(phi)
        y_value = radius * math.sin(phi)
        remaining = 1.0 - radial
        if remaining < 0.0:
            remaining = 0.0
        z_value = math.sqrt(remaining)
        return (
            u_x * x_value + v_x * y_value + normal_x * z_value,
            u_y * x_value + v_y * y_value + normal_y * z_value,
            u_z * x_value + v_z * y_value + normal_z * z_value,
        )

    @cuda.jit(device=True, inline=True)
    def gaussian(
        key,
        depth,
        axis_x,
        axis_y,
        axis_z,
        normal_x,
        normal_y,
        normal_z,
        sigma_deg,
    ):
        u_x, u_y, u_z, v_x, v_y, v_z = basis(axis_x, axis_y, axis_z)
        sigma_value = sigma_deg
        if sigma_value < 1e-6:
            sigma_value = 1e-6
        sigma_rad = sigma_value * (math.pi / 180.0)
        for attempt in range(MAX_GAUSSIAN_ATTEMPTS):
            radial = 1.0 - uniform(
                key,
                depth,
                LANE_GAUSSIAN_RADIAL_BASE + attempt,
            )
            if radial < 1e-12:
                radial = 1e-12
            theta = sigma_rad * math.sqrt(-2.0 * math.log(radial))
            if theta >= math.pi * 0.5:
                continue
            phi = _TAU * uniform(
                key,
                depth,
                LANE_GAUSSIAN_AZIMUTH_BASE + attempt,
            )
            cosine = math.cos(theta)
            sine = math.sin(theta)
            u_scale = sine * math.cos(phi)
            v_scale = sine * math.sin(phi)
            direction_x = axis_x * cosine + u_x * u_scale + v_x * v_scale
            direction_y = axis_y * cosine + u_y * u_scale + v_y * v_scale
            direction_z = axis_z * cosine + u_z * u_scale + v_z * v_scale
            if (
                direction_x * normal_x
                + direction_y * normal_y
                + direction_z * normal_z
            ) > 1e-9:
                return direction_x, direction_y, direction_z, (attempt + 1) * 2
        return axis_x, axis_y, axis_z, MAX_GAUSSIAN_ATTEMPTS * 2

    @cuda.jit(device=True, inline=True)
    def receiver_pdf(
        origin_x,
        origin_y,
        origin_z,
        direction_x,
        direction_y,
        direction_z,
        epsilon_mm,
        receiver_centers,
        receiver_normals,
        receiver_u_axes,
        receiver_v_axes,
        receiver_half_widths,
        receiver_half_heights,
        receiver_minimum_cosines,
    ):
        receiver_count = receiver_centers.shape[0]
        probability = 1.0 / float(receiver_count)
        density = 0.0
        for receiver_index in range(receiver_count):
            normal_x = receiver_normals[receiver_index, 0]
            normal_y = receiver_normals[receiver_index, 1]
            normal_z = receiver_normals[receiver_index, 2]
            denominator = (
                direction_x * normal_x
                + direction_y * normal_y
                + direction_z * normal_z
            )
            if abs(denominator) < 1e-12:
                continue
            numerator = (
                (receiver_centers[receiver_index, 0] - origin_x) * normal_x
                + (receiver_centers[receiver_index, 1] - origin_y) * normal_y
                + (receiver_centers[receiver_index, 2] - origin_z) * normal_z
            )
            distance = numerator / denominator
            acceptance_cosine = -denominator
            if (
                distance <= epsilon_mm
                or acceptance_cosine <= 0.0
                or acceptance_cosine < receiver_minimum_cosines[receiver_index]
            ):
                continue
            point_x = origin_x + direction_x * distance
            point_y = origin_y + direction_y * distance
            point_z = origin_z + direction_z * distance
            local_x = point_x - receiver_centers[receiver_index, 0]
            local_y = point_y - receiver_centers[receiver_index, 1]
            local_z = point_z - receiver_centers[receiver_index, 2]
            local_u = (
                local_x * receiver_u_axes[receiver_index, 0]
                + local_y * receiver_u_axes[receiver_index, 1]
                + local_z * receiver_u_axes[receiver_index, 2]
            )
            local_v = (
                local_x * receiver_v_axes[receiver_index, 0]
                + local_y * receiver_v_axes[receiver_index, 1]
                + local_z * receiver_v_axes[receiver_index, 2]
            )
            half_width = receiver_half_widths[receiver_index]
            half_height = receiver_half_heights[receiver_index]
            if (
                abs(local_u) > half_width + 1e-9
                or abs(local_v) > half_height + 1e-9
            ):
                continue
            area = 4.0 * half_width * half_height
            density += (
                probability
                * distance
                * distance
                / (area * acceptance_cosine)
            )
        return density

    @cuda.jit(device=True, inline=True)
    def lambertian_receiver_mis(
        key,
        depth,
        origin_x,
        origin_y,
        origin_z,
        normal_x,
        normal_y,
        normal_z,
        source_x,
        source_y,
        source_z,
        fraction,
        epsilon_mm,
        receiver_centers,
        receiver_normals,
        receiver_u_axes,
        receiver_v_axes,
        receiver_half_widths,
        receiver_half_heights,
        receiver_minimum_cosines,
    ):
        direction_x = source_x
        direction_y = source_y
        direction_z = source_z
        directed = uniform(key, depth, LANE_BOUNCE_MIS_SELECT) < fraction
        if directed:
            receiver_count = receiver_centers.shape[0]
            receiver_index = int(
                uniform(key, depth, LANE_BOUNCE_MIS_RECEIVER)
                * receiver_count
            )
            if receiver_index >= receiver_count:
                receiver_index = receiver_count - 1
            u_offset = (
                uniform(key, depth, LANE_BOUNCE_MIS_U) * 2.0 - 1.0
            ) * receiver_half_widths[receiver_index]
            v_offset = (
                uniform(key, depth, LANE_BOUNCE_MIS_V) * 2.0 - 1.0
            ) * receiver_half_heights[receiver_index]
            target_x = (
                receiver_centers[receiver_index, 0]
                + u_offset * receiver_u_axes[receiver_index, 0]
                + v_offset * receiver_v_axes[receiver_index, 0]
            )
            target_y = (
                receiver_centers[receiver_index, 1]
                + u_offset * receiver_u_axes[receiver_index, 1]
                + v_offset * receiver_v_axes[receiver_index, 1]
            )
            target_z = (
                receiver_centers[receiver_index, 2]
                + u_offset * receiver_u_axes[receiver_index, 2]
                + v_offset * receiver_v_axes[receiver_index, 2]
            )
            direction_x, direction_y, direction_z = normalize(
                target_x - origin_x,
                target_y - origin_y,
                target_z - origin_z,
            )
        source_pdf = (
            direction_x * normal_x
            + direction_y * normal_y
            + direction_z * normal_z
        )
        if source_pdf < 0.0:
            source_pdf = 0.0
        source_pdf /= math.pi
        proposal_pdf = receiver_pdf(
            origin_x,
            origin_y,
            origin_z,
            direction_x,
            direction_y,
            direction_z,
            epsilon_mm,
            receiver_centers,
            receiver_normals,
            receiver_u_axes,
            receiver_v_axes,
            receiver_half_widths,
            receiver_half_heights,
            receiver_minimum_cosines,
        )
        mixture_pdf = (1.0 - fraction) * source_pdf + fraction * proposal_pdf
        weight = source_pdf / mixture_pdf if mixture_pdf > 0.0 else 0.0
        return direction_x, direction_y, direction_z, weight, directed

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

    @cuda.jit(device=True, inline=True)
    def intersect_bvh(
        ray_index,
        stack_width,
        origin_x,
        origin_y,
        origin_z,
        direction_x,
        direction_y,
        direction_z,
        minimum_t,
        maximum_t,
        ignored_face,
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
        stack_entries,
        stack_nodes,
    ):
        if node_count.shape[0] == 0 or maximum_t <= minimum_t:
            return math.inf, -1, 0
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
            return math.inf, -1, 0

        base = ray_index * stack_width
        best_distance = maximum_t
        best_face = -1
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
                    if face_index == ignored_face or not traceable_face_mask[face_index]:
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
                    u_value = (
                        offset_x * cross_x
                        + offset_y * cross_y
                        + offset_z * cross_z
                    ) * inverse_determinant
                    if u_value < 0.0 or u_value > 1.0:
                        continue
                    offset_cross_x = offset_y * edge1_z - offset_z * edge1_y
                    offset_cross_y = offset_z * edge1_x - offset_x * edge1_z
                    offset_cross_z = offset_x * edge1_y - offset_y * edge1_x
                    v_value = (
                        direction_x * offset_cross_x
                        + direction_y * offset_cross_y
                        + direction_z * offset_cross_z
                    ) * inverse_determinant
                    if v_value < 0.0 or u_value + v_value > 1.0:
                        continue
                    distance = (
                        edge2_x * offset_cross_x
                        + edge2_y * offset_cross_y
                        + edge2_z * offset_cross_z
                    ) * inverse_determinant
                    if distance <= minimum_t or distance > best_distance:
                        continue
                    if (
                        best_face >= 0
                        and abs(distance - best_distance) <= 1e-10
                        and face_index >= best_face
                    ):
                        continue
                    best_distance = distance
                    best_face = face_index
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
                return math.inf, -1, 1
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
        if best_face < 0:
            return math.inf, -1, 0
        return best_distance, best_face, 0

    @cuda.jit(fastmath=False)
    def resident_kernel(
        ray_count,
        depth_count,
        max_depth,
        stack_width,
        epsilon_mm,
        min_energy,
        termination_mode,
        bounce_receiver_mis_enabled,
        bounce_receiver_importance_fraction,
        record_geometry,
        origins,
        directions,
        powers,
        source_faces,
        reflection_seeds,
        triangle_v0,
        triangle_edge1,
        triangle_edge2,
        triangle_normals,
        node_bounds_min,
        node_bounds_max,
        node_left,
        node_right,
        node_start,
        node_count,
        ordered_faces,
        traceable_face_mask,
        face_reflectance,
        face_roughness,
        face_scatter,
        face_specular_ratio,
        face_gaussian_sigma_deg,
        receiver_centers,
        receiver_normals,
        receiver_u_axes,
        receiver_v_axes,
        receiver_half_widths,
        receiver_half_heights,
        receiver_inverse_widths,
        receiver_inverse_heights,
        receiver_minimum_cosines,
        receiver_columns,
        receiver_rows,
        event_faces,
        event_distances,
        event_points,
        event_normals,
        event_incoming_power,
        event_reflected_power,
        event_emitted_power,
        event_status,
        event_lobes,
        event_incoming_kinds,
        terminal_kind,
        terminal_depth,
        terminal_power,
        terminal_ray_kind,
        terminal_receiver,
        terminal_row,
        terminal_column,
        terminal_received_power,
        terminal_point,
        terminal_normal,
        terminal_distance,
        terminal_incoming_power,
        stochastic_primary,
        active_by_depth,
        stack_entries,
        stack_nodes,
        overflow_flags,
        bounce_importance_counts,
        bounce_importance_weight_stats,
    ):
        ray_index = cuda.grid(1)
        if ray_index >= ray_count:
            return
        overflow_flags[ray_index] = 0
        stochastic_primary[ray_index] = 0
        terminal_kind[ray_index] = 0
        terminal_depth[ray_index] = -1
        terminal_power[ray_index] = 0.0
        terminal_ray_kind[ray_index] = RAY_KIND_DIRECT
        terminal_receiver[ray_index] = -1
        terminal_row[ray_index] = -1
        terminal_column[ray_index] = -1
        terminal_received_power[ray_index] = 0.0
        if record_geometry:
            terminal_point[ray_index, 0] = 0.0
            terminal_point[ray_index, 1] = 0.0
            terminal_point[ray_index, 2] = 0.0
            terminal_normal[ray_index, 0] = 0.0
            terminal_normal[ray_index, 1] = 0.0
            terminal_normal[ray_index, 2] = 0.0
            terminal_distance[ray_index] = 0.0
        terminal_incoming_power[ray_index] = 0.0
        for depth_slot in range(depth_count):
            event_faces[ray_index, depth_slot] = -1
            if record_geometry:
                event_distances[ray_index, depth_slot] = 0.0
                event_points[ray_index, depth_slot, 0] = 0.0
                event_points[ray_index, depth_slot, 1] = 0.0
                event_points[ray_index, depth_slot, 2] = 0.0
                event_normals[ray_index, depth_slot, 0] = 0.0
                event_normals[ray_index, depth_slot, 1] = 0.0
                event_normals[ray_index, depth_slot, 2] = 0.0
            event_incoming_power[ray_index, depth_slot] = 0.0
            event_reflected_power[ray_index, depth_slot] = 0.0
            event_emitted_power[ray_index, depth_slot] = 0.0
            event_status[ray_index, depth_slot] = 0
            event_lobes[ray_index, depth_slot] = LOBE_NONE
            event_incoming_kinds[ray_index, depth_slot] = RAY_KIND_DIRECT

        origin_x = origins[ray_index, 0]
        origin_y = origins[ray_index, 1]
        origin_z = origins[ray_index, 2]
        direction_x = directions[ray_index, 0]
        direction_y = directions[ray_index, 1]
        direction_z = directions[ray_index, 2]
        current_power = powers[ray_index]
        current_source_face = source_faces[ray_index]
        current_ray_kind = RAY_KIND_DIRECT
        key = reflection_seeds[ray_index]

        for depth in range(depth_count):
            cuda.atomic.add(active_by_depth, depth, 1)
            best_receiver_distance = math.inf
            best_receiver = -1
            best_receiver_row = -1
            best_receiver_column = -1
            best_receiver_power = 0.0
            best_receiver_point_x = 0.0
            best_receiver_point_y = 0.0
            best_receiver_point_z = 0.0
            best_receiver_normal_x = 0.0
            best_receiver_normal_y = 0.0
            best_receiver_normal_z = 0.0
            for receiver_index in range(receiver_centers.shape[0]):
                normal_x = receiver_normals[receiver_index, 0]
                normal_y = receiver_normals[receiver_index, 1]
                normal_z = receiver_normals[receiver_index, 2]
                denominator = (
                    direction_x * normal_x
                    + direction_y * normal_y
                    + direction_z * normal_z
                )
                if abs(denominator) < 1e-12:
                    continue
                center_x = receiver_centers[receiver_index, 0]
                center_y = receiver_centers[receiver_index, 1]
                center_z = receiver_centers[receiver_index, 2]
                distance = (
                    (center_x - origin_x) * normal_x
                    + (center_y - origin_y) * normal_y
                    + (center_z - origin_z) * normal_z
                ) / denominator
                if distance <= epsilon_mm or distance >= best_receiver_distance:
                    continue
                point_x = origin_x + direction_x * distance
                point_y = origin_y + direction_y * distance
                point_z = origin_z + direction_z * distance
                local_x = point_x - center_x
                local_y = point_y - center_y
                local_z = point_z - center_z
                local_u = (
                    local_x * receiver_u_axes[receiver_index, 0]
                    + local_y * receiver_u_axes[receiver_index, 1]
                    + local_z * receiver_u_axes[receiver_index, 2]
                )
                local_v = (
                    local_x * receiver_v_axes[receiver_index, 0]
                    + local_y * receiver_v_axes[receiver_index, 1]
                    + local_z * receiver_v_axes[receiver_index, 2]
                )
                acceptance_cosine = -denominator
                if acceptance_cosine < 0.0:
                    acceptance_cosine = 0.0
                if (
                    local_u < -receiver_half_widths[receiver_index]
                    or local_u > receiver_half_widths[receiver_index]
                    or local_v < -receiver_half_heights[receiver_index]
                    or local_v > receiver_half_heights[receiver_index]
                    or acceptance_cosine
                    < receiver_minimum_cosines[receiver_index]
                ):
                    continue
                column = int(
                    (local_u + receiver_half_widths[receiver_index])
                    * receiver_inverse_widths[receiver_index]
                    * receiver_columns[receiver_index]
                )
                row = int(
                    (local_v + receiver_half_heights[receiver_index])
                    * receiver_inverse_heights[receiver_index]
                    * receiver_rows[receiver_index]
                )
                if column < 0:
                    column = 0
                elif column >= receiver_columns[receiver_index]:
                    column = receiver_columns[receiver_index] - 1
                if row < 0:
                    row = 0
                elif row >= receiver_rows[receiver_index]:
                    row = receiver_rows[receiver_index] - 1
                best_receiver_distance = distance
                best_receiver = receiver_index
                best_receiver_row = row
                best_receiver_column = column
                best_receiver_power = current_power * acceptance_cosine
                best_receiver_point_x = point_x
                best_receiver_point_y = point_y
                best_receiver_point_z = point_z
                best_receiver_normal_x = normal_x
                best_receiver_normal_y = normal_y
                best_receiver_normal_z = normal_z

            distance, face_index, overflow = intersect_bvh(
                ray_index,
                stack_width,
                origin_x,
                origin_y,
                origin_z,
                direction_x,
                direction_y,
                direction_z,
                epsilon_mm,
                best_receiver_distance,
                current_source_face,
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
                stack_entries,
                stack_nodes,
            )
            if overflow:
                overflow_flags[ray_index] = 1
                return
            if face_index < 0:
                terminal_depth[ray_index] = depth
                terminal_power[ray_index] = current_power
                terminal_ray_kind[ray_index] = current_ray_kind
                terminal_incoming_power[ray_index] = current_power
                if best_receiver >= 0:
                    terminal_kind[ray_index] = TERMINAL_RECEIVER
                    terminal_receiver[ray_index] = best_receiver
                    terminal_row[ray_index] = best_receiver_row
                    terminal_column[ray_index] = best_receiver_column
                    terminal_received_power[ray_index] = best_receiver_power
                    if record_geometry:
                        terminal_point[ray_index, 0] = best_receiver_point_x
                        terminal_point[ray_index, 1] = best_receiver_point_y
                        terminal_point[ray_index, 2] = best_receiver_point_z
                        terminal_normal[ray_index, 0] = best_receiver_normal_x
                        terminal_normal[ray_index, 1] = best_receiver_normal_y
                        terminal_normal[ray_index, 2] = best_receiver_normal_z
                        terminal_distance[ray_index] = best_receiver_distance
                else:
                    terminal_kind[ray_index] = TERMINAL_ESCAPED
                return

            point_x = origin_x + direction_x * distance
            point_y = origin_y + direction_y * distance
            point_z = origin_z + direction_z * distance
            normal_x, normal_y, normal_z = orient(
                direction_x,
                direction_y,
                direction_z,
                triangle_normals[face_index, 0],
                triangle_normals[face_index, 1],
                triangle_normals[face_index, 2],
            )
            reflected_power = current_power * effective_reflectance(
                direction_x,
                direction_y,
                direction_z,
                normal_x,
                normal_y,
                normal_z,
                face_reflectance[face_index],
                face_roughness[face_index],
            )
            event_faces[ray_index, depth] = face_index
            if record_geometry:
                event_distances[ray_index, depth] = distance
                event_points[ray_index, depth, 0] = point_x
                event_points[ray_index, depth, 1] = point_y
                event_points[ray_index, depth, 2] = point_z
                event_normals[ray_index, depth, 0] = normal_x
                event_normals[ray_index, depth, 1] = normal_y
                event_normals[ray_index, depth, 2] = normal_z
            event_incoming_power[ray_index, depth] = current_power
            event_reflected_power[ray_index, depth] = reflected_power
            event_incoming_kinds[ray_index, depth] = current_ray_kind

            if depth >= max_depth:
                event_status[ray_index, depth] = (
                    STATUS_DEPTH_LIMITED | STATUS_DISABLED
                )
                terminal_kind[ray_index] = TERMINAL_BLOCKED
                terminal_depth[ray_index] = depth
                terminal_power[ray_index] = current_power
                terminal_ray_kind[ray_index] = current_ray_kind
                return

            status = STATUS_ATTEMPTED
            emitted_power = reflected_power
            if min_energy > 0.0 and reflected_power < min_energy:
                if termination_mode == TERMINATION_THRESHOLD:
                    event_status[ray_index, depth] = status | STATUS_BELOW_ENERGY
                    terminal_kind[ray_index] = TERMINAL_BLOCKED
                    terminal_depth[ray_index] = depth
                    terminal_power[ray_index] = current_power
                    terminal_ray_kind[ray_index] = current_ray_kind
                    return
                stochastic_primary[ray_index] = 1
                survival_probability = reflected_power / min_energy
                if survival_probability < 0.0:
                    survival_probability = 0.0
                elif survival_probability > 1.0:
                    survival_probability = 1.0
                if uniform(key, depth, LANE_ROULETTE) >= survival_probability:
                    event_status[ray_index, depth] = (
                        status
                        | STATUS_BELOW_ENERGY
                        | STATUS_ROULETTE_TERMINATED
                    )
                    terminal_kind[ray_index] = TERMINAL_BLOCKED
                    terminal_depth[ray_index] = depth
                    terminal_power[ray_index] = current_power
                    terminal_ray_kind[ray_index] = current_ray_kind
                    return
                status |= STATUS_ROULETTE_SURVIVED
                emitted_power = min_energy

            scatter = face_scatter[face_index]
            if scatter == SCATTER_NONE or face_reflectance[face_index] <= 0.0:
                event_status[ray_index, depth] = status | STATUS_DISABLED
                terminal_kind[ray_index] = TERMINAL_BLOCKED
                terminal_depth[ray_index] = depth
                terminal_power[ray_index] = current_power
                terminal_ray_kind[ray_index] = current_ray_kind
                return
            if bounce_receiver_mis_enabled and (
                scatter == SCATTER_SPECULAR
                or scatter == SCATTER_GAUSSIAN
                or scatter == SCATTER_MIXED
            ):
                cuda.atomic.add(bounce_importance_counts, 3, 1)

            specular_x, specular_y, specular_z = specular(
                direction_x,
                direction_y,
                direction_z,
                normal_x,
                normal_y,
                normal_z,
            )
            next_x = specular_x
            next_y = specular_y
            next_z = specular_z
            lobe = LOBE_SPECULAR
            next_kind = RAY_KIND_SPECULAR
            if scatter == SCATTER_LAMBERTIAN:
                stochastic_primary[ray_index] = 1
                next_x, next_y, next_z = lambertian(
                    key,
                    depth,
                    normal_x,
                    normal_y,
                    normal_z,
                )
                lobe = LOBE_LAMBERTIAN
                next_kind = RAY_KIND_LAMBERTIAN
                if bounce_receiver_mis_enabled:
                    (
                        next_x,
                        next_y,
                        next_z,
                        importance_weight,
                        importance_directed,
                    ) = lambertian_receiver_mis(
                        key,
                        depth,
                        point_x,
                        point_y,
                        point_z,
                        normal_x,
                        normal_y,
                        normal_z,
                        next_x,
                        next_y,
                        next_z,
                        bounce_receiver_importance_fraction,
                        epsilon_mm,
                        receiver_centers,
                        receiver_normals,
                        receiver_u_axes,
                        receiver_v_axes,
                        receiver_half_widths,
                        receiver_half_heights,
                        receiver_minimum_cosines,
                    )
                    cuda.atomic.add(bounce_importance_counts, 0, 1)
                    if importance_directed:
                        cuda.atomic.add(bounce_importance_counts, 1, 1)
                    cuda.atomic.add(
                        bounce_importance_weight_stats,
                        0,
                        importance_weight,
                    )
                    cuda.atomic.add(
                        bounce_importance_weight_stats,
                        1,
                        importance_weight * importance_weight,
                    )
                    cuda.atomic.min(
                        bounce_importance_weight_stats,
                        2,
                        importance_weight,
                    )
                    cuda.atomic.max(
                        bounce_importance_weight_stats,
                        3,
                        importance_weight,
                    )
                    if importance_weight <= 0.0:
                        cuda.atomic.add(bounce_importance_counts, 2, 1)
                        event_status[ray_index, depth] = status
                        terminal_kind[ray_index] = TERMINAL_BLOCKED
                        terminal_depth[ray_index] = depth
                        terminal_power[ray_index] = current_power
                        terminal_ray_kind[ray_index] = current_ray_kind
                        return
                    emitted_power *= importance_weight
            elif scatter == SCATTER_GAUSSIAN:
                stochastic_primary[ray_index] = 1
                next_x, next_y, next_z, _ = gaussian(
                    key,
                    depth,
                    specular_x,
                    specular_y,
                    specular_z,
                    normal_x,
                    normal_y,
                    normal_z,
                    face_gaussian_sigma_deg[face_index],
                )
                lobe = LOBE_GAUSSIAN
                next_kind = RAY_KIND_GAUSSIAN
            elif scatter == SCATTER_MIXED:
                stochastic_primary[ray_index] = 1
                if uniform(key, depth, LANE_MIXED_LOBE) < face_specular_ratio[
                    face_index
                ]:
                    if face_gaussian_sigma_deg[face_index] > 0.01:
                        next_x, next_y, next_z, _ = gaussian(
                            key,
                            depth,
                            specular_x,
                            specular_y,
                            specular_z,
                            normal_x,
                            normal_y,
                            normal_z,
                            face_gaussian_sigma_deg[face_index],
                        )
                        lobe = LOBE_GAUSSIAN
                        next_kind = RAY_KIND_GAUSSIAN
                else:
                    next_x, next_y, next_z = lambertian(
                        key,
                        depth,
                        normal_x,
                        normal_y,
                        normal_z,
                    )
                    lobe = LOBE_LAMBERTIAN
                    next_kind = RAY_KIND_LAMBERTIAN

            event_emitted_power[ray_index, depth] = emitted_power
            event_lobes[ray_index, depth] = lobe
            event_status[ray_index, depth] = status | STATUS_EMITTED
            origin_x = point_x + next_x * epsilon_mm
            origin_y = point_y + next_y * epsilon_mm
            origin_z = point_z + next_z * epsilon_mm
            direction_x = next_x
            direction_y = next_y
            direction_z = next_z
            current_power = emitted_power
            current_source_face = face_index
            current_ray_kind = next_kind

    return resident_kernel


def _ensure_kernel() -> Callable[..., None]:
    global _KERNEL
    if _KERNEL is not None:
        return _KERNEL
    with _STATE_LOCK:
        if _KERNEL is None:
            try:
                _KERNEL = _make_kernel()
            except GpuResidentWavefrontProviderError:
                raise
            except Exception as exc:
                raise GpuResidentWavefrontProviderError(
                    "initialize",
                    "gpu_resident_kernel_create_failed",
                ) from exc
        return _KERNEL


def _launch_resident_kernel(
    kernel: Callable[..., None],
    context: GpuResidentWavefrontContext,
    device_scene: Any,
    bindings: _DeviceBindings,
    workspace: _Workspace,
    batch: GpuResidentWavefrontBatch,
    ray_count: int,
    depth_count: int,
    *,
    record_geometry: bool,
) -> None:
    cuda = cuda_backend._CUDA
    if cuda is None:
        raise GpuResidentWavefrontProviderError(
            "initialize",
            "gpu_resident_cuda_runtime_not_initialized",
        )
    block_count = (ray_count + THREADS_PER_BLOCK - 1) // THREADS_PER_BLOCK
    try:
        performance_warning = importlib.import_module(
            "numba.core.errors"
        ).NumbaPerformanceWarning
    except Exception:
        performance_warning = Warning
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", performance_warning)
        workspace.bounce_importance_counts.copy_to_device(
            np.zeros(4, dtype=np.int64)
        )
        workspace.bounce_importance_weight_stats.copy_to_device(
            np.asarray([0.0, 0.0, math.inf, 0.0], dtype=np.float64)
        )
        kernel[block_count, THREADS_PER_BLOCK](
            ray_count,
            depth_count,
            batch.max_depth,
            context.scene.stack_width,
            batch.epsilon_mm,
            batch.min_energy,
            batch.termination_mode,
            int(batch.bounce_receiver_mis_enabled),
            batch.bounce_receiver_importance_fraction,
            int(record_geometry),
            workspace.origins,
            workspace.directions,
            workspace.powers,
            workspace.source_faces,
            workspace.reflection_seeds,
            device_scene.triangle_v0,
            device_scene.triangle_edge1,
            device_scene.triangle_edge2,
            bindings.triangle_normals,
            device_scene.node_bounds_min,
            device_scene.node_bounds_max,
            device_scene.node_left,
            device_scene.node_right,
            device_scene.node_start,
            device_scene.node_count,
            device_scene.ordered_faces,
            device_scene.traceable_face_mask,
            bindings.face_reflectance,
            bindings.face_roughness,
            bindings.face_scatter,
            bindings.face_specular_ratio,
            bindings.face_gaussian_sigma_deg,
            bindings.receiver_centers,
            bindings.receiver_normals,
            bindings.receiver_u_axes,
            bindings.receiver_v_axes,
            bindings.receiver_half_widths,
            bindings.receiver_half_heights,
            bindings.receiver_inverse_widths,
            bindings.receiver_inverse_heights,
            bindings.receiver_minimum_cosines,
            bindings.receiver_columns,
            bindings.receiver_rows,
            workspace.event_faces,
            workspace.event_distances,
            workspace.event_points,
            workspace.event_normals,
            workspace.event_incoming_power,
            workspace.event_reflected_power,
            workspace.event_emitted_power,
            workspace.event_status,
            workspace.event_lobes,
            workspace.event_incoming_kinds,
            workspace.terminal_kind,
            workspace.terminal_depth,
            workspace.terminal_power,
            workspace.terminal_ray_kind,
            workspace.terminal_receiver,
            workspace.terminal_row,
            workspace.terminal_column,
            workspace.terminal_received_power,
            workspace.terminal_point,
            workspace.terminal_normal,
            workspace.terminal_distance,
            workspace.terminal_incoming_power,
            workspace.stochastic_primary,
            workspace.active_by_depth,
            workspace.stack_entries,
            workspace.stack_nodes,
            workspace.overflow_flags,
            workspace.bounce_importance_counts,
            workspace.bounce_importance_weight_stats,
        )
    cuda.synchronize()


def _copy_host(device_array: Any, slice_value: Any, dtype: Any) -> np.ndarray:
    return np.ascontiguousarray(
        device_array[slice_value].copy_to_host(),
        dtype=dtype,
    )


def _make_path_kernels() -> tuple[Callable[..., None], Callable[..., None]]:
    cuda = cuda_backend._CUDA
    if cuda is None:
        raise GpuResidentWavefrontProviderError(
            "initialize",
            "gpu_resident_cuda_runtime_not_initialized",
        )

    @cuda.jit
    def select_paths(
        ray_count,
        existing_path_count,
        existing_dead_end_count,
        max_paths,
        selection_capacity,
        terminal_kind,
        selected_slots,
        selected_count,
    ):
        if cuda.grid(1) != 0:
            return
        path_count = existing_path_count
        dead_end_count = existing_dead_end_count
        output_count = 0
        for primary_slot in range(ray_count):
            if output_count >= selection_capacity:
                break
            terminal_code = terminal_kind[primary_slot]
            if path_count < max_paths:
                selected_slots[output_count] = primary_slot
                output_count += 1
                path_count += 1
                if terminal_code != TERMINAL_RECEIVER:
                    dead_end_count += 1
            elif terminal_code == TERMINAL_RECEIVER and dead_end_count > 0:
                selected_slots[output_count] = primary_slot
                output_count += 1
                dead_end_count -= 1
        selected_count[0] = output_count

    @cuda.jit
    def gather_paths(
        selected_count,
        depth_count,
        selected_slots,
        event_faces,
        event_distances,
        event_points,
        event_normals,
        event_incoming_power,
        event_reflected_power,
        event_emitted_power,
        event_status,
        event_lobes,
        event_incoming_kinds,
        terminal_kind,
        terminal_depth,
        terminal_power,
        terminal_ray_kind,
        terminal_receiver,
        terminal_row,
        terminal_column,
        terminal_received_power,
        terminal_point,
        terminal_normal,
        terminal_distance,
        terminal_incoming_power,
        output_event_faces,
        output_event_distances,
        output_event_points,
        output_event_normals,
        output_event_incoming_power,
        output_event_reflected_power,
        output_event_emitted_power,
        output_event_status,
        output_event_lobes,
        output_event_incoming_kinds,
        output_terminal_kind,
        output_terminal_depth,
        output_terminal_power,
        output_terminal_ray_kind,
        output_terminal_receiver,
        output_terminal_row,
        output_terminal_column,
        output_terminal_received_power,
        output_terminal_point,
        output_terminal_normal,
        output_terminal_distance,
        output_terminal_incoming_power,
    ):
        output_slot = cuda.grid(1)
        if output_slot >= selected_count:
            return
        primary_slot = selected_slots[output_slot]
        for depth in range(depth_count):
            output_event_faces[output_slot, depth] = event_faces[
                primary_slot,
                depth,
            ]
            output_event_distances[output_slot, depth] = event_distances[
                primary_slot,
                depth,
            ]
            output_event_incoming_power[output_slot, depth] = (
                event_incoming_power[primary_slot, depth]
            )
            output_event_reflected_power[output_slot, depth] = (
                event_reflected_power[primary_slot, depth]
            )
            output_event_emitted_power[output_slot, depth] = (
                event_emitted_power[primary_slot, depth]
            )
            output_event_status[output_slot, depth] = event_status[
                primary_slot,
                depth,
            ]
            output_event_lobes[output_slot, depth] = event_lobes[
                primary_slot,
                depth,
            ]
            output_event_incoming_kinds[output_slot, depth] = (
                event_incoming_kinds[primary_slot, depth]
            )
            for coordinate in range(3):
                output_event_points[output_slot, depth, coordinate] = (
                    event_points[primary_slot, depth, coordinate]
                )
                output_event_normals[output_slot, depth, coordinate] = (
                    event_normals[primary_slot, depth, coordinate]
                )
        output_terminal_kind[output_slot] = terminal_kind[primary_slot]
        output_terminal_depth[output_slot] = terminal_depth[primary_slot]
        output_terminal_power[output_slot] = terminal_power[primary_slot]
        output_terminal_ray_kind[output_slot] = terminal_ray_kind[primary_slot]
        output_terminal_receiver[output_slot] = terminal_receiver[primary_slot]
        output_terminal_row[output_slot] = terminal_row[primary_slot]
        output_terminal_column[output_slot] = terminal_column[primary_slot]
        output_terminal_received_power[output_slot] = terminal_received_power[
            primary_slot
        ]
        output_terminal_distance[output_slot] = terminal_distance[primary_slot]
        output_terminal_incoming_power[output_slot] = terminal_incoming_power[
            primary_slot
        ]
        for coordinate in range(3):
            output_terminal_point[output_slot, coordinate] = terminal_point[
                primary_slot,
                coordinate,
            ]
            output_terminal_normal[output_slot, coordinate] = terminal_normal[
                primary_slot,
                coordinate,
            ]

    return select_paths, gather_paths


def _ensure_path_kernels() -> tuple[Callable[..., None], Callable[..., None]]:
    global _PATH_SELECT_KERNEL, _PATH_GATHER_KERNEL
    if _PATH_SELECT_KERNEL is not None and _PATH_GATHER_KERNEL is not None:
        return _PATH_SELECT_KERNEL, _PATH_GATHER_KERNEL
    with _STATE_LOCK:
        if _PATH_SELECT_KERNEL is None or _PATH_GATHER_KERNEL is None:
            try:
                _PATH_SELECT_KERNEL, _PATH_GATHER_KERNEL = _make_path_kernels()
            except GpuResidentWavefrontProviderError:
                raise
            except Exception as exc:
                raise GpuResidentWavefrontProviderError(
                    "initialize",
                    "gpu_resident_path_kernel_create_failed",
                ) from exc
    return _PATH_SELECT_KERNEL, _PATH_GATHER_KERNEL


def _path_candidate_capacity(
    selection: GpuResidentPathSelection,
    ray_count: int,
) -> int:
    return min(
        ray_count,
        max(
            0,
            selection.max_paths
            - selection.existing_path_count
            + selection.existing_dead_end_count,
        ),
    )


def _download_workspace_path_tape(
    workspace: _Workspace,
    batch: GpuResidentWavefrontBatch,
    ray_count: int,
    depth_count: int,
) -> PrimaryMajorEventTape:
    row_slice = (slice(0, ray_count), slice(0, depth_count))
    vector_slice = (
        slice(0, ray_count),
        slice(0, depth_count),
        slice(None),
    )
    compact = {
        "event_faces": _copy_host(workspace.event_faces, row_slice, np.int32),
        "event_distances": _copy_host(
            workspace.event_distances,
            row_slice,
            np.float64,
        ),
        "event_points": _copy_host(
            workspace.event_points,
            vector_slice,
            np.float64,
        ),
        "event_normals": _copy_host(
            workspace.event_normals,
            vector_slice,
            np.float64,
        ),
        "event_incoming_power": _copy_host(
            workspace.event_incoming_power,
            row_slice,
            np.float64,
        ),
        "event_reflected_power": _copy_host(
            workspace.event_reflected_power,
            row_slice,
            np.float64,
        ),
        "event_emitted_power": _copy_host(
            workspace.event_emitted_power,
            row_slice,
            np.float64,
        ),
        "event_status": _copy_host(
            workspace.event_status,
            row_slice,
            np.uint16,
        ),
        "event_lobes": _copy_host(
            workspace.event_lobes,
            row_slice,
            np.int8,
        ),
        "event_incoming_kinds": _copy_host(
            workspace.event_incoming_kinds,
            row_slice,
            np.int8,
        ),
        "terminal_kind": _copy_host(
            workspace.terminal_kind,
            slice(0, ray_count),
            np.int8,
        ),
        "terminal_depth": _copy_host(
            workspace.terminal_depth,
            slice(0, ray_count),
            np.int16,
        ),
        "terminal_power": _copy_host(
            workspace.terminal_power,
            slice(0, ray_count),
            np.float64,
        ),
        "terminal_ray_kind": _copy_host(
            workspace.terminal_ray_kind,
            slice(0, ray_count),
            np.int8,
        ),
        "terminal_receiver": _copy_host(
            workspace.terminal_receiver,
            slice(0, ray_count),
            np.int32,
        ),
        "terminal_row": _copy_host(
            workspace.terminal_row,
            slice(0, ray_count),
            np.int32,
        ),
        "terminal_column": _copy_host(
            workspace.terminal_column,
            slice(0, ray_count),
            np.int32,
        ),
        "terminal_received_power": _copy_host(
            workspace.terminal_received_power,
            slice(0, ray_count),
            np.float64,
        ),
        "terminal_point": _copy_host(
            workspace.terminal_point,
            (slice(0, ray_count), slice(None)),
            np.float64,
        ),
        "terminal_normal": _copy_host(
            workspace.terminal_normal,
            (slice(0, ray_count), slice(None)),
            np.float64,
        ),
        "terminal_distance": _copy_host(
            workspace.terminal_distance,
            slice(0, ray_count),
            np.float64,
        ),
        "terminal_incoming_power": _copy_host(
            workspace.terminal_incoming_power,
            slice(0, ray_count),
            np.float64,
        ),
    }
    return _build_tape(batch, **compact)


def _retrace_selected_path_tape(
    context: GpuResidentWavefrontContext,
    device_scene: Any,
    bindings: _DeviceBindings,
    workspace: _Workspace,
    kernel: Callable[..., None],
    batch: GpuResidentWavefrontBatch,
    selected_host: np.ndarray,
    depth_count: int,
) -> tuple[PrimaryMajorEventTape, float, float]:
    selected_batch = GpuResidentWavefrontBatch(
        origins=batch.origins[selected_host],
        directions=batch.directions[selected_host],
        initial_power_lumen=batch.initial_power_lumen[selected_host],
        source_faces=batch.source_faces[selected_host],
        reflection_seeds=batch.reflection_seeds[selected_host],
        max_depth=batch.max_depth,
        epsilon_mm=batch.epsilon_mm,
        min_energy=batch.min_energy,
        termination_mode=batch.termination_mode,
        include_path_payload=True,
        bounce_receiver_mis_enabled=batch.bounce_receiver_mis_enabled,
        bounce_receiver_importance_fraction=(
            batch.bounce_receiver_importance_fraction
        ),
    )
    selected_count = len(selected_batch)
    if selected_count > workspace.geometry_capacity:
        raise GpuResidentWavefrontProviderError(
            "input_prepare",
            "gpu_resident_sparse_path_workspace_too_small",
        )
    cuda = cuda_backend._CUDA
    if cuda is None:
        raise GpuResidentWavefrontProviderError(
            "initialize",
            "gpu_resident_cuda_runtime_not_initialized",
        )
    retrace_started = time.perf_counter()
    try:
        workspace.origins[:selected_count].copy_to_device(selected_batch.origins)
        workspace.directions[:selected_count].copy_to_device(
            selected_batch.directions
        )
        workspace.powers[:selected_count].copy_to_device(
            selected_batch.initial_power_lumen
        )
        workspace.source_faces[:selected_count].copy_to_device(
            selected_batch.source_faces
        )
        workspace.reflection_seeds[:selected_count].copy_to_device(
            selected_batch.reflection_seeds
        )
        workspace.active_by_depth[:depth_count].copy_to_device(
            np.zeros(depth_count, dtype=np.int64)
        )
        _launch_resident_kernel(
            kernel,
            context,
            device_scene,
            bindings,
            workspace,
            selected_batch,
            selected_count,
            depth_count,
            record_geometry=True,
        )
        overflow = _copy_host(
            workspace.overflow_flags,
            slice(0, selected_count),
            np.uint8,
        )
    except GpuResidentWavefrontProviderError:
        raise
    except Exception as exc:
        raise GpuResidentWavefrontProviderError(
            "execute",
            "gpu_resident_sparse_path_retrace_failed",
        ) from exc
    retrace_sec = time.perf_counter() - retrace_started
    if np.any(overflow):
        raise GpuResidentWavefrontProviderError(
            "result_validation",
            "gpu_resident_sparse_path_bvh_stack_overflow",
        )
    download_started = time.perf_counter()
    try:
        tape = _download_workspace_path_tape(
            workspace,
            selected_batch,
            selected_count,
            depth_count,
        )
    except Exception as exc:
        raise GpuResidentWavefrontProviderError(
            "result_validation",
            "gpu_resident_sparse_path_tape_invalid",
        ) from exc
    return tape, retrace_sec, time.perf_counter() - download_started


def _selected_path_tape(
    workspace: _Workspace,
    batch: GpuResidentWavefrontBatch,
    selection: GpuResidentPathSelection,
    ray_count: int,
    depth_count: int,
    *,
    compact_retrace: bool,
    context: GpuResidentWavefrontContext,
    device_scene: Any,
    bindings: _DeviceBindings,
    kernel: Callable[..., None],
) -> tuple[Optional[PrimaryMajorEventTape], int, int, float, float, float]:
    candidate_capacity = _path_candidate_capacity(selection, ray_count)
    if candidate_capacity == 0:
        return None, 0, ray_count, 0.0, 0.0, 0.0
    cuda = cuda_backend._CUDA
    if cuda is None:
        raise GpuResidentWavefrontProviderError(
            "initialize",
            "gpu_resident_cuda_runtime_not_initialized",
        )
    select_kernel, gather_kernel = _ensure_path_kernels()
    select_started = time.perf_counter()
    try:
        try:
            performance_warning = importlib.import_module(
                "numba.core.errors"
            ).NumbaPerformanceWarning
        except Exception:
            performance_warning = Warning
        selected_slots = cuda.device_array(candidate_capacity, dtype=np.int64)
        selected_count_device = cuda.to_device(np.zeros(1, dtype=np.int64))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", performance_warning)
            select_kernel[1, 1](
                ray_count,
                selection.existing_path_count,
                selection.existing_dead_end_count,
                selection.max_paths,
                candidate_capacity,
                workspace.terminal_kind,
                selected_slots,
                selected_count_device,
            )
        cuda.synchronize()
        selected_count = int(selected_count_device.copy_to_host()[0])
        selected_host = np.ascontiguousarray(
            selected_slots[:selected_count].copy_to_host(),
            dtype=np.int64,
        )
    except Exception as exc:
        raise GpuResidentWavefrontProviderError(
            "execute",
            "gpu_resident_path_selection_failed",
        ) from exc
    select_sec = time.perf_counter() - select_started
    if selected_count == 0:
        return None, 0, ray_count, select_sec, 0.0, 0.0

    if compact_retrace:
        tape, retrace_sec, download_sec = _retrace_selected_path_tape(
            context,
            device_scene,
            bindings,
            workspace,
            kernel,
            batch,
            selected_host,
            depth_count,
        )
        return (
            tape,
            selected_count,
            ray_count - selected_count,
            select_sec,
            retrace_sec,
            download_sec,
        )

    download_started = time.perf_counter()
    event_shape = (selected_count, depth_count)
    vector_shape = (selected_count, depth_count, 3)
    try:
        output_event_faces = cuda.device_array(event_shape, dtype=np.int32)
        output_event_distances = cuda.device_array(event_shape, dtype=np.float64)
        output_event_points = cuda.device_array(vector_shape, dtype=np.float64)
        output_event_normals = cuda.device_array(vector_shape, dtype=np.float64)
        output_event_incoming_power = cuda.device_array(
            event_shape,
            dtype=np.float64,
        )
        output_event_reflected_power = cuda.device_array(
            event_shape,
            dtype=np.float64,
        )
        output_event_emitted_power = cuda.device_array(
            event_shape,
            dtype=np.float64,
        )
        output_event_status = cuda.device_array(event_shape, dtype=np.uint16)
        output_event_lobes = cuda.device_array(event_shape, dtype=np.int8)
        output_event_incoming_kinds = cuda.device_array(
            event_shape,
            dtype=np.int8,
        )
        output_terminal_kind = cuda.device_array(selected_count, dtype=np.int8)
        output_terminal_depth = cuda.device_array(selected_count, dtype=np.int16)
        output_terminal_power = cuda.device_array(
            selected_count,
            dtype=np.float64,
        )
        output_terminal_ray_kind = cuda.device_array(
            selected_count,
            dtype=np.int8,
        )
        output_terminal_receiver = cuda.device_array(
            selected_count,
            dtype=np.int32,
        )
        output_terminal_row = cuda.device_array(selected_count, dtype=np.int32)
        output_terminal_column = cuda.device_array(
            selected_count,
            dtype=np.int32,
        )
        output_terminal_received_power = cuda.device_array(
            selected_count,
            dtype=np.float64,
        )
        output_terminal_point = cuda.device_array(
            (selected_count, 3),
            dtype=np.float64,
        )
        output_terminal_normal = cuda.device_array(
            (selected_count, 3),
            dtype=np.float64,
        )
        output_terminal_distance = cuda.device_array(
            selected_count,
            dtype=np.float64,
        )
        output_terminal_incoming_power = cuda.device_array(
            selected_count,
            dtype=np.float64,
        )
        block_count = (
            selected_count + THREADS_PER_BLOCK - 1
        ) // THREADS_PER_BLOCK
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", performance_warning)
            gather_kernel[block_count, THREADS_PER_BLOCK](
                selected_count,
                depth_count,
                selected_slots,
                workspace.event_faces,
                workspace.event_distances,
                workspace.event_points,
                workspace.event_normals,
                workspace.event_incoming_power,
                workspace.event_reflected_power,
                workspace.event_emitted_power,
                workspace.event_status,
                workspace.event_lobes,
                workspace.event_incoming_kinds,
                workspace.terminal_kind,
                workspace.terminal_depth,
                workspace.terminal_power,
                workspace.terminal_ray_kind,
                workspace.terminal_receiver,
                workspace.terminal_row,
                workspace.terminal_column,
                workspace.terminal_received_power,
                workspace.terminal_point,
                workspace.terminal_normal,
                workspace.terminal_distance,
                workspace.terminal_incoming_power,
                output_event_faces,
                output_event_distances,
                output_event_points,
                output_event_normals,
                output_event_incoming_power,
                output_event_reflected_power,
                output_event_emitted_power,
                output_event_status,
                output_event_lobes,
                output_event_incoming_kinds,
                output_terminal_kind,
                output_terminal_depth,
                output_terminal_power,
                output_terminal_ray_kind,
                output_terminal_receiver,
                output_terminal_row,
                output_terminal_column,
                output_terminal_received_power,
                output_terminal_point,
                output_terminal_normal,
                output_terminal_distance,
                output_terminal_incoming_power,
            )
        cuda.synchronize()
        compact = {
            "event_faces": np.ascontiguousarray(
                output_event_faces.copy_to_host(),
                dtype=np.int32,
            ),
            "event_distances": np.ascontiguousarray(
                output_event_distances.copy_to_host(),
                dtype=np.float64,
            ),
            "event_points": np.ascontiguousarray(
                output_event_points.copy_to_host(),
                dtype=np.float64,
            ),
            "event_normals": np.ascontiguousarray(
                output_event_normals.copy_to_host(),
                dtype=np.float64,
            ),
            "event_incoming_power": np.ascontiguousarray(
                output_event_incoming_power.copy_to_host(),
                dtype=np.float64,
            ),
            "event_reflected_power": np.ascontiguousarray(
                output_event_reflected_power.copy_to_host(),
                dtype=np.float64,
            ),
            "event_emitted_power": np.ascontiguousarray(
                output_event_emitted_power.copy_to_host(),
                dtype=np.float64,
            ),
            "event_status": np.ascontiguousarray(
                output_event_status.copy_to_host(),
                dtype=np.uint16,
            ),
            "event_lobes": np.ascontiguousarray(
                output_event_lobes.copy_to_host(),
                dtype=np.int8,
            ),
            "event_incoming_kinds": np.ascontiguousarray(
                output_event_incoming_kinds.copy_to_host(),
                dtype=np.int8,
            ),
            "terminal_kind": np.ascontiguousarray(
                output_terminal_kind.copy_to_host(),
                dtype=np.int8,
            ),
            "terminal_depth": np.ascontiguousarray(
                output_terminal_depth.copy_to_host(),
                dtype=np.int16,
            ),
            "terminal_power": np.ascontiguousarray(
                output_terminal_power.copy_to_host(),
                dtype=np.float64,
            ),
            "terminal_ray_kind": np.ascontiguousarray(
                output_terminal_ray_kind.copy_to_host(),
                dtype=np.int8,
            ),
            "terminal_receiver": np.ascontiguousarray(
                output_terminal_receiver.copy_to_host(),
                dtype=np.int32,
            ),
            "terminal_row": np.ascontiguousarray(
                output_terminal_row.copy_to_host(),
                dtype=np.int32,
            ),
            "terminal_column": np.ascontiguousarray(
                output_terminal_column.copy_to_host(),
                dtype=np.int32,
            ),
            "terminal_received_power": np.ascontiguousarray(
                output_terminal_received_power.copy_to_host(),
                dtype=np.float64,
            ),
            "terminal_point": np.ascontiguousarray(
                output_terminal_point.copy_to_host(),
                dtype=np.float64,
            ),
            "terminal_normal": np.ascontiguousarray(
                output_terminal_normal.copy_to_host(),
                dtype=np.float64,
            ),
            "terminal_distance": np.ascontiguousarray(
                output_terminal_distance.copy_to_host(),
                dtype=np.float64,
            ),
            "terminal_incoming_power": np.ascontiguousarray(
                output_terminal_incoming_power.copy_to_host(),
                dtype=np.float64,
            ),
        }
    except Exception as exc:
        raise GpuResidentWavefrontProviderError(
            "result_validation",
            "gpu_resident_selected_path_download_failed",
        ) from exc
    selected_batch = GpuResidentWavefrontBatch(
        origins=batch.origins[selected_host],
        directions=batch.directions[selected_host],
        initial_power_lumen=batch.initial_power_lumen[selected_host],
        source_faces=batch.source_faces[selected_host],
        reflection_seeds=batch.reflection_seeds[selected_host],
        max_depth=batch.max_depth,
        epsilon_mm=batch.epsilon_mm,
        min_energy=batch.min_energy,
        termination_mode=batch.termination_mode,
        include_path_payload=True,
        bounce_receiver_mis_enabled=batch.bounce_receiver_mis_enabled,
        bounce_receiver_importance_fraction=(
            batch.bounce_receiver_importance_fraction
        ),
    )
    try:
        tape = _build_tape(selected_batch, **compact)
    except Exception as exc:
        raise GpuResidentWavefrontProviderError(
            "result_validation",
            "gpu_resident_selected_path_tape_invalid",
        ) from exc
    return (
        tape,
        selected_count,
        ray_count - selected_count,
        select_sec,
        0.0,
        time.perf_counter() - download_started,
    )


def _build_tape(
    batch: GpuResidentWavefrontBatch,
    *,
    event_faces: np.ndarray,
    event_distances: np.ndarray,
    event_points: np.ndarray,
    event_normals: np.ndarray,
    event_incoming_power: np.ndarray,
    event_reflected_power: np.ndarray,
    event_emitted_power: np.ndarray,
    event_status: np.ndarray,
    event_lobes: np.ndarray,
    event_incoming_kinds: np.ndarray,
    terminal_kind: np.ndarray,
    terminal_depth: np.ndarray,
    terminal_power: np.ndarray,
    terminal_ray_kind: np.ndarray,
    terminal_receiver: np.ndarray,
    terminal_row: np.ndarray,
    terminal_column: np.ndarray,
    terminal_received_power: np.ndarray,
    terminal_point: np.ndarray,
    terminal_normal: np.ndarray,
    terminal_distance: np.ndarray,
    terminal_incoming_power: np.ndarray,
) -> PrimaryMajorEventTape:
    builder = PrimaryMajorEventTapeBuilder(
        batch.origins if batch.include_path_payload else None,
        batch.directions if batch.include_path_payload else None,
        batch.initial_power_lumen,
        batch.reflection_seeds,
        batch.max_depth,
        include_path_payload=batch.include_path_payload,
        initial_source_faces=batch.source_faces,
    )
    for depth in range(batch.max_depth + 1):
        slots = np.flatnonzero(event_faces[:, depth] >= 0).astype(np.int64)
        if not len(slots):
            continue
        builder.append_surface_events(
            depth=depth,
            primary_slots=slots,
            face_indices=event_faces[slots, depth].astype(np.int64),
            points=(event_points[slots, depth] if batch.include_path_payload else None),
            normals=(
                event_normals[slots, depth]
                if batch.include_path_payload
                else None
            ),
            distances_mm=(
                event_distances[slots, depth]
                if batch.include_path_payload
                else None
            ),
            incoming_power_lumen=event_incoming_power[slots, depth],
            reflected_power_lumen=event_reflected_power[slots, depth],
            emitted_power_lumen=event_emitted_power[slots, depth],
            status_flags=event_status[slots, depth],
            lobe_codes=event_lobes[slots, depth],
            incoming_ray_kind_codes=event_incoming_kinds[slots, depth],
        )

    for kind in (TERMINAL_ESCAPED, TERMINAL_BLOCKED):
        slots = np.flatnonzero(terminal_kind == kind).astype(np.int64)
        for depth in np.unique(terminal_depth[slots]) if len(slots) else ():
            depth_slots = slots[terminal_depth[slots] == depth]
            builder.set_nonreceiver_terminals(
                primary_slots=depth_slots,
                terminal_kind=kind,
                depth=int(depth),
                current_power_lumen=terminal_power[depth_slots],
                ray_kind_codes=terminal_ray_kind[depth_slots],
            )

    receiver_slots = np.flatnonzero(
        terminal_kind == TERMINAL_RECEIVER
    ).astype(np.int64)
    for depth in np.unique(terminal_depth[receiver_slots]) if len(receiver_slots) else ():
        depth_slots = receiver_slots[terminal_depth[receiver_slots] == depth]
        builder.set_receiver_terminals(
            primary_slots=depth_slots,
            depth=int(depth),
            current_power_lumen=terminal_power[depth_slots],
            ray_kind_codes=terminal_ray_kind[depth_slots],
            receiver_indices=terminal_receiver[depth_slots],
            rows=terminal_row[depth_slots],
            columns=terminal_column[depth_slots],
            received_power_lumen=terminal_received_power[depth_slots],
            points=(terminal_point[depth_slots] if batch.include_path_payload else None),
            normals=(
                terminal_normal[depth_slots]
                if batch.include_path_payload
                else None
            ),
            distances_mm=(
                terminal_distance[depth_slots]
                if batch.include_path_payload
                else None
            ),
            incoming_power_lumen=terminal_incoming_power[depth_slots],
        )
    return builder.seal()


def trace_resident_wavefront_gpu_cuda(
    context: GpuResidentWavefrontContext,
    batch: GpuResidentWavefrontBatch,
    *,
    summary_request: Optional[GpuSummaryAccumulatorRequest] = None,
    path_selection: Optional[GpuResidentPathSelection] = None,
    compact_summary_workspace: bool = True,
) -> GpuResidentWavefrontExecution:
    if not isinstance(context, GpuResidentWavefrontContext):
        raise TypeError("context must be a GpuResidentWavefrontContext")
    if not isinstance(batch, GpuResidentWavefrontBatch):
        raise TypeError("batch must be a GpuResidentWavefrontBatch")
    capability = cuda_backend.probe_gpu_cuda()
    if not capability.available:
        raise GpuResidentWavefrontUnavailable(
            capability.reason_code or "gpu_cuda_unavailable"
        )
    if capability.strict_float64 is not True:
        raise GpuResidentWavefrontUnavailable("gpu_resident_requires_float64")
    ray_count = len(batch)
    if ray_count == 0:
        raise ValueError("resident wavefront batch must not be empty")
    if np.any(batch.source_faces >= len(context.scene.triangle_v0)):
        raise ValueError("source_faces contains an out-of-range face")
    if batch.bounce_receiver_mis_enabled and len(context.receiver_centers) == 0:
        raise ValueError("bounce Receiver MIS requires an enabled receiver")
    if summary_request is None and path_selection is not None:
        raise ValueError("path_selection requires summary_request")
    if summary_request is not None and batch.include_path_payload:
        raise ValueError(
            "summary accumulation uses selected path payload, not full payload"
        )
    compact_summary_workspace = bool(
        summary_request is not None and compact_summary_workspace
    )

    total_started = time.perf_counter()
    try:
        device_scene, reused_device_scene = cuda_backend._ensure_device_scene(
            context.scene,
            capability,
        )
    except cuda_backend.GpuCudaProviderError as exc:
        raise GpuResidentWavefrontProviderError(exc.phase, exc.reason_code) from exc
    bindings, reused_bindings = _ensure_device_bindings(context, capability)
    depth_count = batch.max_depth + 1
    geometry_count = ray_count
    if compact_summary_workspace:
        geometry_count = (
            _path_candidate_capacity(path_selection, ray_count)
            if path_selection is not None
            else 1
        )
    workspace, reused_workspace, workspace_prepare_sec = _ensure_workspace(
        context,
        capability,
        ray_count,
        depth_count,
        geometry_count,
        (
            COMPACT_WORKSPACE_CONTRACT
            if compact_summary_workspace
            else FULL_WORKSPACE_CONTRACT
        ),
    )
    cuda = cuda_backend._CUDA
    if cuda is None:
        raise GpuResidentWavefrontProviderError(
            "initialize",
            "gpu_resident_cuda_runtime_not_initialized",
        )

    upload_started = time.perf_counter()
    try:
        workspace.origins[:ray_count].copy_to_device(batch.origins)
        workspace.directions[:ray_count].copy_to_device(batch.directions)
        workspace.powers[:ray_count].copy_to_device(batch.initial_power_lumen)
        workspace.source_faces[:ray_count].copy_to_device(batch.source_faces)
        workspace.reflection_seeds[:ray_count].copy_to_device(
            batch.reflection_seeds
        )
        workspace.active_by_depth[:depth_count].copy_to_device(
            np.zeros(depth_count, dtype=np.int64)
        )
        cuda.synchronize()
    except Exception as exc:
        raise GpuResidentWavefrontProviderError(
            "input_prepare",
            "gpu_resident_input_upload_failed",
        ) from exc
    input_upload_sec = time.perf_counter() - upload_started

    kernel = _ensure_kernel()
    global _KERNEL_COMPILED
    was_compiled = _KERNEL_COMPILED
    kernel_started = time.perf_counter()
    try:
        _launch_resident_kernel(
            kernel,
            context,
            device_scene,
            bindings,
            workspace,
            batch,
            ray_count,
            depth_count,
            record_geometry=not compact_summary_workspace,
        )
    except Exception as exc:
        raise GpuResidentWavefrontProviderError(
            "execute",
            "gpu_resident_kernel_failed",
        ) from exc
    kernel_elapsed = time.perf_counter() - kernel_started
    _KERNEL_COMPILED = True

    importance_download_started = time.perf_counter()
    try:
        bounce_importance_counts = _copy_host(
            workspace.bounce_importance_counts,
            slice(0, 4),
            np.int64,
        )
        bounce_importance_weight_stats = _copy_host(
            workspace.bounce_importance_weight_stats,
            slice(0, 4),
            np.float64,
        )
    except Exception as exc:
        raise GpuResidentWavefrontProviderError(
            "result_validation",
            "gpu_resident_bounce_importance_download_failed",
        ) from exc
    importance_download_sec = time.perf_counter() - importance_download_started
    bounce_eligible_count = int(bounce_importance_counts[0])
    bounce_directed_count = int(bounce_importance_counts[1])
    bounce_zero_weight_count = int(bounce_importance_counts[2])
    bounce_unsupported_count = int(bounce_importance_counts[3])
    if (
        bounce_eligible_count < 0
        or bounce_directed_count < 0
        or bounce_directed_count > bounce_eligible_count
        or bounce_zero_weight_count < 0
        or bounce_zero_weight_count > bounce_eligible_count
        or bounce_unsupported_count < 0
    ):
        raise GpuResidentWavefrontProviderError(
            "result_validation",
            "gpu_resident_bounce_importance_counts_invalid",
        )
    bounce_weight_sum = float(bounce_importance_weight_stats[0])
    bounce_weight_square_sum = float(bounce_importance_weight_stats[1])
    bounce_weight_min = (
        float(bounce_importance_weight_stats[2])
        if bounce_eligible_count
        else 1.0
    )
    bounce_weight_max = (
        float(bounce_importance_weight_stats[3])
        if bounce_eligible_count
        else 1.0
    )
    maximum_weight = 1.0 / (1.0 - batch.bounce_receiver_importance_fraction)
    if (
        not all(
            math.isfinite(value)
            for value in (
                bounce_weight_sum,
                bounce_weight_square_sum,
                bounce_weight_min,
                bounce_weight_max,
            )
        )
        or bounce_weight_sum < 0.0
        or bounce_weight_square_sum < 0.0
        or bounce_weight_min < 0.0
        or bounce_weight_max > maximum_weight + 1e-9
    ):
        raise GpuResidentWavefrontProviderError(
            "result_validation",
            "gpu_resident_bounce_importance_weights_invalid",
        )

    if summary_request is not None:
        try:
            summary_execution = accumulate_resident_summary_gpu_cuda(
                GpuSummaryDeviceEvents(
                    ray_count=ray_count,
                    depth_count=depth_count,
                    event_faces=workspace.event_faces,
                    event_incoming_power=workspace.event_incoming_power,
                    event_reflected_power=workspace.event_reflected_power,
                    event_emitted_power=workspace.event_emitted_power,
                    event_status=workspace.event_status,
                    event_lobes=workspace.event_lobes,
                    terminal_kind=workspace.terminal_kind,
                    terminal_depth=workspace.terminal_depth,
                    terminal_power=workspace.terminal_power,
                    terminal_receiver=workspace.terminal_receiver,
                    terminal_row=workspace.terminal_row,
                    terminal_column=workspace.terminal_column,
                    terminal_received_power=workspace.terminal_received_power,
                    stochastic_primary=workspace.stochastic_primary,
                    overflow_flags=workspace.overflow_flags,
                ),
                summary_request,
                context._summary_session,
            )
        except Exception as exc:
            if hasattr(exc, "phase") and hasattr(exc, "reason_code"):
                raise GpuResidentWavefrontProviderError(
                    str(exc.phase),
                    str(exc.reason_code),
                ) from exc
            raise GpuResidentWavefrontProviderError(
                "execute",
                "gpu_resident_summary_accumulator_failed",
            ) from exc
        active_download_started = time.perf_counter()
        try:
            active_by_depth = _copy_host(
                workspace.active_by_depth,
                slice(0, depth_count),
                np.int64,
            )
        except Exception as exc:
            raise GpuResidentWavefrontProviderError(
                "result_validation",
                "gpu_resident_active_depth_download_failed",
            ) from exc
        active_download_sec = time.perf_counter() - active_download_started
        selected_path_tape = None
        selected_path_count = 0
        skipped_path_count = 0
        path_select_sec = 0.0
        path_retrace_sec = 0.0
        path_download_sec = 0.0
        if path_selection is not None:
            (
                selected_path_tape,
                selected_path_count,
                skipped_path_count,
                path_select_sec,
                path_retrace_sec,
                path_download_sec,
            ) = _selected_path_tape(
                workspace,
                batch,
                path_selection,
                ray_count,
                depth_count,
                compact_retrace=compact_summary_workspace,
                context=context,
                device_scene=device_scene,
                bindings=bindings,
                kernel=kernel,
            )
        return GpuResidentWavefrontExecution(
            tape=None,
            summary_execution=summary_execution,
            selected_path_tape=selected_path_tape,
            selected_path_count=selected_path_count,
            skipped_path_count=skipped_path_count,
            path_select_sec=path_select_sec,
            path_retrace_sec=path_retrace_sec,
            path_download_sec=path_download_sec,
            workspace_contract=(
                COMPACT_WORKSPACE_CONTRACT
                if compact_summary_workspace
                else FULL_WORKSPACE_CONTRACT
            ),
            workspace_bytes=_workspace_bytes(workspace),
            event_geometry_capacity=workspace.geometry_capacity,
            active_ray_count_by_depth=tuple(
                int(value) for value in active_by_depth
            ),
            logical_intersection_rows=int(np.sum(active_by_depth)),
            stochastic_primary_ray_count=(
                summary_execution.stochastic_primary_ray_count
            ),
            scene_upload_sec=(
                0.0 if reused_device_scene else float(device_scene.upload_sec)
            ),
            bindings_upload_sec=(0.0 if reused_bindings else bindings.upload_sec),
            workspace_prepare_sec=workspace_prepare_sec,
            input_upload_sec=input_upload_sec,
            jit_compile_sec=(
                (kernel_elapsed if not was_compiled else 0.0)
                + summary_execution.jit_compile_sec
            ),
            kernel_sec=(
                (kernel_elapsed if was_compiled else 0.0)
                + summary_execution.kernel_sec
                + path_retrace_sec
            ),
            output_download_sec=(
                summary_execution.output_download_sec
                + active_download_sec
                + path_download_sec
                + importance_download_sec
            ),
            tape_build_sec=0.0,
            total_sec=time.perf_counter() - total_started,
            numba_version=capability.numba_version or "unknown",
            device_name=capability.device_name or "unknown",
            compute_capability=capability.compute_capability or "unknown",
            device_id=capability.device_id or 0,
            toolkit_layout=capability.toolkit_layout or "unknown",
            reused_device_scene=reused_device_scene,
            reused_device_bindings=reused_bindings,
            reused_workspace=reused_workspace,
            bounce_importance_eligible_count=bounce_eligible_count,
            bounce_importance_directed_count=bounce_directed_count,
            bounce_importance_zero_weight_count=bounce_zero_weight_count,
            bounce_importance_unsupported_count=bounce_unsupported_count,
            bounce_importance_weight_sum=bounce_weight_sum,
            bounce_importance_weight_square_sum=bounce_weight_square_sum,
            bounce_importance_weight_min=bounce_weight_min,
            bounce_importance_weight_max=bounce_weight_max,
        )

    download_started = time.perf_counter()
    try:
        row_slice = (slice(0, ray_count), slice(0, depth_count))
        event_faces = _copy_host(workspace.event_faces, row_slice, np.int32)
        event_incoming_power = _copy_host(
            workspace.event_incoming_power,
            row_slice,
            np.float64,
        )
        event_reflected_power = _copy_host(
            workspace.event_reflected_power,
            row_slice,
            np.float64,
        )
        event_emitted_power = _copy_host(
            workspace.event_emitted_power,
            row_slice,
            np.float64,
        )
        event_status = _copy_host(workspace.event_status, row_slice, np.uint16)
        event_lobes = _copy_host(workspace.event_lobes, row_slice, np.int8)
        event_incoming_kinds = _copy_host(
            workspace.event_incoming_kinds,
            row_slice,
            np.int8,
        )
        if batch.include_path_payload:
            path_slice = (
                slice(0, ray_count),
                slice(0, depth_count),
                slice(None),
            )
            event_distances = _copy_host(
                workspace.event_distances,
                row_slice,
                np.float64,
            )
            event_points = _copy_host(
                workspace.event_points,
                path_slice,
                np.float64,
            )
            event_normals = _copy_host(
                workspace.event_normals,
                path_slice,
                np.float64,
            )
            terminal_point = _copy_host(
                workspace.terminal_point,
                (slice(0, ray_count), slice(None)),
                np.float64,
            )
            terminal_normal = _copy_host(
                workspace.terminal_normal,
                (slice(0, ray_count), slice(None)),
                np.float64,
            )
            terminal_distance = _copy_host(
                workspace.terminal_distance,
                slice(0, ray_count),
                np.float64,
            )
        else:
            event_distances = np.empty((ray_count, depth_count), dtype=np.float64)
            event_points = np.empty((ray_count, depth_count, 3), dtype=np.float64)
            event_normals = np.empty((ray_count, depth_count, 3), dtype=np.float64)
            terminal_point = np.empty((ray_count, 3), dtype=np.float64)
            terminal_normal = np.empty((ray_count, 3), dtype=np.float64)
            terminal_distance = np.empty(ray_count, dtype=np.float64)
        terminal_kind = _copy_host(
            workspace.terminal_kind,
            slice(0, ray_count),
            np.int8,
        )
        terminal_depth = _copy_host(
            workspace.terminal_depth,
            slice(0, ray_count),
            np.int16,
        )
        terminal_power = _copy_host(
            workspace.terminal_power,
            slice(0, ray_count),
            np.float64,
        )
        terminal_ray_kind = _copy_host(
            workspace.terminal_ray_kind,
            slice(0, ray_count),
            np.int8,
        )
        terminal_receiver = _copy_host(
            workspace.terminal_receiver,
            slice(0, ray_count),
            np.int32,
        )
        terminal_row = _copy_host(
            workspace.terminal_row,
            slice(0, ray_count),
            np.int32,
        )
        terminal_column = _copy_host(
            workspace.terminal_column,
            slice(0, ray_count),
            np.int32,
        )
        terminal_received_power = _copy_host(
            workspace.terminal_received_power,
            slice(0, ray_count),
            np.float64,
        )
        terminal_incoming_power = _copy_host(
            workspace.terminal_incoming_power,
            slice(0, ray_count),
            np.float64,
        )
        stochastic_primary = _copy_host(
            workspace.stochastic_primary,
            slice(0, ray_count),
            np.uint8,
        )
        active_by_depth = _copy_host(
            workspace.active_by_depth,
            slice(0, depth_count),
            np.int64,
        )
        overflow_flags = _copy_host(
            workspace.overflow_flags,
            slice(0, ray_count),
            np.uint8,
        )
    except Exception as exc:
        raise GpuResidentWavefrontProviderError(
            "result_validation",
            "gpu_resident_output_download_failed",
        ) from exc
    output_download_sec = time.perf_counter() - download_started
    if np.any(overflow_flags):
        raise GpuResidentWavefrontProviderError(
            "result_validation",
            "gpu_resident_bvh_stack_overflow",
        )
    if np.any(terminal_kind == 0) or np.any(terminal_depth < 0):
        raise GpuResidentWavefrontProviderError(
            "result_validation",
            "gpu_resident_terminal_contract_invalid",
        )
    if np.any(event_faces >= len(context.scene.triangle_v0)):
        raise GpuResidentWavefrontProviderError(
            "result_validation",
            "gpu_resident_face_out_of_range",
        )

    tape_started = time.perf_counter()
    try:
        tape = _build_tape(
            batch,
            event_faces=event_faces,
            event_distances=event_distances,
            event_points=event_points,
            event_normals=event_normals,
            event_incoming_power=event_incoming_power,
            event_reflected_power=event_reflected_power,
            event_emitted_power=event_emitted_power,
            event_status=event_status,
            event_lobes=event_lobes,
            event_incoming_kinds=event_incoming_kinds,
            terminal_kind=terminal_kind,
            terminal_depth=terminal_depth,
            terminal_power=terminal_power,
            terminal_ray_kind=terminal_ray_kind,
            terminal_receiver=terminal_receiver,
            terminal_row=terminal_row,
            terminal_column=terminal_column,
            terminal_received_power=terminal_received_power,
            terminal_point=terminal_point,
            terminal_normal=terminal_normal,
            terminal_distance=terminal_distance,
            terminal_incoming_power=terminal_incoming_power,
        )
    except Exception as exc:
        raise GpuResidentWavefrontProviderError(
            "result_validation",
            "gpu_resident_event_tape_invalid",
        ) from exc
    tape_build_sec = time.perf_counter() - tape_started
    return GpuResidentWavefrontExecution(
        tape=tape,
        summary_execution=None,
        selected_path_tape=None,
        selected_path_count=0,
        skipped_path_count=0,
        path_select_sec=0.0,
        path_retrace_sec=0.0,
        path_download_sec=0.0,
        workspace_contract=FULL_WORKSPACE_CONTRACT,
        workspace_bytes=_workspace_bytes(workspace),
        event_geometry_capacity=workspace.geometry_capacity,
        active_ray_count_by_depth=tuple(int(value) for value in active_by_depth),
        logical_intersection_rows=int(np.sum(active_by_depth)),
        stochastic_primary_ray_count=int(np.count_nonzero(stochastic_primary)),
        scene_upload_sec=(
            0.0 if reused_device_scene else float(device_scene.upload_sec)
        ),
        bindings_upload_sec=(0.0 if reused_bindings else bindings.upload_sec),
        workspace_prepare_sec=workspace_prepare_sec,
        input_upload_sec=input_upload_sec,
        jit_compile_sec=kernel_elapsed if not was_compiled else 0.0,
        kernel_sec=kernel_elapsed if was_compiled else 0.0,
        output_download_sec=output_download_sec + importance_download_sec,
        tape_build_sec=tape_build_sec,
        total_sec=time.perf_counter() - total_started,
        numba_version=capability.numba_version or "unknown",
        device_name=capability.device_name or "unknown",
        compute_capability=capability.compute_capability or "unknown",
        device_id=capability.device_id or 0,
        toolkit_layout=capability.toolkit_layout or "unknown",
        reused_device_scene=reused_device_scene,
        reused_device_bindings=reused_bindings,
        reused_workspace=reused_workspace,
        bounce_importance_eligible_count=bounce_eligible_count,
        bounce_importance_directed_count=bounce_directed_count,
        bounce_importance_zero_weight_count=bounce_zero_weight_count,
        bounce_importance_unsupported_count=bounce_unsupported_count,
        bounce_importance_weight_sum=bounce_weight_sum,
        bounce_importance_weight_square_sum=bounce_weight_square_sum,
        bounce_importance_weight_min=bounce_weight_min,
        bounce_importance_weight_max=bounce_weight_max,
    )


def _reset_gpu_resident_wavefront_for_tests() -> None:
    global _KERNEL, _KERNEL_COMPILED, _PATH_SELECT_KERNEL, _PATH_GATHER_KERNEL
    with _STATE_LOCK:
        _KERNEL = None
        _KERNEL_COMPILED = False
        _PATH_SELECT_KERNEL = None
        _PATH_GATHER_KERNEL = None
        if hasattr(_WORKSPACES, "values"):
            delattr(_WORKSPACES, "values")


__all__ = [
    "GpuResidentWavefrontBatch",
    "GpuResidentWavefrontContext",
    "GpuResidentWavefrontExecution",
    "GpuResidentPathSelection",
    "GpuResidentWavefrontProviderError",
    "GpuResidentWavefrontUnavailable",
    "COMPACT_WORKSPACE_CONTRACT",
    "FULL_WORKSPACE_CONTRACT",
    "MAX_SUPPORTED_DEPTH",
    "MONTE_CARLO_CONTRACT",
    "PROVIDER_CONTRACT",
    "PROVIDER_NAME",
    "STATE_LAYOUT",
    "trace_resident_wavefront_gpu_cuda",
]
