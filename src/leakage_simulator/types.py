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
EMITTER_SURFACE_CONSTRUCTIONS = ("rectangular_fit", "polygon_auto")
REFERENCE_PLANARITY_TOLERANCE_MM = 0.05
RECEIVER_TYPES = ("rectangle",)
RECEIVER_PLACEMENT_MODES = ("datum_plane", "reference_plane", "current_view")
SCATTER_MODELS = ("none", "specular", "lambertian", "gaussian", "mixed")
OPTICAL_ASSIGNMENT_TARGET_TYPES = ("part", "faces")
TERMINATION_MODES = ("threshold", "russian_roulette")
CONTRIBUTION_MODES = ("summary", "detailed")
INTERSECTION_BACKENDS = ("auto", "brute_force", "bvh")


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
    surface_construction: str = "rectangular_fit"
    polygon_vertices: List[Vec3] = field(default_factory=list)
    reference_vertex_indices: List[int] = field(default_factory=list)
    reference_edge_vertex_indices: List[Tuple[int, int]] = field(default_factory=list)
    reference_vertex_points: List[Vec3] = field(default_factory=list)
    reference_edge_points: List[Tuple[Vec3, Vec3]] = field(default_factory=list)
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
        self.surface_construction = require_choice(
            self.surface_construction,
            "surface_construction",
            EMITTER_SURFACE_CONSTRUCTIONS,
        )
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
        self.polygon_vertices = [
            vec3_from(vertex, "polygon_vertices") for vertex in self.polygon_vertices
        ]
        if self.surface_construction == "polygon_auto":
            if self.emitter_type != "reference_plane":
                raise ValueError("polygon_auto is only valid for reference_plane emitters")
            if len(self.polygon_vertices) < 3:
                raise ValueError("polygon_auto requires at least three polygon_vertices")
            if self.virtual_area_mm2() <= 1e-9:
                raise ValueError("polygon_auto requires a non-degenerate polygon")
            normal = normalize_vec3(
                (
                    self.u_axis[1] * self.v_axis[2] - self.u_axis[2] * self.v_axis[1],
                    self.u_axis[2] * self.v_axis[0] - self.u_axis[0] * self.v_axis[2],
                    self.u_axis[0] * self.v_axis[1] - self.u_axis[1] * self.v_axis[0],
                ),
                "polygon_normal",
            )
            planarity_error_mm = max(
                abs(sum((vertex[axis] - self.center[axis]) * normal[axis] for axis in range(3)))
                for vertex in self.polygon_vertices
            )
            if planarity_error_mm > REFERENCE_PLANARITY_TOLERANCE_MM:
                raise ValueError(
                    "polygon_vertices exceed the 0.05 mm planarity tolerance"
                )
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
        self.reference_vertex_points = [
            vec3_from(point, "reference_vertex_points") for point in self.reference_vertex_points
        ]
        self.reference_edge_points = [
            (
                vec3_from(edge[0], "reference_edge_points"),
                vec3_from(edge[1], "reference_edge_points"),
            )
            for edge in self.reference_edge_points
        ]
        self.ray_count = require_positive_int(self.ray_count, "ray_count")

    def effective_power_lumen(self, area_mm2: float) -> float:
        if self.power_mode == "power_per_area":
            return self.power_density_lm_per_m2 * max(0.0, float(area_mm2)) * 1e-6
        return self.power_lumen

    def virtual_area_mm2(self) -> float:
        if self.surface_construction != "polygon_auto" or len(self.polygon_vertices) < 3:
            return max(0.0, float(self.width_mm or 0.0)) * max(0.0, float(self.height_mm or 0.0))
        origin = self.polygon_vertices[0]
        area = 0.0
        for index in range(1, len(self.polygon_vertices) - 1):
            first = tuple(self.polygon_vertices[index][axis] - origin[axis] for axis in range(3))
            second = tuple(self.polygon_vertices[index + 1][axis] - origin[axis] for axis in range(3))
            cross = (
                first[1] * second[2] - first[2] * second[1],
                first[2] * second[0] - first[0] * second[2],
                first[0] * second[1] - first[1] * second[0],
            )
            area += 0.5 * math.sqrt(sum(value * value for value in cross))
        return area

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict) -> "EmitterSpec":
        return cls(**payload)


