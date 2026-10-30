import { useStore } from 'zustand'
import { emptyBsdfAssets, isBsdfAsset, type BsdfAsset } from '@/features/materials/bsdf-asset'
import { createStore, type StoreApi } from 'zustand/vanilla'
import { normalizeFaceDisplayColors, updateFaceDisplayColors, type FaceDisplayColor } from './face-display-color'

import type {
  EmitterSpec,
  RayTraceConfigRequest,
  RayTraceResult,
  ReceiverSpec,
  SceneComponentMatchMetadata,
  OpticalProfile,
} from '@/api'
import type { LeakPreviewProjectState } from '@/features/leak-preview/leak-preview-model'

export interface ActiveCad {
  path: string
  displayName: string
}

export interface AccessoryCad {
  accessoryId: string
  cad: ActiveCad
  visible: boolean
}

export interface CadCase {
  caseId: string
  order: number
  cad: ActiveCad
  visible: boolean
  name?: string
  note?: string
  workspaceState?: WorkspaceProjectState
  latestJobId?: string | null
  /** Transient Batch job tracking; never drives the single-run query/panel. */
  batchJobId?: string | null
  latestResult?: RayTraceResult | null
  componentMatchMetadata?: SceneComponentMatchMetadata[]
  accessoryCads: AccessoryCad[]
  /** Case-owned precision-preview inputs used by Batch and project restore. */
  leakPreviewState?: LeakPreviewProjectState
  setupLinks?: Partial<Record<LinkedSetupSection, string>>
}

export type LinkedSetupSection = 'allowedAreas' | 'blockers' | 'accessoryCads'
const linkedSetupSections: LinkedSetupSection[] = ['allowedAreas', 'blockers', 'accessoryCads']
export type CopySetupSection = 'receivers' | 'emitters' | 'runOptions' | 'allowedAreas' | 'blockers' | 'accessoryCads'

export interface CopySetupTarget {
  caseId: string
  componentIdMap: Record<number, number>
}

export interface Vector3Value {
  x: number
  y: number
  z: number
}

/** A face clicked in the viewer while "pick CAD face" is armed, for either
 * an Emitter or a Receiver's Datum Plane mode - both just want a starting
 * point + normal to seed their own Center/Rotation fields with. */
export interface DatumFacePickResult {
  center: Vector3Value
  normal: Vector3Value
  faceIds: number[]
}

export type MaterialTargetType = 'part' | 'faces'

export interface OpticalValueOverride {
  reflectance: number
  loss: number
  specularRatio: number
  diffuseRatio: number
  scatterModel?: OpticalProfile['scatter_model']
  roughness?: number
  scatterSigmaDeg?: number
}

export interface MaterialAssignment {
  assignmentId: string
  componentId: number
  targetType: MaterialTargetType
  faceIds: number[]
  baseMaterialId: string
  surfaceId: string
  profileId: string
  bsdfAssetId: string
  opticalOverride?: OpticalValueOverride
  enabled: boolean
}

/** A user-named material-library entry saved from the Material editor's
 *  current draft. It keeps the base material, surface finish, manual optical
 *  fallback and optional BSDF link together for later reuse. */
export interface SavedOpticalProfile {
  id: string
  name: string
  baseMaterialId: string
  surfaceId: string
  bsdfAssetId: string
  opticalOverride?: OpticalValueOverride
}

export type TransformTargetType = 'component' | 'faces'
export type TransformSelectionMethod = 'click' | 'box'

export interface ComponentTransformRule {
  ruleId: string
  componentId: number
  targetType: TransformTargetType
  selectionMethod: TransformSelectionMethod
  faceIds: number[]
  move: Vector3Value
  tilt: Vector3Value
  /** Tilt pivot point in absolute model coordinates. Null/undefined = the
   *  target's own bounding-box center (previous, still-default behavior). */
  pivot?: Vector3Value | null
  enabled: boolean
}

export type RoiSelectionSource = 'box' | 'point'
export type RoiProjectionPlane = 'xy' | 'yz' | 'zx' | 'xyz'
export type RoiView =
  | 'front_xy'
  | 'back_neg_xy'
  | 'front_yz'
  | 'back_neg_yz'
  | 'front_zx'
  | 'back_neg_zx'
  | 'coordinate'

export interface RoiClipBox {
  zUnbounded?: boolean
  plane?: RoiProjectionPlane
  xMin: number
  xMax: number
  yMin: number
  yMax: number
  zMin?: number
  zMax?: number
}

export interface RoiComponentClip {
  componentId: number
  componentName: string
  faceIds: number[]
  areaMm2: number
  bboxMin: Vector3Value
  bboxMax: Vector3Value
}

export interface RoiScope {
  id: string
  scopeId: string
  source: RoiSelectionSource
  view: RoiView
  components: RoiComponentClip[]
  active: boolean
  clipBox?: RoiClipBox
  observerView?: { horizontalDeg: number; verticalDeg: number; direction: [number, number, number] }
  point?: Vector3Value
}

export interface RoiScopeInput {
  label?: string
  source: RoiSelectionSource
  view: RoiView
  components: RoiComponentClip[]
  clipBox?: RoiClipBox
  observerView?: { horizontalDeg: number; verticalDeg: number; direction: [number, number, number] }
  point?: Vector3Value
}

export type RayPathDisplayFilter =
  | 'receiver_direct'
  | 'receiver_reflected'
  | 'direct'
  | 'specular'
  | 'lambertian'
  | 'gaussian'
  | 'bsdf'

export type RayPathDisplayFilters = Record<
  Exclude<RayPathDisplayFilter, 'bsdf'>,
  boolean
> & { bsdf?: boolean }

export const defaultRayPathDisplayFilters: RayPathDisplayFilters = {
  receiver_direct: true,
  receiver_reflected: true,
  direct: false,
  specular: false,
  lambertian: false,
  gaussian: false,
  bsdf: false,
}

export const defaultRayPathDisplayLimit = 500

export type ReceiverHeatmap3dMode = 'off' | 'physical' | 'xray'
export type ReceiverHeatmap3dScaleMode = 'auto' | 'compare' | 'customize'
export type ReceiverHeatmap3dInterpolation = 'pixel' | 'smooth'
export type ReceiverHeatmap3dBasis = 'surface' | 'cone'
export type ReceiverHeatmap3dColorMode = 'color' | 'mono'

export interface WorkspaceSnapshot {
  activeCad: ActiveCad | null
  cadCases: CadCase[]
  activeCadCaseId: string | null
  /** Transient owner of the one allowed non-Batch Ray Tracing execution. */
  singleRayTraceRun: {
    caseId: string
    jobId: string | null
  } | null
  batchRayTraceActive: boolean
  selectedFaceIds: number[]
  selectedComponentIds: number[]
  selectionKind: 'component' | 'faces' | 'roi_cap' | null
  hiddenComponentIds: number[]
  excludedComponentIds: number[]
  deletedComponentIds: number[]
  componentNameOverrides: Record<number, string>
  componentColorOverrides: Record<number, string>
  faceColorOverrides: FaceDisplayColor[]
  materialAssignments: MaterialAssignment[]
  customOpticalProfiles: SavedOpticalProfile[]
  bsdfAssets?: BsdfAsset[]
  transformRules: ComponentTransformRule[]
  roiScopes: RoiScope[]
  roiScopeSequence: number
  roiBoxSelectionArmed: boolean
  emitterFaceSelectionArmed: boolean
  emitterSelectionTarget: 'surface' | 'body'
  /** True while the Material editor's "Face 지정" picker is armed - every
   *  viewer click toggles that face into/out of `selectedFaceIds` instead of
   *  replacing the selection, so several faces can be gathered before the
   *  user confirms with "선택 완료". */
  materialFacePickArmed: boolean
  pivotPickArmed: boolean
  pivotPickPoint: Vector3Value | null
  /** Where the Transform editor's tilt pivot currently sits, so the
   *  viewer can mark it while the dialog is open - cleared once the
   *  dialog closes or leaves custom-pivot mode. */
  pivotPreviewPoint: Vector3Value | null
  datumFacePickArmed: boolean
  datumFacePickResult: DatumFacePickResult | null
  roiDraftLabel: string
  emitters: EmitterSpec[]
  receivers: ReceiverSpec[]
  placementPreviewEmitter: EmitterSpec | null
  placementPreviewReceiver: ReceiverSpec | null
  rayTraceConfig: RayTraceConfigRequest
  activeRayTraceJobId: string | null
  restoredRayTraceResult: RayTraceResult | null
  rayPathDisplayFilters: RayPathDisplayFilters
  /** Maximum number of already-stored paths drawn in the 3D Viewer.
   *  This is a display-only preference and never invalidates trace results. */
  rayPathDisplayLimit: number
  receiverHeatmap3dMode: ReceiverHeatmap3dMode
  receiverHeatmap3dOpacity: number
  receiverHeatmap3dScaleMode: ReceiverHeatmap3dScaleMode
  receiverHeatmap3dCustomMinNit: number
  receiverHeatmap3dCustomMaxNit: number
  receiverHeatmap3dInterpolation: ReceiverHeatmap3dInterpolation
  receiverHeatmap3dBasis: ReceiverHeatmap3dBasis
  receiverHeatmap3dColorMode: ReceiverHeatmap3dColorMode
  highlightedRayPathSelection: {
    runId: string
    pathIndices: number[]
    label: string
  } | null
}

export type WorkspaceProjectState = Pick<
  WorkspaceSnapshot,
  | 'hiddenComponentIds'
  | 'excludedComponentIds'
  | 'deletedComponentIds'
  | 'componentNameOverrides'
  | 'componentColorOverrides'
  | 'faceColorOverrides'
  | 'materialAssignments'
  | 'customOpticalProfiles'
  | 'bsdfAssets'
  | 'transformRules'
  | 'roiScopes'
  | 'roiScopeSequence'
  | 'emitters'
  | 'receivers'
  | 'rayTraceConfig'
  | 'rayPathDisplayFilters'
>

export interface CadCaseRayTraceSnapshot {
  caseId: string
  order: number
  name: string
  note: string
  cad: ActiveCad
  accessoryCads: AccessoryCad[]
  leakPreviewState?: LeakPreviewProjectState
  workspaceState: WorkspaceProjectState
  setupHash: string
}

export type CadCaseResultStoreOutcome = 'saved' | 'stale' | 'missing'
export type CadCaseSetupValidationOutcome = 'current' | 'stale' | 'missing'

