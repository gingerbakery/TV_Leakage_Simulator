import {
  BufferGeometry,
  Float32BufferAttribute,
  ShapeUtils,
  Vector2,
  Vector3,
} from 'three'

import type { ScenePayload } from '@/api'
import type {
  RoiClipBox,
  RoiProjectionPlane,
} from '@/stores'

type Point3 = [number, number, number]
export type RoiComponentPointTransform = (
  componentId: number,
  point: Readonly<Point3>,
) => Point3
type AxisIndex = 0 | 1 | 2
type AxisName = 'x' | 'y' | 'z'
type PlaneName =
  | 'xMin'
  | 'xMax'
  | 'yMin'
  | 'yMax'
  | 'zMin'
  | 'zMax'

interface TriangleRecord {
  a: number
  b: number
  c: number
  boxIndex: number
  componentId: number
}

interface BoundaryEdge {
  a: number
  b: number
  count: number
  boxIndex: number
  componentId: number
}

interface PlaneEdgeGroup {
  planeName: PlaneName
  boxIndex: number
  componentId: number
  edges: BoundaryEdge[]
}

export interface RoiClippedGeometryBundle {
  surfaceGeometry: BufferGeometry
  capGeometry: BufferGeometry | null
  capEdgeGeometry: BufferGeometry | null
  featureEdgeGeometry: BufferGeometry | null
  capLoopCount: number
  openChainCount: number
  clippedTriangleCount: number
  clippedVertexCount: number
}

function roiPlane(box: RoiClipBox): RoiProjectionPlane {
  return box.plane ?? 'xy'
}

function planeAxes(plane: RoiProjectionPlane): [AxisIndex, AxisIndex] {
  if (plane === 'yz') return [1, 2]
  if (plane === 'zx') return [2, 0]
  return [0, 1]
}

function axisName(axisIndex: AxisIndex): AxisName {
  return (['x', 'y', 'z'] as const)[axisIndex]
}

function axisBounds(
  box: RoiClipBox,
  axisIndex: AxisIndex,
): [number, number] {
  if (axisIndex === 0) return [box.xMin, box.xMax]
  if (axisIndex === 1) return [box.yMin, box.yMax]
  return [box.zMin ?? 0, box.zMax ?? 0]
}

function planeAxisIndex(planeName: PlaneName): AxisIndex {
  if (planeName.startsWith('x')) return 0
  if (planeName.startsWith('y')) return 1
  return 2
}

function planeBoundary(
  box: RoiClipBox,
  planeName: PlaneName,
): number {
  const [minimum, maximum] = axisBounds(
    box,
    planeAxisIndex(planeName),
  )
  return planeName.endsWith('Min') ? minimum : maximum
}

function clipPlaneNames(box: RoiClipBox): PlaneName[] {
  return planeAxes(roiPlane(box)).flatMap((axisIndex) => {
    const axis = axisName(axisIndex)
    return [`${axis}Min`, `${axis}Max`] as PlaneName[]
  })
}

export function normalizeRoiClipBoxes(
  boxes: RoiClipBox[],
): RoiClipBox[] {
  return boxes
    .map((box) => {
      const plane = roiPlane(box)
      return {
        plane,
        xMin: Number(box.xMin),
        xMax: Number(box.xMax),
        yMin: Number(box.yMin),
        yMax: Number(box.yMax),
        zMin:
          box.zMin === undefined ? undefined : Number(box.zMin),
        zMax:
          box.zMax === undefined ? undefined : Number(box.zMax),
      }
    })
    .filter((box) => {
      const [firstAxis, secondAxis] = planeAxes(
        box.plane ?? 'xy',
      )
      return [firstAxis, secondAxis].every((axisIndex) => {
        const [minimum, maximum] = axisBounds(box, axisIndex)
        return (
          Number.isFinite(minimum) &&
          Number.isFinite(maximum) &&
          maximum > minimum
        )
      })
    })
}

