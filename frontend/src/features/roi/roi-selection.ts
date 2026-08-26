import type { ScenePayload } from '@/api'
import type {
  RoiClipBox,
  RoiComponentClip,
  RoiProjectionPlane,
  RoiScope,
  Vector3Value,
} from '@/stores'

type Point2 = readonly [number, number]
type Point3 = [number, number, number]

const intersectionEpsilon = 1e-9

function clipPolygonAtAxis(
  points: Point3[],
  axis: 0 | 1 | 2,
  boundary: number,
  keepGreater: boolean,
): Point3[] {
  if (points.length === 0) return []
  const output: Point3[] = []
  let previous = points[points.length - 1]
  let previousInside = keepGreater
    ? previous[axis] >= boundary - intersectionEpsilon
    : previous[axis] <= boundary + intersectionEpsilon

  for (const current of points) {
    const currentInside = keepGreater
      ? current[axis] >= boundary - intersectionEpsilon
      : current[axis] <= boundary + intersectionEpsilon
    if (currentInside !== previousInside) {
      const denominator = current[axis] - previous[axis]
      if (Math.abs(denominator) > intersectionEpsilon) {
        const ratio = (boundary - previous[axis]) / denominator
        output.push([
          previous[0] + (current[0] - previous[0]) * ratio,
          previous[1] + (current[1] - previous[1]) * ratio,
          previous[2] + (current[2] - previous[2]) * ratio,
        ])
      }
    }
    if (currentInside) output.push([...current])
    previous = current
    previousInside = currentInside
  }
  return output
}

function triangleIntersectsRoiVolume(
  triangle: Point3[],
  box: RoiClipBox,
): boolean {
  const bounds: Array<[0 | 1 | 2, number, number]> = [
    [0, box.xMin, box.xMax],
    [1, box.yMin, box.yMax],
    [2, box.zMin ?? 0, box.zMax ?? 0],
  ]
  let clipped = triangle
  for (const [axis, minimum, maximum] of bounds) {
    clipped = clipPolygonAtAxis(clipped, axis, minimum, true)
    clipped = clipPolygonAtAxis(clipped, axis, maximum, false)
    if (clipped.length < 3) return false
  }
  return true
}

function roiPlane(box: RoiClipBox): RoiProjectionPlane {
  return box.plane ?? 'xy'
}

function roiBoxBounds2d(
  box: RoiClipBox,
): [number, number, number, number] {
  if (roiPlane(box) === 'yz') {
    return [
      box.yMin,
      box.yMax,
      box.zMin ?? 0,
      box.zMax ?? 0,
    ]
  }
  if (roiPlane(box) === 'zx') {
    return [
      box.zMin ?? 0,
      box.zMax ?? 0,
      box.xMin,
      box.xMax,
    ]
  }
  return [box.xMin, box.xMax, box.yMin, box.yMax]
}

function projectVertexToRoiPlane(
  vertex: readonly [number, number, number],
  plane: RoiProjectionPlane,
): Point2 {
  if (plane === 'yz') return [vertex[1], vertex[2]]
  if (plane === 'zx') return [vertex[2], vertex[0]]
  return [vertex[0], vertex[1]]
}

function pointInBox(
  point: Point2,
  box: RoiClipBox,
): boolean {
  const [uMin, uMax, vMin, vMax] = roiBoxBounds2d(box)
  return (
    point[0] >= uMin - intersectionEpsilon &&
    point[0] <= uMax + intersectionEpsilon &&
    point[1] >= vMin - intersectionEpsilon &&
    point[1] <= vMax + intersectionEpsilon
  )
}

function signedArea(
  a: Point2,
  b: Point2,
  c: Point2,
): number {
  return (
    (b[0] - a[0]) * (c[1] - a[1]) -
    (b[1] - a[1]) * (c[0] - a[0])
  )
}

function pointInTriangle(point: Point2, triangle: Point2[]): boolean {
  const d1 = signedArea(point, triangle[0], triangle[1])
  const d2 = signedArea(point, triangle[1], triangle[2])
  const d3 = signedArea(point, triangle[2], triangle[0])
  const hasNegative =
    d1 < -intersectionEpsilon ||
    d2 < -intersectionEpsilon ||
    d3 < -intersectionEpsilon
  const hasPositive =
    d1 > intersectionEpsilon ||
    d2 > intersectionEpsilon ||
    d3 > intersectionEpsilon
  return !(hasNegative && hasPositive)
}

function onSegment(a: Point2, b: Point2, point: Point2): boolean {
  return (
    point[0] >= Math.min(a[0], b[0]) - intersectionEpsilon &&
    point[0] <= Math.max(a[0], b[0]) + intersectionEpsilon &&
    point[1] >= Math.min(a[1], b[1]) - intersectionEpsilon &&
    point[1] <= Math.max(a[1], b[1]) + intersectionEpsilon
  )
}

