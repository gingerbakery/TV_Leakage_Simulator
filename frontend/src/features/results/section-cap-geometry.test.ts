import { describe, expect, it } from 'vitest'
import { Vector3 } from 'three'

import type { ScenePayload } from '@/api'

import { computeSectionCapTriangles } from './section-cap-geometry'

/** A single 10x10x10 axis-aligned box, min corner at the origin. */
function createBoxScene(): ScenePayload {
  const vertices: [number, number, number][] = [
    [0, 0, 0],
    [10, 0, 0],
    [10, 10, 0],
    [0, 10, 0],
    [0, 0, 10],
    [10, 0, 10],
    [10, 10, 10],
    [0, 10, 10],
  ]
  const faces: [number, number, number][] = [
    [0, 1, 2], [0, 2, 3], // bottom
    [4, 6, 5], [4, 7, 6], // top
    [0, 5, 1], [0, 4, 5], // front (y=0)
    [3, 2, 6], [3, 6, 7], // back (y=10)
    [0, 3, 7], [0, 7, 4], // left (x=0)
    [1, 2, 6], [1, 6, 5], // right (x=10)
  ]
  return {
    schema_version: 'mesh-scene.v1',
    units: { length: 'mm' },
    coordinate_system: {
      handedness: 'right',
      axes: { x: 'model_x', y: 'model_y', z: 'model_z' },
    },
    mesh: {
      vertices,
      faces,
      face_ids: faces.map((_, index) => index),
      face_component_ids: faces.map(() => 1),
      face_material_ids: faces.map(() => ''),
      face_normals: faces.map(() => [0, 0, 1]),
      face_centroids: faces.map(() => [5, 5, 5]),
      face_areas_mm2: faces.map(() => 50),
      feature_edge_segments: [],
    },
    objects: [],
    components: [
      {
        object_id: 1,
        component_id: 1,
        object_name: 'Box',
        component_name: 'Box',
        face_indices: faces.map((_, index) => index),
        face_count: faces.length,
        area_mm2: 600,
        bbox_min: [0, 0, 0],
        bbox_max: [10, 10, 10],
        is_truncated: false,
        color: null,
      },
    ],
    metadata: {
      face_count: faces.length,
      vertex_count: vertices.length,
      component_count: 1,
      source_file: 'box.step',
      synthetic: true,
      import_note: 'Test fixture',
      receiver_face_hint: [],
      scene_token: 'box-fixture',
    },
  }
}

function triangleSetArea(positions: Float32Array): number {
  let total = 0
  for (let i = 0; i < positions.length; i += 9) {
    const a = new Vector3(positions[i], positions[i + 1], positions[i + 2])
    const b = new Vector3(positions[i + 3], positions[i + 4], positions[i + 5])
    const c = new Vector3(positions[i + 6], positions[i + 7], positions[i + 8])
    total += b.clone().sub(a).cross(c.clone().sub(a)).length() * 0.5
  }
  return total
}

describe('computeSectionCapTriangles', () => {
  it('fills the exact square cross-section of a box cut through its middle', () => {
    const scene = createBoxScene()
    const faceIds = scene.mesh.faces.map((_, index) => index)
    const positions = computeSectionCapTriangles(
      scene,
      faceIds,
      new Vector3(0, 0, 5),
      new Vector3(0, 0, 1),
      new Vector3(0, 1, 0),
      new Vector3(1, 0, 0),
    )
    expect(positions).not.toBeNull()
    expect(triangleSetArea(positions!)).toBeCloseTo(100, 1)
    // Every returned vertex should lie exactly on the cut plane (z = 5).
    for (let i = 2; i < positions!.length; i += 3) {
      expect(positions![i]).toBeCloseTo(5, 6)
    }
  })

  it('returns null when the plane misses the geometry entirely', () => {
    const scene = createBoxScene()
    const faceIds = scene.mesh.faces.map((_, index) => index)
    const positions = computeSectionCapTriangles(
      scene,
      faceIds,
      new Vector3(0, 0, 500),
      new Vector3(0, 0, 1),
      new Vector3(0, 1, 0),
      new Vector3(1, 0, 0),
    )
    expect(positions).toBeNull()
  })

  it('returns null when the face set cannot close into a loop (missing side walls)', () => {
    const scene = createBoxScene()
    // Only the top/bottom faces (indices 0-3), cut by a vertical (Y-normal)
    // plane: each flat face crosses the plane along its own separate line
    // (one at z=0, one at z=10), but with no side walls selected there's
    // nothing to connect those two lines into a closed loop - exactly the
    // ROI-scoped "flat faces only, no revealed walls" case found in
    // production, which is why it must resolve to null (not a broken
    // half-filled shape).
    const positions = computeSectionCapTriangles(
      scene,
      [0, 1, 2, 3],
      new Vector3(0, 5, 0),
      new Vector3(0, 1, 0),
      new Vector3(0, 0, 1),
      new Vector3(1, 0, 0),
    )
    expect(positions).toBeNull()
  })
})
