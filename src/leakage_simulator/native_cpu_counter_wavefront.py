from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
from numbers import Integral
import threading
import time
from typing import Any, Callable, Optional

import numpy as np
from numpy.typing import ArrayLike, NDArray

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
    scatter_codes_from_names,
)


FloatArray = NDArray[np.float64]
Int8Array = NDArray[np.int8]
UInt8Array = NDArray[np.uint8]
UInt16Array = NDArray[np.uint16]
UInt64Array = NDArray[np.uint64]
BoolArray = NDArray[np.bool_]


CONTRACT_VERSION = "counter_rng_v2"
RNG_ALGORITHM = "splitmix64_semantic_lane_v1"
MAX_GAUSSIAN_ATTEMPTS = 32

# Semantic lanes are deliberately sparse and stable. Adding a future draw must
# use a new lane instead of shifting an existing lane. That property makes a
# row independently reproducible on Python, Numba CPU, and a future CUDA
# kernel without depending on compaction, thread scheduling, or rejection
# counts in another row.
LANE_ROULETTE = 0
LANE_MIXED_LOBE = 1
LANE_LAMBERTIAN_RADIAL = 2
LANE_LAMBERTIAN_AZIMUTH = 3
LANE_GAUSSIAN_RADIAL_BASE = 16
LANE_GAUSSIAN_AZIMUTH_BASE = 64

_MASK64 = (1 << 64) - 1
_DEPTH_SALT = 0xD2B74407B1CE6E93
_LANE_SALT = 0xCA5A826395121157
_STREAM_SALT = 0xA0761D6478BD642F
_TWO_POW_NEG_53 = 1.0 / float(1 << 53)
_TAU = 2.0 * math.pi

_VALID_SCATTER_CODES = frozenset(
    {
        SCATTER_NONE,
        SCATTER_SPECULAR,
        SCATTER_LAMBERTIAN,
        SCATTER_GAUSSIAN,
        SCATTER_MIXED,
    }
)
_VALID_TERMINATION_CODES = frozenset(
    {TERMINATION_THRESHOLD, TERMINATION_RUSSIAN_ROULETTE}
)


