import type {
  EmitterSpec,
  RayTraceConfigRequest,
  ReceiverSpec,
  ScenePayload,
} from '@/api'
import type {
  ActiveCad,
  ComponentTransformRule,
  MaterialAssignment,
  RayPathDisplayFilters,
  RoiClipBox,
  RoiComponentClip,
  RoiScope,
  Vector3Value,
  WorkspaceProjectState,
  WorkspaceSnapshot,
} from '@/stores'

export const bitsamFileExtension = '.bitsam'
export const bitsamSchemaVersion = 'bitsam-project.v1'

const bitsamFormat = 'tv-leakage-simulator-project'
const applicationVersion = '1.0.0'

interface BitsamSceneFingerprint {
  scene_schema_version: ScenePayload['schema_version']
  face_count: number
  vertex_count: number
  component_count: number
  geometry_signature: string
}

export interface BitsamCadReference {
  display_name: string
  file_extension: string
  fingerprint: BitsamSceneFingerprint
}

export interface BitsamProject {
  format: typeof bitsamFormat
  schema_version: typeof bitsamSchemaVersion
  application_version: string
  saved_at: string
  project_name: string
  cad: BitsamCadReference
  workspace: WorkspaceProjectState
}

export interface BitsamCompatibility {
  compatible: boolean
  reasons: string[]
  warnings: string[]
}

export class BitsamProjectError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'BitsamProjectError'
  }
}

type UnknownRecord = Record<string, unknown>

function isRecord(value: unknown): value is UnknownRecord {
  return (
    typeof value === 'object' &&
    value !== null &&
    !Array.isArray(value)
  )
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isSafeId(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0
}

function isString(value: unknown): value is string {
  return typeof value === 'string'
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === 'boolean'
}

function isStringOrNull(value: unknown): value is string | null {
  return value === null || isString(value)
}

function isNumberOrNull(value: unknown): value is number | null {
  return value === null || isFiniteNumber(value)
}

function isOneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
): value is T {
  return isString(value) && allowed.includes(value as T)
}

function isArrayOf<T>(
  value: unknown,
  guard: (item: unknown) => item is T,
): value is T[] {
  return Array.isArray(value) && value.every(guard)
}

function isIdArray(value: unknown): value is number[] {
  return isArrayOf(value, isSafeId)
}

function isVec3(value: unknown): value is [number, number, number] {
  return (
    Array.isArray(value) &&
    value.length === 3 &&
    value.every(isFiniteNumber)
  )
}

function isIndexPair(value: unknown): value is [number, number] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    value.every(isSafeId)
  )
}

function isVec3Pair(
  value: unknown,
): value is [[number, number, number], [number, number, number]] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    value.every(isVec3)
  )
}

function isVector3Value(value: unknown): value is Vector3Value {
  return (
    isRecord(value) &&
    isFiniteNumber(value.x) &&
    isFiniteNumber(value.y) &&
    isFiniteNumber(value.z)
  )
}

function isMaterialAssignment(
  value: unknown,
): value is MaterialAssignment {
  return (
    isRecord(value) &&
    isString(value.assignmentId) &&
    isSafeId(value.componentId) &&
    isOneOf(value.targetType, ['part', 'faces']) &&
    isIdArray(value.faceIds) &&
    isString(value.baseMaterialId) &&
    isString(value.surfaceId) &&
    isString(value.profileId) &&
    isString(value.bsdfAssetId) &&
    isBoolean(value.enabled)
  )
}

function isTransformRule(
  value: unknown,
): value is ComponentTransformRule {
  return (
    isRecord(value) &&
    isString(value.ruleId) &&
    isSafeId(value.componentId) &&
    isOneOf(value.targetType, ['component', 'faces']) &&
    isOneOf(value.selectionMethod, ['click', 'box']) &&
    isIdArray(value.faceIds) &&
    isVector3Value(value.move) &&
    isVector3Value(value.tilt) &&
    isBoolean(value.enabled)
  )
}