function segmentsIntersect(
  a: Point2,
  b: Point2,
  c: Point2,
  d: Point2,
): boolean {
  const d1 = signedArea(a, b, c)
  const d2 = signedArea(a, b, d)
  const d3 = signedArea(c, d, a)
  const d4 = signedArea(c, d, b)

  if (
    ((d1 > intersectionEpsilon && d2 < -intersectionEpsilon) ||
      (d1 < -intersectionEpsilon && d2 > intersectionEpsilon)) &&
    ((d3 > intersectionEpsilon && d4 < -intersectionEpsilon) ||
      (d3 < -intersectionEpsilon && d4 > intersectionEpsilon))
  ) {
    return true
  }

  return (
    (Math.abs(d1) <= intersectionEpsilon && onSegment(a, b, c)) ||
    (Math.abs(d2) <= intersectionEpsilon && onSegment(a, b, d)) ||
    (Math.abs(d3) <= intersectionEpsilon && onSegment(c, d, a)) ||
    (Math.abs(d4) <= intersectionEpsilon && onSegment(c, d, b))
  )
}

export function triangleIntersectsRoiBox(
  triangle: Point2[],
  box: RoiClipBox,
): boolean {
  if (triangle.length !== 3) return false

  const [uMin, uMax, vMin, vMax] = roiBoxBounds2d(box)
  const triangleU = triangle.map((point) => point[0])
  const triangleV = triangle.map((point) => point[1])
  if (
    Math.max(...triangleU) < uMin ||
    Math.min(...triangleU) > uMax ||
    Math.max(...triangleV) < vMin ||
    Math.min(...triangleV) > vMax
  ) {
    return false
  }

  if (triangle.some((point) => pointInBox(point, box))) {
    return true
  }

  const corners: Point2[] = [
    [uMin, vMin],
    [uMax, vMin],
    [uMax, vMax],
    [uMin, vMax],
  ]
  if (corners.some((corner) => pointInTriangle(corner, triangle))) {
    return true
  }

  const triangleEdges: [Point2, Point2][] = [
    [triangle[0], triangle[1]],
    [triangle[1], triangle[2]],
    [triangle[2], triangle[0]],
  ]
  const boxEdges: [Point2, Point2][] = [
    [corners[0], corners[1]],
    [corners[1], corners[2]],
    [corners[2], corners[3]],
    [corners[3], corners[0]],
  ]

  return triangleEdges.some(([start, end]) =>
    boxEdges.some(([boxStart, boxEnd]) =>
      segmentsIntersect(start, end, boxStart, boxEnd),
    ),
  )
}

function unavailableComponentIds(
  hiddenComponentIds: Iterable<number>,
  deletedComponentIds: Iterable<number>,
): Set<number> {
  return new Set([...hiddenComponentIds, ...deletedComponentIds])
}

export function resolveFacesInRoiBox(
  scene: ScenePayload,
  box: RoiClipBox,
  hiddenComponentIds: Iterable<number>,
  deletedComponentIds: Iterable<number> = [],
): number[] {
  const unavailable = unavailableComponentIds(
    hiddenComponentIds,
    deletedComponentIds,
  )
  const faceIds: number[] = []
  const plane = roiPlane(box)

  scene.mesh.faces.forEach((face, faceId) => {
    const componentId = scene.mesh.face_component_ids[faceId]
    if (componentId === null || unavailable.has(componentId)) return

    if (plane === 'xyz') {
      const triangle = face.map((vertexId) => [
        ...scene.mesh.vertices[vertexId],
      ]) as Point3[]
      if (triangleIntersectsRoiVolume(triangle, box)) {
        faceIds.push(faceId)
      }
      return
    }

    const triangle: Point2[] = face.map((vertexId) => {
      const vertex = scene.mesh.vertices[vertexId]
      return projectVertexToRoiPlane(vertex, plane)
    })
    if (triangleIntersectsRoiBox(triangle, box)) {
      faceIds.push(faceId)
    }
  })

  return faceIds
}

function toVector3Value(
  value: readonly [number, number, number],
): Vector3Value {
  return { x: value[0], y: value[1], z: value[2] }
}

