import { migrateAllowedAreas } from './blocker-groups'
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
} from './leak-preview-model'
import { defaultObserverSettings } from './leak-preview-model'
import { workspaceStore } from '@/stores/workspace-store'

export function createDefaultLeakPreviewProjectState(): LeakPreviewProjectState {
  return {
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
  points: LeakPreviewPoint[]
  candidates: LeakPreviewCandidate[]
  visualizationVisible: boolean
  ignoreAreasVisible: boolean
  blockersVisible: boolean
  highlight: LeakPreviewHighlight | null
  setHighlight(highlight: LeakPreviewState['highlight']): void
  selectedCandidateId: string | null
  ignoreAreaSelectionArmed: boolean
  activeIgnoreAreaId: string | null
  ignoreAreas: LeakPreviewIgnoreArea[]
  blockers: LeakPreviewBlocker[]
  blockerAreaSelectionId: string | null
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
  selectCandidate(candidateId: string | null): void
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
  restoreProjectState(state?: LeakPreviewProjectState): void
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
  points: [],
  candidates: [],
  visualizationVisible: true,
  ignoreAreasVisible: true,
  blockersVisible: true,
  highlight: null,
  selectedCandidateId: null,
  ignoreAreaSelectionArmed: false,
  activeIgnoreAreaId: null,
  ignoreAreas: [],
  blockers: [],
  blockerAreaSelectionId: null,
  runSignature: null,
  ensureScene: (sceneToken) => {
    const sceneIdentity = activeSceneIdentity(sceneToken)
    let resetPersistentState = false
    set((state) => {
      if (state.sceneIdentity === sceneIdentity) {
        return state.sceneToken === sceneToken ? state : { sceneToken }
      }
      if (state.sceneIdentity === null) {
        return { sceneToken, sceneIdentity }
      }
      resetPersistentState = true
      const workspace = workspaceStore.getState()
      const active = workspace.cadCases.find((item) => item.caseId === workspace.activeCadCaseId)
      const sameCase = active && state.sceneIdentity?.startsWith(JSON.stringify([active.caseId]).slice(0, -1) + ',')
      const preserved = sameCase ? active.leakPreviewState : undefined
      return {
          sceneToken,
          sceneIdentity,
          observer: { ...defaultObserverSettings, ...preserved?.observer, sampling: preserved?.observer ? preserved.observer.sampling ?? 'manual' : 'balanced' },
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
          points: [],
          candidates: [],
          visualizationVisible: true,
          ignoreAreasVisible: preserved?.ignoreAreasVisible ?? true,
          blockersVisible: preserved?.blockersVisible ?? true,
          highlight: null,
          selectedCandidateId: null,
          ignoreAreaSelectionArmed: false,
          activeIgnoreAreaId: null,
          ignoreAreas: structuredClone(preserved?.ignoreAreas ?? []),
          blockers: structuredClone(preserved?.blockers ?? []),
          blockerAreaSelectionId: null,
          runSignature: null,
        }
    })
    if (resetPersistentState) persistActiveCase()
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
  setDetection: (result, points, candidates) => set({
    result,
    points,
    candidates: [...candidates].sort((a, b) => b.relativeStrength - a.relativeStrength),
    highlight: null,
    visualizationVisible: true,
    selectedCandidateId: [...candidates].sort((a, b) => b.relativeStrength - a.relativeStrength)[0]?.id ?? null,
  }),
  removeCandidate: (candidateId) => set((state) => {
    const candidates = state.candidates.filter((candidate) => candidate.id !== candidateId)
    return { candidates, points: state.points.filter((point) => point.candidateId !== candidateId),
      selectedCandidateId: state.selectedCandidateId === candidateId ? candidates[0]?.id ?? null : state.selectedCandidateId }
  }),
  clearDetection: () => set({
    jobId: null,
    result: null,
    points: [],
    candidates: [],
    highlight: null,
    selectedCandidateId: null,
    runSignature: null,
  }),
  setVisualizationVisible: (visualizationVisible) => setAndPersist({ visualizationVisible }),
  setIgnoreAreasVisible: (ignoreAreasVisible) => setAndPersist({ ignoreAreasVisible }),
  setBlockersVisible: (blockersVisible) => setAndPersist({ blockersVisible }),
  setHighlight: (highlight) => set({ highlight, selectedCandidateId: null, visualizationVisible: true }),
  selectCandidate: (selectedCandidateId) => set({ selectedCandidateId, highlight: null, visualizationVisible: true }),
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
    blockers: state.blockers.filter((blocker) => blocker.id !== blockerId),
    ...(state.blockerAreaSelectionId === blockerId
      ? { blockerAreaSelectionId: null }
      : {}),
  })),
  beginBlockerAreaSelection: (blockerId) => set((state) => ({
    blockerAreaSelectionId: state.blockers.some((blocker) => blocker.id === blockerId)
      ? blockerId
      : null,
  })),
  finishBlockerAreaSelection: () => setAndPersist((state) => ({
    blockerAreaSelectionId: null, blockers: state.blockers.filter((blocker) => blocker.regions?.length !== 0),
  })),
  restoreProjectState: (projectState) => {
    const restored = projectState ?? createDefaultLeakPreviewProjectState()
    set({
      ...structuredClone(restored),
      blockers: structuredClone(migrateAllowedAreas(restored.blockers, restored.ignoreAreas, restored.ignoreAreasVisible)),
      ignoreAreas: [],
      sourceEmitterIds: restored.sourceEmitterIds ?? [],
      detectionMode: restored.detectionMode ?? 'planar',
      observer: { ...defaultObserverSettings, ...restored.observer, sampling: restored.observer ? restored.observer.sampling ?? 'manual' : 'balanced' },
      sceneToken: null,
      sceneIdentity: null,
      jobId: null,
      result: null,
      points: [],
      candidates: [],
      highlight: null,
      selectedCandidateId: null,
      ignoreAreaSelectionArmed: false,
      activeIgnoreAreaId: null,
      blockerAreaSelectionId: null,
      runSignature: null,
    })
  },
  clear: () => set({
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
    points: [],
    candidates: [],
    visualizationVisible: true,
    ignoreAreasVisible: true,
    blockersVisible: true,
    highlight: null,
    selectedCandidateId: null,
    ignoreAreaSelectionArmed: false,
    activeIgnoreAreaId: null,
    ignoreAreas: [],
    blockers: [],
    blockerAreaSelectionId: null,
    runSignature: null,
  }),
})})

export function snapshotLeakPreviewProjectState(): LeakPreviewProjectState {
  return projectStateFromStore(store.getState())
}

export function useLeakPreviewStore<T>(selector: (state: LeakPreviewState) => T): T {
  return useStore(store, selector)
}

export const leakPreviewStore = store
