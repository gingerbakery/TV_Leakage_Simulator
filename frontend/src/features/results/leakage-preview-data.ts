import type {
  RayTraceResult,
  ScenePayload,
  TransformRule,
  Vec3,
} from '@/api'
import { createRayTraceSceneMeshSignature } from '@/features/raytracing/ray-result-source-context'

export type LeakagePreviewMethod = 'prototype_aabb'

export type LeakageAabbFace =
  | 'x_min'
  | 'x_max'
  | 'y_min'
  | 'y_max'
  | 'z_min'
  | 'z_max'

export type PrototypeLeakagePreviewUnavailableReason =
  | 'result_source_context_missing'
  | 'result_source_request_missing'
  | 'roi_trace_not_supported_by_prototype_aabb'
  | 'excluded_components_not_supported_by_prototype_aabb'
  | 'stored_receiver_paths_not_available'
  | 'receiver_path_samples_missing'
  | 'scene_mesh_signature_mismatch'
  | 'scene_aabb_invalid'

/**
 * One bounded ray sample reconstructed at the completed request's transformed
 * whole-scene AABB boundary.
 * `prototype_aabb` is a development approximation; it does not identify a
 * physical gap or a general-CAD leakage aperture.
 */
export interface LeakagePreviewSample {
  runId: string
  pathIndex: number
  receiverId: string | null
  exitPoint: Vec3
  /** One face for an ordinary slab exit; two or three mark an AABB edge or
   * corner and are deliberately excluded from continuous interpolation. */
  exitFaces: LeakageAabbFace[]
  outgoingDirection: Vec3
  weight: number
}

export interface LeakagePreviewBounds {
  minimum: Vec3
  maximum: Vec3
}

export interface LeakagePreviewReceiverCoverage {
  receiverId: string
  hitCount: number
  storedPathCount: number
  validCrossingCount: number
  /** Present only for a verified zero-power result; these are geometric paths, not light samples. */
  zeroEnergyCrossingCount?: number
  complete: boolean
}

export interface LeakagePreviewCoverage {
  resultReceiverHitCount: number
  receiverGridHitCount: number
  storedReceiverPathCount: number
  /** Geometrically verified crossings; zero-power paths are separately counted below. */
  validCrossingCount: number
  zeroEnergyCrossingCount?: number
  fullCapture: boolean
  receivers: LeakagePreviewReceiverCoverage[]
}

export type PrototypeLeakagePreviewData =
  | {
      status: 'ready'
      method: 'prototype_aabb'
      bounds: LeakagePreviewBounds
      coverage: LeakagePreviewCoverage
      samples: LeakagePreviewSample[]
    }
  | {
      status: 'unsupported'
      method: 'prototype_aabb'
      reason: PrototypeLeakagePreviewUnavailableReason
      samples: []
    }

type AxisAlignedBounds = LeakagePreviewBounds

const method: LeakagePreviewMethod = 'prototype_aabb'
const directionLengthToleranceSquared = 1e-24
const minimumAabbFaces = ['x_min', 'y_min', 'z_min'] as const
const maximumAabbFaces = ['x_max', 'y_max', 'z_max'] as const

function unsupported(
  reason: PrototypeLeakagePreviewUnavailableReason,
): PrototypeLeakagePreviewData {
  return {
    status: 'unsupported',
    method,
    reason,
    samples: [],
  }
}

function isReceiverBoundPath(
  path: RayTraceResult['stored_paths'][number],
): boolean {
  return path.at(-1)?.event_type === 'receiver'
}

export function prototypeLeakagePreviewUnavailableReason(
  result: RayTraceResult,
): PrototypeLeakagePreviewUnavailableReason | null {
  const sourceContext = result.source_context
  if (!sourceContext) return 'result_source_context_missing'
  if (sourceContext.requests.length === 0) {
    return 'result_source_request_missing'
  }
  if (
    sourceContext.requests.some(
      (request) =>
        request.roi_faces !== undefined &&
        (!Array.isArray(request.roi_faces) || request.roi_faces.length > 0),
    )
  ) {
    return 'roi_trace_not_supported_by_prototype_aabb'
  }
  if (
    sourceContext.requests.some(
      (request) =>
        !Array.isArray(request.excluded_component_ids) ||
        request.excluded_component_ids.length > 0,
    )
  ) {
    return 'excluded_components_not_supported_by_prototype_aabb'
  }
  if (
    result.receiver_hit_count > 0 &&
    !result.stored_paths.some(isReceiverBoundPath)
  ) {
    return 'stored_receiver_paths_not_available'
  }
  return null
}

