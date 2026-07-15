from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple
import math
import random
import uuid

Vec3 = Tuple[float, float, float]
Vec2 = Tuple[float, float]

def clamp(value: float, min_value: float, max_value: float) -> float:
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def vec3_from(value: Any, field_name: str) -> Vec3:
    if value is None:
        raise ValueError(f"{field_name} requires x,y,z")
    values = tuple(float(v) for v in value)
    if len(values) != 3:
        raise ValueError(f"{field_name} requires exactly 3 values")
    return values  # type: ignore[return-value]


def vec2_from(value: Any, field_name: str) -> Vec2:
    if value is None:
        raise ValueError(f"{field_name} requires x,y")
    values = tuple(float(v) for v in value)
    if len(values) != 2:
        raise ValueError(f"{field_name} requires exactly 2 values")
    return values  # type: ignore[return-value]


def int_pair_from(value: Any, field_name: str) -> Tuple[int, int]:
    if value is None:
        raise ValueError(f"{field_name} requires two integer values")
    values = tuple(int(v) for v in value)
    if len(values) != 2:
        raise ValueError(f"{field_name} requires exactly 2 values")
    return values  # type: ignore[return-value]


def normalize_vec3(value: Vec3, field_name: str) -> Vec3:
    x, y, z = vec3_from(value, field_name)
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 1e-12:
        raise ValueError(f"{field_name} must not be zero length")
    return (x / length, y / length, z / length)


def require_choice(value: str, field_name: str, choices: Tuple[str, ...]) -> str:
    if value not in choices:
        raise ValueError(f"{field_name} must be one of {', '.join(choices)}")
    return value


def require_positive(value: float, field_name: str) -> float:
    value = float(value)
    if value <= 0.0:
        raise ValueError(f"{field_name} must be positive")
    return value


def require_non_negative(value: float, field_name: str) -> float:
    value = float(value)
    if value < 0.0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def require_positive_int(value: int, field_name: str) -> int:
    value = int(value)
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


EMITTER_TYPES = ("face", "datum_plane", "reference_plane")
EMITTER_NORMAL_MODES = ("face_normal", "custom")
EMITTER_DISTRIBUTIONS = ("lambertian", "isotropic", "gaussian")
EMITTER_POWER_MODES = ("total", "power_per_area")
RECEIVER_TYPES = ("rectangle",)
SCATTER_MODELS = ("none", "lambertian", "gaussian")
TERMINATION_MODES = ("threshold", "russian_roulette")


@dataclass
class MaterialProfile:
    material_id: str
    name: str
    reflectance_total: float
    diffuse_ratio: float
    specular_ratio: float
    roughness: float
    absorption_ratio: float = 0.0
    alpha: float = 1.0

    def __post_init__(self) -> None:
        self.reflectance_total = clamp(self.reflectance_total, 0.0, 1.0)
        self.diffuse_ratio = clamp(self.diffuse_ratio, 0.0, 1.0)
        self.specular_ratio = clamp(self.specular_ratio, 0.0, 1.0)
        self.absorption_ratio = clamp(self.absorption_ratio, 0.0, 1.0)
        self.alpha = clamp(self.alpha, 0.0, 1.0)


@dataclass
class EmitterConfig:
    source_id: str
    emitter_type: str
    strength: float
    direction_mode: str
    normal_hint: Optional[Vec3] = None
    face_index: Optional[int] = None
    box_min: Optional[Vec3] = None
    box_max: Optional[Vec3] = None
    sphere_center: Optional[Vec3] = None
    sphere_radius: Optional[float] = None
    direction_distribution: str = "isotropic"
    enabled: bool = True


@dataclass
class GapRule:
    rule_id: str
    nominal_gap_mm: float
    target_face_indices: List[int] = field(default_factory=list)
    sigma_gap_mm: float = 0.0
    enable_tunnel: bool = True
    max_depth_penetration: int = 1
    transmissive_threshold: float = 0.4
    gap_mode: str = "face_gap"
    target_component_ids: List[int] = field(default_factory=list)
    move_vector_mm: Optional[Vec3] = None
    rotation_vector_deg: Optional[Vec3] = None
    bbox_min: Optional[Vec3] = None
    bbox_max: Optional[Vec3] = None