function isRoiClipBox(value: unknown): value is RoiClipBox {
  if (!isRecord(value)) return false
  if (
    value.plane !== undefined &&
    !isOneOf(value.plane, ['xy', 'yz', 'zx'])
  ) {
    return false
  }

  return (
    isFiniteNumber(value.xMin) &&
    isFiniteNumber(value.xMax) &&
    isFiniteNumber(value.yMin) &&
    isFiniteNumber(value.yMax) &&
    (value.zMin === undefined || isFiniteNumber(value.zMin)) &&
    (value.zMax === undefined || isFiniteNumber(value.zMax))
  )
}

function isRoiComponentClip(
  value: unknown,
): value is RoiComponentClip {
  return (
    isRecord(value) &&
    isSafeId(value.componentId) &&
    isString(value.componentName) &&
    isIdArray(value.faceIds) &&
    isFiniteNumber(value.areaMm2) &&
    isVector3Value(value.bboxMin) &&
    isVector3Value(value.bboxMax)
  )
}

function isRoiScope(value: unknown): value is RoiScope {
  return (
    isRecord(value) &&
    isString(value.id) &&
    isString(value.scopeId) &&
    isOneOf(value.source, ['box', 'point']) &&
    isOneOf(value.view, [
      'front_xy',
      'back_neg_xy',
      'front_yz',
      'back_neg_yz',
      'front_zx',
      'back_neg_zx',
      'coordinate',
    ]) &&
    isArrayOf(value.components, isRoiComponentClip) &&
    isBoolean(value.active) &&
    (value.clipBox === undefined || isRoiClipBox(value.clipBox)) &&
    (value.point === undefined || isVector3Value(value.point))
  )
}

function isEmitterSpec(value: unknown): value is EmitterSpec {
  return (
    isRecord(value) &&
    isString(value.emitter_id) &&
    isOneOf(value.emitter_type, [
      'face',
      'datum_plane',
      'reference_plane',
    ]) &&
    isIdArray(value.face_indices) &&
    isOneOf(value.normal_mode, ['face_normal', 'custom']) &&
    isBoolean(value.normal_flip) &&
    (value.custom_normal === null || isVec3(value.custom_normal)) &&
    isOneOf(value.direction_distribution, [
      'lambertian',
      'isotropic',
      'gaussian',
    ]) &&
    isFiniteNumber(value.gaussian_sigma_deg) &&
    isOneOf(value.power_mode, ['total', 'power_per_area']) &&
    isFiniteNumber(value.power_lumen) &&
    isFiniteNumber(value.power_density_lm_per_m2) &&
    (value.center === null || isVec3(value.center)) &&
    (value.u_axis === null || isVec3(value.u_axis)) &&
    (value.v_axis === null || isVec3(value.v_axis)) &&
    isNumberOrNull(value.width_mm) &&
    isNumberOrNull(value.height_mm) &&
    isStringOrNull(value.reference_mode) &&
    isOneOf(value.surface_construction, [
      'rectangular_fit',
      'polygon_auto',
    ]) &&
    isArrayOf(value.polygon_vertices, isVec3) &&
    isIdArray(value.reference_vertex_indices) &&
    isArrayOf(value.reference_edge_vertex_indices, isIndexPair) &&
    isArrayOf(value.reference_vertex_points, isVec3) &&
    isArrayOf(value.reference_edge_points, isVec3Pair) &&
    isFiniteNumber(value.ray_count) &&
    isNumberOrNull(value.seed) &&
    isBoolean(value.enabled)
  )
}

