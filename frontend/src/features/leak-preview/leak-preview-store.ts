import type { ObserverPointSelection } from './observer-point-selection'
import { defaultLeakDisplayFilter, type LeakDisplayFilter } from './leak-display-filter'
import { migrateAllowedAreas } from './blocker-groups'
import { previewSignature } from './leak-preview-signature'
import { useStore } from 'zustand'
import { createStore } from 'zustand/vanilla'

import type { RayTraceResult } from '@/api'
import type {
  LeakPreviewCandidate,
  LeakPreviewHighlight,
  LeakPreviewBlocker,
  LeakPreviewIgnoreArea,
  LeakPreviewPoint,
  LeakPreviewQuality,
  LeakPreviewDirection,
  LeakPreviewProjectState,
  SavedLeakPreviewResult,
} from './leak-preview-model'
import { defaultObserverSettings } from './leak-preview-model'
import { workspaceStore } from '@/stores/workspace-store'

function restoredObserver(observer?: LeakPreviewProjectState['observer']) {
  return { ...defaultObserverSettings, ...observer,
    measurement: observer ? observer.measurement ?? 'paths' : defaultObserverSettings.measurement,
    refine_angles: observer ? observer.refine_angles ?? false : defaultObserverSettings.refine_angles,
    sampling: observer ? observer.sampling ?? 'manual' : 'balanced' as const }
}

