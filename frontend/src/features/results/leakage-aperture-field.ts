import type { Vec3 } from '@/api'

import type { LeakageApertureMask } from './leakage-aperture-mask'
import type { LeakageAabbFace, LeakagePreviewSample } from './leakage-preview-data'

export interface LeakageApertureFieldOptions {
  /** Fixed physical bandwidth, shared across ray counts and compared Cases. */
  sigmaMm: number
  supportRadiusMm: number
  maxSamples: number
  maxCells: number
  maxVisitedCells: number
}

export interface LeakageApertureDirectionBin {
  /** Stable hemisphere bin in [0, 8], independent of the number of samples. */
  id: number
  /** Energy-weighted outgoing direction within this fixed angular bin.
   * This is a coarse angular approximation, not a measured BRDF. */
  direction: Vec3
  /** Deposited luminous flux / mm². Never normalized by the brightest cell. */
  density: Float64Array
  totalEnergy: number
}

export type LeakageApertureFieldUnsupportedReason =
  | 'invalid_mask'
  | 'invalid_options'
  | 'sample_budget_exceeded'
  | 'cell_budget_exceeded'
  | 'work_budget_exceeded'
  | 'energy_overflow'

export type LeakageApertureFieldRejectionReason =
  | 'invalid_weight'
  | 'invalid_point'
  | 'invalid_direction'
  | 'inward_direction'
  | 'off_face_plane'
  | 'outside_mask'
  | 'closed_mask_cell'
  | 'support_too_small'

export interface LeakageApertureFieldResult {
  status: 'ready' | 'unsupported'
  reason?: LeakageApertureFieldUnsupportedReason
  mask: LeakageApertureMask
  bins: LeakageApertureDirectionBin[]
  /** Angularly integrated density, primarily for validation and diagnostics. */
  density: Float64Array
  usedSampleKeys: ReadonlySet<string>
  rejected: Array<{
    key: string
    reason: LeakageApertureFieldRejectionReason
    weight: number
  }>
  /** Finite, positive source energy on this unambiguous face, including rejections. */
  totalInputEnergy: number
  /** Integral of density * cell area, with no display curve or per-ray clamp. */
  totalDepositedEnergy: number
  visitedCellCount: number
}

export const leakageApertureFieldDefaults: Readonly<LeakageApertureFieldOptions> = {
  sigmaMm: 1.8,
  supportRadiusMm: 4,
  maxSamples: 50000,
  maxCells: 262144,
  maxVisitedCells: 8000000,
}

const faceAxes: Record<LeakageAabbFace, readonly [0 | 1 | 2, 0 | 1 | 2, 0 | 1 | 2]> = {
  x_min: [1, 2, 0],
  x_max: [1, 2, 0],
  y_min: [0, 2, 1],
  y_max: [0, 2, 1],
  z_min: [0, 1, 2],
  z_max: [0, 1, 2],
}

function finiteVec3(vector: readonly number[]): vector is Vec3 {
  return vector.length === 3 && vector.every(Number.isFinite)
}

function normalize(vector: Vec3): Vec3 | null {
  const length = Math.hypot(...vector)
  if (!Number.isFinite(length) || length <= 1e-12) return null
  return vector.map((value) => value / length) as Vec3
}

function dot(left: Vec3, right: Vec3): number {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]
}

function binDirections(face: LeakageAabbFace): Vec3[] {
  const [uAxis, vAxis, normalAxis] = faceAxes[face]
  const sign = face.endsWith('max') ? 1 : -1
  const normal: Vec3 = [0, 0, 0]
  normal[normalAxis] = sign
  return [normal, ...Array.from({ length: 8 }, (_, index): Vec3 => {
    const angle = index * Math.PI / 4
    const direction: Vec3 = [0, 0, 0]
    direction[uAxis] = Math.cos(angle) * Math.sqrt(3) / 2
    direction[vAxis] = Math.sin(angle) * Math.sqrt(3) / 2
    direction[normalAxis] = sign / 2
    return direction
  })]
}

/** A bounded min-heap for multi-source geodesic distance on a four-neighbor grid. */
class DistanceHeap {
  private readonly indices: number[] = []
  private readonly distances: number[] = []

  get size(): number { return this.indices.length }

  push(index: number, distance: number): void {
    let slot = this.indices.length
    this.indices.push(index)
    this.distances.push(distance)
    while (slot > 0) {
      const parent = (slot - 1) >>> 1
      if (this.distances[parent] <= distance) break
      this.indices[slot] = this.indices[parent]
      this.distances[slot] = this.distances[parent]
      slot = parent
    }
    this.indices[slot] = index
    this.distances[slot] = distance
  }

