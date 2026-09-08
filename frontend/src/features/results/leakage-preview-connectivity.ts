import type { Vec3 } from '@/api'

import type {
  LeakageAabbFace,
  LeakagePreviewSample,
} from './leakage-preview-data'

export interface LeakageConnectivityOptions {
  /** Maximum on-face distance between samples that may be connected. */
  maxNeighborDistance: number
  /** Minimum cosine similarity for two samples to be connected. */
  minDirectionDot: number
  /** One caller-owned scale shared by every compared Case. */
  commonWeightScale: number
}

export type LeakageConnectivityRejectionReason =
  | 'invalid_weight'
  | 'invalid_point'
  | 'invalid_direction'
  | 'ambiguous_exit_face'
  | 'invalid_exit_face'

export interface LeakageConnectivityNode {
  key: string
  face: LeakageAabbFace
  position: Vec3
  facePosition: [number, number]
  direction: Vec3
  weight: number
  displayStrength: number
  sample: LeakagePreviewSample
}

export interface LeakageConnectivityEdge {
  face: LeakageAabbFace
  from: string
  to: string
  distance: number
}

export interface LeakageConnectivityComponent {
  id: string
  face: LeakageAabbFace
  nodeKeys: string[]
  edgeCount: number
  totalWeight: number
}

export interface LeakageConnectivityRejection {
  key: string
  reason: LeakageConnectivityRejectionReason
  weight: number
  /** The renderer may retain a valid rejected point as an unconnected fallback. */
  sample: LeakagePreviewSample
}

export interface LeakageConnectivityResult {
  nodes: LeakageConnectivityNode[]
  edges: LeakageConnectivityEdge[]
  components: LeakageConnectivityComponent[]
  rejected: LeakageConnectivityRejection[]
  energy: {
    validInputWeight: number
    acceptedWeight: number
    rejectedWeight: number
  }
  commonWeightScale: number
}

export interface PrototypeLeakageFieldSelection {
  connectivity: LeakageConnectivityResult | null
  continuousNodeKeys: ReadonlySet<string>
}

export const prototypeLeakageDisplayEnergyGain = 30000
export const prototypeLeakageMinimumContinuousSamples = 8
export const prototypeLeakageConnectivityOptions = {
  maxNeighborDistance: 1.2,
  minDirectionDot: 0.8,
  commonWeightScale: 1 / prototypeLeakageDisplayEnergyGain,
} as const satisfies LeakageConnectivityOptions

interface CandidateEdge {
  leftIndex: number
  rightIndex: number
  distance: number
}

const faceOrder: readonly LeakageAabbFace[] = [
  'x_min',
  'x_max',
  'y_min',
  'y_max',
  'z_min',
  'z_max',
]

const faceAxes: Record<LeakageAabbFace, readonly [0 | 1 | 2, 0 | 1 | 2]> = {
  x_min: [1, 2],
  x_max: [1, 2],
  y_min: [0, 2],
  y_max: [0, 2],
  z_min: [0, 1],
  z_max: [0, 1],
}

function isFiniteVec3(value: readonly number[]): value is Vec3 {
  return value.length === 3 && value.every(Number.isFinite)
}

function isLeakageAabbFace(value: string): value is LeakageAabbFace {
  return faceOrder.includes(value as LeakageAabbFace)
}

function normalize(value: Vec3): Vec3 | null {
  const lengthSquared =
    value[0] * value[0] + value[1] * value[1] + value[2] * value[2]
  if (!Number.isFinite(lengthSquared) || lengthSquared <= 1e-24) return null
  const inverseLength = 1 / Math.sqrt(lengthSquared)
  return [
    value[0] * inverseLength,
    value[1] * inverseLength,
    value[2] * inverseLength,
  ]
}

function dot(left: Vec3, right: Vec3): number {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]
}

function sampleKey(sample: LeakagePreviewSample): string {
  return `${sample.runId}:${sample.pathIndex}`
}

function validateOptions(options: LeakageConnectivityOptions): void {
  if (
    !Number.isFinite(options.maxNeighborDistance) ||
    options.maxNeighborDistance < 0
  ) {
    throw new RangeError('maxNeighborDistance must be finite and nonnegative')
  }
  if (
    !Number.isFinite(options.minDirectionDot) ||
    options.minDirectionDot < -1 ||
    options.minDirectionDot > 1
  ) {
    throw new RangeError('minDirectionDot must be between -1 and 1')
  }
  if (
    !Number.isFinite(options.commonWeightScale) ||
    options.commonWeightScale <= 0
  ) {
    throw new RangeError('commonWeightScale must be finite and positive')
  }
}

function nodeComparator(
  left: LeakageConnectivityNode,
  right: LeakageConnectivityNode,
): number {
  return (
    faceOrder.indexOf(left.face) - faceOrder.indexOf(right.face) ||
    left.facePosition[0] - right.facePosition[0] ||
    left.facePosition[1] - right.facePosition[1] ||
    left.key.localeCompare(right.key) ||
    left.direction[0] - right.direction[0] ||
    left.direction[1] - right.direction[1] ||
    left.direction[2] - right.direction[2] ||
    left.weight - right.weight
  )
}

