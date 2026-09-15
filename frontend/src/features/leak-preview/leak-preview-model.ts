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
export type LeakPreviewDirection = 'pos_x' | 'neg_x' | 'pos_y' | 'neg_y' | 'pos_z' | 'neg_z'

export const allLeakPreviewDirections: LeakPreviewDirection[] = [
  'pos_z', 'neg_z', 'pos_x', 'neg_x', 'pos_y', 'neg_y',
]

export const leakPreviewRoiOffsetMm = 5
export const leakPreviewReceiverDistanceMm = 5
export const leakPreviewAllowedAreaPaddingMm = 5

export interface LeakPreviewIgnoreArea {
  id: string
  label: string
  regions: RoiClipBox[]
  enabled: boolean
}

export interface LeakPreviewBlocker {
  id: string
  label: string
  enabled: boolean
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
  peakFluxLumen: number
  relativeStrength: number
  cellCount: number
  sampledPathCount: number
  grazingPathCount: number
  minReflectionCount: number | null
  maxReflectionCount: number | null
  meanExitAngleDeg: number | null
  clipBox: RoiClipBox
}

export interface LeakPreviewDetection {
  points: LeakPreviewPoint[]
  candidates: LeakPreviewCandidate[]
}

export interface BuildLeakPreviewRequestOptions {
  scene: ScenePayload
  sourceFaceIds: number[]
  sourceComponentIds?: number[]
  quality: LeakPreviewQuality
  computeBackend: RayTraceConfigRequest['compute_backend']
  materialAssignments: MaterialAssignment[]
  transformRules: ComponentTransformRule[]
  excludedComponentIds: number[]
  deletedComponentIds: number[]
  blockers?: LeakPreviewBlocker[]
  directions?: LeakPreviewDirection[]
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
  let normal: Vec3 = [0, 0, 0]
  let referenceNormal: Vec3 | null = null
  let planeOrigin: Vec3 | null = null
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
    const firstPoint = scene.mesh.vertices[face[0]]
    if (!planeOrigin && firstPoint) planeOrigin = firstPoint
    for (let edgeIndex = 0; edgeIndex < face.length; edgeIndex += 1) {
      const start = scene.mesh.vertices[face[edgeIndex]]
      const end = scene.mesh.vertices[face[(edgeIndex + 1) % face.length]]
      if (!start || !end) continue
      const edge = subtract(end, start)
      const length = dot(edge, edge)
      if (length > longestLength) {
        longestLength = length
        longestEdge = edge
      }
    }
  }
  if (!planeOrigin || !referenceNormal) return null
  normal = normalized(normal)
  if (Math.hypot(...normal) < 0.5) return null
  const tolerance = Math.max(0.05, Math.sqrt(longestLength) * 0.002)
  let uAxis = normalized(subtract(longestEdge, addScaled([0, 0, 0], normal, dot(longestEdge, normal))))
  if (Math.hypot(...uAxis) < 0.5) {
    uAxis = normalized(cross(Math.abs(normal[2]) < 0.9 ? [0, 0, 1] : [0, 1, 0], normal))
  }
  const vAxis = normalized(cross(normal, uAxis))
  let minU = Infinity
  let maxU = -Infinity
  let minV = Infinity
  let maxV = -Infinity
  // Stream the selected tessellation instead of copying every triangle
  // vertex into a second array. A single large CAD surface can contain
  // hundreds of thousands of triangles, and that copy used to freeze the UI
  // when the user pressed "선택 완료".
  for (const faceId of faceIds) {
    const face = scene.mesh.faces[faceId]
    if (!face) continue
    for (const vertexId of face) {
      const point = scene.mesh.vertices[vertexId]
      if (!point) continue
      const relative = subtract(point, planeOrigin)
      if (Math.abs(dot(relative, normal)) > tolerance) return null
      const u = dot(relative, uAxis)
      const v = dot(relative, vAxis)
      minU = Math.min(minU, u)
      maxU = Math.max(maxU, u)
      minV = Math.min(minV, v)
      maxV = Math.max(maxV, v)
    }
  }
  if (![minU, maxU, minV, maxV].every(Number.isFinite)) return null
  const baseCenter = addScaled(
    addScaled(planeOrigin, uAxis, (minU + maxU) / 2),
    vAxis,
    (minV + maxV) / 2,
  )
  return {
    id: `preview-blocker-${Date.now()}-${sequence}`,
    label: suggestedLabels[sequence - 1] ?? `Preview Blocker ${String(sequence).padStart(2, '0')}`,
    enabled: true,
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

/** Resize/reposition a blocker from four world-space points projected onto
 * its reference CAD surface. The normal, offset and depth remain unchanged. */
export function resizeLeakPreviewBlockerOnFace(
  blocker: LeakPreviewBlocker,
  points: Vec3[],
): Partial<LeakPreviewBlocker> | null {
  if (points.length < 2) return null
  const uAxis = normalized(blocker.uAxis)
  const vAxis = normalized(blocker.vAxis)
  const coordinates = points.map((point) => {
    const relative = subtract(point, blocker.baseCenter)
    return [dot(relative, uAxis), dot(relative, vAxis)] as const
  })
  const uValues = coordinates.map(([u]) => u)
  const vValues = coordinates.map(([, v]) => v)
  const minU = Math.min(...uValues)
  const maxU = Math.max(...uValues)
  const minV = Math.min(...vValues)
  const maxV = Math.max(...vValues)
  if (![minU, maxU, minV, maxV].every(Number.isFinite)) return null
  const widthMm = maxU - minU
  const heightMm = maxV - minV
  if (widthMm < 0.1 || heightMm < 0.1) return null
  return {
    baseCenter: addScaled(
      addScaled(blocker.baseCenter, uAxis, (minU + maxU) / 2),
      vAxis,
      (minV + maxV) / 2,
    ),
    widthMm,
    heightMm,
  }
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
  const columns = Math.max(16, Math.round(72 * widthMm / longest))
  const rows = Math.max(16, Math.round(72 * heightMm / longest))
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
  directions: LeakPreviewDirection[] = allLeakPreviewDirections,
): ReceiverSpec[] {
  const bounds = getLeakPreviewBounds(scene, transformRules)
  const center = bounds.center
  const size = bounds.size
  const margin = detectorMarginMm(size)
  // A selected exterior direction represents a viewing hemisphere rather
  // than a CAD-sized stencil. Expand the escape envelope by a 45-degree
  // guard cone
  // so oblique corner leaks still reach (for example) the Front detector.
  const span = (value: number, travelDepth: number) =>
    value + 2 * (margin + Math.max(travelDepth, 0) * Math.tan(45 * Math.PI / 180))
  const enabledDirections = new Set(directions)
  return [
    createPreviewReceiver('pos_x', [center[0] + size[0] / 2 + margin, center[1], center[2]], [-1, 0, 0], [0, 1, 0], [0, 0, 1], span(size[1], size[0]), span(size[2], size[0])),
    createPreviewReceiver('neg_x', [center[0] - size[0] / 2 - margin, center[1], center[2]], [1, 0, 0], [0, -1, 0], [0, 0, 1], span(size[1], size[0]), span(size[2], size[0])),
    createPreviewReceiver('pos_y', [center[0], center[1] + size[1] / 2 + margin, center[2]], [0, -1, 0], [1, 0, 0], [0, 0, 1], span(size[0], size[1]), span(size[2], size[1])),
    createPreviewReceiver('neg_y', [center[0], center[1] - size[1] / 2 - margin, center[2]], [0, 1, 0], [-1, 0, 0], [0, 0, 1], span(size[0], size[1]), span(size[2], size[1])),
    createPreviewReceiver('pos_z', [center[0], center[1], center[2] + size[2] / 2 + margin], [0, 0, -1], [1, 0, 0], [0, 1, 0], span(size[0], size[2]), span(size[1], size[2])),
    createPreviewReceiver('neg_z', [center[0], center[1], center[2] - size[2] / 2 - margin], [0, 0, 1], [-1, 0, 0], [0, 1, 0], span(size[0], size[2]), span(size[1], size[2])),
  ].filter((receiver) =>
    enabledDirections.has(receiver.receiver_id.replace('__leak_preview_', '') as LeakPreviewDirection),
  )
}

function previewEmitter(
  id: string,
  faceIds: number[],
  componentIds: number[],
  normalFlip: boolean,
  rayCount: number,
): EmitterSpec {
  return {
    ...createFaceEmitter(id, faceIds),
    ...(componentIds.length > 0 ? { source_component_ids: componentIds } : {}),
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
  sourceComponentIds = [],
  quality,
  computeBackend,
  materialAssignments,
  transformRules,
  excludedComponentIds,
  deletedComponentIds,
  blockers = [],
  directions = allLeakPreviewDirections,
}: BuildLeakPreviewRequestOptions): RayTraceRequest {
  const qualityConfig = {
    fast: { rayCount: 100_000, maxDepth: 3 },
    balanced: { rayCount: 500_000, maxDepth: 5 },
    deep: { rayCount: 1_000_000, maxDepth: 20 },
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
    // A bounded receiver-path sample lets Preview reconstruct the actual
    // model-envelope exit location rather than drawing every point on the
    // remote virtual detector plane.
    store_ray_paths: true,
    max_stored_paths: 4_000,
    auto_convergence: false,
    // Preview uses the selected exterior envelope as an importance target.
    // The MIS weights preserve energy while spending substantially more
    // samples on weak escape paths in the chosen viewing direction.
    primary_sampling_strategy: 'receiver_mis',
    receiver_importance_fraction: 0.65,
    bounce_sampling_strategy: 'receiver_mis',
    bounce_receiver_importance_fraction: 0.55,
  }
  const request = buildRayTraceRequest({
    scene,
    projectName: 'Whole Set Leak Preview',
    emitters: [
      previewEmitter('__leak_preview_source_a', sourceFaceIds, sourceComponentIds, false, perSide),
      previewEmitter('__leak_preview_source_b', sourceFaceIds, sourceComponentIds, true, totalRays - perSide),
    ],
    receivers: createLeakPreviewReceivers(scene, transformRules, directions),
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
  sample?: LeakPreviewEscapeSample
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

function pointInIgnoreRegion(point: Vec3, box: RoiClipBox): boolean {
  // Exit locations reconstructed from tessellated paths can land slightly
  // outside the user-drawn opening. Suppress a small perimeter as part of the
  // allowed opening so mesh/bin jitter does not become a false leak ring.
  const padding = leakPreviewAllowedAreaPaddingMm
  if (box.plane === 'yz') {
    return point[1] >= box.yMin - padding && point[1] <= box.yMax + padding &&
      point[2] >= (box.zMin ?? -Infinity) - padding && point[2] <= (box.zMax ?? Infinity) + padding
  }
  if (box.plane === 'zx') {
    return point[2] >= (box.zMin ?? -Infinity) - padding && point[2] <= (box.zMax ?? Infinity) + padding &&
      point[0] >= box.xMin - padding && point[0] <= box.xMax + padding
  }
  return point[0] >= box.xMin - padding && point[0] <= box.xMax + padding &&
    point[1] >= box.yMin - padding && point[1] <= box.yMax + padding
}

function pointInIgnoreArea(point: Vec3, area: LeakPreviewIgnoreArea): boolean {
  return area.enabled && area.regions.some((region) => pointInIgnoreRegion(point, region))
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
    target.peakFluxLumen = Math.max(target.peakFluxLumen, candidate.peakFluxLumen)
    target.cellCount += candidate.cellCount
    const combinedSampleCount = target.sampledPathCount + candidate.sampledPathCount
    target.meanExitAngleDeg = combinedSampleCount > 0
      ? (
          (target.meanExitAngleDeg ?? 0) * target.sampledPathCount +
          (candidate.meanExitAngleDeg ?? 0) * candidate.sampledPathCount
        ) / combinedSampleCount
      : null
    target.sampledPathCount = combinedSampleCount
    target.grazingPathCount += candidate.grazingPathCount
    target.minReflectionCount = target.minReflectionCount === null
      ? candidate.minReflectionCount
      : candidate.minReflectionCount === null
        ? target.minReflectionCount
        : Math.min(target.minReflectionCount, candidate.minReflectionCount)
    target.maxReflectionCount = target.maxReflectionCount === null
      ? candidate.maxReflectionCount
      : candidate.maxReflectionCount === null
        ? target.maxReflectionCount
        : Math.max(target.maxReflectionCount, candidate.maxReflectionCount)
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

function receiverGridCell(
  receiver: ReceiverSpec,
  grid: ReceiverGrid,
  point: Vec3,
): [number, number] | null {
  const relative = subtract(point, receiver.center)
  const u = dot(relative, receiver.u_axis ?? [1, 0, 0])
  const v = dot(relative, receiver.v_axis ?? [0, 1, 0])
  const column = Math.floor((u / receiver.width_mm + 0.5) * grid.resolution[0])
  const row = Math.floor((v / receiver.height_mm + 0.5) * grid.resolution[1])
  return row >= 0 && row < grid.resolution[1] && column >= 0 && column < grid.resolution[0]
    ? [row, column]
    : null
}

function segmentExitFromBounds(start: Vec3, end: Vec3, minimum: Vec3, maximum: Vec3): Vec3 | null {
  const direction = subtract(end, start)
  let enter = 0
  let exit = 1
  for (let axis = 0; axis < 3; axis += 1) {
    if (Math.abs(direction[axis]) < 1e-12) {
      if (start[axis] < minimum[axis] || start[axis] > maximum[axis]) return null
      continue
    }
    const first = (minimum[axis] - start[axis]) / direction[axis]
    const second = (maximum[axis] - start[axis]) / direction[axis]
    enter = Math.max(enter, Math.min(first, second))
    exit = Math.min(exit, Math.max(first, second))
    if (enter > exit) return null
  }
  if (exit < 0 || exit > 1) return null
  return addScaled(start, direction, exit)
}

interface LeakPreviewEscapeSample {
  position: Vec3
  sampleCount: number
  grazingSampleCount: number
  minReflectionCount: number
  maxReflectionCount: number
  meanExitAngleDeg: number
}

interface AccumulatedEscapeSample {
  position: Vec3
  weight: number
  sampleCount: number
  grazingSampleCount: number
  minReflectionCount: number
  maxReflectionCount: number
  angleSumDeg: number
}

function receiverEnvelopeIntersection(
  receiverId: string,
  start: Vec3,
  end: Vec3,
  minimum: Vec3,
  maximum: Vec3,
): Vec3 | null {
  const directionId = receiverId.replace('__leak_preview_', '') as LeakPreviewDirection
  const boundaryByDirection: Partial<Record<LeakPreviewDirection, [number, number]>> = {
    pos_x: [0, maximum[0]],
    neg_x: [0, minimum[0]],
    pos_y: [1, maximum[1]],
    neg_y: [1, minimum[1]],
    pos_z: [2, maximum[2]],
    neg_z: [2, minimum[2]],
  }
  const boundary = boundaryByDirection[directionId]
  if (!boundary) return null
  const [axis, coordinate] = boundary
  const delta = end[axis] - start[axis]
  if (Math.abs(delta) < 1e-12) return null
  const t = (coordinate - start[axis]) / delta
  if (t < 0 || t > 1) return null
  return addScaled(start, subtract(end, start), t)
}

function sampledEscapeLocations(
  result: RayTraceResult,
  receivers: Map<string, ReceiverSpec>,
  grids: Map<string, ReceiverGrid>,
  minimum: Vec3,
  maximum: Vec3,
): Map<string, LeakPreviewEscapeSample> {
  const accumulated = new Map<string, AccumulatedEscapeSample>()
  for (const path of result.stored_paths) {
    const receiverHit = path.at(-1)
    const previous = path.at(-2)
    if (!receiverHit?.receiver_id || receiverHit.event_type !== 'receiver' || !previous) continue
    const receiver = receivers.get(receiverHit.receiver_id)
    const grid = grids.get(receiverHit.receiver_id)
    if (!receiver || !grid) continue
    const cell = receiverGridCell(receiver, grid, receiverHit.point)
    const exitPoint = receiverEnvelopeIntersection(
      receiver.receiver_id,
      previous.point,
      receiverHit.point,
      minimum,
      maximum,
    ) ?? segmentExitFromBounds(previous.point, receiverHit.point, minimum, maximum)
    if (!cell || !exitPoint) continue
    const key = `${receiver.receiver_id}:${cell[0]}:${cell[1]}`
    const weight = Math.max(receiverHit.receiver_flux_lumen ?? receiverHit.incoming_energy_lumen, 1e-30)
    const rayDirection = normalized(subtract(receiverHit.point, previous.point))
    const outwardNormal = receiver.normal.map((value) => -value) as Vec3
    const exitAngleDeg = Math.acos(Math.max(-1, Math.min(1, dot(rayDirection, outwardNormal)))) * 180 / Math.PI
    const reflectionCount = Math.max(
      receiverHit.depth ?? 0,
      path.filter((hit) => hit.event_type === 'surface').length,
    )
    const grazing = exitAngleDeg >= 59.5 && exitAngleDeg < 90
    const current = accumulated.get(key)
    if (!current) {
      accumulated.set(key, {
        position: exitPoint.map((value) => value * weight) as Vec3,
        weight,
        sampleCount: 1,
        grazingSampleCount: grazing ? 1 : 0,
        minReflectionCount: reflectionCount,
        maxReflectionCount: reflectionCount,
        angleSumDeg: exitAngleDeg,
      })
      continue
    }
    current.position = current.position.map((value, axis) => value + exitPoint[axis] * weight) as Vec3
    current.weight += weight
    current.sampleCount += 1
    current.grazingSampleCount += grazing ? 1 : 0
    current.minReflectionCount = Math.min(current.minReflectionCount, reflectionCount)
    current.maxReflectionCount = Math.max(current.maxReflectionCount, reflectionCount)
    current.angleSumDeg += exitAngleDeg
  }
  return new Map([...accumulated].map(([key, value]) => [
    key,
    {
      position: value.position.map((coordinate) => coordinate / value.weight) as Vec3,
      sampleCount: value.sampleCount,
      grazingSampleCount: value.grazingSampleCount,
      minReflectionCount: value.minReflectionCount,
      maxReflectionCount: value.maxReflectionCount,
      meanExitAngleDeg: value.angleSumDeg / value.sampleCount,
    },
  ]))
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
  const boundsMinimum = bounds.center.map((value, axis) => value - bounds.size[axis] / 2) as Vec3
  const boundsMaximum = bounds.center.map((value, axis) => value + bounds.size[axis] / 2) as Vec3
  const grids = new Map(result.receiver_grids.map((grid) => [grid.receiver_id, grid]))
  const escapeLocations = sampledEscapeLocations(
    result,
    receivers,
    grids,
    boundsMinimum,
    boundsMaximum,
  )
  const maxDimension = Math.max(...bounds.size, 1)
  const modelDepth = maxDimension * 0.18
  const detectorMargin = detectorMarginMm(bounds.size)
  const thresholdRatio = {
    fast: { global: 0.015, local: 0.06 },
    balanced: { global: 0.006, local: 0.025 },
    deep: { global: 0.002, local: 0.01 },
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
        const sample = escapeLocations.get(`${grid.receiver_id}:${row}:${column}`)
        const preserveFrontGrazingPath = grid.receiver_id === '__leak_preview_pos_z' &&
          (sample?.grazingSampleCount ?? 0) > 0
        if (flux < threshold && !preserveFrontGrazingPath) continue
        // The automatic Receiver sits outside the CAD. Move its bin back near
        // the model envelope so the glow reads as a leak on the product, not
        // as a detached heatmap plane floating around it.
        const position = sample?.position ?? addScaled(
          cellPosition(receiver, grid, row, column),
          receiver.normal,
          detectorMargin,
        )
        if (ignoreAreas.some((area) => pointInIgnoreArea(position, area))) continue
        active.push({ row, column, flux, position, sample })
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
      const sampledCells = cluster.flatMap((cell) => cell.sample ? [cell.sample] : [])
      const sampledPathCount = sampledCells.reduce((sum, sample) => sum + sample.sampleCount, 0)
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
        peakFluxLumen: Math.max(...cluster.map((cell) => cell.flux)),
        relativeStrength: 0,
        cellCount: cluster.length,
        sampledPathCount,
        grazingPathCount: sampledCells.reduce((sum, sample) => sum + sample.grazingSampleCount, 0),
        minReflectionCount: sampledPathCount > 0
          ? Math.min(...sampledCells.map((sample) => sample.minReflectionCount))
          : null,
        maxReflectionCount: sampledPathCount > 0
          ? Math.max(...sampledCells.map((sample) => sample.maxReflectionCount))
          : null,
        meanExitAngleDeg: sampledPathCount > 0
          ? sampledCells.reduce((sum, sample) => sum + sample.meanExitAngleDeg * sample.sampleCount, 0) / sampledPathCount
          : null,
        clipBox: candidateClipBox(cluster, receiver, grid, modelDepth),
      })
    }
  }
  const mergedCandidates = mergeCornerCandidates(candidates, maxDimension)
  const strongestFlux = Math.max(1e-30, ...mergedCandidates.map((candidate) => candidate.fluxLumen))
  const strongestPeak = Math.max(1e-30, ...mergedCandidates.map((candidate) => candidate.peakFluxLumen))
  // Preserve both narrow/bright spots and broad leakage regions. Ranking by
  // total flux alone allowed large benign areas to consume all eight slots.
  const rankedCandidates = mergedCandidates
    .map((candidate) => ({
      ...candidate,
      relativeStrength:
        0.65 * (candidate.peakFluxLumen / strongestPeak) +
        0.35 * (candidate.fluxLumen / strongestFlux),
    }))
    .sort((left, right) => right.relativeStrength - left.relativeStrength)
  const topCandidates = rankedCandidates.slice(0, 8)
  const strongestFrontGrazing = rankedCandidates.find((candidate) =>
    candidate.receiverId === '__leak_preview_pos_z' && candidate.grazingPathCount > 0,
  )
  if (strongestFrontGrazing && !topCandidates.includes(strongestFrontGrazing)) {
    topCandidates.splice(Math.max(0, topCandidates.length - 1), 1, strongestFrontGrazing)
    topCandidates.sort((left, right) => right.relativeStrength - left.relativeStrength)
  }
  return {
    points: points
      .sort((left, right) => right.relativeStrength - left.relativeStrength)
      .slice(0, 800),
    candidates: topCandidates.map((candidate, index) => ({
      ...candidate,
      id: `leak-candidate-${index + 1}`,
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
