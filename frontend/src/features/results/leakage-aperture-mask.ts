import type { ScenePayload } from '@/api'

import type {
  LeakageAabbFace,
  LeakagePreviewBounds,
  LeakagePreviewSample,
} from './leakage-preview-data'

export interface LeakageApertureMask {
  face: LeakageAabbFace
  /** Lower-left cell boundary in the face's projected millimetre coordinates. */
  origin: [number, number]
  stepMm: number
  width: number
  height: number
  plane: number
  /** Row-major; 1 only for a supported, entirely uncovered cell. */
  open: Uint8Array
  /** Row-major; -1 outside supported holes, otherwise a nonnegative hole id. */
  componentIds: Int32Array
}

export interface LeakageApertureMaskResult {
  masks: LeakageApertureMask[]
  reason?: string
}

const coarseStepMm = 0.1
const refinedStepMm = 0.025
const paddingMm = 5
const maximumCells = 200_000
const maximumTriangles = 250_000
const maximumTriangleCellChecks = 5_000_000
const boundaryEpsilonMm = 1e-9
const planeToleranceMm = 1e-7

const faceAxes = {
  x_min: [0, 1, 2],
  x_max: [0, 1, 2],
  y_min: [1, 0, 2],
  y_max: [1, 0, 2],
  z_min: [2, 0, 1],
  z_max: [2, 0, 1],
} as const satisfies Record<LeakageAabbFace, readonly [number, number, number]>

/** Choose a stable lattice without changing any geometry or overlap tolerance.
 * Binary scene coordinates can encode -0.4 as -0.4000000059604645; using that
 * value as the grid phase needlessly clips every otherwise aligned edge cell.
 * Snap only exact Float32 representations of a grid multiple, within 10 nm.
 */
function stableGridAnchor(coordinate: number): number {
  const aligned = Math.round(coordinate / coarseStepMm) * coarseStepMm
  return Math.abs(aligned - coordinate) <= 1e-5 && Math.fround(aligned) === coordinate
    ? aligned
    : coordinate
}

interface ProjectedTriangle {
  minimum: [number, number]
  maximum: [number, number]
  axes: Array<{ x: number; y: number; minimum: number; maximum: number }>
}

function finitePoint(point: readonly number[] | undefined): boolean {
  return !!point && point.length === 3 && point.every(Number.isFinite)
}

function projectTriangle(
  points: readonly (readonly number[])[],
  u: number,
  v: number,
): ProjectedTriangle | null {
  const twiceArea =
    (points[1][u] - points[0][u]) * (points[2][v] - points[0][v]) -
    (points[1][v] - points[0][v]) * (points[2][u] - points[0][u])
  if (Math.abs(twiceArea) <= 1e-16) return null
  const axes: ProjectedTriangle['axes'] = []
  for (let edge = 0; edge < 3; edge += 1) {
    const from = points[edge]
    const to = points[(edge + 1) % 3]
    const dx = to[u] - from[u]
    const dy = to[v] - from[v]
    const length = Math.hypot(dx, dy)
    if (length <= 1e-12) continue
    const x = -dy / length
    const y = dx / length
    const projections = points.map((point) => point[u] * x + point[v] * y)
    axes.push({
      x,
      y,
      minimum: Math.min(...projections),
      maximum: Math.max(...projections),
    })
  }
  return {
    minimum: [Math.min(...points.map((p) => p[u])), Math.min(...points.map((p) => p[v]))],
    maximum: [Math.max(...points.map((p) => p[u])), Math.max(...points.map((p) => p[v]))],
    axes,
  }
}

