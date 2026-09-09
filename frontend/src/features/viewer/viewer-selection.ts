import type { ScenePayload } from '@/api'
import type { WorkspaceSnapshot } from '@/stores'

import { findCadSurfaceFaceIds } from './scene-geometry'

export function resolveCadFacePick(
  scene: ScenePayload,
  componentId: number,
  faceId: number | null,
  roiFaceIds: readonly number[] | null = null,
): number[] {
  if (
    faceId === null ||
    !Number.isSafeInteger(faceId) ||
    scene.mesh.face_component_ids[faceId] !== componentId
  ) return []

  const component = scene.components.find(
    (candidate) => candidate.component_id === componentId,
  )
  if (!component?.face_indices.includes(faceId)) return []
  const roiFaces = roiFaceIds === null ? null : new Set(roiFaceIds)
  if (roiFaces && !roiFaces.has(faceId)) return []
  return findCadSurfaceFaceIds(scene, component.face_indices, faceId)
    .filter((id) => !roiFaces || roiFaces.has(id))
}

export function updateCadFaceSelection(
  scene: ScenePayload,
  currentFaceIds: readonly number[],
  patchFaceIds: readonly number[],
  toggle: boolean,
): { faceIds: number[]; componentIds: number[] } {
  const current = new Set(currentFaceIds)
  const removePatch = patchFaceIds.every((faceId) => current.has(faceId))
  const selected = new Set(toggle ? currentFaceIds : [])
  if (toggle) {
    for (const faceId of patchFaceIds) {
      if (removePatch) selected.delete(faceId)
      else selected.add(faceId)
    }
  } else if (!(removePatch && current.size === patchFaceIds.length)) {
    for (const faceId of patchFaceIds) selected.add(faceId)
  }
  const faceIds = [...selected].filter((faceId) =>
    Number.isSafeInteger(faceId) &&
    Number.isSafeInteger(scene.mesh.face_component_ids[faceId]),
  ).sort((first, second) => first - second)
  const componentIds = [...new Set(faceIds.map(
    (faceId) => scene.mesh.face_component_ids[faceId] as number,
  ))].sort((first, second) => first - second)
  return { faceIds, componentIds }
}

export function resolveViewerHighlight(
  selection: Pick<WorkspaceSnapshot,
    'selectionKind' | 'selectedFaceIds' | 'selectedComponentIds'>,
  facePickArmed: boolean,
  transformComponentId?: number | null,
): { faceIds: number[]; componentIds: number[] } {
  if (selection.selectionKind === 'roi_cap') {
    return { faceIds: [], componentIds: [] }
  }
  if (facePickArmed || selection.selectionKind === 'faces') {
    return { faceIds: selection.selectedFaceIds, componentIds: [] }
  }
  const componentIds = new Set(
    selection.selectionKind === 'component' ? selection.selectedComponentIds : [],
  )
  if (transformComponentId !== null && transformComponentId !== undefined) {
    componentIds.add(transformComponentId)
  }
  return { faceIds: [], componentIds: [...componentIds] }
}

export function countSelectedCadFaces(scene: ScenePayload, faceIds: number[]): number {
  return new Set(faceIds.map((faceId) =>
    `${scene.mesh.face_component_ids[faceId]}:${scene.mesh.face_source_ids?.[faceId] ?? faceId}`,
  )).size
}