function isFiniteVec3(value: readonly number[]): value is Vec3 {
  return value.length === 3 && value.every(Number.isFinite)
}

function includePoint(bounds: AxisAlignedBounds, point: Vec3): void {
  for (let axis = 0; axis < 3; axis += 1) {
    bounds.minimum[axis] = Math.min(bounds.minimum[axis], point[axis])
    bounds.maximum[axis] = Math.max(bounds.maximum[axis], point[axis])
  }
}

function transformPoint(
  point: Vec3,
  pivot: Vec3,
  rule: TransformRule,
): Vec3 | null {
  const values = [
    rule.move.x,
    rule.move.y,
    rule.move.z,
    rule.tilt.x,
    rule.tilt.y,
    rule.tilt.z,
  ]
  if (!values.every(Number.isFinite) || !isFiniteVec3(pivot)) return null

  let x = point[0] - pivot[0]
  let y = point[1] - pivot[1]
  let z = point[2] - pivot[2]
  const rotationX = rule.tilt.x * Math.PI / 180
  const rotationY = rule.tilt.y * Math.PI / 180
  const rotationZ = rule.tilt.z * Math.PI / 180

  // Keep this X -> Y -> Z order aligned with raytrace_bridge._transform_point.
  if (Math.abs(rotationX) > 1e-12) {
    const nextY = y * Math.cos(rotationX) - z * Math.sin(rotationX)
    const nextZ = y * Math.sin(rotationX) + z * Math.cos(rotationX)
    y = nextY
    z = nextZ
  }
  if (Math.abs(rotationY) > 1e-12) {
    const nextX = x * Math.cos(rotationY) + z * Math.sin(rotationY)
    const nextZ = -x * Math.sin(rotationY) + z * Math.cos(rotationY)
    x = nextX
    z = nextZ
  }
  if (Math.abs(rotationZ) > 1e-12) {
    const nextX = x * Math.cos(rotationZ) - y * Math.sin(rotationZ)
    const nextY = x * Math.sin(rotationZ) + y * Math.cos(rotationZ)
    x = nextX
    y = nextY
  }

  const transformed: Vec3 = [
    x + pivot[0] + rule.move.x,
    y + pivot[1] + rule.move.y,
    z + pivot[2] + rule.move.z,
  ]
  return isFiniteVec3(transformed) ? transformed : null
}

/**
 * Reproduce raytrace_bridge.build_transformed_mesh closely enough to derive
 * the AABB of the mesh used by the completed request. Bounds and default
 * pivots use only vertices referenced by triangle faces, then component
 * transforms are applied around the component face bounds center.
 */