/** SAT against the entire cell: a diagonal or sub-cell metal strip blocks it. */
function triangleOverlapsCell(
  triangle: ProjectedTriangle,
  x: number,
  y: number,
  stepMm: number,
): boolean {
  if (
    triangle.maximum[0] <= x + boundaryEpsilonMm ||
    triangle.minimum[0] >= x + stepMm - boundaryEpsilonMm ||
    triangle.maximum[1] <= y + boundaryEpsilonMm ||
    triangle.minimum[1] >= y + stepMm - boundaryEpsilonMm
  ) return false
  const centerX = x + stepMm / 2
  const centerY = y + stepMm / 2
  for (const axis of triangle.axes) {
    const center = centerX * axis.x + centerY * axis.y
    const radius = stepMm / 2 * (Math.abs(axis.x) + Math.abs(axis.y))
    if (
      axis.maximum <= center - radius + boundaryEpsilonMm ||
      axis.minimum >= center + radius - boundaryEpsilonMm
    ) return false
  }
  return true
}

function sampleCell(
  mask: LeakageApertureMask,
  sample: LeakagePreviewSample,
): number {
  const [, u, v] = faceAxes[mask.face]
  const column = Math.floor((sample.exitPoint[u] - mask.origin[0]) / mask.stepMm)
  const row = Math.floor((sample.exitPoint[v] - mask.origin[1]) / mask.stepMm)
  return column >= 0 && column < mask.width && row >= 0 && row < mask.height
    ? row * mask.width + column
    : -1
}

/** Keep only enclosed uncovered components containing an actual ray sample. */
function retainSupportedHoles(
  mask: LeakageApertureMask,
  samples: readonly LeakagePreviewSample[],
): boolean {
  const supportedCells = new Set(samples.map((sample) => sampleCell(mask, sample)))
  const queue = new Int32Array(mask.open.length)
  let nextComponent = 0
  for (let start = 0; start < mask.open.length; start += 1) {
    if (!mask.open[start] || mask.componentIds[start] !== -1) continue
    let head = 0
    let tail = 1
    let touchesBoundary = false
    let hasSample = false
    queue[0] = start
    mask.componentIds[start] = -2
    while (head < tail) {
      const current = queue[head++]
      const column = current % mask.width
      const row = Math.floor(current / mask.width)
      if (column === 0 || row === 0 || column === mask.width - 1 || row === mask.height - 1) {
        touchesBoundary = true
      }
      if (supportedCells.has(current)) hasSample = true
      const neighbors = [
        column > 0 ? current - 1 : -1,
        column + 1 < mask.width ? current + 1 : -1,
        row > 0 ? current - mask.width : -1,
        row + 1 < mask.height ? current + mask.width : -1,
      ]
      for (const neighbor of neighbors) {
        if (neighbor < 0 || !mask.open[neighbor] || mask.componentIds[neighbor] !== -1) continue
        mask.componentIds[neighbor] = -2
        queue[tail++] = neighbor
      }
    }
    const keep = !touchesBoundary && hasSample
    for (let index = 0; index < tail; index += 1) {
      const cell = queue[index]
      mask.open[cell] = keep ? 1 : 0
      mask.componentIds[cell] = keep ? nextComponent : -1
    }
    if (keep) nextComponent += 1
  }
  return nextComponent > 0
}

interface RasterBudget {
  cells: number
  checks: number
}

interface PatchBounds {
  minimum: [number, number]
  maximum: [number, number]
}