class NativeCpuCounterWavefrontUnavailable(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.phase = "probe"


class NativeCpuCounterWavefrontProviderError(RuntimeError):
    def __init__(self, phase: str, reason_code: str) -> None:
        super().__init__(reason_code)
        self.phase = phase
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class NativeCpuCounterWavefrontCapability:
    available: bool
    reason_code: Optional[str]
    numba_version: Optional[str]
    contract_version: str = CONTRACT_VERSION
    rng_algorithm: str = RNG_ALGORITHM


@dataclass(frozen=True, slots=True)
class CounterWavefrontPlanInput:
    """Owned immutable row input for stochastic reflection planning.

    ``rng_keys`` are stable per-primary 64-bit keys. A row's stochastic draws
    are addressed by key, depth, and semantic lane; rows may therefore be
    reordered, compacted, or dispatched in parallel without changing any
    other primary ray's stream.
    """

    incoming_directions: FloatArray | ArrayLike
    surface_normals: FloatArray | ArrayLike
    incoming_power_lumen: FloatArray | ArrayLike
    profile_reflectance: FloatArray | ArrayLike
    profile_roughness: FloatArray | ArrayLike
    scatter_models: Int8Array | ArrayLike
    profile_specular_ratio: FloatArray | ArrayLike
    profile_gaussian_sigma_deg: FloatArray | ArrayLike
    rng_keys: UInt64Array | ArrayLike
    depth: int
    max_depth: int
    min_energy: float
    termination_mode: int = TERMINATION_THRESHOLD

    def __post_init__(self) -> None:
        directions = _owned_readonly_vectors(
            self.incoming_directions,
            "incoming_directions",
        )
        normals = _owned_readonly_vectors(self.surface_normals, "surface_normals")
        powers = _owned_readonly_float_rows(
            self.incoming_power_lumen,
            "incoming_power_lumen",
        )
        reflectance = _owned_readonly_float_rows(
            self.profile_reflectance,
            "profile_reflectance",
        )
        roughness = _owned_readonly_float_rows(
            self.profile_roughness,
            "profile_roughness",
        )
        scatter = _owned_readonly_integer_rows(
            self.scatter_models,
            np.int8,
            "scatter_models",
        )
        specular_ratio = _owned_readonly_float_rows(
            self.profile_specular_ratio,
            "profile_specular_ratio",
        )
        gaussian_sigma = _owned_readonly_float_rows(
            self.profile_gaussian_sigma_deg,
            "profile_gaussian_sigma_deg",
        )
        rng_keys = _owned_readonly_integer_rows(
            self.rng_keys,
            np.uint64,
            "rng_keys",
        )
        row_count = len(directions)
        for name, values in (
            ("surface_normals", normals),
            ("incoming_power_lumen", powers),
            ("profile_reflectance", reflectance),
            ("profile_roughness", roughness),
            ("scatter_models", scatter),
            ("profile_specular_ratio", specular_ratio),
            ("profile_gaussian_sigma_deg", gaussian_sigma),
            ("rng_keys", rng_keys),
        ):
            if len(values) != row_count:
                raise ValueError(f"{name} must contain {row_count} rows")
        if np.any(powers < 0.0):
            raise ValueError("incoming_power_lumen must be non-negative")
        if np.any((reflectance < 0.0) | (reflectance > 1.0)):
            raise ValueError("profile_reflectance values must be between 0 and 1")
        if np.any((roughness < 0.0) | (roughness > 1.0)):
            raise ValueError("profile_roughness values must be between 0 and 1")
        if np.any((specular_ratio < 0.0) | (specular_ratio > 1.0)):
            raise ValueError("profile_specular_ratio values must be between 0 and 1")
        if np.any(gaussian_sigma <= 0.0):
            raise ValueError("profile_gaussian_sigma_deg values must be positive")
        if any(int(value) not in _VALID_SCATTER_CODES for value in scatter):
            raise ValueError("scatter_models contains an unknown value")

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

        arrays = (
            directions,
            normals,
            powers,
            reflectance,
            roughness,
            scatter,
            specular_ratio,
            gaussian_sigma,
            rng_keys,
        )
        _require_disjoint(arrays, "counter planner inputs")
        object.__setattr__(self, "incoming_directions", directions)
        object.__setattr__(self, "surface_normals", normals)
        object.__setattr__(self, "incoming_power_lumen", powers)
        object.__setattr__(self, "profile_reflectance", reflectance)
        object.__setattr__(self, "profile_roughness", roughness)
        object.__setattr__(self, "scatter_models", scatter)
        object.__setattr__(self, "profile_specular_ratio", specular_ratio)
        object.__setattr__(self, "profile_gaussian_sigma_deg", gaussian_sigma)
        object.__setattr__(self, "rng_keys", rng_keys)
        object.__setattr__(self, "depth", depth)
        object.__setattr__(self, "max_depth", max_depth)
        object.__setattr__(self, "min_energy", min_energy)
        object.__setattr__(self, "termination_mode", termination_mode)

    def __len__(self) -> int:
        return int(self.incoming_power_lumen.shape[0])


@dataclass(frozen=True, slots=True)
class CounterWavefrontPlanResult:
    supported_mask: BoolArray | ArrayLike
    reflected_power_lumen: FloatArray | ArrayLike
    emitted_power_lumen: FloatArray | ArrayLike
    emitted_directions: FloatArray | ArrayLike
    status_flags: UInt16Array | ArrayLike
    lobe_codes: Int8Array | ArrayLike
    rng_draw_counts: UInt8Array | ArrayLike

    def __post_init__(self) -> None:
        supported = _owned_readonly_output(self.supported_mask, np.bool_)
        reflected = _owned_readonly_output(
            self.reflected_power_lumen,
            np.float64,
        )
        emitted = _owned_readonly_output(self.emitted_power_lumen, np.float64)
        directions = _owned_readonly_output(self.emitted_directions, np.float64)
        statuses = _owned_readonly_output(self.status_flags, np.uint16)
        lobes = _owned_readonly_output(self.lobe_codes, np.int8)
        draw_counts = _owned_readonly_output(self.rng_draw_counts, np.uint8)
        if supported.ndim != 1:
            raise ValueError("supported_mask must have shape (N,)")
        row_count = len(supported)
        for name, values in (
            ("reflected_power_lumen", reflected),
            ("emitted_power_lumen", emitted),
            ("status_flags", statuses),
            ("lobe_codes", lobes),
            ("rng_draw_counts", draw_counts),
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
        arrays = (
            supported,
            reflected,
            emitted,
            directions,
            statuses,
            lobes,
            draw_counts,
        )
        _require_disjoint(arrays, "counter planner outputs")
        object.__setattr__(self, "supported_mask", supported)
        object.__setattr__(self, "reflected_power_lumen", reflected)
        object.__setattr__(self, "emitted_power_lumen", emitted)
        object.__setattr__(self, "emitted_directions", directions)
        object.__setattr__(self, "status_flags", statuses)
        object.__setattr__(self, "lobe_codes", lobes)
        object.__setattr__(self, "rng_draw_counts", draw_counts)

    def __len__(self) -> int:
        return int(self.supported_mask.shape[0])

    @property
    def supported_count(self) -> int:
        return int(np.count_nonzero(self.supported_mask))

    @property
    def stochastic_row_count(self) -> int:
        return int(np.count_nonzero(self.rng_draw_counts))


@dataclass(frozen=True, slots=True)
class NativeCpuCounterWavefrontExecution:
    result: CounterWavefrontPlanResult
    jit_compile_sec: float
    execute_sec: float
    result_validation_sec: float
    numba_version: str
    contract_version: str = CONTRACT_VERSION
    rng_algorithm: str = RNG_ALGORITHM


_STATE_LOCK = threading.RLock()
_CAPABILITY: Optional[NativeCpuCounterWavefrontCapability] = None
_KERNEL: Optional[Callable[..., None]] = None
_KERNEL_COMPILED = False


def probe_native_cpu_counter_wavefront() -> NativeCpuCounterWavefrontCapability:
    """Lazily probe Numba; importing this module never imports Numba."""

    global _CAPABILITY
    if _CAPABILITY is not None:
        return _CAPABILITY
    with _STATE_LOCK:
        if _CAPABILITY is not None:
            return _CAPABILITY
        try:
            numba = importlib.import_module("numba")
        except ModuleNotFoundError:
            _CAPABILITY = NativeCpuCounterWavefrontCapability(
                False,
                "numba_not_installed",
                None,
            )
        except Exception:
            _CAPABILITY = NativeCpuCounterWavefrontCapability(
                False,
                "numba_import_failed",
                None,
            )
        else:
            _CAPABILITY = NativeCpuCounterWavefrontCapability(
                True,
                None,
                str(getattr(numba, "__version__", "unknown")),
            )
        return _CAPABILITY


def counter_uniform(rng_key: int, depth: int, lane: int) -> float:
    """Return the contract's platform-independent 53-bit uniform sample."""

    key = int(rng_key) & _MASK64
    depth_value = _non_negative_int(depth, "depth")
    lane_value = _non_negative_int(lane, "lane")
    counter = (
        key
        ^ _STREAM_SALT
        ^ (((depth_value + 1) * _DEPTH_SALT) & _MASK64)
        ^ (((lane_value + 1) * _LANE_SALT) & _MASK64)
    ) & _MASK64
    mixed = _splitmix64(counter)
    return float(mixed >> 11) * _TWO_POW_NEG_53


def plan_counter_reference(
    batch: CounterWavefrontPlanInput,
) -> CounterWavefrontPlanResult:
    """Portable Python implementation of ``counter_rng_v2``.

    The reference is the no-Numba fallback and the statistical oracle for a
    future GPU implementation. Status/lobe decisions are required to match;
    transcendental direction coordinates may differ by normal libm ULPs.
    """

    if not isinstance(batch, CounterWavefrontPlanInput):
        raise TypeError("batch must be a CounterWavefrontPlanInput")
    arrays = _allocate_outputs(len(batch))
    for row_index in range(len(batch)):
        _plan_reference_row(batch, row_index, arrays)
    return _result_from_arrays(arrays)


def plan_counter_native_cpu(
    batch: CounterWavefrontPlanInput,
) -> NativeCpuCounterWavefrontExecution:
    """Execute every reflection model with a strict-float64 Numba kernel."""

    if not isinstance(batch, CounterWavefrontPlanInput):
        raise TypeError("batch must be a CounterWavefrontPlanInput")
    capability = probe_native_cpu_counter_wavefront()
    if not capability.available:
        raise NativeCpuCounterWavefrontUnavailable(
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
            batch.profile_specular_ratio,
            batch.profile_gaussian_sigma_deg,
            batch.rng_keys,
            batch.depth,
            batch.max_depth,
            batch.min_energy,
            batch.termination_mode,
            *arrays,
        )
    except Exception as exc:
        raise NativeCpuCounterWavefrontProviderError(
            "execute",
            "numba_counter_execute_failed",
        ) from exc
    execute_sec = time.perf_counter() - started
    validation_started = time.perf_counter()
    try:
        result = _result_from_arrays(arrays)
        _validate_result(batch, result)
    except Exception as exc:
        raise NativeCpuCounterWavefrontProviderError(
            "result_validation",
            "numba_counter_invalid_result",
        ) from exc
    return NativeCpuCounterWavefrontExecution(
        result=result,
        jit_compile_sec=jit_compile_sec,
        execute_sec=execute_sec,
        result_validation_sec=time.perf_counter() - validation_started,
        numba_version=capability.numba_version or "unknown",
    )


def _plan_reference_row(
    batch: CounterWavefrontPlanInput,
    row_index: int,
    arrays: tuple[
        BoolArray,
        FloatArray,
        FloatArray,
        FloatArray,
        UInt16Array,
        Int8Array,
        UInt8Array,
    ],
) -> None:
    supported, reflected, emitted, directions, statuses, lobes, draws = arrays
    supported[row_index] = True
    incoming = tuple(float(value) for value in batch.incoming_directions[row_index])
    normal = tuple(float(value) for value in batch.surface_normals[row_index])
    incoming_unit = _normalize(*incoming)
    surface_normal = _oriented_normal(incoming_unit, normal)
    reflectance = _effective_reflectance(
        incoming_unit,
        surface_normal,
        float(batch.profile_reflectance[row_index]),
        float(batch.profile_roughness[row_index]),
    )
    reflected_power = float(batch.incoming_power_lumen[row_index]) * reflectance
    reflected[row_index] = reflected_power
    if batch.depth >= batch.max_depth:
        statuses[row_index] = STATUS_DEPTH_LIMITED | STATUS_DISABLED
        return
    status = STATUS_ATTEMPTED
    emitted_power = reflected_power
    key = int(batch.rng_keys[row_index])
    if batch.min_energy > 0.0 and reflected_power < batch.min_energy:
        if batch.termination_mode == TERMINATION_THRESHOLD:
            statuses[row_index] = status | STATUS_BELOW_ENERGY
            return
        survival_probability = max(
            0.0,
            min(1.0, reflected_power / batch.min_energy),
        )
        draws[row_index] += 1
        if counter_uniform(key, batch.depth, LANE_ROULETTE) >= survival_probability:
            statuses[row_index] = (
                status | STATUS_BELOW_ENERGY | STATUS_ROULETTE_TERMINATED
            )
            return
        status |= STATUS_ROULETTE_SURVIVED
        emitted_power = batch.min_energy
    scatter = int(batch.scatter_models[row_index])
    base_reflectance = float(batch.profile_reflectance[row_index])
    if scatter == SCATTER_NONE or base_reflectance <= 0.0:
        statuses[row_index] = status | STATUS_DISABLED
        return
    specular = _ideal_specular(incoming_unit, surface_normal)
    if scatter == SCATTER_SPECULAR:
        direction = specular
        lobe = LOBE_SPECULAR
    elif scatter == SCATTER_LAMBERTIAN:
        direction = _lambertian_direction(key, batch.depth, surface_normal)
        draws[row_index] += 2
        lobe = LOBE_LAMBERTIAN
    elif scatter == SCATTER_GAUSSIAN:
        direction, draw_count = _gaussian_direction(
            key,
            batch.depth,
            specular,
            surface_normal,
            float(batch.profile_gaussian_sigma_deg[row_index]),
        )
        draws[row_index] += draw_count
        lobe = LOBE_GAUSSIAN
    else:
        draws[row_index] += 1
        if counter_uniform(key, batch.depth, LANE_MIXED_LOBE) < float(
            batch.profile_specular_ratio[row_index]
        ):
            if float(batch.profile_gaussian_sigma_deg[row_index]) <= 0.01:
                direction = specular
                lobe = LOBE_SPECULAR
            else:
                direction, draw_count = _gaussian_direction(
                    key,
                    batch.depth,
                    specular,
                    surface_normal,
                    float(batch.profile_gaussian_sigma_deg[row_index]),
                )
                draws[row_index] += draw_count
                lobe = LOBE_GAUSSIAN
        else:
            direction = _lambertian_direction(key, batch.depth, surface_normal)
            draws[row_index] += 2
            lobe = LOBE_LAMBERTIAN
    emitted[row_index] = emitted_power
    directions[row_index] = direction
    lobes[row_index] = lobe
    statuses[row_index] = status | STATUS_EMITTED


def _make_kernel() -> Callable[..., None]:
    numba = importlib.import_module("numba")

    @numba.njit(inline="always", fastmath=False)
    def splitmix64(value: np.uint64) -> np.uint64:
        value = value + np.uint64(0x9E3779B97F4A7C15)
        value = (value ^ (value >> np.uint64(30))) * np.uint64(
            0xBF58476D1CE4E5B9
        )
        value = (value ^ (value >> np.uint64(27))) * np.uint64(
            0x94D049BB133111EB
        )
        return value ^ (value >> np.uint64(31))

    @numba.njit(inline="always", fastmath=False)
    def uniform(key: np.uint64, depth: int, lane: int) -> float:
        counter = (
            key
            ^ np.uint64(_STREAM_SALT)
            ^ (np.uint64(depth + 1) * np.uint64(_DEPTH_SALT))
            ^ (np.uint64(lane + 1) * np.uint64(_LANE_SALT))
        )
        mixed = splitmix64(counter)
        return float(mixed >> np.uint64(11)) * _TWO_POW_NEG_53

    @numba.njit(inline="always", fastmath=False)
    def normalize(
        x_value: float,
        y_value: float,
        z_value: float,
    ) -> tuple[float, float, float]:
        magnitude_squared = (
            x_value * x_value + y_value * y_value + z_value * z_value
        )
        if magnitude_squared <= 1e-30:
            return 0.0, 0.0, 0.0
        inverse = 1.0 / math.sqrt(magnitude_squared)
        return x_value * inverse, y_value * inverse, z_value * inverse

    @numba.njit(inline="always", fastmath=False)
    def orient(
        incoming_x: float,
        incoming_y: float,
        incoming_z: float,
        normal_x: float,
        normal_y: float,
        normal_z: float,
    ) -> tuple[float, float, float]:
        surface_x, surface_y, surface_z = normalize(normal_x, normal_y, normal_z)
        if (
            incoming_x * surface_x
            + incoming_y * surface_y
            + incoming_z * surface_z
        ) > 0.0:
            return -surface_x, -surface_y, -surface_z
        return surface_x, surface_y, surface_z

    @numba.njit(inline="always", fastmath=False)
    def basis(
        w_x: float,
        w_y: float,
        w_z: float,
    ) -> tuple[float, float, float, float, float, float]:
        if abs(w_z) > 0.95:
            u_x, u_y, u_z = normalize(w_z, 0.0, -w_x)
        else:
            u_x, u_y, u_z = normalize(-w_y, w_x, 0.0)
        v_x = w_y * u_z - w_z * u_y
        v_y = w_z * u_x - w_x * u_z
        v_z = w_x * u_y - w_y * u_x
        return u_x, u_y, u_z, v_x, v_y, v_z

    @numba.njit(inline="always", fastmath=False)
    def specular(
        incoming_x: float,
        incoming_y: float,
        incoming_z: float,
        normal_x: float,
        normal_y: float,
        normal_z: float,
    ) -> tuple[float, float, float]:
        incidence = (
            incoming_x * normal_x
            + incoming_y * normal_y
            + incoming_z * normal_z
        )
        return (
            incoming_x - 2.0 * incidence * normal_x,
            incoming_y - 2.0 * incidence * normal_y,
            incoming_z - 2.0 * incidence * normal_z,
        )

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
    ) -> float:
        cosine_incidence = -(
            incoming_x * normal_x
            + incoming_y * normal_y
            + incoming_z * normal_z
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

    @numba.njit(inline="always", fastmath=False)
    def lambertian(
        key: np.uint64,
        depth: int,
        normal_x: float,
        normal_y: float,
        normal_z: float,
    ) -> tuple[float, float, float]:
        u_x, u_y, u_z, v_x, v_y, v_z = basis(normal_x, normal_y, normal_z)
        radial = uniform(key, depth, LANE_LAMBERTIAN_RADIAL)
        azimuth = uniform(key, depth, LANE_LAMBERTIAN_AZIMUTH)
        radius = math.sqrt(radial)
        phi = _TAU * azimuth
        x_value = radius * math.cos(phi)
        y_value = radius * math.sin(phi)
        z_value = math.sqrt(max(0.0, 1.0 - radial))
        return (
            u_x * x_value + v_x * y_value + normal_x * z_value,
            u_y * x_value + v_y * y_value + normal_y * z_value,
            u_z * x_value + v_z * y_value + normal_z * z_value,
        )

    @numba.njit(inline="always", fastmath=False)
    def gaussian(
        key: np.uint64,
        depth: int,
        axis_x: float,
        axis_y: float,
        axis_z: float,
        normal_x: float,
        normal_y: float,
        normal_z: float,
        sigma_deg: float,
    ) -> tuple[float, float, float, int]:
        u_x, u_y, u_z, v_x, v_y, v_z = basis(axis_x, axis_y, axis_z)
        sigma_rad = math.radians(max(1e-6, sigma_deg))
        for attempt in range(MAX_GAUSSIAN_ATTEMPTS):
            radial = max(
                1e-12,
                1.0 - uniform(
                    key,
                    depth,
                    LANE_GAUSSIAN_RADIAL_BASE + attempt,
                ),
            )
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

    @numba.njit(nogil=True, fastmath=False)
    def plan_kernel(
        incoming_directions: FloatArray,
        surface_normals: FloatArray,
        incoming_power_lumen: FloatArray,
        profile_reflectance: FloatArray,
        profile_roughness: FloatArray,
        scatter_models: Int8Array,
        profile_specular_ratio: FloatArray,
        profile_gaussian_sigma_deg: FloatArray,
        rng_keys: UInt64Array,
        depth: int,
        max_depth: int,
        min_energy: float,
        termination_mode: int,
        supported_mask: BoolArray,
        reflected_power_lumen: FloatArray,
        emitted_power_lumen: FloatArray,
        emitted_directions: FloatArray,
        status_flags: UInt16Array,
        lobe_codes: Int8Array,
        rng_draw_counts: UInt8Array,
    ) -> None:
        for row_index in range(incoming_power_lumen.shape[0]):
            supported_mask[row_index] = True
            reflected_power_lumen[row_index] = 0.0
            emitted_power_lumen[row_index] = 0.0
            emitted_directions[row_index, 0] = 0.0
            emitted_directions[row_index, 1] = 0.0
            emitted_directions[row_index, 2] = 0.0
            status_flags[row_index] = 0
            lobe_codes[row_index] = LOBE_NONE
            rng_draw_counts[row_index] = 0

            incoming_x, incoming_y, incoming_z = normalize(
                incoming_directions[row_index, 0],
                incoming_directions[row_index, 1],
                incoming_directions[row_index, 2],
            )
            normal_x, normal_y, normal_z = orient(
                incoming_x,
                incoming_y,
                incoming_z,
                surface_normals[row_index, 0],
                surface_normals[row_index, 1],
                surface_normals[row_index, 2],
            )
            reflected_power = incoming_power_lumen[row_index] * effective_reflectance(
                incoming_x,
                incoming_y,
                incoming_z,
                normal_x,
                normal_y,
                normal_z,
                profile_reflectance[row_index],
                profile_roughness[row_index],
            )
            reflected_power_lumen[row_index] = reflected_power
            if depth >= max_depth:
                status_flags[row_index] = STATUS_DEPTH_LIMITED | STATUS_DISABLED
                continue

            status = STATUS_ATTEMPTED
            emitted_power = reflected_power
            key = rng_keys[row_index]
            if min_energy > 0.0 and reflected_power < min_energy:
                if termination_mode == TERMINATION_THRESHOLD:
                    status_flags[row_index] = status | STATUS_BELOW_ENERGY
                    continue
                survival_probability = reflected_power / min_energy
                if survival_probability < 0.0:
                    survival_probability = 0.0
                elif survival_probability > 1.0:
                    survival_probability = 1.0
                rng_draw_counts[row_index] += 1
                if uniform(key, depth, LANE_ROULETTE) >= survival_probability:
                    status_flags[row_index] = (
                        status | STATUS_BELOW_ENERGY | STATUS_ROULETTE_TERMINATED
                    )
                    continue
                status |= STATUS_ROULETTE_SURVIVED
                emitted_power = min_energy

            scatter = scatter_models[row_index]
            if scatter == SCATTER_NONE or profile_reflectance[row_index] <= 0.0:
                status_flags[row_index] = status | STATUS_DISABLED
                continue
            specular_x, specular_y, specular_z = specular(
                incoming_x,
                incoming_y,
                incoming_z,
                normal_x,
                normal_y,
                normal_z,
            )
            lobe = LOBE_SPECULAR
            direction_x = specular_x
            direction_y = specular_y
            direction_z = specular_z
            if scatter == SCATTER_LAMBERTIAN:
                direction_x, direction_y, direction_z = lambertian(
                    key,
                    depth,
                    normal_x,
                    normal_y,
                    normal_z,
                )
                rng_draw_counts[row_index] += 2
                lobe = LOBE_LAMBERTIAN
            elif scatter == SCATTER_GAUSSIAN:
                direction_x, direction_y, direction_z, draw_count = gaussian(
                    key,
                    depth,
                    specular_x,
                    specular_y,
                    specular_z,
                    normal_x,
                    normal_y,
                    normal_z,
                    profile_gaussian_sigma_deg[row_index],
                )
                rng_draw_counts[row_index] += draw_count
                lobe = LOBE_GAUSSIAN
            elif scatter == SCATTER_MIXED:
                rng_draw_counts[row_index] += 1
                if uniform(key, depth, LANE_MIXED_LOBE) < profile_specular_ratio[
                    row_index
                ]:
                    if profile_gaussian_sigma_deg[row_index] <= 0.01:
                        lobe = LOBE_SPECULAR
                    else:
                        direction_x, direction_y, direction_z, draw_count = gaussian(
                            key,
                            depth,
                            specular_x,
                            specular_y,
                            specular_z,
                            normal_x,
                            normal_y,
                            normal_z,
                            profile_gaussian_sigma_deg[row_index],
                        )
                        rng_draw_counts[row_index] += draw_count
                        lobe = LOBE_GAUSSIAN
                else:
                    direction_x, direction_y, direction_z = lambertian(
                        key,
                        depth,
                        normal_x,
                        normal_y,
                        normal_z,
                    )
                    rng_draw_counts[row_index] += 2
                    lobe = LOBE_LAMBERTIAN

            emitted_power_lumen[row_index] = emitted_power
            emitted_directions[row_index, 0] = direction_x
            emitted_directions[row_index, 1] = direction_y
            emitted_directions[row_index, 2] = direction_z
            lobe_codes[row_index] = lobe
            status_flags[row_index] = status | STATUS_EMITTED
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
                raise NativeCpuCounterWavefrontProviderError(
                    "initialize",
                    "numba_counter_kernel_create_failed",
                ) from exc
        if _KERNEL_COMPILED:
            return _KERNEL, 0.0
        started = time.perf_counter()
        try:
            empty_vectors = np.empty((0, 3), dtype=np.float64)
            empty_floats = np.empty(0, dtype=np.float64)
            empty_scatter = np.empty(0, dtype=np.int8)
            empty_keys = np.empty(0, dtype=np.uint64)
            for values in (
                empty_vectors,
                empty_floats,
                empty_scatter,
                empty_keys,
            ):
                values.setflags(write=False)
            _KERNEL(
                empty_vectors,
                empty_vectors,
                empty_floats,
                empty_floats,
                empty_floats,
                empty_scatter,
                empty_floats,
                empty_floats,
                empty_keys,
                0,
                1,
                0.0,
                TERMINATION_THRESHOLD,
                *_allocate_outputs(0),
            )
        except Exception as exc:
            raise NativeCpuCounterWavefrontProviderError(
                "initialize",
                "numba_counter_jit_compile_failed",
            ) from exc
        _KERNEL_COMPILED = True
        return _KERNEL, time.perf_counter() - started


def _validate_result(
    batch: CounterWavefrontPlanInput,
    result: CounterWavefrontPlanResult,
) -> None:
    row_count = len(batch)
    if len(result) != row_count or not bool(np.all(result.supported_mask)):
        raise ValueError("counter planner must support every validated row")
    emitted_mask = (result.status_flags & STATUS_EMITTED) != 0
    if np.any(emitted_mask != (result.lobe_codes != LOBE_NONE)):
        raise ValueError("emitted status and lobe code disagree")
    if np.any(emitted_mask != (result.emitted_power_lumen > 0.0)):
        zero_power_emissions = emitted_mask & (result.emitted_power_lumen == 0.0)
        if np.any(zero_power_emissions):
            # Zero input power with min_energy=0 is a valid emitted ray under
            # the established planner semantics.
            if np.any(batch.incoming_power_lumen[zero_power_emissions] != 0.0):
                raise ValueError("emitted status and power disagree")
        if np.any((~emitted_mask) & (result.emitted_power_lumen != 0.0)):
            raise ValueError("non-emitted row carries power")
    if np.any((~emitted_mask) & np.any(result.emitted_directions != 0.0, axis=1)):
        raise ValueError("non-emitted row carries a direction")
    if np.any(result.reflected_power_lumen > batch.incoming_power_lumen * (1.0 + 1e-12)):
        raise ValueError("reflected power exceeds incoming power")
    if np.any(result.rng_draw_counts > 67):
        raise ValueError("rng draw count exceeds the contract maximum")
    if np.any(emitted_mask):
        magnitudes = np.linalg.norm(result.emitted_directions[emitted_mask], axis=1)
        if np.any(np.abs(magnitudes - 1.0) > 1e-10):
            raise ValueError("emitted direction is not unit length")


def _allocate_outputs(
    row_count: int,
) -> tuple[
    BoolArray,
    FloatArray,
    FloatArray,
    FloatArray,
    UInt16Array,
    Int8Array,
    UInt8Array,
]:
    return (
        np.zeros(row_count, dtype=np.bool_),
        np.zeros(row_count, dtype=np.float64),
        np.zeros(row_count, dtype=np.float64),
        np.zeros((row_count, 3), dtype=np.float64),
        np.zeros(row_count, dtype=np.uint16),
        np.full(row_count, LOBE_NONE, dtype=np.int8),
        np.zeros(row_count, dtype=np.uint8),
    )


def _result_from_arrays(
    arrays: tuple[
        BoolArray,
        FloatArray,
        FloatArray,
        FloatArray,
        UInt16Array,
        Int8Array,
        UInt8Array,
    ],
) -> CounterWavefrontPlanResult:
    return CounterWavefrontPlanResult(
        supported_mask=arrays[0],
        reflected_power_lumen=arrays[1],
        emitted_power_lumen=arrays[2],
        emitted_directions=arrays[3],
        status_flags=arrays[4],
        lobe_codes=arrays[5],
        rng_draw_counts=arrays[6],
    )


def _splitmix64(value: int) -> int:
    value = (int(value) + 0x9E3779B97F4A7C15) & _MASK64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK64
    return value ^ (value >> 31)


def _normalize(x_value: float, y_value: float, z_value: float) -> tuple[float, float, float]:
    magnitude_squared = x_value * x_value + y_value * y_value + z_value * z_value
    if magnitude_squared <= 1e-30:
        return 0.0, 0.0, 0.0
    inverse = 1.0 / math.sqrt(magnitude_squared)
    return x_value * inverse, y_value * inverse, z_value * inverse


def _oriented_normal(
    incoming: tuple[float, float, float],
    normal: tuple[float, float, float],
) -> tuple[float, float, float]:
    surface = _normalize(*normal)
    if sum(incoming[index] * surface[index] for index in range(3)) > 0.0:
        return -surface[0], -surface[1], -surface[2]
    return surface


def _effective_reflectance(
    incoming: tuple[float, float, float],
    normal: tuple[float, float, float],
    base: float,
    roughness: float,
) -> float:
    cosine = max(0.0, min(1.0, -sum(incoming[index] * normal[index] for index in range(3))))
    coordinate = max(0.0, (0.7 - cosine) / 0.7)
    gloss = 0.25 + 0.75 * (1.0 - roughness)
    return max(0.0, min(1.0, base + (1.0 - base) * coordinate**5 * gloss))


def _ideal_specular(
    incoming: tuple[float, float, float],
    normal: tuple[float, float, float],
) -> tuple[float, float, float]:
    incidence = sum(incoming[index] * normal[index] for index in range(3))
    return tuple(
        incoming[index] - 2.0 * incidence * normal[index]
        for index in range(3)
    )  # type: ignore[return-value]


def _basis(
    w_axis: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    if abs(w_axis[2]) > 0.95:
        u_axis = _normalize(w_axis[2], 0.0, -w_axis[0])
    else:
        u_axis = _normalize(-w_axis[1], w_axis[0], 0.0)
    v_axis = (
        w_axis[1] * u_axis[2] - w_axis[2] * u_axis[1],
        w_axis[2] * u_axis[0] - w_axis[0] * u_axis[2],
        w_axis[0] * u_axis[1] - w_axis[1] * u_axis[0],
    )
    return u_axis, v_axis


def _lambertian_direction(
    key: int,
    depth: int,
    normal: tuple[float, float, float],
) -> tuple[float, float, float]:
    u_axis, v_axis = _basis(normal)
    radial = counter_uniform(key, depth, LANE_LAMBERTIAN_RADIAL)
    azimuth = counter_uniform(key, depth, LANE_LAMBERTIAN_AZIMUTH)
    radius = math.sqrt(radial)
    phi = _TAU * azimuth
    x_value = radius * math.cos(phi)
    y_value = radius * math.sin(phi)
    z_value = math.sqrt(max(0.0, 1.0 - radial))
    return tuple(
        u_axis[index] * x_value
        + v_axis[index] * y_value
        + normal[index] * z_value
        for index in range(3)
    )  # type: ignore[return-value]


def _gaussian_direction(
    key: int,
    depth: int,
    axis: tuple[float, float, float],
    normal: tuple[float, float, float],
    sigma_deg: float,
) -> tuple[tuple[float, float, float], int]:
    u_axis, v_axis = _basis(axis)
    sigma_rad = math.radians(max(1e-6, sigma_deg))
    for attempt in range(MAX_GAUSSIAN_ATTEMPTS):
        radial = max(
            1e-12,
            1.0 - counter_uniform(
                key,
                depth,
                LANE_GAUSSIAN_RADIAL_BASE + attempt,
            ),
        )
        theta = sigma_rad * math.sqrt(-2.0 * math.log(radial))
        if theta >= math.pi * 0.5:
            continue
        phi = _TAU * counter_uniform(
            key,
            depth,
            LANE_GAUSSIAN_AZIMUTH_BASE + attempt,
        )
        cosine = math.cos(theta)
        sine = math.sin(theta)
        u_scale = sine * math.cos(phi)
        v_scale = sine * math.sin(phi)
        direction = tuple(
            axis[index] * cosine
            + u_axis[index] * u_scale
            + v_axis[index] * v_scale
            for index in range(3)
        )
        if sum(direction[index] * normal[index] for index in range(3)) > 1e-9:
            return direction, (attempt + 1) * 2  # type: ignore[return-value]
    return axis, MAX_GAUSSIAN_ATTEMPTS * 2


def _owned_readonly_vectors(values: ArrayLike, name: str) -> FloatArray:
    result = np.array(values, dtype=np.float64, order="C", copy=True)
    if result.ndim != 2 or result.shape[1:] != (3,):
        raise ValueError(f"{name} must have shape (N, 3)")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain finite values")
    result.setflags(write=False)
    return result


def _owned_readonly_float_rows(values: ArrayLike, name: str) -> FloatArray:
    result = np.array(values, dtype=np.float64, order="C", copy=True)
    if result.ndim != 1:
        raise ValueError(f"{name} must have shape (N,)")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain finite values")
    result.setflags(write=False)
    return result


def _owned_readonly_integer_rows(
    values: ArrayLike,
    dtype: Any,
    name: str,
) -> np.ndarray:
    source = np.asarray(values)
    if source.ndim != 1:
        raise ValueError(f"{name} must have shape (N,)")
    if source.dtype.kind not in "biu":
        raise ValueError(f"{name} must contain integers")
    result = np.array(source, dtype=dtype, order="C", copy=True)
    if not np.array_equal(source, result):
        raise ValueError(f"{name} contains a value outside its integer type")
    result.setflags(write=False)
    return result


def _owned_readonly_output(values: ArrayLike, dtype: Any) -> np.ndarray:
    result = np.array(values, dtype=dtype, order="C", copy=True)
    result.setflags(write=False)
    return result


def _require_disjoint(arrays: tuple[np.ndarray, ...], scope: str) -> None:
    for index, first in enumerate(arrays):
        if not first.flags.owndata or not first.flags.c_contiguous:
            raise ValueError(f"{scope} must be owned and C-contiguous")
        for second in arrays[index + 1 :]:
            if np.shares_memory(first, second):
                raise ValueError(f"{scope} must not alias")


def _non_negative_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be a non-negative integer")
    normalized = int(value)
    if normalized < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return normalized


def _choice_int(value: int, name: str, choices: frozenset[int]) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer")
    normalized = int(value)
    if normalized not in choices:
        raise ValueError(f"{name} contains an unsupported value")
    return normalized


__all__ = [
    "CONTRACT_VERSION",
    "RNG_ALGORITHM",
    "CounterWavefrontPlanInput",
    "CounterWavefrontPlanResult",
    "NativeCpuCounterWavefrontCapability",
    "NativeCpuCounterWavefrontExecution",
    "NativeCpuCounterWavefrontProviderError",
    "NativeCpuCounterWavefrontUnavailable",
    "counter_uniform",
    "plan_counter_native_cpu",
    "plan_counter_reference",
    "probe_native_cpu_counter_wavefront",
    "scatter_codes_from_names",
]