function transformedSceneBounds(
  scene: ScenePayload,
  transformRules: TransformRule[],
): AxisAlignedBounds | null {
  const { faces, face_component_ids: rawComponentIds, vertices } = scene.mesh
  const hasComponentIds = rawComponentIds.length > 0
  if (
    faces.length === 0 ||
    (hasComponentIds && rawComponentIds.length !== faces.length)
  ) return null

  const enabledRules = new Map<number, TransformRule>()
  for (const rule of transformRules) {
    if (!rule.enabled || rule.target_type !== 'component') continue
    if (!Number.isSafeInteger(rule.object_id) || rule.object_id < 0) return null
    enabledRules.set(rule.object_id, rule)
  }

  // Pass 1 only retains one bbox per component for the backend's default
  // pivot. Do not retain per-face objects or vertex lists for large CAD.
  const componentBounds = new Map<number, AxisAlignedBounds>()
  for (let faceIndex = 0; faceIndex < faces.length; faceIndex += 1) {
    const face = faces[faceIndex]
    if (!Array.isArray(face) || face.length !== 3) return null
    const rawComponentId = hasComponentIds
      ? rawComponentIds[faceIndex]
      : null
    const componentId = rawComponentId === null || rawComponentId === undefined
      ? null
      : rawComponentId
    if (
      componentId !== null &&
      (!Number.isSafeInteger(componentId) || componentId < 0)
    ) return null
    if (componentId === null) continue

    let bounds = componentBounds.get(componentId)
    if (!bounds) {
      bounds = {
        minimum: [Infinity, Infinity, Infinity],
        maximum: [-Infinity, -Infinity, -Infinity],
      }
      componentBounds.set(componentId, bounds)
    }
    for (const vertexIndex of face) {
      if (!Number.isSafeInteger(vertexIndex) || vertexIndex < 0) return null
      const point = vertices[vertexIndex]
      if (!point || !isFiniteVec3(point)) return null
      includePoint(bounds, point)
    }
  }

  const pivots = new Map<number, Vec3>()
  for (const [componentId, bounds] of componentBounds) {
    pivots.set(componentId, [
      (bounds.minimum[0] + bounds.maximum[0]) / 2,
      (bounds.minimum[1] + bounds.maximum[1]) / 2,
      (bounds.minimum[2] + bounds.maximum[2]) / 2,
    ])
  }
  for (const [componentId, rule] of enabledRules) {
    if (!rule.pivot) continue
    const pivot: Vec3 = [rule.pivot.x, rule.pivot.y, rule.pivot.z]
    if (!isFiniteVec3(pivot)) return null
    pivots.set(componentId, pivot)
  }

  const minimum: Vec3 = [Infinity, Infinity, Infinity]
  const maximum: Vec3 = [-Infinity, -Infinity, -Infinity]
  const transformedBounds = { minimum, maximum }
  // Pass 2 streams transformed face vertices directly into the global bbox.
  for (let faceIndex = 0; faceIndex < faces.length; faceIndex += 1) {
    const face = faces[faceIndex]
    if (!Array.isArray(face) || face.length !== 3) return null
    const rawComponentId = hasComponentIds
      ? rawComponentIds[faceIndex]
      : null
    const componentId = rawComponentId === null || rawComponentId === undefined
      ? null
      : rawComponentId
    if (
      componentId !== null &&
      (!Number.isSafeInteger(componentId) || componentId < 0)
    ) return null
    const rule = componentId === null
      ? undefined
      : enabledRules.get(componentId)
    const pivot = componentId === null
      ? ([0, 0, 0] as Vec3)
      : (pivots.get(componentId) ?? [0, 0, 0])
    for (const vertexIndex of face) {
      if (!Number.isSafeInteger(vertexIndex) || vertexIndex < 0) return null
      const point = vertices[vertexIndex]
      if (!point || !isFiniteVec3(point)) return null
      const transformed = rule ? transformPoint(point, pivot, rule) : point
      if (!transformed) return null
      includePoint(transformedBounds, transformed)
    }
  }

  return isFiniteVec3(minimum) && isFiniteVec3(maximum)
    ? transformedBounds
    : null
}

function isInsideBounds(point: Vec3, bounds: AxisAlignedBounds): boolean {
  return point.every(
    (coordinate, axis) =>
      coordinate >= bounds.minimum[axis] &&
      coordinate <= bounds.maximum[axis],
  )
}