function rasterMask(
  face: LeakageAabbFace,
  plane: number,
  patch: PatchBounds,
  gridAnchor: readonly [number, number],
  stepMm: number,
  triangles: readonly ProjectedTriangle[],
  budget: RasterBudget,
): { mask?: LeakageApertureMask; reason?: string } {
  const origin: [number, number] = [
    gridAnchor[0] + Math.ceil((patch.minimum[0] - gridAnchor[0]) / stepMm - boundaryEpsilonMm) * stepMm,
    gridAnchor[1] + Math.ceil((patch.minimum[1] - gridAnchor[1]) / stepMm - boundaryEpsilonMm) * stepMm,
  ]
  const width = Math.floor((patch.maximum[0] - origin[0]) / stepMm + boundaryEpsilonMm)
  const height = Math.floor((patch.maximum[1] - origin[1]) / stepMm + boundaryEpsilonMm)
  const cells = width * height
  if (!Number.isSafeInteger(cells) || cells < 1) return {}
  if (budget.cells + cells > maximumCells) return { reason: 'aperture_cell_limit' }
  budget.cells += cells
  const mask: LeakageApertureMask = {
    face, origin, stepMm, width, height, plane,
    open: new Uint8Array(cells).fill(1),
    componentIds: new Int32Array(cells).fill(-1),
  }
  for (const triangle of triangles) {
    const firstColumn = Math.max(0, Math.floor((triangle.minimum[0] - origin[0]) / stepMm))
    const lastColumn = Math.min(width - 1, Math.floor((triangle.maximum[0] - origin[0]) / stepMm))
    const firstRow = Math.max(0, Math.floor((triangle.minimum[1] - origin[1]) / stepMm))
    const lastRow = Math.min(height - 1, Math.floor((triangle.maximum[1] - origin[1]) / stepMm))
    if (firstColumn > lastColumn || firstRow > lastRow) continue
    budget.checks += (lastColumn - firstColumn + 1) * (lastRow - firstRow + 1)
    if (budget.checks > maximumTriangleCellChecks) return { reason: 'aperture_triangle_cell_limit' }
    for (let row = firstRow; row <= lastRow; row += 1) {
      for (let column = firstColumn; column <= lastColumn; column += 1) {
        const cell = row * width + column
        if (!mask.open[cell]) continue
        if (triangleOverlapsCell(triangle, origin[0] + column * stepMm, origin[1] + row * stepMm, stepMm)) {
          mask.open[cell] = 0
        }
      }
    }
  }
  return { mask }
}

/** Intersect a coordinate line with a convex triangle using its SAT slabs. */
function triangleLineInterval(
  triangle: ProjectedTriangle,
  coordinateAxis: 0 | 1,
  fixedCoordinate: number,
): readonly [number, number] | null {
  const fixedAxis = coordinateAxis === 0 ? 1 : 0
  if (
    fixedCoordinate < triangle.minimum[fixedAxis] - boundaryEpsilonMm ||
    fixedCoordinate > triangle.maximum[fixedAxis] + boundaryEpsilonMm
  ) return null
  let minimum = triangle.minimum[coordinateAxis]
  let maximum = triangle.maximum[coordinateAxis]
  for (const axis of triangle.axes) {
    const slope = coordinateAxis === 0 ? axis.x : axis.y
    const fixed = fixedCoordinate * (coordinateAxis === 0 ? axis.y : axis.x)
    if (Math.abs(slope) < 1e-12) {
      if (fixed < axis.minimum - boundaryEpsilonMm || fixed > axis.maximum + boundaryEpsilonMm) return null
      continue
    }
    const first = (axis.minimum - fixed) / slope
    const second = (axis.maximum - fixed) / slope
    minimum = Math.max(minimum, Math.min(first, second))
    maximum = Math.min(maximum, Math.max(first, second))
    if (minimum > maximum + boundaryEpsilonMm) return null
  }
  return [minimum, maximum]
}

/**
 * Only tighten an axis when actual skin bounds every sample's cross-section.
 * The subsequent flood still rejects an opening escaping this smaller patch;
 * sample extents or estimated width alone never establish a closed boundary.
 */
