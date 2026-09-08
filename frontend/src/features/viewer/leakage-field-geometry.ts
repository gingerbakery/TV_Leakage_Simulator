import {
  BufferGeometry,
  Float32BufferAttribute,
  Vector3,
} from 'three'

import type { Vec3 } from '@/api'
import type { LeakageAabbFace } from '@/features/results/leakage-preview-data'

export interface LeakageFieldNode {
  key: string
  face: LeakageAabbFace
  position: Vec3
  direction: Vec3
  displayStrength: number
}

export interface LeakageFieldEdge {
  from: string
  to: string
}

export interface LeakageFieldOptions {
  /** Diameter of one sample-supported halo in millimetres. */
  diameterMm: number
  /** Soft width of a verified short connection in millimetres. */
  ribbonWidthMm: number
  /** Small outward displacement that prevents z-fighting with the CAD skin. */
  surfaceOffsetMm: number
}

export interface LeakageFieldGeometry {
  geometry: BufferGeometry
  edgeCount: number
  focus: Vec3 | null
  sampleCount: number
}

const quadCorners = [
  [-1, -1],
  [1, -1],
  [1, 1],
  [-1, -1],
  [1, 1],
  [-1, 1],
] as const

function faceFrame(face: LeakageAabbFace): {
  normal: Vec3
  u: Vec3
  v: Vec3
} {
  switch (face) {
    case 'x_min':
      return { normal: [-1, 0, 0], u: [0, 1, 0], v: [0, 0, -1] }
    case 'x_max':
      return { normal: [1, 0, 0], u: [0, 1, 0], v: [0, 0, 1] }
    case 'y_min':
      return { normal: [0, -1, 0], u: [1, 0, 0], v: [0, 0, 1] }
    case 'y_max':
      return { normal: [0, 1, 0], u: [1, 0, 0], v: [0, 0, -1] }
    case 'z_min':
      return { normal: [0, 0, -1], u: [1, 0, 0], v: [0, -1, 0] }
    case 'z_max':
      return { normal: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0] }
  }
}

function isFiniteVec3(value: readonly number[]): value is Vec3 {
  return value.length === 3 && value.every(Number.isFinite)
}

/**
 * Build one face-plane halo per accepted exit sample and ribbons only along
 * verified short edges. The geometry never spans a missing graph edge or
 * fills a hull around the samples.
 */
