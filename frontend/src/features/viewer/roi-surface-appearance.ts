import { Color, DoubleSide, MeshBasicMaterial, MeshStandardMaterial, type BufferGeometry } from 'three'
import type { ScenePayload } from '@/api'
import type { MaterialAssignment } from '@/stores'
import { findBaseMaterial, findSurfaceProperty } from '@/features/materials/material-catalog'
import { inheritedBaseMaterialId, partMaterialAssignment } from '@/features/materials/surface-assignment-model'
import { resolveComponentColor } from './viewer-display'

export function roiSurfaceAppearance(geometry: BufferGeometry, scene: ScenePayload, options: {
  assignments: MaterialAssignment[]
  colorOverrides: Record<number, string>
  faceColors?: ReadonlyMap<number, string>
  selectedFaceIds: number[]
  emitterFaceIds: number[]
  selectionColor: number
  selectionStrength: number
  wireframe: boolean
  opacity: number
}) {
  const faceIds = geometry.userData.sourceFaceIds as number[]
  const selected = new Set(options.selectedFaceIds)
  const emitterFaces = new Set(options.emitterFaceIds)
  const partAssignments = new Map(scene.components.map((component) => [component.component_id, partMaterialAssignment(options.assignments, component.component_id)]))
  const faceAssignments = new Map<number, MaterialAssignment>()
  for (const assignment of options.assignments) {
    if (!assignment.enabled || assignment.targetType !== 'faces') continue
    for (const id of assignment.faceIds) {
      if (scene.mesh.face_component_ids[id] === assignment.componentId) faceAssignments.set(id, assignment)
    }
  }
  const components = new Map(scene.components.map((component, index) => [component.component_id, { component, index }]))
  const materials: Array<MeshBasicMaterial | MeshStandardMaterial> = []
  const materialIndices = new Map<string, number>()
  geometry.clearGroups()
  let previousIndex = -1
  for (const [triangleIndex, faceId] of faceIds.entries()) {
    const componentId = scene.mesh.face_component_ids[faceId]!
    const assignment = faceAssignments.get(faceId) ?? partAssignments.get(componentId)
    const isSelected = selected.has(faceId)
    const isEmitter = emitterFaces.has(faceId)
    const faceColor = options.faceColors?.get(faceId)
    const key = `${componentId}:${assignment?.assignmentId ?? ''}:${faceColor ?? ''}:${isSelected}:${isEmitter}`
    let materialIndex = materialIndices.get(key)
    if (materialIndex === undefined) {
      const entry = components.get(componentId)
      const color = new Color(faceColor ?? (options.wireframe ? 0x263b4d : options.colorOverrides[componentId] ?? resolveComponentColor(entry?.component, entry?.index ?? 0)))
      if (isEmitter) color.lerp(new Color(0xfacc15), options.wireframe ? 0.3 : 0.52)
      if (isSelected) color.lerp(new Color(options.selectionColor), options.selectionStrength)
      const common = { color, side: DoubleSide, depthTest: true, depthWrite: options.wireframe || options.opacity >= 1, transparent: options.wireframe || options.opacity < 1,
        opacity: options.wireframe ? 0.75 : options.opacity, polygonOffset: false }
      const material = options.wireframe ? new MeshBasicMaterial({ ...common, toneMapped: false })
        : new MeshStandardMaterial({ ...common, flatShading: false,
          metalness: assignment && findBaseMaterial(inheritedBaseMaterialId(assignment, options.assignments)).category === 'Metal' ? 0.58 : 0.04,
          roughness: assignment ? findSurfaceProperty(assignment.surfaceId).roughness : 0.72,
          emissive: isSelected ? new Color(options.selectionColor) : isEmitter ? new Color(0x713f12) : new Color(0),
          emissiveIntensity: isSelected ? 0.16 : 0.12 })
      materialIndex = materials.length
      materialIndices.set(key, materialIndex)
      materials.push(material)
    }
    if (previousIndex === materialIndex) geometry.groups[geometry.groups.length - 1].count += 3
    else geometry.addGroup(triangleIndex * 3, 3, materialIndex)
    previousIndex = materialIndex
  }
  return materials
}
