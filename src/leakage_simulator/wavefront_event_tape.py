from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np


EVENT_TAPE_CONTRACT = "ordered_primary_event_tape_v1"
STATE_LAYOUT = "stable_active_soa_v1"

LOBE_NONE = -1
LOBE_SPECULAR = 0
LOBE_LAMBERTIAN = 1
LOBE_GAUSSIAN = 2

RAY_KIND_DIRECT = 0
RAY_KIND_SPECULAR = 1
RAY_KIND_LAMBERTIAN = 2
RAY_KIND_GAUSSIAN = 3

TERMINAL_NONE = 0
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
STATUS_UNSUPPORTED = 1 << 7
_KNOWN_STATUS_MASK = (
    STATUS_ATTEMPTED
    | STATUS_DEPTH_LIMITED
    | STATUS_BELOW_ENERGY
    | STATUS_ROULETTE_TERMINATED
    | STATUS_ROULETTE_SURVIVED
    | STATUS_DISABLED
    | STATUS_EMITTED
    | STATUS_UNSUPPORTED
)
_VALID_STATUS_FLAGS = np.asarray(
    (
        STATUS_DEPTH_LIMITED | STATUS_DISABLED,
        STATUS_ATTEMPTED | STATUS_BELOW_ENERGY,
        STATUS_ATTEMPTED | STATUS_BELOW_ENERGY | STATUS_ROULETTE_TERMINATED,
        STATUS_ATTEMPTED | STATUS_ROULETTE_SURVIVED | STATUS_DISABLED,
        STATUS_ATTEMPTED | STATUS_ROULETTE_SURVIVED | STATUS_EMITTED,
        STATUS_ATTEMPTED | STATUS_DISABLED,
        STATUS_ATTEMPTED | STATUS_EMITTED,
    ),
    dtype=np.uint16,
)


def _owned_vector(values: np.ndarray, dtype: np.dtype, name: str) -> np.ndarray:
    result = np.array(values, dtype=dtype, order="C", copy=True)
    if result.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional array")
    return result


def _owned_xyz(values: np.ndarray, name: str) -> np.ndarray:
    result = np.array(values, dtype=np.float64, order="C", copy=True)
    if result.ndim != 2 or result.shape[1:] != (3,):
        raise ValueError(f"{name} must have shape (N, 3)")
    return result


def _readonly(array: np.ndarray) -> np.ndarray:
    array.setflags(write=False)
    return array


def _validate_sealed_array(
    array: np.ndarray,
    *,
    name: str,
    dtype: np.dtype,
    shape: tuple[int, ...],
) -> None:
    if not isinstance(array, np.ndarray):
        raise ValueError(f"{name} must be a NumPy array")
    if array.dtype != np.dtype(dtype):
        raise ValueError(f"{name} must have dtype {np.dtype(dtype)}")
    if array.shape != shape:
        raise ValueError(f"{name} must have shape {shape}")
    if not array.flags.c_contiguous:
        raise ValueError(f"{name} must be C-contiguous")
    if array.flags.writeable:
        raise ValueError(f"{name} must be read-only")


def _array_bytes(*arrays: np.ndarray) -> int:
    return sum(int(array.nbytes) for array in arrays)


def _same_float64_bits(left: float, right: float) -> bool:
    return bool(
        np.float64(left).view(np.uint64) == np.float64(right).view(np.uint64)
    )


