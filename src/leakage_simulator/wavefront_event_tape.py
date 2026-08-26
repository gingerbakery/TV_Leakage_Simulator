from __future__ import annotations

from dataclasses import dataclass
import time
from typing import List

import numpy as np


EVENT_TAPE_CONTRACT = "ordered_primary_event_tape_v3"
STATE_LAYOUT = "stable_active_soa_v1"
VALIDATION_STRICT = "strict_v1"
VALIDATION_TRUSTED = "trusted_structural_v1"
PATH_PAYLOAD_FULL = "full_path_v1"
PATH_PAYLOAD_OMITTED = "omitted_v1"

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
_VALID_STATUS_LOOKUP = np.zeros(1 << 16, dtype=np.bool_)
_VALID_STATUS_LOOKUP[_VALID_STATUS_FLAGS] = True
_VALID_STATUS_LOOKUP.setflags(write=False)


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


def _view_vector(values: np.ndarray, dtype: np.dtype, name: str) -> np.ndarray:
    result = np.asarray(values, dtype=dtype, order="C")
    if result.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional array")
    return result


def _view_xyz(values: np.ndarray, name: str) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64, order="C")
    if result.ndim != 2 or result.shape[1:] != (3,):
        raise ValueError(f"{name} must have shape (N, 3)")
    return result


def _adopt_vector(values: np.ndarray, dtype: np.dtype, name: str) -> np.ndarray:
    if not isinstance(values, np.ndarray):
        raise ValueError(f"{name} must be a NumPy array")
    if values.dtype != np.dtype(dtype):
        raise ValueError(f"{name} must have dtype {np.dtype(dtype)}")
    if values.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional array")
    if not values.flags.c_contiguous or not values.flags.owndata:
        raise ValueError(f"{name} must be an owned C-contiguous array")
    values.setflags(write=False)
    return values


def _adopt_xyz(values: np.ndarray, name: str) -> np.ndarray:
    if not isinstance(values, np.ndarray):
        raise ValueError(f"{name} must be a NumPy array")
    if values.dtype != np.dtype(np.float64):
        raise ValueError(f"{name} must have dtype float64")
    if values.ndim != 2 or values.shape[1:] != (3,):
        raise ValueError(f"{name} must have shape (N, 3)")
    if not values.flags.c_contiguous or not values.flags.owndata:
        raise ValueError(f"{name} must be an owned C-contiguous array")
    values.setflags(write=False)
    return values


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
    if not array.flags.owndata:
        raise ValueError(f"{name} must own its storage")
    if array.flags.writeable:
        raise ValueError(f"{name} must be read-only")


