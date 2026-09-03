from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
from numbers import Integral
import threading
import time
from typing import Any, Callable, Iterable, Optional

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .reflection import effective_surface_reflectance, ideal_specular_direction
from .types import OpticalProfile


FloatArray = NDArray[np.float64]
Int8Array = NDArray[np.int8]
UInt16Array = NDArray[np.uint16]
BoolArray = NDArray[np.bool_]


CONTRACT_VERSION = "deterministic_reflection_v1"

SCATTER_NONE = 0
SCATTER_SPECULAR = 1
SCATTER_LAMBERTIAN = 2
SCATTER_GAUSSIAN = 3
SCATTER_MIXED = 4

TERMINATION_THRESHOLD = 0
TERMINATION_RUSSIAN_ROULETTE = 1

LOBE_NONE = -1
LOBE_SPECULAR = 0
LOBE_LAMBERTIAN = 1
LOBE_GAUSSIAN = 2

STATUS_ATTEMPTED = 1 << 0
STATUS_DEPTH_LIMITED = 1 << 1
STATUS_BELOW_ENERGY = 1 << 2
STATUS_ROULETTE_TERMINATED = 1 << 3
STATUS_ROULETTE_SURVIVED = 1 << 4
STATUS_DISABLED = 1 << 5
STATUS_EMITTED = 1 << 6
STATUS_UNSUPPORTED = 1 << 7

_VALID_SCATTER_CODES = frozenset(
    {
        SCATTER_NONE,
        SCATTER_SPECULAR,
        SCATTER_LAMBERTIAN,
        SCATTER_GAUSSIAN,
        SCATTER_MIXED,
    }
)
_SCATTER_CODE_BY_NAME = {
    "none": SCATTER_NONE,
    "specular": SCATTER_SPECULAR,
    "lambertian": SCATTER_LAMBERTIAN,
    "gaussian": SCATTER_GAUSSIAN,
    "mixed": SCATTER_MIXED,
}
_SCATTER_NAME_BY_CODE = {
    code: name for name, code in _SCATTER_CODE_BY_NAME.items()
}
_VALID_TERMINATION_CODES = frozenset(
    {TERMINATION_THRESHOLD, TERMINATION_RUSSIAN_ROULETTE}
)