function refinementPatch(
  coarse: LeakageApertureMask,
  patch: PatchBounds,
  triangles: readonly ProjectedTriangle[],
  samples: readonly LeakagePreviewSample[],
  budget: RasterBudget,
): { patch?: PatchBounds; reason?: string } {
  const [, u, v] = faceAxes[coarse.face]
  const lower: [number, number] = [Infinity, Infinity]
  const upper: [number, number] = [-Infinity, -Infinity]
  const largestWidth: [number, number] = [0, 0]
  const bounded = [true, true]
  let shouldRefine = false
  let uncoveredSamples = 0
  for (const sample of samples) {
    const point: [number, number] = [sample.exitPoint[u], sample.exitPoint[v]]
    const left: [number, number] = [-Infinity, -Infinity]
    const right: [number, number] = [Infinity, Infinity]
    let covered = false
    for (const triangle of triangles) {
      budget.checks += 1
      if (budget.checks > maximumTriangleCellChecks) return { reason: 'aperture_triangle_cell_limit' }
      for (const coordinateAxis of [0, 1] as const) {
        const interval = triangleLineInterval(triangle, coordinateAxis, point[coordinateAxis === 0 ? 1 : 0])
        if (!interval) continue
        const coordinate = point[coordinateAxis]
        if (interval[0] <= coordinate + boundaryEpsilonMm && interval[1] >= coordinate - boundaryEpsilonMm) {
          covered = true
          break
        }
        if (interval[1] < coordinate) left[coordinateAxis] = Math.max(left[coordinateAxis], interval[1])
        if (interval[0] > coordinate) right[coordinateAxis] = Math.min(right[coordinateAxis], interval[0])
      }
      if (covered) break
    }
    // Rays through metal or exactly on its boundary do not justify refinement.
    if (covered) continue
    uncoveredSamples += 1
    const cell = sampleCell(coarse, sample)
    if (cell < 0 || coarse.open[cell] !== 1) shouldRefine = true
    for (const axis of [0, 1] as const) {
      if (!Number.isFinite(left[axis]) || !Number.isFinite(right[axis])) {
        bounded[axis] = false
        continue
      }
      const width = right[axis] - left[axis]
      if (width < 2 * coarse.stepMm + boundaryEpsilonMm) shouldRefine = true
      largestWidth[axis] = Math.max(largestWidth[axis], width)
      lower[axis] = Math.min(lower[axis], left[axis])
      upper[axis] = Math.max(upper[axis], right[axis])
    }
  }
  if (!shouldRefine || uncoveredSamples === 0) return {}
  const refined: PatchBounds = { minimum: [...patch.minimum], maximum: [...patch.maximum] }
  for (const axis of [0, 1] as const) {
    if (bounded[axis] && largestWidth[axis] <= 1) {
      // Four refined cells of verified skin border are enough for the flood
      // to establish closure without allocating the full 5 mm transverse pad.
      refined.minimum[axis] = Math.max(refined.minimum[axis], lower[axis] - coarseStepMm)
      refined.maximum[axis] = Math.min(refined.maximum[axis], upper[axis] + coarseStepMm)
    }
  }
  return { patch: refined }
}

/**
 * Bounded planar-skin prototype, not general CAD aperture detection. The
 * caller must exclude transformed scenes. Only actual coplanar triangles at
 * the provided whole-scene exit plane establish skin. The normal 0.1 mm grid
 * refines to 0.025 mm for narrow or unresolved openings; geometric line
 * intersections bound its transverse patch so thin seams remain affordable.
 * Both resolutions discard cells intersecting metal, including sub-cell
 * barriers, and components touching a patch edge. Curved/recessed faces,
 * unresolved sub-grid gaps and resource-limited scenes retain point fallback.
 */
