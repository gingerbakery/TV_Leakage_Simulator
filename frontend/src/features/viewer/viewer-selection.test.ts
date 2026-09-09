import { describe, expect, it } from 'vitest'

import { buildRoiClippedGeometries } from '@/features/roi/roi-clipped-geometry'
import { createWorkspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'

import {
  countSelectedCadFaces,
  resolveCadFacePick,
  resolveViewerHighlight,
  updateCadFaceSelection,
} from './viewer-selection'

function cadScene() {
  const scene = createSceneFixture()
  scene.mesh.face_source_ids = [10, 10, 11, 10, 10]
  return scene
}

describe('CAD face selection and component highlight contract', () => {
  it('selects all triangles in a curved CAD face, not its adjacent face or another part', () => {
    const scene = cadScene()
    expect(resolveCadFacePick(scene, 1, 0)).toEqual([0, 1])
    expect(resolveCadFacePick(scene, 1, 1)).toEqual([0, 1])
    expect(resolveCadFacePick(scene, 1, 2)).toEqual([2])
    expect(resolveCadFacePick(scene, 2, 3)).toEqual([3, 4])
    expect(countSelectedCadFaces(scene, [0, 1, 2, 3, 4])).toBe(3)
  })

  it('restricts the picked CAD face to active ROI triangle IDs', () => {
    const scene = cadScene()
    expect(resolveCadFacePick(scene, 1, 0, [0, 2, 3])).toEqual([0])
    expect(resolveCadFacePick(scene, 1, 1, [0, 2, 3])).toEqual([])
    expect(resolveCadFacePick(scene, 1, 0, [])).toEqual([])
  })

  it('does not turn a section cap, invalid hit, or wrong owner into a component-wide selection', () => {
    const scene = cadScene()
    expect(resolveCadFacePick(scene, 1, null)).toEqual([])
    expect(resolveCadFacePick(scene, 1, 999)).toEqual([])
    expect(resolveCadFacePick(scene, 2, 0)).toEqual([])
    expect(resolveCadFacePick(scene, 1, -1)).toEqual([])
    const store = createWorkspaceStore()
    store.getState().actions.setFaceSelection([0, 1], [1])
    store.getState().actions.setRoiCapSelection()
    expect(store.getState().selectedComponentIds).toEqual([])
    expect(store.getState().selectedFaceIds).toEqual([])
    expect(resolveViewerHighlight(store.getState(), false, 1))
      .toEqual({ faceIds: [], componentIds: [] })
  })

  it('falls back to the connected planar patch for legacy geometry without CAD IDs', () => {
    const scene = createSceneFixture()
    scene.mesh.vertices[3][2] = 0
    scene.mesh.face_centroids[1][2] = 0
    expect(resolveCadFacePick(scene, 1, 0)).toEqual([0, 1])
  })

  it('replaces a single selection and toggles the same CAD face off', () => {
    const scene = cadScene()
    const first = updateCadFaceSelection(scene, [], [0, 1], false)
    expect(first).toEqual({ faceIds: [0, 1], componentIds: [1] })
    expect(updateCadFaceSelection(scene, first.faceIds, [2], false))
      .toEqual({ faceIds: [2], componentIds: [1] })
    expect(updateCadFaceSelection(scene, first.faceIds, [0, 1], false))
      .toEqual({ faceIds: [], componentIds: [] })
  })

  it('adds and removes whole face patches without toggling away their parent part', () => {
    const scene = cadScene()
    const second = updateCadFaceSelection(scene, [0, 1], [2], true)
    expect(second).toEqual({ faceIds: [0, 1, 2], componentIds: [1] })
    const anotherPart = updateCadFaceSelection(scene, second.faceIds, [3, 4], true)
    expect(anotherPart.componentIds).toEqual([1, 2])
    const removed = updateCadFaceSelection(scene, anotherPart.faceIds, [0, 1], true)
    expect(removed).toEqual({ faceIds: [2, 3, 4], componentIds: [1, 2] })
  })

  it('never expands a face highlight through the selected parent component', () => {
    const store = createWorkspaceStore()
    store.getState().actions.setFaceSelection([0, 1], [1])
    expect(resolveViewerHighlight(store.getState(), false))
      .toEqual({ faceIds: [0, 1], componentIds: [] })
    expect(resolveViewerHighlight(store.getState(), false, 1))
      .toEqual({ faceIds: [0, 1], componentIds: [] })
  })

  it('switches from face context to whole part on the first tree click and clears on the second', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.setFaceSelection([0, 1], [1])
    actions.toggleSelectedComponentId(1)
    expect(resolveViewerHighlight(store.getState(), false))
      .toEqual({ faceIds: [], componentIds: [1] })
    expect(store.getState().selectedFaceIds).toEqual([])
    actions.toggleSelectedComponentId(1)
    expect(resolveViewerHighlight(store.getState(), false))
      .toEqual({ faceIds: [], componentIds: [] })
  })

  it('preserves whole-component Transform highlighting, but suppresses it while picking faces', () => {
    const store = createWorkspaceStore()
    expect(resolveViewerHighlight(store.getState(), false, 1))
      .toEqual({ faceIds: [], componentIds: [1] })
    store.getState().actions.setSelectedComponentIds([1, 2])
    expect(resolveViewerHighlight(store.getState(), false))
      .toEqual({ faceIds: [], componentIds: [1, 2] })
    expect(resolveViewerHighlight(store.getState(), true, 1))
      .toEqual({ faceIds: [], componentIds: [] })
  })

  it('clips only the selected source face and keeps its picking IDs after ROI and translation', () => {
    const scene = cadScene()
    scene.mesh.vertices[3][2] = 0
    const patch = resolveCadFacePick(scene, 1, 0, [0, 1, 2, 3, 4])
    const box = { xMin: 10, xMax: 40, yMin: 10, yMax: 40, zMin: -1, zMax: 1 }
    const bundle = buildRoiClippedGeometries(
      scene, patch, [box], [],
      (_componentId, point) => [point[0] + 3, point[1], point[2]],
      { includeCaps: false, includeFeatureEdges: false },
    )
    expect(bundle).not.toBeNull()
    expect(new Set(bundle!.surfaceGeometry.userData.sourceFaceIds)).toEqual(new Set([0, 1]))
    expect(new Set(bundle!.surfaceGeometry.userData.componentIds)).toEqual(new Set([1]))
    expect(bundle!.capGeometry).toBeNull()
    const position = bundle!.surfaceGeometry.getAttribute('position')
    for (let index = 0; index < position.count; index += 1) {
      expect(position.getX(index)).toBeGreaterThanOrEqual(13)
      expect(position.getX(index)).toBeLessThanOrEqual(43)
      expect(position.getY(index)).toBeGreaterThanOrEqual(10)
      expect(position.getY(index)).toBeLessThanOrEqual(40)
    }
    bundle!.surfaceGeometry.dispose()
  })
})
