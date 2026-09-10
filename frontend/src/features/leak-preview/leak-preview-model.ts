import type {
  EmitterSpec,
  RayTraceConfigRequest,
  RayTraceRequest,
  RayTraceResult,
  ReceiverGrid,
  ReceiverSpec,
  ScenePayload,
  Vec3,
} from '@/api'
import { buildRayTraceRequest, createFaceEmitter } from '@/features/raytracing'
import type {
  ComponentTransformRule,
  MaterialAssignment,
  RoiClipBox,
} from '@/stores'
import {
  createLeakPreviewPointTransform,
  getLeakPreviewBounds,
} from './leak-preview-geometry'

export { getLeakPreviewBounds } from './leak-preview-geometry'

export type LeakPreviewQuality = 'fast' | 'balanced' | 'deep'

export const leakPreviewRoiOffsetMm = 5
export const leakPreviewReceiverDistanceMm = 5

export interface LeakPreviewIgnoreArea {
  id: string
  label: string
  clipBox: RoiClipBox
  enabled: boolean
}

export interface LeakPreviewBlocker {
  id: string
  label: string
  enabled: boolean
  referenceFaceIds: number[]
  baseCenter: Vec3
  uAxis: Vec3
  vAxis: Vec3
  normal: Vec3
  widthMm: number
  heightMm: number
  offsetMm: number
  depthMm: number
  reverse: boolean
}

export interface LeakPreviewPoint {
  position: Vec3
  relativeStrength: number
}

export interface LeakPreviewCandidate {
  id: string
  label: string
  receiverId: string
  center: Vec3
  normal: Vec3
  uAxis: Vec3
  vAxis: Vec3
  widthMm: number
  heightMm: number
  fluxLumen: number
  relativeStrength: number
  cellCount: number
  clipBox: RoiClipBox
}

export interface LeakPreviewDetection {
  points: LeakPreviewPoint[]
  candidates: LeakPreviewCandidate[]
}

export interface BuildLeakPreviewRequestOptions {
  scene: ScenePayload
  sourceFaceIds: number[]
  quality: LeakPreviewQuality
  computeBackend: RayTraceConfigRequest['compute_backend']
  materialAssignments: MaterialAssignment[]
  transformRules: ComponentTransformRule[]
  excludedComponentIds: number[]
  deletedComponentIds: number[]
  blockers?: LeakPreviewBlocker[]
}

const directionLabels: Record<string, string> = {
  pos_x: 'Right',
  neg_x: 'Left',
  pos_y: 'Top',
  neg_y: 'Bottom',
  pos_z: 'Front',
  neg_z: 'Rear',
}

function addScaled(origin: Vec3, axis: Vec3, scale: number): Vec3 {
  return [
    origin[0] + axis[0] * scale,
    origin[1] + axis[1] * scale,
    origin[2] + axis[2] * scale,
  ]
}

function subtract(left: Vec3, right: Vec3): Vec3 {
  return [left[0] - right[0], left[1] - right[1], left[2] - right[2]]
}

function dot(left: Vec3, right: Vec3): number {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]
}

function cross(left: Vec3, right: Vec3): Vec3 {
  return [
    left[1] * right[2] - left[2] * right[1],
    left[2] * right[0] - left[0] * right[2],
    left[0] * right[1] - left[1] * right[0],
  ]
}

function normalized(value: Vec3): Vec3 {
  const length = Math.hypot(...value)
  return length > 1e-12
    ? [value[0] / length, value[1] / length, value[2] / length]
    : [0, 0, 0]
}

