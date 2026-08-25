from __future__ import annotations

"""Strict-float64 CUDA accumulator for PERF-4C summary reduction."""

from dataclasses import dataclass, fields
import hashlib
import importlib
import math
import threading
import time
from typing import Any, Callable, Optional
import warnings

import numpy as np

from . import gpu_cuda_intersection as cuda_backend
from . import native_cpu_ordered_reducer as reducer


PROVIDER_CONTRACT = "strict_float64_gpu_summary_accumulator_v1"
THREADS_PER_BLOCK = 128
_TOUCH_SENTINEL = np.int64(np.iinfo(np.int64).max)


class GpuSummaryAccumulatorError(RuntimeError):
    def __init__(self, phase: str, reason_code: str) -> None:
        super().__init__(reason_code)
        self.phase = phase
        self.reason_code = reason_code


def _readonly_owned(values: Any, dtype: Any, name: str) -> np.ndarray:
    result = np.array(values, dtype=dtype, order="C", copy=True)
    if result.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    result.setflags(write=False)
    return result


def _empty_readonly(dtype: Any) -> np.ndarray:
    result = np.empty(0, dtype=dtype)
    result.setflags(write=False)
    return result


def _validation_batch(
    face_profile_slots: np.ndarray,
    profile_unassigned: np.ndarray,
    receiver_columns: np.ndarray,
    grid_offsets: np.ndarray,
    max_depth: int,
) -> reducer.OrderedSummaryBatch:
    offsets = np.asarray([0], dtype=np.int64)
    offsets.setflags(write=False)
    return reducer.OrderedSummaryBatch(
        offsets=offsets,
        face_indices=_empty_readonly(np.int64),
        incoming_power_lumen=_empty_readonly(np.float64),
        reflected_power_lumen=_empty_readonly(np.float64),
        emitted_power_lumen=_empty_readonly(np.float64),
        status_flags=_empty_readonly(np.uint16),
        lobe_codes=_empty_readonly(np.int8),
        terminal_kind_codes=_empty_readonly(np.int8),
        terminal_depths=_empty_readonly(np.int16),
        terminal_current_power_lumen=_empty_readonly(np.float64),
        terminal_receiver_indices=_empty_readonly(np.int32),
        terminal_rows=_empty_readonly(np.int32),
        terminal_columns=_empty_readonly(np.int32),
        terminal_received_power_lumen=_empty_readonly(np.float64),
        terminal_received_power_squared_lumen2=_empty_readonly(np.float64),
        face_profile_slots=face_profile_slots,
        profile_unassigned=profile_unassigned,
        receiver_columns=receiver_columns,
        grid_offsets=grid_offsets,
        max_depth=max_depth,
    )


@dataclass(frozen=True, slots=True)
class GpuSummaryAccumulatorRequest:
    state: reducer.OrderedSummaryAccumulator
    face_profile_slots: np.ndarray
    profile_unassigned: np.ndarray
    receiver_columns: np.ndarray
    grid_offsets: np.ndarray
    max_depth: int

    def __post_init__(self) -> None:
        max_depth = int(self.max_depth)
        if max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        face_profile_slots = _readonly_owned(
            self.face_profile_slots,
            np.int32,
            "face_profile_slots",
        )
        profile_unassigned = _readonly_owned(
            self.profile_unassigned,
            np.bool_,
            "profile_unassigned",
        )
        receiver_columns = _readonly_owned(
            self.receiver_columns,
            np.int32,
            "receiver_columns",
        )
        grid_offsets = _readonly_owned(
            self.grid_offsets,
            np.int64,
            "grid_offsets",
        )
        if len(grid_offsets) != len(receiver_columns) + 1:
            raise ValueError("grid_offsets must have one sentinel per receiver")
        state = reducer.clone_ordered_summary_accumulator(self.state)
        state.freeze()
        batch = _validation_batch(
            face_profile_slots,
            profile_unassigned,
            receiver_columns,
            grid_offsets,
            max_depth,
        )
        state.validate_readonly(batch)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "face_profile_slots", face_profile_slots)
        object.__setattr__(self, "profile_unassigned", profile_unassigned)
        object.__setattr__(self, "receiver_columns", receiver_columns)
        object.__setattr__(self, "grid_offsets", grid_offsets)
        object.__setattr__(self, "max_depth", max_depth)


@dataclass(frozen=True, slots=True)
class GpuSummaryDeviceEvents:
    ray_count: int
    depth_count: int
    event_faces: Any
    event_incoming_power: Any
    event_reflected_power: Any
    event_emitted_power: Any
    event_status: Any
    event_lobes: Any
    terminal_kind: Any
    terminal_depth: Any
    terminal_power: Any
    terminal_receiver: Any
    terminal_row: Any
    terminal_column: Any
    terminal_received_power: Any
    stochastic_primary: Any
    overflow_flags: Any