  pop(): readonly [number, number] {
    const index = this.indices[0]
    const distance = this.distances[0]
    const lastIndex = this.indices.pop()!
    const lastDistance = this.distances.pop()!
    if (this.indices.length > 0) {
      let slot = 0
      while (slot * 2 + 1 < this.indices.length) {
        let child = slot * 2 + 1
        if (
          child + 1 < this.indices.length &&
          this.distances[child + 1] < this.distances[child]
        ) child += 1
        if (lastDistance <= this.distances[child]) break
        this.indices[slot] = this.indices[child]
        this.distances[slot] = this.distances[child]
        slot = child
      }
      this.indices[slot] = lastIndex
      this.distances[slot] = lastDistance
    }
    return [index, distance]
  }
}

function optionsValid(options: LeakageApertureFieldOptions): boolean {
  return Number.isFinite(options.sigmaMm) && options.sigmaMm > 0 &&
    Number.isFinite(options.supportRadiusMm) && options.supportRadiusMm > 0 &&
    [options.maxSamples, options.maxCells, options.maxVisitedCells].every(
      (value) => Number.isSafeInteger(value) && value >= 1,
    )
}

function maskValid(mask: LeakageApertureMask): boolean {
  const count = mask.width * mask.height
  return mask.face in faceAxes &&
    mask.origin.length === 2 && mask.origin.every(Number.isFinite) &&
    Number.isFinite(mask.plane) && Number.isFinite(mask.stepMm) && mask.stepMm > 0 &&
    Number.isFinite(mask.stepMm ** 2) && mask.stepMm ** 2 > 0 &&
    Number.isSafeInteger(mask.width) && mask.width > 0 &&
    Number.isSafeInteger(mask.height) && mask.height > 0 &&
    Number.isSafeInteger(count) &&
    mask.open.length === count && mask.componentIds.length === count
}

/**
 * Reconstruct a local density field only inside a geometrically verified mask.
 * Every accepted ray is spread over a finite four-neighbor geodesic footprint,
 * with normalized weights whose integral equals that ray's energy. Grid cells
 * and angular bins are fixed physical coordinates: adding more lower-energy
 * rays does not add artificial light, ribbons, or graph edges.
 *
 * A sample in a conservatively closed mask cell is deliberately left for the
 * caller's unsupported-sample policy. It is never moved across a thin barrier
 * or silently assigned to a nearby opening. Full path-capture eligibility and
 * the physical validity of the mask remain the caller's responsibility.
 */
