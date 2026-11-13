import { planObserverSampling, type ObserverSamplingMode } from './observer-sampling'
import { leakPathPattern, similarLeakPathPatterns } from './leak-path-pattern'
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
import { buildRayTraceRequest, createFaceEmitter, distributeIntegerBudget } from '@/features/raytracing/ray-tracing-model'
import { createViewAngleReceiver } from '@/features/raytracing/receiver-view'
import type {
  ComponentTransformRule,
  MaterialAssignment,
  RoiClipBox,
} from '@/stores'
import {
  createLeakPreviewPointTransform,
  getLeakPreviewBounds,
} from './leak-preview-geometry'

import type { BsdfAsset } from '@/features/materials/bsdf-asset'
import { expandBlockerGroups } from './blocker-groups'
import { buildPrecisionPreviewConstraints } from '@/features/raytracing/precision-preview-constraints'

export { getLeakPreviewBounds } from './leak-preview-geometry'

export type LeakPreviewQuality = 'fast' | 'balanced' | 'deep'
export type LeakPreviewDirection = 'pos_x' | 'neg_x' | 'pos_y' | 'neg_y' | 'pos_z' | 'neg_z'

export const allLeakPreviewDirections: LeakPreviewDirection[] = [
  'pos_z', 'neg_z', 'pos_x', 'neg_x', 'pos_y', 'neg_y',
]

export const leakPreviewRoiOffsetMm = 5
export const leakPreviewRoiMinimumSizeMm = 30
export const leakPreviewReceiverDistanceMm = 3
// Keep the generated Receiver inside the ROI footprint, with 2 mm clearance
// from each edge instead of placing its bins on the clipping boundary.
export const leakPreviewReceiverOffsetMm = 2
export const leakPreviewAllowedAreaPaddingMm = 5

export interface LeakPreviewIgnoreArea {
  id: string
  label: string
  regions: RoiClipBox[]
  enabled: boolean
  visible?: boolean
}