function clipPolygonAgainstPlane(
  points: Point3[],
  axisIndex: AxisIndex,
  boundary: number,
  keepGreater: boolean,
): Point3[] {
  if (points.length === 0) return []
  const output: Point3[] = []
  let previous = points[points.length - 1]
  let previousInside = keepGreater
    ? previous[axisIndex] >= boundary - 1e-9
    : previous[axisIndex] <= boundary + 1e-9

  for (const current of points) {
    const currentInside = keepGreater
      ? current[axisIndex] >= boundary - 1e-9
      : current[axisIndex] <= boundary + 1e-9
    if (currentInside !== previousInside) {
      const denominator =
        current[axisIndex] - previous[axisIndex]
      if (Math.abs(denominator) > 1e-12) {
        const t =
          (boundary - previous[axisIndex]) / denominator
        output.push([
          previous[0] + (current[0] - previous[0]) * t,
          previous[1] + (current[1] - previous[1]) * t,
          previous[2] + (current[2] - previous[2]) * t,
        ])
      }
    }
    if (currentInside) output.push(current)
    previous = current
    previousInside = currentInside
  }

  const deduped: Point3[] = []
  for (const point of output) {
    const last = deduped[deduped.length - 1]
    if (
      !last ||
      Math.hypot(
        point[0] - last[0],
        point[1] - last[1],
        point[2] - last[2],
      ) > 1e-8
    ) {
      deduped.push(point)
    }
  }
  if (deduped.length > 2) {
    const first = deduped[0]
    const last = deduped[deduped.length - 1]
    if (
      Math.hypot(
        first[0] - last[0],
        first[1] - last[1],
        first[2] - last[2],
      ) <= 1e-8
    ) {
      deduped.pop()
    }
  }
  return deduped
}

export function clipTriangleToRoiBox(
  points: Point3[],
  box: RoiClipBox,
): Point3[] {
  let polygon = points
  for (const axisIndex of planeAxes(roiPlane(box))) {
    const [minimum, maximum] = axisBounds(box, axisIndex)
    polygon = clipPolygonAgainstPlane(
      polygon,
      axisIndex,
      minimum,
      true,
    )
    polygon = clipPolygonAgainstPlane(
      polygon,
      axisIndex,
      maximum,
      false,
    )
  }
  return polygon
}

function clipVertexKey(
  componentId: number,
  point: Point3,
): string {
  const precision = 1_000_000
  return `${componentId}:${Math.round(
    point[0] * precision,
  )}:${Math.round(point[1] * precision)}:${Math.round(
    point[2] * precision,
  )}`
}

function flatPosition(
  positions: number[],
  vertexIndex: number,
): Vector3 {
  return new Vector3(
    positions[vertexIndex * 3],
    positions[vertexIndex * 3 + 1],
    positions[vertexIndex * 3 + 2],
  )
}

function simplifyClipLoop(
  loop: number[],
  positions: number[],
): number[] {
  const simplified = [...loop]
  let changed = true
  while (changed && simplified.length > 3) {
    changed = false
    for (let index = 0; index < simplified.length; index += 1) {
      const previous = flatPosition(
        positions,
        simplified[
          (index - 1 + simplified.length) % simplified.length
        ],
      )
      const current = flatPosition(positions, simplified[index])
      const next = flatPosition(
        positions,
        simplified[(index + 1) % simplified.length],
      )
      const first = current.clone().sub(previous)
      const second = next.clone().sub(current)
      const scale = Math.max(
        first.length() * second.length(),
        1e-12,
      )
      if (
        first.dot(second) >= 0 &&
        first.clone().cross(second).length() <= scale * 1e-7
      ) {
        simplified.splice(index, 1)
        changed = true
        break
      }
    }
  }
  return simplified
}