@dataclass(slots=True)
class StableActiveRaySoA:
    """Owned, active-row-aligned ray state with stable compaction semantics."""

    primary_slots: np.ndarray
    primary_indices: np.ndarray
    origins: np.ndarray
    directions: np.ndarray
    powers_lumen: np.ndarray
    source_faces: np.ndarray
    ray_kind_codes: np.ndarray
    reflection_seeds: np.ndarray

    @classmethod
    def initialize(
        cls,
        origins: np.ndarray,
        directions: np.ndarray,
        ray_power_lumen: float,
        primary_start_index: int,
        reflection_seeds: np.ndarray,
    ) -> "StableActiveRaySoA":
        owned_origins = _owned_xyz(origins, "origins")
        owned_directions = _owned_xyz(directions, "directions")
        if len(owned_origins) != len(owned_directions):
            raise ValueError("origins and directions must have the same row count")
        row_count = len(owned_origins)
        seeds = _owned_vector(reflection_seeds, np.uint64, "reflection_seeds")
        if len(seeds) != row_count:
            raise ValueError("reflection_seeds must have one value per ray")
        return cls(
            primary_slots=np.arange(row_count, dtype=np.int64),
            primary_indices=np.arange(
                primary_start_index,
                primary_start_index + row_count,
                dtype=np.int64,
            ),
            origins=owned_origins,
            directions=owned_directions,
            powers_lumen=np.full(row_count, ray_power_lumen, dtype=np.float64),
            source_faces=np.full(row_count, -1, dtype=np.int64),
            ray_kind_codes=np.full(row_count, RAY_KIND_DIRECT, dtype=np.int8),
            reflection_seeds=seeds,
        )

    def __len__(self) -> int:
        return len(self.primary_slots)

    @property
    def nbytes(self) -> int:
        return _array_bytes(
            self.primary_slots,
            self.primary_indices,
            self.origins,
            self.directions,
            self.powers_lumen,
            self.source_faces,
            self.ray_kind_codes,
            self.reflection_seeds,
        )

    def compact_continuations(
        self,
        row_indices: np.ndarray,
        origins: np.ndarray,
        directions: np.ndarray,
        powers_lumen: np.ndarray,
        source_faces: np.ndarray,
        ray_kind_codes: np.ndarray,
    ) -> "StableActiveRaySoA":
        rows = _owned_vector(row_indices, np.int64, "row_indices")
        if len(rows):
            if rows[0] < 0 or rows[-1] >= len(self):
                raise ValueError("row_indices are outside the active state")
            if np.any(rows[1:] <= rows[:-1]):
                raise ValueError("row_indices must be strictly increasing")
        owned_origins = _owned_xyz(origins, "origins")
        owned_directions = _owned_xyz(directions, "directions")
        owned_powers = _owned_vector(powers_lumen, np.float64, "powers_lumen")
        owned_faces = _owned_vector(source_faces, np.int64, "source_faces")
        owned_kinds = _owned_vector(ray_kind_codes, np.int8, "ray_kind_codes")
        expected = len(rows)
        if any(
            len(values) != expected
            for values in (
                owned_origins,
                owned_directions,
                owned_powers,
                owned_faces,
                owned_kinds,
            )
        ):
            raise ValueError("continuation arrays must have one row per row_index")
        return StableActiveRaySoA(
            primary_slots=np.ascontiguousarray(self.primary_slots[rows]),
            primary_indices=np.ascontiguousarray(self.primary_indices[rows]),
            origins=owned_origins,
            directions=owned_directions,
            powers_lumen=owned_powers,
            source_faces=owned_faces,
            ray_kind_codes=owned_kinds,
            reflection_seeds=np.ascontiguousarray(self.reflection_seeds[rows]),
        )


@dataclass(slots=True)
class _SurfaceEventSegment:
    depth: int
    primary_slots: np.ndarray
    face_indices: np.ndarray
    points: np.ndarray
    normals: np.ndarray
    distances_mm: np.ndarray
    incoming_power_lumen: np.ndarray
    reflected_power_lumen: np.ndarray
    emitted_power_lumen: np.ndarray
    status_flags: np.ndarray
    lobe_codes: np.ndarray
    incoming_ray_kind_codes: np.ndarray

    @property
    def nbytes(self) -> int:
        return _array_bytes(
            self.primary_slots,
            self.face_indices,
            self.points,
            self.normals,
            self.distances_mm,
            self.incoming_power_lumen,
            self.reflected_power_lumen,
            self.emitted_power_lumen,
            self.status_flags,
            self.lobe_codes,
            self.incoming_ray_kind_codes,
        )