export interface LeakPreviewBlocker {
  sequence?: number
  shape?: 'box' | 'ellipse'
  regions?: LeakPreviewBlocker[]
  visible?: boolean
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

/**
 * Case-scoped Leak Preview inputs that must survive Case switches and project
 * save/load. Runtime jobs, sampled points, and selection gestures are omitted:
 * they are derived/transient state and must never be replayed as active work.
 */
export interface LeakPreviewObserverSettings {
  measurement?: 'paths' | 'radiance'
  min_luminance_nit?: number
  refine_angles?: boolean
  coverage?: 'local' | 'hemisphere'
  search_mode?: 'preview' | 'full_detail'
  spacing_mm?: number
  sampling?: ObserverSamplingMode
  enabled: boolean
  horizontal_deg: number
  vertical_deg: number
  range_deg: number
  step_deg: number
  resolution: number
  samples_per_cell: number
}

export const defaultObserverSettings: LeakPreviewObserverSettings = {
  measurement: 'radiance', min_luminance_nit: .01, refine_angles: true,
  search_mode: 'preview', spacing_mm: 2,
  enabled: false, sampling: 'balanced', horizontal_deg: 85, vertical_deg: 0, range_deg: 1, step_deg: 1,
  resolution: 64, samples_per_cell: 2,
}

export interface LeakPreviewProjectState {
  displayFilter?: import('./leak-display-filter').LeakDisplayFilter
  sourceMode: 'face' | 'body' | 'emitters'
  sourceEmitterIds?: string[]
  detectionMode?: 'escape_directions' | 'planar'
  observer?: LeakPreviewObserverSettings
  sourceFaceIds: number[]
  sourceComponentIds: number[]
  sourceBodyFaceCount: number
  quality: LeakPreviewQuality
  directions: LeakPreviewDirection[]
  visualizationVisible: boolean
  ignoreAreasVisible: boolean
  blockersVisible: boolean
  ignoreAreas: LeakPreviewIgnoreArea[]
  blockers: LeakPreviewBlocker[]
}

export type LeakPreviewHighlight = { kind: 'observer'; horizontal: number; vertical: number } | { kind: 'escape'; bin: number }

export function isLeakPreviewPointHighlighted(point: LeakPreviewPoint, highlight: LeakPreviewHighlight | null, candidateId: string | null, groupIds: readonly string[] | ReadonlySet<string> = []): boolean {
  if (highlight?.kind === 'observer') return point.observerAngles?.[0] === highlight.horizontal && point.observerAngles?.[1] === highlight.vertical
  if (highlight?.kind === 'escape') {
    if (point.angleBins) return (point.angleBins[highlight.bin] ?? 0) > 0
    if (!point.observerAngles) return false
    // Observer azimuth/elevation use the same reference axis as escape bins.
    // This associates directions, never adds reverse evidence to forward counts.
    const [h,v] = point.observerAngles.map(value => value*Math.PI/180)
    const angle = Number((Math.acos(Math.max(-1,Math.min(1,Math.cos(h)*Math.cos(v))))*180/Math.PI).toFixed(10))
    if (angle > 90) return false
    return highlight.bin === (angle < 30 ? 0 : angle < 60 ? 1 : angle < 80 ? 2 : 3)
  }
  return candidateId !== null && point.candidateId === candidateId || Boolean(point.candidateId && ('has' in groupIds ? groupIds.has(point.candidateId) : groupIds.includes(point.candidateId)))
}

export interface LeakPreviewPoint {
  apertureId?: string
  candidateId?: string
  observerAngles?: [number, number]
  surfacePosition?: Vec3
  angleBins?: number[]
  position: Vec3
  relativeStrength: number
}

export interface LeakPreviewCandidate {
  apertureMethod?: string
  apertureVariationMm?: number
  apertureId?: string
  aperturePosition?: Vec3
  peakLuminanceNit?: number
  peakDirectLuminanceNit?: number
  peakReflectedLuminanceNit?: number
  representativePath?: Vec3[]
  groupedCount?: number
  observerAngles?: Array<{ horizontalDeg: number; verticalDeg: number; directCount: number; reflectedCount: number }>
  sourceCandidateIds?: string[]
  detectedFootprint?: { center: Vec3; widthMm: number; heightMm: number }
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
  observerEvidence?: { axis: LeakPreviewDirection; stepDeg: number; horizontalDeg: number; verticalDeg: number; directCount: number; reflectedCount: number; direction: Vec3; receiverCenter: Vec3; footprintCenter?: Vec3; footprintWidthMm?: number; footprintHeightMm?: number }
}

export interface LeakPreviewDetection {
  points: LeakPreviewPoint[]
  candidates: LeakPreviewCandidate[]
}

/** Completed/partial evidence only; never contains a live job or server token. */
export interface SavedLeakPreviewResult {
  format: 'leak-preview-result.v1'
  result: RayTraceResult
  points: LeakPreviewPoint[]
  candidates: LeakPreviewCandidate[]
  selectedCandidateId: string | null
  highlight: LeakPreviewHighlight | null
  inputSignature: string | null
}

export interface BuildLeakPreviewRequestOptions {
  maxDepth?: number
  precisionConfig?: RayTraceConfigRequest
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
  allowedAreas?: LeakPreviewIgnoreArea[]
  detectionMode?: 'escape_directions' | 'planar'
  observer?: LeakPreviewObserverSettings
  existingEmitters?: EmitterSpec[]
  bsdfAssets?: BsdfAsset[]
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
    label: `Block ${sequence}`,
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

/** Resize/reposition a blocker from ordered screen-rectangle corners projected
 * onto its CAD surface. Align its in-plane axes with the drag, preserving the
 * normal, offset and depth. */
export function resizeLeakPreviewBlockerOnFace(
  blocker: LeakPreviewBlocker,
  points: Vec3[],
): Partial<LeakPreviewBlocker> | null {
  if (points.length < 2) return null
  const normal = normalized(blocker.normal)
  const horizontal = points.length === 4 ? subtract(points[1], points[0]) : blocker.uAxis
  const uAxis = normalized(subtract(horizontal, addScaled([0, 0, 0], normal, dot(horizontal, normal))))
  const vAxis = normalized(cross(normal, uAxis))
  if (Math.hypot(...uAxis) < 0.5 || Math.hypot(...vAxis) < 0.5) return null
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
    uAxis,
    vAxis,
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

/** These six grids localize final escapes; the tracer does not intersect them. */
export function createEscapeEnclosureReceivers(scene: ScenePayload, transforms: ComponentTransformRule[] = []): ReceiverSpec[] {
  const bounds = getLeakPreviewBounds(scene, transforms)
  const size = bounds.size.map((value) => Math.max(value, 0.001)) as Vec3
  return createLeakPreviewReceivers(scene, transforms).map((receiver, index) => {
    const axis = Math.floor(index / 2)
    const sign = index % 2 === 0 ? 1 : -1
    const center = [...bounds.center] as Vec3
    center[axis] += sign * size[axis] / 2
    const width = receiver.u_axis!.reduce((sum, value, i) => sum + Math.abs(value) * size[i], 0)
    const height = receiver.v_axis!.reduce((sum, value, i) => sum + Math.abs(value) * size[i], 0)
    return { ...receiver, center, base_center: center, width_mm: width, height_mm: height,
      resolution: [48, 48] as [number, number] }
  })
}

export interface EscapeDirectionSummary {
  contract: 'final_escape_directions_v1'
  direction: LeakPreviewDirection
  escaped_count: number
  selected_count: number
  excluded_count: number
  bins: Array<{ min_deg: number; max_deg: number; count: number; flux_lumen: number }>
  cells: Array<{ receiver_id: string; row: number; column: number; position: Vec3; surface_position?: Vec3; count: number;
    grazing_count: number; min_depth: number; max_depth: number; angle_sum_deg: number; angle_bin_counts?: number[] }>
}

export function escapeDirectionSummary(result: RayTraceResult): EscapeDirectionSummary | null {
  const value = result.metrics._escape_direction_summary as EscapeDirectionSummary | undefined
  if (!value || value.contract !== 'final_escape_directions_v1' || !Array.isArray(value.bins) || !Array.isArray(value.cells)) return null
  const count = (n: number) => Number.isSafeInteger(n) && n >= 0
  if (!allLeakPreviewDirections.includes(value.direction) || !count(value.escaped_count) || !count(value.selected_count) || !count(value.excluded_count)) return null
  const edges = [0, 30, 60, 80, 90]
  if (value.bins.length !== 4 || !value.bins.every((bin, i) => bin && bin.min_deg === edges[i] && bin.max_deg === edges[i + 1] && count(bin.count) && Number.isFinite(bin.flux_lumen) && bin.flux_lumen >= 0)) return null
  if (value.bins.reduce((sum, bin) => sum + bin.count, 0) !== value.selected_count) return null
  const grids = new Map(result.receiver_grids.map((grid) => [grid.receiver_id, grid]))
  if (!value.cells.every((cell) => {
    const grid = cell && grids.get(cell.receiver_id)
    return grid && count(cell.row) && count(cell.column) && cell.row < grid.resolution[1] && cell.column < grid.resolution[0] &&
      Array.isArray(cell.position) && cell.position.length === 3 && cell.position.every(Number.isFinite) &&
      (cell.surface_position === undefined || (Array.isArray(cell.surface_position) && cell.surface_position.length === 3 && cell.surface_position.every(Number.isFinite))) &&
      count(cell.count) && cell.count > 0 && count(cell.grazing_count) && cell.grazing_count <= cell.count &&
      count(cell.min_depth) && count(cell.max_depth) && cell.max_depth >= cell.min_depth && Number.isFinite(cell.angle_sum_deg) &&
      (cell.angle_bin_counts === undefined || (Array.isArray(cell.angle_bin_counts) && cell.angle_bin_counts.length === 4 &&
        cell.angle_bin_counts.every(count) && cell.angle_bin_counts.reduce((sum, n) => sum + n, 0) === cell.count))
  })) return null
  return value
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

/** A Body source emits only on its real CAD faces, never its bounding box. */
export function createLeakPreviewBodyEmitters(
  scene: ScenePayload,
  componentIds: number[],
  _transformRules: ComponentTransformRule[] = [],
  rayCount = 100_000,
  splitDirections = true,
): EmitterSpec[] {
  const bodies = [...new Set(componentIds)].filter(id => scene.components.some(c => c.component_id === id))
  if (!bodies.length) return []
  const directions = splitDirections ? (['forward', 'reverse'] as const) : (['both'] as const)
  const count = bodies.length * directions.length
  let assigned = 0
  return bodies.flatMap((componentId, bodyIndex) => directions.map((direction, sideIndex) => {
    const last = bodyIndex === bodies.length - 1 && sideIndex === directions.length - 1
    const rays = last ? Math.max(1, rayCount - assigned) : Math.max(1, Math.floor(rayCount / count))
    assigned += rays
    return {
      ...createFaceEmitter(`__leak_preview_body_${componentId}_${bodyIndex + 1}_${direction}`, []),
      source_component_ids: [componentId],
      normal_flip: direction === 'reverse',
      emission_direction: direction,
      direction_distribution: 'lambertian' as const,
      power_mode: 'total' as const,
      power_lumen: 1 / count,
      luminance_nit: undefined,
      ray_count: rays,
      seed: 42 + bodyIndex * 2 + sideIndex,
    }
  }))
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
  allowedAreas = [],
  existingEmitters,
  maxDepth,
  precisionConfig,
  detectionMode = 'planar',
  observer,
  bsdfAssets = [],
  directions = detectionMode === 'escape_directions' ? ['neg_z'] : allLeakPreviewDirections,
}: BuildLeakPreviewRequestOptions): RayTraceRequest {
  const directional = detectionMode === 'escape_directions'
  const fullDetail = directional && observer?.enabled && observer.search_mode === 'full_detail'
  const hasBsdf = materialAssignments.some((assignment) => assignment.enabled && assignment.bsdfAssetId)
  const qualityConfig = {
    fast: { rayCount: 100_000, maxDepth: 3 },
    balanced: { rayCount: 500_000, maxDepth: 5 },
    deep: { rayCount: 1_000_000, maxDepth: 20 },
  }[quality]
  const totalRays = qualityConfig.rayCount
  const perSide = Math.max(1, Math.floor(totalRays / 2))
  const baseConfig: RayTraceConfigRequest = {
    ray_count: totalRays,
    max_depth: fullDetail ? maxDepth ?? 20 : qualityConfig.maxDepth,
    seed: 42,
    min_energy: 1e-9,
    epsilon_mm: 0.001,
    k_abs: 1,
    k_brdf: 1,
    angle_dependent_reflectance: false,
    termination_mode: 'russian_roulette',
    contribution_mode: 'summary',
    intersection_backend: 'auto',
    compute_backend: hasBsdf && !directional ? 'cpu' : computeBackend,
    // A bounded receiver-path sample lets Preview reconstruct the actual
    // model-envelope exit location rather than drawing every point on the
    // remote virtual detector plane.
    store_ray_paths: true,
    max_stored_paths: 4_000,
    auto_convergence: false,
    // Preview uses the selected exterior envelope as an importance target.
    // The MIS weights preserve energy while spending substantially more
    // samples on weak escape paths in the chosen viewing direction.
    primary_sampling_strategy: hasBsdf || directional ? 'source' : 'receiver_mis',
    receiver_importance_fraction: 0.65,
    bounce_sampling_strategy: hasBsdf || directional ? 'source' : 'receiver_mis',
    bounce_receiver_importance_fraction: 0.55,
    ...(fullDetail && precisionConfig ? {
      max_depth: precisionConfig.max_depth,
      epsilon_mm: precisionConfig.epsilon_mm,
      k_abs: precisionConfig.k_abs,
      k_brdf: precisionConfig.k_brdf,
      angle_dependent_reflectance: precisionConfig.angle_dependent_reflectance,
      min_energy: precisionConfig.min_energy,
      min_energy_basis: precisionConfig.min_energy_basis,
      termination_mode: precisionConfig.termination_mode,
      store_ray_paths: precisionConfig.store_ray_paths,
      max_stored_paths: Math.min(4000, precisionConfig.max_stored_paths),
    } : {}),
  }
  const selectedEmitters = existingEmitters?.filter((emitter) => emitter.enabled)
  if (selectedEmitters && selectedEmitters.length === 0) throw new Error('사용할 Ray Tracing Emitter를 하나 이상 선택하세요.')
  const rayCounts = selectedEmitters ? distributeIntegerBudget(totalRays, selectedEmitters.map((emitter) => Math.max(1, emitter.ray_count))) : []
  const request = buildRayTraceRequest({
    scene,
    bsdfAssets,
    projectName: 'Whole Set Leak Preview',
    emitters: selectedEmitters
      ? selectedEmitters.map((emitter, index) => ({ ...structuredClone(emitter), ray_count: rayCounts[index] }))
      : sourceComponentIds.length > 0
      ? createLeakPreviewBodyEmitters(
          scene,
          sourceComponentIds,
          transformRules,
          totalRays,
          true,
        )
      : [
          previewEmitter('__leak_preview_source_a', sourceFaceIds, [], false, perSide),
          previewEmitter('__leak_preview_source_b', sourceFaceIds, [], true, totalRays - perSide),
        ],
    receivers: directional ? createEscapeEnclosureReceivers(scene, transformRules) : createLeakPreviewReceivers(scene, transformRules, directions),
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
  request.geometry_mode = fullDetail ? 'precision' : 'preview'
  request.preview_blockers = expandBlockerGroups(blockers)
    .filter((blocker) => blocker.enabled)
    .map((blocker) => ({
      blocker_id: blocker.id,
      shape: blocker.shape ?? 'box',
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
  // Exclude allowed openings before receiver binning; display-only filtering
  // cannot undo oblique paths or mixed/unsampled receiver cells reliably.
  if (directional) {
    const bounds = getLeakPreviewBounds(scene, transformRules)
    const extent = bounds.size.map((value) => Math.max(value, 0.001)) as Vec3
    if (observer?.enabled) {
      const plan = planObserverSampling(observer, request.emitters.length, { minimum: bounds.minimum, maximum: bounds.maximum, direction: directions[0] ?? 'neg_z' })
      if (plan.error) throw new Error(`역추적 탐색 한도 초과: ${plan.error}`)
      const { enabled: _enabled, sampling: _sampling, ...rawSettings } = observer
      const settings = { ...rawSettings, ...(fullDetail ? { spacing_mm: observer.spacing_mm ?? 2 } : {}), resolution: plan.resolution, samples_per_cell: plan.samples_per_cell }
      if (settings.measurement === 'radiance') {
        settings.min_luminance_nit ??= .01
        if (!Number.isFinite(settings.min_luminance_nit) || settings.min_luminance_nit < 0) throw new Error('최소 관찰 휘도는 0 이상의 유한한 nit 값이어야 합니다.')
      }
      for (const [key, lo, hi] of [['horizontal_deg', -89.9, 89.9], ['vertical_deg', -89.9, 89.9], ['range_deg', 0, 10], ['step_deg', .1, 90], ['resolution', 8, 256], ['samples_per_cell', 1, fullDetail && observer.measurement === 'radiance' ? 64 : 16]] as const) {
        const value = settings[key]
        if (!Number.isFinite(value) || value < lo || value > hi || (['resolution', 'samples_per_cell'].includes(key) && !Number.isInteger(value))) throw new Error('역추적 시야각·탐색 설정을 확인하세요.')
      }
      request.observer_probe = settings
      // Observer occlusion must use the same detailed geometry as precision
      // tracing; a decimated viewer wall cannot certify a physical opening.
      request.geometry_mode = 'precision'
    }
    request.escape_collection = {
      direction: directions[0] ?? 'neg_z',
      minimum: bounds.center.map((value, i) => value - extent[i] / 2) as Vec3,
      maximum: bounds.center.map((value, i) => value + extent[i] / 2) as Vec3,
      allowed_regions: allowedAreas.filter((area) => area.enabled).flatMap((area) => area.regions),
    }
  } else request.preview_blockers.push(...buildPrecisionPreviewConstraints([], allowedAreas, false, true))
  // The production tracer deliberately treats unassigned surfaces as perfect
  // absorbers. Preview needs a neutral fallback so a novice can still locate
  // reflected leak paths without first completing Material Assignment.
  if (!fullDetail) request.optical_profiles = [
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
    // The Receiver remains 3 mm outside the product envelope. Include that
    // measurement plane in the ROI volume without moving it into the CAD.
    for (const depth of [-leakPreviewReceiverDistanceMm - 1, modelDepth]) {
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
  // A single coarse detector cell can be much smaller than a usable
  // inspection window. Keep at least 30 × 30 mm in the receiver plane;
  // larger detected clusters retain their measured extent.
  for (const axis of [receiver.u_axis ?? [1, 0, 0], receiver.v_axis ?? [0, 1, 0]]) {
    const dimension = axis.findIndex((value) => Math.abs(value) > 0.5)
    if (dimension < 0) continue
    const center = (minimum[dimension] + maximum[dimension]) / 2
    const halfSize = Math.max(
      leakPreviewRoiMinimumSizeMm,
      maximum[dimension] - minimum[dimension],
    ) / 2
    minimum[dimension] = center - halfSize
    maximum[dimension] = center + halfSize
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
    target.sourceCandidateIds = [...(target.sourceCandidateIds ?? [target.id]), ...(candidate.sourceCandidateIds ?? [candidate.id])]
    target.cellCount += candidate.cellCount
    target.fluxLumen += candidate.fluxLumen
    target.peakFluxLumen = Math.max(target.peakFluxLumen, candidate.peakFluxLumen)
    if (target.observerEvidence && candidate.observerEvidence) {
      target.observerEvidence = { ...target.observerEvidence, directCount: target.observerEvidence.directCount + candidate.observerEvidence.directCount,
        reflectedCount: target.observerEvidence.reflectedCount + candidate.observerEvidence.reflectedCount }
    }
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
    if (target.detectedFootprint && candidate.detectedFootprint) {
      const corners = [target, candidate].flatMap(item => [-.5,.5].flatMap(u => [-.5,.5].map(v =>
        addScaled(addScaled(item.detectedFootprint!.center, item.uAxis, u*item.detectedFootprint!.widthMm), item.vAxis, v*item.detectedFootprint!.heightMm))))
      const us = corners.map(point => dot(point, target.uAxis)), vs = corners.map(point => dot(point, target.vAxis))
      const center = addScaled(addScaled(target.center, target.uAxis, (Math.min(...us)+Math.max(...us))/2-dot(target.center,target.uAxis)),
        target.vAxis, (Math.min(...vs)+Math.max(...vs))/2-dot(target.center,target.vAxis))
      target.detectedFootprint = { center, widthMm: Math.max(...us)-Math.min(...us), heightMm: Math.max(...vs)-Math.min(...vs) }
    } else target.detectedFootprint = undefined
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
  angleBins?: number[]
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
  const summary = escapeDirectionSummary(result)
  if (summary) return new Map(summary.cells.map((cell) => [
    `${cell.receiver_id}:${cell.row}:${cell.column}`,
    { position: cell.surface_position ?? cell.position, sampleCount: cell.count, grazingSampleCount: cell.grazing_count,
      minReflectionCount: cell.min_depth, maxReflectionCount: cell.max_depth,
      meanExitAngleDeg: cell.angle_sum_deg / Math.max(1, cell.count), angleBins: cell.angle_bin_counts },
  ]))
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

interface ObserverProbeView {
  peak_luminance_nit?: number
  grid_resolution?: [number, number]; sampling_resolution?: [number, number]; sample_spacing_mm?: [number, number]; display_stride?: number
  horizontal_deg: number; vertical_deg: number; direct_count: number; reflected_count: number
  direction: Vec3; observer_center: Vec3; u_axis: Vec3; v_axis: Vec3; spacing_mm: [number, number]
}
interface ObserverProbeCell {
  aperture?: { contract: 'bounded_planar_opening_v1' | 'automatic_exit_estimate_v1'; id: string; position: Vec3; exterior_shell_certified: false; method?: string; uncertainty_mm?: number }
  direct_luminance_nit?: number
  reflected_luminance_nit?: number
  path_validation?: { status: 'valid' | 'invalid' | 'unverified'; endpoint_kind?: 'cad_surface' | 'virtual_emitter'; reason?: string }
  luminance_nit?: number
  sample_count?: number
  representative_path?: Vec3[]
  view_index: number; row: number; column: number; position: Vec3
  direct_count: number; reflected_count: number; min_depth: number; max_depth: number
}
export interface ObserverProbeSummary {
  aperture_summary?: { contract: string; attributed: number; unresolved: number; reason: string }
  filtered_count?: number
  positive_before_threshold_count?: number
  peak_luminance_before_threshold_nit?: number
  photometric_diagnostics_scope?: string
  path_classification_contract?: string
  path_validation_summary?: { contract: string; checked: number; valid: number; invalid: number; missing: number; virtual_emitter: number }
  display_truncated?: boolean
  limitations?: string[]
  calibration_state?: string
  search_contract?: string
  contract: 'observer_path_support_v1' | 'observer_radiance_parallel_v1'; execution_backend: 'cpu' | 'gpu_cuda'; requested_backend?: 'cpu' | 'gpu_cuda'; performance?: Record<string, unknown>; sample_contract?: string; measure: 'path_support_counts' | 'luminance_cd_m2'
  settings: Omit<LeakPreviewObserverSettings, 'enabled' | 'sampling'>
  attempted_count: number; planned_count: number; direct_count: number; reflected_count: number
  excluded_count: number; depth_limited_count: number; complete: boolean; stopped: boolean; runtime_sec: number
  views: ObserverProbeView[]; cells: ObserverProbeCell[]
}

export function observerProbeSummary(result: RayTraceResult): ObserverProbeSummary | null {
  const raw = result.metrics._observer_probe_summary as unknown
  if (!raw || typeof raw !== 'object') return null
  const summary = raw as ObserverProbeSummary
  const count = (x: unknown): x is number => typeof x === 'number' && Number.isSafeInteger(x) && x >= 0
  const vector = (x: unknown, size = 3): boolean => Array.isArray(x) && x.length === size && x.every((v) => typeof v === 'number' && Number.isFinite(v))
  const audit = summary.path_validation_summary
  if ((summary.filtered_count !== undefined && !count(summary.filtered_count)) ||
    (summary.positive_before_threshold_count !== undefined && !count(summary.positive_before_threshold_count)) ||
    (summary.peak_luminance_before_threshold_nit !== undefined && (!Number.isFinite(summary.peak_luminance_before_threshold_nit) || summary.peak_luminance_before_threshold_nit < 0))) return null
  if (audit !== undefined && (!audit || audit.contract !== 'observer_path_geometry_audit_v1' ||
    ![audit.checked, audit.valid, audit.invalid, audit.missing, audit.virtual_emitter].every(count) ||
    audit.checked !== audit.valid+audit.invalid || audit.virtual_emitter > audit.valid)) return null
  const radiance = summary.contract === 'observer_radiance_parallel_v1' && summary.measure === 'luminance_cd_m2'
  if (radiance && (summary.settings?.measurement !== 'radiance' || summary.calibration_state !== 'uncalibrated' ||
    !Array.isArray(summary.limitations) || !summary.limitations.includes('finite_sampling'))) return null
  if (summary.settings?.search_mode === 'full_detail' && (summary.search_contract !== 'observer_full_spacing_v1' ||
    !Array.isArray(summary.views) || summary.views.some((view) => !vector(view.grid_resolution, 2) || !vector(view.sampling_resolution, 2) || !vector(view.sample_spacing_mm, 2) ||
      !view.sampling_resolution!.every((n) => count(n) && n > 0) || !count(view.display_stride) || view.display_stride < 1 ||
      !view.sample_spacing_mm!.every((n) => n > 0 && n <= (summary.settings.spacing_mm ?? 2) + 1e-9)))) return null
  if ((!radiance && (summary.contract !== 'observer_path_support_v1' || summary.measure !== 'path_support_counts')) || !['cpu', 'gpu_cuda'].includes(summary.execution_backend) ||
    !summary.settings || !count(summary.settings.resolution) || summary.settings.resolution < 8 || summary.settings.resolution > 256 ||
    ![summary.attempted_count, summary.planned_count, summary.direct_count, summary.reflected_count, summary.excluded_count, summary.depth_limited_count].every(count) ||
    summary.attempted_count > summary.planned_count || summary.direct_count + summary.reflected_count + summary.excluded_count > summary.attempted_count ||
    typeof summary.complete !== 'boolean' || typeof summary.stopped !== 'boolean' || summary.complete === summary.stopped ||
    !Array.isArray(summary.views) || summary.views.length > (summary.settings.coverage === 'hemisphere' ? 2048 : 121) || !Array.isArray(summary.cells)) return null
  const residentSamples = summary.sample_contract === 'observer_counter_latin_v1' && summary.performance?.observer_transport_contract === 'strict_float64_observer_resident_v1' && Number(summary.performance?.observer_resident_success_count) > 0
  if (summary.execution_backend === 'gpu_cuda' && (summary.requested_backend !== 'gpu_cuda' || (!residentSamples && summary.sample_contract !== 'observer_per_ray_seed_batch_v1') || !summary.performance ||
    !['gpu_active', 'gpu_mixed'].includes(String(summary.performance.compute_execution_state)) ||
    !(Number(summary.performance.gpu_cuda_gpu_success_count) > 0))) return null
  if (summary.views.some((view) => !vector(view.direction) || !vector(view.observer_center) || !vector(view.u_axis) || !vector(view.v_axis) || !vector(view.spacing_mm, 2) ||
    (view.grid_resolution !== undefined && (!vector(view.grid_resolution, 2) || !view.grid_resolution.every((n) => count(n) && n > 0))) ||
    !Number.isFinite(view.horizontal_deg) || !Number.isFinite(view.vertical_deg) || !count(view.direct_count) || !count(view.reflected_count))) return null
  if (summary.cells.some((cell) => !count(cell.view_index) || cell.view_index >= summary.views.length || !vector(cell.position) ||
    (cell.path_validation !== undefined && (!cell.path_validation || !['valid', 'invalid', 'unverified'].includes(cell.path_validation.status) ||
      (cell.path_validation.endpoint_kind !== undefined && !['cad_surface', 'virtual_emitter'].includes(cell.path_validation.endpoint_kind)))) ||
    (radiance && (!Number.isFinite(cell.luminance_nit) || cell.luminance_nit! <= 0 || !count(cell.sample_count) || cell.sample_count < 1)) ||
    ([cell.direct_luminance_nit, cell.reflected_luminance_nit].some(value => value !== undefined && (!Number.isFinite(value) || value < 0))) ||
    (cell.aperture !== undefined && (!cell.aperture || !['bounded_planar_opening_v1','automatic_exit_estimate_v1'].includes(cell.aperture.contract) || typeof cell.aperture.id !== 'string' || !cell.aperture.id || !vector(cell.aperture.position) || cell.aperture.exterior_shell_certified !== false || (cell.aperture.contract === 'automatic_exit_estimate_v1' && (!Number.isFinite(cell.aperture.uncertainty_mm) || cell.aperture.uncertainty_mm! < 0 || typeof cell.aperture.method !== 'string')))) ||
    (cell.representative_path !== undefined && (!Array.isArray(cell.representative_path) || cell.representative_path.length < 2 || !cell.representative_path.every((p) => vector(p)))) ||
    !count(cell.row) || !count(cell.column) || cell.row >= (summary.views[cell.view_index].grid_resolution?.[1] ?? summary.settings.resolution) || cell.column >= (summary.views[cell.view_index].grid_resolution?.[0] ?? summary.settings.resolution) ||
    !count(cell.direct_count) || !count(cell.reflected_count) || cell.direct_count + cell.reflected_count <= 0 ||
    !count(cell.min_depth) || !count(cell.max_depth) || cell.min_depth > cell.max_depth)) return null
  for (const key of ['direct_count', 'reflected_count'] as const) {
    if (summary.cells.reduce((sum, cell) => sum + cell[key], 0) !== summary[key] || summary.views.reduce((sum, view) => sum + view[key], 0) !== summary[key]) return null
  }
  return summary
}

export function observerNoDetectionMessage(summary: ObserverProbeSummary | null | undefined): string {
  const warning = '빛샘 없음 판정이 아닙니다.'
  if (summary?.path_validation_summary?.invalid && summary.path_validation_summary.valid === 0) return `대표 경로 차폐·조건 불일치로 후보 제외 · ${warning}`
  if ((summary?.filtered_count ?? 0) > 0) return `최소 휘도 ${summary?.settings.min_luminance_nit ?? 0} nit 미달로 제외 · ${warning}`
  if (summary?.measure === 'luminance_cd_m2' && summary.positive_before_threshold_count === 0) {
    return summary.excluded_count > 0 ? `Allowed 영역에서 제외 · 관찰 휘도 미검출 · ${warning}` : `관찰 표본에서 양수 휘도 기여 미검출 · ${warning}`
  }
  return `현재 탐색 조건에서 미검출 · ${warning}`
}

/** Show the audited visible endpoint, never intermediate bounces. Opening
 * provenance controls exit guides, not whether optical evidence is visible.
 * Resolve legacy opening markers without modifying saved results. */
export function verifiedLeakPreviewSurfacePoints(
  points: LeakPreviewPoint[], summary: ObserverProbeSummary | null,
): LeakPreviewPoint[] {
  if (!summary) return []
  const key = (position: Vec3, angles: [number, number]) =>
    JSON.stringify([...position, ...angles])
  const endpoints = new Map<string, Vec3>()
  for (const cell of summary.cells) {
    if (cell.path_validation?.status !== 'valid') continue
    const endpoint = cell.representative_path?.at(-2)
    const view = summary.views[cell.view_index]
    if (!endpoint || !view) continue
    const angles: [number, number] = [view.horizontal_deg, view.vertical_deg]
    endpoints.set(key(endpoint, angles), endpoint)
    if (cell.aperture) endpoints.set(key(cell.aperture.position, angles), endpoint)
  }
  // Repeated cells can produce the same marker. Do not add their strengths:
  // these are duplicate display samples, not independent optical energy.
  // Preserve angle/candidate identity for selection and angular highlighting.
  const unique = new Map<string, LeakPreviewPoint>()
  for (const point of points) {
    if (!point.observerAngles) continue
    const position = endpoints.get(key(point.position, point.observerAngles))
    if (!position) continue
    const identity = JSON.stringify([position, point.observerAngles, point.candidateId ?? null])
    const previous = unique.get(identity)
    if (!previous || point.relativeStrength > previous.relativeStrength) unique.set(identity, { ...point, position, surfacePosition: position })
  }
  return [...unique.values()]
}

function detectObserverCandidates(scene: ScenePayload, result: RayTraceResult, options: LeakPreviewDetectionOptions): LeakPreviewDetection {
  const summary = observerProbeSummary(result)
  if (!summary) return { points: [], candidates: [] }
  const points: LeakPreviewPoint[] = []
  const candidates: LeakPreviewCandidate[] = []
  const bounds = getLeakPreviewBounds(scene, options.transformRules)
  const modelDepth = Math.max(...bounds.size, 1) * .18
  const radiance = summary.measure === 'luminance_cd_m2'
  const strength = (cell: ObserverProbeCell) => radiance ? cell.luminance_nit! : cell.direct_count + cell.reflected_count
  const maximum = summary.cells.filter((cell) => cell.path_validation?.status !== 'invalid').reduce((value, cell) => Math.max(value, strength(cell)), Number.MIN_VALUE)
  summary.views.forEach((view, viewIndex) => {
    const cells = summary.cells.filter((cell) => cell.view_index === viewIndex && cell.path_validation?.status !== 'invalid')
    const active = cells.map((cell) => ({ row: cell.row, column: cell.column, position: cell.aperture?.position ?? cell.position, flux: strength(cell) }))
    const lookup = new Map(cells.map((cell) => [`${cell.row}:${cell.column}`, cell]))
    const resolution = view.grid_resolution ?? [summary.settings.resolution, summary.settings.resolution]
    const receiver = { ...result.receivers[0], receiver_id: `observer-${viewIndex}`, center: [0, 0, 0] as Vec3,
      normal: view.direction.map((x) => -x) as Vec3, u_axis: view.u_axis, v_axis: view.v_axis,
      width_mm: view.spacing_mm[0] * resolution[0], height_mm: view.spacing_mm[1] * resolution[1] }
    const grid = { resolution } as ReceiverGrid

    const byOpening = new Map<string, typeof active>()
    for (const cell of active) {
      const id = lookup.get(`${cell.row}:${cell.column}`)?.aperture?.id ?? '__unresolved'
      let bucket = byOpening.get(id)
      if (!bucket) { bucket = []; byOpening.set(id, bucket) }
      bucket.push(cell)
    }
    const clusters = [...byOpening.values()].flatMap(cells => connectedClusters(cells))
    clusters.forEach((cluster, index) => {
      const candidateId = `observer-${viewIndex}-${index}`
      cluster.forEach((cell) => {
        const sample = lookup.get(`${cell.row}:${cell.column}`)!
        if (sample.path_validation?.status !== 'valid') return
        const position = sample.representative_path?.at(-2)
        if (!position) return
        points.push({ ...(sample.aperture ? { apertureId: sample.aperture.id } : {}), position, surfacePosition: position, relativeStrength: cell.flux / maximum, candidateId,
          observerAngles: [view.horizontal_deg, view.vertical_deg] })
      })
      const evidence = cluster.map((cell) => lookup.get(`${cell.row}:${cell.column}`)!)
      const directCount = evidence.reduce((sum, cell) => sum + cell.direct_count, 0)
      const reflectedCount = evidence.reduce((sum, cell) => sum + cell.reflected_count, 0)
      const count = directCount + reflectedCount
      const totalStrength = cluster.reduce((sum, cell) => sum + cell.flux, 0)
      const representative = [...evidence].sort((a, b) => Number(b.representative_path?.length === 2) - Number(a.representative_path?.length === 2) || strength(b) - strength(a)).find(cell => cell.representative_path)
      const aperture = representative?.aperture
      const center = aperture?.position ?? [0, 1, 2].map((axis) => cluster.reduce((sum, cell) => sum + cell.position[axis] * cell.flux, 0) / totalStrength) as Vec3
      const columns = cluster.map((cell) => cell.column), rows = cluster.map((cell) => cell.row)
      const angle = Math.acos(Math.max(-1, Math.min(1, dot(view.direction, directionVector(result.metrics._escape_direction_summary))))) * 180 / Math.PI
      candidates.push({ ...(aperture ? { apertureId: aperture.id, aperturePosition: aperture.position, ...(aperture.method ? {apertureMethod:aperture.method, apertureVariationMm:aperture.uncertainty_mm} : {}) } : {}), id: `observer-${viewIndex}-${index}`, label: `시야각 ${view.horizontal_deg}° / ${view.vertical_deg}°`,
        ...(radiance ? { peakLuminanceNit: Math.max(...evidence.map(strength)) } : {}),
        ...(evidence.some(cell => cell.direct_luminance_nit !== undefined) ? {
          peakDirectLuminanceNit: Math.max(...evidence.map(cell => cell.direct_luminance_nit ?? 0)),
          peakReflectedLuminanceNit: Math.max(...evidence.map(cell => cell.reflected_luminance_nit ?? 0)),
        } : {}),
        representativePath: representative?.representative_path,
        receiverId: receiver.receiver_id, center, normal: receiver.normal, uAxis: view.u_axis, vAxis: view.v_axis,
        widthMm: Math.max(leakPreviewRoiMinimumSizeMm, (Math.max(...columns) - Math.min(...columns) + 1) * view.spacing_mm[0] + 10),
        heightMm: Math.max(leakPreviewRoiMinimumSizeMm, (Math.max(...rows) - Math.min(...rows) + 1) * view.spacing_mm[1] + 10),
        fluxLumen: 0, peakFluxLumen: 0, relativeStrength: Math.max(...cluster.map((cell) => cell.flux)) / maximum,
        cellCount: cluster.length, sampledPathCount: count, grazingPathCount: angle >= 60 ? count : 0,
        minReflectionCount: Math.min(...evidence.map((cell) => cell.min_depth)), maxReflectionCount: Math.max(...evidence.map((cell) => cell.max_depth)),
        meanExitAngleDeg: angle, clipBox: candidateClipBox(cluster, receiver, grid, modelDepth),
        observerEvidence: { axis: directionAxis(result.metrics._escape_direction_summary), stepDeg: summary.settings.step_deg,
          horizontalDeg: view.horizontal_deg, verticalDeg: view.vertical_deg, directCount, reflectedCount, direction: view.direction,
          receiverCenter: addScaled(center, view.direction, dot(subtract(view.observer_center, center), view.direction)),
          footprintWidthMm: (Math.max(...columns) - Math.min(...columns) + 1) * view.spacing_mm[0],
          footprintHeightMm: (Math.max(...rows) - Math.min(...rows) + 1) * view.spacing_mm[1],
          footprintCenter: [0, 1, 2].map((axis) => view.observer_center[axis] +
            view.u_axis[axis]*(Math.min(...columns)+Math.max(...columns)+1-resolution[0])*view.spacing_mm[0]/2 +
            view.v_axis[axis]*(Math.min(...rows)+Math.max(...rows)+1-resolution[1])*view.spacing_mm[1]/2) as Vec3 },
      })
    })
  })
  return groupObserverCandidates(points, candidates, Math.max(3, Math.min(15, modelDepth / 18)))
}

export function groupObserverCandidates(points: LeakPreviewPoint[], candidates: LeakPreviewCandidate[], distance: number): LeakPreviewDetection {
  const patterns = new Map(candidates.map(candidate => [candidate.id, leakPathPattern(candidate.representativePath, distance)]))
  const groups: LeakPreviewCandidate[] = []
  const buckets = new Map<string, LeakPreviewCandidate[]>()
  const ids = new Map<string, string>()
  const openings = new Map<string, LeakPreviewCandidate>()
  for (const candidate of [...candidates].sort((a, b) => (b.peakLuminanceNit ?? b.sampledPathCount) - (a.peakLuminanceNit ?? a.sampledPathCount))) {
    const cell = candidate.center.map((x) => Math.floor(x / distance))
    const nearby: LeakPreviewCandidate[] = []
    for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) for (let z = -1; z <= 1; z++) {
      nearby.push(...(buckets.get(`${cell[0]+x}:${cell[1]+y}:${cell[2]+z}`) ?? []))
    }
    const target = (candidate.apertureId ? openings.get(candidate.apertureId) : undefined) ?? nearby.find((group) => {
      if (group.apertureId || candidate.apertureId) return Boolean(group.apertureId && group.apertureId === candidate.apertureId)
      if (Math.hypot(...subtract(group.center, candidate.center)) > distance) return false
      return similarLeakPathPatterns(patterns.get(group.id) ?? null, patterns.get(candidate.id) ?? null, distance)
    })
    if (!target) {
      const evidence = candidate.observerEvidence
      const group = { ...candidate, groupedCount: candidate.groupedCount ?? 1, sourceCandidateIds: [...(candidate.sourceCandidateIds ?? [candidate.id])],
        ...(evidence ? { observerAngles: candidate.observerAngles?.map(angle => ({ ...angle })) ?? [{ horizontalDeg: evidence.horizontalDeg, verticalDeg: evidence.verticalDeg,
          directCount: evidence.directCount, reflectedCount: evidence.reflectedCount }] } : {}) }

      groups.push(group)
      if (group.apertureId) openings.set(group.apertureId, group)
      const key = cell.join(':')
      buckets.set(key, [...(buckets.get(key) ?? []), group])
      ids.set(candidate.id, candidate.id)
      continue
    }
    ids.set(candidate.id, target.id)
    target.sourceCandidateIds!.push(...(candidate.sourceCandidateIds ?? [candidate.id]))
    target.groupedCount = (target.groupedCount ?? 1) + (candidate.groupedCount ?? 1)
    if (target.observerEvidence && candidate.observerEvidence) {
      const evidence = candidate.observerEvidence
      for (const entry of candidate.observerAngles ?? [{ horizontalDeg: evidence.horizontalDeg, verticalDeg: evidence.verticalDeg, directCount: evidence.directCount, reflectedCount: evidence.reflectedCount }]) {
        const angle = target.observerAngles!.find(item => item.horizontalDeg === entry.horizontalDeg && item.verticalDeg === entry.verticalDeg)
        if (angle) { angle.directCount += entry.directCount; angle.reflectedCount += entry.reflectedCount }
        else target.observerAngles!.push({ ...entry })
      }
    }
    target.sampledPathCount += candidate.sampledPathCount
    target.cellCount += candidate.cellCount
    target.grazingPathCount += candidate.grazingPathCount
    target.relativeStrength = Math.max(target.relativeStrength, candidate.relativeStrength)
    if (candidate.peakLuminanceNit !== undefined) target.peakLuminanceNit = Math.max(target.peakLuminanceNit ?? 0, candidate.peakLuminanceNit)
    if (candidate.peakDirectLuminanceNit !== undefined) target.peakDirectLuminanceNit = Math.max(target.peakDirectLuminanceNit ?? 0, candidate.peakDirectLuminanceNit)
    if (candidate.peakReflectedLuminanceNit !== undefined) target.peakReflectedLuminanceNit = Math.max(target.peakReflectedLuminanceNit ?? 0, candidate.peakReflectedLuminanceNit)
    target.clipBox = { xMin: Math.min(target.clipBox.xMin, candidate.clipBox.xMin), xMax: Math.max(target.clipBox.xMax, candidate.clipBox.xMax),
      yMin: Math.min(target.clipBox.yMin, candidate.clipBox.yMin), yMax: Math.max(target.clipBox.yMax, candidate.clipBox.yMax),
      zMin: Math.min(target.clipBox.zMin ?? 0, candidate.clipBox.zMin ?? 0), zMax: Math.max(target.clipBox.zMax ?? 0, candidate.clipBox.zMax ?? 0) }
    // Cell-level direct preservation must survive grouping across view angles.
    // Keep aggregate photometry unchanged; replace diagnostic representative only.
    if (candidate.representativePath?.length === 2 && target.representativePath?.length !== 2) {
      Object.assign(target, {
        representativePath: candidate.representativePath,
        observerEvidence: candidate.observerEvidence,
        center: candidate.center, normal: candidate.normal, uAxis: candidate.uAxis, vAxis: candidate.vAxis,
        receiverId: candidate.receiverId, widthMm: candidate.widthMm, heightMm: candidate.heightMm,
        aperturePosition: candidate.aperturePosition,
      })
    }
  }
  for (const group of groups) {
    if (group.observerAngles && group.observerAngles.length > 1 && group.observerEvidence) {
      group.label = `대표 ${group.observerEvidence.horizontalDeg}° / ${group.observerEvidence.verticalDeg}° · ${group.observerAngles.length}방향`
    }
  }
  const mapped = points.map((point) => ({ ...point, candidateId: ids.get(point.candidateId ?? '') ?? point.candidateId }))
  // Group identity must not erase distinct visible patches behind one opening.
  const unique = new Map<string, LeakPreviewPoint>()
  for (const point of mapped) {
    const key = JSON.stringify([point.candidateId, point.position, point.observerAngles])
    if (!unique.has(key) || point.relativeStrength > unique.get(key)!.relativeStrength) unique.set(key, point)
  }
  return { candidates: groups, points: [...unique.values()] }
}

function directionAxis(raw: unknown): LeakPreviewDirection {
  const name = (raw as { direction?: LeakPreviewDirection } | undefined)?.direction ?? 'neg_z'
  return allLeakPreviewDirections.includes(name) ? name : 'neg_z'
}

function directionVector(raw: unknown): Vec3 {
  return ({ pos_x: [1,0,0], neg_x: [-1,0,0], pos_y: [0,1,0], neg_y: [0,-1,0], pos_z: [0,0,1], neg_z: [0,0,-1] } as Record<LeakPreviewDirection, Vec3>)[directionAxis(raw)]
}

export function detectLeakPreviewCandidates(
  scene: ScenePayload,
  result: RayTraceResult,
  options: LeakPreviewDetectionOptions = {},
): LeakPreviewDetection {
  const directionalSummary = escapeDirectionSummary(result)
  const receivers = new Map(result.receivers.map((item) => [item.receiver_id, item]))
  const globalMaximum = Math.max(
    0,
    ...result.receiver_grids.flatMap((grid) => grid.flux_lumen.flat()),
  )
  const observerDetection = detectObserverCandidates(scene, result, options)
  // A forward escape count is not an observation. Reintroducing it here
  // would bypass the radiance filter, including a valid zero-cell result.
  if (observerProbeSummary(result)?.measure === 'luminance_cd_m2') return observerDetection
  if (globalMaximum <= 0) return observerDetection

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
  const ignoreAreas = directionalSummary ? [] : options.ignoreAreas ?? []
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
        const preserveGrazingPath = (directionalSummary !== null || grid.receiver_id === '__leak_preview_pos_z') &&
          (sample?.grazingSampleCount ?? 0) > 0
        if (flux <= 0 || (!directionalSummary && flux < threshold && !preserveGrazingPath)) continue
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

      }
    }
    for (const cluster of connectedClusters(active)) {
      const candidateId = `${grid.receiver_id}:${Math.min(...cluster.map((cell) => cell.row))}:${Math.min(...cluster.map((cell) => cell.column))}`
      cluster.forEach((cell) => points.push({ position: cell.position, relativeStrength: cell.flux / globalMaximum, candidateId, angleBins: cell.sample?.angleBins }))
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
      const uAxis = receiver.u_axis ?? [1,0,0] as Vec3, vAxis = receiver.v_axis ?? [0,1,0] as Vec3
      const us = cluster.map(cell => dot(cell.position,uAxis)), vs = cluster.map(cell => dot(cell.position,vAxis))
      const footprintCenter = addScaled(addScaled(weightedCenter, uAxis, (Math.min(...us)+Math.max(...us))/2-dot(weightedCenter,uAxis)),
        vAxis, (Math.min(...vs)+Math.max(...vs))/2-dot(weightedCenter,vAxis))
      const sampledCells = cluster.flatMap((cell) => cell.sample ? [cell.sample] : [])
      const sampledPathCount = sampledCells.reduce((sum, sample) => sum + sample.sampleCount, 0)
      candidates.push({
        id: `${grid.receiver_id}:${Math.min(...rows)}:${Math.min(...columns)}`,
        label: directionLabels[grid.receiver_id.replace('__leak_preview_', '')] ?? receiver.display_name,
        receiverId: grid.receiver_id,
        center: weightedCenter,
        detectedFootprint: { center: footprintCenter, widthMm: Math.max(...us)-Math.min(...us)+cellWidth, heightMm: Math.max(...vs)-Math.min(...vs)+cellHeight },
        normal: receiver.normal,
        uAxis: receiver.u_axis ?? [1, 0, 0],
        vAxis: receiver.v_axis ?? [0, 1, 0],
        widthMm: Math.max(leakPreviewRoiMinimumSizeMm, (Math.max(...columns) - Math.min(...columns) + 1) * cellWidth + leakPreviewRoiOffsetMm * 2),
        heightMm: Math.max(leakPreviewRoiMinimumSizeMm, (Math.max(...rows) - Math.min(...rows) + 1) * cellHeight + leakPreviewRoiOffsetMm * 2),
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
  const topCandidates = directionalSummary ? rankedCandidates : rankedCandidates.slice(0, 8)
  const strongestFrontGrazing = rankedCandidates.find((candidate) =>
    candidate.receiverId === '__leak_preview_pos_z' && candidate.grazingPathCount > 0,
  )
  if (strongestFrontGrazing && !topCandidates.includes(strongestFrontGrazing)) {
    topCandidates.splice(Math.max(0, topCandidates.length - 1), 1, strongestFrontGrazing)
    topCandidates.sort((left, right) => right.relativeStrength - left.relativeStrength)
  }
  for (const candidate of topCandidates) {
    let nearest = Infinity
    for (const path of result.stored_paths) {
      const hit = path.at(-1), previous = path.at(-2)
      if (!hit || !previous || hit.event_type !== 'receiver' || hit.receiver_id !== candidate.receiverId) continue
      const exit = receiverEnvelopeIntersection(candidate.receiverId, previous.point, hit.point, boundsMinimum, boundsMaximum) ?? previous.point
      const distance = Math.hypot(...subtract(exit, candidate.center))
      const clip = candidate.clipBox
      if (distance >= nearest || exit[0] < clip.xMin || exit[0] > clip.xMax || exit[1] < clip.yMin || exit[1] > clip.yMax ||
        (!clip.zUnbounded && (exit[2] < (clip.zMin ?? -Infinity) || exit[2] > (clip.zMax ?? Infinity)))) continue
      nearest = distance
      candidate.representativePath = path.map((vertex) => vertex.point)
    }
  }
  const groupedForward = groupObserverCandidates([], topCandidates, Math.max(3, Math.min(15, maxDimension * .01))).candidates
  const candidateIds = new Map(groupedForward.flatMap((candidate, index) => (candidate.sourceCandidateIds ?? [candidate.id]).map((id) => [id, `leak-candidate-${index + 1}`] as const)))
  return {
    points: [...observerDetection.points, ...points.map((point) => ({ ...point, candidateId: point.candidateId ? candidateIds.get(point.candidateId) ?? point.candidateId : undefined }))]
      .sort((left, right) => right.relativeStrength - left.relativeStrength)
      .slice(0, directionalSummary ? undefined : 800),
    candidates: [...observerDetection.candidates, ...groupedForward.map((candidate, index) => ({
      ...candidate,
      id: `leak-candidate-${index + 1}`,
    }))],
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
    (!clip.zUnbounded && (maximum[2] < (clip.zMin ?? -Infinity) ||
    minimum[2] > (clip.zMax ?? Infinity)))
  )
}

/** Candidate-local face lookup. Component boxes reject unrelated full-set
 * geometry before triangle bounds are inspected. The final pass uses the
 * mesh's authoritative face_component_ids instead of component.face_indices:
 * large binary scenes may intentionally truncate the latter for UI payload
 * size, while the tessellation and its ownership array remain complete. */
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
  const candidateComponentIds = new Set<number>()
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
    candidateComponentIds.add(component.component_id)
  }
  for (const [faceId, face] of scene.mesh.faces.entries()) {
    const componentId = scene.mesh.face_component_ids[faceId]
    if (componentId === null || !candidateComponentIds.has(componentId)) continue
    const minimum: Vec3 = [Infinity, Infinity, Infinity]
    const maximum: Vec3 = [-Infinity, -Infinity, -Infinity]
    for (const vertexId of face) {
      const originalVertex = scene.mesh.vertices[vertexId]
      const vertex = originalVertex
        ? transformPoint(componentId, originalVertex)
        : undefined
      if (!vertex) continue
      for (let axis = 0; axis < 3; axis += 1) {
        minimum[axis] = Math.min(minimum[axis], vertex[axis])
        maximum[axis] = Math.max(maximum[axis], vertex[axis])
      }
    }
    if (boxesOverlap(minimum, maximum, clip)) result.push(faceId)
  }
  return result
}

export function createCandidateReceiver(
  candidate: LeakPreviewCandidate,
  index: number,
  scene?: ScenePayload,
  transforms: ComponentTransformRule[] = [],
): ReceiverSpec {
  if (candidate.observerEvidence && scene) {
    const evidence = candidate.observerEvidence
    const target = candidate.representativePath?.at(-2) ?? candidate.aperturePosition ?? (evidence.footprintCenter && candidate.representativePath
      ? addScaled(evidence.footprintCenter, evidence.direction, dot(subtract(candidate.center, evidence.footprintCenter), evidence.direction))
      : candidate.representativePath?.at(-2) ?? candidate.center)
    const receiver = createViewAngleReceiver(`preview_receiver_${String(index).padStart(3, '0')}`,
      scene, target, { axis: evidence.axis, horizontal_deg: evidence.horizontalDeg,
        vertical_deg: evidence.verticalDeg, range_deg: 0, step_deg: evidence.stepDeg },
      leakPreviewReceiverDistanceMm, transforms)
    const outward = receiver.normal.map(value => -value) as Vec3
    const distance = dot(subtract(receiver.center, target), outward)
    const footprint = evidence.footprintCenter ?? evidence.receiverCenter
    const delta = subtract(footprint, target)
    const offset = subtract(delta, outward.map(value => value * dot(delta, outward)) as Vec3)
    receiver.center = receiver.center.map((value, axis) => value + offset[axis]) as Vec3
    receiver.position_offset_mm = offset
    receiver.view_distance_mm = distance
    receiver.view_distance_reference = 'leak_point'
    // Preserve coverage of the Preview grid footprint when converting from
    // its arbitrary observer U/V basis to the angle editor's canonical basis.
    // Older projects have only the padded ROI dimensions: keep their full coverage.
    const width = Math.max(1, evidence.footprintWidthMm ?? candidate.widthMm)
    const height = Math.max(1, evidence.footprintHeightMm ?? candidate.heightMm)
    const extent = (axis: Vec3) => Math.abs(dot(candidate.uAxis, axis)) * width + Math.abs(dot(candidate.vAxis, axis)) * height
    const widthMm = Math.max(1, extent(receiver.u_axis!)) + 2, heightMm = Math.max(1, extent(receiver.v_axis!)) + 2
    const pixelSize = Math.max(widthMm, heightMm) / 40
    return { ...receiver, display_name: `Preview Receiver ${index}`, reference_mode: 'leak_preview_candidate',
      acceptance_angle_deg: 90, normal_flip: false,
      observer_reference_path: candidate.representativePath && candidate.representativePath.length <= 4098 ? candidate.representativePath.map((p) => [...p] as Vec3) : null,
      width_mm: widthMm, height_mm: heightMm,
      resolution: [Math.max(8, Math.round(widthMm / pixelSize)), Math.max(8, Math.round(heightMm / pixelSize))] }
  }
  const directionId = candidate.receiverId.replace('__leak_preview_', '') as LeakPreviewDirection
  const outwardByDirection: Record<LeakPreviewDirection, Vec3> = {
    pos_x: [1, 0, 0],
    neg_x: [-1, 0, 0],
    pos_y: [0, 1, 0],
    neg_y: [0, -1, 0],
    pos_z: [0, 0, 1],
    neg_z: [0, 0, -1],
  }
  const outwardNormal = outwardByDirection[directionId] ??
    (candidate.normal.map((value) => -value) as Vec3)
  const receiverNormal = outwardNormal.map((value) => -value) as Vec3
  const uDimension = candidate.uAxis.findIndex((value) => Math.abs(value) > 0.5)
  const vDimension = candidate.vAxis.findIndex((value) => Math.abs(value) > 0.5)
  const bounds = candidate.clipBox
  const axisBounds = [
    [bounds.xMin, bounds.xMax],
    [bounds.yMin, bounds.yMax],
    [bounds.zMin ?? 0, bounds.zMax ?? 0],
  ]
  const uBounds = axisBounds[uDimension] ?? [candidate.center[0] - candidate.widthMm / 2, candidate.center[0] + candidate.widthMm / 2]
  const vBounds = axisBounds[vDimension] ?? [candidate.center[1] - candidate.heightMm / 2, candidate.center[1] + candidate.heightMm / 2]
  const footprint = scene ? candidate.detectedFootprint : undefined
  const widthMm = footprint ? Math.max(1, footprint.widthMm) + 2 : Math.max(1, uBounds[1] - uBounds[0] - leakPreviewReceiverOffsetMm * 2)
  const heightMm = footprint ? Math.max(1, footprint.heightMm) + 2 : Math.max(1, vBounds[1] - vBounds[0] - leakPreviewReceiverOffsetMm * 2)
  const pixelSize = Math.max(widthMm, heightMm) / 40
  const centeredOnRoi = [...candidate.center] as Vec3
  if (uDimension >= 0) centeredOnRoi[uDimension] = (uBounds[0] + uBounds[1]) / 2
  if (vDimension >= 0) centeredOnRoi[vDimension] = (vBounds[0] + vBounds[1]) / 2
  if (footprint) centeredOnRoi.splice(0, 3, ...footprint.center)
  let distance = leakPreviewReceiverDistanceMm
  if (scene) {
    const bounds = getLeakPreviewBounds(scene, transforms)
    for (const x of [bounds.minimum[0], bounds.maximum[0]]) for (const y of [bounds.minimum[1], bounds.maximum[1]]) for (const z of [bounds.minimum[2], bounds.maximum[2]]) {
      distance = Math.max(distance, dot(subtract([x,y,z], centeredOnRoi), outwardNormal) + leakPreviewReceiverDistanceMm)
    }
  }
  const center = candidate.observerEvidence?.receiverCenter ?? addScaled(
    centeredOnRoi,
    outwardNormal,
    distance,
  )
  return {
    ...createPreviewReceiver(
      `candidate_${index}`,
      center,
      receiverNormal,
      candidate.uAxis,
      candidate.vAxis,
      candidate.observerEvidence ? Math.max(1, candidate.widthMm - leakPreviewReceiverOffsetMm * 2) : widthMm,
      candidate.observerEvidence ? Math.max(1, candidate.heightMm - leakPreviewReceiverOffsetMm * 2) : heightMm,
    ),
    receiver_id: `preview_receiver_${String(index).padStart(3, '0')}`,
    display_name: `Preview Receiver ${index}`,
    reference_mode: 'leak_preview_candidate',
    view_distance_mm: distance,
    base_center: candidate.observerEvidence ? addScaled(center, outwardNormal, -leakPreviewReceiverDistanceMm) : [...candidate.center],
    position_offset_mm: candidate.observerEvidence ? [0, 0, 0] : centeredOnRoi.map((value, axis) => value - candidate.center[axis]) as Vec3,
    base_u_axis: [...candidate.uAxis],
    base_v_axis: [...candidate.vAxis],
    base_normal: [...receiverNormal],
    resolution: [
      Math.max(8, Math.min(120, Math.round(widthMm / pixelSize))),
      Math.max(8, Math.min(120, Math.round(heightMm / pixelSize))),
    ],
  }
}

/** Include the actual preview emission surfaces, in world coordinates. */
export function candidateRoiWithSources(scene: ScenePayload, candidate: LeakPreviewCandidate,
  emitters: EmitterSpec[], transforms: ComponentTransformRule[] = []): RoiClipBox {
  const clip: RoiClipBox = { ...candidate.clipBox, plane: 'xy', zUnbounded: true }
  const bounds = getLeakPreviewBounds(scene, transforms)
  clip.zMin = Math.min(clip.zMin ?? Infinity, bounds.center[2] - bounds.size[2] / 2 - 0.01)
  clip.zMax = Math.max(clip.zMax ?? -Infinity, bounds.center[2] + bounds.size[2] / 2 + 0.01)
  const transform = createLeakPreviewPointTransform(scene, transforms)
  const include = (point: Vec3) => {
    clip.xMin = Math.min(clip.xMin, point[0] - 0.01)
    clip.xMax = Math.max(clip.xMax, point[0] + 0.01)
    clip.yMin = Math.min(clip.yMin, point[1] - 0.01)
    clip.yMax = Math.max(clip.yMax, point[1] + 0.01)
    clip.zMin = Math.min(clip.zMin ?? Infinity, point[2] - 0.01)
    clip.zMax = Math.max(clip.zMax ?? -Infinity, point[2] + 0.01)
  }
  for (const emitter of emitters.filter((item) => item.enabled)) {
    if (emitter.emitter_type === 'face') {
      const sourceComponents = new Set(emitter.source_component_ids ?? [])
      const faceIds = new Set([...emitter.face_indices, ...scene.mesh.face_component_ids.flatMap((id, index) =>
        id != null && sourceComponents.has(id) ? [index] : [])])
      for (const faceId of faceIds) {
        const component = scene.mesh.face_component_ids[faceId]
        for (const vertexId of scene.mesh.faces[faceId] ?? []) {
          const vertex = scene.mesh.vertices[vertexId]
          if (vertex) include(component == null ? vertex : transform(component, vertex))
        }
      }
    } else if (emitter.surface_construction === 'polygon_auto' && emitter.polygon_vertices?.length) {
      emitter.polygon_vertices.forEach(include)
    } else if (emitter.center && emitter.width_mm != null && emitter.height_mm != null) {
      const uAxis = normalized(emitter.u_axis ?? [1, 0, 0])
      const rawV = emitter.v_axis ?? [0, 1, 0]
      const vAxis = normalized(subtract(rawV, addScaled([0, 0, 0], uAxis, dot(rawV, uAxis))))
      for (const u of [-1, 1]) for (const v of [-1, 1]) include(addScaled(addScaled(
        emitter.center, uAxis, u * emitter.width_mm / 2),
        vAxis, v * emitter.height_mm / 2))
    }
  }
  return clip
}