function appendClipCap(
  loop: number[],
  surfacePositions: number[],
  capPositions: number[],
  capIndices: number[],
  capEdgePositions: number[],
  planeName: PlaneName,
  outputPointTransform?: (point: Vector3) => Vector3,
): boolean {
  const cleanLoop = simplifyClipLoop(loop, surfacePositions)
  if (cleanLoop.length < 3) return false
  const points = cleanLoop.map((vertexIndex) =>
    flatPosition(surfacePositions, vertexIndex),
  )
  const planeAxis = planeAxisIndex(planeName)
  const contour = points.map((point) => {
    if (planeAxis === 0) return new Vector2(point.y, point.z)
    if (planeAxis === 1) return new Vector2(point.x, point.z)
    return new Vector2(point.x, point.y)
  })
  const triangles = ShapeUtils.triangulateShape(contour, [])
  if (triangles.length === 0) return false
  const outputPoints = outputPointTransform
    ? points.map(outputPointTransform)
    : points
  const baseIndex = capPositions.length / 3
  for (const point of outputPoints) {
    capPositions.push(point.x, point.y, point.z)
  }
  for (let index = 0; index < outputPoints.length; index += 1) {
    const current = outputPoints[index]
    const next = outputPoints[(index + 1) % outputPoints.length]
    capEdgePositions.push(
      current.x,
      current.y,
      current.z,
      next.x,
      next.y,
      next.z,
    )
  }
  for (const triangle of triangles) {
    capIndices.push(
      baseIndex + triangle[0],
      baseIndex + triangle[1],
      baseIndex + triangle[2],
    )
  }
  return true
}

function splitCapEdgesAtTJunctions(
  edges: BoundaryEdge[],
  positions: number[],
  tolerance: number,
): BoundaryEdge[] {
  const vertexIds = [
    ...new Set(edges.flatMap((edge) => [edge.a, edge.b])),
  ]
  if (edges.length * vertexIds.length > 6_000_000) return edges
  const segments = new Map<string, BoundaryEdge>()

  for (const edge of edges) {
    const first = flatPosition(positions, edge.a)
    const second = flatPosition(positions, edge.b)
    const direction = second.clone().sub(first)
    const lengthSquared = direction.lengthSq()
    if (lengthSquared < 1e-18) continue
    const cuts = [
      { t: 0, vertexId: edge.a },
      { t: 1, vertexId: edge.b },
    ]
    for (const vertexId of vertexIds) {
      if (vertexId === edge.a || vertexId === edge.b) continue
      const point = flatPosition(positions, vertexId)
      const t =
        point.clone().sub(first).dot(direction) / lengthSquared
      if (t <= 1e-8 || t >= 1 - 1e-8) continue
      const projected = first
        .clone()
        .add(direction.clone().multiplyScalar(t))
      if (projected.distanceTo(point) <= tolerance) {
        cuts.push({ t, vertexId })
      }
    }
    cuts.sort((left, right) => left.t - right.t)
    for (let index = 0; index < cuts.length - 1; index += 1) {
      const a = cuts[index].vertexId
      const b = cuts[index + 1].vertexId
      if (a === b) continue
      const low = Math.min(a, b)
      const high = Math.max(a, b)
      segments.set(`${low}:${high}`, {
        ...edge,
        a: low,
        b: high,
      })
    }
  }
  return [...segments.values()]
}