export interface WorkspaceActions {
  replaceWorkspaceSnapshot(snapshot: WorkspaceSnapshot): void
  setActiveCad(cad: ActiveCad | null): void
  addCadCase(cad: ActiveCad): void
  addAccessoryCad(caseId: string, cad: ActiveCad): void
  restoreCadCaseAccessories(caseId: string, accessories: AccessoryCad[]): void
  setAccessoryCadVisible(caseId: string, accessoryId: string, visible: boolean): void
  removeAccessoryCad(caseId: string, accessoryId: string): void
  setActiveCadCase(caseId: string): void
  setCadCaseVisible(caseId: string, visible: boolean): void
  removeCadCase(caseId: string): void
  setCadCaseComponentMatchMetadata(
    caseId: string,
    metadata: SceneComponentMatchMetadata[],
  ): void
  setCadCaseLeakPreviewState(
    caseId: string,
    state: LeakPreviewProjectState | undefined,
  ): void
  setActiveCadCaseResult(result: RayTraceResult): void
  setSingleRayTraceRun(
    run: { caseId: string; jobId: string | null } | null,
  ): void
  clearSingleRayTraceRun(expectedJobId: string): void
  setBatchRayTraceActive(active: boolean): void
  setCadCaseRayTraceJob(caseId: string, jobId: string | null): void
  validateCadCaseRayTraceSetup(
    caseId: string,
    expectedSetupHash: string,
  ): CadCaseSetupValidationOutcome
  setCadCaseRayTraceResult(
    caseId: string,
    result: RayTraceResult,
    expectedSetupHash: string,
  ): CadCaseResultStoreOutcome
  removeCadCaseReceiverResult(caseId: string, receiverId: string): void
  updateCadCaseMetadata(caseId: string, name: string, note: string): void
  copyActiveSetupToCases(targets: Iterable<CopySetupTarget>, sections?: CopySetupSection[]): void
  unlinkCaseSetup(caseId: string, section: LinkedSetupSection): void
  setSelectedFaceIds(faceIds: Iterable<number>): void
  setFaceSelection(
    faceIds: Iterable<number>,
    componentIds: Iterable<number>,
  ): void
  setRoiCapSelection(): void
  toggleSelectedFaceId(faceId: number): void
  setSelectedComponentIds(componentIds: Iterable<number>): void
  toggleSelectedComponentId(componentId: number): void
  setHiddenComponentIds(componentIds: Iterable<number>): void
  setExcludedComponentIds(componentIds: Iterable<number>): void
  setDeletedComponentIds(componentIds: Iterable<number>): void
  toggleComponentVisibility(componentId: number): void
  toggleComponentTraceability(componentId: number): void
  renameComponent(componentId: number, name: string): void
  setComponentColor(componentId: number, color: string | null): void
  setFaceColor(componentId: number, faceIds: Iterable<number>, color: string | null): void
  deleteComponent(componentId: number, faceIds?: Iterable<number>): void
  upsertMaterialAssignment(assignment: MaterialAssignment): void
  removeMaterialAssignment(assignmentId: string): void
  addCustomOpticalProfile(profile: SavedOpticalProfile): void
  addBsdfAsset(asset: BsdfAsset): void
  removeCustomOpticalProfile(profileId: string): void
  upsertTransformRule(rule: ComponentTransformRule): void
  setTransformRuleEnabled(ruleId: string, enabled: boolean): void
  removeTransformRule(ruleId: string): void
  addRoiScope(scope: RoiScopeInput): void
  setRoiScopes(scopes: RoiScope[]): void
  setRoiScopeActive(scopeId: string, active: boolean): void
  removeRoiScope(scopeId: string): void
  clearRoiScopes(): void
  setRoiBoxSelectionArmed(armed: boolean): void
  setEmitterFaceSelectionArmed(armed: boolean): void
  setEmitterSelectionTarget(target: 'surface' | 'body'): void
  setMaterialFacePickArmed(armed: boolean): void
  setPivotPickArmed(armed: boolean): void
  setPivotPickPoint(point: Vector3Value | null): void
  setPivotPreviewPoint(point: Vector3Value | null): void
  setDatumFacePickArmed(armed: boolean): void
  setDatumFacePickResult(result: DatumFacePickResult | null): void
  setRoiDraftLabel(label: string): void
  upsertEmitter(emitter: EmitterSpec): void
  setEmitterRayCount(rayCount: number): void
  setEmitterEnabled(emitterId: string, enabled: boolean): void
  setEmitterVisible(emitterId: string, visible: boolean): void
  removeEmitter(emitterId: string): void
  upsertReceiver(receiver: ReceiverSpec): void
  setReceiverEnabled(receiverId: string, enabled: boolean): void
  removeReceiver(receiverId: string): void
  setPlacementPreviewEmitter(emitter: EmitterSpec | null): void
  setPlacementPreviewReceiver(receiver: ReceiverSpec | null): void
  setRayTraceConfig(config: RayTraceConfigRequest): void
  setActiveRayTraceJobId(jobId: string | null): void
  setRestoredRayTraceResult(result: RayTraceResult | null): void
  setRayPathDisplayFilter(
    filter: RayPathDisplayFilter,
    visible: boolean,
  ): void
  setRayPathDisplayFilters(
    filters: Partial<RayPathDisplayFilters>,
  ): void
  setRayPathDisplayLimit(limit: number): void
  setReceiverHeatmap3dMode(mode: ReceiverHeatmap3dMode): void
  setReceiverHeatmap3dOpacity(opacity: number): void
  setReceiverHeatmap3dScaleMode(mode: ReceiverHeatmap3dScaleMode): void
  setReceiverHeatmap3dCustomScale(minNit: number, maxNit: number): void
  setReceiverHeatmap3dInterpolation(
    interpolation: ReceiverHeatmap3dInterpolation,
  ): void
  setReceiverHeatmap3dBasis(basis: ReceiverHeatmap3dBasis): void
  setReceiverHeatmap3dColorMode(colorMode: ReceiverHeatmap3dColorMode): void
  setHighlightedRayPathSelection(selection: {
    runId: string
    pathIndices: number[]
    label: string
  } | null): void
  restoreProjectState(projectState: WorkspaceProjectState): void
  clearSceneState(): void
  resetWorkspace(): void
}

export interface WorkspaceStore extends WorkspaceSnapshot {
  actions: WorkspaceActions
}

export type WorkspaceStoreApi = StoreApi<WorkspaceStore>

function normalizeIds(ids: Iterable<number>): number[] {
  return [...new Set(ids)]
    .filter((id) => Number.isSafeInteger(id) && id >= 0)
    .sort((left, right) => left - right)
}

function normalizeEmitter(emitter: EmitterSpec): EmitterSpec {
  return {
    ...emitter,
    face_indices: normalizeIds(emitter.face_indices),
    ...(emitter.source_component_ids
      ? { source_component_ids: normalizeIds(emitter.source_component_ids) }
      : {}),
    ...(emitter.source_face_indices
      ? { source_face_indices: normalizeIds(emitter.source_face_indices) }
      : {}),
    ray_count: Math.max(1, Math.trunc(emitter.ray_count || 1)),
  }
}

function toggleId(ids: number[], id: number): number[] {
  if (!Number.isSafeInteger(id) || id < 0) {
    return ids
  }

  if (ids.includes(id)) {
    return ids.filter((item) => item !== id)
  }

  return normalizeIds([...ids, id])
}

function normalizeVector(vector: Vector3Value): Vector3Value {
  const normalizeValue = (value: number) =>
    Number.isFinite(value) ? value : 0

  return {
    x: normalizeValue(vector.x),
    y: normalizeValue(vector.y),
    z: normalizeValue(vector.z),
  }
}

function normalizeMaterialAssignment(
  assignment: MaterialAssignment,
): MaterialAssignment {
  return {
    ...assignment,
    faceIds: normalizeIds(assignment.faceIds),
  }
}

function normalizeTransformRule(
  rule: ComponentTransformRule,
): ComponentTransformRule {
  return {
    ...rule,
    faceIds: normalizeIds(rule.faceIds),
    move: normalizeVector(rule.move),
    tilt: normalizeVector(rule.tilt),
    pivot: rule.pivot ? normalizeVector(rule.pivot) : rule.pivot,
  }
}

function normalizeRoiComponentClip(
  component: RoiComponentClip,
): RoiComponentClip {
  return {
    ...component,
    faceIds: normalizeIds(component.faceIds),
    areaMm2: Number.isFinite(component.areaMm2)
      ? Math.max(component.areaMm2, 0)
      : 0,
    bboxMin: normalizeVector(component.bboxMin),
    bboxMax: normalizeVector(component.bboxMax),
  }
}

function normalizeRoiClipBox(
  clipBox: RoiClipBox | undefined,
): RoiClipBox | undefined {
  if (!clipBox) return undefined

  const plane = clipBox.zUnbounded ? 'xy' : clipBox.plane ?? 'xy'
  const values =
    plane === 'xyz'
      ? [
          clipBox.xMin,
          clipBox.xMax,
          clipBox.yMin,
          clipBox.yMax,
          clipBox.zMin,
          clipBox.zMax,
        ]
      : plane === 'xy'
      ? [clipBox.xMin, clipBox.xMax, clipBox.yMin, clipBox.yMax]
      : plane === 'yz'
        ? [clipBox.yMin, clipBox.yMax, clipBox.zMin, clipBox.zMax]
        : [clipBox.zMin, clipBox.zMax, clipBox.xMin, clipBox.xMax]
  if (values.some((value) => !Number.isFinite(value))) {
    return undefined
  }

  return {
    plane,
    ...(clipBox.zUnbounded ? { zUnbounded: true } : {}),
    xMin: Math.min(clipBox.xMin, clipBox.xMax),
    xMax: Math.max(clipBox.xMin, clipBox.xMax),
    yMin: Math.min(clipBox.yMin, clipBox.yMax),
    yMax: Math.max(clipBox.yMin, clipBox.yMax),
    zMin:
      clipBox.zMin === undefined || clipBox.zMax === undefined
        ? undefined
        : Math.min(clipBox.zMin, clipBox.zMax),
    zMax:
      clipBox.zMin === undefined || clipBox.zMax === undefined
        ? undefined
        : Math.max(clipBox.zMin, clipBox.zMax),
  }
}

export const defaultRayTraceConfig: RayTraceConfigRequest = {
  ray_count: 10_000,
  max_depth: 1,
  seed: 42,
  min_energy: 1e-9,
  min_energy_basis: 'initial_ray_fraction',
  epsilon_mm: 1e-4,
  k_abs: 0.12,
  k_brdf: 1,
  angle_dependent_reflectance: true,
  termination_mode: 'threshold',
  contribution_mode: 'summary',
  intersection_backend: 'auto',
  compute_backend: 'cpu',
  store_ray_paths: true,
  max_stored_paths: 2000,
  auto_convergence: false,
  convergence_target_percent: 5,
  max_convergence_multiplier: 8,
  apply_preview_blockers: false,
  apply_allowed_areas: false,
  primary_sampling_strategy: 'source',
  receiver_importance_fraction: 0.5,
  bounce_sampling_strategy: 'source',
  bounce_receiver_importance_fraction: 0.5,
}

function blankLeakPreviewProjectState(): LeakPreviewProjectState {
  return {
    sourceMode: 'face',
    detectionMode: 'escape_directions',
    sourceEmitterIds: [],
    sourceFaceIds: [],
    sourceComponentIds: [],
    sourceBodyFaceCount: 0,
    quality: 'balanced',
    directions: ['neg_z'],
    visualizationVisible: true,
    ignoreAreasVisible: true,
    blockersVisible: true,
    ignoreAreas: [],
    blockers: [],
  }
}

export const maxReflectionDepth = 1000

function normalizeRayTraceConfig(
  config: RayTraceConfigRequest,
): RayTraceConfigRequest {
  const computeBackend =
    config.compute_backend === 'gpu_cuda' ? 'gpu_cuda' : 'cpu'
  const requestedIntersectionBackend =
    config.intersection_backend === 'brute_force' ||
    config.intersection_backend === 'bvh'
      ? config.intersection_backend
      : 'auto'
  return {
    ray_count: Math.max(1, Math.trunc(config.ray_count || 1)),
    max_depth: Math.max(
      0,
      Math.min(maxReflectionDepth, Math.trunc(config.max_depth || 0)),
    ),
    seed: Math.trunc(config.seed || 0),
    min_energy: Math.min(
      config.min_energy_basis === 'initial_ray_fraction' ? 1 : Infinity,
      Math.max(0, Number(config.min_energy) || 0),
    ),
    min_energy_basis: config.min_energy_basis === 'initial_ray_fraction'
      ? 'initial_ray_fraction' : 'absolute_lumen',
    epsilon_mm: Math.max(1e-9, Number(config.epsilon_mm) || 1e-4),
    k_abs: Math.max(0, Number(config.k_abs) || 0),
    k_brdf: Math.max(0, Number(config.k_brdf) || 0),
    angle_dependent_reflectance:
      config.angle_dependent_reflectance !== false,
    termination_mode:
      config.termination_mode === 'russian_roulette'
        ? 'russian_roulette'
        : 'threshold',
    contribution_mode:
      config.contribution_mode === 'detailed' ? 'detailed' : 'summary',
    intersection_backend:
      computeBackend === 'gpu_cuda' &&
      requestedIntersectionBackend === 'brute_force'
        ? 'bvh'
        : requestedIntersectionBackend,
    compute_backend: computeBackend,
    store_ray_paths: Boolean(config.store_ray_paths),
    max_stored_paths: Math.max(
      0,
      Math.min(50_000, Math.trunc(config.max_stored_paths || 0)),
    ),
    auto_convergence: Boolean(config.auto_convergence),
    convergence_target_percent: Math.max(
      0.1,
      Number(config.convergence_target_percent) || 5,
    ),
    max_convergence_multiplier: Math.max(
      1,
      Math.min(64, Math.trunc(config.max_convergence_multiplier || 8)),
    ),
    apply_preview_blockers: Boolean(config.apply_preview_blockers),
    apply_allowed_areas: Boolean(config.apply_allowed_areas),
    primary_sampling_strategy:
      config.primary_sampling_strategy === 'receiver_mis'
        ? 'receiver_mis'
        : 'source',
    receiver_importance_fraction: Math.max(
      0.05,
      Math.min(0.95, Number(config.receiver_importance_fraction) || 0.5),
    ),
    bounce_sampling_strategy:
      config.bounce_sampling_strategy === 'receiver_mis'
        ? 'receiver_mis'
        : 'source',
    bounce_receiver_importance_fraction: Math.max(
      0.05,
      Math.min(
        0.95,
        Number(config.bounce_receiver_importance_fraction) || 0.5,
      ),
    ),
  }
}