export function buildLeakageApertureField(
  mask: LeakageApertureMask,
  samples: readonly LeakagePreviewSample[],
  overrides: Partial<LeakageApertureFieldOptions> = {},
): LeakageApertureFieldResult {
  const options = { ...leakageApertureFieldDefaults, ...overrides }
  // Other faces and ambiguous corners are not counted once per mask.
  const eligible = samples.filter(
    (sample) => sample.exitFaces.length === 1 && sample.exitFaces[0] === mask.face,
  )
  const totalInputEnergy = eligible.reduce(
    (sum, sample) => sum + (
      Number.isFinite(sample.weight) && sample.weight > 0 ? sample.weight : 0
    ),
    0,
  )
  const unsupported = (
    reason: LeakageApertureFieldUnsupportedReason,
    visitedCellCount = 0,
  ): LeakageApertureFieldResult => ({
    status: 'unsupported', reason, mask, bins: [], density: new Float64Array(0),
    usedSampleKeys: new Set(), rejected: [], totalInputEnergy,
    totalDepositedEnergy: 0, visitedCellCount,
  })
  if (!optionsValid(options)) return unsupported('invalid_options')
  if (!maskValid(mask)) return unsupported('invalid_mask')
  const cellCount = mask.width * mask.height
  if (cellCount > Math.min(options.maxCells, leakageApertureFieldDefaults.maxCells)) {
    return unsupported('cell_budget_exceeded')
  }
  if (eligible.length > Math.min(options.maxSamples, leakageApertureFieldDefaults.maxSamples)) {
    return unsupported('sample_budget_exceeded')
  }
  if (!Number.isFinite(totalInputEnergy)) return unsupported('energy_overflow')
  const bins = binDirections(mask.face).map((direction, id): LeakageApertureDirectionBin => ({
    id, direction, density: new Float64Array(cellCount), totalEnergy: 0,
  }))
  const directionSums = bins.map((): Vec3 => [0, 0, 0])
  const density = new Float64Array(cellCount)
  const usedSampleKeys = new Set<string>()
  const rejected: LeakageApertureFieldResult['rejected'] = []
  const distances = new Float64Array(cellCount)
  const distanceStamps = new Int32Array(cellCount)
  const [uAxis, vAxis, normalAxis] = faceAxes[mask.face]
  const outwardSign = mask.face.endsWith('max') ? 1 : -1
  const cellArea = mask.stepMm * mask.stepMm
  const radius = options.supportRadiusMm
  const sigma = options.sigmaMm
  const workLimit = Math.min(options.maxVisitedCells, leakageApertureFieldDefaults.maxVisitedCells)
  let visitedCellCount = 0

  for (let sampleIndex = 0; sampleIndex < eligible.length; sampleIndex += 1) {
    const sample = eligible[sampleIndex]
    const key = sample.runId + ':' + sample.pathIndex
    const reject = (reason: LeakageApertureFieldRejectionReason): void => {
      rejected.push({ key, reason, weight: Number.isFinite(sample.weight) && sample.weight > 0 ? sample.weight : 0 })
    }
    if (!Number.isFinite(sample.weight) || sample.weight <= 0) {
      reject('invalid_weight'); continue
    }
    if (!finiteVec3(sample.exitPoint)) { reject('invalid_point'); continue }
    const direction = finiteVec3(sample.outgoingDirection) ? normalize(sample.outgoingDirection) : null
    if (!direction) { reject('invalid_direction'); continue }
    if (direction[normalAxis] * outwardSign <= 0) { reject('inward_direction'); continue }
    if (Math.abs(sample.exitPoint[normalAxis] - mask.plane) > Math.max(1e-7, mask.stepMm * 1e-5)) {
      reject('off_face_plane'); continue
    }
    const u = (sample.exitPoint[uAxis] - mask.origin[0]) / mask.stepMm
    const v = (sample.exitPoint[vAxis] - mask.origin[1]) / mask.stepMm
    if (u < 0 || v < 0 || u >= mask.width || v >= mask.height) {
      reject('outside_mask'); continue
    }
    const column = Math.floor(u)
    const row = Math.floor(v)
    const anchor = row * mask.width + column
    const component = mask.componentIds[anchor]
    if (mask.open[anchor] !== 1 || component < 0) {
      reject('closed_mask_cell'); continue
    }
    const stamp = sampleIndex + 1
    const heap = new DistanceHeap()
    const available = (x: number, y: number): boolean => {
      if (x < 0 || y < 0 || x >= mask.width || y >= mask.height) return false
      const index = y * mask.width + x
      return mask.open[index] === 1 && mask.componentIds[index] === component
    }
    const offer = (index: number, distance: number): void => {
      if (distance > radius) return
      if (distanceStamps[index] === stamp && distances[index] <= distance) return
      distanceStamps[index] = stamp
      distances[index] = distance
      heap.push(index, distance)
    }
    // Seed the nearby cell centers continuously in physical coordinates. A
    // diagonal seed requires both side cells open, so it cannot cut a corner.
    for (let y = Math.floor(v - 0.5); y <= Math.floor(v - 0.5) + 1; y += 1) {
      for (let x = Math.floor(u - 0.5); x <= Math.floor(u - 0.5) + 1; x += 1) {
        if (!available(x, y)) continue
        if (x !== column && y !== row && (!available(x, row) || !available(column, y))) continue
        offer(y * mask.width + x, Math.hypot(x + 0.5 - u, y + 0.5 - v) * mask.stepMm)
      }
    }
    const footprint: Array<readonly [number, number]> = []
    let kernelSum = 0
    while (heap.size > 0) {
      const [index, distance] = heap.pop()
      if (distance !== distances[index]) continue
      visitedCellCount += 1
      if (visitedCellCount > workLimit) return unsupported('work_budget_exceeded', visitedCellCount)
      // Smoothly taper the outer quarter to zero; a hard Gaussian cutoff
      // leaves visible edges where the finite ray-supported footprint ends.
      const taperCoordinate = Math.max(0, Math.min(1, (distance / radius - 0.75) * 4))
      const taper = 1 - taperCoordinate ** 2 * (3 - 2 * taperCoordinate)
      const kernel = Math.exp(-0.5 * (distance / sigma) ** 2) * taper
      footprint.push([index, kernel])
      kernelSum += kernel
      const x = index % mask.width
      const y = Math.floor(index / mask.width)
      for (const [nextX, nextY] of [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]]) {
        if (available(nextX, nextY)) offer(nextY * mask.width + nextX, distance + mask.stepMm)
      }
    }
    if (!(kernelSum > 0)) { reject('support_too_small'); continue }
    let binIndex = 0
    let bestDot = Number.NEGATIVE_INFINITY
    for (const bin of bins) {
      const similarity = dot(direction, bin.direction)
      if (similarity > bestDot) { bestDot = similarity; binIndex = bin.id }
    }
    const bin = bins[binIndex]
    for (const [index, kernel] of footprint) {
      const contribution = sample.weight * (kernel / kernelSum) / cellArea
      bin.density[index] += contribution
      density[index] += contribution
    }
    bin.totalEnergy += sample.weight
    for (let axis = 0; axis < 3; axis += 1) {
      directionSums[binIndex][axis] += direction[axis] * sample.weight
    }
    usedSampleKeys.add(key)
  }
  for (const bin of bins) {
    bin.direction = normalize(directionSums[bin.id]) ?? bin.direction
  }
  const totalDepositedEnergy = density.reduce((sum, value) => sum + value * cellArea, 0)
  if (!Number.isFinite(totalDepositedEnergy)) return unsupported('energy_overflow', visitedCellCount)
  return {
    status: 'ready', mask, bins, density, usedSampleKeys, rejected,
    totalInputEnergy, totalDepositedEnergy, visitedCellCount,
  }
}

export type LeakageApertureField = LeakageApertureFieldResult
