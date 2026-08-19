from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import hmac
import importlib
from numbers import Integral
import threading
import time
from typing import Any, Callable, Optional

import numpy as np


CONTRACT_VERSION = "ordered_summary_reducer_v1"

LOBE_SPECULAR = 0
LOBE_LAMBERTIAN = 1
LOBE_GAUSSIAN = 2
LOBE_COUNT = 3

TERMINAL_RECEIVER = 1
TERMINAL_ESCAPED = 2
TERMINAL_BLOCKED = 3

STATUS_ATTEMPTED = 1 << 0
STATUS_DEPTH_LIMITED = 1 << 1
STATUS_BELOW_ENERGY = 1 << 2
STATUS_ROULETTE_TERMINATED = 1 << 3
STATUS_ROULETTE_SURVIVED = 1 << 4
STATUS_DISABLED = 1 << 5
STATUS_EMITTED = 1 << 6

REF_MAX_OBSERVED_DEPTH = 0
REF_SURFACE_HIT_COUNT = 1
REF_PRIMARY_SURFACE_HIT_COUNT = 2
REF_ATTEMPT_COUNT = 3
REF_EMITTED_COUNT = 4
REF_RECEIVER_HIT_COUNT = 5
REF_BLOCKED_COUNT = 6
REF_CONTINUED_COUNT = 7
REF_ESCAPED_COUNT = 8
REF_BELOW_ENERGY_COUNT = 9
REF_DISABLED_COUNT = 10
REF_DEPTH_LIMIT_COUNT = 11
REF_ROULETTE_TERMINATED_COUNT = 12
REF_ROULETTE_SURVIVED_COUNT = 13
REF_DIRECT_RECEIVER_HIT_COUNT = 14
REF_COUNT_SIZE = 15

REF_DIRECT_RECEIVER_FLUX = 0
REF_REFLECTED_RECEIVER_FLUX = 1
REF_FLUX_SIZE = 2

OUTCOME_EMITTED = 0
OUTCOME_RECEIVER = 1
OUTCOME_BLOCKED = 2
OUTCOME_CONTINUED = 3
OUTCOME_ESCAPED = 4
OUTCOME_SIZE = 5

REF_DEPTH_EMITTED = 0
REF_DEPTH_RECEIVER = 1
REF_DEPTH_BLOCKED = 2
REF_DEPTH_CONTINUED = 3
REF_DEPTH_ESCAPED = 4
REF_DEPTH_SIZE = 5

CONTRIBUTION_DIRECT_RECEIVER = 0
CONTRIBUTION_REFLECTED_RECEIVER = 1
CONTRIBUTION_RECEIVER_SIZE = 2

CONTRIBUTION_DEPTH_SURFACE = 0
CONTRIBUTION_DEPTH_EMITTED = 1
CONTRIBUTION_DEPTH_RECEIVER = 2
CONTRIBUTION_DEPTH_BLOCKED = 3
CONTRIBUTION_DEPTH_CONTINUED = 4
CONTRIBUTION_DEPTH_ESCAPED = 5
CONTRIBUTION_DEPTH_SECONDARY_BLOCK = 6
CONTRIBUTION_DEPTH_SIZE = 7

RECEIVER_DIRECT = 0
RECEIVER_REFLECTED = 1
RECEIVER_TOTAL = 2
RECEIVER_SPECULAR = 3
RECEIVER_LAMBERTIAN = 4
RECEIVER_GAUSSIAN = 5
RECEIVER_FIELD_SIZE = 6

OPTICAL_SURFACE_HIT_COUNT = 0
OPTICAL_UNASSIGNED_SURFACE_HIT_COUNT = 1
OPTICAL_COUNT_SIZE = 2


