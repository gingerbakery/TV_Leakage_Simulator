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
  latestResult?: RayTraceResult | null
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
export type RoiProjectionPlane = 'xy' | 'yz' | 'zx'
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
  rayPathDisplayFilters: RayPathDisplayFilters
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
  setActiveCadCaseResult(result: RayTraceResult): void
  updateCadCaseMetadata(caseId: string, name: string, note: string): void
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
  setRayPathDisplayFilter(
    filter: RayPathDisplayFilter,
    visible: boolean,
  ): void
  setRayPathDisplayFilters(
    filters: Partial<RayPathDisplayFilters>,
  ): void
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
    plane === 'xy'
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
  store_ray_paths: true,
  max_stored_paths: 500,
}

export const maxReflectionDepth = 20

function normalizeRayTraceConfig(
  config: RayTraceConfigRequest,
): RayTraceConfigRequest {
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
      config.intersection_backend === 'brute_force' ||
      config.intersection_backend === 'bvh'
        ? config.intersection_backend
        : 'auto',
    store_ray_paths: Boolean(config.store_ray_paths),
    max_stored_paths: Math.max(
      0,
      Math.min(1000, Math.trunc(config.max_stored_paths || 0)),
    ),
  }
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
  }
}

function invalidateRayTraceState() {
  return {
    activeRayTraceJobId: null,
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
    rayPathDisplayFilters: { ...defaultRayPathDisplayFilters },
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
              ? { ...item, workspaceState: projectStateFromSnapshot(state) }
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
              ? { ...item, workspaceState: projectStateFromSnapshot(state) }
              : item,
          )
          return {
            activeCad: target.cad,
            activeCadCaseId: target.caseId,
            cadCases,
            ...restoredSceneState(target.workspaceState ?? blankProjectState()),
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
                ? { workspaceState: projectStateFromSnapshot(state) }
                : {}),
            })),
            ...restoredSceneState(target.workspaceState ?? blankProjectState()),
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
                  latestResult: structuredClone(result),
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
        set({
          excludedComponentIds: normalizeIds(componentIds),
          ...invalidateRayTraceState(),
        })
      },
      setDeletedComponentIds: (componentIds) => {
        set({
          deletedComponentIds: normalizeIds(componentIds),
          ...invalidateRayTraceState(),
        })
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
          ...invalidateRayTraceState(),
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
            ...invalidateRayTraceState(),
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
          ...invalidateRayTraceState(),
        }))
      },
      removeMaterialAssignment: (assignmentId) => {
        set((state) => ({
          materialAssignments: state.materialAssignments.filter(
            (assignment) => assignment.assignmentId !== assignmentId,
          ),
          ...invalidateRayTraceState(),
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
          ...invalidateRayTraceState(),
        }))
      },
      setTransformRuleEnabled: (ruleId, enabled) => {
        set((state) => ({
          transformRules: state.transformRules.map((rule) =>
            rule.ruleId === ruleId ? { ...rule, enabled } : rule,
          ),
          ...invalidateRayTraceState(),
        }))
      },
      removeTransformRule: (ruleId) => {
        set((state) => ({
          transformRules: state.transformRules.filter(
            (rule) => rule.ruleId !== ruleId,
          ),
          ...invalidateRayTraceState(),
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
            ...invalidateRayTraceState(),
          }
        })
      },
      setRoiScopeActive: (scopeId, active) => {
        set((state) => ({
          roiScopes: state.roiScopes.map((scope) =>
            scope.id === scopeId ? { ...scope, active } : scope,
          ),
          ...invalidateRayTraceState(),
        }))
      },
      removeRoiScope: (scopeId) => {
        set((state) => ({
          roiScopes: state.roiScopes.filter(
            (scope) => scope.id !== scopeId,
          ),
          ...invalidateRayTraceState(),
        }))
      },
      clearRoiScopes: () => {
        set({
          roiScopes: [],
          roiBoxSelectionArmed: false,
          roiDraftLabel: '',
          ...invalidateRayTraceState(),
        })
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
        set({ datumFacePickArmed })
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
          ...invalidateRayTraceState(),
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
          ...invalidateRayTraceState(),
        }))
      },
      setEmitterEnabled: (emitterId, enabled) => {
        set((state) => ({
          emitters: state.emitters.map((emitter) =>
            emitter.emitter_id === emitterId
              ? { ...emitter, enabled }
              : emitter,
          ),
          ...invalidateRayTraceState(),
        }))
      },
      removeEmitter: (emitterId) => {
        set((state) => ({
          emitters: state.emitters.filter(
            (emitter) => emitter.emitter_id !== emitterId,
          ),
          ...invalidateRayTraceState(),
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
          ...invalidateRayTraceState(),
        }))
      },
      setReceiverEnabled: (receiverId, enabled) => {
        set((state) => ({
          receivers: state.receivers.map((receiver) =>
            receiver.receiver_id === receiverId
              ? { ...receiver, enabled }
              : receiver,
          ),
          ...invalidateRayTraceState(),
        }))
      },
      removeReceiver: (receiverId) => {
        set((state) => ({
          receivers: state.receivers.filter(
            (receiver) => receiver.receiver_id !== receiverId,
          ),
          ...invalidateRayTraceState(),
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
        set({
          rayTraceConfig: normalizeRayTraceConfig(rayTraceConfig),
          ...invalidateRayTraceState(),
        })
      },
      setActiveRayTraceJobId: (activeRayTraceJobId) => {
        set({ activeRayTraceJobId })
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
  rayPathDisplayFilters: (state: WorkspaceStore) =>
    state.rayPathDisplayFilters ?? defaultRayPathDisplayFilters,
  actions: (state: WorkspaceStore) => state.actions,
}