function isReceiverSpec(value: unknown): value is ReceiverSpec {
  return (
    isRecord(value) &&
    isString(value.receiver_id) &&
    value.receiver_type === 'rectangle' &&
    isString(value.display_name) &&
    isOneOf(value.placement_mode, [
      'datum_plane',
      'reference_plane',
      'current_view',
    ]) &&
    isVec3(value.center) &&
    isVec3(value.normal) &&
    (value.u_axis === null || isVec3(value.u_axis)) &&
    (value.v_axis === null || isVec3(value.v_axis)) &&
    isFiniteNumber(value.width_mm) &&
    isFiniteNumber(value.height_mm) &&
    isIndexPair(value.resolution) &&
    isFiniteNumber(value.acceptance_angle_deg) &&
    isBoolean(value.normal_flip) &&
    isStringOrNull(value.reference_mode) &&
    isIdArray(value.reference_vertex_indices) &&
    isArrayOf(value.reference_edge_vertex_indices, isIndexPair) &&
    isArrayOf(value.reference_vertex_points, isVec3) &&
    isArrayOf(value.reference_edge_points, isVec3Pair) &&
    isNumberOrNull(value.view_distance_mm) &&
    (value.base_center === null || isVec3(value.base_center)) &&
    (value.base_u_axis === null || isVec3(value.base_u_axis)) &&
    (value.base_v_axis === null || isVec3(value.base_v_axis)) &&
    (value.base_normal === null || isVec3(value.base_normal)) &&
    isVec3(value.position_offset_mm) &&
    isVec3(value.tilt_xyz_deg) &&
    isBoolean(value.enabled)
  )
}

function isRayTraceConfig(
  value: unknown,
): value is RayTraceConfigRequest {
  return (
    isRecord(value) &&
    isFiniteNumber(value.ray_count) &&
    isFiniteNumber(value.max_depth) &&
    isFiniteNumber(value.seed) &&
    isFiniteNumber(value.min_energy) &&
    isFiniteNumber(value.epsilon_mm) &&
    isFiniteNumber(value.k_abs) &&
    isFiniteNumber(value.k_brdf) &&
    isOneOf(value.termination_mode, [
      'threshold',
      'russian_roulette',
    ]) &&
    isOneOf(value.contribution_mode, ['summary', 'detailed']) &&
    (value.intersection_backend === undefined ||
      isOneOf(value.intersection_backend, [
        'auto',
        'brute_force',
        'bvh',
      ])) &&
    isBoolean(value.store_ray_paths) &&
    isFiniteNumber(value.max_stored_paths)
  )
}

function isRayPathDisplayFilters(
  value: unknown,
): value is RayPathDisplayFilters {
  return (
    isRecord(value) &&
    isBoolean(value.receiver_direct) &&
    isBoolean(value.receiver_reflected) &&
    isBoolean(value.direct) &&
    isBoolean(value.specular) &&
    isBoolean(value.lambertian) &&
    isBoolean(value.gaussian)
  )
}

function isComponentNameOverrides(
  value: unknown,
): value is Record<number, string> {
  return (
    isRecord(value) &&
    Object.entries(value).every(
      ([componentId, name]) =>
        isSafeId(Number(componentId)) && isString(name),
    )
  )
}

function isWorkspaceProjectState(
  value: unknown,
): value is WorkspaceProjectState {
  return (
    isRecord(value) &&
    isIdArray(value.hiddenComponentIds) &&
    isIdArray(value.excludedComponentIds) &&
    isIdArray(value.deletedComponentIds) &&
    isComponentNameOverrides(value.componentNameOverrides) &&
    isArrayOf(value.materialAssignments, isMaterialAssignment) &&
    isArrayOf(value.transformRules, isTransformRule) &&
    isArrayOf(value.roiScopes, isRoiScope) &&
    isFiniteNumber(value.roiScopeSequence) &&
    isArrayOf(value.emitters, isEmitterSpec) &&
    isArrayOf(value.receivers, isReceiverSpec) &&
    isRayTraceConfig(value.rayTraceConfig) &&
    isRayPathDisplayFilters(value.rayPathDisplayFilters)
  )
}