class NativeCpuWavefrontUnavailable(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.phase = "probe"


class NativeCpuWavefrontProviderError(RuntimeError):
    def __init__(self, phase: str, reason_code: str) -> None:
        super().__init__(reason_code)
        self.phase = phase
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class NativeCpuWavefrontCapability:
    available: bool
    reason_code: Optional[str]
    numba_version: Optional[str]
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class WavefrontPlanInput:
    """Immutable row-aligned input for deterministic reflection planning.

    ``scatter_models`` uses the ``SCATTER_*`` integer constants. The compiled
    provider currently accepts only ``none`` and ``specular`` rows under the
    threshold termination policy. Every other row is returned with
    ``supported_mask=False`` so that the caller can preserve its Python RNG
    sidecar without consuming or reordering a draw.
    """

    incoming_directions: FloatArray | ArrayLike
    surface_normals: FloatArray | ArrayLike
    incoming_power_lumen: FloatArray | ArrayLike
    profile_reflectance: FloatArray | ArrayLike
    profile_roughness: FloatArray | ArrayLike
    scatter_models: Int8Array | ArrayLike
    depth: int
    max_depth: int
    min_energy: float
    termination_mode: int = TERMINATION_THRESHOLD
    angle_dependent_reflectance: bool = True

    def __post_init__(self) -> None:
        directions = _readonly_vectors(self.incoming_directions, "incoming_directions")
        normals = _readonly_vectors(self.surface_normals, "surface_normals")
        powers = _readonly_float_rows(
            self.incoming_power_lumen,
            "incoming_power_lumen",
        )
        reflectance = _readonly_float_rows(
            self.profile_reflectance,
            "profile_reflectance",
        )
        roughness = _readonly_float_rows(
            self.profile_roughness,
            "profile_roughness",
        )
        scatter_models = _readonly_scatter_rows(self.scatter_models)
        row_count = len(directions)
        for name, values in (
            ("surface_normals", normals),
            ("incoming_power_lumen", powers),
            ("profile_reflectance", reflectance),
            ("profile_roughness", roughness),
            ("scatter_models", scatter_models),
        ):
            if len(values) != row_count:
                raise ValueError(f"{name} must contain {row_count} rows")
        if np.any(powers < 0.0):
            raise ValueError("incoming_power_lumen must be non-negative")
        if np.any((reflectance < 0.0) | (reflectance > 1.0)):
            raise ValueError("profile_reflectance values must be between 0 and 1")
        if np.any((roughness < 0.0) | (roughness > 1.0)):
            raise ValueError("profile_roughness values must be between 0 and 1")

        depth = _non_negative_int(self.depth, "depth")
        max_depth = _non_negative_int(self.max_depth, "max_depth")
        min_energy = float(self.min_energy)
        if not math.isfinite(min_energy) or min_energy < 0.0:
            raise ValueError("min_energy must be finite and non-negative")
        termination_mode = _choice_int(
            self.termination_mode,
            "termination_mode",
            _VALID_TERMINATION_CODES,
        )

        object.__setattr__(self, "incoming_directions", directions)
        object.__setattr__(self, "surface_normals", normals)
        object.__setattr__(self, "incoming_power_lumen", powers)
        object.__setattr__(self, "profile_reflectance", reflectance)
        object.__setattr__(self, "profile_roughness", roughness)
        object.__setattr__(self, "scatter_models", scatter_models)
        object.__setattr__(self, "depth", depth)
        object.__setattr__(self, "max_depth", max_depth)
        object.__setattr__(self, "min_energy", min_energy)
        object.__setattr__(self, "termination_mode", termination_mode)
        object.__setattr__(
            self,
            "angle_dependent_reflectance",
            bool(self.angle_dependent_reflectance),
        )

    def __len__(self) -> int:
        return int(self.incoming_power_lumen.shape[0])

    @classmethod
    def from_face_profiles(
        cls,
        incoming_directions: FloatArray | ArrayLike,
        surface_normals: FloatArray | ArrayLike,
        incoming_power_lumen: FloatArray | ArrayLike,
        face_indices: ArrayLike,
        face_reflectance: FloatArray | ArrayLike,
        face_roughness: FloatArray | ArrayLike,
        face_scatter_models: Int8Array | ArrayLike,
        *,
        depth: int,
        max_depth: int,
        min_energy: float,
        termination_mode: int = TERMINATION_THRESHOLD,
    ) -> "WavefrontPlanInput":
        """Gather immutable row profiles from face-aligned primitive tables."""

        row_faces = _face_indices(face_indices)
        face_reflectance_array = _face_float_table(
            face_reflectance,
            "face_reflectance",
        )
        face_roughness_array = _face_float_table(
            face_roughness,
            "face_roughness",
        )
        face_scatter_array = _scatter_table(face_scatter_models)
        face_count = len(face_reflectance_array)
        if len(face_roughness_array) != face_count:
            raise ValueError("face_roughness must match face_reflectance length")
        if len(face_scatter_array) != face_count:
            raise ValueError("face_scatter_models must match face_reflectance length")
        if np.any(
            (face_reflectance_array < 0.0) | (face_reflectance_array > 1.0)
        ):
            raise ValueError("face_reflectance values must be between 0 and 1")
        if np.any((face_roughness_array < 0.0) | (face_roughness_array > 1.0)):
            raise ValueError("face_roughness values must be between 0 and 1")
        if len(row_faces) and (
            np.any(row_faces < 0) or np.any(row_faces >= face_count)
        ):
            raise ValueError("face_indices contains a face outside the profile table")
        return cls(
            incoming_directions=incoming_directions,
            surface_normals=surface_normals,
            incoming_power_lumen=incoming_power_lumen,
            profile_reflectance=face_reflectance_array[row_faces],
            profile_roughness=face_roughness_array[row_faces],
            scatter_models=face_scatter_array[row_faces],
            depth=depth,
            max_depth=max_depth,
            min_energy=min_energy,
            termination_mode=termination_mode,
        )


@dataclass(frozen=True, slots=True)
class WavefrontPlanResult:
    """Immutable compact result aligned one-to-one with ``WavefrontPlanInput``."""

    supported_mask: BoolArray | ArrayLike
    reflected_power_lumen: FloatArray | ArrayLike
    emitted_power_lumen: FloatArray | ArrayLike
    emitted_directions: FloatArray | ArrayLike
    status_flags: UInt16Array | ArrayLike
    lobe_codes: Int8Array | ArrayLike

    def __post_init__(self) -> None:
        supported = _readonly_output(self.supported_mask, np.bool_)
        reflected = _readonly_output(self.reflected_power_lumen, np.float64)
        emitted = _readonly_output(self.emitted_power_lumen, np.float64)
        directions = _readonly_output(self.emitted_directions, np.float64)
        statuses = _readonly_output(self.status_flags, np.uint16)
        lobes = _readonly_output(self.lobe_codes, np.int8)
        if supported.ndim != 1:
            raise ValueError("supported_mask must have shape (N,)")
        row_count = len(supported)
        for name, values in (
            ("reflected_power_lumen", reflected),
            ("emitted_power_lumen", emitted),
            ("status_flags", statuses),
            ("lobe_codes", lobes),
        ):
            if values.ndim != 1 or len(values) != row_count:
                raise ValueError(f"{name} must have shape ({row_count},)")
        if directions.shape != (row_count, 3):
            raise ValueError(f"emitted_directions must have shape ({row_count}, 3)")
        if not np.all(np.isfinite(reflected)) or np.any(reflected < 0.0):
            raise ValueError("reflected_power_lumen must be finite and non-negative")
        if not np.all(np.isfinite(emitted)) or np.any(emitted < 0.0):
            raise ValueError("emitted_power_lumen must be finite and non-negative")
        if not np.all(np.isfinite(directions)):
            raise ValueError("emitted_directions must be finite")
        if np.any((lobes < LOBE_NONE) | (lobes > LOBE_GAUSSIAN)):
            raise ValueError("lobe_codes contains an unknown lobe")

        object.__setattr__(self, "supported_mask", supported)
        object.__setattr__(self, "reflected_power_lumen", reflected)
        object.__setattr__(self, "emitted_power_lumen", emitted)
        object.__setattr__(self, "emitted_directions", directions)
        object.__setattr__(self, "status_flags", statuses)
        object.__setattr__(self, "lobe_codes", lobes)

    def __len__(self) -> int:
        return int(self.supported_mask.shape[0])

    @property
    def supported_count(self) -> int:
        return int(np.count_nonzero(self.supported_mask))


@dataclass(frozen=True, slots=True)
class NativeCpuWavefrontExecution:
    result: WavefrontPlanResult
    jit_compile_sec: float
    execute_sec: float
    numba_version: str
    contract_version: str = CONTRACT_VERSION


_STATE_LOCK = threading.RLock()
_CAPABILITY: Optional[NativeCpuWavefrontCapability] = None
_KERNEL: Optional[Callable[..., None]] = None
_KERNEL_COMPILED = False


def scatter_codes_from_names(names: Iterable[str]) -> Int8Array:
    """Encode public scatter-model names into an immutable compact array."""

    codes = []
    for name in names:
        normalized = str(name).strip().lower()
        try:
            codes.append(_SCATTER_CODE_BY_NAME[normalized])
        except KeyError as exc:
            raise ValueError(f"unknown scatter model: {name}") from exc
    result = np.asarray(codes, dtype=np.int8)
    result.setflags(write=False)
    return result


def scatter_name_from_code(code: int) -> str:
    normalized = _choice_int(code, "scatter code", _VALID_SCATTER_CODES)
    return _SCATTER_NAME_BY_CODE[normalized]


def probe_native_cpu_wavefront() -> NativeCpuWavefrontCapability:
    """Lazily probe Numba without importing it during module import."""

    global _CAPABILITY
    if _CAPABILITY is not None:
        return _CAPABILITY
    with _STATE_LOCK:
        if _CAPABILITY is not None:
            return _CAPABILITY
        try:
            numba = importlib.import_module("numba")
        except ModuleNotFoundError:
            _CAPABILITY = NativeCpuWavefrontCapability(
                False,
                "numba_not_installed",
                None,
            )
        except Exception:
            _CAPABILITY = NativeCpuWavefrontCapability(
                False,
                "numba_import_failed",
                None,
            )
        else:
            _CAPABILITY = NativeCpuWavefrontCapability(
                True,
                None,
                str(getattr(numba, "__version__", "unknown")),
            )
        return _CAPABILITY


def plan_deterministic_reference(batch: WavefrontPlanInput) -> WavefrontPlanResult:
    """Execute the deterministic contract with the production Python math."""

    if not isinstance(batch, WavefrontPlanInput):
        raise TypeError("batch must be a WavefrontPlanInput")
    arrays = _allocate_outputs(len(batch))
    (
        supported,
        reflected_power,
        emitted_power,
        emitted_directions,
        status_flags,
        lobe_codes,
    ) = arrays
    for row_index in range(len(batch)):
        scatter_code = int(batch.scatter_models[row_index])
        if (
            batch.termination_mode != TERMINATION_THRESHOLD
            or scatter_code not in {SCATTER_NONE, SCATTER_SPECULAR}
        ):
            status_flags[row_index] = STATUS_UNSUPPORTED
            continue

        supported[row_index] = True
        incoming = tuple(float(value) for value in batch.incoming_directions[row_index])
        normal = tuple(float(value) for value in batch.surface_normals[row_index])
        profile = OpticalProfile(
            profile_id=f"__compiled_reference_{row_index}",
            reflectance=float(batch.profile_reflectance[row_index]),
            scatter_model=scatter_name_from_code(scatter_code),
            roughness=float(batch.profile_roughness[row_index]),
        )
        row_reflected_power = float(
            float(batch.incoming_power_lumen[row_index])
            * effective_surface_reflectance(
                incoming,
                normal,
                profile,
                batch.angle_dependent_reflectance,
            )
        )
        reflected_power[row_index] = row_reflected_power
        if batch.depth >= batch.max_depth:
            status_flags[row_index] = STATUS_DEPTH_LIMITED | STATUS_DISABLED
            continue

        row_status = STATUS_ATTEMPTED
        if (
            batch.min_energy > 0.0
            and row_reflected_power < batch.min_energy
        ):
            status_flags[row_index] = row_status | STATUS_BELOW_ENERGY
            continue
        if scatter_code == SCATTER_NONE or profile.reflectance <= 0.0:
            status_flags[row_index] = row_status | STATUS_DISABLED
            continue

        direction = ideal_specular_direction(incoming, normal)
        emitted_power[row_index] = row_reflected_power
        emitted_directions[row_index] = direction
        lobe_codes[row_index] = LOBE_SPECULAR
        status_flags[row_index] = row_status | STATUS_EMITTED
    return _result_from_arrays(arrays)


def plan_deterministic_native_cpu(
    batch: WavefrontPlanInput,
) -> NativeCpuWavefrontExecution:
    """Execute supported deterministic rows with a strict-float64 Numba kernel."""

    if not isinstance(batch, WavefrontPlanInput):
        raise TypeError("batch must be a WavefrontPlanInput")
    capability = probe_native_cpu_wavefront()
    if not capability.available:
        raise NativeCpuWavefrontUnavailable(
            capability.reason_code or "numba_unavailable"
        )
    kernel, jit_compile_sec = _ensure_kernel()
    arrays = _allocate_outputs(len(batch))
    started = time.perf_counter()
    try:
        kernel(
            batch.incoming_directions,
            batch.surface_normals,
            batch.incoming_power_lumen,
            batch.profile_reflectance,
            batch.profile_roughness,
            batch.scatter_models,
            batch.depth,
            batch.max_depth,
            batch.min_energy,
            batch.termination_mode,
            batch.angle_dependent_reflectance,
            *arrays,
        )
    except Exception as exc:
        raise NativeCpuWavefrontProviderError(
            "execute",
            "numba_execute_failed",
        ) from exc
    execute_sec = time.perf_counter() - started
    try:
        result = _result_from_arrays(arrays)
        _validate_native_result(batch, result)
    except Exception as exc:
        raise NativeCpuWavefrontProviderError(
            "result_validation",
            "numba_invalid_result",
        ) from exc
    return NativeCpuWavefrontExecution(
        result=result,
        jit_compile_sec=jit_compile_sec,
        execute_sec=execute_sec,
        numba_version=capability.numba_version or "unknown",
    )


def _make_kernel() -> Callable[..., None]:
    numba = importlib.import_module("numba")

    @numba.njit(inline="always", fastmath=False)
    def normalize_components(
        x_value: float,
        y_value: float,
        z_value: float,
    ) -> tuple[float, float, float]:
        magnitude_squared = (
            x_value * x_value + y_value * y_value + z_value * z_value
        )
        if magnitude_squared <= 1e-30:
            return 0.0, 0.0, 0.0
        inverse_magnitude = 1.0 / math.sqrt(magnitude_squared)
        return (
            x_value * inverse_magnitude,
            y_value * inverse_magnitude,
            z_value * inverse_magnitude,
        )

    @numba.njit(inline="always", fastmath=False)
    def oriented_normal(
        incoming_x: float,
        incoming_y: float,
        incoming_z: float,
        normal_x: float,
        normal_y: float,
        normal_z: float,
    ) -> tuple[float, float, float]:
        surface_x, surface_y, surface_z = normalize_components(
            normal_x,
            normal_y,
            normal_z,
        )
        orientation = (
            incoming_x * surface_x
            + incoming_y * surface_y
            + incoming_z * surface_z
        )
        if orientation > 0.0:
            return -surface_x, -surface_y, -surface_z
        return surface_x, surface_y, surface_z

    @numba.njit(inline="always", fastmath=False)
    def effective_reflectance(
        incoming_x: float,
        incoming_y: float,
        incoming_z: float,
        normal_x: float,
        normal_y: float,
        normal_z: float,
        base_reflectance: float,
        roughness: float,
        angle_dependent: bool,
    ) -> float:
        if not angle_dependent:
            return base_reflectance
        direction_x, direction_y, direction_z = normalize_components(
            incoming_x,
            incoming_y,
            incoming_z,
        )
        surface_x, surface_y, surface_z = oriented_normal(
            direction_x,
            direction_y,
            direction_z,
            normal_x,
            normal_y,
            normal_z,
        )
        cosine_incidence = -(
            direction_x * surface_x
            + direction_y * surface_y
            + direction_z * surface_z
        )
        if cosine_incidence < 0.0:
            cosine_incidence = 0.0
        elif cosine_incidence > 1.0:
            cosine_incidence = 1.0
        grazing_coordinate = (0.7 - cosine_incidence) / 0.7
        if grazing_coordinate < 0.0:
            grazing_coordinate = 0.0
        # Python float ``x ** 5`` delegates to libm pow. Numba's integer
        # exponent lowering uses multiply chains and differs by 1+ ULP for
        # many values, so a floating exponent is intentional here.
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

    @numba.njit(inline="always", fastmath=False)
    def specular_direction(
        incoming_x: float,
        incoming_y: float,
        incoming_z: float,
        normal_x: float,
        normal_y: float,
        normal_z: float,
    ) -> tuple[float, float, float]:
        direction_x, direction_y, direction_z = normalize_components(
            incoming_x,
            incoming_y,
            incoming_z,
        )
        surface_x, surface_y, surface_z = oriented_normal(
            direction_x,
            direction_y,
            direction_z,
            normal_x,
            normal_y,
            normal_z,
        )
        incidence = (
            direction_x * surface_x
            + direction_y * surface_y
            + direction_z * surface_z
        )
        return (
            direction_x - 2.0 * incidence * surface_x,
            direction_y - 2.0 * incidence * surface_y,
            direction_z - 2.0 * incidence * surface_z,
        )

    @numba.njit(nogil=True, fastmath=False)
    def plan_kernel(
        incoming_directions: FloatArray,
        surface_normals: FloatArray,
        incoming_power_lumen: FloatArray,
        profile_reflectance: FloatArray,
        profile_roughness: FloatArray,
        scatter_models: Int8Array,
        depth: int,
        max_depth: int,
        min_energy: float,
        termination_mode: int,
        angle_dependent_reflectance: bool,
        supported_mask: BoolArray,
        reflected_power_lumen: FloatArray,
        emitted_power_lumen: FloatArray,
        emitted_directions: FloatArray,
        status_flags: UInt16Array,
        lobe_codes: Int8Array,
    ) -> None:
        for row_index in range(incoming_power_lumen.shape[0]):
            supported_mask[row_index] = False
            reflected_power_lumen[row_index] = 0.0
            emitted_power_lumen[row_index] = 0.0
            emitted_directions[row_index, 0] = 0.0
            emitted_directions[row_index, 1] = 0.0
            emitted_directions[row_index, 2] = 0.0
            status_flags[row_index] = STATUS_UNSUPPORTED
            lobe_codes[row_index] = LOBE_NONE

            scatter_code = scatter_models[row_index]
            if termination_mode != TERMINATION_THRESHOLD or not (
                scatter_code == SCATTER_NONE
                or scatter_code == SCATTER_SPECULAR
            ):
                continue

            supported_mask[row_index] = True
            incoming_x = incoming_directions[row_index, 0]
            incoming_y = incoming_directions[row_index, 1]
            incoming_z = incoming_directions[row_index, 2]
            normal_x = surface_normals[row_index, 0]
            normal_y = surface_normals[row_index, 1]
            normal_z = surface_normals[row_index, 2]
            base_reflectance = profile_reflectance[row_index]
            row_reflected_power = incoming_power_lumen[row_index] * effective_reflectance(
                incoming_x,
                incoming_y,
                incoming_z,
                normal_x,
                normal_y,
                normal_z,
                base_reflectance,
                profile_roughness[row_index],
                angle_dependent_reflectance,
            )
            reflected_power_lumen[row_index] = row_reflected_power
            if depth >= max_depth:
                status_flags[row_index] = STATUS_DEPTH_LIMITED | STATUS_DISABLED
                continue

            row_status = STATUS_ATTEMPTED
            if min_energy > 0.0 and row_reflected_power < min_energy:
                status_flags[row_index] = row_status | STATUS_BELOW_ENERGY
                continue
            if scatter_code == SCATTER_NONE or base_reflectance <= 0.0:
                status_flags[row_index] = row_status | STATUS_DISABLED
                continue

            reflected_x, reflected_y, reflected_z = specular_direction(
                incoming_x,
                incoming_y,
                incoming_z,
                normal_x,
                normal_y,
                normal_z,
            )
            emitted_power_lumen[row_index] = row_reflected_power
            emitted_directions[row_index, 0] = reflected_x
            emitted_directions[row_index, 1] = reflected_y
            emitted_directions[row_index, 2] = reflected_z
            status_flags[row_index] = row_status | STATUS_EMITTED
            lobe_codes[row_index] = LOBE_SPECULAR
        return None

    return plan_kernel


def _ensure_kernel() -> tuple[Callable[..., Any], float]:
    global _KERNEL, _KERNEL_COMPILED
    if _KERNEL is not None and _KERNEL_COMPILED:
        return _KERNEL, 0.0
    with _STATE_LOCK:
        if _KERNEL is None:
            try:
                _KERNEL = _make_kernel()
            except Exception as exc:
                raise NativeCpuWavefrontProviderError(
                    "initialize",
                    "numba_kernel_create_failed",
                ) from exc
        if _KERNEL_COMPILED:
            return _KERNEL, 0.0
        started = time.perf_counter()
        try:
            empty_vectors = np.empty((0, 3), dtype=np.float64)
            empty_floats = np.empty(0, dtype=np.float64)
            empty_scatter = np.empty(0, dtype=np.int8)
            for values in (empty_vectors, empty_floats, empty_scatter):
                values.setflags(write=False)
            _KERNEL(
                empty_vectors,
                empty_vectors,
                empty_floats,
                empty_floats,
                empty_floats,
                empty_scatter,
                0,
                1,
                0.0,
                TERMINATION_THRESHOLD,
                True,
                *_allocate_outputs(0),
            )
        except Exception as exc:
            raise NativeCpuWavefrontProviderError(
                "initialize",
                "numba_jit_compile_failed",
            ) from exc
        _KERNEL_COMPILED = True
        return _KERNEL, time.perf_counter() - started


def _allocate_outputs(
    row_count: int,
) -> tuple[BoolArray, FloatArray, FloatArray, FloatArray, UInt16Array, Int8Array]:
    return (
        np.zeros(row_count, dtype=np.bool_),
        np.zeros(row_count, dtype=np.float64),
        np.zeros(row_count, dtype=np.float64),
        np.zeros((row_count, 3), dtype=np.float64),
        np.zeros(row_count, dtype=np.uint16),
        np.full(row_count, LOBE_NONE, dtype=np.int8),
    )


def _result_from_arrays(
    arrays: tuple[
        BoolArray,
        FloatArray,
        FloatArray,
        FloatArray,
        UInt16Array,
        Int8Array,
    ],
) -> WavefrontPlanResult:
    return WavefrontPlanResult(
        supported_mask=arrays[0],
        reflected_power_lumen=arrays[1],
        emitted_power_lumen=arrays[2],
        emitted_directions=arrays[3],
        status_flags=arrays[4],
        lobe_codes=arrays[5],
    )


def _validate_native_result(
    batch: WavefrontPlanInput,
    result: WavefrontPlanResult,
) -> None:
    expected_supported = np.isin(
        batch.scatter_models,
        np.asarray([SCATTER_NONE, SCATTER_SPECULAR], dtype=np.int8),
    )
    if batch.termination_mode != TERMINATION_THRESHOLD:
        expected_supported[:] = False
    if not np.array_equal(result.supported_mask, expected_supported):
        raise ValueError("native supported_mask violates the deterministic contract")
    unsupported = ~result.supported_mask
    if np.any(
        result.status_flags[unsupported]
        != np.uint16(STATUS_UNSUPPORTED)
    ):
        raise ValueError("unsupported rows must carry STATUS_UNSUPPORTED only")
    if np.any(result.lobe_codes[unsupported] != LOBE_NONE):
        raise ValueError("unsupported rows must not report a lobe")
    if np.any(result.reflected_power_lumen[unsupported] != 0.0):
        raise ValueError("unsupported rows must not report reflected power")
    if np.any(result.emitted_power_lumen[unsupported] != 0.0):
        raise ValueError("unsupported rows must not report emitted power")
    if np.any(result.emitted_directions[unsupported] != 0.0):
        raise ValueError("unsupported rows must not report a direction")

    expected_status = np.full(
        len(batch),
        STATUS_UNSUPPORTED,
        dtype=np.uint16,
    )
    if batch.depth >= batch.max_depth:
        expected_status[result.supported_mask] = (
            STATUS_DEPTH_LIMITED | STATUS_DISABLED
        )
    else:
        expected_status[result.supported_mask] = STATUS_ATTEMPTED
        below_energy = (
            result.supported_mask
            & (batch.min_energy > 0.0)
            & (result.reflected_power_lumen < batch.min_energy)
        )
        disabled = (
            result.supported_mask
            & ~below_energy
            & (
                (batch.scatter_models == SCATTER_NONE)
                | (batch.profile_reflectance <= 0.0)
            )
        )
        emitted = result.supported_mask & ~below_energy & ~disabled
        expected_status[below_energy] |= STATUS_BELOW_ENERGY
        expected_status[disabled] |= STATUS_DISABLED
        expected_status[emitted] |= STATUS_EMITTED
    if not np.array_equal(result.status_flags, expected_status):
        raise ValueError("native status_flags violates the deterministic contract")

    emitted = (result.status_flags & np.uint16(STATUS_EMITTED)) != 0
    if np.any(result.lobe_codes[emitted] != LOBE_SPECULAR):
        raise ValueError("emitted deterministic rows must use the specular lobe")
    if np.any(result.lobe_codes[~emitted] != LOBE_NONE):
        raise ValueError("non-emitted rows must not report a lobe")
    if np.any(result.emitted_power_lumen[~emitted] != 0.0):
        raise ValueError("non-emitted rows must not report emitted power")
    if np.any(result.emitted_directions[~emitted] != 0.0):
        raise ValueError("non-emitted rows must not report a direction")
    if not np.array_equal(
        result.emitted_power_lumen[emitted].view(np.uint64),
        result.reflected_power_lumen[emitted].view(np.uint64),
    ):
        raise ValueError("emitted power must exactly match reflected power")


def _readonly_vectors(values: ArrayLike, name: str) -> FloatArray:
    result = np.array(values, dtype=np.float64, order="C", copy=True)
    if result.ndim != 2 or result.shape[1:] != (3,):
        raise ValueError(f"{name} must have shape (N, 3)")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain only finite values")
    result.setflags(write=False)
    return result


def _readonly_float_rows(values: ArrayLike, name: str) -> FloatArray:
    result = np.array(values, dtype=np.float64, order="C", copy=True)
    if result.ndim != 1:
        raise ValueError(f"{name} must have shape (N,)")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain only finite values")
    result.setflags(write=False)
    return result


def _readonly_scatter_rows(values: ArrayLike) -> Int8Array:
    raw = np.asarray(values)
    if raw.ndim != 1:
        raise ValueError("scatter_models must have shape (N,)")
    if raw.dtype.kind not in {"i", "u"}:
        raise ValueError("scatter_models must contain integer codes")
    if any(int(value) not in _VALID_SCATTER_CODES for value in raw):
        raise ValueError("scatter_models contains an unknown code")
    result = np.array(raw, dtype=np.int8, order="C", copy=True)
    result.setflags(write=False)
    return result


def _readonly_output(values: ArrayLike, dtype: Any) -> NDArray[Any]:
    # Results are public snapshots: never freeze or alias an array owned by
    # the caller merely because it already has the requested dtype/layout.
    result = np.array(values, dtype=dtype, order="C", copy=True)
    result.setflags(write=False)
    return result


def _face_indices(values: ArrayLike) -> NDArray[np.int64]:
    raw = np.asarray(values)
    if raw.ndim != 1 or raw.dtype.kind not in {"i", "u"}:
        raise ValueError("face_indices must be a one-dimensional integer array")
    return np.ascontiguousarray(raw, dtype=np.int64)


def _face_float_table(values: ArrayLike, name: str) -> FloatArray:
    result = np.ascontiguousarray(np.asarray(values, dtype=np.float64))
    if result.ndim != 1:
        raise ValueError(f"{name} must have shape (F,)")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain only finite values")
    return result


def _scatter_table(values: ArrayLike) -> Int8Array:
    raw = np.asarray(values)
    if raw.ndim != 1 or raw.dtype.kind not in {"i", "u"}:
        raise ValueError("face_scatter_models must contain one integer code per face")
    if any(int(value) not in _VALID_SCATTER_CODES for value in raw):
        raise ValueError("face_scatter_models contains an unknown code")
    return np.ascontiguousarray(raw, dtype=np.int8)


def _non_negative_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer")
    normalized = int(value)
    if normalized < 0:
        raise ValueError(f"{name} must be non-negative")
    return normalized


def _choice_int(value: int, name: str, choices: frozenset[int]) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer code")
    normalized = int(value)
    if normalized not in choices:
        raise ValueError(f"{name} contains an unknown code")
    return normalized


__all__ = [
    "CONTRACT_VERSION",
    "LOBE_GAUSSIAN",
    "LOBE_LAMBERTIAN",
    "LOBE_NONE",
    "LOBE_SPECULAR",
    "NativeCpuWavefrontCapability",
    "NativeCpuWavefrontExecution",
    "NativeCpuWavefrontProviderError",
    "NativeCpuWavefrontUnavailable",
    "SCATTER_GAUSSIAN",
    "SCATTER_LAMBERTIAN",
    "SCATTER_MIXED",
    "SCATTER_NONE",
    "SCATTER_SPECULAR",
    "STATUS_ATTEMPTED",
    "STATUS_BELOW_ENERGY",
    "STATUS_DEPTH_LIMITED",
    "STATUS_DISABLED",
    "STATUS_EMITTED",
    "STATUS_ROULETTE_SURVIVED",
    "STATUS_ROULETTE_TERMINATED",
    "STATUS_UNSUPPORTED",
    "TERMINATION_RUSSIAN_ROULETTE",
    "TERMINATION_THRESHOLD",
    "WavefrontPlanInput",
    "WavefrontPlanResult",
    "plan_deterministic_native_cpu",
    "plan_deterministic_reference",
    "probe_native_cpu_wavefront",
    "scatter_codes_from_names",
    "scatter_name_from_code",
]