export function createLeakPreviewBlockerFromFaces(
  scene: ScenePayload,
  faceIds: number[],
  sequence: number,
): LeakPreviewBlocker | null {
  const suggestedLabels = ['Main Board', 'Power Board', 'Speaker']
  const points: Vec3[] = []
  let normal: Vec3 = [0, 0, 0]
  let referenceNormal: Vec3 | null = null
  let longestEdge: Vec3 = [1, 0, 0]
  let longestLength = 0
  for (const faceId of faceIds) {
    const face = scene.mesh.faces[faceId]
    const rawNormal = scene.mesh.face_normals[faceId]
    if (!face || !rawNormal) continue
    let currentNormal = normalized(rawNormal)
    if (!referenceNormal) referenceNormal = currentNormal
    if (dot(currentNormal, referenceNormal) < 0) {
      currentNormal = [-currentNormal[0], -currentNormal[1], -currentNormal[2]]
    }
    const weight = Math.max(scene.mesh.face_areas_mm2[faceId] ?? 0, 1e-6)
    normal = addScaled(normal, currentNormal, weight)
    const vertices = face.map((vertexId) => scene.mesh.vertices[vertexId]).filter(Boolean) as Vec3[]
    points.push(...vertices)
    for (let edgeIndex = 0; edgeIndex < vertices.length; edgeIndex += 1) {
      const edge = subtract(vertices[(edgeIndex + 1) % vertices.length], vertices[edgeIndex])
      const length = dot(edge, edge)
      if (length > longestLength) {
        longestLength = length
        longestEdge = edge
      }
    }
  }
  if (points.length === 0 || !referenceNormal) return null
  normal = normalized(normal)
  if (Math.hypot(...normal) < 0.5) return null
  const tolerance = Math.max(0.05, Math.sqrt(longestLength) * 0.002)
  const planeOrigin = points[0]
  if (points.some((point) => Math.abs(dot(subtract(point, planeOrigin), normal)) > tolerance)) {
    return null
  }
  let uAxis = normalized(subtract(longestEdge, addScaled([0, 0, 0], normal, dot(longestEdge, normal))))
  if (Math.hypot(...uAxis) < 0.5) {
    uAxis = normalized(cross(Math.abs(normal[2]) < 0.9 ? [0, 0, 1] : [0, 1, 0], normal))
  }
  const vAxis = normalized(cross(normal, uAxis))
  let minU = Infinity
  let maxU = -Infinity
  let minV = Infinity
  let maxV = -Infinity
  for (const point of points) {
    const relative = subtract(point, planeOrigin)
    const u = dot(relative, uAxis)
    const v = dot(relative, vAxis)
    minU = Math.min(minU, u)
    maxU = Math.max(maxU, u)
    minV = Math.min(minV, v)
    maxV = Math.max(maxV, v)
  }
  const baseCenter = addScaled(
    addScaled(planeOrigin, uAxis, (minU + maxU) / 2),
    vAxis,
    (minV + maxV) / 2,
  )
  return {
    id: `preview-blocker-${Date.now()}-${sequence}`,
    label: suggestedLabels[sequence - 1] ?? `Preview Blocker ${String(sequence).padStart(2, '0')}`,
    enabled: true,
    referenceFaceIds: [...new Set(faceIds)],
    baseCenter,
    uAxis,
    vAxis,
    normal,
    widthMm: Math.max(maxU - minU, 1),
    heightMm: Math.max(maxV - minV, 1),
    offsetMm: 0,
    depthMm: 1.5,
    reverse: false,
  }
}

export function leakPreviewBlockerCenter(blocker: LeakPreviewBlocker): Vec3 {
  const direction = blocker.reverse
    ? ([-blocker.normal[0], -blocker.normal[1], -blocker.normal[2]] as Vec3)
    : blocker.normal
  return addScaled(blocker.baseCenter, direction, blocker.offsetMm + blocker.depthMm / 2)
}

function createPreviewReceiver(
  id: string,
  center: Vec3,
  normal: Vec3,
  uAxis: Vec3,
  vAxis: Vec3,
  widthMm: number,
  heightMm: number,
): ReceiverSpec {
  const longest = Math.max(widthMm, heightMm, 1)
  const columns = Math.max(12, Math.round(44 * widthMm / longest))
  const rows = Math.max(12, Math.round(44 * heightMm / longest))
  return {
    receiver_id: `__leak_preview_${id}`,
    receiver_type: 'rectangle',
    display_name: `Preview ${directionLabels[id] ?? id}`,
    placement_mode: 'datum_plane',
    center,
    normal,
    u_axis: uAxis,
    v_axis: vAxis,
    width_mm: widthMm,
    height_mm: heightMm,
    resolution: [columns, rows],
    acceptance_angle_deg: 90,
    normal_flip: false,
    reference_mode: 'leak_preview_enclosure',
    reference_vertex_indices: [],
    reference_edge_vertex_indices: [],
    reference_vertex_points: [],
    reference_edge_points: [],
    view_distance_mm: null,
    base_center: center,
    base_u_axis: uAxis,
    base_v_axis: vAxis,
    base_normal: normal,
    position_offset_mm: [0, 0, 0],
    tilt_xyz_deg: [0, 0, 0],
    pivot: null,
    enabled: true,
  }
}

