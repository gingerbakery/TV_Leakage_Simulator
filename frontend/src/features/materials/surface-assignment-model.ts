import type { SceneComponent, ScenePayload } from '@/api'
import type { MaterialAssignment } from '@/stores'
import { findCadSurfaceFaceIds } from '@/features/viewer/scene-geometry'

export interface CadSurfaceGroup {
  key: string
  label: string
  faceIds: number[]
}

export function componentCadSurfaces(scene: ScenePayload, component: SceneComponent): CadSurfaceGroup[] {
  const sourceIds = scene.mesh.face_source_ids
  const groups = new Map<string, number[]>()
  const visited = new Set<number>()
  for (const faceId of component.face_indices) {
    if (visited.has(faceId)) continue
    const sourceId = sourceIds?.[faceId]
    if (sourceId !== undefined && sourceId !== null) {
      const key = String(sourceId)
      const faces = groups.get(key) ?? []
      faces.push(faceId)
      groups.set(key, faces)
    } else {
      const faces = findCadSurfaceFaceIds(scene, component.face_indices, faceId)
      faces.forEach((id) => visited.add(id))
      groups.set(`mesh-${faceId}`, faces)
    }
  }
  return [...groups].map(([key, faceIds], index) => ({ key, label: `Face ${index + 1}`, faceIds }))
}

export function partMaterialAssignment(assignments: MaterialAssignment[], componentId: number) {
  return assignments.findLast((item) => item.enabled && item.componentId === componentId && item.targetType === 'part')
}

export function inheritedBaseMaterialId(assignment: MaterialAssignment, assignments: MaterialAssignment[]) {
  return assignment.targetType === 'faces'
    ? partMaterialAssignment(assignments, assignment.componentId)?.baseMaterialId ?? assignment.baseMaterialId
    : assignment.baseMaterialId
}