@dataclass(frozen=True, slots=True)
class GpuSummaryAccumulatorExecution:
    result: reducer.OrderedSummaryResult
    stochastic_primary_ray_count: int
    jit_compile_sec: float
    state_upload_sec: float
    kernel_sec: float
    output_download_sec: float
    input_bytes: int
    output_bytes: int
    reused_device_state: bool
    provider_contract: str = PROVIDER_CONTRACT


@dataclass(slots=True)
class GpuSummaryAccumulatorSession:
    layout_key: Optional[tuple[int, ...]] = None
    device_state: Optional[dict[str, Any]] = None
    device_face_profile_slots: Any = None
    device_profile_unassigned: Any = None
    device_receiver_columns: Any = None
    device_grid_offsets: Any = None
    profile_touch_ordinals: Any = None
    profile_touch_faces: Any = None
    reflection_touch_ordinals: Any = None
    contribution_touch_ordinals: Any = None
    receiver_touch_ordinals: Any = None
    chunk_counts: Any = None
    last_state_digest: Optional[str] = None


_NUMERIC_STATE_FIELDS = tuple(
    field.name
    for field in fields(reducer.OrderedSummaryAccumulator)
    if field.name
    not in {
        "profile_seen",
        "reflection_depth_seen",
        "contribution_depth_seen",
        "receiver_depth_seen",
    }
)
_STATE_LOCK = threading.RLock()
_KERNEL: Optional[Callable[..., None]] = None
_PROFILE_FACE_KERNEL: Optional[Callable[..., None]] = None
_KERNEL_COMPILED = False


def _state_digest(state: reducer.OrderedSummaryAccumulator) -> str:
    digest = hashlib.blake2b(digest_size=24)
    for field in fields(state):
        values = getattr(state, field.name)
        digest.update(field.name.encode("ascii"))
        digest.update(values.dtype.str.encode("ascii"))
        digest.update(np.asarray(values.shape, dtype=np.int64).tobytes())
        digest.update(memoryview(values).cast("B"))
    return digest.hexdigest()


def _layout_key(request: GpuSummaryAccumulatorRequest) -> tuple[int, ...]:
    state = request.state
    return (
        len(request.face_profile_slots),
        len(request.profile_unassigned),
        len(request.receiver_columns),
        int(request.grid_offsets[-1]),
        request.max_depth,
        *tuple(int(getattr(state, name).size) for name in _NUMERIC_STATE_FIELDS),
    )


def _initialize_session(
    session: GpuSummaryAccumulatorSession,
    request: GpuSummaryAccumulatorRequest,
) -> tuple[bool, float, int]:
    cuda = cuda_backend._CUDA
    if cuda is None:
        raise GpuSummaryAccumulatorError(
            "initialize",
            "gpu_summary_cuda_runtime_not_initialized",
        )
    key = _layout_key(request)
    request_digest = _state_digest(request.state)
    if session.layout_key == key and session.device_state is not None:
        if session.last_state_digest != request_digest:
            raise GpuSummaryAccumulatorError(
                "input_prepare",
                "gpu_summary_accumulator_state_desynchronized",
            )
        return True, 0.0, 0
    started = time.perf_counter()
    try:
        session.device_state = {
            name: cuda.to_device(getattr(request.state, name))
            for name in _NUMERIC_STATE_FIELDS
        }
        session.device_face_profile_slots = cuda.to_device(
            request.face_profile_slots
        )
        session.device_profile_unassigned = cuda.to_device(
            request.profile_unassigned.astype(np.uint8)
        )
        session.device_receiver_columns = cuda.to_device(
            request.receiver_columns
        )
        session.device_grid_offsets = cuda.to_device(request.grid_offsets)
        profile_count = len(request.profile_unassigned)
        depth_count = request.max_depth + 1
        receiver_depth_count = len(request.receiver_columns) * depth_count
        session.profile_touch_ordinals = cuda.device_array(
            profile_count,
            dtype=np.int64,
        )
        session.profile_touch_faces = cuda.device_array(
            profile_count,
            dtype=np.int64,
        )
        session.reflection_touch_ordinals = cuda.device_array(
            depth_count,
            dtype=np.int64,
        )
        session.contribution_touch_ordinals = cuda.device_array(
            depth_count,
            dtype=np.int64,
        )
        session.receiver_touch_ordinals = cuda.device_array(
            receiver_depth_count,
            dtype=np.int64,
        )
        session.chunk_counts = cuda.device_array(5, dtype=np.int64)
        cuda.synchronize()
    except Exception as exc:
        raise GpuSummaryAccumulatorError(
            "initialize",
            "gpu_summary_accumulator_allocation_failed",
        ) from exc
    session.layout_key = key
    session.last_state_digest = request_digest
    input_bytes = sum(
        int(getattr(request.state, name).nbytes) for name in _NUMERIC_STATE_FIELDS
    )
    input_bytes += (
        request.face_profile_slots.nbytes
        + request.profile_unassigned.nbytes
        + request.receiver_columns.nbytes
        + request.grid_offsets.nbytes
    )
    return False, time.perf_counter() - started, input_bytes


