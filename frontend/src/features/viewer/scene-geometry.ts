import {
  BufferGeometry,
  Float32BufferAttribute,
  Vector3,
} from 'three'

import type {
  SceneComponent,
  SceneFeatureEdgeSegment,
  ScenePayload,
} from '@/api'

export interface ViewerGeometryBundle {
  center: Vector3
  faceIds: number[]
  geometry: BufferGeometry
}

function componentCenter(component: SceneComponent): Vector3 {
  return new Vector3(
    (component.bbox_min[0] + component.bbox_max[0]) / 2,
    (component.bbox_min[1] + component.bbox_max[1]) / 2,
    (component.bbox_min[2] + component.bbox_max[2]) / 2,
  )
}

export function createFaceGeometry(
  scene: ScenePayload,
  faceIds: Iterable<number>,
  center = new Vector3(),
): ViewerGeometryBundle {
  const requestedFaceIds = [...faceIds]
  const positions: number[] = []
  const normals: number[] = []
  const includedFaceIds: number[] = []
  const smoothNormals = new Map<string, Vector3>()
  const normalKeysByFace = new Map<number, string[]>()

  // Smooth only inside each original CAD/B-rep face. Triangles belonging
  // to the same curved surface share averaged vertex normals, while a real
  // CAD edge (different source face id) remains sharp like NX shaded mode.
  for (const faceId of requestedFaceIds) {
    const face = scene.mesh.faces[faceId]
    const faceNormal = scene.mesh.face_normals[faceId]
    if (!face || !faceNormal) continue
    const triangleVertices = face.map(
      (vertexId) => scene.mesh.vertices[vertexId],
    )
    const areaWeightedNormal =
      triangleVertices.length === 3 &&
      triangleVertices.every((vertex) => vertex !== undefined)
        ? new Vector3()
            .subVectors(
              new Vector3(...triangleVertices[1]),
              new Vector3(...triangleVertices[0]),
            )
            .cross(
              new Vector3().subVectors(
                new Vector3(...triangleVertices[2]),
                new Vector3(...triangleVertices[0]),
              ),
            )
        : new Vector3(...faceNormal)
    const sourceFaceId = scene.mesh.face_source_ids?.[faceId] ?? faceId
    const keys: string[] = []
    for (const vertexId of face) {
      const vertex = scene.mesh.vertices[vertexId]
      if (!vertex) continue
      const key = `${sourceFaceId}:${vertex[0].toFixed(6)},${vertex[1].toFixed(6)},${vertex[2].toFixed(6)}`
      keys.push(key)
      const sum = smoothNormals.get(key) ?? new Vector3()
      // Area weighting prevents a cluster of tiny tessellation triangles
      // from skewing the visual normal on compound R-surfaces.
      sum.add(areaWeightedNormal)
      smoothNormals.set(key, sum)
    }
    normalKeysByFace.set(faceId, keys)
  }

  for (const faceId of requestedFaceIds) {
    const face = scene.mesh.faces[faceId]
    if (!face) continue

    const vertices = face.map((vertexId) => scene.mesh.vertices[vertexId])
    if (vertices.some((vertex) => vertex === undefined)) continue

    const normal = scene.mesh.face_normals[faceId]
    const normalKeys = normalKeysByFace.get(faceId)

    for (const vertex of vertices) {
      if (!vertex) continue
      positions.push(
        vertex[0] - center.x,
        vertex[1] - center.y,
        vertex[2] - center.z,
      )
      const key = normalKeys?.shift()
      const smoothNormal = key ? smoothNormals.get(key) : undefined
      if (smoothNormal && smoothNormal.lengthSq() > 1e-12) {
        smoothNormal.normalize()
        normals.push(smoothNormal.x, smoothNormal.y, smoothNormal.z)
      } else if (normal) {
        normals.push(normal[0], normal[1], normal[2])
      }
    }
    includedFaceIds.push(faceId)
  }

  const geometry = new BufferGeometry()
  geometry.setAttribute(
    'position',
    new Float32BufferAttribute(positions, 3),
  )

  if (normals.length === positions.length) {
    geometry.setAttribute(
      'normal',
      new Float32BufferAttribute(normals, 3),
    )
  } else {
    geometry.computeVertexNormals()
  }

  geometry.computeBoundingBox()
  geometry.computeBoundingSphere()
  geometry.userData.sourceFaceIds = includedFaceIds

  return {
    center: center.clone(),
    faceIds: includedFaceIds,
    geometry,
  }
}

export function createComponentGeometry(
  scene: ScenePayload,
  component: SceneComponent,
): ViewerGeometryBundle {
  const center = componentCenter(component)
  return createFaceGeometry(scene, component.face_indices, center)
}

export function createFeatureEdgeGeometry(
  segments: Iterable<SceneFeatureEdgeSegment>,
  center = new Vector3(),
): BufferGeometry {
  const positions: number[] = []

  for (const segment of segments) {
    positions.push(
      segment.start[0] - center.x,
      segment.start[1] - center.y,
      segment.start[2] - center.z,
      segment.end[0] - center.x,
      segment.end[1] - center.y,
      segment.end[2] - center.z,
    )
  }

  const geometry = new BufferGeometry()
  geometry.setAttribute(
    'position',
    new Float32BufferAttribute(positions, 3),
  )
  geometry.computeBoundingBox()
  geometry.computeBoundingSphere()
  return geometry
}