function closeCapEdgeChains(
  edges: BoundaryEdge[],
  positions: number[],
  planeName: PlaneName,
  box: RoiClipBox,
  tolerance: number,
): BoundaryEdge[] {
  const adjacency = new Map<number, number[]>()
  const edgeKeys = new Set<string>()
  for (const edge of edges) {
    const aNeighbors = adjacency.get(edge.a) ?? []
    aNeighbors.push(edge.b)
    adjacency.set(edge.a, aNeighbors)
    const bNeighbors = adjacency.get(edge.b) ?? []
    bNeighbors.push(edge.a)
    adjacency.set(edge.b, bNeighbors)
    edgeKeys.add(
      `${Math.min(edge.a, edge.b)}:${Math.max(edge.a, edge.b)}`,
    )
  }
  const endpoints = [...adjacency.entries()]
    .filter(([, neighbors]) => neighbors.length === 1)
    .map(([vertexId]) => vertexId)
  if (endpoints.length === 0) return edges

  const boundaryGroups = new Map<PlaneName, number[]>()
  const remaining = new Set(endpoints)
  const primaryAxis = planeAxisIndex(planeName)
  const companionPlanes = clipPlaneNames(box).filter(
    (candidate) => planeAxisIndex(candidate) !== primaryAxis,
  )
  for (const vertexId of endpoints) {
    const point = flatPosition(positions, vertexId)
    const boundaryName =
      companionPlanes.find(
        (candidate) =>
          Math.abs(
            point.getComponent(planeAxisIndex(candidate)) -
              planeBoundary(box, candidate),
          ) <= tolerance,
      ) ?? null
    if (!boundaryName) continue
    const members = boundaryGroups.get(boundaryName) ?? []
    members.push(vertexId)
    boundaryGroups.set(boundaryName, members)
  }

  const connectors: BoundaryEdge[] = []
  const connectPairs = (vertexIds: number[]) => {
    const ordered = [...vertexIds].sort(
      (left, right) =>
        flatPosition(positions, left).z -
        flatPosition(positions, right).z,
    )
    while (ordered.length >= 2) {
      const first = ordered.shift()
      if (first === undefined) break
      let bestIndex = 0
      let bestDistance = Infinity
      const firstPoint = flatPosition(positions, first)
      for (let index = 0; index < ordered.length; index += 1) {
        const distance = firstPoint.distanceTo(
          flatPosition(positions, ordered[index]),
        )
        if (distance < bestDistance) {
          bestDistance = distance
          bestIndex = index
        }
      }
      const [second] = ordered.splice(bestIndex, 1)
      if (second === undefined) break
      const low = Math.min(first, second)
      const high = Math.max(first, second)
      const key = `${low}:${high}`
      if (!edgeKeys.has(key) && first !== second) {
        edgeKeys.add(key)
        connectors.push({
          ...edges[0],
          a: low,
          b: high,
          count: 1,
        })
      }
      remaining.delete(first)
      remaining.delete(second)
    }
  }

  for (const vertexIds of boundaryGroups.values()) {
    connectPairs(vertexIds)
  }
  if (remaining.size >= 2) connectPairs([...remaining])
  return [...edges, ...connectors]
}

function clipFeatureSegment(
  start: Point3,
  end: Point3,
  box: RoiClipBox,
): [Point3, Point3] | null {
  const delta: Point3 = [
    end[0] - start[0],
    end[1] - start[1],
    end[2] - start[2],
  ]
  let enter = 0
  let leave = 1
  const constraints = planeAxes(roiPlane(box)).flatMap(
    (axisIndex) => {
      const [minimum, maximum] = axisBounds(box, axisIndex)
      return [
        [-delta[axisIndex], start[axisIndex] - minimum],
        [delta[axisIndex], maximum - start[axisIndex]],
      ]
    },
  )
  for (const [direction, distance] of constraints) {
    if (Math.abs(direction) < 1e-12) {
      if (distance < 0) return null
      continue
    }
    const ratio = distance / direction
    if (direction < 0) enter = Math.max(enter, ratio)
    else leave = Math.min(leave, ratio)
    if (enter > leave) return null
  }
  return [
    [
      start[0] + delta[0] * enter,
      start[1] + delta[1] * enter,
      start[2] + delta[2] * enter,
    ],
    [
      start[0] + delta[0] * leave,
      start[1] + delta[1] * leave,
      start[2] + delta[2] * leave,
    ],
  ]
}

