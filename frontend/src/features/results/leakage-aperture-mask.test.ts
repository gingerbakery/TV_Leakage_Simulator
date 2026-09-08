import { describe, expect, it } from 'vitest'

import type { ScenePayload, Vec3 } from '@/api'

import { buildLeakageApertureMasks, type LeakageApertureMask } from './leakage-aperture-mask'
import type { LeakageAabbFace, LeakagePreviewBounds, LeakagePreviewSample } from './leakage-preview-data'

type Rectangle = readonly [number, number, number, number]
const bounds: LeakagePreviewBounds = { minimum: [0, 0, 0], maximum: [10, 10, 1] }

function plate(holes: Rectangle[] = [], size = 10): ScenePayload {
  const us = [...new Set([0, size, ...holes.flatMap((hole) => [hole[0], hole[2]])])].sort((a, b) => a - b)
  const vs = [...new Set([0, size, ...holes.flatMap((hole) => [hole[1], hole[3]])])].sort((a, b) => a - b)
  const vertices: Vec3[] = []
  const faces: [number, number, number][] = []
  for (let y = 0; y + 1 < vs.length; y += 1) {
    for (let x = 0; x + 1 < us.length; x += 1) {
      const centerU = (us[x] + us[x + 1]) / 2
      const centerV = (vs[y] + vs[y + 1]) / 2
      if (holes.some((hole) => centerU > hole[0] && centerU < hole[2] && centerV > hole[1] && centerV < hole[3])) continue
      const start = vertices.length
      vertices.push([us[x], vs[y], 1], [us[x + 1], vs[y], 1], [us[x + 1], vs[y + 1], 1], [us[x], vs[y + 1], 1])
      faces.push([start, start + 1, start + 2], [start, start + 2, start + 3])
    }
  }
  return {
    schema_version: 'mesh-scene.v1',
    units: { length: 'mm' },
    coordinate_system: { handedness: 'right', axes: { x: 'model_x', y: 'model_y', z: 'model_z' } },
    mesh: {
      vertices,
      faces,
      face_ids: faces.map((_, index) => index),
      face_component_ids: faces.map(() => 0),
      face_material_ids: faces.map(() => ''),
      face_normals: faces.map(() => [0, 0, 1]),
      face_centroids: faces.map(() => [0, 0, 0]),
      face_areas_mm2: faces.map(() => 0),
      feature_edge_segments: [],
    },
    objects: [],
    components: [],
    metadata: {
      face_count: faces.length,
      vertex_count: vertices.length,
      component_count: 0,
      source_file: 'arbitrary-plate.step',
      synthetic: true,
      import_note: '',
      receiver_face_hint: [],
      scene_token: 'aperture-test',
    },
  }
}

function sample(u = 5, v = 4.95, face: LeakageAabbFace = 'z_max'): LeakagePreviewSample {
  return { runId: 'mask', pathIndex: 0, receiverId: 'receiver', exitPoint: [u, v, 1], exitFaces: [face], outgoingDirection: [0, 0, 1], weight: 1 }
}

function at(mask: LeakageApertureMask, u: number, v: number): number {
  return Math.floor((v - mask.origin[1]) / mask.stepMm) * mask.width + Math.floor((u - mask.origin[0]) / mask.stepMm)
}

function ids(mask: LeakageApertureMask): number[] {
  return [...new Set(mask.componentIds)].filter((id) => id >= 0).sort()
}