export function findCoplanarFacePatch(
  scene: ScenePayload,
  componentFaceIds: Iterable<number>,
  seedFaceId: number,
): number[] {
  const seedFace = scene.mesh.faces[seedFaceId]
  const seedNormalValues = scene.mesh.face_normals[seedFaceId]
  const seedCentroidValues = scene.mesh.face_centroids[seedFaceId]
  if (!seedFace || !seedNormalValues || !seedCentroidValues) {
    return [seedFaceId]
  }

  const componentFaces = new Set(componentFaceIds)
  if (!componentFaces.has(seedFaceId)) return [seedFaceId]

  const component = scene.components.find((candidate) =>
    candidate.face_indices.includes(seedFaceId),
  )
  const componentDiagonal = component
    ? new Vector3(...component.bbox_max)
        .sub(new Vector3(...component.bbox_min))
        .length()
    : 1
  const planeTolerance = Math.max(componentDiagonal * 1e-5, 1e-4)
  // CAD-to-mesh tessellation commonly triangulates each original B-rep face
  // independently, with its own vertex buffer segment - two triangles from
  // *different* B-rep faces that visually touch along one CAD-drawn surface
  // will usually NOT share a vertex index even though their edges sit at
  // the exact same 3D position. Keying adjacency off quantized vertex
  // *position* (instead of vertex index) bridges those seams, so a click
  // still grows to the whole surface as drawn rather than stopping at the
  // first internal tessellation/B-rep-face boundary.
  const positionTolerance = planeTolerance
  const positionKey = (vertexId: number): string => {
    const vertex = scene.mesh.vertices[vertexId]
    if (!vertex) return `v${vertexId}`
    return `${Math.round(vertex[0] / positionTolerance)}:${Math.round(
      vertex[1] / positionTolerance,
    )}:${Math.round(vertex[2] / positionTolerance)}`
  }
  const edgeFaces = new Map<string, number[]>()
  const edgeKey = (firstVertexId: number, secondVertexId: number) => {
    const first = positionKey(firstVertexId)
    const second = positionKey(secondVertexId)
    return first < second ? `${first}|${second}` : `${second}|${first}`
  }

  for (const faceId of componentFaces) {
    const face = scene.mesh.faces[faceId]
    if (!face) continue
    for (let edge = 0; edge < 3; edge += 1) {
      const key = edgeKey(face[edge], face[(edge + 1) % 3])
      const faceIds = edgeFaces.get(key)
      if (faceIds) faceIds.push(faceId)
      else edgeFaces.set(key, [faceId])
    }
  }

  const seedNormal = new Vector3(...seedNormalValues).normalize()
  const seedCentroid = new Vector3(...seedCentroidValues)
  const normalDotTolerance = Math.cos(Math.PI / 360)
  const selected = new Set<number>([seedFaceId])
  const queue = [seedFaceId]

  while (queue.length > 0) {
    const faceId = queue.pop()
    if (faceId === undefined) break
    const face = scene.mesh.faces[faceId]
    if (!face) continue
    for (let edge = 0; edge < 3; edge += 1) {
      const neighbors =
        edgeFaces.get(edgeKey(face[edge], face[(edge + 1) % 3])) ?? []
      for (const neighborId of neighbors) {
        if (selected.has(neighborId)) continue
        const normalValues = scene.mesh.face_normals[neighborId]
        const centroidValues = scene.mesh.face_centroids[neighborId]
        if (!normalValues || !centroidValues) continue
        const normal = new Vector3(...normalValues).normalize()
        if (normal.dot(seedNormal) < normalDotTolerance) continue
        const planeDistance = Math.abs(
          new Vector3(...centroidValues)
            .sub(seedCentroid)
            .dot(seedNormal),
        )
        if (planeDistance > planeTolerance) continue
        selected.add(neighborId)
        queue.push(neighborId)
      }
    }
  }

  return [...selected].sort((first, second) => first - second)
}

/** Returns every render triangle belonging to the same authored CAD face.
 * Older/non-B-rep payloads fall back to the geometric coplanar patch. */
export function findCadSurfaceFaceIds(
  scene: ScenePayload,
  componentFaces: number[],
  seedFaceId: number,
): number[] {
  const sourceIds = scene.mesh.face_source_ids
  const sourceId = sourceIds?.[seedFaceId]
  if (sourceIds && sourceId !== undefined) {
    return componentFaces.filter((faceId) => sourceIds[faceId] === sourceId)
  }
  return findCoplanarFacePatch(scene, componentFaces, seedFaceId)
}

export function getSceneBounds(scene: ScenePayload): {
  center: Vector3
  size: Vector3
} {
  const minimum = new Vector3(
    Number.POSITIVE_INFINITY,
    Number.POSITIVE_INFINITY,
    Number.POSITIVE_INFINITY,
  )
  const maximum = new Vector3(
    Number.NEGATIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
  )

  for (const vertex of scene.mesh.vertices) {
    minimum.min(new Vector3(vertex[0], vertex[1], vertex[2]))
    maximum.max(new Vector3(vertex[0], vertex[1], vertex[2]))
  }

  if (!Number.isFinite(minimum.x) || !Number.isFinite(maximum.x)) {
    return {
      center: new Vector3(),
      size: new Vector3(1, 1, 1),
    }
  }

  return {
    center: minimum.clone().add(maximum).multiplyScalar(0.5),
    size: maximum.clone().sub(minimum),
  }
}