def _array_bytes(*arrays: np.ndarray) -> int:
    return sum(int(array.nbytes) for array in arrays)


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
        ray_power_lumen: float | np.ndarray,
        primary_start_index: int,
        reflection_seeds: np.ndarray,
        *,
        source_faces: np.ndarray | None = None,
    ) -> "StableActiveRaySoA":
        owned_origins = _owned_xyz(origins, "origins")
        owned_directions = _owned_xyz(directions, "directions")
        if len(owned_origins) != len(owned_directions):
            raise ValueError("origins and directions must have the same row count")
        row_count = len(owned_origins)
        seeds = _owned_vector(reflection_seeds, np.uint64, "reflection_seeds")
        if len(seeds) != row_count:
            raise ValueError("reflection_seeds must have one value per ray")
        owned_source_faces = _owned_vector(
            (
                np.full(row_count, -1, dtype=np.int64)
                if source_faces is None
                else source_faces
            ),
            np.int64,
            "source_faces",
        )
        if len(owned_source_faces) != row_count:
            raise ValueError("source_faces must have one value per ray")
        if np.any(owned_source_faces < -1):
            raise ValueError("source_faces values must be -1 or a face index")
        if np.isscalar(ray_power_lumen):
            owned_powers = np.full(
                row_count,
                float(ray_power_lumen),
                dtype=np.float64,
            )
        else:
            owned_powers = _owned_vector(
                ray_power_lumen,
                np.float64,
                "ray_power_lumen",
            )
            if len(owned_powers) != row_count:
                raise ValueError("ray_power_lumen must have one value per ray")
        if not np.all(np.isfinite(owned_powers)) or np.any(owned_powers < 0.0):
            raise ValueError("ray_power_lumen must be finite and non-negative")
        return cls(
            primary_slots=np.arange(row_count, dtype=np.int64),
            primary_indices=np.arange(
                primary_start_index,
                primary_start_index + row_count,
                dtype=np.int64,
            ),
            origins=owned_origins,
            directions=owned_directions,
            powers_lumen=owned_powers,
            source_faces=owned_source_faces,
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
    path_payload: str
    primary_count: int
    offsets: np.ndarray
    initial_origins: np.ndarray
    initial_directions: np.ndarray
    initial_source_faces: np.ndarray
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
            self.initial_source_faces,
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

    def _validate(self, *, strict: bool) -> None:
        if self.contract != EVENT_TAPE_CONTRACT:
            raise ValueError("unsupported event tape contract")
        if self.path_payload not in {PATH_PAYLOAD_FULL, PATH_PAYLOAD_OMITTED}:
            raise ValueError("unsupported event tape path payload")
        if self.primary_count < 0:
            raise ValueError("primary_count must be non-negative")
        event_count = len(self.face_indices)
        path_primary_count = (
            self.primary_count if self.path_payload == PATH_PAYLOAD_FULL else 0
        )
        path_event_count = (
            event_count if self.path_payload == PATH_PAYLOAD_FULL else 0
        )
        sealed_arrays = (
            (self.offsets, "offsets", np.int64, (self.primary_count + 1,)),
            (self.initial_origins, "initial_origins", np.float64, (path_primary_count, 3)),
            (self.initial_directions, "initial_directions", np.float64, (path_primary_count, 3)),
            (self.initial_source_faces, "initial_source_faces", np.int64, (path_primary_count,)),
            (self.initial_power_lumen, "initial_power_lumen", np.float64, (self.primary_count,)),
            (self.reflection_seeds, "reflection_seeds", np.uint64, (self.primary_count,)),
            (self.face_indices, "face_indices", np.int64, (event_count,)),
            (self.points, "points", np.float64, (path_event_count, 3)),
            (self.normals, "normals", np.float64, (path_event_count, 3)),
            (self.distances_mm, "distances_mm", np.float64, (path_event_count,)),
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
            (self.terminal_points, "terminal_points", np.float64, (path_primary_count, 3)),
            (self.terminal_normals, "terminal_normals", np.float64, (path_primary_count, 3)),
            (self.terminal_distances_mm, "terminal_distances_mm", np.float64, (path_primary_count,)),
            (self.terminal_incoming_power_lumen, "terminal_incoming_power_lumen", np.float64, (self.primary_count,)),
        )
        for array, name, dtype, shape in sealed_arrays:
            _validate_sealed_array(
                array,
                name=name,
                dtype=np.dtype(dtype),
                shape=shape,
            )
        storage_ranges = sorted(
            (
                int(array.__array_interface__["data"][0]),
                int(array.__array_interface__["data"][0]) + int(array.nbytes),
                name,
            )
            for array, name, _, _ in sealed_arrays
            if array.nbytes
        )
        for previous, current in zip(storage_ranges, storage_ranges[1:]):
            if current[0] < previous[1]:
                raise ValueError(
                    f"{previous[2]} and {current[2]} must not share storage"
                )
        if int(self.offsets[0]) != 0 or np.any(self.offsets[1:] < self.offsets[:-1]):
            raise ValueError("offsets must be monotonic and start at zero")
        if int(self.offsets[-1]) != event_count:
            raise ValueError("offsets do not cover the event arrays")
        if self.peak_bytes < self.nbytes:
            raise ValueError("peak_bytes must cover the sealed tape storage")
        if not strict:
            return
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
        if np.any(self.initial_source_faces < -1):
            raise ValueError(
                "initial_source_faces values must be -1 or a face index"
            )
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
        if not np.all(_VALID_STATUS_LOOKUP[self.status_flags]):
            raise ValueError("surface event status is not a valid planner outcome")
        emitted = (self.status_flags & STATUS_EMITTED) != 0
        if np.any(emitted & ((self.lobe_codes < LOBE_SPECULAR) | (self.lobe_codes > LOBE_GAUSSIAN))):
            raise ValueError("emitted events require a known lobe")
        if np.any((~emitted) & (self.lobe_codes != LOBE_NONE)):
            raise ValueError("non-emitted events must use LOBE_NONE")
        if np.any((~emitted) & (self.emitted_power_lumen != 0.0)):
            raise ValueError("non-emitted events must have zero emitted power")
        valid_terminals = (
            (self.terminal_kind_codes >= TERMINAL_RECEIVER)
            & (self.terminal_kind_codes <= TERMINAL_BLOCKED)
        )
        if not np.all(valid_terminals):
            raise ValueError("every primary ray must have one terminal kind")
        valid_ray_kinds = (
            (self.incoming_ray_kind_codes >= RAY_KIND_DIRECT)
            & (self.incoming_ray_kind_codes <= RAY_KIND_GAUSSIAN)
        )
        if not np.all(valid_ray_kinds):
            raise ValueError("surface events contain an unknown incoming ray kind")
        if not np.all(
            (self.terminal_ray_kind_codes >= RAY_KIND_DIRECT)
            & (self.terminal_ray_kind_codes <= RAY_KIND_GAUSSIAN)
        ):
            raise ValueError("terminals contain an unknown ray kind")
        if np.any(self.terminal_depths < 0):
            raise ValueError("terminal depths must be non-negative")
        counts = self.offsets[1:] - self.offsets[:-1]
        blocked_terminals = self.terminal_kind_codes == TERMINAL_BLOCKED
        expected_counts = self.terminal_depths.astype(np.int64) + (
            blocked_terminals.astype(np.int64)
        )
        if not np.array_equal(counts, expected_counts):
            raise ValueError("event count and terminal depth are inconsistent")

        nonempty_primaries = np.flatnonzero(counts > 0)
        starts = self.offsets[:-1][nonempty_primaries]
        ends = self.offsets[1:][nonempty_primaries]
        lasts = ends - 1
        incoming_power_bits = self.incoming_power_lumen.view(np.uint64)
        emitted_power_bits = self.emitted_power_lumen.view(np.uint64)
        initial_power_bits = self.initial_power_lumen.view(np.uint64)
        if len(starts) and np.any(
            incoming_power_bits[starts] != initial_power_bits[nonempty_primaries]
        ):
            raise ValueError("first event power must match initial power")

        if len(starts) and np.any(
            self.incoming_ray_kind_codes[starts] != RAY_KIND_DIRECT
        ):
            raise ValueError("the first surface event must be a direct ray")
        if event_count > 1:
            boundary_starts = self.offsets[1:-1]
            valid_boundaries = boundary_starts[
                (boundary_starts > 0) & (boundary_starts < event_count)
            ]
            boundary_pairs = np.unique(valid_boundaries) - 1
            power_mismatch = incoming_power_bits[1:] != emitted_power_bits[:-1]
            power_mismatch[boundary_pairs] = False
            if np.any(power_mismatch):
                raise ValueError("surface event powers must form one chain")
            ray_kind_mismatch = (
                self.incoming_ray_kind_codes[1:]
                != self.lobe_codes[:-1] + np.int8(1)
            )
            ray_kind_mismatch[boundary_pairs] = False
            if np.any(ray_kind_mismatch):
                raise ValueError("surface event ray kinds must follow prior lobes")

        blocked_nonempty = blocked_terminals[nonempty_primaries]
        blocked_lasts = lasts[blocked_nonempty]
        if (
            event_count - int(np.count_nonzero(emitted)) != len(blocked_lasts)
            or (len(blocked_lasts) and np.any(emitted[blocked_lasts]))
        ):
            raise ValueError("surface event emission order is inconsistent")

        positive_depth = self.terminal_depths > 0
        positive_primaries = np.flatnonzero(positive_depth)
        positive_lasts = self.offsets[1:][positive_primaries] - 1
        expected_terminal_kinds = np.full(
            self.primary_count,
            RAY_KIND_DIRECT,
            dtype=np.int8,
        )
        if len(positive_primaries):
            positive_blocked = blocked_terminals[positive_primaries]
            expected_terminal_kinds[positive_primaries] = np.where(
                positive_blocked,
                self.incoming_ray_kind_codes[positive_lasts],
                self.lobe_codes[positive_lasts] + np.int8(1),
            )
        if not np.array_equal(
            self.terminal_ray_kind_codes,
            expected_terminal_kinds,
        ):
            raise ValueError("terminal ray kind is inconsistent with the event path")

        expected_terminal_power = self.initial_power_lumen.copy()
        if len(nonempty_primaries):
            nonempty_blocked = blocked_terminals[nonempty_primaries]
            expected_terminal_power[nonempty_primaries] = np.where(
                nonempty_blocked,
                self.incoming_power_lumen[lasts],
                self.emitted_power_lumen[lasts],
            )
        if not np.array_equal(
            self.terminal_current_power_lumen.view(np.uint64),
            expected_terminal_power.view(np.uint64),
        ):
            raise ValueError("terminal power is inconsistent with the event path")

        receiver_terminals = self.terminal_kind_codes == TERMINAL_RECEIVER
        nonreceiver_terminals = ~receiver_terminals
        if np.any(self.terminal_receiver_indices[receiver_terminals] < 0):
            raise ValueError("receiver terminal requires a receiver index")
        if np.any(self.terminal_rows[receiver_terminals] < 0) or np.any(
            self.terminal_columns[receiver_terminals] < 0
        ):
            raise ValueError("receiver terminal requires a grid cell")
        if np.any(self.terminal_receiver_indices[nonreceiver_terminals] != -1):
            raise ValueError("non-receiver terminal must not name a receiver")
        if np.any(self.terminal_rows[nonreceiver_terminals] != -1) or np.any(
            self.terminal_columns[nonreceiver_terminals] != -1
        ):
            raise ValueError("non-receiver terminal must not name a grid cell")
        if not np.array_equal(
            self.terminal_incoming_power_lumen[receiver_terminals].view(np.uint64),
            self.terminal_current_power_lumen[receiver_terminals].view(np.uint64),
        ):
            raise ValueError("receiver incoming power must match terminal power")

    def validate(self) -> None:
        self._validate(strict=True)

    def _validate_trusted_structure(self) -> None:
        self._validate(strict=False)


class PrimaryMajorEventTapeBuilder:
    """Depth-segment builder sealed into actual-event-proportional CSR storage."""

    def __init__(
        self,
        initial_origins: np.ndarray | None,
        initial_directions: np.ndarray | None,
        initial_power_lumen: np.ndarray,
        reflection_seeds: np.ndarray,
        max_depth: int,
        *,
        include_path_payload: bool = True,
        initial_source_faces: np.ndarray | None = None,
    ) -> None:
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
        self.primary_count = len(self.initial_power_lumen)
        self.include_path_payload = bool(include_path_payload)
        if self.include_path_payload:
            if initial_origins is None or initial_directions is None:
                raise ValueError("full path payload requires initial ray geometry")
            self.initial_origins = _owned_xyz(initial_origins, "initial_origins")
            self.initial_directions = _owned_xyz(
                initial_directions,
                "initial_directions",
            )
            self.initial_source_faces = _owned_vector(
                (
                    np.full(self.primary_count, -1, dtype=np.int64)
                    if initial_source_faces is None
                    else initial_source_faces
                ),
                np.int64,
                "initial_source_faces",
            )
        else:
            self.initial_origins = np.empty((0, 3), dtype=np.float64)
            self.initial_directions = np.empty((0, 3), dtype=np.float64)
            self.initial_source_faces = np.empty(0, dtype=np.int64)
        if any(
            len(values) != self.primary_count
            for values in (self.reflection_seeds,)
        ):
            raise ValueError("initial arrays must have the same row count")
        if self.include_path_payload and any(
            len(values) != self.primary_count
            for values in (
                self.initial_origins,
                self.initial_directions,
                self.initial_source_faces,
            )
        ):
            raise ValueError("initial arrays must have the same row count")
        self.max_depth = int(max_depth)
        if self.max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        self._segments: List[_SurfaceEventSegment] = []
        self._last_segment_depth = -1
        self._sealed = False
        self.validation_mode = "not_used"
        self.validation_sec = 0.0
        self.copy_bytes = self._base_input_nbytes()
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
        path_primary_count = self.primary_count if self.include_path_payload else 0
        self.terminal_points = np.zeros((path_primary_count, 3), dtype=np.float64)
        self.terminal_normals = np.zeros((path_primary_count, 3), dtype=np.float64)
        self.terminal_distances_mm = np.zeros(path_primary_count, dtype=np.float64)
        self.terminal_incoming_power_lumen = np.zeros(
            self.primary_count,
            dtype=np.float64,
        )

    @property
    def event_count(self) -> int:
        return sum(len(segment.primary_slots) for segment in self._segments)

    def _base_input_nbytes(self) -> int:
        return _array_bytes(
            self.initial_origins,
            self.initial_directions,
            self.initial_source_faces,
            self.initial_power_lumen,
            self.reflection_seeds,
        )

    @property
    def nbytes(self) -> int:
        return self._base_nbytes() + sum(segment.nbytes for segment in self._segments)

    def _base_nbytes(self) -> int:
        return _array_bytes(
            self.initial_origins,
            self.initial_directions,
            self.initial_source_faces,
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
        points: np.ndarray | None = None,
        normals: np.ndarray | None = None,
        distances_mm: np.ndarray | None = None,
        incoming_power_lumen: np.ndarray,
        reflected_power_lumen: np.ndarray,
        emitted_power_lumen: np.ndarray,
        status_flags: np.ndarray,
        lobe_codes: np.ndarray,
        incoming_ray_kind_codes: np.ndarray,
        _take_ownership: bool = False,
    ) -> None:
        if self._sealed:
            raise RuntimeError("event tape builder is already sealed")
        depth = int(depth)
        if depth < 0 or depth > self.max_depth:
            raise ValueError("surface event depth is outside the configured range")
        if depth <= self._last_segment_depth:
            raise ValueError("surface event segments must be appended by increasing depth")
        vector = _adopt_vector if _take_ownership else _owned_vector
        xyz = _adopt_xyz if _take_ownership else _owned_xyz
        slots = vector(primary_slots, np.int64, "primary_slots")
        if len(slots):
            if slots[0] < 0 or slots[-1] >= self.primary_count:
                raise ValueError("primary_slots are outside the tape")
            if np.any(slots[1:] <= slots[:-1]):
                raise ValueError("primary_slots must be strictly increasing")
        if self.include_path_payload:
            if points is None or normals is None or distances_mm is None:
                raise ValueError("full path payload requires surface geometry")
            owned_points = xyz(points, "points")
            owned_normals = xyz(normals, "normals")
            owned_distances = vector(
                distances_mm,
                np.float64,
                "distances_mm",
            )
        else:
            owned_points = np.empty((0, 3), dtype=np.float64)
            owned_normals = np.empty((0, 3), dtype=np.float64)
            owned_distances = np.empty(0, dtype=np.float64)
        segment = _SurfaceEventSegment(
            depth=depth,
            primary_slots=slots,
            face_indices=vector(face_indices, np.int64, "face_indices"),
            points=owned_points,
            normals=owned_normals,
            distances_mm=owned_distances,
            incoming_power_lumen=vector(
                incoming_power_lumen,
                np.float64,
                "incoming_power_lumen",
            ),
            reflected_power_lumen=vector(
                reflected_power_lumen,
                np.float64,
                "reflected_power_lumen",
            ),
            emitted_power_lumen=vector(
                emitted_power_lumen,
                np.float64,
                "emitted_power_lumen",
            ),
            status_flags=vector(status_flags, np.uint16, "status_flags"),
            lobe_codes=vector(lobe_codes, np.int8, "lobe_codes"),
            incoming_ray_kind_codes=vector(
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
                segment.incoming_power_lumen,
                segment.reflected_power_lumen,
                segment.emitted_power_lumen,
                segment.status_flags,
                segment.lobe_codes,
                segment.incoming_ray_kind_codes,
            )
        ):
            raise ValueError("surface event arrays must have equal row counts")
        if self.include_path_payload and any(
            len(values) != expected
            for values in (
                segment.points,
                segment.normals,
                segment.distances_mm,
            )
        ):
            raise ValueError("surface event arrays must have equal row counts")
        if not expected:
            return
        self._segments.append(segment)
        if not _take_ownership:
            self.copy_bytes += segment.nbytes
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
        powers = _view_vector(
            current_power_lumen,
            np.float64,
            "current_power_lumen",
        )
        kinds = _view_vector(ray_kind_codes, np.int8, "ray_kind_codes")
        if len(powers) != len(slots) or len(kinds) != len(slots):
            raise ValueError("terminal arrays must have equal row counts")
        self.terminal_kind_codes[slots] = terminal_kind
        self.terminal_depths[slots] = depth
        self.terminal_current_power_lumen[slots] = powers
        self.terminal_ray_kind_codes[slots] = kinds
        self.copy_bytes += len(slots) * (
            np.dtype(np.int8).itemsize
            + np.dtype(np.int16).itemsize
            + np.dtype(np.float64).itemsize
            + np.dtype(np.int8).itemsize
        )

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
        points: np.ndarray | None = None,
        normals: np.ndarray | None = None,
        distances_mm: np.ndarray | None = None,
        incoming_power_lumen: np.ndarray,
    ) -> None:
        slots = self._prepare_terminal_slots(primary_slots, depth)
        core_values = (
            _view_vector(current_power_lumen, np.float64, "current_power_lumen"),
            _view_vector(ray_kind_codes, np.int8, "ray_kind_codes"),
            _view_vector(receiver_indices, np.int32, "receiver_indices"),
            _view_vector(rows, np.int32, "rows"),
            _view_vector(columns, np.int32, "columns"),
            _view_vector(
                received_power_lumen,
                np.float64,
                "received_power_lumen",
            ),
            _view_vector(
                incoming_power_lumen,
                np.float64,
                "incoming_power_lumen",
            ),
        )
        if any(len(value) != len(slots) for value in core_values):
            raise ValueError("receiver terminal arrays must have equal row counts")
        (
            powers,
            kinds,
            receivers,
            rows_owned,
            columns_owned,
            received,
            incoming,
        ) = core_values
        if self.include_path_payload:
            if points is None or normals is None or distances_mm is None:
                raise ValueError("full path payload requires receiver geometry")
            points_owned = _view_xyz(points, "points")
            normals_owned = _view_xyz(normals, "normals")
            distances = _view_vector(
                distances_mm,
                np.float64,
                "distances_mm",
            )
            if any(
                len(value) != len(slots)
                for value in (points_owned, normals_owned, distances)
            ):
                raise ValueError("receiver terminal arrays must have equal row counts")
        else:
            points_owned = None
            normals_owned = None
            distances = None
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
        if self.include_path_payload:
            assert points_owned is not None
            assert normals_owned is not None
            assert distances is not None
            self.terminal_points[slots] = points_owned
            self.terminal_normals[slots] = normals_owned
            self.terminal_distances_mm[slots] = distances
        self.terminal_incoming_power_lumen[slots] = incoming
        self.copy_bytes += len(slots) * (
            np.dtype(np.int8).itemsize
            + np.dtype(np.int16).itemsize
            + np.dtype(np.float64).itemsize
            + np.dtype(np.int8).itemsize
            + 3 * np.dtype(np.int32).itemsize
            + np.dtype(np.float64).itemsize
            + np.dtype(np.float64).itemsize
            + (
                7 * np.dtype(np.float64).itemsize
                if self.include_path_payload
                else 0
            )
        )

    def _prepare_terminal_slots(self, primary_slots: np.ndarray, depth: int) -> np.ndarray:
        if self._sealed:
            raise RuntimeError("event tape builder is already sealed")
        depth = int(depth)
        if depth < 0 or depth > self.max_depth:
            raise ValueError("terminal depth is outside the configured range")
        slots = _view_vector(primary_slots, np.int64, "primary_slots")
        if len(slots):
            if slots[0] < 0 or slots[-1] >= self.primary_count:
                raise ValueError("terminal primary_slots are outside the tape")
            if np.any(slots[1:] <= slots[:-1]):
                raise ValueError("terminal primary_slots must be strictly increasing")
            if np.any(self.terminal_kind_codes[slots] != TERMINAL_NONE):
                raise ValueError("a primary ray cannot have multiple terminals")
        return slots

    def seal(self) -> PrimaryMajorEventTape:
        return self._seal(validation_mode=VALIDATION_STRICT)

    def _seal_trusted(self) -> PrimaryMajorEventTape:
        return self._seal(validation_mode=VALIDATION_TRUSTED)

    def _seal(
        self,
        *,
        validation_mode: str,
    ) -> PrimaryMajorEventTape:
        if self._sealed:
            raise RuntimeError("event tape builder is already sealed")
        if validation_mode not in {VALIDATION_STRICT, VALIDATION_TRUSTED}:
            raise ValueError("unsupported event tape validation mode")
        self._sealed = True
        counts = np.zeros(self.primary_count, dtype=np.int64)
        for segment in self._segments:
            slots = segment.primary_slots
            if np.any(counts[slots] != segment.depth):
                raise ValueError("surface events for each primary must be depth-contiguous")
            counts[slots] += 1
        valid_terminal_kinds = np.isin(
            self.terminal_kind_codes,
            (TERMINAL_RECEIVER, TERMINAL_ESCAPED, TERMINAL_BLOCKED),
        )
        if not np.all(valid_terminal_kinds):
            raise ValueError("every primary ray must have one terminal kind")
        expected_counts = self.terminal_depths.astype(np.int64) + (
            (self.terminal_kind_codes == TERMINAL_BLOCKED).astype(np.int64)
        )
        if not np.array_equal(counts, expected_counts):
            raise ValueError("event count and terminal depth are inconsistent")
        offsets = np.empty(self.primary_count + 1, dtype=np.int64)
        offsets[0] = 0
        np.cumsum(counts, out=offsets[1:])
        event_count = int(offsets[-1])
        face_indices = np.empty(event_count, dtype=np.int64)
        path_event_count = event_count if self.include_path_payload else 0
        points = np.empty((path_event_count, 3), dtype=np.float64)
        normals = np.empty((path_event_count, 3), dtype=np.float64)
        distances = np.empty(path_event_count, dtype=np.float64)
        incoming_power = np.empty(event_count, dtype=np.float64)
        reflected_power = np.empty(event_count, dtype=np.float64)
        emitted_power = np.empty(event_count, dtype=np.float64)
        status_flags = np.empty(event_count, dtype=np.uint16)
        lobe_codes = np.empty(event_count, dtype=np.int8)
        incoming_kinds = np.empty(event_count, dtype=np.int8)
        max_segment_rows = max(
            (len(segment.primary_slots) for segment in self._segments),
            default=0,
        )
        destination_scratch = np.empty(max_segment_rows, dtype=np.int64)
        for segment in self._segments:
            destinations = destination_scratch[: len(segment.primary_slots)]
            np.take(offsets, segment.primary_slots, out=destinations)
            destinations += segment.depth
            face_indices[destinations] = segment.face_indices
            if self.include_path_payload:
                points[destinations] = segment.points
                normals[destinations] = segment.normals
                distances[destinations] = segment.distances_mm
            incoming_power[destinations] = segment.incoming_power_lumen
            reflected_power[destinations] = segment.reflected_power_lumen
            emitted_power[destinations] = segment.emitted_power_lumen
            status_flags[destinations] = segment.status_flags
            lobe_codes[destinations] = segment.lobe_codes
            incoming_kinds[destinations] = segment.incoming_ray_kind_codes
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
            counts,
        )
        tape = PrimaryMajorEventTape(
            contract=EVENT_TAPE_CONTRACT,
            path_payload=(
                PATH_PAYLOAD_FULL
                if self.include_path_payload
                else PATH_PAYLOAD_OMITTED
            ),
            primary_count=self.primary_count,
            offsets=_readonly(offsets),
            initial_origins=_readonly(self.initial_origins),
            initial_directions=_readonly(self.initial_directions),
            initial_source_faces=_readonly(self.initial_source_faces),
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
        validation_started = time.perf_counter()
        if validation_mode == VALIDATION_STRICT:
            tape.validate()
        else:
            tape._validate_trusted_structure()
        self.validation_mode = validation_mode
        self.validation_sec = time.perf_counter() - validation_started
        self.copy_bytes += _array_bytes(
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
        )
        self._segments.clear()
        return tape