function spatialCellKey(first: number, second: number): string {
  return `${first}:${second}`
}

function buildCandidateEdges(
  nodes: readonly LeakageConnectivityNode[],
  options: LeakageConnectivityOptions,
): CandidateEdge[] {
  const distanceTolerance = Math.max(
    1e-12,
    options.maxNeighborDistance * 1e-12,
  )
  // A positive gap is its own spatial-hash cell size. With a zero gap, a
  // fixed cell still groups coincident points while the exact distance check
  // below keeps the zero-distance contract.
  const cellSize = options.maxNeighborDistance > 0
    ? options.maxNeighborDistance + distanceTolerance
    : 1
  const grids = new Map<LeakageAabbFace, Map<string, number[]>>()
  const cellCoordinates: Array<readonly [number, number]> = []

  nodes.forEach((node, index) => {
    const first = Math.floor(node.facePosition[0] / cellSize)
    const second = Math.floor(node.facePosition[1] / cellSize)
    cellCoordinates.push([first, second])
    let faceGrid = grids.get(node.face)
    if (!faceGrid) {
      faceGrid = new Map<string, number[]>()
      grids.set(node.face, faceGrid)
    }
    const key = spatialCellKey(first, second)
    const indices = faceGrid.get(key)
    if (indices) indices.push(index)
    else faceGrid.set(key, [index])
  })

  const candidates: CandidateEdge[] = []
  nodes.forEach((left, leftIndex) => {
    const faceGrid = grids.get(left.face)
    const cell = cellCoordinates[leftIndex]
    if (!faceGrid || !cell) return
    for (let firstOffset = -1; firstOffset <= 1; firstOffset += 1) {
      for (let secondOffset = -1; secondOffset <= 1; secondOffset += 1) {
        const neighborIndices = faceGrid.get(
          spatialCellKey(cell[0] + firstOffset, cell[1] + secondOffset),
        ) ?? []
        for (const rightIndex of neighborIndices) {
          if (rightIndex <= leftIndex) continue
          const right = nodes[rightIndex]
          const firstDelta = right.facePosition[0] - left.facePosition[0]
          const secondDelta = right.facePosition[1] - left.facePosition[1]
          const distance = Math.hypot(firstDelta, secondDelta)
          if (distance > options.maxNeighborDistance + distanceTolerance) continue
          if (dot(left.direction, right.direction) < options.minDirectionDot) continue
          candidates.push({ leftIndex, rightIndex, distance })
        }
      }
    }
  })
  return candidates
}

/**
 * Retain the relative-neighborhood graph. An edge is redundant when a third
 * direction-compatible sample is strictly closer to both endpoints. This
 * removes long straight bridges and the diagonal shortcut around an L corner
 * without inventing edges across an empty interval.
 */
function pruneRelativeNeighborhood(
  candidates: readonly CandidateEdge[],
  nodeCount: number,
): CandidateEdge[] {
  const adjacency = Array.from(
    { length: nodeCount },
    () => new Map<number, number>(),
  )
  for (const candidate of candidates) {
    adjacency[candidate.leftIndex].set(candidate.rightIndex, candidate.distance)
    adjacency[candidate.rightIndex].set(candidate.leftIndex, candidate.distance)
  }

  return candidates.filter((candidate) => {
    const leftNeighbors = adjacency[candidate.leftIndex]
    const rightNeighbors = adjacency[candidate.rightIndex]
    const [smaller, other] = leftNeighbors.size <= rightNeighbors.size
      ? [leftNeighbors, rightNeighbors]
      : [rightNeighbors, leftNeighbors]
    const tolerance = Math.max(1e-12, candidate.distance * 1e-12)
    for (const [thirdIndex, firstDistance] of smaller) {
      if (
        thirdIndex === candidate.leftIndex ||
        thirdIndex === candidate.rightIndex
      ) continue
      const secondDistance = other.get(thirdIndex)
      if (secondDistance === undefined) continue
      if (
        Math.max(firstDistance, secondDistance) <
        candidate.distance - tolerance
      ) return false
    }
    return true
  })
}

class DisjointSet {
  private readonly parents: number[]

  constructor(size: number) {
    this.parents = Array.from({ length: size }, (_, index) => index)
  }

  find(index: number): number {
    const parent = this.parents[index]
    if (parent === index) return index
    const root = this.find(parent)
    this.parents[index] = root
    return root
  }

  union(left: number, right: number): void {
    const leftRoot = this.find(left)
    const rightRoot = this.find(right)
    if (leftRoot === rightRoot) return
    this.parents[Math.max(leftRoot, rightRoot)] = Math.min(leftRoot, rightRoot)
  }
}

/**
 * Connect nearby, direction-compatible samples only when extraction assigned
 * one unambiguous AABB exit face. AABB coordinates are not reinterpreted here.
 * Edges carry no energy; every accepted source contributes exactly once.
 */