function segmentExitPoint(
  start: Vec3,
  end: Vec3,
  bounds: AxisAlignedBounds,
): {
  exitPoint: Vec3
  exitFaces: LeakageAabbFace[]
  outgoingDirection: Vec3
} | null {
  const delta: Vec3 = [
    end[0] - start[0],
    end[1] - start[1],
    end[2] - start[2],
  ]
  const lengthSquared =
    delta[0] * delta[0] +
    delta[1] * delta[1] +
    delta[2] * delta[2]
  if (!Number.isFinite(lengthSquared) || lengthSquared <= directionLengthToleranceSquared) {
    return null
  }

  let entryParameter = Number.NEGATIVE_INFINITY
  let exitParameter = Number.POSITIVE_INFINITY
  const axisExitFaces: Array<{
    face: LeakageAabbFace
    parameter: number
  }> = []
  for (let axis = 0; axis < 3; axis += 1) {
    const axisDelta = delta[axis]
    if (axisDelta === 0) {
      if (
        start[axis] < bounds.minimum[axis] ||
        start[axis] > bounds.maximum[axis]
      ) return null
      continue
    }

    const first = (bounds.minimum[axis] - start[axis]) / axisDelta
    const second = (bounds.maximum[axis] - start[axis]) / axisDelta
    entryParameter = Math.max(entryParameter, Math.min(first, second))
    const axisExitParameter = Math.max(first, second)
    exitParameter = Math.min(exitParameter, axisExitParameter)
    axisExitFaces.push({
      face: axisDelta > 0
        ? maximumAabbFaces[axis]
        : minimumAabbFaces[axis],
      parameter: axisExitParameter,
    })
    if (entryParameter > exitParameter) return null
  }

  if (
    !Number.isFinite(exitParameter) ||
    exitParameter < 0 ||
    exitParameter > 1
  ) return null

  const exitPoint: Vec3 = [
    start[0] + delta[0] * exitParameter,
    start[1] + delta[1] * exitParameter,
    start[2] + delta[2] * exitParameter,
  ]
  const inverseLength = 1 / Math.sqrt(lengthSquared)
  const outgoingDirection: Vec3 = [
    delta[0] * inverseLength,
    delta[1] * inverseLength,
    delta[2] * inverseLength,
  ]
  const faceTolerance = Math.max(1e-10, Math.abs(exitParameter) * 1e-10)
  const exitFaces = axisExitFaces
    .filter(
      (candidate) =>
        Math.abs(candidate.parameter - exitParameter) <= faceTolerance,
    )
    .map((candidate) => candidate.face)
  return isFiniteVec3(exitPoint) &&
    isFiniteVec3(outgoingDirection) &&
    exitFaces.length > 0
    ? { exitPoint, exitFaces, outgoingDirection }
    : null
}

function incrementCount(counts: Map<string, number>, key: string): void {
  counts.set(key, (counts.get(key) ?? 0) + 1)
}

function leakagePreviewCoverage(
  result: RayTraceResult,
  samples: LeakagePreviewSample[],
): LeakagePreviewCoverage {
  const gridHits = new Map<string, number>()
  for (const grid of result.receiver_grids) {
    if (!Number.isFinite(grid.hit_count) || grid.hit_count < 0) continue
    gridHits.set(
      grid.receiver_id,
      (gridHits.get(grid.receiver_id) ?? 0) + grid.hit_count,
    )
  }

  const storedPaths = new Map<string, number>()
  let unknownStoredReceiverPaths = 0
  for (const path of result.stored_paths) {
    const terminal = path.at(-1)
    if (terminal?.event_type !== 'receiver') continue
    if (terminal.receiver_id) incrementCount(storedPaths, terminal.receiver_id)
    else unknownStoredReceiverPaths += 1
  }

  const validCrossings = new Map<string, number>()
  let unknownValidCrossings = 0
  for (const sample of samples) {
    if (sample.receiverId) incrementCount(validCrossings, sample.receiverId)
    else unknownValidCrossings += 1
  }

  const receiverIds = [...new Set([
    ...gridHits.keys(),
    ...storedPaths.keys(),
    ...validCrossings.keys(),
  ])].sort()
  const receivers = receiverIds.map((receiverId) => {
    const hitCount = gridHits.get(receiverId) ?? 0
    const storedPathCount = storedPaths.get(receiverId) ?? 0
    const validCrossingCount = validCrossings.get(receiverId) ?? 0
    return {
      receiverId,
      hitCount,
      storedPathCount,
      validCrossingCount,
      complete:
        hitCount === storedPathCount &&
        storedPathCount === validCrossingCount,
    }
  })
  const receiverGridHitCount = [...gridHits.values()].reduce(
    (sum, count) => sum + count,
    0,
  )
  const storedReceiverPathCount =
    [...storedPaths.values()].reduce((sum, count) => sum + count, 0) +
    unknownStoredReceiverPaths
  const validCrossingCount = samples.length
  const fullCapture =
    unknownStoredReceiverPaths === 0 &&
    unknownValidCrossings === 0 &&
    result.receiver_hit_count === receiverGridHitCount &&
    receiverGridHitCount === storedReceiverPathCount &&
    storedReceiverPathCount === validCrossingCount &&
    receivers.every((receiver) => receiver.complete)

  return {
    resultReceiverHitCount: result.receiver_hit_count,
    receiverGridHitCount,
    storedReceiverPathCount,
    validCrossingCount,
    fullCapture,
    receivers,
  }
}