function buildFeatureEdgeGeometry(
  scene: ScenePayload,
  boxes: RoiClipBox[],
  unavailableComponentIds: Set<number>,
  transformPoint?: RoiComponentPointTransform,
): BufferGeometry | null {
  const positions: number[] = []
  for (const segment of scene.mesh.feature_edge_segments) {
    if (
      segment.component_id === null ||
      unavailableComponentIds.has(segment.component_id)
    ) {
      continue
    }
    const componentId = segment.component_id
    const start = [...segment.start] as Point3
    const end = [...segment.end] as Point3
    for (const box of boxes) {
      const clipped = clipFeatureSegment(
        start,
        end,
        box,
      )
      if (!clipped) continue
      const outputStart = transformPoint
        ? transformPoint(componentId, clipped[0])
        : clipped[0]
      const outputEnd = transformPoint
        ? transformPoint(componentId, clipped[1])
        : clipped[1]
      positions.push(...outputStart, ...outputEnd)
    }
  }
  if (positions.length === 0) return null
  const geometry = new BufferGeometry()
  geometry.setAttribute(
    'position',
    new Float32BufferAttribute(positions, 3),
  )
  return geometry
}

export function buildRoiClippedGeometries(
  scene: ScenePayload,
  faceFilter: number[],
  boxes: RoiClipBox[],
  unavailableComponentIds: Iterable<number> = [],
  transformPoint?: RoiComponentPointTransform,
): RoiClippedGeometryBundle | null {
  const clipBoxes = normalizeRoiClipBoxes(boxes)
  if (clipBoxes.length === 0 || faceFilter.length === 0) return null
  const unavailable = new Set(unavailableComponentIds)
  const positions: number[] = []
  const indices: number[] = []
  const sourceFaceIds: number[] = []
  const triangleRecords: TriangleRecord[] = []
  const vertexMaps = clipBoxes.map(() => new Map<string, number>())
  const vertexComponentIds: number[] = []

  const addVertex = (
    boxIndex: number,
    componentId: number,
    point: Point3,
  ) => {
    const key = clipVertexKey(componentId, point)
    const vertexMap = vertexMaps[boxIndex]
    const existing = vertexMap.get(key)
    if (existing !== undefined) return existing
    const vertexIndex = positions.length / 3
    positions.push(...point)
    vertexComponentIds.push(componentId)
    vertexMap.set(key, vertexIndex)
    return vertexIndex
  }

  for (const faceId of faceFilter) {
    const triangle = scene.mesh.faces[faceId]
    const componentId =
      scene.mesh.face_component_ids[faceId] ?? -1
    if (
      !triangle ||
      componentId < 0 ||
      unavailable.has(componentId)
    ) {
      continue
    }
    const trianglePoints = triangle.map(
      (vertexIndex) =>
        [...scene.mesh.vertices[vertexIndex]] as Point3,
    )
    for (
      let boxIndex = 0;
      boxIndex < clipBoxes.length;
      boxIndex += 1
    ) {
      const polygon = clipTriangleToRoiBox(
        trianglePoints,
        clipBoxes[boxIndex],
      )
      if (polygon.length < 3) continue
      const polygonIndices = polygon.map((point) =>
        addVertex(boxIndex, componentId, point),
      )
      for (
        let index = 1;
        index < polygonIndices.length - 1;
        index += 1
      ) {
        const a = polygonIndices[0]
        const b = polygonIndices[index]
        const c = polygonIndices[index + 1]
        const pa = flatPosition(positions, a)
        const pb = flatPosition(positions, b)
        const pc = flatPosition(positions, c)
        if (
          pb
            .clone()
            .sub(pa)
            .cross(pc.clone().sub(pa))
            .lengthSq() < 1e-16
        ) {
          continue
        }
        indices.push(a, b, c)
        sourceFaceIds.push(faceId)
        triangleRecords.push({
          a,
          b,
          c,
          boxIndex,
          componentId,
        })
      }
    }
  }
  if (indices.length === 0) return null

  const outputPositions: number[] = []
  for (
    let coordinateIndex = 0;
    coordinateIndex < positions.length;
    coordinateIndex += 3
  ) {
    const vertexIndex = coordinateIndex / 3
    const point = [
      positions[coordinateIndex],
      positions[coordinateIndex + 1],
      positions[coordinateIndex + 2],
    ] as Point3
    outputPositions.push(
      ...(transformPoint
        ? transformPoint(vertexComponentIds[vertexIndex], point)
        : point),
    )
  }
  const indexedSurfaceGeometry = new BufferGeometry()
  indexedSurfaceGeometry.setAttribute(
    'position',
    new Float32BufferAttribute(outputPositions, 3),
  )
  indexedSurfaceGeometry.setIndex(indices)
  // Clipping shares position vertices to build watertight section caps.
  // The rendered surface must not share normals across separate CAD
  // triangles: averaging them across hard edges creates scalloped bands
  // when a lit material is assigned.
  const surfaceGeometry = indexedSurfaceGeometry.toNonIndexed()
  indexedSurfaceGeometry.dispose()
  surfaceGeometry.computeVertexNormals()
  surfaceGeometry.computeBoundingBox()
  surfaceGeometry.computeBoundingSphere()
  surfaceGeometry.userData.sourceFaceIds = sourceFaceIds
  surfaceGeometry.userData.componentIds = triangleRecords.map(
    (triangle) => triangle.componentId,
  )

  const edgeRecords = new Map<string, BoundaryEdge>()
  for (const triangle of triangleRecords) {
    for (const [a, b] of [
      [triangle.a, triangle.b],
      [triangle.b, triangle.c],
      [triangle.c, triangle.a],
    ]) {
      const low = Math.min(a, b)
      const high = Math.max(a, b)
      const key = `${triangle.boxIndex}:${triangle.componentId}:${low}:${high}`
      const record = edgeRecords.get(key) ?? {
        a: low,
        b: high,
        count: 0,
        boxIndex: triangle.boxIndex,
        componentId: triangle.componentId,
      }
      record.count += 1
      edgeRecords.set(key, record)
    }
  }

  const planeGroups = new Map<string, PlaneEdgeGroup>()
  for (const edge of edgeRecords.values()) {
    if (edge.count !== 1) continue
    const box = clipBoxes[edge.boxIndex]
    const first = flatPosition(positions, edge.a)
    const second = flatPosition(positions, edge.b)
    const activeAxes = planeAxes(roiPlane(box))
    const tolerance =
      Math.max(
        ...activeAxes.map((axisIndex) => {
          const [minimum, maximum] = axisBounds(box, axisIndex)
          return maximum - minimum
        }),
        1,
      ) * 1e-6
    const planeName =
      clipPlaneNames(box).find((candidate) => {
        const axisIndex = planeAxisIndex(candidate)
        const boundary = planeBoundary(box, candidate)
        return (
          Math.abs(first.getComponent(axisIndex) - boundary) <=
            tolerance &&
          Math.abs(second.getComponent(axisIndex) - boundary) <=
            tolerance
        )
      }) ?? null
    if (!planeName) continue
    const key = `${edge.boxIndex}:${edge.componentId}:${planeName}`
    const group = planeGroups.get(key) ?? {
      planeName,
      boxIndex: edge.boxIndex,
      componentId: edge.componentId,
      edges: [],
    }
    group.edges.push(edge)
    planeGroups.set(key, group)
  }

  const capPositions: number[] = []
  const capIndices: number[] = []
  const capComponentIds: number[] = []
  const capEdgePositions: number[] = []
  let capLoopCount = 0
  let openChainCount = 0
  for (const group of planeGroups.values()) {
    const groupSpan = group.edges.reduce((maxLength, edge) => {
      const first = flatPosition(positions, edge.a)
      const second = flatPosition(positions, edge.b)
      return Math.max(maxLength, first.distanceTo(second))
    }, 1)
    const tolerance = Math.max(groupSpan * 1e-7, 1e-6)
    const splitEdges = splitCapEdgesAtTJunctions(
      group.edges,
      positions,
      tolerance,
    )
    const edges = closeCapEdgeChains(
      splitEdges,
      positions,
      group.planeName,
      clipBoxes[group.boxIndex],
      tolerance,
    )
    const adjacency = new Map<number, number[]>()
    edges.forEach((edge, edgeIndex) => {
      const aEdges = adjacency.get(edge.a) ?? []
      aEdges.push(edgeIndex)
      adjacency.set(edge.a, aEdges)
      const bEdges = adjacency.get(edge.b) ?? []
      bEdges.push(edgeIndex)
      adjacency.set(edge.b, bEdges)
    })

    const unused = new Set(edges.map((_, index) => index))
    while (unused.size > 0) {
      const firstEdgeIndex = unused.values().next().value
      if (firstEdgeIndex === undefined) break
      const firstEdge = edges[firstEdgeIndex]
      unused.delete(firstEdgeIndex)
      const loop = [firstEdge.a, firstEdge.b]
      let currentVertex = firstEdge.b
      let closed = false
      let guard = 0
      while (guard < edges.length + 2) {
        guard += 1
        if (currentVertex === loop[0]) {
          closed = true
          break
        }
        const nextEdgeIndex = (
          adjacency.get(currentVertex) ?? []
        ).find((edgeIndex) => unused.has(edgeIndex))
        if (nextEdgeIndex === undefined) break
        unused.delete(nextEdgeIndex)
        const nextEdge = edges[nextEdgeIndex]
        currentVertex =
          nextEdge.a === currentVertex ? nextEdge.b : nextEdge.a
        loop.push(currentVertex)
      }
      if (!closed || loop.length < 4) {
        openChainCount += 1
        continue
      }
      loop.pop()
      const capTriangleCountBefore = capIndices.length / 3
      if (
        appendClipCap(
          loop,
          positions,
          capPositions,
          capIndices,
          capEdgePositions,
          group.planeName,
          transformPoint
            ? (point) => {
                const transformed = transformPoint(
                  group.componentId,
                  [point.x, point.y, point.z],
                )
                return new Vector3(...transformed)
              }
            : undefined,
        )
      ) {
        const capTriangleCountAfter = capIndices.length / 3
        capComponentIds.push(
          ...Array.from(
            {
              length:
                capTriangleCountAfter - capTriangleCountBefore,
            },
            () => group.componentId,
          ),
        )
        capLoopCount += 1
      }
    }
  }

  let capGeometry: BufferGeometry | null = null
  if (capIndices.length > 0) {
    capGeometry = new BufferGeometry()
    capGeometry.setAttribute(
      'position',
      new Float32BufferAttribute(capPositions, 3),
    )
    capGeometry.setIndex(capIndices)
    capGeometry.computeVertexNormals()
    capGeometry.computeBoundingBox()
    capGeometry.computeBoundingSphere()
    capGeometry.userData.componentIds = capComponentIds
    capGeometry.userData.roiCapLoopCount = capLoopCount
    capGeometry.userData.roiCapOpenChainCount = openChainCount
  }

  let capEdgeGeometry: BufferGeometry | null = null
  if (capEdgePositions.length > 0) {
    capEdgeGeometry = new BufferGeometry()
    capEdgeGeometry.setAttribute(
      'position',
      new Float32BufferAttribute(capEdgePositions, 3),
    )
  }

  return {
    surfaceGeometry,
    capGeometry,
    capEdgeGeometry,
    featureEdgeGeometry: buildFeatureEdgeGeometry(
      scene,
      clipBoxes,
      unavailable,
      transformPoint,
    ),
    capLoopCount,
    openChainCount,
    clippedTriangleCount: indices.length / 3,
    clippedVertexCount: positions.length / 3,
  }
}