function traceBackendConfigChanged(
  previous: RayTraceConfigRequest,
  next: RayTraceConfigRequest,
): boolean {
  const frontendOnlyKeys = new Set<keyof RayTraceConfigRequest>([
    'auto_convergence',
    'convergence_target_percent',
    'max_convergence_multiplier',
  ])
  return (Object.keys(next) as Array<keyof RayTraceConfigRequest>).some(
    (key) => !frontendOnlyKeys.has(key) && previous[key] !== next[key],
  )
}

function normalizeComponentNameOverrides(
  overrides: Record<number, string>,
): Record<number, string> {
  return Object.fromEntries(
    Object.entries(overrides)
      .map(([componentId, name]) => [
        Number(componentId),
        name.trim(),
      ] as const)
      .filter(
        ([componentId, name]) =>
          Number.isSafeInteger(componentId) &&
          componentId >= 0 &&
          name.length > 0,
      ),
  )
}

function normalizeComponentColorOverrides(
  overrides: Record<number, string> | undefined,
): Record<number, string> {
  return Object.fromEntries(
    Object.entries(overrides ?? {})
      .map(([componentId, color]) => [
        Number(componentId),
        color.trim().toLowerCase(),
      ] as const)
      .filter(
        ([componentId, color]) =>
          Number.isSafeInteger(componentId) &&
          componentId >= 0 &&
          /^#[0-9a-f]{6}$/.test(color),
      ),
  )
}

function normalizeRoiScope(scope: RoiScope): RoiScope | null {
  const components = scope.components
    .map(normalizeRoiComponentClip)
    .filter((component) => component.faceIds.length > 0)
  if (components.length === 0) return null

  return {
    ...scope,
    id: scope.id.trim(),
    scopeId: scope.scopeId.trim(),
    components,
    clipBox: normalizeRoiClipBox(scope.clipBox),
    point: scope.point ? normalizeVector(scope.point) : undefined,
  }
}

function normalizeProjectState(
  projectState: WorkspaceProjectState,
): WorkspaceProjectState {
  const deletedComponentIds = normalizeIds(
    projectState.deletedComponentIds,
  )
  const deletedComponentSet = new Set(deletedComponentIds)
  const roiScopes = projectState.roiScopes
    .map(normalizeRoiScope)
    .filter((scope): scope is RoiScope => scope !== null)
    .map((scope) => ({
      ...scope,
      components: scope.components.filter(
        (component) =>
          !deletedComponentSet.has(component.componentId),
      ),
    }))
    .filter((scope) => scope.components.length > 0)

  return {
    hiddenComponentIds: normalizeIds(
      projectState.hiddenComponentIds,
    ).filter((id) => !deletedComponentSet.has(id)),
    excludedComponentIds: normalizeIds(
      projectState.excludedComponentIds,
    ).filter((id) => !deletedComponentSet.has(id)),
    deletedComponentIds,
    componentNameOverrides: normalizeComponentNameOverrides(
      projectState.componentNameOverrides,
    ),
    componentColorOverrides: normalizeComponentColorOverrides(
      projectState.componentColorOverrides,
    ),
    faceColorOverrides: normalizeFaceDisplayColors(projectState.faceColorOverrides)
      .filter((entry) => !deletedComponentSet.has(entry.componentId)),
    materialAssignments: projectState.materialAssignments
      .map(normalizeMaterialAssignment)
      .filter(
        (assignment) =>
          !deletedComponentSet.has(assignment.componentId),
      ),
    customOpticalProfiles: structuredClone(
      projectState.customOpticalProfiles ?? [],
    ),
    bsdfAssets: structuredClone(projectState.bsdfAssets ?? []),
    transformRules: projectState.transformRules
      .map(normalizeTransformRule)
      .filter(
        (rule) => !deletedComponentSet.has(rule.componentId),
      ),
    roiScopes,
    roiScopeSequence: Math.max(
      roiScopes.length,
      Math.trunc(projectState.roiScopeSequence || 0),
      0,
    ),
    emitters: structuredClone(projectState.emitters),
    receivers: structuredClone(projectState.receivers),
    rayTraceConfig: normalizeRayTraceConfig(
      projectState.rayTraceConfig,
    ),
    rayPathDisplayFilters: {
      ...defaultRayPathDisplayFilters,
      ...projectState.rayPathDisplayFilters,
    },
  }
}

function projectStateFromSnapshot(
  state: WorkspaceSnapshot,
): WorkspaceProjectState {
  return structuredClone({
    hiddenComponentIds: state.hiddenComponentIds,
    excludedComponentIds: state.excludedComponentIds,
    deletedComponentIds: state.deletedComponentIds,
    componentNameOverrides: state.componentNameOverrides,
    componentColorOverrides: state.componentColorOverrides,
    faceColorOverrides: state.faceColorOverrides,
    materialAssignments: state.materialAssignments,
    customOpticalProfiles: state.customOpticalProfiles,
    bsdfAssets: state.bsdfAssets ?? [],
    transformRules: state.transformRules,
    roiScopes: state.roiScopes,
    roiScopeSequence: state.roiScopeSequence,
    emitters: state.emitters,
    receivers: state.receivers,
    rayTraceConfig: state.rayTraceConfig,
    rayPathDisplayFilters: state.rayPathDisplayFilters,
  })
}

// Library entries belong to the workspace; assignments remain Case-specific.
function materialLibrary(state: Pick<WorkspaceSnapshot, 'customOpticalProfiles' | 'bsdfAssets'>) {
  return {
    customOpticalProfiles: state.customOpticalProfiles,
    bsdfAssets: state.bsdfAssets ?? [],
  }
}

function sharedMaterialLibrary(
  state: WorkspaceSnapshot,
  library: ReturnType<typeof materialLibrary>,
) {
  const activeChanged = stableJson(referencedBsdfAssets(state)) !== stableJson(
    referencedBsdfAssets({ ...state, ...library }),
  )
  return {
    ...library,
    ...(activeChanged ? invalidateRayTraceState() : {}),
    cadCases: state.cadCases.map((item) => {
      const previous = effectiveCadCaseProjectState(state, item)
      const workspaceState = { ...previous, ...library }
      const changed = stableJson(referencedBsdfAssets(previous)) !== stableJson(
        referencedBsdfAssets(workspaceState),
      )
      return {
        ...item,
        workspaceState,
        ...(changed ? { latestJobId: null, latestResult: null } : {}),
      }
    }),
  }
}

function referencedBsdfAssets(
  state: Pick<WorkspaceProjectState, 'materialAssignments' | 'bsdfAssets'>,
) {
  const assetIds = new Set(state.materialAssignments
    .filter((item) => item.enabled && item.bsdfAssetId)
    .map((item) => item.bsdfAssetId))
  const byId = new Map((state.bsdfAssets ?? []).map((item) => [item.id, item]))
  return [...assetIds].sort().map((id) => byId.get(id) ?? { id, missing: true })
}

export function assertCompatibleProjectLibrary(
  current: Pick<WorkspaceProjectState, 'bsdfAssets'>,
  incoming: Pick<WorkspaceProjectState, 'bsdfAssets'>,
) {
  const byId = new Map((current.bsdfAssets ?? []).map((item) => [item.id, item]))
  for (const asset of incoming.bsdfAssets ?? []) {
    const existing = byId.get(asset.id)
    if (existing && stableJson(existing) !== stableJson(asset)) {
      throw new Error(`BSDF 설정 충돌: ${asset.files[0]?.name ?? asset.id}. 동일 파일의 계산 옵션이 다릅니다. Case별 BSDF 설정을 일치시킨 후 다시 불러오세요.`)
    }
    byId.set(asset.id, asset)
  }
}

function blankProjectState(): WorkspaceProjectState {
  const blank = createSceneSnapshot()
  return projectStateFromSnapshot({
    activeCad: null,
    cadCases: [],
    activeCadCaseId: null,
    singleRayTraceRun: null,
    batchRayTraceActive: false,
    ...blank,
  })
}

function stableJson(value: unknown): string {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value) ?? 'null'
  }
  if (Array.isArray(value)) {
    return `[${value.map(stableJson).join(',')}]`
  }
  return `{${Object.entries(value)
    .filter(([, entry]) => entry !== undefined)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, entry]) => `${JSON.stringify(key)}:${stableJson(entry)}`)
    .join(',')}}`
}

function fnv1a64(value: string): string {
  let hash = 0xcbf29ce484222325n
  const prime = 0x100000001b3n
  for (let index = 0; index < value.length; index += 1) {
    hash ^= BigInt(value.charCodeAt(index))
    hash = BigInt.asUintN(64, hash * prime)
  }
  return hash.toString(16).padStart(16, '0')
}

/** Ordered CAD sources used by the backend composite-scene contract. */
export function cadCaseRayTraceSourcePaths(
  snapshot: Pick<CadCaseRayTraceSnapshot, 'cad' | 'accessoryCads'>,
): string[] {
  return [
    snapshot.cad.path,
    ...snapshot.accessoryCads
      .filter((accessory) => accessory.visible)
      .map((accessory) => accessory.cad.path),
  ]
}

/**
 * Stable identity of inputs that can change a Ray Tracing result. Display-only
 * Case state is intentionally excluded so hiding a component or recoloring the
 * Viewer while a long run is active does not invalidate a valid result.
 */
function effectiveLeakPreviewTraceInputs(
  leakPreviewState: LeakPreviewProjectState | undefined,
  rayTraceConfig: RayTraceConfigRequest,
) {
  if (
    !rayTraceConfig.apply_preview_blockers &&
    !rayTraceConfig.apply_allowed_areas
  ) return null
  if (!leakPreviewState) return { missing: true }
  return {
    blockers: rayTraceConfig.apply_preview_blockers
      ? leakPreviewState.blockers
          .filter((item) => item.enabled)
          .map((item) => ({
            id: item.id,
            baseCenter: item.baseCenter,
            uAxis: item.uAxis,
            vAxis: item.vAxis,
            normal: item.normal,
            widthMm: item.widthMm,
            heightMm: item.heightMm,
            offsetMm: item.offsetMm,
            depthMm: item.depthMm,
            reverse: item.reverse,
          }))
      : [],
    ignoreAreas: rayTraceConfig.apply_allowed_areas
      ? leakPreviewState.ignoreAreas
          .filter((item) => item.enabled)
          .map((item) => ({ id: item.id, regions: item.regions }))
      : [],
  }
}