@dataclass(frozen=True, slots=True)
class PrimaryMajorEventTape:
    contract: str
    primary_count: int
    offsets: np.ndarray
    initial_origins: np.ndarray
    initial_directions: np.ndarray
    initial_power_lumen: np.ndarray
    reflection_seeds: np.ndarray
    face_indices: np.ndarray
    points: np.ndarray
    normals: np.ndarray
    distances_mm: np.ndarray
    incoming_power_lumen: np.ndarray
    reflected_power_lumen: np.ndarray
    emitted_power_lumen: np.ndarray
    status_flags: np.ndarray
    lobe_codes: np.ndarray
    incoming_ray_kind_codes: np.ndarray
    terminal_kind_codes: np.ndarray
    terminal_depths: np.ndarray
    terminal_current_power_lumen: np.ndarray
    terminal_ray_kind_codes: np.ndarray
    terminal_receiver_indices: np.ndarray
    terminal_rows: np.ndarray
    terminal_columns: np.ndarray
    terminal_received_power_lumen: np.ndarray
    terminal_points: np.ndarray
    terminal_normals: np.ndarray
    terminal_distances_mm: np.ndarray
    terminal_incoming_power_lumen: np.ndarray
    peak_bytes: int

    def __len__(self) -> int:
        return self.primary_count

    @property
    def event_count(self) -> int:
        return len(self.face_indices)

    @property
    def nbytes(self) -> int:
        return _array_bytes(
            self.offsets,
            self.initial_origins,
            self.initial_directions,
            self.initial_power_lumen,
            self.reflection_seeds,
            self.face_indices,
            self.points,
            self.normals,
            self.distances_mm,
            self.incoming_power_lumen,
            self.reflected_power_lumen,
            self.emitted_power_lumen,
            self.status_flags,
            self.lobe_codes,
            self.incoming_ray_kind_codes,
            self.terminal_kind_codes,
            self.terminal_depths,
            self.terminal_current_power_lumen,
            self.terminal_ray_kind_codes,
            self.terminal_receiver_indices,
            self.terminal_rows,
            self.terminal_columns,
            self.terminal_received_power_lumen,
            self.terminal_points,
            self.terminal_normals,
            self.terminal_distances_mm,
            self.terminal_incoming_power_lumen,
        )

    def primary_event_bounds(self, primary_slot: int) -> tuple[int, int]:
        if primary_slot < 0 or primary_slot >= self.primary_count:
            raise IndexError("primary_slot is outside the event tape")
        return int(self.offsets[primary_slot]), int(self.offsets[primary_slot + 1])

    def validate(self) -> None:
        if self.contract != EVENT_TAPE_CONTRACT:
            raise ValueError("unsupported event tape contract")
        if self.primary_count < 0:
            raise ValueError("primary_count must be non-negative")
        event_count = len(self.face_indices)
        sealed_arrays = (
            (self.offsets, "offsets", np.int64, (self.primary_count + 1,)),
            (self.initial_origins, "initial_origins", np.float64, (self.primary_count, 3)),
            (self.initial_directions, "initial_directions", np.float64, (self.primary_count, 3)),
            (self.initial_power_lumen, "initial_power_lumen", np.float64, (self.primary_count,)),
            (self.reflection_seeds, "reflection_seeds", np.uint64, (self.primary_count,)),
            (self.face_indices, "face_indices", np.int64, (event_count,)),
            (self.points, "points", np.float64, (event_count, 3)),
            (self.normals, "normals", np.float64, (event_count, 3)),
            (self.distances_mm, "distances_mm", np.float64, (event_count,)),
            (self.incoming_power_lumen, "incoming_power_lumen", np.float64, (event_count,)),
            (self.reflected_power_lumen, "reflected_power_lumen", np.float64, (event_count,)),
            (self.emitted_power_lumen, "emitted_power_lumen", np.float64, (event_count,)),
            (self.status_flags, "status_flags", np.uint16, (event_count,)),
            (self.lobe_codes, "lobe_codes", np.int8, (event_count,)),
            (self.incoming_ray_kind_codes, "incoming_ray_kind_codes", np.int8, (event_count,)),
            (self.terminal_kind_codes, "terminal_kind_codes", np.int8, (self.primary_count,)),
            (self.terminal_depths, "terminal_depths", np.int16, (self.primary_count,)),
            (self.terminal_current_power_lumen, "terminal_current_power_lumen", np.float64, (self.primary_count,)),
            (self.terminal_ray_kind_codes, "terminal_ray_kind_codes", np.int8, (self.primary_count,)),
            (self.terminal_receiver_indices, "terminal_receiver_indices", np.int32, (self.primary_count,)),
            (self.terminal_rows, "terminal_rows", np.int32, (self.primary_count,)),
            (self.terminal_columns, "terminal_columns", np.int32, (self.primary_count,)),
            (self.terminal_received_power_lumen, "terminal_received_power_lumen", np.float64, (self.primary_count,)),
            (self.terminal_points, "terminal_points", np.float64, (self.primary_count, 3)),
            (self.terminal_normals, "terminal_normals", np.float64, (self.primary_count, 3)),
            (self.terminal_distances_mm, "terminal_distances_mm", np.float64, (self.primary_count,)),
            (self.terminal_incoming_power_lumen, "terminal_incoming_power_lumen", np.float64, (self.primary_count,)),
        )
        for array, name, dtype, shape in sealed_arrays:
            _validate_sealed_array(
                array,
                name=name,
                dtype=np.dtype(dtype),
                shape=shape,
            )
        if int(self.offsets[0]) != 0 or np.any(self.offsets[1:] < self.offsets[:-1]):
            raise ValueError("offsets must be monotonic and start at zero")
        if int(self.offsets[-1]) != event_count:
            raise ValueError("offsets do not cover the event arrays")
        if self.peak_bytes < self.nbytes:
            raise ValueError("peak_bytes must cover the sealed tape storage")
        finite_arrays = (
            self.initial_origins,
            self.initial_directions,
            self.initial_power_lumen,
            self.points,
            self.normals,
            self.distances_mm,
            self.incoming_power_lumen,
            self.reflected_power_lumen,
            self.emitted_power_lumen,
            self.terminal_current_power_lumen,
            self.terminal_received_power_lumen,
            self.terminal_points,
            self.terminal_normals,
            self.terminal_distances_mm,
            self.terminal_incoming_power_lumen,
        )
        if any(not np.all(np.isfinite(values)) for values in finite_arrays):
            raise ValueError("event tape floating-point arrays must be finite")
        nonnegative_arrays = (
            self.initial_power_lumen,
            self.distances_mm,
            self.incoming_power_lumen,
            self.reflected_power_lumen,
            self.emitted_power_lumen,
            self.terminal_current_power_lumen,
            self.terminal_received_power_lumen,
            self.terminal_distances_mm,
            self.terminal_incoming_power_lumen,
        )
        if any(np.any(values < 0.0) for values in nonnegative_arrays):
            raise ValueError("event tape powers and distances must be non-negative")
        if np.any(self.face_indices < 0):
            raise ValueError("surface events require non-negative face indices")
        unknown_status_mask = np.uint16((~_KNOWN_STATUS_MASK) & 0xFFFF)
        if np.any(self.status_flags.astype(np.uint16) & unknown_status_mask):
            raise ValueError("surface event status contains unknown bits")
        if np.any(self.status_flags.astype(np.uint16) & STATUS_UNSUPPORTED):
            raise ValueError("unsupported planner rows cannot be sealed")
        if not np.all(np.isin(self.status_flags, _VALID_STATUS_FLAGS)):
            raise ValueError("surface event status is not a valid planner outcome")
        emitted = (self.status_flags & STATUS_EMITTED) != 0
        attempted = (self.status_flags & STATUS_ATTEMPTED) != 0
        depth_limited = (self.status_flags & STATUS_DEPTH_LIMITED) != 0
        below_energy = (self.status_flags & STATUS_BELOW_ENERGY) != 0
        roulette_terminated = (
            self.status_flags & STATUS_ROULETTE_TERMINATED
        ) != 0
        roulette_survived = (
            self.status_flags & STATUS_ROULETTE_SURVIVED
        ) != 0
        disabled = (self.status_flags & STATUS_DISABLED) != 0
        if np.any(depth_limited & (attempted | emitted | below_energy)):
            raise ValueError("depth-limited events cannot be attempted or emitted")
        if np.any(depth_limited & ~disabled):
            raise ValueError("depth-limited events must be disabled")
        if np.any((below_energy | roulette_terminated | roulette_survived | emitted) & ~attempted):
            raise ValueError("reflection outcomes require an attempted event")
        if np.any(roulette_terminated & (~below_energy | emitted | roulette_survived)):
            raise ValueError("roulette termination status is inconsistent")
        if np.any(roulette_survived & roulette_terminated):
            raise ValueError("roulette survival status is inconsistent")
        if np.any(disabled & emitted):
            raise ValueError("disabled events cannot emit")
        if np.any(emitted & ((self.lobe_codes < LOBE_SPECULAR) | (self.lobe_codes > LOBE_GAUSSIAN))):
            raise ValueError("emitted events require a known lobe")
        if np.any((~emitted) & (self.lobe_codes != LOBE_NONE)):
            raise ValueError("non-emitted events must use LOBE_NONE")
        if np.any((~emitted) & (self.emitted_power_lumen != 0.0)):
            raise ValueError("non-emitted events must have zero emitted power")
        valid_terminals = np.isin(
            self.terminal_kind_codes,
            (TERMINAL_RECEIVER, TERMINAL_ESCAPED, TERMINAL_BLOCKED),
        )
        if not np.all(valid_terminals):
            raise ValueError("every primary ray must have one terminal kind")
        valid_ray_kinds = np.isin(
            self.incoming_ray_kind_codes,
            (RAY_KIND_DIRECT, RAY_KIND_SPECULAR, RAY_KIND_LAMBERTIAN, RAY_KIND_GAUSSIAN),
        )
        if not np.all(valid_ray_kinds):
            raise ValueError("surface events contain an unknown incoming ray kind")
        if not np.all(
            np.isin(
                self.terminal_ray_kind_codes,
                (RAY_KIND_DIRECT, RAY_KIND_SPECULAR, RAY_KIND_LAMBERTIAN, RAY_KIND_GAUSSIAN),
            )
        ):
            raise ValueError("terminals contain an unknown ray kind")
        if np.any(self.terminal_depths < 0):
            raise ValueError("terminal depths must be non-negative")
        for primary_slot in range(self.primary_count):
            start, end = self.primary_event_bounds(primary_slot)
            count = end - start
            terminal_depth = int(self.terminal_depths[primary_slot])
            terminal_kind = int(self.terminal_kind_codes[primary_slot])
            expected_count = (
                terminal_depth + 1
                if terminal_kind == TERMINAL_BLOCKED
                else terminal_depth
            )
            if count != expected_count:
                raise ValueError("event count and terminal depth are inconsistent")
            if count:
                primary_emitted = emitted[start:end]
                primary_incoming_kinds = self.incoming_ray_kind_codes[start:end]
                if not _same_float64_bits(
                    self.incoming_power_lumen[start],
                    self.initial_power_lumen[primary_slot],
                ):
                    raise ValueError("first event power must match initial power")
                for event_index in range(start + 1, end):
                    if not _same_float64_bits(
                        self.incoming_power_lumen[event_index],
                        self.emitted_power_lumen[event_index - 1],
                    ):
                        raise ValueError("surface event powers must form one chain")
                if int(primary_incoming_kinds[0]) != RAY_KIND_DIRECT:
                    raise ValueError("the first surface event must be a direct ray")
                if count > 1 and not np.array_equal(
                    primary_incoming_kinds[1:],
                    self.lobe_codes[start : end - 1] + np.int8(1),
                ):
                    raise ValueError("surface event ray kinds must follow prior lobes")
                if terminal_kind == TERMINAL_BLOCKED:
                    if np.any(~primary_emitted[:-1]) or bool(primary_emitted[-1]):
                        raise ValueError("blocked paths must end with one non-emitted event")
                elif np.any(~primary_emitted):
                    raise ValueError("receiver and escaped paths require emitted surface events")
            expected_terminal_ray_kind = RAY_KIND_DIRECT
            if terminal_depth > 0:
                expected_terminal_ray_kind = (
                    int(self.incoming_ray_kind_codes[end - 1])
                    if terminal_kind == TERMINAL_BLOCKED
                    else int(self.lobe_codes[end - 1]) + 1
                )
            if int(self.terminal_ray_kind_codes[primary_slot]) != expected_terminal_ray_kind:
                raise ValueError("terminal ray kind is inconsistent with the event path")
            expected_terminal_power = (
                self.incoming_power_lumen[end - 1]
                if terminal_kind == TERMINAL_BLOCKED
                else (
                    self.initial_power_lumen[primary_slot]
                    if terminal_depth == 0
                    else self.emitted_power_lumen[end - 1]
                )
            )
            if not _same_float64_bits(
                self.terminal_current_power_lumen[primary_slot],
                expected_terminal_power,
            ):
                raise ValueError("terminal power is inconsistent with the event path")
            if terminal_kind == TERMINAL_RECEIVER:
                if int(self.terminal_receiver_indices[primary_slot]) < 0:
                    raise ValueError("receiver terminal requires a receiver index")
                if int(self.terminal_rows[primary_slot]) < 0 or int(
                    self.terminal_columns[primary_slot]
                ) < 0:
                    raise ValueError("receiver terminal requires a grid cell")
                if not _same_float64_bits(
                    self.terminal_incoming_power_lumen[primary_slot],
                    self.terminal_current_power_lumen[primary_slot],
                ):
                    raise ValueError("receiver incoming power must match terminal power")
            else:
                if int(self.terminal_receiver_indices[primary_slot]) != -1:
                    raise ValueError("non-receiver terminal must not name a receiver")
                if int(self.terminal_rows[primary_slot]) != -1 or int(
                    self.terminal_columns[primary_slot]
                ) != -1:
                    raise ValueError("non-receiver terminal must not name a grid cell")


