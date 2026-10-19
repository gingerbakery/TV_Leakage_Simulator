from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from itertools import islice
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

from .geometry import TriangleMesh
from .types import OpticalAssignment, OpticalProfile


UNASSIGNED_PROFILE_ID = "__unassigned_absorber__"
OPTICAL_ROUTING_CACHE_MIN_FACES = 256


@dataclass(frozen=True, slots=True, eq=False)
class _RoutingScalar:
    value: object

    def __eq__(self, other):
        return type(other) is type(self.value) and self.value == other


@dataclass(frozen=True, slots=True, eq=False)
class _OpticalRoutingCache:
    face_count: int
    signature: tuple
    default_material_id: str
    material_snapshot: dict
    metadata_ids: np.ndarray
    metadata_refs: tuple
    metadata_snapshots: tuple
    palette_keys: tuple
    slots: np.ndarray

    def matches(self, mesh, signature):
        if (
            self.face_count != len(mesh.faces)
            or self.signature != signature
            or type(mesh.default_material_id) is not str
            or self.default_material_id != mesh.default_material_id
            or len(self.metadata_ids) != min(self.face_count, len(mesh.face_metadata))
        ):
            return False
        identifiers = np.fromiter(map(id, mesh.face_metadata), dtype=np.uintp, count=len(self.metadata_ids))
        if not np.array_equal(identifiers, self.metadata_ids):
            return False
        try:
            return self.material_snapshot == mesh.face_material and all(
                snapshot == metadata for snapshot, metadata in zip(self.metadata_snapshots, self.metadata_refs)
            )
        except Exception:
            return False


def _build_optical_routing_cache(mesh, signature, palette_keys, slots):
    count = min(len(mesh.faces), len(mesh.face_metadata))
    unique_metadata = {id(metadata): metadata for metadata in islice(mesh.face_metadata, count)}
    snapshots = []
    scalar_types = (str, int, float, bool, type(None))
    for metadata in unique_metadata.values():
        if type(metadata) is not dict:
            return None
        snapshot = metadata.copy()
        for key in ('component_id', 'source_face_index'):
            if key in metadata:
                value = metadata[key]
                if type(value) not in scalar_types:
                    return None
                snapshot[key] = _RoutingScalar(value)
        snapshots.append(snapshot)
    if any(type(value) is not str for value in mesh.face_material.values()):
        return None
    identifiers = np.fromiter(map(id, mesh.face_metadata), dtype=np.uintp, count=count)
    identifiers.setflags(write=False)
    owned_slots = slots.copy()
    owned_slots.setflags(write=False)
    return _OpticalRoutingCache(
        len(mesh.faces), signature, mesh.default_material_id,
        {key: _RoutingScalar(value) for key, value in mesh.face_material.items()},
        identifiers, tuple(unique_metadata.values()), tuple(snapshots), palette_keys, owned_slots,
    )


@dataclass(frozen=True)
class ResolvedOpticalProperty:
    profile: OpticalProfile
    source: str
    assignment_id: Optional[str] = None


@dataclass(frozen=True, slots=True, eq=False)
class ResolvedOpticalFaces(Sequence[ResolvedOpticalProperty]):
    properties: tuple[ResolvedOpticalProperty, ...]
    palette: tuple[ResolvedOpticalProperty, ...]
    palette_slots: np.ndarray

    def __len__(self):
        return len(self.properties)

    def __getitem__(self, index):
        return self.properties[index]

    def __iter__(self):
        return iter(self.properties)

    def numeric_tables(self, scatter_encoder):
        profiles = tuple(resolved.profile for resolved in self.palette)
        columns = (
            np.asarray([profile.reflectance for profile in profiles], dtype=np.float64),
            np.asarray([profile.roughness for profile in profiles], dtype=np.float64),
            scatter_encoder(profile.scatter_model for profile in profiles),
            np.asarray([profile.specular_ratio for profile in profiles], dtype=np.float64),
            np.asarray([profile.gaussian_sigma_deg for profile in profiles], dtype=np.float64),
        )
        tables = tuple(column[self.palette_slots] for column in columns)
        for table in tables:
            table.setflags(write=False)
        return tables