export function buildLeakagePreviewConnectivity(
  samples: readonly LeakagePreviewSample[],
  options: LeakageConnectivityOptions,
): LeakageConnectivityResult {
  validateOptions(options)

  const nodes: LeakageConnectivityNode[] = []
  const rejected: LeakageConnectivityRejection[] = []

  for (const sample of samples) {
    const key = sampleKey(sample)
    const weight = sample.weight
    if (!Number.isFinite(weight) || weight <= 0) {
      rejected.push({ key, reason: 'invalid_weight', weight: 0, sample })
      continue
    }
    if (!isFiniteVec3(sample.exitPoint)) {
      rejected.push({ key, reason: 'invalid_point', weight, sample })
      continue
    }
    if (!isFiniteVec3(sample.outgoingDirection)) {
      rejected.push({ key, reason: 'invalid_direction', weight, sample })
      continue
    }
    const direction = normalize(sample.outgoingDirection)
    if (!direction) {
      rejected.push({ key, reason: 'invalid_direction', weight, sample })
      continue
    }
    if (!Array.isArray(sample.exitFaces) || sample.exitFaces.length !== 1) {
      rejected.push({ key, reason: 'ambiguous_exit_face', weight, sample })
      continue
    }
    const face = sample.exitFaces[0]
    if (!isLeakageAabbFace(face)) {
      rejected.push({ key, reason: 'invalid_exit_face', weight, sample })
      continue
    }
    const [firstAxis, secondAxis] = faceAxes[face]
    nodes.push({
      key,
      face,
      position: [...sample.exitPoint],
      facePosition: [sample.exitPoint[firstAxis], sample.exitPoint[secondAxis]],
      direction,
      weight,
      displayStrength: Math.min(weight / options.commonWeightScale, 1),
      sample,
    })
  }

  nodes.sort(nodeComparator)
  rejected.sort(
    (left, right) =>
      left.key.localeCompare(right.key) || left.reason.localeCompare(right.reason),
  )

  const candidates = buildCandidateEdges(nodes, options)
  const retainedCandidates = pruneRelativeNeighborhood(candidates, nodes.length)
  const disjointSet = new DisjointSet(nodes.length)
  const edges = retainedCandidates.map((candidate): LeakageConnectivityEdge => {
    const left = nodes[candidate.leftIndex]
    const right = nodes[candidate.rightIndex]
    disjointSet.union(candidate.leftIndex, candidate.rightIndex)
    return {
      face: left.face,
      from: left.key,
      to: right.key,
      distance: candidate.distance,
    }
  })
  edges.sort(
    (left, right) =>
      faceOrder.indexOf(left.face) - faceOrder.indexOf(right.face) ||
      left.from.localeCompare(right.from) ||
      left.to.localeCompare(right.to),
  )

  const componentMembers = new Map<number, number[]>()
  nodes.forEach((_, index) => {
    const root = disjointSet.find(index)
    const indices = componentMembers.get(root)
    if (indices) indices.push(index)
    else componentMembers.set(root, [index])
  })
  const componentEdgeCounts = new Map<number, number>()
  for (const candidate of retainedCandidates) {
    const root = disjointSet.find(candidate.leftIndex)
    componentEdgeCounts.set(root, (componentEdgeCounts.get(root) ?? 0) + 1)
  }
  const components = [...componentMembers.entries()]
    .map(([root, indices]): LeakageConnectivityComponent => {
      const nodeKeys = indices.map((index) => nodes[index].key).sort()
      const face = nodes[indices[0]].face
      return {
        id: `${face}:${nodeKeys.join('|')}`,
        face,
        nodeKeys,
        edgeCount: componentEdgeCounts.get(root) ?? 0,
        totalWeight: indices.reduce(
          (sum, index) => sum + nodes[index].weight,
          0,
        ),
      }
    })
    .sort((left, right) => left.id.localeCompare(right.id))

  const acceptedWeight = nodes.reduce((sum, node) => sum + node.weight, 0)
  const rejectedWeight = rejected.reduce(
    (sum, rejection) => sum + rejection.weight,
    0,
  )
  return {
    nodes,
    edges,
    components,
    rejected,
    energy: {
      validInputWeight: acceptedWeight + rejectedWeight,
      acceptedWeight,
      rejectedWeight,
    },
    commonWeightScale: options.commonWeightScale,
  }
}

/**
 * Apply the shared prototype display policy used by both the workspace label
 * and the Three.js renderer. Partial path capture remains point-only.
 */
export function buildPrototypeLeakageFieldSelection(
  samples: readonly LeakagePreviewSample[],
  fullCapture: boolean,
): PrototypeLeakageFieldSelection {
  if (!fullCapture) {
    return { connectivity: null, continuousNodeKeys: new Set() }
  }
  const connectivity = buildLeakagePreviewConnectivity(
    samples,
    prototypeLeakageConnectivityOptions,
  )
  const continuousNodeKeys = new Set(
    connectivity.components
      .filter(
        (component) =>
          component.nodeKeys.length >=
            prototypeLeakageMinimumContinuousSamples &&
          component.edgeCount >= component.nodeKeys.length - 1,
      )
      .flatMap((component) => component.nodeKeys),
  )
  return { connectivity, continuousNodeKeys }
}