class PrimaryMajorEventTapeBuilder:
    """Depth-segment builder sealed into actual-event-proportional CSR storage."""

    def __init__(
        self,
        initial_origins: np.ndarray,
        initial_directions: np.ndarray,
        initial_power_lumen: np.ndarray,
        reflection_seeds: np.ndarray,
        max_depth: int,
    ) -> None:
        self.initial_origins = _owned_xyz(initial_origins, "initial_origins")
        self.initial_directions = _owned_xyz(initial_directions, "initial_directions")
        self.initial_power_lumen = _owned_vector(
            initial_power_lumen,
            np.float64,
            "initial_power_lumen",
        )
        self.reflection_seeds = _owned_vector(
            reflection_seeds,
            np.uint64,
            "reflection_seeds",
        )
        self.primary_count = len(self.initial_origins)
        if any(
            len(values) != self.primary_count
            for values in (
                self.initial_directions,
                self.initial_power_lumen,
                self.reflection_seeds,
            )
        ):
            raise ValueError("initial arrays must have the same row count")
        self.max_depth = int(max_depth)
        if self.max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        self._segments: List[_SurfaceEventSegment] = []
        self._last_segment_depth = -1
        self._sealed = False
        self.terminal_kind_codes = np.full(
            self.primary_count,
            TERMINAL_NONE,
            dtype=np.int8,
        )
        self.terminal_depths = np.full(self.primary_count, -1, dtype=np.int16)
        self.terminal_current_power_lumen = np.zeros(
            self.primary_count,
            dtype=np.float64,
        )
        self.terminal_ray_kind_codes = np.full(
            self.primary_count,
            RAY_KIND_DIRECT,
            dtype=np.int8,
        )
        self.terminal_receiver_indices = np.full(
            self.primary_count,
            -1,
            dtype=np.int32,
        )
        self.terminal_rows = np.full(self.primary_count, -1, dtype=np.int32)
        self.terminal_columns = np.full(self.primary_count, -1, dtype=np.int32)
        self.terminal_received_power_lumen = np.zeros(
            self.primary_count,
            dtype=np.float64,
        )
        self.terminal_points = np.zeros((self.primary_count, 3), dtype=np.float64)
        self.terminal_normals = np.zeros((self.primary_count, 3), dtype=np.float64)
        self.terminal_distances_mm = np.zeros(self.primary_count, dtype=np.float64)
        self.terminal_incoming_power_lumen = np.zeros(
            self.primary_count,
            dtype=np.float64,
        )

    @property
    def event_count(self) -> int:
        return sum(len(segment.primary_slots) for segment in self._segments)

    @property
    def nbytes(self) -> int:
        return self._base_nbytes() + sum(segment.nbytes for segment in self._segments)

    def _base_nbytes(self) -> int:
        return _array_bytes(
            self.initial_origins,
            self.initial_directions,
            self.initial_power_lumen,
            self.reflection_seeds,
            self.terminal_kind_codes,
            self.terminal_depths,
            self.terminal_current_power_lumen,
            self.terminal_ray_kind_codes,
            self.terminal_receiver_indices,
            self.terminal_rows,
            self.terminal_columns,
            self.terminal_received_power_lumen,
            self.terminal_points,
            self.terminal_normals,
            self.terminal_distances_mm,
            self.terminal_incoming_power_lumen,
        )

    def append_surface_events(
        self,
        *,
        depth: int,
        primary_slots: np.ndarray,
        face_indices: np.ndarray,
        points: np.ndarray,
        normals: np.ndarray,
        distances_mm: np.ndarray,
        incoming_directions: np.ndarray | None = None,
        incoming_power_lumen: np.ndarray,
        reflected_power_lumen: np.ndarray,
        emitted_power_lumen: np.ndarray,
        emitted_directions: np.ndarray | None = None,
        status_flags: np.ndarray,
        lobe_codes: np.ndarray,
        incoming_ray_kind_codes: np.ndarray,
    ) -> None:
        if self._sealed:
            raise RuntimeError("event tape builder is already sealed")
        depth = int(depth)
        if depth < 0 or depth > self.max_depth:
            raise ValueError("surface event depth is outside the configured range")
        if depth <= self._last_segment_depth:
            raise ValueError("surface event segments must be appended by increasing depth")
        slots = _owned_vector(primary_slots, np.int64, "primary_slots")
        if len(slots):
            if slots[0] < 0 or slots[-1] >= self.primary_count:
                raise ValueError("primary_slots are outside the tape")
            if np.any(slots[1:] <= slots[:-1]):
                raise ValueError("primary_slots must be strictly increasing")
        segment = _SurfaceEventSegment(
            depth=depth,
            primary_slots=slots,
            face_indices=_owned_vector(face_indices, np.int64, "face_indices"),
            points=_owned_xyz(points, "points"),
            normals=_owned_xyz(normals, "normals"),
            distances_mm=_owned_vector(distances_mm, np.float64, "distances_mm"),
            incoming_power_lumen=_owned_vector(
                incoming_power_lumen,
                np.float64,
                "incoming_power_lumen",
            ),
            reflected_power_lumen=_owned_vector(
                reflected_power_lumen,
                np.float64,
                "reflected_power_lumen",
            ),
            emitted_power_lumen=_owned_vector(
                emitted_power_lumen,
                np.float64,
                "emitted_power_lumen",
            ),
            status_flags=_owned_vector(status_flags, np.uint16, "status_flags"),
            lobe_codes=_owned_vector(lobe_codes, np.int8, "lobe_codes"),
            incoming_ray_kind_codes=_owned_vector(
                incoming_ray_kind_codes,
                np.int8,
                "incoming_ray_kind_codes",
            ),
        )
        expected = len(slots)
        if any(
            len(values) != expected
            for values in (
                segment.face_indices,
                segment.points,
                segment.normals,
                segment.distances_mm,
                segment.incoming_power_lumen,
                segment.reflected_power_lumen,
                segment.emitted_power_lumen,
                segment.status_flags,
                segment.lobe_codes,
                segment.incoming_ray_kind_codes,
            )
        ):
            raise ValueError("surface event arrays must have equal row counts")
        if incoming_directions is not None and len(
            _owned_xyz(incoming_directions, "incoming_directions")
        ) != expected:
            raise ValueError("surface event arrays must have equal row counts")
        if emitted_directions is not None and len(
            _owned_xyz(emitted_directions, "emitted_directions")
        ) != expected:
            raise ValueError("surface event arrays must have equal row counts")
        if not expected:
            return
        self._segments.append(segment)
        self._last_segment_depth = depth

    def set_nonreceiver_terminals(
        self,
        *,
        primary_slots: np.ndarray,
        terminal_kind: int,
        depth: int,
        current_power_lumen: np.ndarray,
        ray_kind_codes: np.ndarray,
    ) -> None:
        if terminal_kind not in (TERMINAL_ESCAPED, TERMINAL_BLOCKED):
            raise ValueError("terminal_kind must be escaped or blocked")
        slots = self._prepare_terminal_slots(primary_slots, depth)
        powers = _owned_vector(
            current_power_lumen,
            np.float64,
            "current_power_lumen",
        )
        kinds = _owned_vector(ray_kind_codes, np.int8, "ray_kind_codes")
        if len(powers) != len(slots) or len(kinds) != len(slots):
            raise ValueError("terminal arrays must have equal row counts")
        self.terminal_kind_codes[slots] = terminal_kind
        self.terminal_depths[slots] = depth
        self.terminal_current_power_lumen[slots] = powers
        self.terminal_ray_kind_codes[slots] = kinds

    def set_receiver_terminals(
        self,
        *,
        primary_slots: np.ndarray,
        depth: int,
        current_power_lumen: np.ndarray,
        ray_kind_codes: np.ndarray,
        receiver_indices: np.ndarray,
        rows: np.ndarray,
        columns: np.ndarray,
        received_power_lumen: np.ndarray,
        points: np.ndarray,
        normals: np.ndarray,
        distances_mm: np.ndarray,
        incoming_power_lumen: np.ndarray,
    ) -> None:
        slots = self._prepare_terminal_slots(primary_slots, depth)
        values = (
            _owned_vector(current_power_lumen, np.float64, "current_power_lumen"),
            _owned_vector(ray_kind_codes, np.int8, "ray_kind_codes"),
            _owned_vector(receiver_indices, np.int32, "receiver_indices"),
            _owned_vector(rows, np.int32, "rows"),
            _owned_vector(columns, np.int32, "columns"),
            _owned_vector(
                received_power_lumen,
                np.float64,
                "received_power_lumen",
            ),
            _owned_xyz(points, "points"),
            _owned_xyz(normals, "normals"),
            _owned_vector(distances_mm, np.float64, "distances_mm"),
            _owned_vector(
                incoming_power_lumen,
                np.float64,
                "incoming_power_lumen",
            ),
        )
        if any(len(value) != len(slots) for value in values):
            raise ValueError("receiver terminal arrays must have equal row counts")
        (
            powers,
            kinds,
            receivers,
            rows_owned,
            columns_owned,
            received,
            points_owned,
            normals_owned,
            distances,
            incoming,
        ) = values
        if np.any(receivers < 0) or np.any(rows_owned < 0) or np.any(columns_owned < 0):
            raise ValueError("receiver terminal indices must be non-negative")
        self.terminal_kind_codes[slots] = TERMINAL_RECEIVER
        self.terminal_depths[slots] = depth
        self.terminal_current_power_lumen[slots] = powers
        self.terminal_ray_kind_codes[slots] = kinds
        self.terminal_receiver_indices[slots] = receivers
        self.terminal_rows[slots] = rows_owned
        self.terminal_columns[slots] = columns_owned
        self.terminal_received_power_lumen[slots] = received
        self.terminal_points[slots] = points_owned
        self.terminal_normals[slots] = normals_owned
        self.terminal_distances_mm[slots] = distances
        self.terminal_incoming_power_lumen[slots] = incoming

    def _prepare_terminal_slots(self, primary_slots: np.ndarray, depth: int) -> np.ndarray:
        if self._sealed:
            raise RuntimeError("event tape builder is already sealed")
        depth = int(depth)
        if depth < 0 or depth > self.max_depth:
            raise ValueError("terminal depth is outside the configured range")
        slots = _owned_vector(primary_slots, np.int64, "primary_slots")
        if len(slots):
            if slots[0] < 0 or slots[-1] >= self.primary_count:
                raise ValueError("terminal primary_slots are outside the tape")
            if np.any(slots[1:] <= slots[:-1]):
                raise ValueError("terminal primary_slots must be strictly increasing")
            if np.any(self.terminal_kind_codes[slots] != TERMINAL_NONE):
                raise ValueError("a primary ray cannot have multiple terminals")
        return slots

    def seal(self) -> PrimaryMajorEventTape:
        if self._sealed:
            raise RuntimeError("event tape builder is already sealed")
        self._sealed = True
        counts = np.zeros(self.primary_count, dtype=np.int64)
        for segment in self._segments:
            slots = segment.primary_slots
            if np.any(counts[slots] != segment.depth):
                raise ValueError("surface events for each primary must be depth-contiguous")
            counts[slots] += 1
        offsets = np.empty(self.primary_count + 1, dtype=np.int64)
        offsets[0] = 0
        np.cumsum(counts, out=offsets[1:])
        event_count = int(offsets[-1])
        face_indices = np.empty(event_count, dtype=np.int64)
        points = np.empty((event_count, 3), dtype=np.float64)
        normals = np.empty((event_count, 3), dtype=np.float64)
        distances = np.empty(event_count, dtype=np.float64)
        incoming_power = np.empty(event_count, dtype=np.float64)
        reflected_power = np.empty(event_count, dtype=np.float64)
        emitted_power = np.empty(event_count, dtype=np.float64)
        status_flags = np.empty(event_count, dtype=np.uint16)
        lobe_codes = np.empty(event_count, dtype=np.int8)
        incoming_kinds = np.empty(event_count, dtype=np.int8)
        assigned = np.zeros(event_count, dtype=np.bool_)
        for segment in self._segments:
            destinations = np.empty_like(segment.primary_slots)
            np.take(offsets, segment.primary_slots, out=destinations)
            destinations += segment.depth
            if np.any(assigned[destinations]):
                raise ValueError("surface event destinations must be unique")
            assigned[destinations] = True
            face_indices[destinations] = segment.face_indices
            points[destinations] = segment.points
            normals[destinations] = segment.normals
            distances[destinations] = segment.distances_mm
            incoming_power[destinations] = segment.incoming_power_lumen
            reflected_power[destinations] = segment.reflected_power_lumen
            emitted_power[destinations] = segment.emitted_power_lumen
            status_flags[destinations] = segment.status_flags
            lobe_codes[destinations] = segment.lobe_codes
            incoming_kinds[destinations] = segment.incoming_ray_kind_codes
        if event_count and not np.all(assigned):
            raise ValueError("surface event tape contains an unassigned destination")
        builder_bytes = self.nbytes
        destination_peak_bytes = max(
            (
                int(segment.primary_slots.nbytes)
                for segment in self._segments
            ),
            default=0,
        )
        new_event_bytes = _array_bytes(
            offsets,
            face_indices,
            points,
            normals,
            distances,
            incoming_power,
            reflected_power,
            emitted_power,
            status_flags,
            lobe_codes,
            incoming_kinds,
            assigned,
            counts,
        )
        tape = PrimaryMajorEventTape(
            contract=EVENT_TAPE_CONTRACT,
            primary_count=self.primary_count,
            offsets=_readonly(offsets),
            initial_origins=_readonly(self.initial_origins),
            initial_directions=_readonly(self.initial_directions),
            initial_power_lumen=_readonly(self.initial_power_lumen),
            reflection_seeds=_readonly(self.reflection_seeds),
            face_indices=_readonly(face_indices),
            points=_readonly(points),
            normals=_readonly(normals),
            distances_mm=_readonly(distances),
            incoming_power_lumen=_readonly(incoming_power),
            reflected_power_lumen=_readonly(reflected_power),
            emitted_power_lumen=_readonly(emitted_power),
            status_flags=_readonly(status_flags),
            lobe_codes=_readonly(lobe_codes),
            incoming_ray_kind_codes=_readonly(incoming_kinds),
            terminal_kind_codes=_readonly(self.terminal_kind_codes),
            terminal_depths=_readonly(self.terminal_depths),
            terminal_current_power_lumen=_readonly(
                self.terminal_current_power_lumen
            ),
            terminal_ray_kind_codes=_readonly(self.terminal_ray_kind_codes),
            terminal_receiver_indices=_readonly(self.terminal_receiver_indices),
            terminal_rows=_readonly(self.terminal_rows),
            terminal_columns=_readonly(self.terminal_columns),
            terminal_received_power_lumen=_readonly(
                self.terminal_received_power_lumen
            ),
            terminal_points=_readonly(self.terminal_points),
            terminal_normals=_readonly(self.terminal_normals),
            terminal_distances_mm=_readonly(self.terminal_distances_mm),
            terminal_incoming_power_lumen=_readonly(
                self.terminal_incoming_power_lumen
            ),
            peak_bytes=(
                builder_bytes + new_event_bytes + destination_peak_bytes
            ),
        )
        tape.validate()
        self._segments.clear()
        return tape
