import type {
  EmitterSpec,
  OpticalAssignment,
  OpticalProfile,
  RayTraceConfigRequest,
  RayTraceRequest,
  ReceiverSpec,
  ScenePayload,
  Vec3,
} from '@/api'
import {
  compileOpticalProfile,
} from '@/features/materials'
import type {
  ComponentTransformRule,
  MaterialAssignment,
  RoiScope,
} from '@/stores'

export interface ViewerCameraFrame {
  target: Vec3
  normal: Vec3
  uAxis: Vec3
  vAxis: Vec3
}

export interface RayTraceRequestSource {
  scene: ScenePayload
  projectName: string
  emitters: EmitterSpec[]
  receivers: ReceiverSpec[]
  materialAssignments: MaterialAssignment[]
  transformRules: ComponentTransformRule[]
  excludedComponentIds: number[]
  deletedComponentIds: number[]
  roiScopes: RoiScope[]
  config: RayTraceConfigRequest
}

function toRadians(value: number): number {
  return (value * Math.PI) / 180
}

function toDegrees(value: number): number {
  return (value * 180) / Math.PI
}

function rotateX([x, y, z]: Vec3, angle: number): Vec3 {
  const cosine = Math.cos(angle)
  const sine = Math.sin(angle)
  return [x, y * cosine - z * sine, y * sine + z * cosine]
}

function rotateY([x, y, z]: Vec3, angle: number): Vec3 {
  const cosine = Math.cos(angle)
  const sine = Math.sin(angle)
  return [x * cosine + z * sine, y, -x * sine + z * cosine]
}

function rotateZ([x, y, z]: Vec3, angle: number): Vec3 {
  const cosine = Math.cos(angle)
  const sine = Math.sin(angle)
  return [x * cosine - y * sine, x * sine + y * cosine, z]
}

function rotateVector(vector: Vec3, rotationDeg: Vec3): Vec3 {
  return rotateZ(
    rotateY(
      rotateX(vector, toRadians(rotationDeg[0])),
      toRadians(rotationDeg[1]),
    ),
    toRadians(rotationDeg[2]),
  )
}

export function planeAxesFromRotation(rotationDeg: Vec3): {
  normal: Vec3
  uAxis: Vec3
  vAxis: Vec3
} {
  return {
    uAxis: rotateVector([1, 0, 0], rotationDeg),
    vAxis: rotateVector([0, 1, 0], rotationDeg),
    normal: rotateVector([0, 0, 1], rotationDeg),
  }
}

export function rotationFromPlaneAxes(
  uAxis: Vec3 | null,
  vAxis: Vec3 | null,
  normal: Vec3 | null,
): Vec3 {
  if (!uAxis || !vAxis || !normal) return [0, 0, 0]
  const rotationY = Math.asin(
    Math.max(-1, Math.min(1, -uAxis[2])),
  )
  const cosineY = Math.cos(rotationY)
  const rotationX =
    Math.abs(cosineY) > 1e-7
      ? Math.atan2(vAxis[2], normal[2])
      : 0
  const rotationZ =
    Math.abs(cosineY) > 1e-7
      ? Math.atan2(uAxis[1], uAxis[0])
      : Math.atan2(-vAxis[0], vAxis[1])
  return [
    toDegrees(rotationX),
    toDegrees(rotationY),
    toDegrees(rotationZ),
  ]
}

export function nextSpecId(
  prefix: 'emitter' | 'receiver',
  currentIds: Iterable<string>,
): string {
  let maximum = 0
  const pattern = new RegExp(`^${prefix}_(\\d+)$`)
  for (const id of currentIds) {
    const match = pattern.exec(id)
    if (match) maximum = Math.max(maximum, Number(match[1]) || 0)
  }
  return `${prefix}_${String(maximum + 1).padStart(3, '0')}`
}

export function createFaceEmitter(
  emitterId: string,
  faceIds: number[],
): EmitterSpec {
  return {
    emitter_id: emitterId,
    emitter_type: 'face',
    face_indices: [...new Set(faceIds)].sort((left, right) => left - right),
    normal_mode: 'face_normal',
    normal_flip: false,
    custom_normal: null,
    direction_distribution: 'lambertian',
    gaussian_sigma_deg: 12,
    power_mode: 'set_luminance',
    power_lumen: 1,
    power_density_lm_per_m2: 100,
    luminance_nit: 500,
    center: null,
    u_axis: null,
    v_axis: null,
    width_mm: null,
    height_mm: null,
    reference_mode: null,
    surface_construction: 'rectangular_fit',
    polygon_vertices: [],
    reference_vertex_indices: [],
    reference_edge_vertex_indices: [],
    reference_vertex_points: [],
    reference_edge_points: [],
    ray_count: 10_000,
    seed: null,
    enabled: true,
  }
}

export function createDatumEmitter(
  emitterId: string,
  center: Vec3,
  rotationDeg: Vec3,
): EmitterSpec {
  const axes = planeAxesFromRotation(rotationDeg)
  return {
    ...createFaceEmitter(emitterId, []),
    emitter_type: 'datum_plane',
    normal_mode: 'custom',
    custom_normal: axes.normal,
    center,
    u_axis: axes.uAxis,
    v_axis: axes.vAxis,
    width_mm: 20,
    height_mm: 20,
  }
}

