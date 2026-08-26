import { describe, expect, it } from 'vitest'

import { createSceneFixture } from '@/test/scene-fixture'
import type { RoiScope } from '@/stores'
import type { ScenePayload } from '@/api'

import { buildRoiClippedGeometries } from './roi-clipped-geometry'
import {
  getActiveRoiFaceIds,
  groupRoiFacesByComponent,
  resolveFacesInRoiBox,
  resolveNearestVisibleFace,
  summarizeActiveRoiScopes,
  triangleIntersectsRoiBox,
} from './roi-selection'

describe('ROI selection', () => {
  it('uses true triangle-to-box intersection instead of centroid only', () => {
    expect(
      triangleIntersectsRoiBox(
        [
          [0, 0],
          [100, 0],
          [0, 100],
        ],
        { xMin: 1, xMax: 2, yMin: 1, yMax: 2 },
      ),
    ).toBe(true)
    expect(
      triangleIntersectsRoiBox(
        [
          [0, 0],
          [5, 0],
          [0, 5],
        ],
        { xMin: 10, xMax: 12, yMin: 10, yMax: 12 },
      ),
    ).toBe(false)
  })

  it('selects XY-intersecting faces and excludes hidden components', () => {
    const scene = createSceneFixture()
    const box = { xMin: 58, xMax: 59, yMin: 1, yMax: 2 }

    expect(resolveFacesInRoiBox(scene, box, [])).toEqual([0, 2])
    expect(resolveFacesInRoiBox(scene, box, [1])).toEqual([])
  })

  it('projects face selection onto YZ and ZX ROI planes', () => {
    const scene = createSceneFixture()
    const yzBox = {
      plane: 'yz' as const,
      xMin: 0,
      xMax: 60,
      yMin: 4,
      yMax: 6,
      zMin: 9,
      zMax: 11,
    }
    const zxBox = {
      plane: 'zx' as const,
      xMin: 4,
      xMax: 6,
      yMin: 0,
      yMax: 60,
      zMin: 9,
      zMax: 11,
    }

    expect(resolveFacesInRoiBox(scene, yzBox, [])).toEqual([3, 4])
    expect(resolveFacesInRoiBox(scene, zxBox, [])).toEqual([
      1, 2, 3, 4,
    ])
  })

  it('uses all six XYZ boundaries for coordinate-box ROI selection', () => {
    const scene = createSceneFixture()
    expect(
      resolveFacesInRoiBox(
        scene,
        {
          plane: 'xyz',
          xMin: 5,
          xMax: 55,
          yMin: 5,
          yMax: 55,
          zMin: 11,
          zMax: 20,
        },
        [],
      ),
    ).toEqual([3, 4])
  })

  it('groups ROI metadata by component and resolves coordinate input', () => {
    const scene = createSceneFixture()
    const components = groupRoiFacesByComponent(
      scene,
      [0, 3],
      { 2: 'Rear cover' },
    )

    expect(components).toHaveLength(2)
    expect(components[0]).toMatchObject({
      componentId: 1,
      faceIds: [0],
      areaMm2: 1800,
    })
    expect(components[1]).toMatchObject({
      componentId: 2,
      componentName: 'Rear cover',
      faceIds: [3],
      areaMm2: 1250,
    })
    expect(
      resolveNearestVisibleFace(
        scene,
        { x: 38, y: 22, z: 13 },
        [],
      ),
    ).toBe(3)
    expect(
      resolveNearestVisibleFace(
        scene,
        { x: 38, y: 22, z: 13 },
        [2],
      ),
    ).toBe(0)
  })

  it('merges only active ROI scopes into the analysis summary', () => {
    const scene = createSceneFixture()
    const componentOne = groupRoiFacesByComponent(scene, [0, 1])
    const componentTwo = groupRoiFacesByComponent(scene, [3])
    const scopes: RoiScope[] = [
      {
        id: 'roi-1',
        scopeId: 'ROI-1',
        source: 'box',
        view: 'front_xy',
        components: componentOne,
        active: true,
      },
      {
        id: 'roi-2',
        scopeId: 'ROI-2',
        source: 'point',
        view: 'coordinate',
        components: componentTwo,
        active: false,
      },
    ]

    expect(getActiveRoiFaceIds(scopes)).toEqual([0, 1])
    expect(summarizeActiveRoiScopes(scopes)).toMatchObject({
      scopeCount: 1,
      faceCount: 2,
      componentCount: 1,
      areaMm2: 3600,
    })
  })

  it('clips a closed solid at exact box planes and caps every section', () => {
    const cubeVertices: ScenePayload['mesh']['vertices'] = [
      [0, 0, 0],
      [1, 0, 0],
      [1, 1, 0],
      [0, 1, 0],
      [0, 0, 1],
      [1, 0, 1],
      [1, 1, 1],
      [0, 1, 1],
    ]
    const cubeFaces: ScenePayload['mesh']['faces'] = [
      [0, 2, 1],
      [0, 3, 2],
      [4, 5, 6],
      [4, 6, 7],
      [0, 1, 5],
      [0, 5, 4],
      [1, 2, 6],
      [1, 6, 5],
      [2, 3, 7],
      [2, 7, 6],
      [3, 0, 4],
      [3, 4, 7],
    ]
    const scene: ScenePayload = {
      ...createSceneFixture(),
      mesh: {
        vertices: cubeVertices,
        faces: cubeFaces,
        face_ids: cubeFaces.map((_, index) => index),
        face_component_ids: cubeFaces.map(() => 1),
        face_material_ids: cubeFaces.map(() => ''),
        face_normals: cubeFaces.map(() => [0, 0, 1]),
        face_centroids: cubeFaces.map(() => [0.5, 0.5, 0.5]),
        face_areas_mm2: cubeFaces.map(() => 0.5),
        feature_edge_segments: [],
      },
      components: [
        {
          ...createSceneFixture().components[0],
          face_indices: cubeFaces.map((_, index) => index),
          face_count: cubeFaces.length,
          bbox_min: [0, 0, 0],
          bbox_max: [1, 1, 1],
        },
      ],
      objects: [],
      metadata: {
        ...createSceneFixture().metadata,
        face_count: cubeFaces.length,
        vertex_count: cubeVertices.length,
        component_count: 1,
      },
    }
    scene.objects = scene.components

    const clipped = buildRoiClippedGeometries(
      scene,
      cubeFaces.map((_, index) => index),
      [{ xMin: 0.25, xMax: 0.75, yMin: -1, yMax: 2 }],
    )

    expect(clipped).not.toBeNull()
    expect(clipped?.openChainCount).toBe(0)
    expect(clipped?.capLoopCount).toBe(2)
    expect(clipped?.capGeometry).not.toBeNull()
    expect(clipped?.surfaceGeometry.index).toBeNull()
    const overlayOnly = buildRoiClippedGeometries(
      scene,
      cubeFaces.map((_, index) => index),
      [{ xMin: 0.25, xMax: 0.75, yMin: -1, yMax: 2 }],
      [],
      undefined,
      { includeCaps: false, includeFeatureEdges: false },
    )
    expect(overlayOnly?.surfaceGeometry).not.toBeNull()
    expect(overlayOnly?.capGeometry).toBeNull()
    expect(overlayOnly?.capEdgeGeometry).toBeNull()
    expect(overlayOnly?.featureEdgeGeometry).toBeNull()
    const surfaceNormals =
      clipped?.surfaceGeometry.getAttribute('normal')
    const triangleNormalKeys = new Set<string>()
    for (
      let vertexIndex = 0;
      vertexIndex < (surfaceNormals?.count ?? 0);
      vertexIndex += 3
    ) {
      const first = [
        surfaceNormals?.getX(vertexIndex) ?? 0,
        surfaceNormals?.getY(vertexIndex) ?? 0,
        surfaceNormals?.getZ(vertexIndex) ?? 0,
      ]
      const second = [
        surfaceNormals?.getX(vertexIndex + 1) ?? 0,
        surfaceNormals?.getY(vertexIndex + 1) ?? 0,
        surfaceNormals?.getZ(vertexIndex + 1) ?? 0,
      ]
      const third = [
        surfaceNormals?.getX(vertexIndex + 2) ?? 0,
        surfaceNormals?.getY(vertexIndex + 2) ?? 0,
        surfaceNormals?.getZ(vertexIndex + 2) ?? 0,
      ]
      expect(second).toEqual(first)
      expect(third).toEqual(first)
      triangleNormalKeys.add(
        first.map((value) => value.toFixed(4)).join(':'),
      )
    }
    expect(triangleNormalKeys.size).toBeGreaterThan(1)
    const surfaceComponentIds = clipped?.surfaceGeometry.userData
      .componentIds as number[] | undefined
    expect(surfaceComponentIds).toHaveLength(
      clipped?.clippedTriangleCount ?? 0,
    )
    expect(new Set(surfaceComponentIds)).toEqual(new Set([1]))
    const capComponentIds = clipped?.capGeometry?.userData
      .componentIds as number[] | undefined
    expect(capComponentIds?.length).toBeGreaterThan(0)
    expect(new Set(capComponentIds)).toEqual(new Set([1]))

    const positions = clipped?.surfaceGeometry.getAttribute('position')
    const xValues = Array.from(
      { length: positions?.count ?? 0 },
      (_, index) => positions?.getX(index) ?? 0,
    )
    expect(Math.min(...xValues)).toBeCloseTo(0.25)
    expect(Math.max(...xValues)).toBeCloseTo(0.75)

    const transformed = buildRoiClippedGeometries(
      scene,
      cubeFaces.map((_, index) => index),
      [{ xMin: 0.25, xMax: 0.75, yMin: -1, yMax: 2 }],
      [],
      (componentId, point) =>
        componentId === 1
          ? [point[0], point[1], point[2] + 3]
          : [point[0], point[1], point[2]],
    )
    expect(transformed?.openChainCount).toBe(0)
    expect(transformed?.capLoopCount).toBe(2)
    const transformedPositions =
      transformed?.surfaceGeometry.getAttribute('position')
    const transformedZValues = Array.from(
      { length: transformedPositions?.count ?? 0 },
      (_, index) => transformedPositions?.getZ(index) ?? 0,
    )
    expect(Math.min(...transformedZValues)).toBeCloseTo(3)
    expect(Math.max(...transformedZValues)).toBeCloseTo(4)
    expect(
      new Set(
        transformed?.surfaceGeometry.userData
          .componentIds as number[],
      ),
    ).toEqual(new Set([1]))

    const translatedAcrossClipPlane = buildRoiClippedGeometries(
      scene,
      cubeFaces.map((_, index) => index),
      [{ xMin: 0.25, xMax: 0.75, yMin: -1, yMax: 2 }],
      [],
      (componentId, point) =>
        componentId === 1
          ? [point[0] + 1.5, point[1], point[2]]
          : [point[0], point[1], point[2]],
    )
    expect(translatedAcrossClipPlane).not.toBeNull()
    expect(translatedAcrossClipPlane?.openChainCount).toBe(0)
    expect(translatedAcrossClipPlane?.capLoopCount).toBe(2)
    const translatedPositions =
      translatedAcrossClipPlane?.surfaceGeometry.getAttribute(
        'position',
      )
    const translatedXValues = Array.from(
      { length: translatedPositions?.count ?? 0 },
      (_, index) => translatedPositions?.getX(index) ?? 0,
    )
    expect(Math.min(...translatedXValues)).toBeCloseTo(1.75)
    expect(Math.max(...translatedXValues)).toBeCloseTo(2.25)
    const translatedCapPositions =
      translatedAcrossClipPlane?.capGeometry?.getAttribute(
        'position',
      )
    const translatedCapXValues = Array.from(
      { length: translatedCapPositions?.count ?? 0 },
      (_, index) => translatedCapPositions?.getX(index) ?? 0,
    )
    expect(Math.min(...translatedCapXValues)).toBeCloseTo(1.75)
    expect(Math.max(...translatedCapXValues)).toBeCloseTo(2.25)

    const yzClipped = buildRoiClippedGeometries(
      scene,
      cubeFaces.map((_, index) => index),
      [
        {
          plane: 'yz',
          xMin: 0,
          xMax: 1,
          yMin: 0.25,
          yMax: 0.75,
          zMin: -1,
          zMax: 2,
        },
      ],
    )
    expect(yzClipped?.openChainCount).toBe(0)
    expect(yzClipped?.capLoopCount).toBe(2)
    const yzPositions =
      yzClipped?.surfaceGeometry.getAttribute('position')
    const yValues = Array.from(
      { length: yzPositions?.count ?? 0 },
      (_, index) => yzPositions?.getY(index) ?? 0,
    )
    expect(Math.min(...yValues)).toBeCloseTo(0.25)
    expect(Math.max(...yValues)).toBeCloseTo(0.75)

    const zxClipped = buildRoiClippedGeometries(
      scene,
      cubeFaces.map((_, index) => index),
      [
        {
          plane: 'zx',
          xMin: -1,
          xMax: 2,
          yMin: 0,
          yMax: 1,
          zMin: 0.25,
          zMax: 0.75,
        },
      ],
    )
    expect(zxClipped?.openChainCount).toBe(0)
    expect(zxClipped?.capLoopCount).toBe(2)
    const zxPositions =
      zxClipped?.surfaceGeometry.getAttribute('position')
    const zValues = Array.from(
      { length: zxPositions?.count ?? 0 },
      (_, index) => zxPositions?.getZ(index) ?? 0,
    )
    expect(Math.min(...zValues)).toBeCloseTo(0.25)
    expect(Math.max(...zValues)).toBeCloseTo(0.75)

    clipped?.surfaceGeometry.dispose()
    clipped?.capGeometry?.dispose()
    clipped?.capEdgeGeometry?.dispose()
    clipped?.featureEdgeGeometry?.dispose()
    transformed?.surfaceGeometry.dispose()
    transformed?.capGeometry?.dispose()
    transformed?.capEdgeGeometry?.dispose()
    transformed?.featureEdgeGeometry?.dispose()
    yzClipped?.surfaceGeometry.dispose()
    yzClipped?.capGeometry?.dispose()
    yzClipped?.capEdgeGeometry?.dispose()
    yzClipped?.featureEdgeGeometry?.dispose()
    zxClipped?.surfaceGeometry.dispose()
    zxClipped?.capGeometry?.dispose()
    zxClipped?.capEdgeGeometry?.dispose()
    zxClipped?.featureEdgeGeometry?.dispose()
  })
})