function isSceneFingerprint(
  value: unknown,
): value is BitsamSceneFingerprint {
  return (
    isRecord(value) &&
    value.scene_schema_version === 'mesh-scene.v1' &&
    isSafeId(value.face_count) &&
    isSafeId(value.vertex_count) &&
    isSafeId(value.component_count) &&
    isString(value.geometry_signature)
  )
}

function isBitsamCadReference(
  value: unknown,
): value is BitsamCadReference {
  return (
    isRecord(value) &&
    isString(value.display_name) &&
    isString(value.file_extension) &&
    isSceneFingerprint(value.fingerprint)
  )
}

function roundFingerprintValue(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000
}

function createGeometrySignature(scene: ScenePayload): string {
  const signatureSource = JSON.stringify(
    [...scene.components]
      .sort(
        (left, right) =>
          left.component_id - right.component_id,
      )
      .map((component) => ({
        component_id: component.component_id,
        object_id: component.object_id,
        face_count: component.face_count,
        bbox_min: component.bbox_min.map(roundFingerprintValue),
        bbox_max: component.bbox_max.map(roundFingerprintValue),
      })),
  )
  let hash = 0x811c9dc5
  for (let index = 0; index < signatureSource.length; index += 1) {
    hash ^= signatureSource.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return `fnv1a32:${(hash >>> 0).toString(16).padStart(8, '0')}`
}

function createSceneFingerprint(
  scene: ScenePayload,
): BitsamSceneFingerprint {
  return {
    scene_schema_version: scene.schema_version,
    face_count: scene.metadata.face_count,
    vertex_count: scene.metadata.vertex_count,
    component_count: scene.metadata.component_count,
    geometry_signature: createGeometrySignature(scene),
  }
}

function extensionFromFileName(fileName: string): string {
  const extensionIndex = fileName.lastIndexOf('.')
  return extensionIndex >= 0
    ? fileName.slice(extensionIndex).toLowerCase()
    : ''
}

function projectNameFromFileName(fileName: string): string {
  const extensionIndex = fileName.lastIndexOf('.')
  return (extensionIndex > 0
    ? fileName.slice(0, extensionIndex)
    : fileName
  ).trim()
}

function createWorkspaceProjectState(
  workspace: WorkspaceSnapshot,
): WorkspaceProjectState {
  return structuredClone({
    hiddenComponentIds: workspace.hiddenComponentIds,
    excludedComponentIds: workspace.excludedComponentIds,
    deletedComponentIds: workspace.deletedComponentIds,
    componentNameOverrides: workspace.componentNameOverrides,
    materialAssignments: workspace.materialAssignments,
    transformRules: workspace.transformRules,
    roiScopes: workspace.roiScopes,
    roiScopeSequence: workspace.roiScopeSequence,
    emitters: workspace.emitters,
    receivers: workspace.receivers,
    rayTraceConfig: workspace.rayTraceConfig,
    rayPathDisplayFilters: workspace.rayPathDisplayFilters,
  })
}

export function createBitsamProject(
  scene: ScenePayload,
  workspace: WorkspaceSnapshot,
  savedAt = new Date(),
): BitsamProject {
  if (!workspace.activeCad) {
    throw new BitsamProjectError(
      'CAD 모델을 먼저 불러온 뒤 저장해 주세요.',
    )
  }

  const displayName = workspace.activeCad.displayName
  return {
    format: bitsamFormat,
    schema_version: bitsamSchemaVersion,
    application_version: applicationVersion,
    saved_at: savedAt.toISOString(),
    project_name:
      projectNameFromFileName(displayName) || 'leakage-simulation',
    cad: {
      display_name: displayName,
      file_extension: extensionFromFileName(displayName),
      fingerprint: createSceneFingerprint(scene),
    },
    workspace: createWorkspaceProjectState(workspace),
  }
}

export function serializeBitsamProject(
  project: BitsamProject,
): string {
  return `${JSON.stringify(project, null, 2)}\n`
}

export function parseBitsamProject(source: string): BitsamProject {
  let parsed: unknown
  try {
    parsed = JSON.parse(source)
  } catch {
    throw new BitsamProjectError(
      '파일 내용이 올바른 JSON 형식이 아닙니다.',
    )
  }

  if (!isRecord(parsed)) {
    throw new BitsamProjectError(
      'BITSAM 프로젝트 루트 데이터가 올바르지 않습니다.',
    )
  }
  if (parsed.format !== bitsamFormat) {
    throw new BitsamProjectError(
      'TV Leakage Simulator 프로젝트 파일이 아닙니다.',
    )
  }
  if (parsed.schema_version !== bitsamSchemaVersion) {
    throw new BitsamProjectError(
      `지원하지 않는 BITSAM 버전입니다: ${String(parsed.schema_version)}`,
    )
  }
  if (
    !isString(parsed.application_version) ||
    !isString(parsed.saved_at) ||
    Number.isNaN(Date.parse(parsed.saved_at)) ||
    !isString(parsed.project_name) ||
    !isBitsamCadReference(parsed.cad) ||
    !isWorkspaceProjectState(parsed.workspace)
  ) {
    throw new BitsamProjectError(
      'BITSAM 프로젝트의 필수 데이터가 없거나 손상되었습니다.',
    )
  }

  return structuredClone(parsed) as unknown as BitsamProject
}

export async function readBitsamProjectFile(
  file: File,
): Promise<BitsamProject> {
  if (!file.name.toLowerCase().endsWith(bitsamFileExtension)) {
    throw new BitsamProjectError(
      `${bitsamFileExtension} 파일을 선택해 주세요.`,
    )
  }
  return parseBitsamProject(await file.text())
}

export function compareBitsamProjectScene(
  project: BitsamProject,
  scene: ScenePayload,
  activeCad: ActiveCad,
): BitsamCompatibility {
  const current = createSceneFingerprint(scene)
  const expected = project.cad.fingerprint
  const reasons: string[] = []
  const warnings: string[] = []

  if (current.scene_schema_version !== expected.scene_schema_version) {
    reasons.push('3D scene 데이터 버전이 다릅니다.')
  }
  if (current.face_count !== expected.face_count) {
    reasons.push(
      `면 개수가 다릅니다. 저장 ${expected.face_count.toLocaleString()} / 현재 ${current.face_count.toLocaleString()}`,
    )
  }
  if (current.vertex_count !== expected.vertex_count) {
    reasons.push(
      `정점 개수가 다릅니다. 저장 ${expected.vertex_count.toLocaleString()} / 현재 ${current.vertex_count.toLocaleString()}`,
    )
  }
  if (current.component_count !== expected.component_count) {
    reasons.push(
      `부품 개수가 다릅니다. 저장 ${expected.component_count.toLocaleString()} / 현재 ${current.component_count.toLocaleString()}`,
    )
  }
  if (current.geometry_signature !== expected.geometry_signature) {
    reasons.push('부품 ID 또는 형상 경계 정보가 다릅니다.')
  }
  if (activeCad.displayName !== project.cad.display_name) {
    warnings.push(
      `파일명이 다릅니다. 저장 ${project.cad.display_name} / 현재 ${activeCad.displayName}`,
    )
  }

  return {
    compatible: reasons.length === 0,
    reasons,
    warnings,
  }
}

export function bitsamDownloadFileName(project: BitsamProject): string {
  const baseName =
    project.project_name
      .replace(/[<>:"/\\|?*]/g, '_')
      .split('')
      .map((character) =>
        character.charCodeAt(0) < 32 ? '_' : character,
      )
      .join('')
      .replace(/[.\s]+$/g, '')
      .trim() || 'leakage-simulation'
  return `${baseName}${bitsamFileExtension}`
}

export function downloadBitsamProject(project: BitsamProject): void {
  const blob = new Blob([serializeBitsamProject(project)], {
    type: 'application/vnd.bitsam+json',
  })
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = bitsamDownloadFileName(project)
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(objectUrl)
}