export function cadCaseRayTraceSetupHash(
  cad: ActiveCad,
  accessoryCads: AccessoryCad[],
  workspaceState: WorkspaceProjectState,
  leakPreviewState?: LeakPreviewProjectState,
): string {
  const normalized = normalizeProjectState(workspaceState)
  const leakPreviewTraceInputs = effectiveLeakPreviewTraceInputs(
    leakPreviewState,
    normalized.rayTraceConfig,
  )
  const canonical = stableJson({
    contract: 'cad-case-raytrace-setup.v1',
    sourcePaths: cadCaseRayTraceSourcePaths({ cad, accessoryCads }),
    excludedComponentIds: normalized.excludedComponentIds,
    deletedComponentIds: normalized.deletedComponentIds,
    materialAssignments: normalized.materialAssignments,
    bsdfAssets: referencedBsdfAssets(normalized),
    transformRules: normalized.transformRules,
    roiScopes: normalized.roiScopes,
    emitters: normalized.emitters.map(({ visible: _visible, ...emitter }) => emitter),
    receivers: normalized.receivers,
    rayTraceConfig: normalized.rayTraceConfig,
    leakPreviewTraceInputs,
  })
  return `cad-case-raytrace-setup.v1:${canonical.length}:${fnv1a64(canonical)}`
}

function effectiveCadCaseProjectState(
  state: WorkspaceSnapshot,
  cadCase: CadCase,
): WorkspaceProjectState {
  return normalizeProjectState(
    cadCase.caseId === state.activeCadCaseId
      ? projectStateFromSnapshot(state)
      : cadCase.workspaceState ?? blankProjectState(),
  )
}

/** Capture immutable, execution-ready Case inputs without changing the active Case. */
export function snapshotCadCasesForRayTrace(
  state: WorkspaceSnapshot,
  caseIds: Iterable<string>,
): CadCaseRayTraceSnapshot[] {
  const byId = new Map(state.cadCases.map((item) => [item.caseId, item]))
  const seen = new Set<string>()
  const snapshots: CadCaseRayTraceSnapshot[] = []
  for (const caseId of caseIds) {
    if (seen.has(caseId)) continue
    seen.add(caseId)
    const cadCase = byId.get(caseId)
    if (!cadCase) continue
    const workspaceState = effectiveCadCaseProjectState(state, cadCase)
    const accessoryCads = structuredClone(cadCase.accessoryCads ?? [])
    const leakPreviewState = cadCase.leakPreviewState
      ? structuredClone(cadCase.leakPreviewState)
      : undefined
    const cad = structuredClone(cadCase.cad)
    snapshots.push({
      caseId: cadCase.caseId,
      order: cadCase.order,
      name: cadCase.name || `CASE ${String(cadCase.order).padStart(2, '0')}`,
      note: cadCase.note ?? '',
      cad,
      accessoryCads,
      leakPreviewState,
      workspaceState: structuredClone(workspaceState),
      setupHash: cadCaseRayTraceSetupHash(
        cad,
        accessoryCads,
        workspaceState,
        leakPreviewState,
      ),
    })
  }
  return snapshots
}

function restoredSceneState(projectState: WorkspaceProjectState) {
  return {
    ...normalizeProjectState(projectState),
    selectedFaceIds: [],
    selectedComponentIds: [],
    selectionKind: null,
    roiBoxSelectionArmed: false,
    emitterFaceSelectionArmed: false,
    emitterSelectionTarget: 'surface' as const,
    materialFacePickArmed: false,
    pivotPickArmed: false,
    pivotPickPoint: null,
    pivotPreviewPoint: null,
    datumFacePickArmed: false,
    datumFacePickResult: null,
    roiDraftLabel: '',
    placementPreviewEmitter: null,
    placementPreviewReceiver: null,
    activeRayTraceJobId: null,
    restoredRayTraceResult: null,
  }
}

function invalidateRayTraceState(state?: WorkspaceSnapshot) {
  return {
    activeRayTraceJobId: null,
    restoredRayTraceResult: null,
    highlightedRayPathSelection: null,
    ...(state
      ? {
          cadCases: state.cadCases.map((item) =>
            item.caseId === state.activeCadCaseId
              ? { ...item, latestJobId: null, latestResult: null }
              : item,
          ),
        }
      : {}),
  }
}

function storedPathReceiverId(
  path: RayTraceResult['stored_paths'][number],
): string | null {
  for (let index = path.length - 1; index >= 0; index -= 1) {
    const receiverId = path[index]?.receiver_id
    if (receiverId) return receiverId
  }
  return null
}

/**
 * Combines independently calculated Receiver results without treating a
 * disabled Receiver as deleted. The newest run replaces only the Receivers
 * included in that run; unchanged Receiver cards, heatmaps and stored paths
 * remain available in the Case report.
 */
export function mergeRayTraceReceiverResults(
  previous: RayTraceResult | null | undefined,
  current: RayTraceResult,
  workspaceReceivers: ReceiverSpec[],
): RayTraceResult {
  if (!previous) return structuredClone(current)

  const validIds = new Set(
    workspaceReceivers.map((receiver) => receiver.receiver_id),
  )
  const updatedIds = new Set(
    current.receivers.map((receiver) => receiver.receiver_id),
  )
  const retainedIds = new Set(
    previous.receivers
      .map((receiver) => receiver.receiver_id)
      .filter((receiverId) => validIds.has(receiverId) && !updatedIds.has(receiverId)),
  )
  if (retainedIds.size === 0) return structuredClone(current)

  const previousReceivers = new Map(
    previous.receivers.map((receiver) => [receiver.receiver_id, receiver]),
  )
  const currentReceivers = new Map(
    current.receivers.map((receiver) => [receiver.receiver_id, receiver]),
  )
  const receivers = workspaceReceivers.flatMap((workspaceReceiver) => {
    const receiver =
      currentReceivers.get(workspaceReceiver.receiver_id) ??
      previousReceivers.get(workspaceReceiver.receiver_id)
    return receiver ? [structuredClone(receiver)] : []
  })
  const receiverGrids = [
    ...previous.receiver_grids.filter((grid) => retainedIds.has(grid.receiver_id)),
    ...current.receiver_grids,
  ].map((grid) => structuredClone(grid))
  const metrics = structuredClone(current.metrics)
  for (const receiverId of retainedIds) {
    if (receiverId in previous.metrics) {
      metrics[receiverId] = structuredClone(previous.metrics[receiverId])
    }
  }
  const contributionSummary = structuredClone(current.contribution_summary)
  for (const receiverId of retainedIds) {
    const retainedContribution = previous.contribution_summary.receivers[receiverId]
    if (retainedContribution) {
      contributionSummary.receivers[receiverId] = structuredClone(retainedContribution)
    }
  }
  const retainedPaths = previous.stored_paths.filter((path) => {
    const receiverId = storedPathReceiverId(path)
    return receiverId !== null && retainedIds.has(receiverId)
  })

  return {
    ...structuredClone(current),
    receivers,
    receiver_grids: receiverGrids,
    contribution_summary: contributionSummary,
    stored_paths: [
      ...retainedPaths.map((path) => structuredClone(path)),
      ...current.stored_paths.map((path) => structuredClone(path)),
    ],
    metrics,
  }
}

export function removeReceiverFromRayTraceResult(
  result: RayTraceResult | null | undefined,
  receiverId: string,
): RayTraceResult | null {
  if (!result) return null
  const receivers = result.receivers.filter(
    (receiver) => receiver.receiver_id !== receiverId,
  )
  if (receivers.length === 0) return null
  const next = structuredClone(result)
  const removedGrid = next.receiver_grids.find(
    (grid) => grid.receiver_id === receiverId,
  )
  const removedContribution = next.contribution_summary.receivers[
    receiverId
  ] as Record<string, unknown> | undefined
  const contributionValue = (section: string, field: string): number => {
    const candidate = removedContribution?.[section]
    if (!candidate || typeof candidate !== 'object') return 0
    const value = Number((candidate as Record<string, unknown>)[field])
    return Number.isFinite(value) ? Math.max(0, value) : 0
  }
  const removedDirectHits = contributionValue('direct', 'hit_count')
  const removedDirectFlux = contributionValue('direct', 'flux_lumen')
  const removedReflectedHits = contributionValue('reflected', 'hit_count')
  const removedReflectedFlux = contributionValue('reflected', 'flux_lumen')
  const removedHits = removedDirectHits + removedReflectedHits ||
    Math.max(0, removedGrid?.hit_count ?? 0)
  next.receivers = receivers
  next.receiver_grids = next.receiver_grids.filter(
    (grid) => grid.receiver_id !== receiverId,
  )
  next.stored_paths = next.stored_paths.filter(
    (path) => storedPathReceiverId(path) !== receiverId,
  )
  delete next.metrics[receiverId]
  delete next.contribution_summary.receivers[receiverId]
  next.receiver_hit_count = Math.max(0, next.receiver_hit_count - removedHits)
  next.contribution_summary.direct_receiver_hit_count = Math.max(
    0,
    next.contribution_summary.direct_receiver_hit_count - removedDirectHits,
  )
  next.contribution_summary.direct_receiver_flux_lumen = Math.max(
    0,
    next.contribution_summary.direct_receiver_flux_lumen - removedDirectFlux,
  )
  next.contribution_summary.reflected_receiver_hit_count = Math.max(
    0,
    next.contribution_summary.reflected_receiver_hit_count - removedReflectedHits,
  )
  next.contribution_summary.reflected_receiver_flux_lumen = Math.max(
    0,
    next.contribution_summary.reflected_receiver_flux_lumen - removedReflectedFlux,
  )
  return next
}

function invalidateReceiverRayTraceState(
  state: WorkspaceSnapshot,
  receiverId: string,
) {
  return {
    activeRayTraceJobId: null,
    restoredRayTraceResult: null,
    highlightedRayPathSelection: null,
    cadCases: state.cadCases.map((item) =>
      item.caseId === state.activeCadCaseId
        ? {
            ...item,
            latestJobId: null,
            latestResult: removeReceiverFromRayTraceResult(
              item.latestResult,
              receiverId,
            ),
          }
        : item,
    ),
  }
}

function canonicalSerializable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalSerializable)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, entry]) => entry !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, entry]) => [key, canonicalSerializable(entry)]),
    )
  }
  return value
}

function sameSerializableValue(current: unknown, next: unknown): boolean {
  return current === next || JSON.stringify(canonicalSerializable(current)) ===
    JSON.stringify(canonicalSerializable(next))
}

function samePlacementPreview(
  current: EmitterSpec | ReceiverSpec | null,
  next: EmitterSpec | ReceiverSpec | null,
): boolean {
  return sameSerializableValue(current, next)
}

function createSceneSnapshot(): Omit<
  WorkspaceSnapshot,
  | 'activeCad'
  | 'cadCases'
  | 'activeCadCaseId'
  | 'singleRayTraceRun'
  | 'batchRayTraceActive'
> {
  return {
    selectedFaceIds: [],
    selectedComponentIds: [],
    selectionKind: null,
    hiddenComponentIds: [],
    excludedComponentIds: [],
    deletedComponentIds: [],
    componentNameOverrides: {},
    componentColorOverrides: {},
    faceColorOverrides: [],
    materialAssignments: [],
    customOpticalProfiles: [],
    bsdfAssets: [],
    transformRules: [],
    roiScopes: [],
    roiScopeSequence: 0,
    roiBoxSelectionArmed: false,
    emitterFaceSelectionArmed: false,
    emitterSelectionTarget: 'surface' as const,
    materialFacePickArmed: false,
    pivotPickArmed: false,
    pivotPickPoint: null,
    pivotPreviewPoint: null,
    datumFacePickArmed: false,
    datumFacePickResult: null,
    roiDraftLabel: '',
    emitters: [],
    receivers: [],
    placementPreviewEmitter: null,
    placementPreviewReceiver: null,
    rayTraceConfig: { ...defaultRayTraceConfig },
    activeRayTraceJobId: null,
    restoredRayTraceResult: null,
    rayPathDisplayFilters: { ...defaultRayPathDisplayFilters },
    rayPathDisplayLimit: defaultRayPathDisplayLimit,
    receiverHeatmap3dMode: 'off',
    receiverHeatmap3dOpacity: 0.82,
    receiverHeatmap3dScaleMode: 'auto',
    receiverHeatmap3dCustomMinNit: 0,
    receiverHeatmap3dCustomMaxNit: 10,
    receiverHeatmap3dInterpolation: 'pixel',
    receiverHeatmap3dBasis: 'surface',
    receiverHeatmap3dColorMode: 'color',
    highlightedRayPathSelection: null,
  }
}

