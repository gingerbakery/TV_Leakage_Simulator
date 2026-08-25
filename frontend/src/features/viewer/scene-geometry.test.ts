import { describe, expect, it } from 'vitest'

import type { ScenePayload } from '@/api'
import { createSceneFixture } from '@/test/scene-fixture'

import {
  createComponentGeometry,
  createFeatureEdgeGeometry,
  findCadSurfaceFaceIds,
  findCoplanarFacePatch,
  getSceneBounds,
  resolveComponentColor,
} from './scene-geometry'

describe('Three.js scene geometry', () => {
  it('preserves the CAD-authored component display color', () => {
    const component = {
      ...createSceneFixture().components[0],
      color: '#ff0000',
    }

    expect(resolveComponentColor(component, 0)).toBe(0xff0000)
    expect(resolveComponentColor({ ...component, color: null }, 0)).toBe(0x64748b)
  })

  it('groups tessellation triangles by original CAD face id', () => {
    const scene = createSceneFixture()
    scene.mesh.face_source_ids = [10, 10, 11, 20, 20]

    expect(
      findCadSurfaceFaceIds(scene, scene.components[0].face_indices, 0),
    ).toEqual([0, 1])
    expect(
      findCadSurfaceFaceIds(scene, scene.components[0].face_indices, 2),
    ).toEqual([2])
  })

  it('builds component-local triangles with stable source face ids', () => {
    const scene = createSceneFixture()
    const bundle = createComponentGeometry(scene, scene.components[0])
    const position = bundle.geometry.getAttribute('position')

    expect(position.count).toBe(9)
    expect(bundle.faceIds).toEqual([0, 1, 2])
    expect(bundle.geometry.userData.sourceFaceIds).toEqual([0, 1, 2])
    expect(bundle.center.toArray()).toEqual([30, 30, 5])
    expect([position.getX(0), position.getY(0), position.getZ(0)]).toEqual([
      -30,
      -30,
      -5,
    ])

    bundle.geometry.dispose()
  })

  it('shares indexed vertices inside one authored CAD face', () => {
    const scene = createSceneFixture()
    scene.mesh.face_source_ids = [10, 10, 11, 20, 20]
    const bundle = createComponentGeometry(scene, scene.components[0])

    expect(bundle.geometry.getAttribute('position').count).toBe(7)
    expect(bundle.geometry.getIndex()?.count).toBe(9)
    expect(bundle.faceIds).toBe(scene.components[0].face_indices)

    bundle.geometry.dispose()
  })

  it('builds clean feature edge segments in component-local space', () => {
    const scene = createSceneFixture()
    const bundle = createComponentGeometry(scene, scene.components[0])
    const geometry = createFeatureEdgeGeometry(
      scene.mesh.feature_edge_segments.filter(
        (segment) => segment.component_id === 1,
      ),
      bundle.center,
    )
    const position = geometry.getAttribute('position')

    expect(position.count).toBe(2)
    expect([position.getX(0), position.getY(0), position.getZ(0)]).toEqual([
      -30,
      -30,
      -5,
    ])
    expect([position.getX(1), position.getY(1), position.getZ(1)]).toEqual([
      30,
      -30,
      -5,
    ])

    geometry.dispose()
    bundle.geometry.dispose()
  })

  it('derives a stable fit-to-view bounding box', () => {
    const bounds = getSceneBounds(createSceneFixture())

    expect(bounds.center.toArray()).toEqual([30, 30, 10])
    expect(bounds.size.toArray()).toEqual([60, 60, 20])
  })

  it('expands one picked triangle to its connected coplanar surface', () => {
    const scene = createSceneFixture()
    scene.mesh.vertices[3][2] = 0
    scene.mesh.face_centroids[1][2] = 0

    expect(
      findCoplanarFacePatch(
        scene,
        scene.components[0].face_indices,
        0,
      ),
    ).toEqual([0, 1])
    expect(
      findCoplanarFacePatch(
        scene,
        scene.components[0].face_indices,
        2,
      ),
    ).toEqual([2])
  })

  it('bridges CAD faces tessellated independently (duplicate seam vertices, not shared indices)', () => {
    // Two coplanar 10x10 squares sitting side by side in the XY plane, each
    // triangulated on its own - exactly what a STEP tessellator produces
    // when two adjacent B-rep faces are meshed independently: the shared
    // seam is geometrically coincident but uses two *different* vertex
    // indices per side, never a shared one.
    const scene: ScenePayload = {
      ...createSceneFixture(),
      mesh: {
        vertices: [
          [0, 0, 0], // 0
          [10, 0, 0], // 1 - left square's right-bottom corner
          [10, 10, 0], // 2 - left square's right-top corner
          [0, 10, 0], // 3
          [10, 0, 0], // 4 - right square's left-bottom corner (duplicate of 1)
          [10, 10, 0], // 5 - right square's left-top corner (duplicate of 2)
          [20, 0, 0], // 6
          [20, 10, 0], // 7
        ],
        faces: [
          [0, 1, 2],
          [0, 2, 3],
          [4, 6, 7],
          [4, 7, 5],
        ],
        face_ids: [0, 1, 2, 3],
        face_component_ids: [1, 1, 1, 1],
        face_material_ids: ['', '', '', ''],
        face_normals: [
          [0, 0, 1],
          [0, 0, 1],
          [0, 0, 1],
          [0, 0, 1],
        ],
        face_centroids: [
          [6.667, 3.333, 0],
          [3.333, 6.667, 0],
          [16.667, 3.333, 0],
          [13.333, 6.667, 0],
        ],
        face_areas_mm2: [50, 50, 50, 50],
        feature_edge_segments: [],
      },
      components: [
        {
          object_id: 1,
          component_id: 1,
          object_name: 'Panel',
          component_name: 'Panel',
          face_indices: [0, 1, 2, 3],
          face_count: 4,
          area_mm2: 200,
          bbox_min: [0, 0, 0],
          bbox_max: [20, 10, 0],
          is_truncated: false,
          color: null,
        },
      ],
      metadata: {
        ...createSceneFixture().metadata,
        face_count: 4,
        vertex_count: 8,
        component_count: 1,
      },
    }
    scene.objects = scene.components

    expect(
      findCoplanarFacePatch(scene, scene.components[0].face_indices, 0),
    ).toEqual([0, 1, 2, 3])
  })
})
