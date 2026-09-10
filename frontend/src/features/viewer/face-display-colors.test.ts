import { describe, expect, it } from 'vitest'
import { Color } from 'three'
import { createSceneFixture } from '@/test/scene-fixture'
import { createComponentGeometry } from './scene-geometry'
import { applyFaceDisplayColors, resolveFaceDisplayColors } from './face-display-colors'
import { roiSurfaceAppearance } from './roi-surface-appearance'
import { buildRoiClippedGeometries } from '@/features/roi/roi-clipped-geometry'

describe('CAD face display colors', () => {
  it('colors an indexed CAD face without changing triangles, normals or source IDs', () => {
    const scene = createSceneFixture()
    scene.mesh.face_source_ids = [10, 10, 11, 20, 20]
    const geometry = createComponentGeometry(scene, scene.components[0]).geometry
    const positions = [...geometry.getAttribute('position').array]
    const indices = [...geometry.getIndex()!.array]
    const normals = [...geometry.getAttribute('normal').array]
    const colors = resolveFaceDisplayColors(scene, [
      { componentId: 1, faceIds: [0, 1], color: '#ef4444' },
      { componentId: 2, faceIds: [2], color: '#ffffff' },
    ])
    expect(colors.has(2)).toBe(false)
    expect(applyFaceDisplayColors(geometry, colors, new Color('#2563eb'))).toBe(true)
    const attribute = geometry.getAttribute('color')
    const expectFaceColor = (faceIndex: number, hex: string) => {
      const expected = new Color(hex)
      for (let corner = 0; corner < 3; corner += 1) {
        const vertex = geometry.getIndex()!.getX(faceIndex * 3 + corner)
        expect(attribute.getX(vertex)).toBeCloseTo(expected.r)
        expect(attribute.getY(vertex)).toBeCloseTo(expected.g)
        expect(attribute.getZ(vertex)).toBeCloseTo(expected.b)
      }
    }
    expectFaceColor(0, '#ef4444')
    expectFaceColor(1, '#ef4444')
    expectFaceColor(2, '#2563eb')
    applyFaceDisplayColors(geometry, colors, new Color('#2563eb'))
    expect(geometry.getAttribute('color')).toBe(attribute)
    expect([...geometry.getAttribute('position').array]).toEqual(positions)
    expect([...geometry.getAttribute('normal').array]).toEqual(normals)
    expect([...geometry.getIndex()!.array]).toEqual(indices)
    expect(geometry.userData.sourceFaceIds).toEqual([0, 1, 2])
    expect(applyFaceDisplayColors(geometry, new Map(), new Color('#22c55e'))).toBe(false)
    expect(geometry.getAttribute('color')).toBe(attribute)
    applyFaceDisplayColors(geometry, new Map([[0, '#14b8a6'], [1, '#14b8a6']]), new Color('#22c55e'))
    expect(geometry.getAttribute('color')).toBe(attribute)
    geometry.dispose()
  })

  it.each([false, true])('preserves face colors and single-pass ROI skin (wireframe %s)', (wireframe) => {
    const scene = createSceneFixture()
    const geometry = buildRoiClippedGeometries(scene, [0, 1, 2],
      [{ xMin: 10, xMax: 40, yMin: 10, yMax: 40 }], [], undefined,
      { includeCaps: false, includeFeatureEdges: false })!.surfaceGeometry
    const options = { assignments: [], colorOverrides: { 1: '#2563eb' },
      faceColors: new Map([[0, '#ef4444']]), selectedFaceIds: [], emitterFaceIds: [],
      selectionColor: 0xff8a00, selectionStrength: 0.6, wireframe, opacity: 1 }
    const materials = roiSurfaceAppearance(geometry, scene, options)
    const sourceIds = geometry.userData.sourceFaceIds as number[]
    const seen = new Set<number>()
    for (const group of geometry.groups) {
      const id = sourceIds[group.start / 3]
      seen.add(id)
      expect(materials[group.materialIndex!].color.getHexString()).toBe(id === 0 ? 'ef4444' : wireframe ? '263b4d' : '2563eb')
      expect(materials[group.materialIndex!].polygonOffset).toBe(false)
    }
    expect(seen.has(0)).toBe(true)
    expect(seen.size).toBeGreaterThan(1)
    const highlighted = roiSurfaceAppearance(geometry, scene, { ...options, selectedFaceIds: [0] })
    const selectedIndex = geometry.groups.find((group) => sourceIds[group.start / 3] === 0)!.materialIndex!
    expect(highlighted[selectedIndex].color.getHexString()).not.toBe('ef4444')
    materials.concat(highlighted).forEach((material) => material.dispose())
    geometry.dispose()
  })
})