function detectorMarginMm(size: Vec3): number {
  return Math.max(2, Math.min(10, Math.max(...size, 1) * 0.01))
}

export function createLeakPreviewReceivers(
  scene: ScenePayload,
  transformRules: ComponentTransformRule[] = [],
): ReceiverSpec[] {
  const bounds = getLeakPreviewBounds(scene, transformRules)
  const center = bounds.center
  const size = bounds.size
  const margin = detectorMarginMm(size)
  const span = (value: number) => value + margin * 2
  return [
    createPreviewReceiver('pos_x', [center[0] + size[0] / 2 + margin, center[1], center[2]], [-1, 0, 0], [0, 1, 0], [0, 0, 1], span(size[1]), span(size[2])),
    createPreviewReceiver('neg_x', [center[0] - size[0] / 2 - margin, center[1], center[2]], [1, 0, 0], [0, -1, 0], [0, 0, 1], span(size[1]), span(size[2])),
    createPreviewReceiver('pos_y', [center[0], center[1] + size[1] / 2 + margin, center[2]], [0, -1, 0], [1, 0, 0], [0, 0, 1], span(size[0]), span(size[2])),
    createPreviewReceiver('neg_y', [center[0], center[1] - size[1] / 2 - margin, center[2]], [0, 1, 0], [-1, 0, 0], [0, 0, 1], span(size[0]), span(size[2])),
    createPreviewReceiver('pos_z', [center[0], center[1], center[2] + size[2] / 2 + margin], [0, 0, -1], [1, 0, 0], [0, 1, 0], span(size[0]), span(size[1])),
    createPreviewReceiver('neg_z', [center[0], center[1], center[2] - size[2] / 2 - margin], [0, 0, 1], [-1, 0, 0], [0, 1, 0], span(size[0]), span(size[1])),
  ]
}

function previewEmitter(
  id: string,
  faceIds: number[],
  normalFlip: boolean,
  rayCount: number,
): EmitterSpec {
  return {
    ...createFaceEmitter(id, faceIds),
    normal_flip: normalFlip,
    power_mode: 'total',
    power_lumen: 0.5,
    luminance_nit: undefined,
    ray_count: rayCount,
    seed: normalFlip ? 43 : 42,
  }
}