@dataclass
class ReceiverPatchConfig:
    receiver_id: str
    face_indices: List[int]
    weight: float = 1.0


@dataclass
class EmitterSpec:
    emitter_id: str
    emitter_type: str = "face"
    face_indices: List[int] = field(default_factory=list)
    normal_mode: str = "face_normal"
    normal_flip: bool = False
    custom_normal: Optional[Vec3] = None
    direction_distribution: str = "lambertian"
    gaussian_sigma_deg: float = 12.0
    power_mode: str = "total"
    power_lumen: float = 1.0
    power_density_lm_per_m2: float = 100.0
    center: Optional[Vec3] = None
    u_axis: Optional[Vec3] = None
    v_axis: Optional[Vec3] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    reference_mode: Optional[str] = None
    reference_vertex_indices: List[int] = field(default_factory=list)
    reference_edge_vertex_indices: List[Tuple[int, int]] = field(default_factory=list)
    ray_count: int = 10000
    seed: Optional[int] = None
    enabled: bool = True

    def __post_init__(self) -> None:
        self.emitter_type = require_choice(self.emitter_type, "emitter_type", EMITTER_TYPES)
        self.normal_mode = require_choice(self.normal_mode, "normal_mode", EMITTER_NORMAL_MODES)
        self.direction_distribution = require_choice(
            self.direction_distribution,
            "direction_distribution",
            EMITTER_DISTRIBUTIONS,
        )
        self.power_mode = require_choice(self.power_mode, "power_mode", EMITTER_POWER_MODES)
        self.face_indices = [int(face_index) for face_index in self.face_indices]
        if self.emitter_type == "face" and not self.face_indices:
            raise ValueError("face emitter requires at least one face index")
        if self.emitter_type in ("datum_plane", "reference_plane"):
            if self.center is None or self.u_axis is None or self.v_axis is None:
                raise ValueError("virtual plane emitter requires center, u_axis and v_axis")
            self.center = vec3_from(self.center, "center")
            self.u_axis = normalize_vec3(self.u_axis, "u_axis")
            self.v_axis = normalize_vec3(self.v_axis, "v_axis")
            cross = (
                self.u_axis[1] * self.v_axis[2] - self.u_axis[2] * self.v_axis[1],
                self.u_axis[2] * self.v_axis[0] - self.u_axis[0] * self.v_axis[2],
                self.u_axis[0] * self.v_axis[1] - self.u_axis[1] * self.v_axis[0],
            )
            if math.sqrt(sum(value * value for value in cross)) <= 1e-9:
                raise ValueError("u_axis and v_axis must not be parallel")
            self.width_mm = require_positive(self.width_mm, "width_mm")
            self.height_mm = require_positive(self.height_mm, "height_mm")
        if self.normal_mode == "custom":
            if self.custom_normal is None:
                raise ValueError("custom normal mode requires custom_normal")
            self.custom_normal = normalize_vec3(self.custom_normal, "custom_normal")
        self.gaussian_sigma_deg = require_positive(self.gaussian_sigma_deg, "gaussian_sigma_deg")
        self.power_lumen = require_non_negative(self.power_lumen, "power_lumen")
        self.power_density_lm_per_m2 = require_non_negative(
            self.power_density_lm_per_m2,
            "power_density_lm_per_m2",
        )
        self.reference_vertex_indices = [int(index) for index in self.reference_vertex_indices]
        self.reference_edge_vertex_indices = [
            (int(edge[0]), int(edge[1])) for edge in self.reference_edge_vertex_indices
        ]
        self.ray_count = require_positive_int(self.ray_count, "ray_count")

    def effective_power_lumen(self, area_mm2: float) -> float:
        if self.power_mode == "power_per_area":
            return self.power_density_lm_per_m2 * max(0.0, float(area_mm2)) * 1e-6
        return self.power_lumen

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict) -> "EmitterSpec":
        return cls(**payload)