function createWorkspaceSnapshot(): WorkspaceSnapshot {
  return {
    activeCad: null,
    cadCases: [],
    activeCadCaseId: null,
    singleRayTraceRun: null,
    batchRayTraceActive: false,
    ...createSceneSnapshot(),
  }
}

function linkedSetupValue(item: CadCase, section: LinkedSetupSection): unknown {
  if (section === 'accessoryCads') return item.accessoryCads
  const preview = item.leakPreviewState
  return section === 'allowedAreas'
    ? [preview?.ignoreAreas ?? [], preview?.ignoreAreasVisible ?? true]
    : [preview?.blockers ?? [], preview?.blockersVisible ?? true]
}

/** Resolve links in the same transaction so analysis never sees stale inputs. */
function synchronizeCaseSetup(previous: WorkspaceStore, next: WorkspaceStore): WorkspaceStore {
  if (previous.cadCases === next.cadCases) return next
  const cases = new Map(next.cadCases.map((item) => [item.caseId, item]))
  const oldCases = new Map(previous.cadCases.map((item) => [item.caseId, item]))
  const resolved = new Set<string>()
  const visiting = new Set<string>()
  const invalidated = new Set<string>()
  const resolve = (id: string, section: LinkedSetupSection): CadCase => {
    const key = `${id}:${section}`
    if (resolved.has(key)) return cases.get(id)!
    let item = cases.get(id)!
    visiting.add(key)
    const old = oldCases.get(id)
    const sourceId = item.setupLinks?.[section]
    if (sourceId) {
      const locallyEdited = old?.setupLinks?.[section] === sourceId &&
        !sameSerializableValue(linkedSetupValue(old, section), linkedSetupValue(item, section))
      if (!cases.has(sourceId) || visiting.has(`${sourceId}:${section}`) || locallyEdited) {
        const setupLinks = { ...item.setupLinks }
        delete setupLinks[section]
        item = { ...item, setupLinks }
      } else {
        const source = resolve(sourceId, section)
        if (!sameSerializableValue(linkedSetupValue(source, section), linkedSetupValue(item, section))) {
          const before = item
          const workspaceState = effectiveCadCaseProjectState(next, item)
          const preview = structuredClone(item.leakPreviewState ?? blankLeakPreviewProjectState())
          const sourcePreview = source.leakPreviewState ?? blankLeakPreviewProjectState()
          if (section === 'accessoryCads') {
            item = { ...item, accessoryCads: structuredClone(source.accessoryCads) }
            if (!sameSerializableValue(cadCaseRayTraceSourcePaths(before), cadCaseRayTraceSourcePaths(item))) {
              preview.sourceFaceIds = []
              preview.sourceComponentIds = []
              preview.sourceBodyFaceCount = 0
            }
          } else if (section === 'allowedAreas') {
            preview.ignoreAreas = structuredClone(sourcePreview.ignoreAreas)
            preview.ignoreAreasVisible = sourcePreview.ignoreAreasVisible
          } else {
            preview.blockers = structuredClone(sourcePreview.blockers)
            preview.blockersVisible = sourcePreview.blockersVisible
          }
          const traceInputsChanged =
            !sameSerializableValue(cadCaseRayTraceSourcePaths(before), cadCaseRayTraceSourcePaths(item)) ||
            !sameSerializableValue(
              effectiveLeakPreviewTraceInputs(before.leakPreviewState, workspaceState.rayTraceConfig),
              effectiveLeakPreviewTraceInputs(preview, workspaceState.rayTraceConfig),
            )
          if (traceInputsChanged) invalidated.add(id)
          item = { ...item, leakPreviewState: preview,
            ...(traceInputsChanged ? { latestJobId: null, latestResult: null } : {}),
          }
        }
      }
    }
    visiting.delete(key)
    resolved.add(key)
    cases.set(id, item)
    return item
  }
  for (const section of linkedSetupSections) {
    for (const item of next.cadCases) resolve(item.caseId, section)
  }
  const cadCases = next.cadCases.map((item) => cases.get(item.caseId)!)
  return { ...next, cadCases,
    ...(next.activeCadCaseId && invalidated.has(next.activeCadCaseId)
      ? { activeRayTraceJobId: null, restoredRayTraceResult: null } : {}),
  }
}

