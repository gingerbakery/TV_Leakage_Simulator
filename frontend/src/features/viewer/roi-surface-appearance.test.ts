import { describe, expect, it } from 'vitest'
import { createSceneFixture } from '@/test/scene-fixture'
import { buildRoiClippedGeometries } from '@/features/roi/roi-clipped-geometry'
import { roiSurfaceAppearance } from './roi-surface-appearance'

describe('single-pass ROI surface appearance', () => {
  it.each([false, true])('does not offset or retessellate selected/material/emitter faces (wireframe %s)', (wireframe) => {
    const scene = createSceneFixture()
    const clipped = buildRoiClippedGeometries(scene, [0, 1, 2],
      [{ xMin: 10, xMax: 40, yMin: 10, yMax: 40 }], [], undefined,
      { includeCaps: false, includeFeatureEdges: false })!
    const geometry = clipped.surfaceGeometry
    const original = [...geometry.getAttribute('position').array]
    const normals = [...geometry.getAttribute('normal').array]
    const sourceIds = [...geometry.userData.sourceFaceIds]
    const materials = roiSurfaceAppearance(geometry, scene, {
      assignments: [{ assignmentId: 'face1', componentId: 1, targetType: 'faces', faceIds: [0],
        baseMaterialId: 'pc_black', surfaceId: 'semi_gloss_black_resin', profileId: '', bsdfAssetId: '', enabled: true }],
      colorOverrides: {}, selectedFaceIds: [0], emitterFaceIds: [1],
      selectionColor: 0xff8a00, selectionStrength: 0.6, wireframe, opacity: 1,
    })
    expect([...geometry.getAttribute('position').array]).toEqual(original)
    expect([...geometry.getAttribute('normal').array]).toEqual(normals)
    expect(geometry.userData.sourceFaceIds).toEqual(sourceIds)
    expect(geometry.groups.reduce((sum, group) => sum + group.count, 0)).toBe(original.length / 3)
    let cursor = 0
    for (const group of geometry.groups) {
      expect(group.start).toBe(cursor)
      cursor += group.count
    }
    for (const material of materials) {
      expect(material.polygonOffset).toBe(false)
      expect(material.depthTest).toBe(true)
      expect(material.depthWrite).toBe(true)
      if ('flatShading' in material) expect(material.flatShading).toBe(false)
      material.dispose()
    }
    geometry.dispose()
  })
})