@dataclass
class ReceiverSpec:
    receiver_id: str
    receiver_type: str = "rectangle"
    center: Vec3 = (0.0, 0.0, 0.0)
    normal: Vec3 = (0.0, 0.0, 1.0)
    width_mm: float = 100.0
    height_mm: float = 30.0
    resolution: Tuple[int, int] = (80, 24)
    acceptance_angle_deg: float = 90.0
    enabled: bool = True

    def __post_init__(self) -> None:
        self.receiver_type = require_choice(self.receiver_type, "receiver_type", RECEIVER_TYPES)
        self.center = vec3_from(self.center, "center")
        self.normal = normalize_vec3(self.normal, "normal")
        self.width_mm = require_positive(self.width_mm, "width_mm")
        self.height_mm = require_positive(self.height_mm, "height_mm")
        self.resolution = int_pair_from(self.resolution, "resolution")
        if self.resolution[0] <= 0 or self.resolution[1] <= 0:
            raise ValueError("resolution values must be positive")
        self.acceptance_angle_deg = float(self.acceptance_angle_deg)
        if self.acceptance_angle_deg <= 0.0 or self.acceptance_angle_deg > 180.0:
            raise ValueError("acceptance_angle_deg must be within (0, 180]")

    def bin_area_mm2(self) -> float:
        column_count, row_count = self.resolution
        return (self.width_mm * self.height_mm) / float(column_count * row_count)

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict) -> "ReceiverSpec":
        return cls(**payload)


@dataclass
class OpticalProfile:
    profile_id: str
    reflectance: float
    absorption: Optional[float] = None
    specular_ratio: float = 0.0
    diffuse_ratio: float = 1.0
    scatter_model: str = "lambertian"
    roughness: float = 0.5
    gaussian_sigma_deg: float = 18.0
    bsdf_asset_id: Optional[str] = None
    notes: str = ""

    def __post_init__(self) -> None:
        self.reflectance = clamp(float(self.reflectance), 0.0, 1.0)
        if self.absorption is None:
            self.absorption = 1.0 - self.reflectance
        self.absorption = clamp(float(self.absorption), 0.0, 1.0)
        self.specular_ratio = clamp(float(self.specular_ratio), 0.0, 1.0)
        self.diffuse_ratio = clamp(float(self.diffuse_ratio), 0.0, 1.0)
        ratio_sum = self.specular_ratio + self.diffuse_ratio
        if ratio_sum > 1.0:
            self.specular_ratio = self.specular_ratio / ratio_sum
            self.diffuse_ratio = self.diffuse_ratio / ratio_sum
        self.scatter_model = require_choice(self.scatter_model, "scatter_model", SCATTER_MODELS)
        self.roughness = clamp(float(self.roughness), 0.0, 1.0)
        self.gaussian_sigma_deg = require_positive(self.gaussian_sigma_deg, "gaussian_sigma_deg")

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict) -> "OpticalProfile":
        return cls(**payload)


@dataclass
class RayTraceConfig:
    ray_count: int = 10000
    max_depth: int = 1
    seed: int = 42
    min_energy: float = 1e-9
    epsilon_mm: float = 1e-4
    k_abs: float = 0.12
    k_brdf: float = 1.0
    termination_mode: str = "threshold"
    store_ray_paths: bool = False
    max_stored_paths: int = 500

    def __post_init__(self) -> None:
        self.ray_count = require_positive_int(self.ray_count, "ray_count")
        self.max_depth = int(self.max_depth)
        if self.max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        self.seed = int(self.seed)
        self.min_energy = require_non_negative(self.min_energy, "min_energy")
        self.epsilon_mm = require_positive(self.epsilon_mm, "epsilon_mm")
        self.k_abs = require_non_negative(self.k_abs, "k_abs")
        self.k_brdf = require_non_negative(self.k_brdf, "k_brdf")
        self.termination_mode = require_choice(self.termination_mode, "termination_mode", TERMINATION_MODES)
        self.max_stored_paths = int(self.max_stored_paths)
        if self.max_stored_paths < 0:
            raise ValueError("max_stored_paths must be non-negative")

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict) -> "RayTraceConfig":
        return cls(**payload)