class OpticalPropertyResolver:
    def __init__(
        self,
        mesh: TriangleMesh,
        profiles: List[OpticalProfile],
        assignments: List[OpticalAssignment],
    ) -> None:
        self.mesh = mesh
        self.profiles: Dict[str, OpticalProfile] = {
            profile.profile_id: profile for profile in profiles
        }
        self.face_assignments: Dict[Tuple[int, int], OpticalAssignment] = {}
        self.part_assignments: Dict[int, OpticalAssignment] = {}
        for assignment in assignments:
            if not assignment.enabled:
                continue
            if assignment.target_type == "faces":
                for face_index in assignment.face_indices:
                    self._store_assignment(
                        self.face_assignments,
                        (assignment.component_id, face_index),
                        assignment,
                    )
            else:
                self._store_assignment(
                    self.part_assignments,
                    assignment.component_id,
                    assignment,
                )
        self.unassigned_profile = OpticalProfile(
            profile_id=UNASSIGNED_PROFILE_ID,
            reflectance=0.0,
            specular_ratio=0.0,
            diffuse_ratio=0.0,
            scatter_model="none",
            roughness=1.0,
            gaussian_sigma_deg=18.0,
            notes="Safe fallback for a surface without a resolved optical profile",
        )

    @staticmethod
    def _store_assignment(mapping, key, candidate: OpticalAssignment) -> None:
        current = mapping.get(key)
        if current is None or candidate.priority >= current.priority:
            mapping[key] = candidate

    def resolve(self, face_index: int) -> ResolvedOpticalProperty:
        metadata = self.mesh.metadata(face_index)
        component_id = metadata.get("component_id")
        source_face_index = int(metadata.get("source_face_index", face_index))
        if component_id is not None:
            component_id = int(component_id)
            face_assignment = self.face_assignments.get((component_id, source_face_index))
            resolved = self._resolve_assignment(face_assignment, "face_override")
            if resolved is not None:
                return resolved
            part_assignment = self.part_assignments.get(component_id)
            resolved = self._resolve_assignment(part_assignment, "part_assignment")
            if resolved is not None:
                return resolved

        material_profile = self.profiles.get(self.mesh.material_id(face_index))
        if material_profile is not None:
            return ResolvedOpticalProperty(material_profile, "mesh_material")
        default_profile = self.profiles.get("default")
        if default_profile is not None:
            return ResolvedOpticalProperty(default_profile, "default")
        return ResolvedOpticalProperty(self.unassigned_profile, "unassigned")

    def _resolve_assignment(
        self,
        assignment: Optional[OpticalAssignment],
        source: str,
    ) -> Optional[ResolvedOpticalProperty]:
        if assignment is None:
            return None
        profile = self.profiles.get(assignment.profile_id)
        if profile is None:
            return None
        return ResolvedOpticalProperty(
            profile=profile,
            source=source,
            assignment_id=assignment.assignment_id,
        )

    def resolve_all(self) -> ResolvedOpticalFaces:
        assignment_cache = {}

        def resolve_assignment(assignment, source):
            key = (id(assignment), source)
            if key not in assignment_cache:
                assignment_cache[key] = self._resolve_assignment(assignment, source)
            return assignment_cache[key]

        face_assignments = {
            key: resolve_assignment(assignment, "face_override")
            for key, assignment in self.face_assignments.items()
        }
        part_assignments = {
            key: resolve_assignment(assignment, "part_assignment")
            for key, assignment in self.part_assignments.items()
        }
        materials = {
            key: ResolvedOpticalProperty(profile, "mesh_material")
            for key, profile in self.profiles.items()
        }
        default = self.profiles.get("default")
        fallback = (
            ResolvedOpticalProperty(default, "default") if default is not None
            else ResolvedOpticalProperty(self.unassigned_profile, "unassigned")
        )
        cache_mode = os.environ.get('LEAKAGE_OPTICAL_ROUTING_CACHE', 'auto')
        cache_enabled = (
            cache_mode != 'off'
            and (len(self.mesh.faces) >= OPTICAL_ROUTING_CACHE_MIN_FACES or cache_mode == 'force')
            and type(self.mesh) is TriangleMesh
            and type(self.mesh.face_metadata) is list
            and type(self.mesh.face_material) is dict
            and type(self.mesh.default_material_id) is str
            and 'metadata' not in self.mesh.__dict__
            and 'material_id' not in self.mesh.__dict__
            and all(type(key) is str for key in self.profiles)
        )
        if cache_enabled:
            routes = {('fallback',): fallback}
            routes.update({('material', key): value for key, value in materials.items()})
            mapping_signatures = []
            for source, assignments, resolved_mapping in (
                ('face_override', self.face_assignments, face_assignments),
                ('part_assignment', self.part_assignments, part_assignments),
            ):
                mapping_signature = []
                for key, assignment in assignments.items():
                    resolved = resolved_mapping[key]
                    token = (source, id(assignment)) if resolved is not None else None
                    mapping_signature.append((key, token))
                    if token is not None:
                        routes[token] = resolved
                mapping_signatures.append(tuple(mapping_signature))
            signature = (tuple(self.profiles), *mapping_signatures)
            cached = getattr(self.mesh, '_optical_routing_cache', None)
            if cached is not None and cached.matches(self.mesh, signature):
                palette = tuple(routes[key] for key in cached.palette_keys)
                slots = cached.slots.copy()
                properties = tuple(np.take(np.asarray(palette, dtype=object), slots).tolist())
                slots.setflags(write=False)
                return ResolvedOpticalFaces(properties, palette, slots)
        properties = []
        palette = []
        palette_by_identity = {}
        slots = np.empty(len(self.mesh.faces), dtype=np.int32)
        for face_index in range(len(self.mesh.faces)):
            metadata = self.mesh.metadata(face_index)
            component_id = metadata.get("component_id")
            source_face_index = int(metadata.get("source_face_index", face_index))
            resolved = None
            if component_id is not None:
                component_id = int(component_id)
                resolved = face_assignments.get((component_id, source_face_index))
                if resolved is None:
                    resolved = part_assignments.get(component_id)
            if resolved is None:
                resolved = materials.get(self.mesh.material_id(face_index), fallback)
            identity = id(resolved)
            slot = palette_by_identity.get(identity)
            if slot is None:
                slot = len(palette)
                palette_by_identity[identity] = slot
                palette.append(resolved)
            slots[face_index] = slot
            properties.append(resolved)
        slots.setflags(write=False)
        if cache_enabled:
            route_by_identity = {id(value): key for key, value in routes.items()}
            self.mesh._optical_routing_cache = _build_optical_routing_cache(
                self.mesh, signature, tuple(route_by_identity[id(value)] for value in palette), slots,
            )
        return ResolvedOpticalFaces(tuple(properties), tuple(palette), slots)