export function groupRoiFacesByComponent(
  scene: ScenePayload,
  faceIds: Iterable<number>,
  componentNameOverrides: Record<number, string> = {},
): RoiComponentClip[] {
  const componentById = new Map(
    scene.components.map((component) => [
      component.component_id,
      component,
    ]),
  )
  const groups = new Map<
    number,
    {
      faceIds: number[]
      areaMm2: number
      min: [number, number, number]
      max: [number, number, number]
    }
  >()

  for (const faceId of [...new Set(faceIds)].sort(
    (left, right) => left - right,
  )) {
    if (!Number.isSafeInteger(faceId) || faceId < 0) continue
    const face = scene.mesh.faces[faceId]
    const componentId = scene.mesh.face_component_ids[faceId]
    if (!face || componentId === null) continue

    let group = groups.get(componentId)
    if (!group) {
      group = {
        faceIds: [],
        areaMm2: 0,
        min: [Infinity, Infinity, Infinity],
        max: [-Infinity, -Infinity, -Infinity],
      }
      groups.set(componentId, group)
    }

    group.faceIds.push(faceId)
    group.areaMm2 += scene.mesh.face_areas_mm2[faceId] ?? 0
    for (const vertexId of face) {
      const vertex = scene.mesh.vertices[vertexId]
      for (let axis = 0; axis < 3; axis += 1) {
        group.min[axis] = Math.min(group.min[axis], vertex[axis])
        group.max[axis] = Math.max(group.max[axis], vertex[axis])
      }
    }
  }

  return [...groups.entries()]
    .sort(([left], [right]) => left - right)
    .map(([componentId, group]) => {
      const component = componentById.get(componentId)
      return {
        componentId,
        componentName:
          componentNameOverrides[componentId] ??
          component?.component_name ??
          component?.object_name ??
          `Component ${componentId}`,
        faceIds: group.faceIds,
        areaMm2: group.areaMm2,
        bboxMin: toVector3Value(group.min),
        bboxMax: toVector3Value(group.max),
      }
    })
}

export function resolveNearestVisibleFace(
  scene: ScenePayload,
  point: Vector3Value,
  hiddenComponentIds: Iterable<number>,
  deletedComponentIds: Iterable<number> = [],
): number | null {
  const unavailable = unavailableComponentIds(
    hiddenComponentIds,
    deletedComponentIds,
  )
  let bestFaceId: number | null = null
  let bestDistanceSquared = Infinity

  scene.mesh.face_centroids.forEach((centroid, faceId) => {
    const componentId = scene.mesh.face_component_ids[faceId]
    if (componentId === null || unavailable.has(componentId)) return
    const dx = centroid[0] - point.x
    const dy = centroid[1] - point.y
    const dz = centroid[2] - point.z
    const distanceSquared = dx * dx + dy * dy + dz * dz
    if (distanceSquared < bestDistanceSquared) {
      bestDistanceSquared = distanceSquared
      bestFaceId = faceId
    }
  })

  return bestFaceId
}

export function getActiveRoiFaceIds(
  scopes: RoiScope[],
  deletedComponentIds: Iterable<number> = [],
): number[] {
  const deleted = new Set(deletedComponentIds)
  return [
    ...new Set(
      scopes
        .filter((scope) => scope.active)
        .flatMap((scope) =>
          scope.components
            .filter(
              (component) => !deleted.has(component.componentId),
            )
            .flatMap((component) => component.faceIds),
        ),
    ),
  ].sort((left, right) => left - right)
}

export interface RoiSelectionSummary {
  scopeCount: number
  faceCount: number
  componentCount: number
  areaMm2: number
  bboxMin: Vector3Value | null
  bboxMax: Vector3Value | null
}

export function summarizeActiveRoiScopes(
  scopes: RoiScope[],
): RoiSelectionSummary {
  const activeScopes = scopes.filter((scope) => scope.active)
  const components = activeScopes.flatMap((scope) => scope.components)
  const bboxMin = { x: Infinity, y: Infinity, z: Infinity }
  const bboxMax = { x: -Infinity, y: -Infinity, z: -Infinity }

  for (const component of components) {
    bboxMin.x = Math.min(bboxMin.x, component.bboxMin.x)
    bboxMin.y = Math.min(bboxMin.y, component.bboxMin.y)
    bboxMin.z = Math.min(bboxMin.z, component.bboxMin.z)
    bboxMax.x = Math.max(bboxMax.x, component.bboxMax.x)
    bboxMax.y = Math.max(bboxMax.y, component.bboxMax.y)
    bboxMax.z = Math.max(bboxMax.z, component.bboxMax.z)
  }

  return {
    scopeCount: activeScopes.length,
    faceCount: getActiveRoiFaceIds(activeScopes).length,
    componentCount: new Set(
      components.map((component) => component.componentId),
    ).size,
    areaMm2: activeScopes.reduce(
      (sum, scope) =>
        sum +
        scope.components.reduce(
          (scopeSum, component) =>
            scopeSum + component.areaMm2,
          0,
        ),
      0,
    ),
    bboxMin: components.length > 0 ? bboxMin : null,
    bboxMax: components.length > 0 ? bboxMax : null,
  }
}