def _make_kernels() -> tuple[Callable[..., None], Callable[..., None]]:
    cuda = cuda_backend._CUDA
    if cuda is None:
        raise GpuSummaryAccumulatorError(
            "initialize",
            "gpu_summary_cuda_runtime_not_initialized",
        )

    @cuda.jit
    def accumulate_kernel(
        ray_count,
        depth_count,
        event_faces,
        event_incoming_power,
        event_reflected_power,
        event_emitted_power,
        event_status,
        event_lobes,
        terminal_kind,
        terminal_depth,
        terminal_power,
        terminal_receiver,
        terminal_row,
        terminal_column,
        terminal_received_power,
        stochastic_primary,
        overflow_flags,
        face_profile_slots,
        profile_unassigned,
        receiver_columns,
        grid_offsets,
        optical_counts,
        profile_hit_counts,
        profile_incoming_flux,
        profile_reflected_flux,
        reflection_counts,
        reflection_flux,
        reflection_lobe_counts,
        reflection_lobe_flux,
        reflection_depth_counts,
        reflection_depth_flux,
        contribution_receiver_counts,
        contribution_receiver_flux,
        contribution_lobe_counts,
        contribution_lobe_flux,
        contribution_depth_counts,
        contribution_depth_flux,
        receiver_counts,
        receiver_flux,
        receiver_depth_counts,
        receiver_depth_flux,
        grid_flux,
        grid_flux_squared,
        grid_hit_counts,
        grid_flux_squared_totals,
        profile_touch_ordinals,
        reflection_touch_ordinals,
        contribution_touch_ordinals,
        receiver_touch_ordinals,
        chunk_counts,
    ):
        primary_slot = cuda.grid(1)
        if primary_slot >= ray_count:
            return
        if overflow_flags[primary_slot] != 0:
            cuda.atomic.max(chunk_counts, 4, 1)
            return
        terminal_code = terminal_kind[primary_slot]
        terminal_depth_value = terminal_depth[primary_slot]
        if (
            terminal_code < reducer.TERMINAL_RECEIVER
            or terminal_code > reducer.TERMINAL_BLOCKED
            or terminal_depth_value < 0
            or terminal_depth_value >= depth_count
        ):
            cuda.atomic.max(chunk_counts, 4, 2)
            return
        cuda.atomic.max(
            reflection_counts,
            reducer.REF_MAX_OBSERVED_DEPTH,
            terminal_depth_value,
        )
        if stochastic_primary[primary_slot] != 0:
            cuda.atomic.add(chunk_counts, 3, 1)

        event_count = 0
        for depth in range(depth_count):
            face_index = event_faces[primary_slot, depth]
            if face_index < 0:
                break
            if face_index >= face_profile_slots.shape[0]:
                cuda.atomic.max(chunk_counts, 4, 3)
                return
            event_count += 1
            cuda.atomic.add(chunk_counts, 1, 1)
            profile_slot = face_profile_slots[face_index]
            if (
                profile_slot < 0
                or profile_slot >= profile_unassigned.shape[0]
            ):
                cuda.atomic.max(chunk_counts, 4, 4)
                return
            ordinal = primary_slot * depth_count + depth
            cuda.atomic.min(profile_touch_ordinals, profile_slot, ordinal)
            incoming_power = event_incoming_power[primary_slot, depth]
            reflected_power = event_reflected_power[primary_slot, depth]
            cuda.atomic.add(
                optical_counts,
                reducer.OPTICAL_SURFACE_HIT_COUNT,
                1,
            )
            if profile_unassigned[profile_slot] != 0:
                cuda.atomic.add(
                    optical_counts,
                    reducer.OPTICAL_UNASSIGNED_SURFACE_HIT_COUNT,
                    1,
                )
            cuda.atomic.add(profile_hit_counts, profile_slot, 1)
            cuda.atomic.add(
                profile_incoming_flux,
                profile_slot,
                incoming_power,
            )
            cuda.atomic.add(
                profile_reflected_flux,
                profile_slot,
                reflected_power,
            )
            cuda.atomic.add(
                reflection_counts,
                reducer.REF_SURFACE_HIT_COUNT,
                1,
            )
            flags = event_status[primary_slot, depth]
            if flags & reducer.STATUS_ATTEMPTED:
                cuda.atomic.add(
                    reflection_counts,
                    reducer.REF_ATTEMPT_COUNT,
                    1,
                )
            if flags & reducer.STATUS_DEPTH_LIMITED:
                cuda.atomic.add(
                    reflection_counts,
                    reducer.REF_DEPTH_LIMIT_COUNT,
                    1,
                )
            if flags & reducer.STATUS_BELOW_ENERGY:
                cuda.atomic.add(
                    reflection_counts,
                    reducer.REF_BELOW_ENERGY_COUNT,
                    1,
                )
            if flags & reducer.STATUS_ROULETTE_TERMINATED:
                cuda.atomic.add(
                    reflection_counts,
                    reducer.REF_ROULETTE_TERMINATED_COUNT,
                    1,
                )
            if flags & reducer.STATUS_ROULETTE_SURVIVED:
                cuda.atomic.add(
                    reflection_counts,
                    reducer.REF_ROULETTE_SURVIVED_COUNT,
                    1,
                )
            if flags & reducer.STATUS_DISABLED:
                cuda.atomic.add(
                    reflection_counts,
                    reducer.REF_DISABLED_COUNT,
                    1,
                )

            emitted = bool(flags & reducer.STATUS_EMITTED)
            if depth > 0:
                previous_lobe = event_lobes[primary_slot, depth - 1]
                outcome = (
                    reducer.OUTCOME_CONTINUED
                    if emitted
                    else reducer.OUTCOME_BLOCKED
                )
                cuda.atomic.add(
                    reflection_lobe_counts,
                    (previous_lobe, outcome),
                    1,
                )
                cuda.atomic.add(
                    reflection_depth_counts,
                    (depth, outcome),
                    1,
                )
                if outcome == reducer.OUTCOME_CONTINUED:
                    cuda.atomic.add(
                        reflection_counts,
                        reducer.REF_CONTINUED_COUNT,
                        1,
                    )
                else:
                    cuda.atomic.add(
                        reflection_counts,
                        reducer.REF_BLOCKED_COUNT,
                        1,
                    )
                cuda.atomic.add(
                    contribution_lobe_counts,
                    (previous_lobe, outcome),
                    1,
                )
                cuda.atomic.add(
                    contribution_lobe_flux,
                    (previous_lobe, outcome),
                    incoming_power,
                )
                depth_field = outcome + 1
                cuda.atomic.add(
                    contribution_depth_counts,
                    (depth, depth_field),
                    1,
                )
                cuda.atomic.add(
                    contribution_depth_flux,
                    (depth, depth_field),
                    incoming_power,
                )
            if not emitted:
                break

            lobe = event_lobes[primary_slot, depth]
            next_depth = depth + 1
            emitted_power = event_emitted_power[primary_slot, depth]
            cuda.atomic.add(
                reflection_counts,
                reducer.REF_EMITTED_COUNT,
                1,
            )
            cuda.atomic.add(
                reflection_lobe_counts,
                (lobe, reducer.OUTCOME_EMITTED),
                1,
            )
            cuda.atomic.add(reflection_lobe_flux, (lobe, 0), emitted_power)
            cuda.atomic.add(
                reflection_depth_counts,
                (next_depth, reducer.REF_DEPTH_EMITTED),
                1,
            )
            cuda.atomic.add(
                reflection_depth_flux,
                (next_depth, 0),
                emitted_power,
            )
            cuda.atomic.min(
                reflection_touch_ordinals,
                next_depth,
                ordinal,
            )
            cuda.atomic.add(
                contribution_lobe_counts,
                (lobe, reducer.OUTCOME_EMITTED),
                1,
            )
            cuda.atomic.add(
                contribution_lobe_flux,
                (lobe, reducer.OUTCOME_EMITTED),
                emitted_power,
            )
            cuda.atomic.add(
                contribution_depth_counts,
                (next_depth, reducer.CONTRIBUTION_DEPTH_EMITTED),
                1,
            )
            cuda.atomic.add(
                contribution_depth_flux,
                (next_depth, reducer.CONTRIBUTION_DEPTH_EMITTED),
                emitted_power,
            )
            cuda.atomic.min(
                contribution_touch_ordinals,
                next_depth,
                primary_slot * (depth_count + 1) + depth,
            )

        if event_count > 0:
            cuda.atomic.add(
                reflection_counts,
                reducer.REF_PRIMARY_SURFACE_HIT_COUNT,
                1,
            )

        if terminal_code == reducer.TERMINAL_RECEIVER:
            receiver_index = terminal_receiver[primary_slot]
            if (
                receiver_index < 0
                or receiver_index >= receiver_columns.shape[0]
            ):
                cuda.atomic.max(chunk_counts, 4, 5)
                return
            row = terminal_row[primary_slot]
            column = terminal_column[primary_slot]
            grid_start = grid_offsets[receiver_index]
            grid_end = grid_offsets[receiver_index + 1]
            columns = receiver_columns[receiver_index]
            if row < 0 or column < 0 or column >= columns:
                cuda.atomic.max(chunk_counts, 4, 6)
                return
            grid_index = grid_start + row * columns + column
            if grid_index < grid_start or grid_index >= grid_end:
                cuda.atomic.max(chunk_counts, 4, 7)
                return
            received_power = terminal_received_power[primary_slot]
            received_square = received_power * received_power
            cuda.atomic.add(chunk_counts, 0, 1)
            cuda.atomic.add(grid_flux, grid_index, received_power)
            cuda.atomic.add(grid_flux_squared, grid_index, received_square)
            cuda.atomic.add(grid_hit_counts, receiver_index, 1)
            cuda.atomic.add(
                grid_flux_squared_totals,
                receiver_index,
                received_square,
            )
            receiver_code = receiver_index * depth_count + terminal_depth_value
            cuda.atomic.min(
                receiver_touch_ordinals,
                receiver_code,
                primary_slot,
            )
            cuda.atomic.add(
                receiver_depth_counts,
                (receiver_index, terminal_depth_value),
                1,
            )
            cuda.atomic.add(
                receiver_depth_flux,
                (receiver_index, terminal_depth_value),
                received_power,
            )
            cuda.atomic.add(
                receiver_counts,
                (receiver_index, reducer.RECEIVER_TOTAL),
                1,
            )
            cuda.atomic.add(
                receiver_flux,
                (receiver_index, reducer.RECEIVER_TOTAL),
                received_power,
            )
            if terminal_depth_value == 0:
                cuda.atomic.add(
                    reflection_counts,
                    reducer.REF_DIRECT_RECEIVER_HIT_COUNT,
                    1,
                )
                cuda.atomic.add(
                    reflection_flux,
                    reducer.REF_DIRECT_RECEIVER_FLUX,
                    received_power,
                )
                cuda.atomic.add(
                    contribution_receiver_counts,
                    reducer.CONTRIBUTION_DIRECT_RECEIVER,
                    1,
                )
                cuda.atomic.add(
                    contribution_receiver_flux,
                    reducer.CONTRIBUTION_DIRECT_RECEIVER,
                    received_power,
                )
                cuda.atomic.add(
                    receiver_counts,
                    (receiver_index, reducer.RECEIVER_DIRECT),
                    1,
                )
                cuda.atomic.add(
                    receiver_flux,
                    (receiver_index, reducer.RECEIVER_DIRECT),
                    received_power,
                )
                cuda.atomic.add(
                    contribution_depth_counts,
                    (0, reducer.CONTRIBUTION_DEPTH_RECEIVER),
                    1,
                )
                cuda.atomic.add(
                    contribution_depth_flux,
                    (0, reducer.CONTRIBUTION_DEPTH_RECEIVER),
                    received_power,
                )
                terminal_ordinal = primary_slot * (depth_count + 1) + depth_count
                cuda.atomic.min(
                    contribution_touch_ordinals,
                    0,
                    terminal_ordinal,
                )
            else:
                previous_lobe = event_lobes[
                    primary_slot,
                    terminal_depth_value - 1,
                ]
                cuda.atomic.add(
                    reflection_counts,
                    reducer.REF_RECEIVER_HIT_COUNT,
                    1,
                )
                cuda.atomic.add(
                    reflection_flux,
                    reducer.REF_REFLECTED_RECEIVER_FLUX,
                    received_power,
                )
                cuda.atomic.add(
                    reflection_lobe_counts,
                    (previous_lobe, reducer.OUTCOME_RECEIVER),
                    1,
                )
                cuda.atomic.add(
                    reflection_lobe_flux,
                    (previous_lobe, 1),
                    received_power,
                )
                cuda.atomic.add(
                    reflection_depth_counts,
                    (terminal_depth_value, reducer.REF_DEPTH_RECEIVER),
                    1,
                )
                cuda.atomic.add(
                    reflection_depth_flux,
                    (terminal_depth_value, 1),
                    received_power,
                )
                cuda.atomic.add(
                    contribution_receiver_counts,
                    reducer.CONTRIBUTION_REFLECTED_RECEIVER,
                    1,
                )
                cuda.atomic.add(
                    contribution_receiver_flux,
                    reducer.CONTRIBUTION_REFLECTED_RECEIVER,
                    received_power,
                )
                cuda.atomic.add(
                    contribution_lobe_counts,
                    (previous_lobe, reducer.OUTCOME_RECEIVER),
                    1,
                )
                cuda.atomic.add(
                    contribution_lobe_flux,
                    (previous_lobe, reducer.OUTCOME_RECEIVER),
                    received_power,
                )
                cuda.atomic.add(
                    contribution_depth_counts,
                    (terminal_depth_value, reducer.CONTRIBUTION_DEPTH_RECEIVER),
                    1,
                )
                cuda.atomic.add(
                    contribution_depth_flux,
                    (terminal_depth_value, reducer.CONTRIBUTION_DEPTH_RECEIVER),
                    received_power,
                )
                cuda.atomic.add(
                    receiver_counts,
                    (receiver_index, reducer.RECEIVER_REFLECTED),
                    1,
                )
                cuda.atomic.add(
                    receiver_flux,
                    (receiver_index, reducer.RECEIVER_REFLECTED),
                    received_power,
                )
                receiver_field = reducer.RECEIVER_SPECULAR + previous_lobe
                cuda.atomic.add(
                    receiver_counts,
                    (receiver_index, receiver_field),
                    1,
                )
                cuda.atomic.add(
                    receiver_flux,
                    (receiver_index, receiver_field),
                    received_power,
                )
        else:
            cuda.atomic.add(chunk_counts, 2, 1)
            if (
                terminal_code == reducer.TERMINAL_ESCAPED
                and terminal_depth_value > 0
            ):
                previous_lobe = event_lobes[
                    primary_slot,
                    terminal_depth_value - 1,
                ]
                cuda.atomic.add(
                    reflection_counts,
                    reducer.REF_ESCAPED_COUNT,
                    1,
                )
                cuda.atomic.add(
                    reflection_lobe_counts,
                    (previous_lobe, reducer.OUTCOME_ESCAPED),
                    1,
                )
                cuda.atomic.add(
                    reflection_depth_counts,
                    (terminal_depth_value, reducer.REF_DEPTH_ESCAPED),
                    1,
                )
                escaped_power = terminal_power[primary_slot]
                cuda.atomic.add(
                    contribution_lobe_counts,
                    (previous_lobe, reducer.OUTCOME_ESCAPED),
                    1,
                )
                cuda.atomic.add(
                    contribution_lobe_flux,
                    (previous_lobe, reducer.OUTCOME_ESCAPED),
                    escaped_power,
                )
                cuda.atomic.add(
                    contribution_depth_counts,
                    (terminal_depth_value, reducer.CONTRIBUTION_DEPTH_ESCAPED),
                    1,
                )
                cuda.atomic.add(
                    contribution_depth_flux,
                    (terminal_depth_value, reducer.CONTRIBUTION_DEPTH_ESCAPED),
                    escaped_power,
                )

    @cuda.jit
    def resolve_profile_faces(
        depth_count,
        event_faces,
        touch_ordinals,
        touch_faces,
    ):
        profile_slot = cuda.grid(1)
        if profile_slot >= touch_ordinals.shape[0]:
            return
        ordinal = touch_ordinals[profile_slot]
        if ordinal == _TOUCH_SENTINEL:
            touch_faces[profile_slot] = -1
            return
        primary_slot = ordinal // depth_count
        depth = ordinal - primary_slot * depth_count
        touch_faces[profile_slot] = event_faces[primary_slot, depth]

    return accumulate_kernel, resolve_profile_faces