@dataclass
class RayHit:
    face_index: int
    component_id: Optional[int]
    material_id: Optional[str]
    point: Vec3
    normal: Vec3
    distance_mm: float
    incoming_energy_lumen: float
    outgoing_energy_lumen: float
    depth: int
    event_type: str = "surface"
    receiver_id: Optional[str] = None

    def __post_init__(self) -> None:
        self.face_index = int(self.face_index)
        if self.component_id is not None:
            self.component_id = int(self.component_id)
        self.point = vec3_from(self.point, "point")
        self.normal = normalize_vec3(self.normal, "normal")
        self.distance_mm = require_non_negative(self.distance_mm, "distance_mm")
        self.incoming_energy_lumen = require_non_negative(
            self.incoming_energy_lumen,
            "incoming_energy_lumen",
        )
        self.outgoing_energy_lumen = require_non_negative(
            self.outgoing_energy_lumen,
            "outgoing_energy_lumen",
        )
        self.depth = int(self.depth)
        if self.depth < 0:
            raise ValueError("depth must be non-negative")

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ReceiverGrid:
    receiver_id: str
    resolution: Tuple[int, int]
    bin_area_mm2: float
    flux_lumen: List[List[float]]
    hit_count: int = 0

    def __post_init__(self) -> None:
        self.resolution = int_pair_from(self.resolution, "resolution")
        if self.resolution[0] <= 0 or self.resolution[1] <= 0:
            raise ValueError("resolution values must be positive")
        self.bin_area_mm2 = require_positive(self.bin_area_mm2, "bin_area_mm2")
        expected_columns, expected_rows = self.resolution
        if len(self.flux_lumen) != expected_rows:
            raise ValueError("flux_lumen row count must match receiver resolution")
        for row in self.flux_lumen:
            if len(row) != expected_columns:
                raise ValueError("flux_lumen column count must match receiver resolution")
        self.hit_count = int(self.hit_count)
        if self.hit_count < 0:
            raise ValueError("hit_count must be non-negative")

    @classmethod
    def empty(cls, receiver: ReceiverSpec) -> "ReceiverGrid":
        column_count, row_count = receiver.resolution
        return cls(
            receiver_id=receiver.receiver_id,
            resolution=receiver.resolution,
            bin_area_mm2=receiver.bin_area_mm2(),
            flux_lumen=[[0.0 for _ in range(column_count)] for _ in range(row_count)],
            hit_count=0,
        )

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class RayTraceResult:
    run_id: str
    config: RayTraceConfig
    emitters: List[EmitterSpec]
    receivers: List[ReceiverSpec]
    receiver_grids: List[ReceiverGrid]
    optical_profiles: List[OpticalProfile]
    total_rays: int
    receiver_hit_count: int
    surface_hit_count: int
    terminated_ray_count: int
    runtime_sec: float = 0.0
    stored_paths: List[List[RayHit]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.total_rays = int(self.total_rays)
        self.receiver_hit_count = int(self.receiver_hit_count)
        self.surface_hit_count = int(self.surface_hit_count)
        self.terminated_ray_count = int(self.terminated_ray_count)
        self.runtime_sec = require_non_negative(self.runtime_sec, "runtime_sec")
        if self.total_rays < 0:
            raise ValueError("total_rays must be non-negative")
        if self.receiver_hit_count < 0:
            raise ValueError("receiver_hit_count must be non-negative")
        if self.surface_hit_count < 0:
            raise ValueError("surface_hit_count must be non-negative")
        if self.terminated_ray_count < 0:
            raise ValueError("terminated_ray_count must be non-negative")

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class RunConfig:
    ray_count: int = 4000
    max_depth: int = 2
    seed: int = 42
    k_abs: float = 0.12
    k_brdf: float = 1.0
    random_seed: Optional[int] = None


@dataclass
class RunResultSummary:
    run_id: str
    total_rays: int
    hit_count: int
    max_depth: int
    runtime_sec: float
    metadata: Dict


@dataclass
class ReceiverMetrics:
    receiver_id: str
    irradiance_sum: float
    peak_nit: float
    mean_nit: float
    p95_nit: float
    area_mm2: float
    area_above_threshold: float
    rays_hit: int


@dataclass
class SimulationOutput:
    run_id: str
    project_name: str
    source_file: Optional[str]
    summary: RunResultSummary
    receiver_metrics: List[ReceiverMetrics]
    mesh_info: Dict
    emitter_count: int
    gap_rule_count: int

    def to_dict(self) -> Dict:
        return asdict(self)


def fresh_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def random_unit_vector(rng: random.Random) -> Vec3:
    z = rng.uniform(-1.0, 1.0)
    a = rng.uniform(0.0, 2.0 * math.pi)
    r = math.sqrt(max(0.0, 1.0 - z * z))
    return (r * math.cos(a), r * math.sin(a), z)
