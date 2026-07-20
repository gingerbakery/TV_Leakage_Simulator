from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .geometry import TriangleMesh
from .types import OpticalAssignment, OpticalProfile


UNASSIGNED_PROFILE_ID = "__unassigned_absorber__"


@dataclass(frozen=True)
class ResolvedOpticalProperty:
    profile: OpticalProfile
    source: str
    assignment_id: Optional[str] = None


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