export function buildLeakPreviewRequest({
  scene,
  sourceFaceIds,
  quality,
  computeBackend,
  materialAssignments,
  transformRules,
  excludedComponentIds,
  deletedComponentIds,
  blockers = [],
}: BuildLeakPreviewRequestOptions): RayTraceRequest {
  const qualityConfig = {
    fast: { rayCount: 100_000, maxDepth: 3 },
    balanced: { rayCount: 500_000, maxDepth: 5 },
    deep: { rayCount: 1_000_000, maxDepth: 8 },
  }[quality]
  const totalRays = qualityConfig.rayCount
  const perSide = Math.max(1, Math.floor(totalRays / 2))
  const baseConfig: RayTraceConfigRequest = {
    ray_count: totalRays,
    max_depth: qualityConfig.maxDepth,
    seed: 42,
    min_energy: 1e-9,
    epsilon_mm: 0.001,
    k_abs: 1,
    k_brdf: 1,
    angle_dependent_reflectance: false,
    termination_mode: 'russian_roulette',
    contribution_mode: 'summary',
    intersection_backend: 'auto',
    compute_backend: computeBackend,
    store_ray_paths: false,
    max_stored_paths: 0,
    auto_convergence: false,
    primary_sampling_strategy: 'source',
    // The backend validates MIS fractions independently of the selected
    // source-only strategy, so retain a valid dormant value here.
    receiver_importance_fraction: 0.5,
    bounce_sampling_strategy: 'source',
    bounce_receiver_importance_fraction: 0.5,
  }
  const request = buildRayTraceRequest({
    scene,
    projectName: 'Whole Set Leak Preview',
    emitters: [
      previewEmitter('__leak_preview_source_a', sourceFaceIds, false, perSide),
      previewEmitter('__leak_preview_source_b', sourceFaceIds, true, totalRays - perSide),
    ],
    receivers: createLeakPreviewReceivers(scene, transformRules),
    materialAssignments,
    transformRules,
    excludedComponentIds,
    deletedComponentIds,
    roiScopes: [],
    config: baseConfig,
  })
  // Whole-set Preview is a risk-location search, not the final quantitative
  // solve. Reuse the display tessellation so a large STEP does not have to
  // materialize its precision trace mesh before the first Preview ray.
  request.geometry_mode = 'preview'
  request.preview_blockers = blockers
    .filter((blocker) => blocker.enabled)
    .map((blocker) => ({
      blocker_id: blocker.id,
      center: leakPreviewBlockerCenter(blocker),
      u_axis: blocker.uAxis,
      v_axis: blocker.vAxis,
      normal: blocker.reverse
        ? [-blocker.normal[0], -blocker.normal[1], -blocker.normal[2]]
        : blocker.normal,
      width_mm: blocker.widthMm,
      height_mm: blocker.heightMm,
      depth_mm: blocker.depthMm,
      enabled: true,
    }))
  // The production tracer deliberately treats unassigned surfaces as perfect
  // absorbers. Preview needs a neutral fallback so a novice can still locate
  // reflected leak paths without first completing Material Assignment.
  request.optical_profiles = [
    {
      profile_id: 'default',
      reflectance: 0.35,
      absorption: 0.65,
      specular_ratio: 0.3,
      diffuse_ratio: 0.7,
      scatter_model: 'mixed',
      roughness: 0.5,
      gaussian_sigma_deg: 18,
      bsdf_asset_id: null,
      notes: 'Whole-set Preview neutral fallback; relative location search only',
    },
    ...request.optical_profiles.filter((profile) => profile.profile_id !== 'default'),
  ]
  return request
}

function cellPosition(
  receiver: ReceiverSpec,
  grid: ReceiverGrid,
  row: number,
  column: number,
): Vec3 {
  const u = ((column + 0.5) / grid.resolution[0] - 0.5) * receiver.width_mm
  const v = ((row + 0.5) / grid.resolution[1] - 0.5) * receiver.height_mm
  return addScaled(
    addScaled(receiver.center, receiver.u_axis ?? [1, 0, 0], u),
    receiver.v_axis ?? [0, 1, 0],
    v,
  )
}

interface ActiveCell {
  row: number
  column: number
  flux: number
  position: Vec3
}

function connectedClusters(cells: ActiveCell[]): ActiveCell[][] {
  const byKey = new Map(cells.map((cell) => [`${cell.row}:${cell.column}`, cell]))
  const visited = new Set<string>()
  const clusters: ActiveCell[][] = []
  for (const cell of cells) {
    const startKey = `${cell.row}:${cell.column}`
    if (visited.has(startKey)) continue
    visited.add(startKey)
    const queue = [cell]
    const cluster: ActiveCell[] = []
    while (queue.length > 0) {
      const current = queue.pop()!
      cluster.push(current)
      for (let dr = -1; dr <= 1; dr += 1) {
        for (let dc = -1; dc <= 1; dc += 1) {
          if (dr === 0 && dc === 0) continue
          const key = `${current.row + dr}:${current.column + dc}`
          const neighbor = byKey.get(key)
          if (!neighbor || visited.has(key)) continue
          visited.add(key)
          queue.push(neighbor)
        }
      }
    }
    clusters.push(cluster)
  }
  return clusters
}

