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
    power_mode: 'total',
    power_lumen: 1,
    power_density_lm_per_m2: 100,
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

export function createDatumReceiver(
  receiverId: string,
  center: Vec3,
  rotationDeg: Vec3,
): ReceiverSpec {
  const axes = planeAxesFromRotation(rotationDeg)
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
    base_center: null,
    base_u_axis: null,
    base_v_axis: null,
    base_normal: null,
    position_offset_mm: [0, 0, 0],
    tilt_xyz_deg: [0, 0, 0],
    enabled: true,
  }
}

export function createCurrentViewReceiver(
  receiverId: string,
  frame: ViewerCameraFrame,
  distanceMm: number,
): ReceiverSpec {
  const distance = Math.max(0.001, distanceMm)
  const center: Vec3 = [
    frame.target[0] - frame.normal[0] * distance,
    frame.target[1] - frame.normal[1] * distance,
    frame.target[2] - frame.normal[2] * distance,
  ]
  return {
    ...createDatumReceiver(receiverId, center, [0, 0, 0]),
    placement_mode: 'current_view',
    center,
    normal: [...frame.normal],
    u_axis: [...frame.uAxis],
    v_axis: [...frame.vAxis],
    view_distance_mm: distance,
    base_center: center,
    base_u_axis: [...frame.uAxis],
    base_v_axis: [...frame.vAxis],
    base_normal: [...frame.normal],
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
    profiles.set(profileId, {
      profile_id: profileId,
      reflectance: compiled.reflectance,
      absorption: compiled.loss,
      specular_ratio: compiled.specularRatio,
      diffuse_ratio: compiled.diffuseRatio,
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
      })),
    excluded_component_ids: [
      ...new Set([...excludedComponentIds, ...deletedComponentIds]),
    ].sort((left, right) => left - right),
    ...(roiFaces.length > 0 ? { roi_faces: roiFaces } : {}),
    config: {
      ...config,
      ray_count: Math.max(1, totalRayCount),
    },
  }
}