export function buildLeakageApertureMasks(
  scene: ScenePayload,
  bounds: LeakagePreviewBounds,
  samples: readonly LeakagePreviewSample[],
): LeakageApertureMaskResult {
  const reject = (reason: string): LeakageApertureMaskResult => ({ masks: [], reason })
  if (
    scene.units.length !== 'mm' ||
    !finitePoint(bounds.minimum) ||
    !finitePoint(bounds.maximum) ||
    bounds.minimum.some((value, axis) => value >= bounds.maximum[axis])
  ) return reject('invalid_aperture_bounds')
  const { vertices, faces } = scene.mesh
  if (faces.length > maximumTriangles) return reject('aperture_triangle_limit')
  for (const triangle of faces) {
    if (
      triangle.length !== 3 ||
      triangle.some((index) => !Number.isSafeInteger(index) || index < 0 || !finitePoint(vertices[index]))
    ) return reject('invalid_aperture_mesh')
  }
  const masks: LeakageApertureMask[] = []
  // Includes attempted coarse and refined grids, not only retained output.
  const budget: RasterBudget = { cells: 0, checks: 0 }
  for (const face of Object.keys(faceAxes) as LeakageAabbFace[]) {
    const [normal, u, v] = faceAxes[face]
    const plane = face.endsWith('_min') ? bounds.minimum[normal] : bounds.maximum[normal]
    const faceSamples = samples.filter((sample) =>
      sample.exitFaces.length === 1 && sample.exitFaces[0] === face &&
      finitePoint(sample.exitPoint) && Number.isFinite(sample.weight) && sample.weight > 0 &&
      Math.abs(sample.exitPoint[normal] - plane) <= planeToleranceMm &&
      sample.exitPoint.every((value, axis) => value >= bounds.minimum[axis] && value <= bounds.maximum[axis]),
    )
    if (faceSamples.length === 0) continue
    const triangles: ProjectedTriangle[] = []
    for (const indices of faces) {
      const points = indices.map((index) => vertices[index])
      if (!points.every((point) => Math.abs(point[normal] - plane) <= planeToleranceMm)) continue
      const triangle = projectTriangle(points, u, v)
      if (triangle) triangles.push(triangle)
    }
    if (triangles.length === 0) continue
    const patch: PatchBounds = { minimum: [Infinity, Infinity], maximum: [-Infinity, -Infinity] }
    for (const sample of faceSamples) {
      for (const [axis, projected] of [[u, 0], [v, 1]] as const) {
        patch.minimum[projected] = Math.min(patch.minimum[projected], sample.exitPoint[axis] - paddingMm)
        patch.maximum[projected] = Math.max(patch.maximum[projected], sample.exitPoint[axis] + paddingMm)
      }
    }
    patch.minimum = [Math.max(bounds.minimum[u], patch.minimum[0]), Math.max(bounds.minimum[v], patch.minimum[1])]
    patch.maximum = [Math.min(bounds.maximum[u], patch.maximum[0]), Math.min(bounds.maximum[v], patch.maximum[1])]
    const gridAnchor: [number, number] = [stableGridAnchor(bounds.minimum[u]), stableGridAnchor(bounds.minimum[v])]
    const coarse = rasterMask(face, plane, patch, gridAnchor, coarseStepMm, triangles, budget)
    if (coarse.reason) return reject(coarse.reason)
    if (!coarse.mask) continue
    // Inspect the untouched conservative raster before unsupported components
    // are removed, so an unbounded broad hole alone does not request refinement.
    const refinement = refinementPatch(coarse.mask, patch, triangles, faceSamples, budget)
    if (refinement.reason) return reject(refinement.reason)
    const coarseSupported = retainSupportedHoles(coarse.mask, faceSamples)
    if (refinement.patch) {
      const refined = rasterMask(face, plane, refinement.patch, gridAnchor, refinedStepMm, triangles, budget)
      if (refined.reason && refined.reason !== 'aperture_cell_limit') return reject(refined.reason)
      if (refined.mask && retainSupportedHoles(refined.mask, faceSamples)) {
        const refinedMask = refined.mask
        const losesCoarseSupport = coarseSupported && faceSamples.some((sample) =>
          coarse.mask!.open[sampleCell(coarse.mask!, sample)] === 1 &&
          refinedMask.open[sampleCell(refinedMask, sample)] !== 1,
        )
        if (!losesCoarseSupport) {
          masks.push(refinedMask)
          continue
        }
      }
      // Optional refinement may exceed the remaining grid budget or encounter
      // patch-edge ambiguity. A previously verified coarse hole stays valid.
    }
    if (coarseSupported) masks.push(coarse.mask)
  }
  return masks.length > 0 ? { masks } : reject('no_supported_planar_aperture')
}