function candidateClipBox(
  cluster: ActiveCell[],
  receiver: ReceiverSpec,
  grid: ReceiverGrid,
  modelDepth: number,
): RoiClipBox {
  const cellWidth = receiver.width_mm / grid.resolution[0]
  const cellHeight = receiver.height_mm / grid.resolution[1]
  const points: Vec3[] = []
  const uPadding = cellWidth / 2 + leakPreviewRoiOffsetMm
  const vPadding = cellHeight / 2 + leakPreviewRoiOffsetMm
  for (const cell of cluster) {
    for (const depth of [0, modelDepth]) {
      for (const u of [-uPadding, uPadding]) {
        for (const v of [-vPadding, vPadding]) {
          points.push(addScaled(
            addScaled(addScaled(cell.position, receiver.u_axis ?? [1, 0, 0], u), receiver.v_axis ?? [0, 1, 0], v),
            receiver.normal,
            depth,
          ))
        }
      }
    }
  }
  const minimum: Vec3 = [Infinity, Infinity, Infinity]
  const maximum: Vec3 = [-Infinity, -Infinity, -Infinity]
  for (const point of points) {
    for (let axis = 0; axis < 3; axis += 1) {
      minimum[axis] = Math.min(minimum[axis], point[axis])
      maximum[axis] = Math.max(maximum[axis], point[axis])
    }
  }
  return {
    plane: 'xyz',
    xMin: minimum[0],
    xMax: maximum[0],
    yMin: minimum[1],
    yMax: maximum[1],
    zMin: minimum[2],
    zMax: maximum[2],
  }
}

function pointInIgnoreArea(point: Vec3, area: LeakPreviewIgnoreArea): boolean {
  if (!area.enabled) return false
  const box = area.clipBox
  if (box.plane === 'yz') {
    return point[1] >= box.yMin && point[1] <= box.yMax &&
      point[2] >= (box.zMin ?? -Infinity) && point[2] <= (box.zMax ?? Infinity)
  }
  if (box.plane === 'zx') {
    return point[2] >= (box.zMin ?? -Infinity) && point[2] <= (box.zMax ?? Infinity) &&
      point[0] >= box.xMin && point[0] <= box.xMax
  }
  return point[0] >= box.xMin && point[0] <= box.xMax &&
    point[1] >= box.yMin && point[1] <= box.yMax
}

function candidateDistance(left: LeakPreviewCandidate, right: LeakPreviewCandidate): number {
  return Math.hypot(
    left.center[0] - right.center[0],
    left.center[1] - right.center[1],
    left.center[2] - right.center[2],
  )
}

function mergeCornerCandidates(
  source: LeakPreviewCandidate[],
  maxDimension: number,
): LeakPreviewCandidate[] {
  const mergeDistance = Math.max(5, Math.min(25, maxDimension * 0.015))
  const merged: LeakPreviewCandidate[] = []
  for (const candidate of [...source].sort((a, b) => b.fluxLumen - a.fluxLumen)) {
    const target = merged.find((current) => {
      const normalDot = Math.abs(
        current.normal[0] * candidate.normal[0] +
        current.normal[1] * candidate.normal[1] +
        current.normal[2] * candidate.normal[2],
      )
      return normalDot < 0.5 && candidateDistance(current, candidate) <= mergeDistance
    })
    if (!target) {
      merged.push({ ...candidate })
      continue
    }
    const totalFlux = target.fluxLumen + candidate.fluxLumen
    target.center = target.center.map((value, axis) =>
      (value * target.fluxLumen + candidate.center[axis] * candidate.fluxLumen) /
      Math.max(totalFlux, 1e-30),
    ) as Vec3
    target.fluxLumen = totalFlux
    target.cellCount += candidate.cellCount
    target.label = [...new Set([...target.label.split(' / '), ...candidate.label.split(' / ')])].join(' / ')
    target.clipBox = {
      plane: 'xyz',
      xMin: Math.min(target.clipBox.xMin, candidate.clipBox.xMin),
      xMax: Math.max(target.clipBox.xMax, candidate.clipBox.xMax),
      yMin: Math.min(target.clipBox.yMin, candidate.clipBox.yMin),
      yMax: Math.max(target.clipBox.yMax, candidate.clipBox.yMax),
      zMin: Math.min(target.clipBox.zMin ?? Infinity, candidate.clipBox.zMin ?? Infinity),
      zMax: Math.max(target.clipBox.zMax ?? -Infinity, candidate.clipBox.zMax ?? -Infinity),
    }
  }
  return merged
}

interface LeakPreviewDetectionOptions {
  quality?: LeakPreviewQuality
  ignoreAreas?: LeakPreviewIgnoreArea[]
  transformRules?: ComponentTransformRule[]
}