function explicitZeroPowerEmitters(emitters: RayTraceResult['emitters']): boolean {
  const enabled = emitters.filter((emitter) => emitter.enabled !== false)
  return enabled.length > 0 && enabled.every(
    (emitter) => emitter.power_mode === 'total' && emitter.power_lumen === 0,
  )
}

/**
 * Zero source power may still produce geometric receiver hits. Accept an empty
 * light display only when every saved energy value, receiver grid and metric
 * explicitly confirms zero, and all receiver paths geometrically cross the
 * completed scene boundary. Missing or invalid observations remain unsupported.
 */
function verifiedZeroEnergyCoverage(
  result: RayTraceResult,
  bounds: AxisAlignedBounds,
): LeakagePreviewCoverage | null {
  if (
    !explicitZeroPowerEmitters(result.emitters) ||
    !result.source_context?.requests.every((request) => explicitZeroPowerEmitters(request.emitters)) ||
    result.config.store_ray_paths !== true ||
    !Number.isSafeInteger(result.receiver_hit_count) || result.receiver_hit_count <= 0 ||
    !Number.isSafeInteger(result.total_rays) || result.total_rays < result.receiver_hit_count
  ) return null
  const contribution = result.contribution_summary
  if (
    contribution.direct_receiver_flux_lumen !== 0 || contribution.reflected_receiver_flux_lumen !== 0 ||
    !Number.isSafeInteger(contribution.direct_receiver_hit_count) || contribution.direct_receiver_hit_count < 0 ||
    !Number.isSafeInteger(contribution.reflected_receiver_hit_count) || contribution.reflected_receiver_hit_count < 0 ||
    contribution.direct_receiver_hit_count + contribution.reflected_receiver_hit_count !== result.receiver_hit_count
  ) return null
  const receiverIds = new Set<string>()
  for (const grid of result.receiver_grids) {
    const [width, height] = grid.resolution
    const zeroGrid = (values: number[][]): boolean => values.length === height &&
      values.every((row) => row.length === width && row.every((value) => value === 0))
    if (
      !grid.receiver_id || receiverIds.has(grid.receiver_id) ||
      !Number.isSafeInteger(grid.hit_count) || grid.hit_count < 0 ||
      !Number.isSafeInteger(width) || width <= 0 || !Number.isSafeInteger(height) || height <= 0 ||
      !Number.isFinite(grid.bin_area_mm2) || grid.bin_area_mm2 <= 0 ||
      !zeroGrid(grid.flux_lumen) ||
      (grid.flux_squared_lumen2 !== undefined && grid.flux_squared_lumen2 !== 0) ||
      (grid.flux_squared_lumen2_grid !== undefined && !zeroGrid(grid.flux_squared_lumen2_grid))
    ) return null
    receiverIds.add(grid.receiver_id)
    const rawMetric = result.metrics[grid.receiver_id]
    if (!rawMetric || typeof rawMetric !== 'object' || Array.isArray(rawMetric)) return null
    const metric = rawMetric as Record<string, unknown>
    if (
      metric.hit_count !== grid.hit_count || metric.total_flux_lumen !== 0 ||
      metric.peak_nit_est !== 0 || metric.mean_nit_est !== 0 || metric.p95_nit_est !== 0
    ) return null
  }
  const zeroCrossings: LeakagePreviewSample[] = []
  for (let pathIndex = 0; pathIndex < result.stored_paths.length; pathIndex += 1) {
    const path = result.stored_paths[pathIndex]
    if (path.length === 0 || path.some((hit) =>
      !isFiniteVec3(hit.point) || !isFiniteVec3(hit.normal) ||
      !Number.isFinite(hit.distance_mm) || hit.distance_mm < 0 ||
      hit.incoming_energy_lumen !== 0 || hit.outgoing_energy_lumen !== 0 ||
      (hit.receiver_flux_lumen !== null && hit.receiver_flux_lumen !== undefined && hit.receiver_flux_lumen !== 0)
    )) return null
    const terminal = path.at(-1)!
    if (terminal.event_type !== 'receiver') continue
    const previous = path.at(-2)
    if (
      !previous || !terminal.receiver_id || !receiverIds.has(terminal.receiver_id) || terminal.receiver_flux_lumen !== 0 ||
      !isInsideBounds(previous.point, bounds) || isInsideBounds(terminal.point, bounds)
    ) return null
    const crossing = segmentExitPoint(previous.point, terminal.point, bounds)
    if (!crossing) return null
    zeroCrossings.push({
      runId: result.run_id, pathIndex, receiverId: terminal.receiver_id,
      ...crossing, weight: 0,
    })
  }
  const coverage = leakagePreviewCoverage(result, zeroCrossings)
  if (!coverage.fullCapture) return null
  return {
    ...coverage,
    zeroEnergyCrossingCount: coverage.validCrossingCount,
    receivers: coverage.receivers.map((receiver) => ({
      ...receiver, zeroEnergyCrossingCount: receiver.validCrossingCount,
    })),
  }
}