def _ensure_kernels() -> tuple[Callable[..., None], Callable[..., None]]:
    global _KERNEL, _PROFILE_FACE_KERNEL
    if _KERNEL is not None and _PROFILE_FACE_KERNEL is not None:
        return _KERNEL, _PROFILE_FACE_KERNEL
    with _STATE_LOCK:
        if _KERNEL is None or _PROFILE_FACE_KERNEL is None:
            try:
                _KERNEL, _PROFILE_FACE_KERNEL = _make_kernels()
            except GpuSummaryAccumulatorError:
                raise
            except Exception as exc:
                raise GpuSummaryAccumulatorError(
                    "initialize",
                    "gpu_summary_accumulator_kernel_create_failed",
                ) from exc
    return _KERNEL, _PROFILE_FACE_KERNEL


def _readonly(values: np.ndarray, dtype: Any) -> np.ndarray:
    result = np.array(values, dtype=dtype, order="C", copy=True)
    result.setflags(write=False)
    return result


def _touch_vector(
    ordinals: np.ndarray,
    baseline_seen: np.ndarray,
    dtype: Any,
) -> np.ndarray:
    candidates = np.flatnonzero(
        (ordinals != _TOUCH_SENTINEL) & ~baseline_seen.reshape(-1)
    )
    if not len(candidates):
        return _readonly(np.empty(0, dtype=dtype), dtype)
    order = np.argsort(ordinals[candidates], kind="stable")
    return _readonly(candidates[order], dtype)