class NativeCpuOrderedReducerUnavailable(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.phase = "probe"


class NativeCpuOrderedReducerProviderError(RuntimeError):
    def __init__(
        self,
        phase: str,
        reason_code: str,
        *,
        jit_compile_sec: float = 0.0,
        execute_sec: float = 0.0,
        result_validation_sec: float = 0.0,
        numba_version: Optional[str] = None,
    ) -> None:
        super().__init__(reason_code)
        self.phase = phase
        self.reason_code = reason_code
        self.jit_compile_sec = float(jit_compile_sec)
        self.execute_sec = float(execute_sec)
        self.result_validation_sec = float(result_validation_sec)
        self.numba_version = numba_version


@dataclass(frozen=True, slots=True)
class NativeCpuOrderedReducerCapability:
    available: bool
    reason_code: Optional[str]
    numba_version: Optional[str]
    contract_version: str = CONTRACT_VERSION


def _readonly_input(
    values: np.ndarray,
    *,
    name: str,
    dtype: Any,
    shape: tuple[int, ...],
) -> np.ndarray:
    if not isinstance(values, np.ndarray):
        raise ValueError(f"{name} must be a NumPy array")
    if values.dtype != np.dtype(dtype):
        raise ValueError(f"{name} must have dtype {np.dtype(dtype)}")
    if values.shape != shape:
        raise ValueError(f"{name} must have shape {shape}")
    if not values.flags.c_contiguous:
        raise ValueError(f"{name} must be C-contiguous")
    if values.flags.writeable:
        raise ValueError(f"{name} must be read-only")
    return values


def _mutable_state_array(
    values: np.ndarray,
    *,
    name: str,
    dtype: Any,
    shape: tuple[int, ...],
) -> np.ndarray:
    if not isinstance(values, np.ndarray):
        raise ValueError(f"{name} must be a NumPy array")
    if values.dtype != np.dtype(dtype):
        raise ValueError(f"{name} must have dtype {np.dtype(dtype)}")
    if values.shape != shape:
        raise ValueError(f"{name} must have shape {shape}")
    if not values.flags.c_contiguous or not values.flags.owndata:
        raise ValueError(f"{name} must be an owned C-contiguous array")
    if not values.flags.writeable:
        raise ValueError(f"{name} must be writeable before native execution")
    return values


@dataclass(frozen=True, slots=True)
class OrderedSummaryBatch:
    offsets: np.ndarray
    face_indices: np.ndarray
    incoming_power_lumen: np.ndarray
    reflected_power_lumen: np.ndarray
    emitted_power_lumen: np.ndarray
    status_flags: np.ndarray
    lobe_codes: np.ndarray
    terminal_kind_codes: np.ndarray
    terminal_depths: np.ndarray
    terminal_current_power_lumen: np.ndarray
    terminal_receiver_indices: np.ndarray
    terminal_rows: np.ndarray
    terminal_columns: np.ndarray
    terminal_received_power_lumen: np.ndarray
    terminal_received_power_squared_lumen2: np.ndarray
    face_profile_slots: np.ndarray
    profile_unassigned: np.ndarray
    receiver_columns: np.ndarray
    grid_offsets: np.ndarray
    max_depth: int

    def __post_init__(self) -> None:
        primary_count = len(self.terminal_kind_codes)
        event_count = len(self.face_indices)
        profile_count = len(self.profile_unassigned)
        receiver_count = len(self.receiver_columns)
        arrays = (
            (self.offsets, "offsets", np.int64, (primary_count + 1,)),
            (self.face_indices, "face_indices", np.int64, (event_count,)),
            (
                self.incoming_power_lumen,
                "incoming_power_lumen",
                np.float64,
                (event_count,),
            ),
            (
                self.reflected_power_lumen,
                "reflected_power_lumen",
                np.float64,
                (event_count,),
            ),
            (
                self.emitted_power_lumen,
                "emitted_power_lumen",
                np.float64,
                (event_count,),
            ),
            (self.status_flags, "status_flags", np.uint16, (event_count,)),
            (self.lobe_codes, "lobe_codes", np.int8, (event_count,)),
            (
                self.terminal_kind_codes,
                "terminal_kind_codes",
                np.int8,
                (primary_count,),
            ),
            (
                self.terminal_depths,
                "terminal_depths",
                np.int16,
                (primary_count,),
            ),
            (
                self.terminal_current_power_lumen,
                "terminal_current_power_lumen",
                np.float64,
                (primary_count,),
            ),
            (
                self.terminal_receiver_indices,
                "terminal_receiver_indices",
                np.int32,
                (primary_count,),
            ),
            (self.terminal_rows, "terminal_rows", np.int32, (primary_count,)),
            (
                self.terminal_columns,
                "terminal_columns",
                np.int32,
                (primary_count,),
            ),
            (
                self.terminal_received_power_lumen,
                "terminal_received_power_lumen",
                np.float64,
                (primary_count,),
            ),
            (
                self.terminal_received_power_squared_lumen2,
                "terminal_received_power_squared_lumen2",
                np.float64,
                (primary_count,),
            ),
            (
                self.face_profile_slots,
                "face_profile_slots",
                np.int32,
                (len(self.face_profile_slots),),
            ),
            (
                self.profile_unassigned,
                "profile_unassigned",
                np.bool_,
                (profile_count,),
            ),
            (
                self.receiver_columns,
                "receiver_columns",
                np.int32,
                (receiver_count,),
            ),
            (
                self.grid_offsets,
                "grid_offsets",
                np.int64,
                (receiver_count + 1,),
            ),
        )
        storage_ranges = []
        for values, name, dtype, shape in arrays:
            values = _readonly_input(values, name=name, dtype=dtype, shape=shape)
            if not values.flags.owndata:
                raise ValueError(f"{name} must own its input storage")
            if values.nbytes:
                start = int(values.__array_interface__["data"][0])
                storage_ranges.append((start, start + int(values.nbytes), name))
        storage_ranges.sort()
        for previous, current in zip(storage_ranges, storage_ranges[1:]):
            if current[0] < previous[1]:
                raise ValueError(
                    f"{previous[2]} and {current[2]} must not share storage"
                )

        if isinstance(self.max_depth, bool) or not isinstance(self.max_depth, Integral):
            raise ValueError("max_depth must be an integer")
        max_depth = int(self.max_depth)
        if max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        object.__setattr__(self, "max_depth", max_depth)
        if int(self.offsets[0]) != 0 or np.any(self.offsets[1:] < self.offsets[:-1]):
            raise ValueError("offsets must be monotonic and start at zero")
        if int(self.offsets[-1]) != event_count:
            raise ValueError("offsets must cover every event")
        if np.any(self.terminal_depths < 0) or np.any(
            self.terminal_depths > max_depth
        ):
            raise ValueError("terminal_depths are outside the configured range")
        if np.any(
            ~np.isin(
                self.terminal_kind_codes,
                np.asarray(
                    [TERMINAL_RECEIVER, TERMINAL_ESCAPED, TERMINAL_BLOCKED],
                    dtype=np.int8,
                ),
            )
        ):
            raise ValueError("terminal_kind_codes contains an unknown terminal")
        valid_status = np.asarray(
            [
                STATUS_DEPTH_LIMITED | STATUS_DISABLED,
                STATUS_ATTEMPTED | STATUS_BELOW_ENERGY,
                STATUS_ATTEMPTED
                | STATUS_BELOW_ENERGY
                | STATUS_ROULETTE_TERMINATED,
                STATUS_ATTEMPTED
                | STATUS_ROULETTE_SURVIVED
                | STATUS_DISABLED,
                STATUS_ATTEMPTED
                | STATUS_ROULETTE_SURVIVED
                | STATUS_EMITTED,
                STATUS_ATTEMPTED | STATUS_DISABLED,
                STATUS_ATTEMPTED | STATUS_EMITTED,
            ],
            dtype=np.uint16,
        )
        if np.any(~np.isin(self.status_flags, valid_status)):
            raise ValueError("status_flags contains an invalid planner outcome")
        emitted = (self.status_flags & np.uint16(STATUS_EMITTED)) != 0
        if (
            np.any(self.lobe_codes[emitted] < LOBE_SPECULAR)
            or np.any(self.lobe_codes[emitted] > LOBE_GAUSSIAN)
            or np.any(self.lobe_codes[~emitted] != -1)
        ):
            raise ValueError("lobe_codes violates the emitted-status contract")
        if np.any((~emitted) & (self.emitted_power_lumen != 0.0)):
            raise ValueError("non-emitted events must have zero emitted power")
        event_counts = self.offsets[1:] - self.offsets[:-1]
        blocked = self.terminal_kind_codes == TERMINAL_BLOCKED
        expected_event_counts = self.terminal_depths.astype(np.int64) + blocked
        if not np.array_equal(event_counts, expected_event_counts):
            raise ValueError("event count and terminal depth are inconsistent")
        nonempty_slots = np.flatnonzero(event_counts > 0)
        blocked_lasts = (
            self.offsets[1:][nonempty_slots[blocked[nonempty_slots]]] - 1
        )
        if (
            event_count - int(np.count_nonzero(emitted))
            != len(blocked_lasts)
            or (len(blocked_lasts) and np.any(emitted[blocked_lasts]))
        ):
            raise ValueError("surface emission order is inconsistent")
        if event_count and (
            np.any(self.face_indices < 0)
            or np.any(self.face_indices >= len(self.face_profile_slots))
        ):
            raise ValueError("face_indices are outside face_profile_slots")
        if len(self.face_profile_slots) and (
            np.any(self.face_profile_slots < 0)
            or np.any(self.face_profile_slots >= profile_count)
        ):
            raise ValueError("face_profile_slots contains an unknown profile slot")
        if int(self.grid_offsets[0]) != 0 or np.any(
            self.grid_offsets[1:] < self.grid_offsets[:-1]
        ):
            raise ValueError("grid_offsets must be monotonic and start at zero")
        if receiver_count and np.any(self.receiver_columns <= 0):
            raise ValueError("receiver_columns must be positive")
        for receiver_index in range(receiver_count):
            cell_count = int(
                self.grid_offsets[receiver_index + 1]
                - self.grid_offsets[receiver_index]
            )
            if cell_count % int(self.receiver_columns[receiver_index]) != 0:
                raise ValueError("receiver grid size must be divisible by its columns")
        receiver_mask = self.terminal_kind_codes == TERMINAL_RECEIVER
        receiver_indices = self.terminal_receiver_indices[receiver_mask]
        if len(receiver_indices) and (
            np.any(receiver_indices < 0)
            or np.any(receiver_indices >= receiver_count)
        ):
            raise ValueError("receiver terminal contains an unknown receiver")
        receiver_slots = np.flatnonzero(receiver_mask)
        for primary_slot in receiver_slots:
            receiver_index = int(self.terminal_receiver_indices[primary_slot])
            columns = int(self.receiver_columns[receiver_index])
            rows = int(
                (
                    self.grid_offsets[receiver_index + 1]
                    - self.grid_offsets[receiver_index]
                )
                // columns
            )
            if (
                int(self.terminal_rows[primary_slot]) < 0
                or int(self.terminal_rows[primary_slot]) >= rows
                or int(self.terminal_columns[primary_slot]) < 0
                or int(self.terminal_columns[primary_slot]) >= columns
            ):
                raise ValueError("receiver terminal contains an invalid grid cell")
        expected_squared = np.zeros(primary_count, dtype=np.float64)
        for primary_slot_value in receiver_slots:
            primary_slot = int(primary_slot_value)
            expected_squared[primary_slot] = (
                float(self.terminal_received_power_lumen[primary_slot]) ** 2
            )
        if not np.array_equal(
            self.terminal_received_power_squared_lumen2.view(np.uint64),
            expected_squared.view(np.uint64),
        ):
            raise ValueError("receiver squared power violates the Python contract")
        float_arrays = (
            self.incoming_power_lumen,
            self.reflected_power_lumen,
            self.emitted_power_lumen,
            self.terminal_current_power_lumen,
            self.terminal_received_power_lumen,
            self.terminal_received_power_squared_lumen2,
        )
        if any(not np.all(np.isfinite(values)) for values in float_arrays):
            raise ValueError("ordered reducer inputs must be finite")
        if any(np.any(values < 0.0) for values in float_arrays):
            raise ValueError("ordered reducer power inputs must be non-negative")

    @property
    def primary_count(self) -> int:
        return len(self.terminal_kind_codes)

    @property
    def event_count(self) -> int:
        return len(self.face_indices)

    @property
    def profile_count(self) -> int:
        return len(self.profile_unassigned)

    @property
    def receiver_count(self) -> int:
        return len(self.receiver_columns)

    @property
    def depth_count(self) -> int:
        return self.max_depth + 1

    @property
    def grid_cell_count(self) -> int:
        return int(self.grid_offsets[-1])


@dataclass(frozen=True, slots=True)
class OrderedSummaryAccumulator:
    optical_counts: np.ndarray
    profile_hit_counts: np.ndarray
    profile_incoming_flux_lumen: np.ndarray
    profile_reflected_flux_lumen: np.ndarray
    profile_seen: np.ndarray
    reflection_counts: np.ndarray
    reflection_flux_lumen: np.ndarray
    reflection_lobe_counts: np.ndarray
    reflection_lobe_flux_lumen: np.ndarray
    reflection_depth_counts: np.ndarray
    reflection_depth_flux_lumen: np.ndarray
    reflection_depth_seen: np.ndarray
    contribution_receiver_counts: np.ndarray
    contribution_receiver_flux_lumen: np.ndarray
    contribution_lobe_counts: np.ndarray
    contribution_lobe_flux_lumen: np.ndarray
    contribution_depth_counts: np.ndarray
    contribution_depth_flux_lumen: np.ndarray
    contribution_depth_seen: np.ndarray
    receiver_counts: np.ndarray
    receiver_flux_lumen: np.ndarray
    receiver_depth_counts: np.ndarray
    receiver_depth_flux_lumen: np.ndarray
    receiver_depth_seen: np.ndarray
    grid_flux_lumen: np.ndarray
    grid_flux_squared_lumen2: np.ndarray
    grid_hit_counts: np.ndarray
    grid_flux_squared_totals_lumen2: np.ndarray

    def validate_mutable(self, batch: OrderedSummaryBatch) -> None:
        self._validate(batch, require_writeable=True)

    def validate_readonly(self, batch: OrderedSummaryBatch) -> None:
        self._validate(batch, require_writeable=False)

    def _validate(
        self,
        batch: OrderedSummaryBatch,
        *,
        require_writeable: bool,
    ) -> None:
        p = batch.profile_count
        d = batch.depth_count
        r = batch.receiver_count
        g = batch.grid_cell_count
        specifications = (
            ("optical_counts", np.int64, (OPTICAL_COUNT_SIZE,)),
            ("profile_hit_counts", np.int64, (p,)),
            ("profile_incoming_flux_lumen", np.float64, (p,)),
            ("profile_reflected_flux_lumen", np.float64, (p,)),
            ("profile_seen", np.bool_, (p,)),
            ("reflection_counts", np.int64, (REF_COUNT_SIZE,)),
            ("reflection_flux_lumen", np.float64, (REF_FLUX_SIZE,)),
            ("reflection_lobe_counts", np.int64, (LOBE_COUNT, OUTCOME_SIZE)),
            ("reflection_lobe_flux_lumen", np.float64, (LOBE_COUNT, 2)),
            ("reflection_depth_counts", np.int64, (d, REF_DEPTH_SIZE)),
            ("reflection_depth_flux_lumen", np.float64, (d, 2)),
            ("reflection_depth_seen", np.bool_, (d,)),
            (
                "contribution_receiver_counts",
                np.int64,
                (CONTRIBUTION_RECEIVER_SIZE,),
            ),
            (
                "contribution_receiver_flux_lumen",
                np.float64,
                (CONTRIBUTION_RECEIVER_SIZE,),
            ),
            ("contribution_lobe_counts", np.int64, (LOBE_COUNT, OUTCOME_SIZE)),
            (
                "contribution_lobe_flux_lumen",
                np.float64,
                (LOBE_COUNT, OUTCOME_SIZE),
            ),
            (
                "contribution_depth_counts",
                np.int64,
                (d, CONTRIBUTION_DEPTH_SIZE),
            ),
            (
                "contribution_depth_flux_lumen",
                np.float64,
                (d, CONTRIBUTION_DEPTH_SIZE),
            ),
            ("contribution_depth_seen", np.bool_, (d,)),
            ("receiver_counts", np.int64, (r, RECEIVER_FIELD_SIZE)),
            ("receiver_flux_lumen", np.float64, (r, RECEIVER_FIELD_SIZE)),
            ("receiver_depth_counts", np.int64, (r, d)),
            ("receiver_depth_flux_lumen", np.float64, (r, d)),
            ("receiver_depth_seen", np.bool_, (r, d)),
            ("grid_flux_lumen", np.float64, (g,)),
            ("grid_flux_squared_lumen2", np.float64, (g,)),
            ("grid_hit_counts", np.int64, (r,)),
            ("grid_flux_squared_totals_lumen2", np.float64, (r,)),
        )
        storage_ranges = []
        for name, dtype, shape in specifications:
            if require_writeable:
                values = _mutable_state_array(
                    getattr(self, name),
                    name=name,
                    dtype=dtype,
                    shape=shape,
                )
            else:
                values = _readonly_input(
                    getattr(self, name),
                    name=name,
                    dtype=dtype,
                    shape=shape,
                )
                if not values.flags.owndata:
                    raise ValueError(f"{name} must own its result storage")
            if values.nbytes:
                start = int(values.__array_interface__["data"][0])
                storage_ranges.append((start, start + int(values.nbytes), name))
        storage_ranges.sort()
        for previous, current in zip(storage_ranges, storage_ranges[1:]):
            if current[0] < previous[1]:
                raise ValueError(
                    f"{previous[2]} and {current[2]} must not share storage"
                )
        count_arrays = tuple(
            getattr(self, name)
            for name in (
                "optical_counts",
                "profile_hit_counts",
                "reflection_counts",
                "reflection_lobe_counts",
                "reflection_depth_counts",
                "contribution_receiver_counts",
                "contribution_lobe_counts",
                "contribution_depth_counts",
                "receiver_counts",
                "receiver_depth_counts",
                "grid_hit_counts",
            )
        )
        if any(np.any(values < 0) for values in count_arrays):
            raise ValueError("ordered reducer count state must be non-negative")
        float_arrays = tuple(
            getattr(self, field.name)
            for field in fields(self)
            if isinstance(getattr(self, field.name), np.ndarray)
            and getattr(self, field.name).dtype == np.dtype(np.float64)
        )
        if any(not np.all(np.isfinite(values)) for values in float_arrays):
            raise ValueError("ordered reducer float state must be finite")
        if any(np.any(values < 0.0) for values in float_arrays):
            raise ValueError("ordered reducer float state must be non-negative")

    def freeze(self) -> None:
        for field in fields(self):
            values = getattr(self, field.name)
            if isinstance(values, np.ndarray):
                values.setflags(write=False)


@dataclass(frozen=True, slots=True)
class OrderedSummaryResult:
    state: OrderedSummaryAccumulator
    profile_first_touch_slots: np.ndarray
    profile_first_touch_faces: np.ndarray
    reflection_depth_first_touch: np.ndarray
    contribution_depth_first_touch: np.ndarray
    receiver_depth_first_touch_receivers: np.ndarray
    receiver_depth_first_touch_depths: np.ndarray
    receiver_hit_count: int
    surface_hit_count: int
    terminated_ray_count: int


@dataclass(frozen=True, slots=True)
class NativeCpuOrderedReducerExecution:
    result: OrderedSummaryResult
    jit_compile_sec: float
    execute_sec: float
    result_validation_sec: float
    numba_version: str
    result_digest: str
    contract_version: str = CONTRACT_VERSION


_STATE_LOCK = threading.RLock()
_CAPABILITY: Optional[NativeCpuOrderedReducerCapability] = None
_KERNEL: Optional[Callable[..., Any]] = None
_KERNEL_COMPILED = False


def probe_native_cpu_ordered_reducer() -> NativeCpuOrderedReducerCapability:
    global _CAPABILITY
    if _CAPABILITY is not None:
        return _CAPABILITY
    with _STATE_LOCK:
        if _CAPABILITY is not None:
            return _CAPABILITY
        try:
            numba = importlib.import_module("numba")
        except ModuleNotFoundError:
            _CAPABILITY = NativeCpuOrderedReducerCapability(
                False,
                "numba_not_installed",
                None,
            )
        except Exception:
            _CAPABILITY = NativeCpuOrderedReducerCapability(
                False,
                "numba_import_failed",
                None,
            )
        else:
            _CAPABILITY = NativeCpuOrderedReducerCapability(
                True,
                None,
                str(getattr(numba, "__version__", "unknown")),
            )
        return _CAPABILITY


def reduce_ordered_summary_native_cpu(
    batch: OrderedSummaryBatch,
    state: OrderedSummaryAccumulator,
) -> NativeCpuOrderedReducerExecution:
    if not isinstance(batch, OrderedSummaryBatch):
        raise TypeError("batch must be an OrderedSummaryBatch")
    if not isinstance(state, OrderedSummaryAccumulator):
        raise TypeError("state must be an OrderedSummaryAccumulator")
    try:
        state.validate_mutable(batch)
        _validate_batch_state_no_alias(batch, state)
        _validate_count_headroom(batch, state)
    except Exception as exc:
        raise NativeCpuOrderedReducerProviderError(
            "input_prepare",
            "invalid_accumulator_state",
        ) from exc
    capability = probe_native_cpu_ordered_reducer()
    if not capability.available:
        raise NativeCpuOrderedReducerUnavailable(
            capability.reason_code or "numba_unavailable"
        )
    kernel, jit_compile_sec = _ensure_kernel()
    state = _copy_accumulator(state)
    validation_state = _copy_accumulator(state)
    baseline = _count_baseline(state)
    float_baseline = _float_baseline(state)
    profile_touch_slots = np.empty(batch.profile_count, dtype=np.int32)
    profile_touch_faces = np.empty(batch.profile_count, dtype=np.int64)
    reflection_depth_touch = np.empty(batch.depth_count, dtype=np.int16)
    contribution_depth_touch = np.empty(batch.depth_count, dtype=np.int16)
    receiver_depth_touch_receivers = np.empty(
        batch.receiver_count * batch.depth_count,
        dtype=np.int32,
    )
    receiver_depth_touch_depths = np.empty(
        batch.receiver_count * batch.depth_count,
        dtype=np.int16,
    )
    started = time.perf_counter()
    try:
        counts = kernel(
            batch.offsets,
            batch.face_indices,
            batch.incoming_power_lumen,
            batch.reflected_power_lumen,
            batch.emitted_power_lumen,
            batch.status_flags,
            batch.lobe_codes,
            batch.terminal_kind_codes,
            batch.terminal_depths,
            batch.terminal_current_power_lumen,
            batch.terminal_receiver_indices,
            batch.terminal_rows,
            batch.terminal_columns,
            batch.terminal_received_power_lumen,
            batch.terminal_received_power_squared_lumen2,
            batch.face_profile_slots,
            batch.profile_unassigned,
            batch.receiver_columns,
            batch.grid_offsets,
            state.optical_counts,
            state.profile_hit_counts,
            state.profile_incoming_flux_lumen,
            state.profile_reflected_flux_lumen,
            state.profile_seen,
            state.reflection_counts,
            state.reflection_flux_lumen,
            state.reflection_lobe_counts,
            state.reflection_lobe_flux_lumen,
            state.reflection_depth_counts,
            state.reflection_depth_flux_lumen,
            state.reflection_depth_seen,
            state.contribution_receiver_counts,
            state.contribution_receiver_flux_lumen,
            state.contribution_lobe_counts,
            state.contribution_lobe_flux_lumen,
            state.contribution_depth_counts,
            state.contribution_depth_flux_lumen,
            state.contribution_depth_seen,
            state.receiver_counts,
            state.receiver_flux_lumen,
            state.receiver_depth_counts,
            state.receiver_depth_flux_lumen,
            state.receiver_depth_seen,
            state.grid_flux_lumen,
            state.grid_flux_squared_lumen2,
            state.grid_hit_counts,
            state.grid_flux_squared_totals_lumen2,
            validation_state.profile_incoming_flux_lumen,
            validation_state.profile_reflected_flux_lumen,
            validation_state.reflection_flux_lumen,
            validation_state.reflection_lobe_flux_lumen,
            validation_state.reflection_depth_flux_lumen,
            validation_state.contribution_receiver_flux_lumen,
            validation_state.contribution_lobe_flux_lumen,
            validation_state.contribution_depth_flux_lumen,
            validation_state.receiver_flux_lumen,
            validation_state.receiver_depth_flux_lumen,
            validation_state.grid_flux_lumen,
            validation_state.grid_flux_squared_lumen2,
            validation_state.grid_flux_squared_totals_lumen2,
            profile_touch_slots,
            profile_touch_faces,
            reflection_depth_touch,
            contribution_depth_touch,
            receiver_depth_touch_receivers,
            receiver_depth_touch_depths,
        )
    except Exception as exc:
        raise NativeCpuOrderedReducerProviderError(
            "execute",
            "numba_execute_failed",
            jit_compile_sec=jit_compile_sec,
            execute_sec=time.perf_counter() - started,
            numba_version=capability.numba_version,
        ) from exc
    execute_sec = time.perf_counter() - started
    validation_started = time.perf_counter()
    try:
        (
            profile_touch_count,
            reflection_depth_touch_count,
            contribution_depth_touch_count,
            receiver_depth_touch_count,
            receiver_hit_count,
            terminated_ray_count,
        ) = (int(value) for value in counts)
        _validate_float_shadow(state, validation_state)
        _validate_float_reference(batch, float_baseline, state)
        result = OrderedSummaryResult(
            state=state,
            profile_first_touch_slots=_readonly_copy(
                profile_touch_slots[:profile_touch_count],
                np.int32,
            ),
            profile_first_touch_faces=_readonly_copy(
                profile_touch_faces[:profile_touch_count],
                np.int64,
            ),
            reflection_depth_first_touch=_readonly_copy(
                reflection_depth_touch[:reflection_depth_touch_count],
                np.int16,
            ),
            contribution_depth_first_touch=_readonly_copy(
                contribution_depth_touch[:contribution_depth_touch_count],
                np.int16,
            ),
            receiver_depth_first_touch_receivers=_readonly_copy(
                receiver_depth_touch_receivers[:receiver_depth_touch_count],
                np.int32,
            ),
            receiver_depth_first_touch_depths=_readonly_copy(
                receiver_depth_touch_depths[:receiver_depth_touch_count],
                np.int16,
            ),
            receiver_hit_count=receiver_hit_count,
            surface_hit_count=batch.event_count,
            terminated_ray_count=terminated_ray_count,
        )
        _validate_result(batch, baseline, result)
        state.freeze()
        _validate_result_storage(result)
        result_digest = _result_digest(result)
    except Exception as exc:
        raise NativeCpuOrderedReducerProviderError(
            "result_validation",
            "numba_invalid_result",
            jit_compile_sec=jit_compile_sec,
            execute_sec=execute_sec,
            result_validation_sec=time.perf_counter() - validation_started,
            numba_version=capability.numba_version,
        ) from exc
    result_validation_sec = time.perf_counter() - validation_started
    return NativeCpuOrderedReducerExecution(
        result=result,
        jit_compile_sec=jit_compile_sec,
        execute_sec=execute_sec,
        result_validation_sec=result_validation_sec,
        numba_version=capability.numba_version or "unknown",
        result_digest=result_digest,
    )


def _make_kernel() -> Callable[..., Any]:
    numba = importlib.import_module("numba")

    @numba.njit(inline="always", fastmath=False)
    def touch_depth(seen, touch_order, touch_count, depth):
        if not seen[depth]:
            seen[depth] = True
            touch_order[touch_count] = depth
            return touch_count + 1
        return touch_count

    @numba.njit(inline="always", fastmath=False)
    def touch_receiver_depth(
        seen,
        receiver_touch_order,
        depth_touch_order,
        touch_count,
        receiver_index,
        depth,
    ):
        if not seen[receiver_index, depth]:
            seen[receiver_index, depth] = True
            receiver_touch_order[touch_count] = receiver_index
            depth_touch_order[touch_count] = depth
            return touch_count + 1
        return touch_count

    @numba.njit(inline="always", fastmath=False)
    def record_reflection_outcome(
        lobe,
        outcome,
        depth,
        received_power,
        reflection_counts,
        reflection_flux,
        reflection_lobe_counts,
        reflection_lobe_flux,
        reflection_depth_counts,
        reflection_depth_flux,
        validation_reflection_flux,
        validation_reflection_lobe_flux,
        validation_reflection_depth_flux,
        reflection_depth_seen,
        reflection_depth_touch,
        reflection_touch_count,
    ):
        if outcome == OUTCOME_RECEIVER:
            reflection_counts[REF_RECEIVER_HIT_COUNT] += 1
            reflection_flux[REF_REFLECTED_RECEIVER_FLUX] += received_power
            reflection_lobe_flux[lobe, 1] += received_power
            reflection_depth_flux[depth, 1] += received_power
            validation_reflection_flux[REF_REFLECTED_RECEIVER_FLUX] += received_power
            validation_reflection_lobe_flux[lobe, 1] += received_power
            validation_reflection_depth_flux[depth, 1] += received_power
        elif outcome == OUTCOME_BLOCKED:
            reflection_counts[REF_BLOCKED_COUNT] += 1
        elif outcome == OUTCOME_CONTINUED:
            reflection_counts[REF_CONTINUED_COUNT] += 1
        else:
            reflection_counts[REF_ESCAPED_COUNT] += 1
        reflection_lobe_counts[lobe, outcome] += 1
        reflection_touch_count = touch_depth(
            reflection_depth_seen,
            reflection_depth_touch,
            reflection_touch_count,
            depth,
        )
        reflection_depth_counts[depth, outcome] += 1
        return reflection_touch_count

    @numba.njit(inline="always", fastmath=False)
    def record_contribution_outcome(
        lobe,
        outcome,
        depth,
        flux,
        contribution_lobe_counts,
        contribution_lobe_flux,
        contribution_depth_counts,
        contribution_depth_flux,
        validation_contribution_lobe_flux,
        validation_contribution_depth_flux,
        contribution_depth_seen,
        contribution_depth_touch,
        contribution_touch_count,
    ):
        contribution_lobe_counts[lobe, outcome] += 1
        contribution_lobe_flux[lobe, outcome] += flux
        validation_contribution_lobe_flux[lobe, outcome] += flux
        contribution_touch_count = touch_depth(
            contribution_depth_seen,
            contribution_depth_touch,
            contribution_touch_count,
            depth,
        )
        depth_field = outcome + 1
        contribution_depth_counts[depth, depth_field] += 1
        contribution_depth_flux[depth, depth_field] += flux
        validation_contribution_depth_flux[depth, depth_field] += flux
        return contribution_touch_count

    @numba.njit(nogil=True, fastmath=False)
    def reduce_kernel(
        offsets,
        face_indices,
        incoming_power,
        reflected_power,
        emitted_power,
        status_flags,
        lobe_codes,
        terminal_kind_codes,
        terminal_depths,
        terminal_current_power,
        terminal_receiver_indices,
        terminal_rows,
        terminal_columns,
        terminal_received_power,
        terminal_received_power_squared,
        face_profile_slots,
        profile_unassigned,
        receiver_columns,
        grid_offsets,
        optical_counts,
        profile_hit_counts,
        profile_incoming_flux,
        profile_reflected_flux,
        profile_seen,
        reflection_counts,
        reflection_flux,
        reflection_lobe_counts,
        reflection_lobe_flux,
        reflection_depth_counts,
        reflection_depth_flux,
        reflection_depth_seen,
        contribution_receiver_counts,
        contribution_receiver_flux,
        contribution_lobe_counts,
        contribution_lobe_flux,
        contribution_depth_counts,
        contribution_depth_flux,
        contribution_depth_seen,
        receiver_counts,
        receiver_flux,
        receiver_depth_counts,
        receiver_depth_flux,
        receiver_depth_seen,
        grid_flux,
        grid_flux_squared,
        grid_hit_counts,
        grid_flux_squared_totals,
        validation_profile_incoming_flux,
        validation_profile_reflected_flux,
        validation_reflection_flux,
        validation_reflection_lobe_flux,
        validation_reflection_depth_flux,
        validation_contribution_receiver_flux,
        validation_contribution_lobe_flux,
        validation_contribution_depth_flux,
        validation_receiver_flux,
        validation_receiver_depth_flux,
        validation_grid_flux,
        validation_grid_flux_squared,
        validation_grid_flux_squared_totals,
        profile_touch_slots,
        profile_touch_faces,
        reflection_depth_touch,
        contribution_depth_touch,
        receiver_depth_touch_receivers,
        receiver_depth_touch_depths,
    ):
        profile_touch_count = 0
        reflection_touch_count = 0
        contribution_touch_count = 0
        receiver_depth_touch_count = 0
        receiver_hit_count = 0
        terminated_ray_count = 0

        for primary_slot in range(terminal_kind_codes.shape[0]):
            start = offsets[primary_slot]
            end = offsets[primary_slot + 1]
            terminal_depth = terminal_depths[primary_slot]
            if terminal_depth > reflection_counts[REF_MAX_OBSERVED_DEPTH]:
                reflection_counts[REF_MAX_OBSERVED_DEPTH] = terminal_depth
            if end > start:
                reflection_counts[REF_PRIMARY_SURFACE_HIT_COUNT] += 1

            for event_index in range(start, end):
                depth = event_index - start
                face_index = face_indices[event_index]
                profile_slot = face_profile_slots[face_index]
                row_incoming_power = incoming_power[event_index]
                row_reflected_power = reflected_power[event_index]
                optical_counts[OPTICAL_SURFACE_HIT_COUNT] += 1
                if profile_unassigned[profile_slot]:
                    optical_counts[OPTICAL_UNASSIGNED_SURFACE_HIT_COUNT] += 1
                if not profile_seen[profile_slot]:
                    profile_seen[profile_slot] = True
                    profile_touch_slots[profile_touch_count] = profile_slot
                    profile_touch_faces[profile_touch_count] = face_index
                    profile_touch_count += 1
                profile_hit_counts[profile_slot] += 1
                profile_incoming_flux[profile_slot] += row_incoming_power
                profile_reflected_flux[profile_slot] += row_reflected_power
                validation_profile_incoming_flux[profile_slot] += row_incoming_power
                validation_profile_reflected_flux[profile_slot] += row_reflected_power

                reflection_counts[REF_SURFACE_HIT_COUNT] += 1
                flags = status_flags[event_index]
                if flags & STATUS_ATTEMPTED:
                    reflection_counts[REF_ATTEMPT_COUNT] += 1
                if flags & STATUS_DEPTH_LIMITED:
                    reflection_counts[REF_DEPTH_LIMIT_COUNT] += 1
                if flags & STATUS_BELOW_ENERGY:
                    reflection_counts[REF_BELOW_ENERGY_COUNT] += 1
                if flags & STATUS_ROULETTE_TERMINATED:
                    reflection_counts[REF_ROULETTE_TERMINATED_COUNT] += 1
                if flags & STATUS_ROULETTE_SURVIVED:
                    reflection_counts[REF_ROULETTE_SURVIVED_COUNT] += 1
                if flags & STATUS_DISABLED:
                    reflection_counts[REF_DISABLED_COUNT] += 1

                emitted = bool(flags & STATUS_EMITTED)
                if depth > 0:
                    previous_lobe = lobe_codes[event_index - 1]
                    outcome = OUTCOME_CONTINUED if emitted else OUTCOME_BLOCKED
                    reflection_touch_count = record_reflection_outcome(
                        previous_lobe,
                        outcome,
                        depth,
                        0.0,
                        reflection_counts,
                        reflection_flux,
                        reflection_lobe_counts,
                        reflection_lobe_flux,
                        reflection_depth_counts,
                        reflection_depth_flux,
                        validation_reflection_flux,
                        validation_reflection_lobe_flux,
                        validation_reflection_depth_flux,
                        reflection_depth_seen,
                        reflection_depth_touch,
                        reflection_touch_count,
                    )
                    contribution_touch_count = record_contribution_outcome(
                        previous_lobe,
                        outcome,
                        depth,
                        row_incoming_power,
                        contribution_lobe_counts,
                        contribution_lobe_flux,
                        contribution_depth_counts,
                        contribution_depth_flux,
                        validation_contribution_lobe_flux,
                        validation_contribution_depth_flux,
                        contribution_depth_seen,
                        contribution_depth_touch,
                        contribution_touch_count,
                    )
                if not emitted:
                    break

                lobe = lobe_codes[event_index]
                next_depth = depth + 1
                row_emitted_power = emitted_power[event_index]
                reflection_counts[REF_EMITTED_COUNT] += 1
                reflection_lobe_counts[lobe, OUTCOME_EMITTED] += 1
                reflection_lobe_flux[lobe, 0] += row_emitted_power
                validation_reflection_lobe_flux[lobe, 0] += row_emitted_power
                reflection_touch_count = touch_depth(
                    reflection_depth_seen,
                    reflection_depth_touch,
                    reflection_touch_count,
                    next_depth,
                )
                reflection_depth_counts[next_depth, REF_DEPTH_EMITTED] += 1
                reflection_depth_flux[next_depth, 0] += row_emitted_power
                validation_reflection_depth_flux[next_depth, 0] += row_emitted_power

                contribution_lobe_counts[lobe, OUTCOME_EMITTED] += 1
                contribution_lobe_flux[lobe, OUTCOME_EMITTED] += row_emitted_power
                validation_contribution_lobe_flux[
                    lobe, OUTCOME_EMITTED
                ] += row_emitted_power
                contribution_touch_count = touch_depth(
                    contribution_depth_seen,
                    contribution_depth_touch,
                    contribution_touch_count,
                    next_depth,
                )
                contribution_depth_counts[
                    next_depth, CONTRIBUTION_DEPTH_EMITTED
                ] += 1
                contribution_depth_flux[
                    next_depth, CONTRIBUTION_DEPTH_EMITTED
                ] += row_emitted_power
                validation_contribution_depth_flux[
                    next_depth, CONTRIBUTION_DEPTH_EMITTED
                ] += row_emitted_power

            terminal_kind = terminal_kind_codes[primary_slot]
            if terminal_kind == TERMINAL_RECEIVER:
                receiver_hit_count += 1
                receiver_index = terminal_receiver_indices[primary_slot]
                received_power = terminal_received_power[primary_slot]
                received_square = terminal_received_power_squared[primary_slot]
                row = terminal_rows[primary_slot]
                column = terminal_columns[primary_slot]
                grid_index = (
                    grid_offsets[receiver_index]
                    + row * receiver_columns[receiver_index]
                    + column
                )
                grid_flux[grid_index] += received_power
                validation_grid_flux[grid_index] += received_power
                grid_hit_counts[receiver_index] += 1
                grid_flux_squared_totals[receiver_index] += received_square
                validation_grid_flux_squared_totals[receiver_index] += received_square
                grid_flux_squared[grid_index] += received_square
                validation_grid_flux_squared[grid_index] += received_square

                if terminal_depth == 0:
                    reflection_counts[REF_DIRECT_RECEIVER_HIT_COUNT] += 1
                    reflection_flux[REF_DIRECT_RECEIVER_FLUX] += received_power
                    validation_reflection_flux[
                        REF_DIRECT_RECEIVER_FLUX
                    ] += received_power
                    contribution_receiver_counts[
                        CONTRIBUTION_DIRECT_RECEIVER
                    ] += 1
                    contribution_receiver_flux[
                        CONTRIBUTION_DIRECT_RECEIVER
                    ] += received_power
                    validation_contribution_receiver_flux[
                        CONTRIBUTION_DIRECT_RECEIVER
                    ] += received_power
                    receiver_counts[receiver_index, RECEIVER_DIRECT] += 1
                    receiver_flux[receiver_index, RECEIVER_DIRECT] += received_power
                    validation_receiver_flux[
                        receiver_index, RECEIVER_DIRECT
                    ] += received_power
                    receiver_counts[receiver_index, RECEIVER_TOTAL] += 1
                    receiver_flux[receiver_index, RECEIVER_TOTAL] += received_power
                    validation_receiver_flux[
                        receiver_index, RECEIVER_TOTAL
                    ] += received_power
                    receiver_depth_touch_count = touch_receiver_depth(
                        receiver_depth_seen,
                        receiver_depth_touch_receivers,
                        receiver_depth_touch_depths,
                        receiver_depth_touch_count,
                        receiver_index,
                        0,
                    )
                    receiver_depth_counts[receiver_index, 0] += 1
                    receiver_depth_flux[receiver_index, 0] += received_power
                    validation_receiver_depth_flux[
                        receiver_index, 0
                    ] += received_power
                    contribution_touch_count = touch_depth(
                        contribution_depth_seen,
                        contribution_depth_touch,
                        contribution_touch_count,
                        0,
                    )
                    contribution_depth_counts[
                        0, CONTRIBUTION_DEPTH_RECEIVER
                    ] += 1
                    contribution_depth_flux[
                        0, CONTRIBUTION_DEPTH_RECEIVER
                    ] += received_power
                    validation_contribution_depth_flux[
                        0, CONTRIBUTION_DEPTH_RECEIVER
                    ] += received_power
                else:
                    previous_lobe = lobe_codes[end - 1]
                    reflection_touch_count = record_reflection_outcome(
                        previous_lobe,
                        OUTCOME_RECEIVER,
                        terminal_depth,
                        received_power,
                        reflection_counts,
                        reflection_flux,
                        reflection_lobe_counts,
                        reflection_lobe_flux,
                        reflection_depth_counts,
                        reflection_depth_flux,
                        validation_reflection_flux,
                        validation_reflection_lobe_flux,
                        validation_reflection_depth_flux,
                        reflection_depth_seen,
                        reflection_depth_touch,
                        reflection_touch_count,
                    )
                    contribution_receiver_counts[
                        CONTRIBUTION_REFLECTED_RECEIVER
                    ] += 1
                    contribution_receiver_flux[
                        CONTRIBUTION_REFLECTED_RECEIVER
                    ] += received_power
                    validation_contribution_receiver_flux[
                        CONTRIBUTION_REFLECTED_RECEIVER
                    ] += received_power
                    receiver_counts[receiver_index, RECEIVER_REFLECTED] += 1
                    receiver_flux[receiver_index, RECEIVER_REFLECTED] += received_power
                    validation_receiver_flux[
                        receiver_index, RECEIVER_REFLECTED
                    ] += received_power
                    receiver_counts[receiver_index, RECEIVER_TOTAL] += 1
                    receiver_flux[receiver_index, RECEIVER_TOTAL] += received_power
                    validation_receiver_flux[
                        receiver_index, RECEIVER_TOTAL
                    ] += received_power
                    receiver_field = RECEIVER_SPECULAR + previous_lobe
                    receiver_counts[receiver_index, receiver_field] += 1
                    receiver_flux[receiver_index, receiver_field] += received_power
                    validation_receiver_flux[
                        receiver_index, receiver_field
                    ] += received_power
                    receiver_depth_touch_count = touch_receiver_depth(
                        receiver_depth_seen,
                        receiver_depth_touch_receivers,
                        receiver_depth_touch_depths,
                        receiver_depth_touch_count,
                        receiver_index,
                        terminal_depth,
                    )
                    receiver_depth_counts[receiver_index, terminal_depth] += 1
                    receiver_depth_flux[
                        receiver_index, terminal_depth
                    ] += received_power
                    validation_receiver_depth_flux[
                        receiver_index, terminal_depth
                    ] += received_power
                    contribution_touch_count = record_contribution_outcome(
                        previous_lobe,
                        OUTCOME_RECEIVER,
                        terminal_depth,
                        received_power,
                        contribution_lobe_counts,
                        contribution_lobe_flux,
                        contribution_depth_counts,
                        contribution_depth_flux,
                        validation_contribution_lobe_flux,
                        validation_contribution_depth_flux,
                        contribution_depth_seen,
                        contribution_depth_touch,
                        contribution_touch_count,
                    )
            elif terminal_kind == TERMINAL_ESCAPED:
                terminated_ray_count += 1
                if terminal_depth > 0:
                    previous_lobe = lobe_codes[end - 1]
                    reflection_touch_count = record_reflection_outcome(
                        previous_lobe,
                        OUTCOME_ESCAPED,
                        terminal_depth,
                        0.0,
                        reflection_counts,
                        reflection_flux,
                        reflection_lobe_counts,
                        reflection_lobe_flux,
                        reflection_depth_counts,
                        reflection_depth_flux,
                        validation_reflection_flux,
                        validation_reflection_lobe_flux,
                        validation_reflection_depth_flux,
                        reflection_depth_seen,
                        reflection_depth_touch,
                        reflection_touch_count,
                    )
                    contribution_touch_count = record_contribution_outcome(
                        previous_lobe,
                        OUTCOME_ESCAPED,
                        terminal_depth,
                        terminal_current_power[primary_slot],
                        contribution_lobe_counts,
                        contribution_lobe_flux,
                        contribution_depth_counts,
                        contribution_depth_flux,
                        validation_contribution_lobe_flux,
                        validation_contribution_depth_flux,
                        contribution_depth_seen,
                        contribution_depth_touch,
                        contribution_touch_count,
                    )
            else:
                terminated_ray_count += 1

        return (
            profile_touch_count,
            reflection_touch_count,
            contribution_touch_count,
            receiver_depth_touch_count,
            receiver_hit_count,
            terminated_ray_count,
        )

    return reduce_kernel


def _ensure_kernel() -> tuple[Callable[..., Any], float]:
    global _KERNEL, _KERNEL_COMPILED
    if _KERNEL is not None and _KERNEL_COMPILED:
        return _KERNEL, 0.0
    with _STATE_LOCK:
        if _KERNEL is None:
            try:
                _KERNEL = _make_kernel()
            except Exception as exc:
                raise NativeCpuOrderedReducerProviderError(
                    "initialize",
                    "numba_kernel_create_failed",
                ) from exc
        if _KERNEL_COMPILED:
            return _KERNEL, 0.0
        started = time.perf_counter()
        try:
            batch, state = _empty_compile_inputs()
            _KERNEL(
                batch.offsets,
                batch.face_indices,
                batch.incoming_power_lumen,
                batch.reflected_power_lumen,
                batch.emitted_power_lumen,
                batch.status_flags,
                batch.lobe_codes,
                batch.terminal_kind_codes,
                batch.terminal_depths,
                batch.terminal_current_power_lumen,
                batch.terminal_receiver_indices,
                batch.terminal_rows,
                batch.terminal_columns,
                batch.terminal_received_power_lumen,
                batch.terminal_received_power_squared_lumen2,
                batch.face_profile_slots,
                batch.profile_unassigned,
                batch.receiver_columns,
            batch.grid_offsets,
            *tuple(getattr(state, field.name) for field in fields(state)),
            state.profile_incoming_flux_lumen.copy(),
            state.profile_reflected_flux_lumen.copy(),
            state.reflection_flux_lumen.copy(),
            state.reflection_lobe_flux_lumen.copy(),
            state.reflection_depth_flux_lumen.copy(),
            state.contribution_receiver_flux_lumen.copy(),
            state.contribution_lobe_flux_lumen.copy(),
            state.contribution_depth_flux_lumen.copy(),
            state.receiver_flux_lumen.copy(),
            state.receiver_depth_flux_lumen.copy(),
            state.grid_flux_lumen.copy(),
            state.grid_flux_squared_lumen2.copy(),
            state.grid_flux_squared_totals_lumen2.copy(),
            np.empty(0, dtype=np.int32),
                np.empty(0, dtype=np.int64),
                np.empty(1, dtype=np.int16),
                np.empty(1, dtype=np.int16),
                np.empty(0, dtype=np.int32),
                np.empty(0, dtype=np.int16),
            )
        except Exception as exc:
            raise NativeCpuOrderedReducerProviderError(
                "initialize",
                "numba_jit_compile_failed",
            ) from exc
        _KERNEL_COMPILED = True
        return _KERNEL, time.perf_counter() - started


def _empty_compile_inputs() -> tuple[OrderedSummaryBatch, OrderedSummaryAccumulator]:
    def readonly(values: np.ndarray) -> np.ndarray:
        values.setflags(write=False)
        return values

    batch = OrderedSummaryBatch(
        offsets=readonly(np.asarray([0], dtype=np.int64)),
        face_indices=readonly(np.empty(0, dtype=np.int64)),
        incoming_power_lumen=readonly(np.empty(0, dtype=np.float64)),
        reflected_power_lumen=readonly(np.empty(0, dtype=np.float64)),
        emitted_power_lumen=readonly(np.empty(0, dtype=np.float64)),
        status_flags=readonly(np.empty(0, dtype=np.uint16)),
        lobe_codes=readonly(np.empty(0, dtype=np.int8)),
        terminal_kind_codes=readonly(np.empty(0, dtype=np.int8)),
        terminal_depths=readonly(np.empty(0, dtype=np.int16)),
        terminal_current_power_lumen=readonly(np.empty(0, dtype=np.float64)),
        terminal_receiver_indices=readonly(np.empty(0, dtype=np.int32)),
        terminal_rows=readonly(np.empty(0, dtype=np.int32)),
        terminal_columns=readonly(np.empty(0, dtype=np.int32)),
        terminal_received_power_lumen=readonly(np.empty(0, dtype=np.float64)),
        terminal_received_power_squared_lumen2=readonly(
            np.empty(0, dtype=np.float64)
        ),
        face_profile_slots=readonly(np.empty(0, dtype=np.int32)),
        profile_unassigned=readonly(np.empty(0, dtype=np.bool_)),
        receiver_columns=readonly(np.empty(0, dtype=np.int32)),
        grid_offsets=readonly(np.asarray([0], dtype=np.int64)),
        max_depth=0,
    )
    state = OrderedSummaryAccumulator(
        optical_counts=np.zeros(OPTICAL_COUNT_SIZE, dtype=np.int64),
        profile_hit_counts=np.empty(0, dtype=np.int64),
        profile_incoming_flux_lumen=np.empty(0, dtype=np.float64),
        profile_reflected_flux_lumen=np.empty(0, dtype=np.float64),
        profile_seen=np.empty(0, dtype=np.bool_),
        reflection_counts=np.zeros(REF_COUNT_SIZE, dtype=np.int64),
        reflection_flux_lumen=np.zeros(REF_FLUX_SIZE, dtype=np.float64),
        reflection_lobe_counts=np.zeros((LOBE_COUNT, OUTCOME_SIZE), dtype=np.int64),
        reflection_lobe_flux_lumen=np.zeros((LOBE_COUNT, 2), dtype=np.float64),
        reflection_depth_counts=np.zeros((1, REF_DEPTH_SIZE), dtype=np.int64),
        reflection_depth_flux_lumen=np.zeros((1, 2), dtype=np.float64),
        reflection_depth_seen=np.zeros(1, dtype=np.bool_),
        contribution_receiver_counts=np.zeros(
            CONTRIBUTION_RECEIVER_SIZE,
            dtype=np.int64,
        ),
        contribution_receiver_flux_lumen=np.zeros(
            CONTRIBUTION_RECEIVER_SIZE,
            dtype=np.float64,
        ),
        contribution_lobe_counts=np.zeros(
            (LOBE_COUNT, OUTCOME_SIZE),
            dtype=np.int64,
        ),
        contribution_lobe_flux_lumen=np.zeros(
            (LOBE_COUNT, OUTCOME_SIZE),
            dtype=np.float64,
        ),
        contribution_depth_counts=np.zeros(
            (1, CONTRIBUTION_DEPTH_SIZE),
            dtype=np.int64,
        ),
        contribution_depth_flux_lumen=np.zeros(
            (1, CONTRIBUTION_DEPTH_SIZE),
            dtype=np.float64,
        ),
        contribution_depth_seen=np.zeros(1, dtype=np.bool_),
        receiver_counts=np.empty((0, RECEIVER_FIELD_SIZE), dtype=np.int64),
        receiver_flux_lumen=np.empty((0, RECEIVER_FIELD_SIZE), dtype=np.float64),
        receiver_depth_counts=np.empty((0, 1), dtype=np.int64),
        receiver_depth_flux_lumen=np.empty((0, 1), dtype=np.float64),
        receiver_depth_seen=np.empty((0, 1), dtype=np.bool_),
        grid_flux_lumen=np.empty(0, dtype=np.float64),
        grid_flux_squared_lumen2=np.empty(0, dtype=np.float64),
        grid_hit_counts=np.empty(0, dtype=np.int64),
        grid_flux_squared_totals_lumen2=np.empty(0, dtype=np.float64),
    )
    return batch, state


def _count_baseline(state: OrderedSummaryAccumulator) -> dict[str, np.ndarray]:
    return {
        name: np.array(getattr(state, name), copy=True, order="C")
        for name in (
            "optical_counts",
            "profile_hit_counts",
            "profile_seen",
            "reflection_counts",
            "reflection_lobe_counts",
            "reflection_depth_counts",
            "reflection_depth_seen",
            "contribution_receiver_counts",
            "contribution_lobe_counts",
            "contribution_depth_counts",
            "contribution_depth_seen",
            "receiver_counts",
            "receiver_depth_counts",
            "receiver_depth_seen",
            "grid_hit_counts",
        )
    }


def _float_baseline(state: OrderedSummaryAccumulator) -> dict[str, np.ndarray]:
    return {
        field.name: np.array(
            getattr(state, field.name),
            dtype=np.float64,
            copy=True,
            order="C",
        )
        for field in fields(state)
        if getattr(state, field.name).dtype == np.dtype(np.float64)
    }


def _validate_float_shadow(
    state: OrderedSummaryAccumulator,
    validation_state: OrderedSummaryAccumulator,
) -> None:
    for field in fields(state):
        values = getattr(state, field.name)
        if values.dtype != np.dtype(np.float64):
            continue
        expected = getattr(validation_state, field.name)
        if not np.array_equal(values.view(np.uint64), expected.view(np.uint64)):
            raise ValueError(
                f"{field.name} violates the redundant float shadow"
            )


def _validate_float_reference(
    batch: OrderedSummaryBatch,
    baseline: dict[str, np.ndarray],
    state: OrderedSummaryAccumulator,
) -> None:
    """Recompute every float accumulator independently from the sealed tape.

    ``np.add.at`` performs unbuffered additions in input order.  Each source
    vector below is already in primary-major/event-major order, so this is an
    independent exact-order oracle rather than a second write in the native
    kernel's control flow.
    """

    expected = {
        name: np.array(values, dtype=np.float64, copy=True, order="C")
        for name, values in baseline.items()
    }
    event_counts = batch.offsets[1:] - batch.offsets[:-1]
    event_depths = np.arange(batch.event_count, dtype=np.int64) - np.repeat(
        batch.offsets[:-1],
        event_counts,
    )
    profile_slots = batch.face_profile_slots[batch.face_indices].astype(
        np.int64,
        copy=False,
    )
    np.add.at(
        expected["profile_incoming_flux_lumen"],
        profile_slots,
        batch.incoming_power_lumen,
    )
    np.add.at(
        expected["profile_reflected_flux_lumen"],
        profile_slots,
        batch.reflected_power_lumen,
    )

    emitted_mask = (batch.status_flags & np.uint16(STATUS_EMITTED)) != 0
    emitted_positions = np.flatnonzero(emitted_mask)
    emitted_lobes = batch.lobe_codes[emitted_positions].astype(
        np.int64,
        copy=False,
    )
    emitted_depths = event_depths[emitted_positions] + 1
    emitted_power = batch.emitted_power_lumen[emitted_positions]
    emitted_columns = np.full(len(emitted_positions), OUTCOME_EMITTED)
    np.add.at(
        expected["reflection_lobe_flux_lumen"],
        (emitted_lobes, emitted_columns),
        emitted_power,
    )
    np.add.at(
        expected["reflection_depth_flux_lumen"],
        (emitted_depths, emitted_columns),
        emitted_power,
    )
    np.add.at(
        expected["contribution_lobe_flux_lumen"],
        (emitted_lobes, emitted_columns),
        emitted_power,
    )
    np.add.at(
        expected["contribution_depth_flux_lumen"],
        (
            emitted_depths,
            np.full(len(emitted_positions), CONTRIBUTION_DEPTH_EMITTED),
        ),
        emitted_power,
    )

    secondary_positions = np.flatnonzero(event_depths > 0)
    secondary_lobes = batch.lobe_codes[secondary_positions - 1].astype(
        np.int64,
        copy=False,
    )
    secondary_outcomes = np.where(
        emitted_mask[secondary_positions],
        OUTCOME_CONTINUED,
        OUTCOME_BLOCKED,
    )
    secondary_power = batch.incoming_power_lumen[secondary_positions]
    np.add.at(
        expected["contribution_lobe_flux_lumen"],
        (secondary_lobes, secondary_outcomes),
        secondary_power,
    )
    np.add.at(
        expected["contribution_depth_flux_lumen"],
        (event_depths[secondary_positions], secondary_outcomes + 1),
        secondary_power,
    )

    receiver_slots = np.flatnonzero(
        batch.terminal_kind_codes == TERMINAL_RECEIVER
    )
    receiver_indices = batch.terminal_receiver_indices[receiver_slots].astype(
        np.int64,
        copy=False,
    )
    receiver_depths = batch.terminal_depths[receiver_slots].astype(
        np.int64,
        copy=False,
    )
    received_power = batch.terminal_received_power_lumen[receiver_slots]
    received_squared = (
        batch.terminal_received_power_squared_lumen2[receiver_slots]
    )
    direct_mask = receiver_depths == 0
    direct_slots = receiver_slots[direct_mask]
    direct_indices = receiver_indices[direct_mask]
    direct_power = received_power[direct_mask]
    reflected_slots = receiver_slots[~direct_mask]
    reflected_indices = receiver_indices[~direct_mask]
    reflected_depths = receiver_depths[~direct_mask]
    reflected_power = received_power[~direct_mask]

    np.add.at(
        expected["reflection_flux_lumen"],
        np.full(len(direct_slots), REF_DIRECT_RECEIVER_FLUX),
        direct_power,
    )
    np.add.at(
        expected["contribution_receiver_flux_lumen"],
        np.full(len(direct_slots), CONTRIBUTION_DIRECT_RECEIVER),
        direct_power,
    )
    np.add.at(
        expected["reflection_flux_lumen"],
        np.full(len(reflected_slots), REF_REFLECTED_RECEIVER_FLUX),
        reflected_power,
    )
    np.add.at(
        expected["contribution_receiver_flux_lumen"],
        np.full(len(reflected_slots), CONTRIBUTION_REFLECTED_RECEIVER),
        reflected_power,
    )

    if len(reflected_slots):
        reflected_last_events = batch.offsets[1:][reflected_slots] - 1
        reflected_lobes = batch.lobe_codes[reflected_last_events].astype(
            np.int64,
            copy=False,
        )
        np.add.at(
            expected["reflection_lobe_flux_lumen"],
            (reflected_lobes, np.full(len(reflected_slots), 1)),
            reflected_power,
        )
        np.add.at(
            expected["reflection_depth_flux_lumen"],
            (reflected_depths, np.full(len(reflected_slots), 1)),
            reflected_power,
        )
        np.add.at(
            expected["contribution_lobe_flux_lumen"],
            (
                reflected_lobes,
                np.full(len(reflected_slots), OUTCOME_RECEIVER),
            ),
            reflected_power,
        )
        np.add.at(
            expected["receiver_flux_lumen"],
            (reflected_indices, np.full(len(reflected_slots), RECEIVER_REFLECTED)),
            reflected_power,
        )
        np.add.at(
            expected["receiver_flux_lumen"],
            (reflected_indices, RECEIVER_SPECULAR + reflected_lobes),
            reflected_power,
        )

    np.add.at(
        expected["contribution_depth_flux_lumen"],
        (
            receiver_depths,
            np.full(len(receiver_slots), CONTRIBUTION_DEPTH_RECEIVER),
        ),
        received_power,
    )
    np.add.at(
        expected["receiver_flux_lumen"],
        (direct_indices, np.full(len(direct_slots), RECEIVER_DIRECT)),
        direct_power,
    )
    np.add.at(
        expected["receiver_flux_lumen"],
        (receiver_indices, np.full(len(receiver_slots), RECEIVER_TOTAL)),
        received_power,
    )
    np.add.at(
        expected["receiver_depth_flux_lumen"],
        (receiver_indices, receiver_depths),
        received_power,
    )

    grid_indices = (
        batch.grid_offsets[receiver_indices]
        + batch.terminal_rows[receiver_slots]
        * batch.receiver_columns[receiver_indices]
        + batch.terminal_columns[receiver_slots]
    ).astype(np.int64, copy=False)
    np.add.at(expected["grid_flux_lumen"], grid_indices, received_power)
    np.add.at(
        expected["grid_flux_squared_lumen2"],
        grid_indices,
        received_squared,
    )
    np.add.at(
        expected["grid_flux_squared_totals_lumen2"],
        receiver_indices,
        received_squared,
    )

    escaped_slots = np.flatnonzero(
        (batch.terminal_kind_codes == TERMINAL_ESCAPED)
        & (batch.terminal_depths > 0)
    )
    if len(escaped_slots):
        escaped_last_events = batch.offsets[1:][escaped_slots] - 1
        escaped_lobes = batch.lobe_codes[escaped_last_events].astype(
            np.int64,
            copy=False,
        )
        escaped_depths = batch.terminal_depths[escaped_slots].astype(
            np.int64,
            copy=False,
        )
        escaped_power = batch.terminal_current_power_lumen[escaped_slots]
        np.add.at(
            expected["contribution_lobe_flux_lumen"],
            (escaped_lobes, np.full(len(escaped_slots), OUTCOME_ESCAPED)),
            escaped_power,
        )
        np.add.at(
            expected["contribution_depth_flux_lumen"],
            (
                escaped_depths,
                np.full(len(escaped_slots), CONTRIBUTION_DEPTH_ESCAPED),
            ),
            escaped_power,
        )

    for name, expected_values in expected.items():
        actual_values = getattr(state, name)
        if not np.array_equal(
            actual_values.view(np.uint64),
            expected_values.view(np.uint64),
        ):
            raise ValueError(f"{name} violates the independent float oracle")


def _validate_count_headroom(
    batch: OrderedSummaryBatch,
    state: OrderedSummaryAccumulator,
) -> None:
    maximum_delta = batch.primary_count + batch.event_count + 1
    maximum_safe = np.iinfo(np.int64).max - maximum_delta
    for field in fields(state):
        values = getattr(state, field.name)
        if (
            isinstance(values, np.ndarray)
            and values.dtype == np.dtype(np.int64)
            and values.size
            and int(np.max(values)) > maximum_safe
        ):
            raise ValueError("ordered reducer count state lacks int64 headroom")


def _validate_batch_state_no_alias(
    batch: OrderedSummaryBatch,
    state: OrderedSummaryAccumulator,
) -> None:
    batch_ranges = []
    for field in fields(batch):
        values = getattr(batch, field.name)
        if isinstance(values, np.ndarray) and values.nbytes:
            start = int(values.__array_interface__["data"][0])
            batch_ranges.append((start, start + int(values.nbytes), field.name))
    state_ranges = []
    for field in fields(state):
        values = getattr(state, field.name)
        if isinstance(values, np.ndarray) and values.nbytes:
            start = int(values.__array_interface__["data"][0])
            state_ranges.append((start, start + int(values.nbytes), field.name))
    for batch_start, batch_end, batch_name in batch_ranges:
        for state_start, state_end, state_name in state_ranges:
            if batch_start < state_end and state_start < batch_end:
                raise ValueError(
                    f"{batch_name} and {state_name} must not share storage"
                )


def _copy_accumulator(
    state: OrderedSummaryAccumulator,
) -> OrderedSummaryAccumulator:
    return OrderedSummaryAccumulator(
        **{
            field.name: np.array(
                getattr(state, field.name),
                copy=True,
                order="C",
            )
            for field in fields(state)
        }
    )


def clone_ordered_summary_accumulator(
    state: OrderedSummaryAccumulator,
) -> OrderedSummaryAccumulator:
    """Return an owned mutable copy suitable for a subsequent tape call.

    Provider results are intentionally frozen at the public boundary.  A
    run-local ordered reduction session may nevertheless feed the validated
    state from one tape into the next without hydrating Python summaries in
    between.  Keeping that transition here preserves the accumulator storage
    contract and avoids consumers depending on the private copy helper.
    """

    if not isinstance(state, OrderedSummaryAccumulator):
        raise TypeError("state must be an OrderedSummaryAccumulator")
    return _copy_accumulator(state)


def _validate_result(
    batch: OrderedSummaryBatch,
    baseline: dict[str, np.ndarray],
    result: OrderedSummaryResult,
) -> None:
    state = result.state
    # Reuse the complete state validator before freezing the provider-owned arrays.
    state.validate_mutable(batch)
    if result.surface_hit_count != batch.event_count:
        raise ValueError("surface_hit_count must equal the tape event count")
    expected_receiver_hits = int(
        np.count_nonzero(batch.terminal_kind_codes == TERMINAL_RECEIVER)
    )
    if result.receiver_hit_count != expected_receiver_hits:
        raise ValueError("receiver_hit_count violates the terminal contract")
    if result.terminated_ray_count != batch.primary_count - expected_receiver_hits:
        raise ValueError("terminated_ray_count violates the terminal contract")

    profile_slots = batch.face_profile_slots[batch.face_indices]
    expected_profile_hits = np.bincount(
        profile_slots,
        minlength=batch.profile_count,
    ).astype(np.int64, copy=False)
    if not np.array_equal(
        state.profile_hit_counts - baseline["profile_hit_counts"],
        expected_profile_hits,
    ):
        raise ValueError("profile hit counts violate the tape contract")
    if int(
        state.optical_counts[OPTICAL_SURFACE_HIT_COUNT]
        - baseline["optical_counts"][OPTICAL_SURFACE_HIT_COUNT]
    ) != batch.event_count:
        raise ValueError("optical surface count violates the tape contract")
    expected_unassigned = int(
        np.count_nonzero(batch.profile_unassigned[profile_slots])
    )
    if int(
        state.optical_counts[OPTICAL_UNASSIGNED_SURFACE_HIT_COUNT]
        - baseline["optical_counts"][OPTICAL_UNASSIGNED_SURFACE_HIT_COUNT]
    ) != expected_unassigned:
        raise ValueError("optical unassigned count violates the tape contract")

    reflection_delta = state.reflection_counts - baseline["reflection_counts"]
    event_counts = batch.offsets[1:] - batch.offsets[:-1]
    emitted_mask = (batch.status_flags & np.uint16(STATUS_EMITTED)) != 0
    expected_reflection = {
        REF_SURFACE_HIT_COUNT: batch.event_count,
        REF_PRIMARY_SURFACE_HIT_COUNT: int(np.count_nonzero(event_counts)),
        REF_ATTEMPT_COUNT: int(
            np.count_nonzero(batch.status_flags & np.uint16(STATUS_ATTEMPTED))
        ),
        REF_EMITTED_COUNT: int(np.count_nonzero(emitted_mask)),
        REF_BELOW_ENERGY_COUNT: int(
            np.count_nonzero(batch.status_flags & np.uint16(STATUS_BELOW_ENERGY))
        ),
        REF_DISABLED_COUNT: int(
            np.count_nonzero(batch.status_flags & np.uint16(STATUS_DISABLED))
        ),
        REF_DEPTH_LIMIT_COUNT: int(
            np.count_nonzero(batch.status_flags & np.uint16(STATUS_DEPTH_LIMITED))
        ),
        REF_ROULETTE_TERMINATED_COUNT: int(
            np.count_nonzero(
                batch.status_flags & np.uint16(STATUS_ROULETTE_TERMINATED)
            )
        ),
        REF_ROULETTE_SURVIVED_COUNT: int(
            np.count_nonzero(
                batch.status_flags & np.uint16(STATUS_ROULETTE_SURVIVED)
            )
        ),
        REF_DIRECT_RECEIVER_HIT_COUNT: int(
            np.count_nonzero(
                (batch.terminal_kind_codes == TERMINAL_RECEIVER)
                & (batch.terminal_depths == 0)
            )
        ),
    }
    for index, expected in expected_reflection.items():
        if int(reflection_delta[index]) != expected:
            raise ValueError("reflection counters violate the tape contract")
    expected_max_depth = max(
        int(baseline["reflection_counts"][REF_MAX_OBSERVED_DEPTH]),
        int(np.max(batch.terminal_depths)) if batch.primary_count else 0,
    )
    if int(state.reflection_counts[REF_MAX_OBSERVED_DEPTH]) != expected_max_depth:
        raise ValueError("max observed depth violates the tape contract")
    event_primary_slots = np.repeat(
        np.arange(batch.primary_count, dtype=np.int64),
        event_counts,
    )
    event_depths = np.arange(batch.event_count, dtype=np.int64) - np.repeat(
        batch.offsets[:-1],
        event_counts,
    )
    expected_lobe_counts = np.zeros((LOBE_COUNT, OUTCOME_SIZE), dtype=np.int64)
    expected_depth_counts = np.zeros(
        (batch.depth_count, REF_DEPTH_SIZE),
        dtype=np.int64,
    )
    emitted_positions = np.flatnonzero(emitted_mask)
    emitted_lobes = batch.lobe_codes[emitted_positions].astype(
        np.int64,
        copy=False,
    )
    emitted_depths = event_depths[emitted_positions] + 1
    np.add.at(
        expected_lobe_counts,
        (emitted_lobes, np.full(len(emitted_lobes), OUTCOME_EMITTED)),
        1,
    )
    np.add.at(
        expected_depth_counts,
        (emitted_depths, np.full(len(emitted_depths), REF_DEPTH_EMITTED)),
        1,
    )
    secondary_positions = np.flatnonzero(event_depths > 0)
    secondary_lobes = batch.lobe_codes[secondary_positions - 1].astype(
        np.int64,
        copy=False,
    )
    secondary_outcomes = np.where(
        emitted_mask[secondary_positions],
        OUTCOME_CONTINUED,
        OUTCOME_BLOCKED,
    )
    np.add.at(
        expected_lobe_counts,
        (secondary_lobes, secondary_outcomes),
        1,
    )
    np.add.at(
        expected_depth_counts,
        (event_depths[secondary_positions], secondary_outcomes),
        1,
    )
    reflected_receiver_slots = np.flatnonzero(
        (batch.terminal_kind_codes == TERMINAL_RECEIVER)
        & (batch.terminal_depths > 0)
    )
    escaped_slots = np.flatnonzero(
        (batch.terminal_kind_codes == TERMINAL_ESCAPED)
        & (batch.terminal_depths > 0)
    )
    for terminal_slots, outcome in (
        (reflected_receiver_slots, OUTCOME_RECEIVER),
        (escaped_slots, OUTCOME_ESCAPED),
    ):
        last_events = batch.offsets[1:][terminal_slots] - 1
        terminal_lobes = batch.lobe_codes[last_events].astype(
            np.int64,
            copy=False,
        )
        terminal_depths = batch.terminal_depths[terminal_slots].astype(
            np.int64,
            copy=False,
        )
        np.add.at(
            expected_lobe_counts,
            (terminal_lobes, np.full(len(terminal_lobes), outcome)),
            1,
        )
        np.add.at(
            expected_depth_counts,
            (terminal_depths, np.full(len(terminal_depths), outcome)),
            1,
        )
    if not np.array_equal(
        state.reflection_lobe_counts - baseline["reflection_lobe_counts"],
        expected_lobe_counts,
    ) or not np.array_equal(
        state.reflection_depth_counts - baseline["reflection_depth_counts"],
        expected_depth_counts,
    ):
        raise ValueError("reflection distribution counters violate the tape contract")
    for global_index, outcome_index in (
        (REF_EMITTED_COUNT, OUTCOME_EMITTED),
        (REF_RECEIVER_HIT_COUNT, OUTCOME_RECEIVER),
        (REF_BLOCKED_COUNT, OUTCOME_BLOCKED),
        (REF_CONTINUED_COUNT, OUTCOME_CONTINUED),
        (REF_ESCAPED_COUNT, OUTCOME_ESCAPED),
    ):
        if int(reflection_delta[global_index]) != int(
            np.sum(expected_lobe_counts[:, outcome_index])
        ):
            raise ValueError("reflection outcome counter violates the tape contract")

    receiver_delta = (
        state.contribution_receiver_counts
        - baseline["contribution_receiver_counts"]
    )
    expected_direct = expected_reflection[REF_DIRECT_RECEIVER_HIT_COUNT]
    expected_reflected = expected_receiver_hits - expected_direct
    if not np.array_equal(
        receiver_delta,
        np.asarray([expected_direct, expected_reflected], dtype=np.int64),
    ):
        raise ValueError("contribution receiver counters violate the tape contract")
    if not np.array_equal(
        state.contribution_lobe_counts - baseline["contribution_lobe_counts"],
        expected_lobe_counts,
    ):
        raise ValueError("contribution lobe counters violate the tape contract")
    expected_contribution_depth_counts = np.zeros(
        (batch.depth_count, CONTRIBUTION_DEPTH_SIZE),
        dtype=np.int64,
    )
    for outcome_index in range(OUTCOME_SIZE):
        expected_contribution_depth_counts[:, outcome_index + 1] = (
            expected_depth_counts[:, outcome_index]
        )
    expected_contribution_depth_counts[
        0, CONTRIBUTION_DEPTH_RECEIVER
    ] += expected_direct
    if not np.array_equal(
        state.contribution_depth_counts - baseline["contribution_depth_counts"],
        expected_contribution_depth_counts,
    ):
        raise ValueError("contribution depth counters violate the tape contract")
    expected_by_receiver = np.bincount(
        batch.terminal_receiver_indices[
            batch.terminal_kind_codes == TERMINAL_RECEIVER
        ],
        minlength=batch.receiver_count,
    ).astype(np.int64, copy=False)
    if not np.array_equal(
        state.grid_hit_counts - baseline["grid_hit_counts"],
        expected_by_receiver,
    ):
        raise ValueError("receiver grid hit counters violate the tape contract")
    if not np.array_equal(
        np.sum(
            state.receiver_depth_counts - baseline["receiver_depth_counts"],
            axis=1,
        ),
        expected_by_receiver,
    ):
        raise ValueError("receiver depth counters violate the tape contract")
    expected_receiver_counts = np.zeros_like(state.receiver_counts)
    receiver_slots = np.flatnonzero(
        batch.terminal_kind_codes == TERMINAL_RECEIVER
    )
    receiver_indices = batch.terminal_receiver_indices[receiver_slots]
    direct_mask = batch.terminal_depths[receiver_slots] == 0
    direct_slots = receiver_slots[direct_mask]
    direct_indices = batch.terminal_receiver_indices[direct_slots]
    reflected_slots = receiver_slots[~direct_mask]
    reflected_indices = batch.terminal_receiver_indices[reflected_slots]
    np.add.at(expected_receiver_counts, (direct_indices, RECEIVER_DIRECT), 1)
    np.add.at(
        expected_receiver_counts,
        (reflected_indices, RECEIVER_REFLECTED),
        1,
    )
    np.add.at(expected_receiver_counts, (receiver_indices, RECEIVER_TOTAL), 1)
    if len(reflected_slots):
        reflected_last_events = batch.offsets[1:][reflected_slots] - 1
        reflected_lobes = batch.lobe_codes[reflected_last_events].astype(
            np.int64,
            copy=False,
        )
        np.add.at(
            expected_receiver_counts,
            (reflected_indices, RECEIVER_SPECULAR + reflected_lobes),
            1,
        )
    if not np.array_equal(
        state.receiver_counts - baseline["receiver_counts"],
        expected_receiver_counts,
    ):
        raise ValueError("receiver counters violate the tape contract")
    expected_receiver_depth_counts = np.zeros_like(state.receiver_depth_counts)
    np.add.at(
        expected_receiver_depth_counts,
        (
            receiver_indices,
            batch.terminal_depths[receiver_slots].astype(np.int64, copy=False),
        ),
        1,
    )
    if not np.array_equal(
        state.receiver_depth_counts - baseline["receiver_depth_counts"],
        expected_receiver_depth_counts,
    ):
        raise ValueError("receiver depth counters violate the tape contract")

    expected_profile_seen = baseline["profile_seen"] | (expected_profile_hits > 0)
    expected_reflection_seen = baseline["reflection_depth_seen"] | (
        np.sum(expected_depth_counts, axis=1) > 0
    )
    expected_contribution_seen = baseline["contribution_depth_seen"] | (
        np.sum(expected_contribution_depth_counts, axis=1) > 0
    )
    expected_receiver_seen = baseline["receiver_depth_seen"] | (
        expected_receiver_depth_counts > 0
    )
    if not np.array_equal(state.profile_seen, expected_profile_seen):
        raise ValueError("profile seen state violates the tape contract")
    if not np.array_equal(state.reflection_depth_seen, expected_reflection_seen):
        raise ValueError("reflection depth seen state violates the tape contract")
    if not np.array_equal(
        state.contribution_depth_seen,
        expected_contribution_seen,
    ):
        raise ValueError("contribution depth seen state violates the tape contract")
    if not np.array_equal(state.receiver_depth_seen, expected_receiver_seen):
        raise ValueError("receiver depth seen state violates the tape contract")

    event_profile_slots = batch.face_profile_slots[batch.face_indices]
    profile_candidate_positions = np.flatnonzero(
        ~baseline["profile_seen"][event_profile_slots]
    )
    profile_candidates = event_profile_slots[profile_candidate_positions]
    expected_profile_slots, profile_first_positions = _stable_unique_values(
        profile_candidates
    )
    expected_profile_faces = batch.face_indices[
        profile_candidate_positions[profile_first_positions]
    ]
    if not np.array_equal(
        result.profile_first_touch_slots,
        expected_profile_slots.astype(np.int32, copy=False),
    ) or not np.array_equal(
        result.profile_first_touch_faces,
        expected_profile_faces.astype(np.int64, copy=False),
    ):
        raise ValueError("profile touch order violates primary-major order")

    event_counts = batch.offsets[1:] - batch.offsets[:-1]
    event_primary_slots = np.repeat(
        np.arange(batch.primary_count, dtype=np.int64),
        event_counts,
    )
    event_depths = np.arange(batch.event_count, dtype=np.int64) - np.repeat(
        batch.offsets[:-1],
        event_counts,
    )
    emitted_positions = np.flatnonzero(emitted_mask)
    emitted_depths = event_depths[emitted_positions] + 1
    reflection_candidates = emitted_depths[
        ~baseline["reflection_depth_seen"][emitted_depths]
    ]
    expected_reflection_depths, _ = _stable_unique_values(
        reflection_candidates
    )
    if not np.array_equal(
        result.reflection_depth_first_touch,
        expected_reflection_depths.astype(np.int16, copy=False),
    ):
        raise ValueError("reflection depth touch order violates primary-major order")

    contribution_ranks = emitted_positions + event_primary_slots[emitted_positions]
    contribution_depths = emitted_depths
    direct_receiver_slots = np.flatnonzero(
        (batch.terminal_kind_codes == TERMINAL_RECEIVER)
        & (batch.terminal_depths == 0)
    )
    if len(direct_receiver_slots):
        contribution_ranks = np.concatenate(
            (
                contribution_ranks,
                batch.offsets[1:][direct_receiver_slots]
                + direct_receiver_slots,
            )
        )
        contribution_depths = np.concatenate(
            (
                contribution_depths,
                np.zeros(len(direct_receiver_slots), dtype=np.int64),
            )
        )
        rank_order = np.argsort(contribution_ranks, kind="stable")
        contribution_depths = contribution_depths[rank_order]
    contribution_candidates = contribution_depths[
        ~baseline["contribution_depth_seen"][contribution_depths]
    ]
    expected_contribution_depths, _ = _stable_unique_values(
        contribution_candidates
    )
    if not np.array_equal(
        result.contribution_depth_first_touch,
        expected_contribution_depths.astype(np.int16, copy=False),
    ):
        raise ValueError("contribution depth touch order violates primary-major order")

    receiver_slots = np.flatnonzero(
        batch.terminal_kind_codes == TERMINAL_RECEIVER
    )
    receiver_pair_codes = (
        batch.terminal_receiver_indices[receiver_slots].astype(np.int64)
        * batch.depth_count
        + batch.terminal_depths[receiver_slots].astype(np.int64)
    )
    receiver_new_mask = ~baseline["receiver_depth_seen"][
        batch.terminal_receiver_indices[receiver_slots],
        batch.terminal_depths[receiver_slots],
    ]
    expected_receiver_codes, _ = _stable_unique_values(
        receiver_pair_codes[receiver_new_mask]
    )
    expected_receiver_indices = expected_receiver_codes // batch.depth_count
    expected_receiver_depths = expected_receiver_codes % batch.depth_count
    if not np.array_equal(
        result.receiver_depth_first_touch_receivers,
        expected_receiver_indices.astype(np.int32, copy=False),
    ) or not np.array_equal(
        result.receiver_depth_first_touch_depths,
        expected_receiver_depths.astype(np.int16, copy=False),
    ):
        raise ValueError("receiver depth touch order violates primary-major order")


def _stable_unique_values(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if not len(values):
        return values, np.empty(0, dtype=np.int64)
    _, first_positions = np.unique(values, return_index=True)
    first_positions.sort()
    return values[first_positions], first_positions


def _validate_result_storage(result: OrderedSummaryResult) -> None:
    named_arrays = []
    for field in fields(result.state):
        values = getattr(result.state, field.name)
        named_arrays.append((f"state.{field.name}", values))
    for field in fields(result):
        values = getattr(result, field.name)
        if isinstance(values, np.ndarray):
            named_arrays.append((field.name, values))
    storage_ranges = []
    for name, values in named_arrays:
        if (
            not values.flags.c_contiguous
            or not values.flags.owndata
            or values.flags.writeable
        ):
            raise ValueError(
                f"{name} must be an owned read-only C-contiguous array"
            )
        if values.nbytes:
            start = int(values.__array_interface__["data"][0])
            storage_ranges.append((start, start + int(values.nbytes), name))
    storage_ranges.sort()
    for previous, current in zip(storage_ranges, storage_ranges[1:]):
        if current[0] < previous[1]:
            raise ValueError(
                f"{previous[2]} and {current[2]} must not share storage"
            )


def validate_ordered_summary_execution(
    batch: OrderedSummaryBatch,
    execution: NativeCpuOrderedReducerExecution,
) -> None:
    """Validate the immutable provider result again at the consumer boundary."""

    if not isinstance(batch, OrderedSummaryBatch):
        raise TypeError("batch must be an OrderedSummaryBatch")
    if not isinstance(execution, NativeCpuOrderedReducerExecution):
        raise TypeError("execution must be a NativeCpuOrderedReducerExecution")
    if execution.contract_version != CONTRACT_VERSION:
        raise ValueError("ordered reducer execution contract is incompatible")
    for value in (
        execution.jit_compile_sec,
        execution.execute_sec,
        execution.result_validation_sec,
    ):
        if not np.isfinite(value) or value < 0.0:
            raise ValueError("ordered reducer execution timing is invalid")
    if not isinstance(execution.numba_version, str) or not execution.numba_version:
        raise ValueError("ordered reducer provider version is invalid")
    result = execution.result
    if not isinstance(result, OrderedSummaryResult):
        raise TypeError("ordered reducer execution result has an invalid type")
    result.state.validate_readonly(batch)
    _validate_result_storage(result)
    vectors = (
        (result.profile_first_touch_slots, np.int32, "profile_first_touch_slots"),
        (result.profile_first_touch_faces, np.int64, "profile_first_touch_faces"),
        (
            result.reflection_depth_first_touch,
            np.int16,
            "reflection_depth_first_touch",
        ),
        (
            result.contribution_depth_first_touch,
            np.int16,
            "contribution_depth_first_touch",
        ),
        (
            result.receiver_depth_first_touch_receivers,
            np.int32,
            "receiver_depth_first_touch_receivers",
        ),
        (
            result.receiver_depth_first_touch_depths,
            np.int16,
            "receiver_depth_first_touch_depths",
        ),
    )
    for values, dtype, name in vectors:
        if values.dtype != np.dtype(dtype) or values.ndim != 1:
            raise ValueError(f"{name} has an invalid layout")
    if len(result.profile_first_touch_slots) != len(
        result.profile_first_touch_faces
    ):
        raise ValueError("profile touch result lengths are inconsistent")
    if len(result.receiver_depth_first_touch_receivers) != len(
        result.receiver_depth_first_touch_depths
    ):
        raise ValueError("receiver depth touch result lengths are inconsistent")
    expected_receiver_hits = int(
        np.count_nonzero(batch.terminal_kind_codes == TERMINAL_RECEIVER)
    )
    if (
        result.receiver_hit_count != expected_receiver_hits
        or result.surface_hit_count != batch.event_count
        or result.terminated_ray_count
        != batch.primary_count - expected_receiver_hits
    ):
        raise ValueError("ordered reducer execution counts are inconsistent")
    if not isinstance(execution.result_digest, str) or not hmac.compare_digest(
        execution.result_digest,
        _result_digest(result),
    ):
        raise ValueError("ordered reducer execution digest is invalid")


def _result_digest(result: OrderedSummaryResult) -> str:
    digest = hashlib.blake2b(digest_size=32)
    for field in fields(result.state):
        _update_array_digest(
            digest,
            f"state.{field.name}",
            getattr(result.state, field.name),
        )
    for field in fields(result):
        values = getattr(result, field.name)
        if isinstance(values, np.ndarray):
            _update_array_digest(digest, field.name, values)
    digest.update(
        np.asarray(
            [
                result.receiver_hit_count,
                result.surface_hit_count,
                result.terminated_ray_count,
            ],
            dtype=np.int64,
        ).tobytes()
    )
    return digest.hexdigest()


def _update_array_digest(
    digest: Any,
    name: str,
    values: np.ndarray,
) -> None:
    digest.update(name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(values.dtype.str.encode("ascii"))
    digest.update(np.asarray(values.shape, dtype=np.int64).tobytes())
    if values.nbytes:
        digest.update(memoryview(values).cast("B"))


def _readonly_copy(values: np.ndarray, dtype: Any) -> np.ndarray:
    result = np.array(values, dtype=dtype, order="C", copy=True)
    result.setflags(write=False)
    return result


__all__ = [
    "CONTRACT_VERSION",
    "CONTRIBUTION_DEPTH_BLOCKED",
    "CONTRIBUTION_DEPTH_EMITTED",
    "CONTRIBUTION_DEPTH_ESCAPED",
    "CONTRIBUTION_DEPTH_RECEIVER",
    "CONTRIBUTION_DEPTH_SECONDARY_BLOCK",
    "CONTRIBUTION_DEPTH_SIZE",
    "CONTRIBUTION_DEPTH_SURFACE",
    "CONTRIBUTION_DEPTH_CONTINUED",
    "CONTRIBUTION_DIRECT_RECEIVER",
    "CONTRIBUTION_REFLECTED_RECEIVER",
    "NativeCpuOrderedReducerCapability",
    "NativeCpuOrderedReducerExecution",
    "NativeCpuOrderedReducerProviderError",
    "NativeCpuOrderedReducerUnavailable",
    "OPTICAL_COUNT_SIZE",
    "OPTICAL_SURFACE_HIT_COUNT",
    "OPTICAL_UNASSIGNED_SURFACE_HIT_COUNT",
    "OUTCOME_BLOCKED",
    "OUTCOME_CONTINUED",
    "OUTCOME_EMITTED",
    "OUTCOME_ESCAPED",
    "OUTCOME_RECEIVER",
    "OUTCOME_SIZE",
    "OrderedSummaryAccumulator",
    "OrderedSummaryBatch",
    "OrderedSummaryResult",
    "RECEIVER_DIRECT",
    "RECEIVER_FIELD_SIZE",
    "RECEIVER_GAUSSIAN",
    "RECEIVER_LAMBERTIAN",
    "RECEIVER_REFLECTED",
    "RECEIVER_SPECULAR",
    "RECEIVER_TOTAL",
    "REF_ATTEMPT_COUNT",
    "REF_BELOW_ENERGY_COUNT",
    "REF_BLOCKED_COUNT",
    "REF_CONTINUED_COUNT",
    "REF_COUNT_SIZE",
    "REF_DEPTH_BLOCKED",
    "REF_DEPTH_CONTINUED",
    "REF_DEPTH_EMITTED",
    "REF_DEPTH_ESCAPED",
    "REF_DEPTH_LIMIT_COUNT",
    "REF_DEPTH_RECEIVER",
    "REF_DEPTH_SIZE",
    "REF_DIRECT_RECEIVER_FLUX",
    "REF_DIRECT_RECEIVER_HIT_COUNT",
    "REF_DISABLED_COUNT",
    "REF_EMITTED_COUNT",
    "REF_ESCAPED_COUNT",
    "REF_FLUX_SIZE",
    "REF_MAX_OBSERVED_DEPTH",
    "REF_PRIMARY_SURFACE_HIT_COUNT",
    "REF_RECEIVER_HIT_COUNT",
    "REF_REFLECTED_RECEIVER_FLUX",
    "REF_ROULETTE_SURVIVED_COUNT",
    "REF_ROULETTE_TERMINATED_COUNT",
    "REF_SURFACE_HIT_COUNT",
    "reduce_ordered_summary_native_cpu",
    "clone_ordered_summary_accumulator",
    "probe_native_cpu_ordered_reducer",
    "validate_ordered_summary_execution",
]