export function detectLeakPreviewCandidates(
  scene: ScenePayload,
  result: RayTraceResult,
  options: LeakPreviewDetectionOptions = {},
): LeakPreviewDetection {
  const receivers = new Map(result.receivers.map((item) => [item.receiver_id, item]))
  const globalMaximum = Math.max(
    0,
    ...result.receiver_grids.flatMap((grid) => grid.flux_lumen.flat()),
  )
  if (globalMaximum <= 0) return { points: [], candidates: [] }

  const bounds = getLeakPreviewBounds(scene, options.transformRules)
  const maxDimension = Math.max(...bounds.size, 1)
  const modelDepth = maxDimension * 0.18
  const detectorMargin = detectorMarginMm(bounds.size)
  const thresholdRatio = {
    fast: { global: 0.04, local: 0.12 },
    balanced: { global: 0.025, local: 0.08 },
    deep: { global: 0.01, local: 0.04 },
  }[options.quality ?? 'balanced']
  const ignoreAreas = options.ignoreAreas ?? []
  const points: LeakPreviewPoint[] = []
  const candidates: LeakPreviewCandidate[] = []
  for (const grid of result.receiver_grids) {
    const receiver = receivers.get(grid.receiver_id)
    if (!receiver) continue
    const localMaximum = Math.max(0, ...grid.flux_lumen.flat())
    const threshold = Math.max(
      globalMaximum * thresholdRatio.global,
      localMaximum * thresholdRatio.local,
    )
    const active: ActiveCell[] = []
    for (let row = 0; row < grid.resolution[1]; row += 1) {
      for (let column = 0; column < grid.resolution[0]; column += 1) {
        const flux = Number(grid.flux_lumen[row]?.[column]) || 0
        if (flux < threshold) continue
        // The automatic Receiver sits outside the CAD. Move its bin back near
        // the model envelope so the glow reads as a leak on the product, not
        // as a detached heatmap plane floating around it.
        const position = addScaled(
          cellPosition(receiver, grid, row, column),
          receiver.normal,
          detectorMargin,
        )
        if (ignoreAreas.some((area) => pointInIgnoreArea(position, area))) continue
        active.push({ row, column, flux, position })
        points.push({ position, relativeStrength: flux / globalMaximum })
      }
    }
    for (const cluster of connectedClusters(active)) {
      const fluxLumen = cluster.reduce((sum, cell) => sum + cell.flux, 0)
      const weightedCenter = cluster.reduce<Vec3>(
        (sum, cell) => [
          sum[0] + cell.position[0] * cell.flux,
          sum[1] + cell.position[1] * cell.flux,
          sum[2] + cell.position[2] * cell.flux,
        ],
        [0, 0, 0],
      ).map((value) => value / Math.max(fluxLumen, 1e-30)) as Vec3
      const columns = cluster.map((cell) => cell.column)
      const rows = cluster.map((cell) => cell.row)
      const cellWidth = receiver.width_mm / grid.resolution[0]
      const cellHeight = receiver.height_mm / grid.resolution[1]
      candidates.push({
        id: `${grid.receiver_id}:${Math.min(...rows)}:${Math.min(...columns)}`,
        label: directionLabels[grid.receiver_id.replace('__leak_preview_', '')] ?? receiver.display_name,
        receiverId: grid.receiver_id,
        center: weightedCenter,
        normal: receiver.normal,
        uAxis: receiver.u_axis ?? [1, 0, 0],
        vAxis: receiver.v_axis ?? [0, 1, 0],
        widthMm: (Math.max(...columns) - Math.min(...columns) + 1) * cellWidth + leakPreviewRoiOffsetMm * 2,
        heightMm: (Math.max(...rows) - Math.min(...rows) + 1) * cellHeight + leakPreviewRoiOffsetMm * 2,
        fluxLumen,
        relativeStrength: 0,
        cellCount: cluster.length,
        clipBox: candidateClipBox(cluster, receiver, grid, modelDepth),
      })
    }
  }
  const mergedCandidates = mergeCornerCandidates(candidates, maxDimension)
    .sort((left, right) => right.fluxLumen - left.fluxLumen)
  const strongest = mergedCandidates[0]?.fluxLumen ?? 1
  return {
    points: points
      .sort((left, right) => right.relativeStrength - left.relativeStrength)
      .slice(0, 800),
    candidates: mergedCandidates.slice(0, 8).map((candidate, index) => ({
      ...candidate,
      id: `leak-candidate-${index + 1}`,
      relativeStrength: candidate.fluxLumen / strongest,
    })),
  }
}