function vectorLength(vector: Vec3): number {
  return Math.sqrt(
    vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2],
  )
}

function normalizeVector(vector: Vec3): Vec3 {
  const length = vectorLength(vector)
  if (length < 1e-9) return [0, 0, 1]
  return [vector[0] / length, vector[1] / length, vector[2] / length]
}

function crossVector(a: Vec3, b: Vec3): Vec3 {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ]
}

/** Canonical (u, v) basis for a given face normal, used to turn a picked
 * CAD face into a Rotation X/Y/Z the datum-plane editor can display and
 * further adjust - same "pick any stable perpendicular" approach as most
 * CAD tools use when only a normal (no in-plane reference) is available. */
export function axesFromNormal(normal: Vec3): {
  uAxis: Vec3
  vAxis: Vec3
} {
  const n = normalizeVector(normal)
  // Keep Receiver heatmaps aligned with the product/front-view convention:
  // project world +X onto the picked plane for heatmap +X, then derive +Y.
  // Side faces parallel to world X fall back to projected world +Y.
  const preferred: Vec3 = Math.abs(n[0]) < 0.95 ? [1, 0, 0] : [0, 1, 0]
  const projection =
    preferred[0] * n[0] + preferred[1] * n[1] + preferred[2] * n[2]
  const uAxis = normalizeVector([
    preferred[0] - n[0] * projection,
    preferred[1] - n[1] * projection,
    preferred[2] - n[2] * projection,
  ])
  const vAxis = crossVector(n, uAxis)
  return { uAxis, vAxis }
}

export function createDatumReceiver(
  receiverId: string,
  baseCenter: Vec3,
  rotationDeg: Vec3,
  positionOffset: Vec3 = [0, 0, 0],
  pivot: Vec3 | null = null,
): ReceiverSpec {
  const axes = planeAxesFromRotation(rotationDeg)
  const centerBeforeTilt: Vec3 = [
    baseCenter[0] + positionOffset[0],
    baseCenter[1] + positionOffset[1],
    baseCenter[2] + positionOffset[2],
  ]
  const pivotPoint = pivot ?? centerBeforeTilt
  // The axes above already encode the full absolute orientation (rotated
  // straight from the canonical [0,0,1]/[1,0,0]/[0,1,0] frame), so only
  // the position needs a pivot correction: revolve the offset-from-pivot
  // by the same rotation to find where the plane ends up. With no custom
  // pivot (pivot === centerBeforeTilt) this delta is zero and the center
  // is unaffected by rotationDeg, same as before this field existed.
  const delta: Vec3 = [
    centerBeforeTilt[0] - pivotPoint[0],
    centerBeforeTilt[1] - pivotPoint[1],
    centerBeforeTilt[2] - pivotPoint[2],
  ]
  const rotatedDelta = rotateVector(delta, rotationDeg)
  const center: Vec3 = [
    pivotPoint[0] + rotatedDelta[0],
    pivotPoint[1] + rotatedDelta[1],
    pivotPoint[2] + rotatedDelta[2],
  ]
  return {
    receiver_id: receiverId,
    receiver_type: 'rectangle',
    display_name: receiverId,
    placement_mode: 'datum_plane',
    center,
    normal: axes.normal,
    u_axis: axes.uAxis,
    v_axis: axes.vAxis,
    width_mm: 30,
    height_mm: 30,
    resolution: [80, 24],
    acceptance_angle_deg: 90,
    normal_flip: false,
    reference_mode: null,
    reference_vertex_indices: [],
    reference_edge_vertex_indices: [],
    reference_vertex_points: [],
    reference_edge_points: [],
    view_distance_mm: null,
    base_center: baseCenter,
    base_u_axis: null,
    base_v_axis: null,
    base_normal: null,
    position_offset_mm: positionOffset,
    tilt_xyz_deg: rotationDeg,
    pivot,
    enabled: true,
  }
}

export function createCurrentViewReceiver(
  receiverId: string,
  frame: ViewerCameraFrame,
  distanceMm: number,
  positionOffset: Vec3 = [0, 0, 0],
  tiltDeg: Vec3 = [0, 0, 0],
): ReceiverSpec {
  const distance = Math.max(0.001, distanceMm)
  const baseCenter: Vec3 = [
    frame.target[0] - frame.normal[0] * distance,
    frame.target[1] - frame.normal[1] * distance,
    frame.target[2] - frame.normal[2] * distance,
  ]
  const center: Vec3 = [
    baseCenter[0] + positionOffset[0],
    baseCenter[1] + positionOffset[1],
    baseCenter[2] + positionOffset[2],
  ]
  const viewingNormal = rotateVector(frame.normal, tiltDeg)
  const uAxis = rotateVector(frame.uAxis, tiltDeg)
  // viewerCameraFrame.vAxis points toward screen-bottom. Receiver local +Y
  // must point toward screen-top, while its stored right-handed normal stays
  // opposite the viewing/acceptance direction and is flipped for tracing.
  const vAxis = rotateVector(
    [-frame.vAxis[0], -frame.vAxis[1], -frame.vAxis[2]],
    tiltDeg,
  )
  const opposite = (value: number) => value === 0 ? 0 : -value
  const normal: Vec3 = viewingNormal.map(opposite) as Vec3
  return {
    ...createDatumReceiver(receiverId, center, [0, 0, 0]),
    placement_mode: 'current_view',
    center,
    normal,
    u_axis: uAxis,
    v_axis: vAxis,
    normal_flip: true,
    view_distance_mm: distance,
    base_center: baseCenter,
    base_u_axis: [...frame.uAxis],
    base_v_axis: [...frame.vAxis],
    base_normal: [...frame.normal],
    position_offset_mm: [...positionOffset],
    tilt_xyz_deg: [...tiltDeg],
  }
}