describe('bounded planar aperture masks', () => {
  it('does not manufacture an opening through a closed triangle skin', () => {
    expect(buildLeakageApertureMasks(plate(), bounds, [sample()])).toEqual({ masks: [], reason: 'no_supported_planar_aperture' })
  })

  it('recovers an actual 0.5 mm slit using whole uncovered cells', () => {
    const { masks } = buildLeakageApertureMasks(plate([[2, 4.7, 8, 5.2]]), bounds, [sample(3), sample(7)])
    expect(masks).toHaveLength(1)
    const mask = masks[0]
    expect(mask.stepMm).toBe(0.1)
    expect(mask.origin).toEqual([0, 0])
    expect(mask.plane).toBe(1)
    expect(ids(mask)).toEqual([0])
    expect(mask.open.reduce((sum, value) => sum + value, 0)).toBe(300)
    expect(mask.open[at(mask, 5, 4.95)]).toBe(1)
    expect(mask.open[at(mask, 5, 4.65)]).toBe(0)
    expect(mask.open[at(mask, 5, 5.25)]).toBe(0)
  })

  it('keeps nearby disjoint slits in separate components', () => {
    const holes: Rectangle[] = [[2, 4, 8, 4.5], [2, 4.7, 8, 5.2]]
    const { masks } = buildLeakageApertureMasks(plate(holes), bounds, [sample(3, 4.25), sample(7, 4.95)])
    expect(ids(masks[0])).toEqual([0, 1])
    expect(masks[0].open[at(masks[0], 5, 4.6)]).toBe(0)
    expect(masks[0].componentIds[at(masks[0], 3, 4.25)]).not.toBe(masks[0].componentIds[at(masks[0], 7, 4.95)])
  })

  it('retains a 0.02 mm metal barrier even though the grid is 0.1 mm', () => {
    const holes: Rectangle[] = [[2, 4.7, 4.91, 5.2], [4.93, 4.7, 8, 5.2]]
    const { masks } = buildLeakageApertureMasks(plate(holes), bounds, [sample(3), sample(7)])
    expect(ids(masks[0])).toEqual([0, 1])
    expect(masks[0].open[at(masks[0], 4.95, 4.95)]).toBe(0)
    expect(masks[0].componentIds[at(masks[0], 4.95, 4.95)]).toBe(-1)
  })

  it('does not turn a cell open when only its center lies in the gap', () => {
    const result = buildLeakageApertureMasks(plate([[2, 4.94, 8, 4.97]]), bounds, [sample(5, 4.95)])
    expect(result.masks).toEqual([])
  })

  it('discards holes without receiver-bound sample support', () => {
    const holes: Rectangle[] = [[2, 4, 8, 4.5], [2, 4.7, 8, 5.2]]
    const { masks } = buildLeakageApertureMasks(plate(holes), bounds, [sample(5, 4.25)])
    expect(ids(masks[0])).toEqual([0])
    expect(masks[0].open[at(masks[0], 5, 4.95)]).toBe(0)
  })

  it('rejects uncovered space reaching the model edge or inspected patch edge', () => {
    expect(buildLeakageApertureMasks(plate([[0, 4.7, 8, 5.2]]), bounds, [sample()]).masks).toEqual([])
    const largeBounds: LeakagePreviewBounds = { minimum: [0, 0, 0], maximum: [30, 30, 1] }
    expect(buildLeakageApertureMasks(plate([[2, 14.7, 28, 15.2]], 30), largeBounds, [sample(15, 14.95)]).masks).toEqual([])
  })

  it('does not interpret a recessed plate or ambiguous corner as exterior skin', () => {
    const recessed = plate([[2, 4.7, 8, 5.2]])
    recessed.mesh.vertices.forEach((point) => { point[2] = 0.8 })
    expect(buildLeakageApertureMasks(recessed, bounds, [sample()]).masks).toEqual([])
    expect(buildLeakageApertureMasks(plate([[2, 4.7, 8, 5.2]]), bounds, [{ ...sample(), exitFaces: ['z_max', 'x_max'] }]).masks).toEqual([])
  })

  it.each(['x_min', 'x_max', 'y_min', 'y_max', 'z_min', 'z_max'] as const)('uses the documented axis projection on %s', (face) => {
    const scene = plate([[2, 4.7, 8, 5.2]])
    const axis = face[0] === 'x' ? 0 : face[0] === 'y' ? 1 : 2
    const plane = face.endsWith('min') ? 0 : 1
    const map = ([u, v]: readonly number[]): Vec3 => axis === 0 ? [plane, u, v] : axis === 1 ? [u, plane, v] : [u, v, plane]
    scene.mesh.vertices = scene.mesh.vertices.map(map)
    const high: Vec3 = [10, 10, 10]
    high[axis] = 1
    const result = buildLeakageApertureMasks(scene, { minimum: [0, 0, 0], maximum: high }, [{ ...sample(), exitPoint: map([5, 4.95]), exitFaces: [face] }])
    expect(result.masks).toHaveLength(1)
    expect(result.masks[0]).toMatchObject({ face, plane })
    expect(result.masks[0].open[at(result.masks[0], 5, 4.95)]).toBe(1)
  })

  it('fails closed before allocating an excessive cell grid', () => {
    const largeBounds: LeakagePreviewBounds = { minimum: [0, 0, 0], maximum: [100, 100, 1] }
    expect(buildLeakageApertureMasks(plate([], 100), largeBounds, [sample(10, 10), sample(90, 90)])).toEqual({ masks: [], reason: 'aperture_cell_limit' })
  })

  it('bounds mesh scanning and triangle raster work separately', () => {
    const tooManyFaces = plate()
    tooManyFaces.mesh.faces = Array.from({ length: 250_001 }, () => [0, 1, 2])
    expect(buildLeakageApertureMasks(tooManyFaces, bounds, [sample()])).toEqual({ masks: [], reason: 'aperture_triangle_limit' })
    const expensive = plate()
    expensive.mesh.faces = Array.from({ length: 600 }, () => [0, 1, 2])
    expect(buildLeakageApertureMasks(expensive, bounds, [sample()])).toEqual({ masks: [], reason: 'aperture_triangle_cell_limit' })
  })

  it('rejects invalid referenced geometry and invalid bounds', () => {
    const scene = plate()
    scene.mesh.faces[0][0] = 999
    expect(buildLeakageApertureMasks(scene, bounds, [sample()]).reason).toBe('invalid_aperture_mesh')
    expect(buildLeakageApertureMasks(plate(), { minimum: [0, 0, 0], maximum: [NaN, 10, 1] }, [sample()]).reason).toBe('invalid_aperture_bounds')
  })

  it.each(['x_min', 'x_max', 'y_min', 'y_max', 'z_min', 'z_max'] as const)('resolves an off-grid 0.1 mm seam on %s without opening metal cells', (face) => {
    const scene = plate([[2, 4.837, 8, 4.937]])
    const axis = face[0] === 'x' ? 0 : face[0] === 'y' ? 1 : 2
    const plane = face.endsWith('min') ? 0 : 1
    const map = ([u, v]: readonly number[]): Vec3 => axis === 0 ? [plane, u, v] : axis === 1 ? [u, plane, v] : [u, v, plane]
    scene.mesh.vertices = scene.mesh.vertices.map(map)
    const maximum: Vec3 = [10, 10, 10]
    maximum[axis] = 1
    const result = buildLeakageApertureMasks(scene, { minimum: [0, 0, 0], maximum }, [
      { ...sample(), exitPoint: map([3, 4.887]), exitFaces: [face] },
      { ...sample(), pathIndex: 1, exitPoint: map([7, 4.887]), exitFaces: [face] },
    ])
    expect(result.masks).toHaveLength(1)
    const mask = result.masks[0]
    expect(mask.stepMm).toBe(0.025)
    expect(ids(mask)).toEqual([0])
    expect(mask.open[at(mask, 5, 4.887)]).toBe(1)
    expect(mask.open[at(mask, 5, 4.825)]).toBe(0)
    expect(mask.open[at(mask, 5, 4.95)]).toBe(0)
    for (let index = 0; index < mask.open.length; index += 1) {
      if (!mask.open[index]) continue
      const lowerV = mask.origin[1] + Math.floor(index / mask.width) * mask.stepMm
      expect(lowerV).toBeGreaterThanOrEqual(4.837 - 1e-9)
      expect(lowerV + mask.stepMm).toBeLessThanOrEqual(4.937 + 1e-9)
    }
  })

  it('refines even a grid-aligned 0.1 mm seam so its width has multiple cells', () => {
    const { masks } = buildLeakageApertureMasks(plate([[2, 4.8, 8, 4.9]]), bounds, [sample(5, 4.85)])
    expect(masks[0].stepMm).toBe(0.025)
    expect(masks[0].open.reduce((sum, value) => sum + value, 0)).toBe(960)
  })

  it('keeps an off-center 0.006 mm metal barrier closed after refinement', () => {
    const holes: Rectangle[] = [[2, 4.837, 4.901, 4.937], [4.907, 4.837, 8, 4.937]]
    const { masks } = buildLeakageApertureMasks(plate(holes), bounds, [sample(3, 4.887), sample(7, 4.887)])
    const mask = masks[0]
    expect(mask.stepMm).toBe(0.025)
    expect(ids(mask)).toEqual([0, 1])
    // The cell center at 4.9125 is outside the metal, but the cell overlaps it.
    expect(mask.open[at(mask, 4.9125, 4.887)]).toBe(0)
    expect(mask.componentIds[at(mask, 3, 4.887)]).not.toBe(mask.componentIds[at(mask, 7, 4.887)])
  })

  it('uses real transverse skin boundaries to keep a long thin seam within budget', () => {
    const largeBounds: LeakagePreviewBounds = { minimum: [0, 0, 0], maximum: [100, 100, 1] }
    const { masks } = buildLeakageApertureMasks(plate([[8, 50.037, 28, 50.137]], 100), largeBounds, [sample(10, 50.087), sample(26, 50.087)])
    expect(masks).toHaveLength(1)
    const mask = masks[0]
    expect(mask.stepMm).toBe(0.025)
    expect(mask.open.length).toBeLessThan(20_000)
    expect(mask.open[at(mask, 8.05, 50.087)]).toBe(1)
    expect(mask.open[at(mask, 27.95, 50.087)]).toBe(1)
    expect(mask.open[at(mask, 7.95, 50.087)]).toBe(0)
  })

  it('still rejects a narrow seam that opens through the inspected boundary', () => {
    expect(buildLeakageApertureMasks(plate([[0, 4.837, 8, 4.937]]), bounds, [sample(5, 4.887)]).masks).toEqual([])
  })

  it('preserves aligned aperture coverage after binary Float32 coordinate encoding', () => {
    const scene = plate([[8, 2, 8.3, 14]], 30)
    scene.mesh.vertices = scene.mesh.vertices.map((point) => point.map((coordinate, axis) =>
      Math.fround(axis < 2 && coordinate === 0 ? -0.4 : coordinate)) as Vec3)
    const binaryBounds: LeakagePreviewBounds = {
      minimum: [Math.fround(-0.4), Math.fround(-0.4), 0], maximum: [30, 30, 1],
    }
    const rays = [sample(8.005, 2.02), { ...sample(8.295, 13.98), pathIndex: 1 }]
    const { masks } = buildLeakageApertureMasks(scene, binaryBounds, rays)
    expect(masks).toHaveLength(1)
    const mask = masks[0]
    expect(mask.stepMm).toBe(0.1)
    expect(mask.open.reduce((sum, value) => sum + value, 0) * mask.stepMm ** 2).toBeCloseTo(3.6, 10)
    for (const ray of rays) expect(mask.open[at(mask, ray.exitPoint[0], ray.exitPoint[1])]).toBe(1)
    for (let index = 0; index < mask.open.length; index++) {
      if (!mask.open[index]) continue
      const u = mask.origin[0] + index % mask.width * mask.stepMm
      const v = mask.origin[1] + Math.floor(index / mask.width) * mask.stepMm
      expect(u).toBeGreaterThanOrEqual(8 - 1e-9)
      expect(u + mask.stepMm).toBeLessThanOrEqual(Math.fround(8.3) + 1e-9)
      expect(v).toBeGreaterThanOrEqual(2 - 1e-9)
      expect(v + mask.stepMm).toBeLessThanOrEqual(14 + 1e-9)
    }
  })

  it('keeps genuine off-grid metal barriers closed when stabilizing a Float32 lattice', () => {
    const scene = plate([[8, 2, 8.3, 7.901], [8, 7.907, 8.3, 14]], 30)
    scene.mesh.vertices = scene.mesh.vertices.map((point) => point.map((coordinate, axis) =>
      Math.fround(axis < 2 && coordinate === 0 ? -0.4 : coordinate)) as Vec3)
    const binaryBounds: LeakagePreviewBounds = {
      minimum: [Math.fround(-0.4), Math.fround(-0.4), 0], maximum: [30, 30, 1],
    }
    const { masks } = buildLeakageApertureMasks(scene, binaryBounds,
      [sample(8.15, 3), { ...sample(8.15, 13), pathIndex: 1 }])
    const mask = masks[0]
    expect(ids(mask)).toEqual([0, 1])
    expect(mask.open[at(mask, 8.15, 7.904)]).toBe(0)
    for (let index = 0; index < mask.open.length; index++) {
      if (!mask.open[index]) continue
      const v = mask.origin[1] + Math.floor(index / mask.width) * mask.stepMm
      expect(v + mask.stepMm <= Math.fround(7.901) + 1e-9 || v >= Math.fround(7.907) - 1e-9).toBe(true)
    }
  })

})