export function createWorkspaceStore(): WorkspaceStoreApi {
  return createStore<WorkspaceStore>()((rawSet, get) => {
    const set = (update: Partial<WorkspaceStore> | ((state: WorkspaceStore) => Partial<WorkspaceStore>)) => {
      rawSet((state) => {
        const patch = typeof update === 'function' ? update(state) : update
        if (patch === state) return state
        return synchronizeCaseSetup(state, { ...state, ...patch })
      })
    }
    return ({
    ...createWorkspaceSnapshot(),
    actions: {
      replaceWorkspaceSnapshot: (snapshot) => {
        set({
          ...structuredClone(snapshot),
          singleRayTraceRun: null,
          batchRayTraceActive: false,
        })
      },
      setActiveCad: (activeCad) => {
        set((state) => ({
          activeCad,
          ...createSceneSnapshot(),
          ...materialLibrary(state),
        }))
      },
      addCadCase: (cad) => {
        set((state) => {
          if (state.singleRayTraceRun || state.batchRayTraceActive) return state
          // The upload service intentionally reuses one content-addressed
          // CAD path for identical files. Importing that path again must
          // still create an independent analysis Case with its own setup and
          // results instead of merely reactivating the first Case.
          const nextCase = {
            caseId: `cad-case-${Date.now()}-${state.cadCases.length + 1}`,
            order: state.cadCases.length + 1,
            cad,
            visible: true,
            workspaceState: { ...blankProjectState(), ...materialLibrary(state) },
            latestResult: null,
            accessoryCads: [],
            leakPreviewState: blankLeakPreviewProjectState(),
          }
          const savedCases = state.cadCases.map((item) =>
            item.caseId === state.activeCadCaseId
              ? {
                  ...item,
                  workspaceState: projectStateFromSnapshot(state),
                  latestJobId: state.activeRayTraceJobId,
                }
              : item,
          )
          return {
            activeCad: cad,
            activeCadCaseId: nextCase.caseId,
            cadCases: [
              ...savedCases.map((item) => ({ ...item, visible: false })),
              nextCase,
            ],
            ...restoredSceneState(nextCase.workspaceState ?? blankProjectState()),
            ...materialLibrary(state),
          }
        })
      },
      addAccessoryCad: (caseId, cad) => {
        set((state) => state.singleRayTraceRun || state.batchRayTraceActive
          ? state
          : ({
          cadCases: state.cadCases.map((item) => item.caseId === caseId
            ? { ...item, latestJobId: null, latestResult: null, accessoryCads: [
                ...(item.accessoryCads ?? []),
                { accessoryId: `accessory-cad-${Date.now()}-${(item.accessoryCads ?? []).length + 1}`, cad, visible: true },
              ] }
            : item),
          ...(state.activeCadCaseId === caseId ? {
            activeRayTraceJobId: null,
            restoredRayTraceResult: null,
            selectedFaceIds: [],
            selectedComponentIds: [],
          } : {}),
        }))
      },
      restoreCadCaseAccessories: (caseId, accessories) => {
        const restored = structuredClone(accessories)
        set((state) => ({
          cadCases: state.cadCases.map((item) => item.caseId === caseId
            ? {
                ...item,
                latestJobId: null,
                latestResult: null,
                accessoryCads: restored,
              }
            : item),
          ...(state.activeCadCaseId === caseId ? {
            activeRayTraceJobId: null,
            restoredRayTraceResult: null,
            selectedFaceIds: [],
            selectedComponentIds: [],
          } : {}),
        }))
      },
      setAccessoryCadVisible: (caseId, accessoryId, visible) => {
        set((state) => state.singleRayTraceRun || state.batchRayTraceActive
          ? state
          : ({
          cadCases: state.cadCases.map((item) => item.caseId === caseId
            ? { ...item, latestJobId: null, latestResult: null,
                accessoryCads: (item.accessoryCads ?? []).map((accessory) =>
                  accessory.accessoryId === accessoryId ? { ...accessory, visible } : accessory) }
            : item),
          ...(state.activeCadCaseId === caseId ? {
            activeRayTraceJobId: null,
            restoredRayTraceResult: null,
            selectedFaceIds: [],
            selectedComponentIds: [],
          } : {}),
        }))
      },
      removeAccessoryCad: (caseId, accessoryId) => {
        set((state) => state.singleRayTraceRun || state.batchRayTraceActive
          ? state
          : ({
          cadCases: state.cadCases.map((item) => item.caseId === caseId
            ? { ...item, latestJobId: null, latestResult: null,
                accessoryCads: (item.accessoryCads ?? []).filter((accessory) => accessory.accessoryId !== accessoryId) }
            : item),
          ...(state.activeCadCaseId === caseId ? {
            activeRayTraceJobId: null,
            restoredRayTraceResult: null,
            selectedFaceIds: [],
            selectedComponentIds: [],
          } : {}),
        }))
      },
      setActiveCadCase: (caseId) => {
        set((state) => {
          if (state.singleRayTraceRun || state.batchRayTraceActive) return state
          const target = state.cadCases.find(
            (item) => item.caseId === caseId,
          )
          if (!target || target.caseId === state.activeCadCaseId) return state
          const cadCases = state.cadCases.map((item) =>
            item.caseId === state.activeCadCaseId
              ? {
                  ...item,
                  workspaceState: projectStateFromSnapshot(state),
                  latestJobId: state.activeRayTraceJobId,
                }
              : item,
          )
          return {
            activeCad: target.cad,
            activeCadCaseId: target.caseId,
            cadCases,
            ...restoredSceneState(target.workspaceState ?? blankProjectState()),
            ...materialLibrary(state),
            activeRayTraceJobId: target.latestJobId ?? null,
          }
        })
      },
      setCadCaseVisible: (caseId, visible) => {
        set((state) => {
          if (state.singleRayTraceRun || state.batchRayTraceActive) return state
          const target = state.cadCases.find((item) => item.caseId === caseId)
          if (!target) return state
          if (!visible) {
            return {
              cadCases: state.cadCases.map((item) =>
                item.caseId === caseId ? { ...item, visible: false } : item,
              ),
            }
          }
          if (caseId === state.activeCadCaseId) {
            return {
              cadCases: state.cadCases.map((item) => ({
                ...item,
                visible: item.caseId === caseId,
              })),
            }
          }
          return {
            activeCad: target.cad,
            activeCadCaseId: target.caseId,
            cadCases: state.cadCases.map((item) => ({
              ...item,
              visible: item.caseId === caseId,
              ...(item.caseId === state.activeCadCaseId
                ? {
                    workspaceState: projectStateFromSnapshot(state),
                    latestJobId: state.activeRayTraceJobId,
                  }
                : {}),
            })),
            ...restoredSceneState(target.workspaceState ?? blankProjectState()),
            ...materialLibrary(state),
            activeRayTraceJobId: target.latestJobId ?? null,
          }
        })
      },
      removeCadCase: (caseId) => {
        set((state) => {
          if (state.singleRayTraceRun || state.batchRayTraceActive) return state
          const removedIndex = state.cadCases.findIndex(
            (item) => item.caseId === caseId,
          )
          if (removedIndex < 0) return state
          const remaining = state.cadCases.filter(
            (item) => item.caseId !== caseId,
          )
          const reordered = remaining.map((item, index) => ({
            ...item,
            order: index + 1,
          }))
          if (caseId !== state.activeCadCaseId) {
            return { cadCases: reordered }
          }
          const nextCase =
            reordered[Math.min(removedIndex, reordered.length - 1)] ?? null
          if (!nextCase) {
            return {
              activeCad: null,
              activeCadCaseId: null,
              cadCases: [],
              ...createSceneSnapshot(),
              ...materialLibrary(state),
            }
          }
          return {
            activeCad: nextCase.cad,
            activeCadCaseId: nextCase.caseId,
            cadCases: reordered.map((item) => ({
              ...item,
              visible: item.caseId === nextCase.caseId,
            })),
            ...restoredSceneState(
              nextCase.workspaceState ?? blankProjectState(),
            ),
            ...materialLibrary(state),
            activeRayTraceJobId: nextCase.latestJobId ?? null,
          }
        })
      },
      setCadCaseComponentMatchMetadata: (caseId, metadata) => {
        set((state) => ({
          cadCases: state.cadCases.map((item) =>
            item.caseId === caseId
              ? {
                  ...item,
                  componentMatchMetadata: structuredClone(metadata),
                }
              : item,
          ),
        }))
      },
      setCadCaseLeakPreviewState: (caseId, leakPreviewState) => {
        set((state) => {
          const target = state.cadCases.find((item) => item.caseId === caseId)
          if (!target) return state
          const next = leakPreviewState
            ? structuredClone(leakPreviewState)
            : undefined
          if (sameSerializableValue(target.leakPreviewState, next)) return state
          const workspaceState = effectiveCadCaseProjectState(state, target)
          const traceInputsChanged = !sameSerializableValue(
            effectiveLeakPreviewTraceInputs(
              target.leakPreviewState,
              workspaceState.rayTraceConfig,
            ),
            effectiveLeakPreviewTraceInputs(
              next,
              workspaceState.rayTraceConfig,
            ),
          )
          return {
            cadCases: state.cadCases.map((item) =>
              item.caseId === caseId
                ? {
                    ...item,
                    leakPreviewState: next,
                    ...(traceInputsChanged
                      ? { latestJobId: null, latestResult: null }
                      : {}),
                  }
                : item,
            ),
            ...(traceInputsChanged && state.activeCadCaseId === caseId
              ? {
                  activeRayTraceJobId: null,
                  restoredRayTraceResult: null,
                }
              : {}),
          }
        })
      },
      setActiveCadCaseResult: (result) => {
        set((state) => ({
          cadCases: state.cadCases.map((item) =>
            item.caseId === state.activeCadCaseId
              ? {
                  ...item,
                  workspaceState: projectStateFromSnapshot(state),
                  latestResult: mergeRayTraceReceiverResults(
                    item.latestResult,
                    result,
                    state.receivers,
                  ),
                }
              : item,
          ),
        }))
      },
      setSingleRayTraceRun: (singleRayTraceRun) => {
        set({
          singleRayTraceRun: singleRayTraceRun
            ? { ...singleRayTraceRun }
            : null,
        })
      },
      clearSingleRayTraceRun: (expectedJobId) => {
        set((state) =>
          state.singleRayTraceRun?.jobId === expectedJobId
            ? { singleRayTraceRun: null }
            : state,
        )
      },
      setBatchRayTraceActive: (batchRayTraceActive) => {
        set({ batchRayTraceActive })
      },
      setCadCaseRayTraceJob: (caseId, jobId) => {
        set((state) => ({
          cadCases: state.cadCases.map((item) =>
            item.caseId === caseId
              ? { ...item, batchJobId: jobId }
              : item,
          ),
        }))
      },
      validateCadCaseRayTraceSetup: (caseId, expectedSetupHash) => {
        const state = get()
        const target = state.cadCases.find((item) => item.caseId === caseId)
        if (!target) return 'missing'
        const workspaceState = effectiveCadCaseProjectState(state, target)
        return cadCaseRayTraceSetupHash(
          target.cad,
          target.accessoryCads ?? [],
          workspaceState,
          target.leakPreviewState,
        ) === expectedSetupHash
          ? 'current'
          : 'stale'
      },
      setCadCaseRayTraceResult: (caseId, result, expectedSetupHash) => {
        let outcome: CadCaseResultStoreOutcome = 'missing'
        set((state) => {
          const target = state.cadCases.find((item) => item.caseId === caseId)
          if (!target) return state
          const workspaceState = effectiveCadCaseProjectState(state, target)
          const currentHash = cadCaseRayTraceSetupHash(
            target.cad,
            target.accessoryCads ?? [],
            workspaceState,
            target.leakPreviewState,
          )
          if (currentHash !== expectedSetupHash) {
            outcome = 'stale'
            return {
              cadCases: state.cadCases.map((item) =>
                item.caseId === caseId
                  ? { ...item, batchJobId: null }
                  : item,
              ),
            }
          }
          outcome = 'saved'
          return {
            cadCases: state.cadCases.map((item) =>
              item.caseId === caseId
                ? {
                    ...item,
                    workspaceState: structuredClone(workspaceState),
                    latestJobId: null,
                    batchJobId: null,
                    latestResult: mergeRayTraceReceiverResults(
                      item.latestResult,
                      result,
                      workspaceState.receivers,
                    ),
                  }
                : item,
            ),
            ...(state.activeCadCaseId === caseId
              ? {
                  activeRayTraceJobId: null,
                  restoredRayTraceResult: null,
                }
              : {}),
          }
        })
        return outcome
      },
      removeCadCaseReceiverResult: (caseId, receiverId) => {
        set((state) => ({
          cadCases: state.cadCases.map((item) => {
            if (item.caseId !== caseId) return item
            const latestResult = removeReceiverFromRayTraceResult(
              item.latestResult,
              receiverId,
            )
            return {
              ...item,
              latestJobId: latestResult ? item.latestJobId : null,
              latestResult,
            }
          }),
        }))
      },
      updateCadCaseMetadata: (caseId, name, note) => {
        set((state) => ({
          cadCases: state.cadCases.map((item) =>
            item.caseId === caseId
              ? { ...item, name, note }
              : item,
          ),
        }))
      },
      setSelectedFaceIds: (faceIds) => {
        const selectedFaceIds = normalizeIds(faceIds)
        set((state) => ({
          selectedFaceIds,
          selectionKind: selectedFaceIds.length > 0
            ? 'faces'
            : state.selectionKind === 'component'
              ? 'component'
              : null,
        }))
      },
      setFaceSelection: (faceIds, componentIds) => {
        const selectedFaceIds = normalizeIds(faceIds)
        set({
          selectedFaceIds,
          selectedComponentIds: selectedFaceIds.length > 0
            ? normalizeIds(componentIds)
            : [],
          selectionKind: selectedFaceIds.length > 0 ? 'faces' : null,
        })
      },
      setRoiCapSelection: () => {
        set({
          selectedFaceIds: [],
          selectedComponentIds: [],
          selectionKind: 'roi_cap',
        })
      },
      toggleSelectedFaceId: (faceId) => {
        set((state) => {
          const selectedFaceIds = toggleId(state.selectedFaceIds, faceId)
          return {
            selectedFaceIds,
            selectionKind: selectedFaceIds.length > 0 ? 'faces' : null,
          }
        })
      },
      setSelectedComponentIds: (componentIds) => {
        const selectedComponentIds = normalizeIds(componentIds)
        set({
          selectedComponentIds,
          selectedFaceIds: [],
          selectionKind: selectedComponentIds.length > 0 ? 'component' : null,
        })
      },
      toggleSelectedComponentId: (componentId) => {
        set((state) => {
          const selectedComponentIds = toggleId(
            state.selectionKind === 'component' ? state.selectedComponentIds : [],
            componentId,
          )
          return {
            selectedComponentIds,
            selectedFaceIds: [],
            selectionKind: selectedComponentIds.length > 0 ? 'component' : null,
          }
        })
      },
      setHiddenComponentIds: (componentIds) => {
        set({ hiddenComponentIds: normalizeIds(componentIds) })
      },
      setExcludedComponentIds: (componentIds) => {
        set((state) => ({
          excludedComponentIds: normalizeIds(componentIds),
          ...invalidateRayTraceState(state),
        }))
      },
      unlinkCaseSetup: (caseId, section) => {
        set((state) => state.singleRayTraceRun || state.batchRayTraceActive ? state : ({
          cadCases: state.cadCases.map((item) => {
            if (item.caseId !== caseId || !item.setupLinks?.[section]) return item
            const setupLinks = { ...item.setupLinks }
            delete setupLinks[section]
            return { ...item, setupLinks }
          }),
        }))
      },
      copyActiveSetupToCases: (targets, sections) => {
        if (sections?.length === 0) return
        const targetMappings = new Map(
          Array.from(targets, (target) => [
            target.caseId,
            target.componentIdMap,
          ]),
        )
        set((state) => {
          if (state.singleRayTraceRun || state.batchRayTraceActive || !state.activeCadCaseId || targetMappings.size === 0) return state
          const source = projectStateFromSnapshot(state)
          const sourceCase = state.cadCases.find((item) => item.caseId === state.activeCadCaseId)!
          const sourcePreview = sourceCase.leakPreviewState ?? blankLeakPreviewProjectState()
          const includes = (section: CopySetupSection) => !sections || sections.includes(section)
          return {
            cadCases: state.cadCases.map((item) => {
              const componentIdMap = targetMappings.get(item.caseId)
              if (!componentIdMap || item.caseId === state.activeCadCaseId) {
                return item
              }
              const remapId = (componentId: number) =>
                componentIdMap[componentId]
              const remapComponentRecord = <T>(record: Record<number, T>) =>
                Object.fromEntries(
                  Object.entries(record).flatMap(([sourceId, value]) => {
                    const targetId = remapId(Number(sourceId))
                    return targetId === undefined
                      ? []
                      : [[targetId, structuredClone(value)]]
                  }),
                ) as Record<number, T>
              const copiedState: WorkspaceProjectState = {
                ...structuredClone(source),
                hiddenComponentIds: [],
                deletedComponentIds: [],
                excludedComponentIds: source.excludedComponentIds.flatMap(
                  (componentId) => {
                    const targetId = remapId(componentId)
                    return targetId === undefined ? [] : [targetId]
                  },
                ),
                componentNameOverrides: remapComponentRecord(
                  source.componentNameOverrides,
                ),
                componentColorOverrides: remapComponentRecord(
                  source.componentColorOverrides,
                ),
                faceColorOverrides: [],
                materialAssignments: source.materialAssignments.flatMap(
                  (assignment) => {
                    const targetId = remapId(assignment.componentId)
                    return targetId === undefined ||
                      assignment.targetType === 'faces'
                      ? []
                      : [
                          {
                            ...structuredClone(assignment),
                            componentId: targetId,
                            faceIds: [],
                          },
                        ]
                  },
                ),
                transformRules: source.transformRules.flatMap((rule) => {
                  const targetId = remapId(rule.componentId)
                  return targetId === undefined || rule.targetType === 'faces'
                    ? []
                    : [
                        {
                          ...structuredClone(rule),
                          componentId: targetId,
                          faceIds: [],
                        },
                      ]
                }),
                roiScopes: source.roiScopes.map((scope) => ({
                  ...structuredClone(scope),
                  components: [],
                })),
                emitters: source.emitters.map((emitter) =>
                  emitter.emitter_type === 'face'
                    ? {
                        ...structuredClone(emitter),
                        face_indices: [],
                        source_face_indices: [],
                        ...(emitter.source_component_ids ? { source_component_ids: [] } : {}),
                      }
                    : structuredClone(emitter),
                ),
                receivers: source.receivers.map((receiver) => ({
                  ...structuredClone(receiver),
                  source_face_indices: [],
                })),
              }
              const selectedState = sections ? {
                ...structuredClone(item.workspaceState ?? blankProjectState()),
                ...materialLibrary(state),
                ...(sections.includes('receivers') ? { receivers: copiedState.receivers } : {}),
                ...(sections.includes('emitters') ? { emitters: copiedState.emitters } : {}),
                ...(sections.includes('runOptions') ? { rayTraceConfig: copiedState.rayTraceConfig } : {}),
              } : copiedState
              const accessoriesChanged = includes('accessoryCads') &&
                !sameSerializableValue(item.accessoryCads, sourceCase.accessoryCads)
              const targetPreview = structuredClone(item.leakPreviewState ?? blankLeakPreviewProjectState())
              // A changed composite CAD invalidates target triangle/component IDs.
              // Coordinate-based constraints survive; source CAD IDs never transfer.
              if (accessoriesChanged) {
                targetPreview.sourceFaceIds = []
                targetPreview.sourceComponentIds = []
                targetPreview.sourceBodyFaceCount = 0
              }
              if (includes('allowedAreas')) {
                targetPreview.ignoreAreas = structuredClone(sourcePreview.ignoreAreas)
                targetPreview.ignoreAreasVisible = sourcePreview.ignoreAreasVisible
              }
              if (includes('blockers')) {
                targetPreview.blockers = structuredClone(sourcePreview.blockers)
                targetPreview.blockersVisible = sourcePreview.blockersVisible
              }
              return {
                ...item,
                workspaceState: selectedState,
                setupLinks: {
                  ...item.setupLinks,
                  ...Object.fromEntries(linkedSetupSections.filter(includes).map((section) => [
                    section, sourceCase.setupLinks?.[section] === item.caseId
                      ? undefined : sourceCase.setupLinks?.[section] ?? sourceCase.caseId,
                  ])),
                },
                ...(includes('accessoryCads') ? { accessoryCads: structuredClone(sourceCase.accessoryCads) } : {}),
                ...(accessoriesChanged || includes('allowedAreas') || includes('blockers') ? { leakPreviewState: targetPreview } : {}),
                latestJobId: null,
                latestResult: null,
              }
            }),
          }
        })
      },
      setDeletedComponentIds: (componentIds) => {
        set((state) => ({
          deletedComponentIds: normalizeIds(componentIds),
          ...invalidateRayTraceState(state),
        }))
      },
      toggleComponentVisibility: (componentId) => {
        set((state) => ({
          hiddenComponentIds: toggleId(
            state.hiddenComponentIds,
            componentId,
          ),
        }))
      },
      toggleComponentTraceability: (componentId) => {
        set((state) => ({
          excludedComponentIds: toggleId(
            state.excludedComponentIds,
            componentId,
          ),
          ...invalidateRayTraceState(state),
        }))
      },
      renameComponent: (componentId, name) => {
        if (!Number.isSafeInteger(componentId) || componentId < 0) return
        const normalizedName = name.trim()

        set((state) => {
          const componentNameOverrides = {
            ...state.componentNameOverrides,
          }

          if (normalizedName) {
            componentNameOverrides[componentId] = normalizedName
          } else {
            delete componentNameOverrides[componentId]
          }

          return { componentNameOverrides }
        })
      },
      setComponentColor: (componentId, color) => {
        if (!Number.isSafeInteger(componentId) || componentId < 0) return
        set((state) => {
          const componentColorOverrides = {
            ...state.componentColorOverrides,
          }
          const normalizedColor = color?.trim().toLowerCase() ?? ''
          if (/^#[0-9a-f]{6}$/.test(normalizedColor)) {
            componentColorOverrides[componentId] = normalizedColor
          } else {
            delete componentColorOverrides[componentId]
          }
          return { componentColorOverrides }
        })
      },
      setFaceColor: (componentId, faceIds, color) => {
        set((state) => ({ faceColorOverrides: updateFaceDisplayColors(state.faceColorOverrides, componentId, faceIds, color) }))
      },
      deleteComponent: (componentId, faceIds = []) => {
        if (!Number.isSafeInteger(componentId) || componentId < 0) return
        const deletedFaceIds = new Set(normalizeIds(faceIds))

        set((state) => {
          const roiScopes = state.roiScopes
            .map((scope) => ({
              ...scope,
              components: scope.components.filter(
                (component) => component.componentId !== componentId,
              ),
            }))
            .filter((scope) => scope.components.length > 0)

          return {
            selectedFaceIds: state.selectedFaceIds.filter(
              (faceId) => !deletedFaceIds.has(faceId),
            ),
            selectedComponentIds: state.selectedComponentIds.filter(
              (id) => id !== componentId,
            ),
            hiddenComponentIds: state.hiddenComponentIds.filter(
              (id) => id !== componentId,
            ),
            excludedComponentIds: state.excludedComponentIds.filter(
              (id) => id !== componentId,
            ),
            deletedComponentIds: normalizeIds([
              ...state.deletedComponentIds,
              componentId,
            ]),
            componentColorOverrides: Object.fromEntries(
              Object.entries(state.componentColorOverrides).filter(
                ([id]) => Number(id) !== componentId,
              ),
            ),
            faceColorOverrides: state.faceColorOverrides.filter((entry) => entry.componentId !== componentId),
            materialAssignments: state.materialAssignments.filter(
              (assignment) => assignment.componentId !== componentId,
            ),
            transformRules: state.transformRules.filter(
              (rule) => rule.componentId !== componentId,
            ),
            roiScopes,
            ...invalidateRayTraceState(state),
          }
        })
      },
      upsertMaterialAssignment: (assignment) => {
        const normalized = normalizeMaterialAssignment(assignment)
        const claimed = new Set(normalized.faceIds)
        set((state) => ({
          materialAssignments: [
            ...state.materialAssignments.flatMap((item) => {
              if (item.assignmentId === normalized.assignmentId) return []
              if (!normalized.enabled || normalized.targetType !== 'faces' || item.targetType !== 'faces' || item.componentId !== normalized.componentId) return [item]
              const faceIds = item.faceIds.filter((id) => !claimed.has(id))
              return faceIds.length ? [{ ...item, faceIds }] : []
            }),
            normalized,
          ],
          ...invalidateRayTraceState(state),
        }))
      },
      removeMaterialAssignment: (assignmentId) => {
        set((state) => ({
          materialAssignments: state.materialAssignments.filter(
            (assignment) => assignment.assignmentId !== assignmentId,
          ),
          ...invalidateRayTraceState(state),
        }))
      },
      addCustomOpticalProfile: (profile) => {
        set((state) => sharedMaterialLibrary(state, {
          ...materialLibrary(state),
          customOpticalProfiles: [
            ...state.customOpticalProfiles.filter((item) => item.id !== profile.id),
            structuredClone(profile),
          ],
        }))
      },
      removeCustomOpticalProfile: (profileId) => {
        set((state) => sharedMaterialLibrary(state, {
          ...materialLibrary(state),
          customOpticalProfiles: state.customOpticalProfiles.filter((item) => item.id !== profileId),
        }))
      },
      addBsdfAsset: (asset) => {
        if (!isBsdfAsset(asset)) throw new Error('Invalid BSDF asset')
        set((state) => sharedMaterialLibrary(state, {
          ...materialLibrary(state),
          bsdfAssets: [...(state.bsdfAssets ?? []).filter((item) => item.id !== asset.id), structuredClone(asset)],
        }))
      },
      upsertTransformRule: (rule) => {
        const normalized = normalizeTransformRule(rule)
        set((state) => ({
          transformRules: [
            ...state.transformRules.filter(
              (item) => item.ruleId !== normalized.ruleId,
            ),
            normalized,
          ],
          ...invalidateRayTraceState(state),
        }))
      },
      setTransformRuleEnabled: (ruleId, enabled) => {
        set((state) => ({
          transformRules: state.transformRules.map((rule) =>
            rule.ruleId === ruleId ? { ...rule, enabled } : rule,
          ),
          ...invalidateRayTraceState(state),
        }))
      },
      removeTransformRule: (ruleId) => {
        set((state) => ({
          transformRules: state.transformRules.filter(
            (rule) => rule.ruleId !== ruleId,
          ),
          ...invalidateRayTraceState(state),
        }))
      },
      addRoiScope: (scope) => {
        const components = scope.components
          .map(normalizeRoiComponentClip)
          .filter((component) => component.faceIds.length > 0)
        if (components.length === 0) return

        set((state) => {
          const sequence = state.roiScopeSequence + 1
          const label = scope.label?.trim()
          return {
            roiScopeSequence: sequence,
            roiDraftLabel: '',
            roiBoxSelectionArmed: false,
            roiScopes: [
              ...state.roiScopes,
              {
                id: `roi-${sequence}`,
                scopeId: label || `ROI-${sequence}`,
                source: scope.source,
                view: scope.view,
                components,
                active: true,
                clipBox: normalizeRoiClipBox(scope.clipBox),
                observerView: scope.observerView ? structuredClone(scope.observerView) : undefined,
                point: scope.point
                  ? normalizeVector(scope.point)
                  : undefined,
              },
            ],
            ...invalidateRayTraceState(state),
          }
        })
      },
      setRoiScopeActive: (scopeId, active) => {
        set((state) => ({
          roiScopes: state.roiScopes.map((scope) =>
            scope.id === scopeId ? { ...scope, active } : scope,
          ),
          ...invalidateRayTraceState(state),
        }))
      },
      removeRoiScope: (scopeId) => {
        set((state) => ({
          roiScopes: state.roiScopes.filter(
            (scope) => scope.id !== scopeId,
          ),
          ...invalidateRayTraceState(state),
        }))
      },
      clearRoiScopes: () => {
        set((state) => ({
          roiScopes: [],
          roiBoxSelectionArmed: false,
          roiDraftLabel: '',
          ...invalidateRayTraceState(state),
        }))
      },
      setRoiBoxSelectionArmed: (roiBoxSelectionArmed) => {
        set({ roiBoxSelectionArmed })
      },
      setEmitterFaceSelectionArmed: (emitterFaceSelectionArmed) => {
        set({ emitterFaceSelectionArmed })
      },
      setEmitterSelectionTarget: (emitterSelectionTarget) => {
        set({ emitterSelectionTarget })
      },
      setMaterialFacePickArmed: (materialFacePickArmed) => {
        set({ materialFacePickArmed })
      },
      setPivotPickArmed: (pivotPickArmed) => {
        set({ pivotPickArmed })
      },
      setPivotPickPoint: (pivotPickPoint) => {
        set({ pivotPickPoint })
      },
      setPivotPreviewPoint: (pivotPreviewPoint) => {
        set({ pivotPreviewPoint })
      },
      setDatumFacePickArmed: (datumFacePickArmed) => {
        set({
          datumFacePickArmed,
          ...(datumFacePickArmed ? { selectedComponentIds: [] } : {}),
        })
      },
      setRoiScopes: (roiScopes) => {
        set((state) => ({
          roiScopes: structuredClone(roiScopes),
          roiScopeSequence: Math.max(state.roiScopeSequence, roiScopes.length),
          ...invalidateRayTraceState(state),
        }))
      },
      setDatumFacePickResult: (datumFacePickResult) => {
        set({ datumFacePickResult })
      },
      setRoiDraftLabel: (roiDraftLabel) => {
        set({ roiDraftLabel })
      },
      upsertEmitter: (emitter) => {
        set((state) => {
          const normalized = normalizeEmitter(emitter)
          const current = state.emitters.find(
            (item) => item.emitter_id === normalized.emitter_id,
          )
          if (current && sameSerializableValue(current, normalized)) {
            return state
          }
          return {
            emitters: [
              ...state.emitters.filter(
                (item) => item.emitter_id !== normalized.emitter_id,
              ),
              normalized,
            ],
            ...invalidateRayTraceState(state),
          }
        })
      },
      setEmitterRayCount: (rayCount) => {
        const normalizedRayCount = Math.max(
          1,
          Math.trunc(rayCount || 1),
        )
        set((state) => ({
          emitters: state.emitters.map((emitter) => ({
            ...emitter,
            ray_count: normalizedRayCount,
          })),
          ...invalidateRayTraceState(state),
        }))
      },
      setEmitterVisible: (emitterId, visible) => set((state) => ({
        emitters: state.emitters.map((emitter) => emitter.emitter_id === emitterId ? { ...emitter, visible } : emitter),
      })),
      setEmitterEnabled: (emitterId, enabled) => {
        set((state) => ({
          emitters: state.emitters.map((emitter) =>
            emitter.emitter_id === emitterId
              ? { ...emitter, enabled }
              : emitter,
          ),
          ...invalidateRayTraceState(state),
        }))
      },
      removeEmitter: (emitterId) => {
        set((state) => ({
          emitters: state.emitters.filter(
            (emitter) => emitter.emitter_id !== emitterId,
          ),
          ...invalidateRayTraceState(state),
        }))
      },
      upsertReceiver: (receiver) => {
        set((state) => ({
          receivers: [
            ...state.receivers.filter(
              (item) => item.receiver_id !== receiver.receiver_id,
            ),
            receiver,
          ],
          ...invalidateReceiverRayTraceState(state, receiver.receiver_id),
        }))
      },
      setReceiverEnabled: (receiverId, enabled) => {
        set((state) => ({
          receivers: state.receivers.map((receiver) =>
            receiver.receiver_id === receiverId
              ? { ...receiver, enabled }
              : receiver,
          ),
        }))
      },
      removeReceiver: (receiverId) => {
        set((state) => ({
          receivers: state.receivers.filter(
            (receiver) => receiver.receiver_id !== receiverId,
          ),
          ...invalidateReceiverRayTraceState(state, receiverId),
        }))
      },
      setPlacementPreviewEmitter: (placementPreviewEmitter) => {
        set((state) =>
          samePlacementPreview(
            state.placementPreviewEmitter,
            placementPreviewEmitter,
          )
            ? state
            : { placementPreviewEmitter },
        )
      },
      setPlacementPreviewReceiver: (placementPreviewReceiver) => {
        set((state) =>
          samePlacementPreview(
            state.placementPreviewReceiver,
            placementPreviewReceiver,
          )
            ? state
            : { placementPreviewReceiver },
        )
      },
      setRayTraceConfig: (rayTraceConfig) => {
        set((state) => {
          const normalized = normalizeRayTraceConfig(rayTraceConfig)
          return {
            rayTraceConfig: normalized,
            ...(traceBackendConfigChanged(state.rayTraceConfig, normalized)
              ? invalidateRayTraceState(state)
              : {}),
          }
        })
      },
      setActiveRayTraceJobId: (activeRayTraceJobId) => {
        set((state) => ({
          activeRayTraceJobId,
          restoredRayTraceResult: null,
          cadCases: state.cadCases.map((item) =>
            item.caseId === state.activeCadCaseId
              ? { ...item, latestJobId: activeRayTraceJobId }
              : item,
          ),
        }))
      },
      setRestoredRayTraceResult: (restoredRayTraceResult) => {
        set({
          restoredRayTraceResult: restoredRayTraceResult
            ? structuredClone(restoredRayTraceResult)
            : null,
          activeRayTraceJobId: null,
        })
      },
      setRayPathDisplayFilter: (filter, visible) => {
        set((state) => ({
          rayPathDisplayFilters: {
            ...state.rayPathDisplayFilters,
            [filter]: visible,
          },
        }))
      },
      setRayPathDisplayFilters: (filters) => {
        set((state) => ({
          rayPathDisplayFilters: {
            ...state.rayPathDisplayFilters,
            ...filters,
          },
        }))
      },
      setRayPathDisplayLimit: (limit) => {
        set({
          rayPathDisplayLimit: Math.max(
            0,
            Math.min(50_000, Math.trunc(Number.isFinite(limit) ? limit : 0)),
          ),
        })
      },
      setReceiverHeatmap3dMode: (receiverHeatmap3dMode) => {
        set({ receiverHeatmap3dMode })
      },
      setReceiverHeatmap3dOpacity: (opacity) => {
        set({
          receiverHeatmap3dOpacity: Math.max(
            0.05,
            Math.min(1, Number.isFinite(opacity) ? opacity : 0.82),
          ),
        })
      },
      setReceiverHeatmap3dScaleMode: (receiverHeatmap3dScaleMode) => {
        set({ receiverHeatmap3dScaleMode })
      },
      setReceiverHeatmap3dCustomScale: (minNit, maxNit) => {
        const minimum = Math.max(0, Number.isFinite(minNit) ? minNit : 0)
        const maximum = Math.max(
          minimum + 1e-9,
          Number.isFinite(maxNit) ? maxNit : minimum + 1,
        )
        set({
          receiverHeatmap3dCustomMinNit: minimum,
          receiverHeatmap3dCustomMaxNit: maximum,
        })
      },
      setReceiverHeatmap3dInterpolation: (receiverHeatmap3dInterpolation) => {
        set({ receiverHeatmap3dInterpolation })
      },
      setReceiverHeatmap3dBasis: (receiverHeatmap3dBasis) => {
        set({ receiverHeatmap3dBasis })
      },
      setReceiverHeatmap3dColorMode: (receiverHeatmap3dColorMode) => {
        set({ receiverHeatmap3dColorMode })
      },
      setHighlightedRayPathSelection: (selection) => {
        set({
          highlightedRayPathSelection: selection
            ? { ...selection, pathIndices: normalizeIds(selection.pathIndices) }
            : null,
        })
      },
      restoreProjectState: (projectState) => {
        const normalized = normalizeProjectState(projectState)
        assertCompatibleProjectLibrary(get(), normalized)
        set((state) => ({
          ...normalized,
          ...sharedMaterialLibrary(state, {
            customOpticalProfiles: Array.from(new Map([
              ...state.customOpticalProfiles, ...normalized.customOpticalProfiles,
            ].map((item) => [item.id, item])).values()),
            bsdfAssets: Array.from(new Map([
              ...(state.bsdfAssets ?? []), ...(normalized.bsdfAssets ?? []),
            ].map((item) => [item.id, item])).values()),
          }),
          selectedFaceIds: [],
          selectedComponentIds: [],
          selectionKind: null,
          roiBoxSelectionArmed: false,
          emitterFaceSelectionArmed: false,
          emitterSelectionTarget: 'surface' as const,
          materialFacePickArmed: false,
          pivotPickArmed: false,
          pivotPickPoint: null,
          pivotPreviewPoint: null,
          datumFacePickArmed: false,
          datumFacePickResult: null,
          roiDraftLabel: '',
          placementPreviewEmitter: null,
          placementPreviewReceiver: null,
          activeRayTraceJobId: null,
          restoredRayTraceResult: null,
        }))
      },
      clearSceneState: () => {
        // Saved material-library entries aren't tied to any specific CAD's
        // geometry/components (unlike materialAssignments) - keep them
        // across a fresh import instead of wiping them with everything else.
        set((state) => ({
          ...createSceneSnapshot(),
          customOpticalProfiles: state.customOpticalProfiles,
          bsdfAssets: state.bsdfAssets ?? [],
        }))
      },
      resetWorkspace: () => {
        set(createWorkspaceSnapshot())
      },
    },
  })
  })
}