@dataclass
class ReceiverSpec:
    receiver_id: str
    receiver_type: str = "rectangle"
    display_name: str = "Receiver"
    placement_mode: str = "datum_plane"
    center: Vec3 = (0.0, 0.0, 0.0)
    normal: Vec3 = (0.0, 0.0, 1.0)
    u_axis: Optional[Vec3] = None
    v_axis: Optional[Vec3] = None
    width_mm: float = 100.0
    height_mm: float = 30.0
    resolution: Tuple[int, int] = (80, 24)
    acceptance_angle_deg: float = 90.0
    normal_flip: bool = False
    reference_mode: Optional[str] = None
    reference_vertex_indices: List[int] = field(default_factory=list)
    reference_edge_vertex_indices: List[Tuple[int, int]] = field(default_factory=list)
    reference_vertex_points: List[Vec3] = field(default_factory=list)
    reference_edge_points: List[Tuple[Vec3, Vec3]] = field(default_factory=list)
    view_distance_mm: Optional[float] = None
    base_center: Optional[Vec3] = None
    base_u_axis: Optional[Vec3] = None
    base_v_axis: Optional[Vec3] = None
    base_normal: Optional[Vec3] = None
    position_offset_mm: Vec3 = (0.0, 0.0, 0.0)
    tilt_xyz_deg: Vec3 = (0.0, 0.0, 0.0)
    enabled: bool = True

    def __post_init__(self) -> None:
        self.receiver_type = require_choice(self.receiver_type, "receiver_type", RECEIVER_TYPES)
        self.placement_mode = require_choice(
            self.placement_mode,
            "placement_mode",
            RECEIVER_PLACEMENT_MODES,
        )
        self.display_name = str(self.display_name or self.receiver_id)
        self.center = vec3_from(self.center, "center")
        self.normal = normalize_vec3(self.normal, "normal")
        if (self.u_axis is None) != (self.v_axis is None):
            raise ValueError("u_axis and v_axis must be provided together")
        if self.u_axis is not None and self.v_axis is not None:
            self.u_axis = normalize_vec3(self.u_axis, "u_axis")
            raw_v = vec3_from(self.v_axis, "v_axis")
            projection = sum(self.u_axis[index] * raw_v[index] for index in range(3))
            orthogonal_v = tuple(
                raw_v[index] - self.u_axis[index] * projection for index in range(3)
            )
            self.v_axis = normalize_vec3(orthogonal_v, "v_axis")
            plane_normal = (
                self.u_axis[1] * self.v_axis[2] - self.u_axis[2] * self.v_axis[1],
                self.u_axis[2] * self.v_axis[0] - self.u_axis[0] * self.v_axis[2],
                self.u_axis[0] * self.v_axis[1] - self.u_axis[1] * self.v_axis[0],
            )
            if sum(plane_normal[index] * self.normal[index] for index in range(3)) < 0.0:
                self.v_axis = tuple(-value for value in self.v_axis)
                plane_normal = tuple(-value for value in plane_normal)
            self.normal = normalize_vec3(plane_normal, "normal")
        self.width_mm = require_positive(self.width_mm, "width_mm")
        self.height_mm = require_positive(self.height_mm, "height_mm")
        self.resolution = int_pair_from(self.resolution, "resolution")
        if self.resolution[0] <= 0 or self.resolution[1] <= 0:
            raise ValueError("resolution values must be positive")
        self.acceptance_angle_deg = float(self.acceptance_angle_deg)
        if self.acceptance_angle_deg <= 0.0 or self.acceptance_angle_deg > 180.0:
            raise ValueError("acceptance_angle_deg must be within (0, 180]")
        self.reference_vertex_indices = [int(index) for index in self.reference_vertex_indices]
        self.reference_edge_vertex_indices = [
            (int(edge[0]), int(edge[1])) for edge in self.reference_edge_vertex_indices
        ]
        self.reference_vertex_points = [
            vec3_from(point, "reference_vertex_points") for point in self.reference_vertex_points
        ]
        self.reference_edge_points = [
            (
                vec3_from(edge[0], "reference_edge_points"),
                vec3_from(edge[1], "reference_edge_points"),
            )
            for edge in self.reference_edge_points
        ]
        if self.view_distance_mm is not None:
            self.view_distance_mm = require_positive(self.view_distance_mm, "view_distance_mm")
        if self.base_center is not None:
            self.base_center = vec3_from(self.base_center, "base_center")
        if self.base_u_axis is not None:
            self.base_u_axis = normalize_vec3(self.base_u_axis, "base_u_axis")
        if self.base_v_axis is not None:
            self.base_v_axis = normalize_vec3(self.base_v_axis, "base_v_axis")
        if self.base_normal is not None:
            self.base_normal = normalize_vec3(self.base_normal, "base_normal")
        self.position_offset_mm = vec3_from(self.position_offset_mm, "position_offset_mm")
        self.tilt_xyz_deg = vec3_from(self.tilt_xyz_deg, "tilt_xyz_deg")

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
        self.absorption = 1.0 - self.reflectance
        self.specular_ratio = max(0.0, float(self.specular_ratio))
        self.diffuse_ratio = max(0.0, float(self.diffuse_ratio))
        ratio_sum = self.specular_ratio + self.diffuse_ratio
        if ratio_sum > 0.0:
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
class OpticalAssignment:
    assignment_id: str
    target_type: str
    component_id: int
    profile_id: str
    face_indices: List[int] = field(default_factory=list)
    priority: int = 0
    enabled: bool = True

    def __post_init__(self) -> None:
        self.target_type = require_choice(
            str(self.target_type),
            "target_type",
            OPTICAL_ASSIGNMENT_TARGET_TYPES,
        )
        self.component_id = int(self.component_id)
        self.profile_id = str(self.profile_id).strip()
        if not self.profile_id:
            raise ValueError("profile_id must not be empty")
        self.face_indices = sorted({int(value) for value in self.face_indices})
        self.priority = int(self.priority)

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict) -> "OpticalAssignment":
        normalized = dict(payload)
        if "component_id" not in normalized and "object_id" in normalized:
            normalized["component_id"] = normalized.pop("object_id")
        target_type = normalized.get("target_type")
        if target_type == "component":
            normalized["target_type"] = "part"
        elif target_type == "face_override":
            normalized["target_type"] = "faces"
        return cls(**normalized)


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
    contribution_mode: str = "summary"
    intersection_backend: str = "auto"
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
        self.contribution_mode = require_choice(
            self.contribution_mode,
            "contribution_mode",
            CONTRIBUTION_MODES,
        )
        self.intersection_backend = require_choice(
            self.intersection_backend,
            "intersection_backend",
            INTERSECTION_BACKENDS,
        )
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
    optical_profile_id: Optional[str] = None
    reflectance: Optional[float] = None
    scatter_model: Optional[str] = None
    optical_assignment_source: Optional[str] = None
    ray_kind: Optional[str] = None

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
        if self.reflectance is not None:
            self.reflectance = clamp(float(self.reflectance), 0.0, 1.0)
        if self.scatter_model is not None:
            self.scatter_model = require_choice(self.scatter_model, "scatter_model", SCATTER_MODELS)

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
class RayTraceContributionSummary:
    schema_version: str = "rt-contribution.v1"
    direct_receiver_hit_count: int = 0
    direct_receiver_flux_lumen: float = 0.0
    reflected_receiver_hit_count: int = 0
    reflected_receiver_flux_lumen: float = 0.0
    receivers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    components: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    faces: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    materials: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    lobes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    depths: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.direct_receiver_hit_count = int(self.direct_receiver_hit_count)
        self.reflected_receiver_hit_count = int(self.reflected_receiver_hit_count)
        if self.direct_receiver_hit_count < 0 or self.reflected_receiver_hit_count < 0:
            raise ValueError("receiver contribution hit counts must be non-negative")
        self.direct_receiver_flux_lumen = require_non_negative(
            self.direct_receiver_flux_lumen,
            "direct_receiver_flux_lumen",
        )
        self.reflected_receiver_flux_lumen = require_non_negative(
            self.reflected_receiver_flux_lumen,
            "reflected_receiver_flux_lumen",
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
    contribution_summary: RayTraceContributionSummary
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


@dataclass
class ROIComponentClip:
    """One component's share of an XY-box ROI drag - only the faces that
    actually overlap the drag box (see roi.py:resolve_faces_in_xy_box),
    not the whole component. area_mm2/bbox are computed from just those
    faces, so a component that only clips the corner of the box gets a
    correspondingly small area/bbox, not its full-part values."""

    component_id: int
    component_name: str
    face_indices: List[int]
    area_mm2: float
    bbox_min: Vec3
    bbox_max: Vec3


@dataclass
class ROIRegionResult:
    """Result of one box-drag ROI pick. `view` records which fixed
    orthographic view the drag happened in - box-drag ROI is only valid in
    a front/back orthographic view (see docs/roi-native-selection-plan.md),
    since screen XY must equal model XY for the Z-unbounded-prism test to
    make sense."""

    scope_id: str
    drag_rect_xy: Tuple[float, float, float, float]  # (x_min, x_max, y_min, y_max), model coords
    view: str  # "front_xy" | "back_neg_xy"
    components: List[ROIComponentClip] = field(default_factory=list)

    @property
    def face_indices(self) -> List[int]:
        """Flattened face_indices across every clipped component - the
        roi_face_indices shape engine.py/roi.py already expect."""
        result: List[int] = []
        for component in self.components:
            result.extend(component.face_indices)
        return result


@dataclass
class ROIPointSelection:
    """Fallback ROI input path (see docs/roi-native-selection-plan.md) for
    when box-drag selection itself is unreliable - a directly-specified
    coordinate resolved to its nearest face."""

    coordinate: Vec3
    face_index: Optional[int]
    component_id: Optional[int] = None
    note: str = ""


def fresh_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def random_unit_vector(rng: random.Random) -> Vec3:
    z = rng.uniform(-1.0, 1.0)
    a = rng.uniform(0.0, 2.0 * math.pi)
    r = math.sqrt(max(0.0, 1.0 - z * z))
    return (r * math.cos(a), r * math.sin(a), z)
