import { useStore } from 'zustand'
import { createStore, type StoreApi } from 'zustand/vanilla'

import type {
  EmitterSpec,
  RayTraceConfigRequest,
  RayTraceResult,
  ReceiverSpec,
} from '@/api'

export interface ActiveCad {
  path: string
  displayName: string
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
  latestResult?: RayTraceResult | null
}

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

/** A user-named Base material + Surface property combo, saved from the
 *  Material editor's current draft so it can be reapplied later via the
 *  "Saved optical profile" picker - alongside the built-in catalog presets.
 *  Session-only for now (not part of `WorkspaceProjectState`/.bitsam). */
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
  point?: Vector3Value
}

export interface RoiScopeInput {
  label?: string
  source: RoiSelectionSource
  view: RoiView
  components: RoiComponentClip[]
  clipBox?: RoiClipBox
  point?: Vector3Value
}

export type RayPathDisplayFilter =
  | 'receiver_direct'
  | 'receiver_reflected'
  | 'direct'
  | 'specular'
  | 'lambertian'
  | 'gaussian'

export type RayPathDisplayFilters = Record<
  RayPathDisplayFilter,
  boolean
>

export const defaultRayPathDisplayFilters: RayPathDisplayFilters = {
  receiver_direct: true,
  receiver_reflected: true,
  direct: false,
  specular: false,
  lambertian: false,
  gaussian: false,
}

export interface WorkspaceSnapshot {
  activeCad: ActiveCad | null
  cadCases: CadCase[]
  activeCadCaseId: string | null
  selectedFaceIds: number[]
  selectedComponentIds: number[]
  hiddenComponentIds: number[]
  excludedComponentIds: number[]
  deletedComponentIds: number[]
  componentNameOverrides: Record<number, string>
  componentColorOverrides: Record<number, string>
  materialAssignments: MaterialAssignment[]
  customOpticalProfiles: SavedOpticalProfile[]
  transformRules: ComponentTransformRule[]
  roiScopes: RoiScope[]
  roiScopeSequence: number
  roiBoxSelectionArmed: boolean
  emitterFaceSelectionArmed: boolean
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
  | 'materialAssignments'
  | 'transformRules'
  | 'roiScopes'
  | 'roiScopeSequence'
  | 'emitters'
  | 'receivers'
  | 'rayTraceConfig'
  | 'rayPathDisplayFilters'
>

export interface WorkspaceActions {
  setActiveCad(cad: ActiveCad | null): void
  addCadCase(cad: ActiveCad): void
  setActiveCadCase(caseId: string): void
  setCadCaseVisible(caseId: string, visible: boolean): void
  removeCadCase(caseId: string): void
  setActiveCadCaseResult(result: RayTraceResult): void
  updateCadCaseMetadata(caseId: string, name: string, note: string): void
  copyActiveSetupToCases(targets: Iterable<CopySetupTarget>): void
  setSelectedFaceIds(faceIds: Iterable<number>): void
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
  deleteComponent(componentId: number, faceIds?: Iterable<number>): void
  upsertMaterialAssignment(assignment: MaterialAssignment): void
  removeMaterialAssignment(assignmentId: string): void
  addCustomOpticalProfile(profile: SavedOpticalProfile): void
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

  const plane = clipBox.plane ?? 'xy'
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
  epsilon_mm: 1e-4,
  k_abs: 0.12,
  k_brdf: 1,
  termination_mode: 'threshold',
  contribution_mode: 'summary',
  intersection_backend: 'auto',
  compute_backend: 'cpu',
  store_ray_paths: true,
  max_stored_paths: 500,
  auto_convergence: false,
  convergence_target_percent: 5,
  max_convergence_multiplier: 8,
  primary_sampling_strategy: 'source',
  receiver_importance_fraction: 0.5,
  bounce_sampling_strategy: 'source',
  bounce_receiver_importance_fraction: 0.5,
}

export const maxReflectionDepth = 20

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
    min_energy: Math.max(0, Number(config.min_energy) || 0),
    epsilon_mm: Math.max(1e-9, Number(config.epsilon_mm) || 1e-4),
    k_abs: Math.max(0, Number(config.k_abs) || 0),
    k_brdf: Math.max(0, Number(config.k_brdf) || 0),
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
      Math.min(1000, Math.trunc(config.max_stored_paths || 0)),
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
    materialAssignments: projectState.materialAssignments
      .map(normalizeMaterialAssignment)
      .filter(
        (assignment) =>
          !deletedComponentSet.has(assignment.componentId),
      ),
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
    materialAssignments: state.materialAssignments,
    transformRules: state.transformRules,
    roiScopes: state.roiScopes,
    roiScopeSequence: state.roiScopeSequence,
    emitters: state.emitters,
    receivers: state.receivers,
    rayTraceConfig: state.rayTraceConfig,
    rayPathDisplayFilters: state.rayPathDisplayFilters,
  })
}