/**
 * Reconstruct leakage candidates where a receiver-bound terminal segment
 * leaves the whole-scene AABB. This `prototype_aabb` method deliberately
 * fails closed for unbound, ROI-traced, component-excluded, or scene-mismatched
 * results and must not be presented as automatic physical-gap detection for
 * general CAD.
 */
export function buildPrototypeLeakagePreviewData(
  result: RayTraceResult,
  scene: ScenePayload,
): PrototypeLeakagePreviewData {
  const resultUnavailableReason =
    prototypeLeakagePreviewUnavailableReason(result)
  if (resultUnavailableReason) return unsupported(resultUnavailableReason)

  const sourceContext = result.source_context!
  const request = sourceContext.requests.at(-1)
  if (!request) return unsupported('result_source_request_missing')

  if (
    sourceContext.scene.mesh_signature !==
    createRayTraceSceneMeshSignature(scene)
  ) {
    return unsupported('scene_mesh_signature_mismatch')
  }

  const bounds = transformedSceneBounds(scene, request.transform_rules)
  if (!bounds) return unsupported('scene_aabb_invalid')

  const samples: LeakagePreviewSample[] = []
  for (let pathIndex = 0; pathIndex < result.stored_paths.length; pathIndex += 1) {
    const path = result.stored_paths[pathIndex]
    if (path.length < 2) continue
    const terminal = path.at(-1)
    const previous = path.at(-2)
    if (!terminal || !previous || terminal.event_type !== 'receiver') continue
    if (!isFiniteVec3(previous.point) || !isFiniteVec3(terminal.point)) continue
    if (!isInsideBounds(previous.point, bounds)) continue
    if (isInsideBounds(terminal.point, bounds)) continue

    const weight = terminal.incoming_energy_lumen
    if (!Number.isFinite(weight) || weight <= 0) continue
    const crossing = segmentExitPoint(
      previous.point,
      terminal.point,
      bounds,
    )
    if (!crossing) continue

    samples.push({
      runId: result.run_id,
      pathIndex,
      receiverId: terminal.receiver_id,
      exitPoint: crossing.exitPoint,
      exitFaces: crossing.exitFaces,
      outgoingDirection: crossing.outgoingDirection,
      weight,
    })
  }

  if (result.receiver_hit_count > 0 && samples.length === 0) {
    const zeroEnergyCoverage = verifiedZeroEnergyCoverage(result, bounds)
    if (!zeroEnergyCoverage) return unsupported('receiver_path_samples_missing')
    return { status: 'ready', method, bounds: structuredClone(bounds), coverage: zeroEnergyCoverage, samples: [] }
  }

  return {
    status: 'ready',
    method,
    bounds: structuredClone(bounds),
    coverage: leakagePreviewCoverage(result, samples),
    samples,
  }
}
