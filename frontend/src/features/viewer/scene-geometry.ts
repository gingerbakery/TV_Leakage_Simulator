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
  const positions: number[] = []
  const normals: number[] = []
  const includedFaceIds: number[] = []
  let hasCompleteNormals = true

  for (const faceId of faceIds) {
    const face = scene.mesh.faces[faceId]
    if (!face) continue

    const vertices = face.map((vertexId) => scene.mesh.vertices[vertexId])
    if (vertices.some((vertex) => vertex === undefined)) continue

    const normal = scene.mesh.face_normals[faceId]
    if (!normal) hasCompleteNormals = false

    for (const vertex of vertices) {
      if (!vertex) continue
      positions.push(
        vertex[0] - center.x,
        vertex[1] - center.y,
        vertex[2] - center.z,
      )
      if (normal) normals.push(normal[0], normal[1], normal[2])
    }
    includedFaceIds.push(faceId)
  }

  const geometry = new BufferGeometry()
  geometry.setAttribute(
    'position',
    new Float32BufferAttribute(positions, 3),
  )

  if (hasCompleteNormals && normals.length === positions.length) {
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
  const edgeFaces = new Map<string, number[]>()
  const edgeKey = (first: number, second: number) =>
    first < second ? `${first}:${second}` : `${second}:${first}`

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
  const component = scene.components.find((candidate) =>
    candidate.face_indices.includes(seedFaceId),
  )
  const componentDiagonal = component
    ? new Vector3(...component.bbox_max)
        .sub(new Vector3(...component.bbox_min))
        .length()
    : 1
  const planeTolerance = Math.max(componentDiagonal * 1e-5, 1e-4)
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