export function createDefaultLeakPreviewProjectState(): LeakPreviewProjectState {
  return {
    displayFilter: { ...defaultLeakDisplayFilter },
    sourceMode: 'face',
    sourceEmitterIds: [],
    detectionMode: 'escape_directions',
    observer: { ...defaultObserverSettings },
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

function projectStateFromStore(state: LeakPreviewState): LeakPreviewProjectState {
  return structuredClone({
    displayFilter: state.displayFilter,
    sourceMode: state.sourceMode,
    detectionMode: state.detectionMode,
    observer: state.observer,
    sourceEmitterIds: state.sourceEmitterIds,
    sourceFaceIds: state.sourceFaceIds,
    sourceComponentIds: state.sourceComponentIds,
    sourceBodyFaceCount: state.sourceBodyFaceCount,
    quality: state.quality,
    directions: state.directions,
    visualizationVisible: state.visualizationVisible,
    ignoreAreasVisible: state.ignoreAreasVisible,
    blockersVisible: state.blockersVisible,
    ignoreAreas: state.ignoreAreas,
    blockers: state.blockers,
  })
}

interface LeakPreviewState {
  displayFilter: LeakDisplayFilter
  setDisplayFilter(patch: Partial<LeakDisplayFilter>): void
  cameraViewRequest: { candidate: LeakPreviewCandidate | null; sceneToken: string | null } | null
  requestCameraView(candidate: LeakPreviewCandidate | null): void
  sceneToken: string | null
  sceneIdentity: string | null
  sourceMode: LeakPreviewProjectState['sourceMode']
  detectionMode: 'escape_directions' | 'planar'
  observer: import('./leak-preview-model').LeakPreviewObserverSettings
  sourceEmitterIds: string[]
  sourceFaceIds: number[]
  sourceComponentIds: number[]
  sourceBodyFaceCount: number
  quality: LeakPreviewQuality
  directions: LeakPreviewDirection[]
  jobId: string | null
  result: RayTraceResult | null
  resultRestored: boolean
  points: LeakPreviewPoint[]
  candidates: LeakPreviewCandidate[]
  visualizationVisible: boolean
  ignoreAreasVisible: boolean
  blockersVisible: boolean
  highlight: LeakPreviewHighlight | null
  setHighlight(highlight: LeakPreviewState['highlight']): void
  selectedCandidateId: string | null
  selectedObservation: ObserverPointSelection | null
  selectedGroupCandidateIds: string[]
  selectCandidateGroup(candidateIds: string[]): void
  candidateRevealRequest: { candidateId: string } | null
  ignoreAreaSelectionArmed: boolean
  activeIgnoreAreaId: string | null
  ignoreAreas: LeakPreviewIgnoreArea[]
  blockers: LeakPreviewBlocker[]
  blockerAreaSelectionId: string | null
  selectedBlocker: { groupId: string; regionId: string | null } | null
  selectBlocker(groupId: string | null, regionId?: string | null): void
  runSignature: string | null
  ensureScene(sceneToken: string): void
  rebindSceneToken(sceneToken: string): void
  setSourceEmitters(emitterIds: string[]): void
  setSourceFaceIds(faceIds: number[]): void
  setSourceBody(componentIds: number[], faceCount: number): void
  setSourceMode(mode: LeakPreviewProjectState['sourceMode']): void
  setDetectionMode(mode: 'escape_directions' | 'planar'): void
  setObserver(patch: Partial<LeakPreviewState['observer']>): void
  setQuality(quality: LeakPreviewQuality): void
  toggleDirection(direction: LeakPreviewDirection): void
  setJobId(jobId: string | null): void
  setRunSignature(signature: string | null): void
  setDetection(result: RayTraceResult, points: LeakPreviewPoint[], candidates: LeakPreviewCandidate[]): void
  clearDetection(): void
  removeCandidate(candidateId: string): void
  setVisualizationVisible(visible: boolean): void
  setIgnoreAreasVisible(visible: boolean): void
  setBlockersVisible(visible: boolean): void
  selectCandidate(candidateId: string | null, source?: 'list' | 'viewer', observation?: ObserverPointSelection): void
  beginIgnoreAreaSelection(areaId?: string): void
  finishIgnoreAreaSelection(): void
  addIgnoreAreaRegion(clipBox: LeakPreviewIgnoreArea['regions'][number]): void
  setIgnoreAreaEnabled(areaId: string, enabled: boolean): void
  setIgnoreAreaVisible(areaId: string, visible: boolean): void
  removeIgnoreArea(areaId: string): void
  migrateAllowedAreas(): void
  appendBlockerRegion(blockerId: string, patch: Partial<LeakPreviewBlocker>): void
  updateBlockerRegion(blockerId: string, regionId: string, patch: Partial<LeakPreviewBlocker>): void
  removeBlockerRegion(blockerId: string, regionId: string): void
  addBlocker(blocker: LeakPreviewBlocker): void
  updateBlocker(blockerId: string, patch: Partial<LeakPreviewBlocker>): void
  removeBlocker(blockerId: string): void
  beginBlockerAreaSelection(blockerId: string): void
  finishBlockerAreaSelection(): void
  restoreProjectState(state?: LeakPreviewProjectState, result?: SavedLeakPreviewResult | null): void
  clear(): void
}

const store = createStore<LeakPreviewState>()((set, get) => {
  const activeSceneIdentity = (sceneToken: string) => {
    const workspace = workspaceStore.getState()
    const active = workspace.cadCases.find(
      (item) => item.caseId === workspace.activeCadCaseId,
    )
    if (!active) return `scene-token:${sceneToken}`
    return JSON.stringify([
      active.caseId,
      active.cad.path,
      ...(active.accessoryCads ?? [])
        .filter((item) => item.visible)
        .map((item) => item.cad.path),
    ])
  }
  const persistActiveCase = () => {
    const workspace = workspaceStore.getState()
    if (!workspace.activeCadCaseId) return
    workspace.actions.setCadCaseLeakPreviewState(
      workspace.activeCadCaseId,
      projectStateFromStore(get()),
    )
  }
  const setAndPersist = (
    update:
      | Partial<LeakPreviewState>
      | ((state: LeakPreviewState) => Partial<LeakPreviewState>),
  ) => {
    set(update)
    persistActiveCase()
  }
  const persistDetection = () => {
    const workspace = workspaceStore.getState()
    if (workspace.activeCadCaseId) {
      const active = workspace.cadCases.find((item) => item.caseId === workspace.activeCadCaseId)
      if (!active?.leakPreviewState && get().result) persistActiveCase()
      workspace.actions.setCadCaseLeakPreviewResult(workspace.activeCadCaseId, savedDetection(get()))
    }
  }
  const setDetectionAndPersist = (
    update: Partial<LeakPreviewState> | ((state: LeakPreviewState) => Partial<LeakPreviewState>),
  ) => {
    const visible = get().visualizationVisible
    set(update)
    if (get().visualizationVisible !== visible) persistActiveCase()
    persistDetection()
  }
  return ({
  sceneToken: null,
  sceneIdentity: null,
  sourceMode: 'face',
  detectionMode: 'escape_directions',
  observer: { ...defaultObserverSettings },
  sourceEmitterIds: [],
  sourceFaceIds: [],
  sourceComponentIds: [],
  sourceBodyFaceCount: 0,
  quality: 'balanced',
  directions: ['neg_z'],
  jobId: null,
  result: null,
  resultRestored: false,
  displayFilter: { ...defaultLeakDisplayFilter },
  setDisplayFilter: (patch) => {
    setAndPersist(state => ({
      displayFilter: { ...state.displayFilter, ...patch },
      selectedCandidateId: null, selectedObservation: null, selectedGroupCandidateIds: [], highlight: null,
    }))
    persistDetection()
  },
  cameraViewRequest: null,
  requestCameraView: (candidate) => set({ cameraViewRequest: { candidate: candidate ? structuredClone(candidate) : null, sceneToken: get().sceneToken } }),
  points: [],
  candidates: [],
  visualizationVisible: true,
  ignoreAreasVisible: true,
  blockersVisible: true,
  highlight: null,
  selectedCandidateId: null,
  selectedObservation: null,
  selectedGroupCandidateIds: [],
  candidateRevealRequest: null,
  ignoreAreaSelectionArmed: false,
  activeIgnoreAreaId: null,
  ignoreAreas: [],
  blockers: [],
  blockerAreaSelectionId: null,
  selectedBlocker: null,
  selectBlocker: (groupId, regionId = null) => set((state) => ({ selectedBlocker:
    groupId && state.blockers.some((group) => group.id === groupId &&
      (regionId === null || group.regions?.some((region) => region.id === regionId)))
      ? { groupId, regionId } : null,
  })),
  runSignature: null,
  ensureScene: (sceneToken) => {
    const sceneIdentity = activeSceneIdentity(sceneToken)
    let resetPersistentState = false
    set((state) => {
      if (state.sceneIdentity === sceneIdentity) {
        return state.sceneToken === sceneToken ? state : { sceneToken }
      }
      if (state.sceneIdentity === null) {
        return { sceneToken, sceneIdentity, runSignature: rebindSignature(state.runSignature, sceneToken) }
      }
      resetPersistentState = true
      const workspace = workspaceStore.getState()
      const active = workspace.cadCases.find((item) => item.caseId === workspace.activeCadCaseId)
      const sameCase = active && state.sceneIdentity?.startsWith(JSON.stringify([active.caseId]).slice(0, -1) + ',')
      const preserved = sameCase ? active.leakPreviewState : undefined
      return {
          sceneToken,
          sceneIdentity,
          observer: restoredObserver(preserved?.observer),
          displayFilter: { ...defaultLeakDisplayFilter, ...preserved?.displayFilter },
          detectionMode: 'escape_directions',
          sourceMode: 'face',
          sourceEmitterIds: [],
          sourceFaceIds: [],
          sourceComponentIds: [],
          sourceBodyFaceCount: 0,
          quality: 'balanced',
          directions: ['neg_z'],
          jobId: null,
          result: null,
          resultRestored: false,
          points: [],
          candidates: [],
          visualizationVisible: true,
          ignoreAreasVisible: preserved?.ignoreAreasVisible ?? true,
          blockersVisible: preserved?.blockersVisible ?? true,
          highlight: null,
          selectedCandidateId: null,
          selectedObservation: null,
          selectedGroupCandidateIds: [],
          candidateRevealRequest: null,
          ignoreAreaSelectionArmed: false,
          activeIgnoreAreaId: null,
          ignoreAreas: structuredClone(preserved?.ignoreAreas ?? []),
          blockers: structuredClone(preserved?.blockers ?? []),
          blockerAreaSelectionId: null,
          selectedBlocker: null,
          runSignature: null,
        }
    })
    if (resetPersistentState) {
      persistActiveCase()
      persistDetection()
    }
  },
  rebindSceneToken: (sceneToken) => set({ sceneToken }),
  setSourceEmitters: (emitterIds) => setAndPersist({ sourceMode: 'emitters', sourceEmitterIds: [...new Set(emitterIds)] }),
  setSourceFaceIds: (sourceFaceIds) => setAndPersist({
    sourceMode: 'face',
    sourceEmitterIds: [],
    sourceFaceIds: [...new Set(sourceFaceIds)],
    sourceComponentIds: [],
    sourceBodyFaceCount: 0,
  }),
  setSourceBody: (sourceComponentIds, sourceBodyFaceCount) => setAndPersist({
    sourceMode: 'body',
    sourceEmitterIds: [],
    sourceComponentIds: [...new Set(sourceComponentIds)],
    sourceFaceIds: [],
    sourceBodyFaceCount: Math.max(0, Math.trunc(sourceBodyFaceCount)),
  }),
  setSourceMode: (sourceMode) => setAndPersist({ sourceMode }),
  setDetectionMode: (detectionMode) => setAndPersist({ detectionMode }),
  setObserver: (patch) => setAndPersist((state) => ({ observer: { ...state.observer, ...patch } })),
  setQuality: (quality) => setAndPersist({ quality }),
  toggleDirection: (direction) => setAndPersist({ directions: [direction] }),
  setJobId: (jobId) => set({ jobId }),
  setRunSignature: (runSignature) => set({ runSignature }),
  setDetection: (result, points, candidates) => setDetectionAndPersist({
    result,
    resultRestored: false,
    points,
    candidateRevealRequest: null,
    candidates: [...candidates].sort((a, b) => b.relativeStrength - a.relativeStrength),
    highlight: null,
    visualizationVisible: true,
    selectedCandidateId: null,
    selectedObservation: null,
    selectedGroupCandidateIds: [],
  }),
  removeCandidate: (candidateId) => setDetectionAndPersist((state) => {
    const candidates = state.candidates.filter((candidate) => candidate.id !== candidateId)
    return { candidates, selectedGroupCandidateIds: state.selectedGroupCandidateIds.filter(id => id !== candidateId), candidateRevealRequest: state.candidateRevealRequest?.candidateId === candidateId ? null : state.candidateRevealRequest, points: state.points.filter((point) => point.candidateId !== candidateId),
      selectedCandidateId: state.selectedCandidateId === candidateId ? null : state.selectedCandidateId,
      selectedObservation: state.selectedCandidateId === candidateId ? null : state.selectedObservation }
  }),
  clearDetection: () => setDetectionAndPersist({
    jobId: null,
    result: null,
    resultRestored: false,
    points: [],
    candidates: [],
    highlight: null,
    selectedCandidateId: null,
    selectedObservation: null,
    selectedGroupCandidateIds: [],
    candidateRevealRequest: null,
    runSignature: null,
  }),
  setVisualizationVisible: (visualizationVisible) => setAndPersist({ visualizationVisible }),
  setIgnoreAreasVisible: (ignoreAreasVisible) => setAndPersist({ ignoreAreasVisible }),
  setBlockersVisible: (blockersVisible) => setAndPersist({ blockersVisible }),
  setHighlight: (highlight) => setDetectionAndPersist({ highlight, selectedCandidateId: null, selectedObservation: null, selectedGroupCandidateIds: [], candidateRevealRequest: null, visualizationVisible: true }),
  selectCandidateGroup: (candidateIds) => setDetectionAndPersist(state => {
    const valid = new Set(state.candidates.map(candidate => candidate.id))
    return { selectedGroupCandidateIds: [...new Set(candidateIds)].filter(id => valid.has(id)),
      selectedCandidateId: null, selectedObservation: null, highlight: null, candidateRevealRequest: null, visualizationVisible: true }
  }),
  selectCandidate: (selectedCandidateId, source = 'list', observation) => setDetectionAndPersist({ selectedCandidateId, selectedObservation: selectedCandidateId && observation ? structuredClone(observation) : null, selectedGroupCandidateIds: [], highlight: null, visualizationVisible: true,
    candidateRevealRequest: source === 'viewer' && selectedCandidateId ? { candidateId: selectedCandidateId } : null,
  }),
  beginIgnoreAreaSelection: (areaId) => setAndPersist((state) => {
    if (areaId && state.ignoreAreas.some((area) => area.id === areaId)) {
      return { ignoreAreaSelectionArmed: true, activeIgnoreAreaId: areaId }
    }
    const sequence = state.ignoreAreas.length + 1
    const id = `leak-ignore-${Date.now()}-${sequence}`
    return {
      ignoreAreaSelectionArmed: true,
      activeIgnoreAreaId: id,
      ignoreAreas: [
        ...state.ignoreAreas,
        {
          id,
          label: `Allowed Area ${String(sequence).padStart(2, '0')}`,
          regions: [],
          enabled: true,
        },
      ],
    }
  }),
  finishIgnoreAreaSelection: () => setAndPersist((state) => ({
    ignoreAreaSelectionArmed: false,
    activeIgnoreAreaId: null,
    ignoreAreas: state.ignoreAreas.filter((area) => area.regions.length > 0),
  })),
  addIgnoreAreaRegion: (clipBox) => setAndPersist((state) => ({
    ignoreAreas: state.ignoreAreas.map((area) =>
      area.id === state.activeIgnoreAreaId
        ? { ...area, regions: [...area.regions, { ...clipBox }] }
        : area,
    ),
  })),
  setIgnoreAreaVisible: (areaId, visible) => setAndPersist((state) => ({
    ignoreAreas: state.ignoreAreas.map((area) => area.id === areaId ? { ...area, visible } : area),
  })),
  setIgnoreAreaEnabled: (areaId, enabled) => setAndPersist((state) => ({
    ignoreAreas: state.ignoreAreas.map((area) =>
      area.id === areaId ? { ...area, enabled } : area,
    ),
  })),
  removeIgnoreArea: (areaId) => setAndPersist((state) => ({
    ignoreAreas: state.ignoreAreas.filter((area) => area.id !== areaId),
    ...(state.activeIgnoreAreaId === areaId
      ? { activeIgnoreAreaId: null, ignoreAreaSelectionArmed: false }
      : {}),
  })),
  migrateAllowedAreas: () => setAndPersist((state) => state.ignoreAreas.length ? {
    blockers: migrateAllowedAreas(state.blockers, state.ignoreAreas, state.ignoreAreasVisible), ignoreAreas: [],
  } : {}),
  appendBlockerRegion: (blockerId, patch) => setAndPersist((state) => ({
    blockers: state.blockers.map((group) => group.id !== blockerId ? group : {
      ...group,
      baseCenter: patch.baseCenter ?? group.baseCenter, normal: patch.normal ?? group.normal,
      uAxis: patch.uAxis ?? group.uAxis, vAxis: patch.vAxis ?? group.vAxis,
      regions: [...(group.regions ?? [{ ...group, regions: undefined }]), {
        ...group, ...patch, regions: undefined, enabled: true, visible: true, id: crypto.randomUUID(),
      }],
    }),
  })),
  updateBlockerRegion: (blockerId, regionId, patch) => setAndPersist((state) => ({
    blockers: state.blockers.map((group) => group.id !== blockerId ? group : {
      ...group, regions: (group.regions ?? []).map((region) => region.id === regionId ? { ...region, ...patch, id: region.id } : region),
    }),
  })),
  removeBlockerRegion: (blockerId, regionId) => setAndPersist((state) => ({
    selectedBlocker: state.selectedBlocker?.groupId === blockerId && state.selectedBlocker.regionId === regionId
      ? { groupId: blockerId, regionId: null } : state.selectedBlocker,
    blockers: state.blockers.map((group) => group.id !== blockerId ? group : {
      ...group, regions: group.regions?.filter((region) => region.id !== regionId),
    }),
  })),
  addBlocker: (blocker) => setAndPersist((state) => ({
    blockers: [...state.blockers, blocker],
  })),
  updateBlocker: (blockerId, patch) => setAndPersist((state) => ({
    blockers: state.blockers.map((blocker) =>
      blocker.id === blockerId ? { ...blocker, ...patch, id: blocker.id } : blocker,
    ),
  })),
  removeBlocker: (blockerId) => setAndPersist((state) => ({
    selectedBlocker: state.selectedBlocker?.groupId === blockerId ? null : state.selectedBlocker,
    blockers: state.blockers.filter((blocker) => blocker.id !== blockerId),
    ...(state.blockerAreaSelectionId === blockerId
      ? { blockerAreaSelectionId: null }
      : {}),
  })),
  beginBlockerAreaSelection: (blockerId) => set((state) => ({
    selectedBlocker: state.blockers.some((blocker) => blocker.id === blockerId) ? { groupId: blockerId, regionId: null } : null,
    blockerAreaSelectionId: state.blockers.some((blocker) => blocker.id === blockerId)
      ? blockerId
      : null,
  })),
  finishBlockerAreaSelection: () => setAndPersist((state) => ({
    selectedBlocker: state.blockers.some((group) => group.id === state.selectedBlocker?.groupId && group.regions?.length !== 0) ? state.selectedBlocker : null,
    blockerAreaSelectionId: null, blockers: state.blockers.filter((blocker) => blocker.regions?.length !== 0),
  })),
  restoreProjectState: (projectState, saved) => {
    const restored = projectState ?? createDefaultLeakPreviewProjectState()
    set({
      ...structuredClone(restored),
      displayFilter: { ...defaultLeakDisplayFilter, ...restored.displayFilter },
      blockers: structuredClone(migrateAllowedAreas(restored.blockers, restored.ignoreAreas, restored.ignoreAreasVisible)),
      ignoreAreas: [],
      sourceEmitterIds: restored.sourceEmitterIds ?? [],
      detectionMode: restored.detectionMode ?? 'planar',
      observer: restoredObserver(restored.observer),
      sceneToken: null,
      sceneIdentity: null,
      jobId: null,
      result: saved?.result ?? null,
      resultRestored: Boolean(saved),
      points: saved?.points.map((point) => point.surfacePosition ? { ...point, position: point.surfacePosition } : point) ?? [],
      candidates: saved?.candidates ?? [],
      highlight: saved?.highlight ?? null,
      selectedCandidateId: saved?.selectedCandidateId ?? null,
      selectedObservation: null,
      selectedGroupCandidateIds: [],
      candidateRevealRequest: null,
      ignoreAreaSelectionArmed: false,
      activeIgnoreAreaId: null,
      blockerAreaSelectionId: null,
      selectedBlocker: null,
      runSignature: saved?.inputSignature ?? null,
    })
  },
  clear: () => set({
    displayFilter: { ...defaultLeakDisplayFilter },
    detectionMode: 'escape_directions',
    observer: { ...defaultObserverSettings },
    sceneToken: null,
    sceneIdentity: null,
    sourceMode: 'face',
    sourceEmitterIds: [],
    sourceFaceIds: [],
    sourceComponentIds: [],
    sourceBodyFaceCount: 0,
    quality: 'balanced',
    directions: ['neg_z'],
    jobId: null,
    result: null,
    resultRestored: false,
    points: [],
    candidates: [],
    visualizationVisible: true,
    ignoreAreasVisible: true,
    blockersVisible: true,
    highlight: null,
    selectedCandidateId: null,
    selectedObservation: null,
    selectedGroupCandidateIds: [],
    candidateRevealRequest: null,
    ignoreAreaSelectionArmed: false,
    activeIgnoreAreaId: null,
    ignoreAreas: [],
    blockers: [],
    blockerAreaSelectionId: null,
    selectedBlocker: null,
    runSignature: null,
  }),
})})

export function snapshotLeakPreviewProjectState(): LeakPreviewProjectState {
  return projectStateFromStore(store.getState())
}

function rebindSignature(signature: string | null, sceneToken: string | null): string | null {
  if (!signature) return null
  try {
    return previewSignature({ ...JSON.parse(signature), sceneToken })
  } catch {
    return null
  }
}

function savedDetection(state: LeakPreviewState): SavedLeakPreviewResult | null {
  if (!state.result) return null
  return {
    format: 'leak-preview-result.v1',
    result: state.result,
    points: state.points,
    candidates: state.candidates,
    selectedCandidateId: state.selectedCandidateId,
    highlight: state.highlight,
    inputSignature: rebindSignature(state.runSignature, null),
  }
}

export function snapshotLeakPreviewResult(): SavedLeakPreviewResult | null {
  return savedDetection(store.getState())
}

export function useLeakPreviewStore<T>(selector: (state: LeakPreviewState) => T): T {
  return useStore(store, selector)
}

export const leakPreviewStore = store