function buildOpticalPayload(assignments: MaterialAssignment[]): {
  profiles: OpticalProfile[]
  assignments: OpticalAssignment[]
} {
  const profiles = new Map<string, OpticalProfile>()
  const opticalAssignments: OpticalAssignment[] = []

  for (const [priority, assignment] of assignments.entries()) {
    if (!assignment.enabled) continue
    const profileId =
      assignment.profileId.trim() || `compiled-${assignment.assignmentId}`
    const compiled = compileOpticalProfile(
      assignment.baseMaterialId,
      assignment.surfaceId,
    )
    const custom = assignment.opticalOverride
    profiles.set(profileId, {
      profile_id: profileId,
      reflectance: custom?.reflectance ?? compiled.reflectance,
      absorption: custom?.loss ?? compiled.loss,
      specular_ratio: custom?.specularRatio ?? compiled.specularRatio,
      diffuse_ratio: custom?.diffuseRatio ?? compiled.diffuseRatio,
      scatter_model: compiled.scatterModel,
      roughness: compiled.roughness,
      gaussian_sigma_deg: compiled.scatterSigmaDeg,
      bsdf_asset_id: assignment.bsdfAssetId || null,
      notes: `Compiled from ${assignment.baseMaterialId} / ${assignment.surfaceId}`,
    })
    opticalAssignments.push({
      assignment_id: assignment.assignmentId,
      target_type: assignment.targetType,
      component_id: assignment.componentId,
      profile_id: profileId,
      face_indices:
        assignment.targetType === 'faces' ? assignment.faceIds : [],
      priority,
      enabled: true,
    })
  }

  return {
    profiles: [...profiles.values()],
    assignments: opticalAssignments,
  }
}

function activeRoiFaces(
  scopes: RoiScope[],
  deletedComponentIds: Set<number>,
): number[] {
  return [
    ...new Set(
      scopes
        .filter((scope) => scope.active)
        .flatMap((scope) =>
          scope.components
            .filter(
              (component) =>
                !deletedComponentIds.has(component.componentId),
            )
            .flatMap((component) => component.faceIds),
        ),
    ),
  ].sort((left, right) => left - right)
}

export function buildRayTraceRequest({
  scene,
  projectName,
  emitters,
  receivers,
  materialAssignments,
  transformRules,
  excludedComponentIds,
  deletedComponentIds,
  roiScopes,
  config,
}: RayTraceRequestSource): RayTraceRequest {
  const enabledEmitters = emitters.filter((emitter) => emitter.enabled)
  const enabledReceivers = receivers.filter((receiver) => receiver.enabled)
  const totalRayCount = enabledEmitters.reduce(
    (sum, emitter) => sum + Math.max(1, emitter.ray_count),
    0,
  )
  const optical = buildOpticalPayload(materialAssignments)
  const deleted = new Set(deletedComponentIds)
  const roiFaces = activeRoiFaces(roiScopes, deleted)
  const {
    auto_convergence: _autoConvergence,
    convergence_target_percent: _convergenceTarget,
    max_convergence_multiplier: _maxConvergenceMultiplier,
    ...backendConfig
  } = config

  return {
    scene_token: scene.metadata.scene_token,
    project_name: projectName.trim() || 'TV-Leakage-Direct',
    emitters: enabledEmitters,
    receivers: enabledReceivers,
    optical_profiles: optical.profiles,
    optical_assignments: optical.assignments,
    transform_rules: transformRules
      .filter(
        (rule) =>
          rule.enabled &&
          rule.targetType === 'component' &&
          !deleted.has(rule.componentId),
      )
      .map((rule) => ({
        rule_id: rule.ruleId,
        target_type: 'component',
        object_id: rule.componentId,
        label: rule.ruleId,
        enabled: true,
        move: rule.move,
        tilt: rule.tilt,
        ...(rule.pivot ? { pivot: rule.pivot } : {}),
      })),
    excluded_component_ids: [
      ...new Set([
        ...excludedComponentIds,
        ...deletedComponentIds,
      ]),
    ].sort((left, right) => left - right),
    ...(roiFaces.length > 0 ? { roi_faces: roiFaces } : {}),
    config: {
      ...backendConfig,
      ray_count: Math.max(1, totalRayCount),
    },
  }
}