export function createLeakageFieldGeometry(
  nodes: readonly LeakageFieldNode[],
  edges: readonly LeakageFieldEdge[],
  options: LeakageFieldOptions,
): LeakageFieldGeometry {
  if (!Number.isFinite(options.diameterMm) || options.diameterMm <= 0) {
    throw new RangeError('diameterMm must be finite and positive')
  }
  if (!Number.isFinite(options.ribbonWidthMm) || options.ribbonWidthMm <= 0) {
    throw new RangeError('ribbonWidthMm must be finite and positive')
  }
  if (
    !Number.isFinite(options.surfaceOffsetMm) ||
    options.surfaceOffsetMm < 0
  ) {
    throw new RangeError('surfaceOffsetMm must be finite and nonnegative')
  }

  const positions: number[] = []
  const fieldCoordinates: number[] = []
  const directions: number[] = []
  const strengths: number[] = []
  const fieldKinds: number[] = []
  const focus = new Vector3()
  const halfExtent = options.diameterMm / 2
  const validNodes = new Map<string, LeakageFieldNode>()
  let sampleCount = 0

  for (const node of nodes) {
    if (
      !isFiniteVec3(node.position) ||
      !isFiniteVec3(node.direction) ||
      !Number.isFinite(node.displayStrength) ||
      node.displayStrength <= 0
    ) continue
    const direction = new Vector3(...node.direction)
    if (!Number.isFinite(direction.lengthSq()) || direction.lengthSq() <= 1e-24) {
      continue
    }
    direction.normalize()
    const { normal, u, v } = faceFrame(node.face)
    const center: Vec3 = [
      node.position[0] + normal[0] * options.surfaceOffsetMm,
      node.position[1] + normal[1] * options.surfaceOffsetMm,
      node.position[2] + normal[2] * options.surfaceOffsetMm,
    ]
    for (const [uCoordinate, vCoordinate] of quadCorners) {
      positions.push(
        center[0] + halfExtent * (u[0] * uCoordinate + v[0] * vCoordinate),
        center[1] + halfExtent * (u[1] * uCoordinate + v[1] * vCoordinate),
        center[2] + halfExtent * (u[2] * uCoordinate + v[2] * vCoordinate),
      )
      fieldCoordinates.push(uCoordinate, vCoordinate)
      directions.push(direction.x, direction.y, direction.z)
      strengths.push(Math.min(node.displayStrength, 1))
      fieldKinds.push(0)
    }
    focus.add(new Vector3(...node.position))
    validNodes.set(node.key, node)
    sampleCount += 1
  }
  let edgeCount = 0
  const ribbonHalfWidth = options.ribbonWidthMm / 2
  for (const edge of edges) {
    const from = validNodes.get(edge.from)
    const to = validNodes.get(edge.to)
    if (!from || !to || from.face !== to.face) continue
    const tangent = new Vector3(...to.position).sub(new Vector3(...from.position))
    if (!Number.isFinite(tangent.lengthSq()) || tangent.lengthSq() <= 1e-24) {
      continue
    }
    tangent.normalize()
    const { normal } = faceFrame(from.face)
    const normalVector = new Vector3(...normal)
    const across = normalVector.clone().cross(tangent).normalize()
    const fromDirection = new Vector3(...from.direction).normalize()
    const toDirection = new Vector3(...to.direction).normalize()
    const fromCenter = new Vector3(...from.position)
      .addScaledVector(normalVector, options.surfaceOffsetMm)
    const toCenter = new Vector3(...to.position)
      .addScaledVector(normalVector, options.surfaceOffsetMm)
    const vertices = [
      { center: fromCenter, across: -1, along: 0, node: from },
      { center: toCenter, across: -1, along: 1, node: to },
      { center: toCenter, across: 1, along: 1, node: to },
      { center: fromCenter, across: -1, along: 0, node: from },
      { center: toCenter, across: 1, along: 1, node: to },
      { center: fromCenter, across: 1, along: 0, node: from },
    ] as const
    for (const vertex of vertices) {
      const position = vertex.center
        .clone()
        .addScaledVector(across, ribbonHalfWidth * vertex.across)
      const direction = vertex.node === from ? fromDirection : toDirection
      positions.push(position.x, position.y, position.z)
      fieldCoordinates.push(vertex.along, vertex.across)
      directions.push(direction.x, direction.y, direction.z)
      strengths.push(Math.min(vertex.node.displayStrength, 1))
      fieldKinds.push(1)
    }
    edgeCount += 1
  }

  const geometry = new BufferGeometry()
  geometry.setAttribute(
    'position',
    new Float32BufferAttribute(positions, 3),
  )
  geometry.setAttribute(
    'fieldCoordinate',
    new Float32BufferAttribute(fieldCoordinates, 2),
  )
  geometry.setAttribute(
    'leakDirection',
    new Float32BufferAttribute(directions, 3),
  )
  geometry.setAttribute(
    'leakStrength',
    new Float32BufferAttribute(strengths, 1),
  )
  geometry.setAttribute(
    'fieldKind',
    new Float32BufferAttribute(fieldKinds, 1),
  )
  if (sampleCount > 0) geometry.computeBoundingSphere()

  return {
    geometry,
    edgeCount,
    focus:
      sampleCount > 0
        ? (focus.multiplyScalar(1 / sampleCount).toArray() as Vec3)
        : null,
    sampleCount,
  }
}