function blankProjectState(): WorkspaceProjectState {
  const blank = createSceneSnapshot()
  return projectStateFromSnapshot({
    activeCad: null,
    cadCases: [],
    activeCadCaseId: null,
    ...blank,
  })
}

function restoredSceneState(projectState: WorkspaceProjectState) {
  return {
    ...normalizeProjectState(projectState),
    selectedFaceIds: [],
    selectedComponentIds: [],
    roiBoxSelectionArmed: false,
    emitterFaceSelectionArmed: false,
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

function removeReceiverFromRayTraceResult(
  result: RayTraceResult | null | undefined,
  receiverId: string,
): RayTraceResult | null {
  if (!result) return null
  const receivers = result.receivers.filter(
    (receiver) => receiver.receiver_id !== receiverId,
  )
  if (receivers.length === 0) return null
  const next = structuredClone(result)
  next.receivers = receivers
  next.receiver_grids = next.receiver_grids.filter(
    (grid) => grid.receiver_id !== receiverId,
  )
  next.stored_paths = next.stored_paths.filter(
    (path) => storedPathReceiverId(path) !== receiverId,
  )
  delete next.metrics[receiverId]
  delete next.contribution_summary.receivers[receiverId]
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

function samePlacementPreview(
  current: EmitterSpec | ReceiverSpec | null,
  next: EmitterSpec | ReceiverSpec | null,
): boolean {
  return (
    current === next ||
    (current !== null &&
      next !== null &&
      JSON.stringify(current) === JSON.stringify(next))
  )
}

function createSceneSnapshot(): Omit<
  WorkspaceSnapshot,
  'activeCad' | 'cadCases' | 'activeCadCaseId'
> {
  return {
    selectedFaceIds: [],
    selectedComponentIds: [],
    hiddenComponentIds: [],
    excludedComponentIds: [],
    deletedComponentIds: [],
    componentNameOverrides: {},
    componentColorOverrides: {},
    materialAssignments: [],
    customOpticalProfiles: [],
    transformRules: [],
    roiScopes: [],
    roiScopeSequence: 0,
    roiBoxSelectionArmed: false,
    emitterFaceSelectionArmed: false,
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
    highlightedRayPathSelection: null,
  }
}

function createWorkspaceSnapshot(): WorkspaceSnapshot {
  return {
    activeCad: null,
    cadCases: [],
    activeCadCaseId: null,
    ...createSceneSnapshot(),
  }
}

export function createWorkspaceStore(): WorkspaceStoreApi {
  return createStore<WorkspaceStore>()((set) => ({
    ...createWorkspaceSnapshot(),
    actions: {
      setActiveCad: (activeCad) => {
        set({
          activeCad,
          ...createSceneSnapshot(),
        })
      },
      addCadCase: (cad) => {
        set((state) => {
          const existing = state.cadCases.find(
            (item) => item.cad.path === cad.path,
          )
          const nextCase = existing ?? {
            caseId: `cad-case-${Date.now()}-${state.cadCases.length + 1}`,
            order: state.cadCases.length + 1,
            cad,
            visible: true,
            workspaceState: blankProjectState(),
            latestResult: null,
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
            cadCases: existing
              ? savedCases.map((item) =>
                  item.caseId === existing.caseId
                    ? { ...item, visible: true }
                    : { ...item, visible: false },
                )
              : [
                  ...savedCases.map((item) => ({ ...item, visible: false })),
                  nextCase,
                ],
            ...restoredSceneState(nextCase.workspaceState ?? blankProjectState()),
          }
        })
      },
      setActiveCadCase: (caseId) => {
        set((state) => {
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
            activeRayTraceJobId: target.latestJobId ?? null,
          }
        })
      },
      setCadCaseVisible: (caseId, visible) => {
        set((state) => {
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
            activeRayTraceJobId: target.latestJobId ?? null,
          }
        })
      },
      removeCadCase: (caseId) => {
        set((state) => {
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
            activeRayTraceJobId: nextCase.latestJobId ?? null,
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
      updateCadCaseMetadata: (caseId, name, note) => {
        set((state) => ({
          cadCases: state.cadCases.map((item) =>
            item.caseId === caseId
              ? { ...item, name: name.trim(), note: note.trim() }
              : item,
          ),
        }))
      },
      setSelectedFaceIds: (faceIds) => {
        set({ selectedFaceIds: normalizeIds(faceIds) })
      },
      toggleSelectedFaceId: (faceId) => {
        set((state) => ({
          selectedFaceIds: toggleId(state.selectedFaceIds, faceId),
        }))
      },
      setSelectedComponentIds: (componentIds) => {
        set({ selectedComponentIds: normalizeIds(componentIds) })
      },
      toggleSelectedComponentId: (componentId) => {
        set((state) => ({
          selectedComponentIds: toggleId(
            state.selectedComponentIds,
            componentId,
          ),
        }))
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
      copyActiveSetupToCases: (targets) => {
        const targetMappings = new Map(
          Array.from(targets, (target) => [
            target.caseId,
            target.componentIdMap,
          ]),
        )
        set((state) => {
          if (!state.activeCadCaseId || targetMappings.size === 0) return state
          const source = projectStateFromSnapshot(state)
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
                      }
                    : structuredClone(emitter),
                ),
                receivers: source.receivers.map((receiver) => ({
                  ...structuredClone(receiver),
                  source_face_indices: [],
                })),
              }
              return {
                ...item,
                workspaceState: copiedState,
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
        set((state) => ({
          materialAssignments: [
            ...state.materialAssignments.filter(
              (item) => item.assignmentId !== normalized.assignmentId,
            ),
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
        set((state) => ({
          customOpticalProfiles: [
            ...state.customOpticalProfiles.filter(
              (item) => item.id !== profile.id,
            ),
            profile,
          ],
        }))
      },
      removeCustomOpticalProfile: (profileId) => {
        set((state) => ({
          customOpticalProfiles: state.customOpticalProfiles.filter(
            (item) => item.id !== profileId,
          ),
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
        set((state) => ({
          emitters: [
            ...state.emitters.filter(
              (item) => item.emitter_id !== emitter.emitter_id,
            ),
            {
              ...emitter,
              face_indices: normalizeIds(emitter.face_indices),
              ray_count: Math.max(1, Math.trunc(emitter.ray_count || 1)),
            },
          ],
          ...invalidateRayTraceState(state),
        }))
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
      setHighlightedRayPathSelection: (selection) => {
        set({
          highlightedRayPathSelection: selection
            ? { ...selection, pathIndices: normalizeIds(selection.pathIndices) }
            : null,
        })
      },
      restoreProjectState: (projectState) => {
        const normalized = normalizeProjectState(projectState)
        set({
          ...normalized,
          selectedFaceIds: [],
          selectedComponentIds: [],
          roiBoxSelectionArmed: false,
          emitterFaceSelectionArmed: false,
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
        })
      },
      clearSceneState: () => {
        // Saved optical profiles aren't tied to any specific CAD's
        // geometry/components (unlike materialAssignments) - keep them
        // across a fresh import instead of wiping them with everything else.
        set((state) => ({
          ...createSceneSnapshot(),
          customOpticalProfiles: state.customOpticalProfiles,
        }))
      },
      resetWorkspace: () => {
        set(createWorkspaceSnapshot())
      },
    },
  }))
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
  selectedFaceIds: (state: WorkspaceStore) => state.selectedFaceIds,
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
  materialAssignments: (state: WorkspaceStore) =>
    state.materialAssignments,
  customOpticalProfiles: (state: WorkspaceStore) =>
    state.customOpticalProfiles ?? [],
  transformRules: (state: WorkspaceStore) => state.transformRules,
  roiScopes: (state: WorkspaceStore) => state.roiScopes,
  roiBoxSelectionArmed: (state: WorkspaceStore) =>
    state.roiBoxSelectionArmed,
  emitterFaceSelectionArmed: (state: WorkspaceStore) =>
    state.emitterFaceSelectionArmed ?? false,
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
  highlightedRayPathSelection: (state: WorkspaceStore) =>
    state.highlightedRayPathSelection ?? null,
  actions: (state: WorkspaceStore) => state.actions,
}