def accumulate_resident_summary_gpu_cuda(
    events: GpuSummaryDeviceEvents,
    request: GpuSummaryAccumulatorRequest,
    session: GpuSummaryAccumulatorSession,
) -> GpuSummaryAccumulatorExecution:
    if events.ray_count <= 0:
        raise ValueError("GPU summary events must not be empty")
    if events.depth_count != request.max_depth + 1:
        raise ValueError("GPU summary depth count is inconsistent")
    cuda = cuda_backend._CUDA
    if cuda is None:
        raise GpuSummaryAccumulatorError(
            "initialize",
            "gpu_summary_cuda_runtime_not_initialized",
        )
    reused, upload_sec, input_bytes = _initialize_session(session, request)
    assert session.device_state is not None
    profile_count = len(request.profile_unassigned)
    depth_count = request.max_depth + 1
    receiver_depth_count = len(request.receiver_columns) * depth_count
    reset_started = time.perf_counter()
    try:
        session.profile_touch_ordinals.copy_to_device(
            np.full(profile_count, _TOUCH_SENTINEL, dtype=np.int64)
        )
        session.profile_touch_faces.copy_to_device(
            np.full(profile_count, -1, dtype=np.int64)
        )
        session.reflection_touch_ordinals.copy_to_device(
            np.full(depth_count, _TOUCH_SENTINEL, dtype=np.int64)
        )
        session.contribution_touch_ordinals.copy_to_device(
            np.full(depth_count, _TOUCH_SENTINEL, dtype=np.int64)
        )
        session.receiver_touch_ordinals.copy_to_device(
            np.full(receiver_depth_count, _TOUCH_SENTINEL, dtype=np.int64)
        )
        session.chunk_counts.copy_to_device(np.zeros(5, dtype=np.int64))
        cuda.synchronize()
    except Exception as exc:
        raise GpuSummaryAccumulatorError(
            "input_prepare",
            "gpu_summary_accumulator_reset_failed",
        ) from exc
    state_upload_sec = upload_sec + time.perf_counter() - reset_started
    kernel, profile_kernel = _ensure_kernels()
    global _KERNEL_COMPILED
    was_compiled = _KERNEL_COMPILED
    kernel_started = time.perf_counter()
    state = session.device_state
    try:
        block_count = (
            events.ray_count + THREADS_PER_BLOCK - 1
        ) // THREADS_PER_BLOCK
        try:
            performance_warning = importlib.import_module(
                "numba.core.errors"
            ).NumbaPerformanceWarning
        except Exception:
            performance_warning = Warning
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", performance_warning)
            kernel[block_count, THREADS_PER_BLOCK](
                events.ray_count,
                events.depth_count,
                events.event_faces,
                events.event_incoming_power,
                events.event_reflected_power,
                events.event_emitted_power,
                events.event_status,
                events.event_lobes,
                events.terminal_kind,
                events.terminal_depth,
                events.terminal_power,
                events.terminal_receiver,
                events.terminal_row,
                events.terminal_column,
                events.terminal_received_power,
                events.stochastic_primary,
                events.overflow_flags,
                session.device_face_profile_slots,
                session.device_profile_unassigned,
                session.device_receiver_columns,
                session.device_grid_offsets,
                state["optical_counts"],
                state["profile_hit_counts"],
                state["profile_incoming_flux_lumen"],
                state["profile_reflected_flux_lumen"],
                state["reflection_counts"],
                state["reflection_flux_lumen"],
                state["reflection_lobe_counts"],
                state["reflection_lobe_flux_lumen"],
                state["reflection_depth_counts"],
                state["reflection_depth_flux_lumen"],
                state["contribution_receiver_counts"],
                state["contribution_receiver_flux_lumen"],
                state["contribution_lobe_counts"],
                state["contribution_lobe_flux_lumen"],
                state["contribution_depth_counts"],
                state["contribution_depth_flux_lumen"],
                state["receiver_counts"],
                state["receiver_flux_lumen"],
                state["receiver_depth_counts"],
                state["receiver_depth_flux_lumen"],
                state["grid_flux_lumen"],
                state["grid_flux_squared_lumen2"],
                state["grid_hit_counts"],
                state["grid_flux_squared_totals_lumen2"],
                session.profile_touch_ordinals,
                session.reflection_touch_ordinals,
                session.contribution_touch_ordinals,
                session.receiver_touch_ordinals,
                session.chunk_counts,
            )
            if profile_count:
                profile_blocks = (
                    profile_count + THREADS_PER_BLOCK - 1
                ) // THREADS_PER_BLOCK
                profile_kernel[profile_blocks, THREADS_PER_BLOCK](
                    depth_count,
                    events.event_faces,
                    session.profile_touch_ordinals,
                    session.profile_touch_faces,
                )
        cuda.synchronize()
    except Exception as exc:
        raise GpuSummaryAccumulatorError(
            "execute",
            "gpu_summary_accumulator_kernel_failed",
        ) from exc
    kernel_elapsed = time.perf_counter() - kernel_started
    _KERNEL_COMPILED = True

    download_started = time.perf_counter()
    try:
        host_numeric = {
            name: np.array(
                device_values.copy_to_host(),
                copy=True,
                order="C",
            )
            for name, device_values in state.items()
        }
        profile_ordinals = np.ascontiguousarray(
            session.profile_touch_ordinals.copy_to_host(),
            dtype=np.int64,
        )
        profile_faces = np.ascontiguousarray(
            session.profile_touch_faces.copy_to_host(),
            dtype=np.int64,
        )
        reflection_ordinals = np.ascontiguousarray(
            session.reflection_touch_ordinals.copy_to_host(),
            dtype=np.int64,
        )
        contribution_ordinals = np.ascontiguousarray(
            session.contribution_touch_ordinals.copy_to_host(),
            dtype=np.int64,
        )
        receiver_ordinals = np.ascontiguousarray(
            session.receiver_touch_ordinals.copy_to_host(),
            dtype=np.int64,
        )
        chunk_counts = np.ascontiguousarray(
            session.chunk_counts.copy_to_host(),
            dtype=np.int64,
        )
    except Exception as exc:
        raise GpuSummaryAccumulatorError(
            "result_validation",
            "gpu_summary_accumulator_download_failed",
        ) from exc
    output_download_sec = time.perf_counter() - download_started
    if int(chunk_counts[4]) != 0:
        raise GpuSummaryAccumulatorError(
            "result_validation",
            f"gpu_summary_accumulator_invalid_event_{int(chunk_counts[4])}",
        )

    profile_touch_slots = _touch_vector(
        profile_ordinals,
        request.state.profile_seen,
        np.int32,
    )
    profile_touch_values = _readonly(
        profile_faces[profile_touch_slots.astype(np.int64)],
        np.int64,
    )
    reflection_touch = _touch_vector(
        reflection_ordinals,
        request.state.reflection_depth_seen,
        np.int16,
    )
    contribution_touch = _touch_vector(
        contribution_ordinals,
        request.state.contribution_depth_seen,
        np.int16,
    )
    receiver_touch_codes = _touch_vector(
        receiver_ordinals,
        request.state.receiver_depth_seen,
        np.int64,
    )
    receiver_touch_receivers = _readonly(
        receiver_touch_codes.astype(np.int64) // depth_count,
        np.int32,
    )
    receiver_touch_depths = _readonly(
        receiver_touch_codes.astype(np.int64) % depth_count,
        np.int16,
    )
    profile_seen = np.array(request.state.profile_seen, copy=True, order="C")
    profile_seen[profile_ordinals != _TOUCH_SENTINEL] = True
    reflection_seen = np.array(
        request.state.reflection_depth_seen,
        copy=True,
        order="C",
    )
    reflection_seen[reflection_ordinals != _TOUCH_SENTINEL] = True
    contribution_seen = np.array(
        request.state.contribution_depth_seen,
        copy=True,
        order="C",
    )
    contribution_seen[contribution_ordinals != _TOUCH_SENTINEL] = True
    receiver_seen = np.array(
        request.state.receiver_depth_seen,
        copy=True,
        order="C",
    )
    receiver_seen.reshape(-1)[receiver_ordinals != _TOUCH_SENTINEL] = True
    result_state = reducer.OrderedSummaryAccumulator(
        **host_numeric,
        profile_seen=profile_seen,
        reflection_depth_seen=reflection_seen,
        contribution_depth_seen=contribution_seen,
        receiver_depth_seen=receiver_seen,
    )
    validation_batch = _validation_batch(
        request.face_profile_slots,
        request.profile_unassigned,
        request.receiver_columns,
        request.grid_offsets,
        request.max_depth,
    )
    try:
        result_state.validate_mutable(validation_batch)
        surface_count = int(chunk_counts[1])
        receiver_count = int(chunk_counts[0])
        terminated_count = int(chunk_counts[2])
        if receiver_count + terminated_count != events.ray_count:
            raise ValueError("terminal counts do not cover the primary batch")
        optical_delta = (
            result_state.optical_counts[
                reducer.OPTICAL_SURFACE_HIT_COUNT
            ]
            - request.state.optical_counts[
                reducer.OPTICAL_SURFACE_HIT_COUNT
            ]
        )
        if int(optical_delta) != surface_count:
            raise ValueError("surface count does not match optical accumulation")
        grid_delta = result_state.grid_hit_counts - request.state.grid_hit_counts
        if int(np.sum(grid_delta)) != receiver_count:
            raise ValueError("receiver count does not match grid accumulation")
        result_state.freeze()
    except Exception as exc:
        raise GpuSummaryAccumulatorError(
            "result_validation",
            "gpu_summary_accumulator_result_invalid",
        ) from exc
    result = reducer.OrderedSummaryResult(
        state=result_state,
        profile_first_touch_slots=profile_touch_slots,
        profile_first_touch_faces=profile_touch_values,
        reflection_depth_first_touch=reflection_touch,
        contribution_depth_first_touch=contribution_touch,
        receiver_depth_first_touch_receivers=receiver_touch_receivers,
        receiver_depth_first_touch_depths=receiver_touch_depths,
        receiver_hit_count=receiver_count,
        surface_hit_count=surface_count,
        terminated_ray_count=terminated_count,
    )
    session.last_state_digest = _state_digest(result_state)
    output_bytes = sum(values.nbytes for values in host_numeric.values())
    output_bytes += sum(
        values.nbytes
        for values in (
            profile_ordinals,
            profile_faces,
            reflection_ordinals,
            contribution_ordinals,
            receiver_ordinals,
            chunk_counts,
        )
    )
    return GpuSummaryAccumulatorExecution(
        result=result,
        stochastic_primary_ray_count=int(chunk_counts[3]),
        jit_compile_sec=kernel_elapsed if not was_compiled else 0.0,
        state_upload_sec=state_upload_sec,
        kernel_sec=kernel_elapsed if was_compiled else 0.0,
        output_download_sec=output_download_sec,
        input_bytes=input_bytes,
        output_bytes=output_bytes,
        reused_device_state=reused,
    )


def reset_gpu_summary_accumulator_for_tests() -> None:
    global _KERNEL, _PROFILE_FACE_KERNEL, _KERNEL_COMPILED
    with _STATE_LOCK:
        _KERNEL = None
        _PROFILE_FACE_KERNEL = None
        _KERNEL_COMPILED = False


__all__ = [
    "GpuSummaryAccumulatorError",
    "GpuSummaryAccumulatorExecution",
    "GpuSummaryAccumulatorRequest",
    "GpuSummaryAccumulatorSession",
    "GpuSummaryDeviceEvents",
    "PROVIDER_CONTRACT",
    "accumulate_resident_summary_gpu_cuda",
    "reset_gpu_summary_accumulator_for_tests",
]