export const workspaceStore = createWorkspaceStore()

export function useWorkspaceStore<T>(
  selector: (state: WorkspaceStore) => T,
): T {
  return useStore(workspaceStore, selector)
}

export const workspaceSelectors = {
  activeCad: (state: WorkspaceStore) => state.activeCad,
  cadCases: (state: WorkspaceStore) => state.cadCases,
  activeCadCaseId: (state: WorkspaceStore) => state.activeCadCaseId,
  singleRayTraceRun: (state: WorkspaceStore) => state.singleRayTraceRun,
  batchRayTraceActive: (state: WorkspaceStore) => state.batchRayTraceActive,
  selectedFaceIds: (state: WorkspaceStore) => state.selectedFaceIds,
  selectionKind: (state: WorkspaceStore) => state.selectionKind,
  selectedComponentIds: (state: WorkspaceStore) =>
    state.selectedComponentIds,
  hiddenComponentIds: (state: WorkspaceStore) => state.hiddenComponentIds,
  excludedComponentIds: (state: WorkspaceStore) =>
    state.excludedComponentIds,
  deletedComponentIds: (state: WorkspaceStore) =>
    state.deletedComponentIds,
  componentNameOverrides: (state: WorkspaceStore) =>
    state.componentNameOverrides,
  componentColorOverrides: (state: WorkspaceStore) =>
    state.componentColorOverrides,
  faceColorOverrides: (state: WorkspaceStore) => state.faceColorOverrides,
  materialAssignments: (state: WorkspaceStore) =>
    state.materialAssignments,
  customOpticalProfiles: (state: WorkspaceStore) =>
    state.customOpticalProfiles ?? [],
  bsdfAssets: (state: WorkspaceStore) => state.bsdfAssets ?? emptyBsdfAssets,
  transformRules: (state: WorkspaceStore) => state.transformRules,
  roiScopes: (state: WorkspaceStore) => state.roiScopes,
  roiBoxSelectionArmed: (state: WorkspaceStore) =>
    state.roiBoxSelectionArmed,
  emitterFaceSelectionArmed: (state: WorkspaceStore) =>
    state.emitterFaceSelectionArmed ?? false,
  emitterSelectionTarget: (state: WorkspaceStore) =>
    state.emitterSelectionTarget ?? 'surface',
  materialFacePickArmed: (state: WorkspaceStore) =>
    state.materialFacePickArmed ?? false,
  pivotPickArmed: (state: WorkspaceStore) =>
    state.pivotPickArmed ?? false,
  pivotPickPoint: (state: WorkspaceStore) =>
    state.pivotPickPoint ?? null,
  pivotPreviewPoint: (state: WorkspaceStore) =>
    state.pivotPreviewPoint ?? null,
  datumFacePickArmed: (state: WorkspaceStore) =>
    state.datumFacePickArmed ?? false,
  datumFacePickResult: (state: WorkspaceStore) =>
    state.datumFacePickResult ?? null,
  roiDraftLabel: (state: WorkspaceStore) => state.roiDraftLabel,
  emitters: (state: WorkspaceStore) => state.emitters ?? [],
  receivers: (state: WorkspaceStore) => state.receivers ?? [],
  placementPreviewEmitter: (state: WorkspaceStore) =>
    state.placementPreviewEmitter ?? null,
  placementPreviewReceiver: (state: WorkspaceStore) =>
    state.placementPreviewReceiver ?? null,
  rayTraceConfig: (state: WorkspaceStore) =>
    state.rayTraceConfig ?? defaultRayTraceConfig,
  activeRayTraceJobId: (state: WorkspaceStore) =>
    state.activeRayTraceJobId,
  restoredRayTraceResult: (state: WorkspaceStore) =>
    state.restoredRayTraceResult ?? null,
  rayPathDisplayFilters: (state: WorkspaceStore) =>
    state.rayPathDisplayFilters ?? defaultRayPathDisplayFilters,
  rayPathDisplayLimit: (state: WorkspaceStore) =>
    state.rayPathDisplayLimit ?? defaultRayPathDisplayLimit,
  receiverHeatmap3dMode: (state: WorkspaceStore) =>
    state.receiverHeatmap3dMode ?? 'off',
  receiverHeatmap3dOpacity: (state: WorkspaceStore) =>
    state.receiverHeatmap3dOpacity ?? 0.82,
  receiverHeatmap3dScaleMode: (state: WorkspaceStore) =>
    state.receiverHeatmap3dScaleMode ?? 'auto',
  receiverHeatmap3dCustomMinNit: (state: WorkspaceStore) =>
    state.receiverHeatmap3dCustomMinNit ?? 0,
  receiverHeatmap3dCustomMaxNit: (state: WorkspaceStore) =>
    state.receiverHeatmap3dCustomMaxNit ?? 10,
  receiverHeatmap3dInterpolation: (state: WorkspaceStore) =>
    state.receiverHeatmap3dInterpolation ?? 'pixel',
  receiverHeatmap3dBasis: (state: WorkspaceStore) =>
    state.receiverHeatmap3dBasis ?? 'surface',
  receiverHeatmap3dColorMode: (state: WorkspaceStore) =>
    state.receiverHeatmap3dColorMode ?? 'color',
  highlightedRayPathSelection: (state: WorkspaceStore) =>
    state.highlightedRayPathSelection ?? null,
  actions: (state: WorkspaceStore) => state.actions,
}