function boxesOverlap(
  minimum: Vec3,
  maximum: Vec3,
  clip: RoiClipBox,
): boolean {
  return !(
    maximum[0] < clip.xMin || minimum[0] > clip.xMax ||
    maximum[1] < clip.yMin || minimum[1] > clip.yMax ||
    maximum[2] < (clip.zMin ?? -Infinity) ||
    minimum[2] > (clip.zMax ?? Infinity)
  )
}

/** Candidate-local face lookup. Component boxes reject unrelated full-set
 * geometry before triangle bounds are inspected, which avoids a complete
 * multi-million-face scan for a small exterior leak candidate. */
export function resolveLeakPreviewRoiFaces(
  scene: ScenePayload,
  clip: RoiClipBox,
  hiddenComponentIds: Iterable<number>,
  deletedComponentIds: Iterable<number>,
  transformRules: ComponentTransformRule[] = [],
): number[] {
  const unavailable = new Set([...hiddenComponentIds, ...deletedComponentIds])
  const result: number[] = []
  const transformPoint = createLeakPreviewPointTransform(scene, transformRules)
  for (const component of scene.components) {
    if (unavailable.has(component.component_id)) continue
    const componentMinimum: Vec3 = [Infinity, Infinity, Infinity]
    const componentMaximum: Vec3 = [-Infinity, -Infinity, -Infinity]
    for (const x of [component.bbox_min[0], component.bbox_max[0]]) {
      for (const y of [component.bbox_min[1], component.bbox_max[1]]) {
        for (const z of [component.bbox_min[2], component.bbox_max[2]]) {
          const point = transformPoint(component.component_id, [x, y, z])
          for (let axis = 0; axis < 3; axis += 1) {
            componentMinimum[axis] = Math.min(componentMinimum[axis], point[axis])
            componentMaximum[axis] = Math.max(componentMaximum[axis], point[axis])
          }
        }
      }
    }
    if (!boxesOverlap(componentMinimum, componentMaximum, clip)) continue
    for (const faceId of component.face_indices) {
      const face = scene.mesh.faces[faceId]
      if (!face) continue
      const minimum: Vec3 = [Infinity, Infinity, Infinity]
      const maximum: Vec3 = [-Infinity, -Infinity, -Infinity]
      for (const vertexId of face) {
        const originalVertex = scene.mesh.vertices[vertexId]
        const vertex = originalVertex
          ? transformPoint(component.component_id, originalVertex)
          : undefined
        if (!vertex) continue
        for (let axis = 0; axis < 3; axis += 1) {
          minimum[axis] = Math.min(minimum[axis], vertex[axis])
          maximum[axis] = Math.max(maximum[axis], vertex[axis])
        }
      }
      if (boxesOverlap(minimum, maximum, clip)) result.push(faceId)
    }
  }
  return result
}

export function createCandidateReceiver(
  candidate: LeakPreviewCandidate,
  index: number,
): ReceiverSpec {
  const pixelSize = Math.max(candidate.widthMm, candidate.heightMm) / 40
  const center = addScaled(
    candidate.center,
    candidate.normal,
    -leakPreviewReceiverDistanceMm,
  )
  return {
    ...createPreviewReceiver(
      `candidate_${index}`,
      center,
      candidate.normal,
      candidate.uAxis,
      candidate.vAxis,
      candidate.widthMm,
      candidate.heightMm,
    ),
    receiver_id: `preview_receiver_${String(index).padStart(3, '0')}`,
    display_name: `Preview Receiver ${index}`,
    reference_mode: 'leak_preview_candidate',
    view_distance_mm: leakPreviewReceiverDistanceMm,
    base_center: [...candidate.center],
    base_u_axis: [...candidate.uAxis],
    base_v_axis: [...candidate.vAxis],
    base_normal: [...candidate.normal],
    resolution: [
      Math.max(8, Math.min(120, Math.round(candidate.widthMm / pixelSize))),
      Math.max(8, Math.min(120, Math.round(candidate.heightMm / pixelSize))),
    ],
  }
}
