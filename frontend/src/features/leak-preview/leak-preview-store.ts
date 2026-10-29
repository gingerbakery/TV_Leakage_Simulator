import { useStore } from 'zustand'
import { createStore } from 'zustand/vanilla'

import type { RayTraceResult } from '@/api'
import type {
  LeakPreviewCandidate,
  LeakPreviewBlocker,
  LeakPreviewIgnoreArea,
  LeakPreviewPoint,
  LeakPreviewQuality,
  LeakPreviewDirection,
  LeakPreviewProjectState,
} from './leak-preview-model'
import { workspaceStore } from '@/stores/workspace-store'

export function createDefaultLeakPreviewProjectState(): LeakPreviewProjectState {
  return {
    sourceMode: 'face',
    sourceEmitterIds: [],
    detectionMode: 'escape_directions',
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
  setQuality(quality: LeakPreviewQuality): void
  toggleDirection(direction: LeakPreviewDirection): void
  setJobId(jobId: string | null): void
  setRunSignature(signature: string | null): void
  setDetection(result: RayTraceResult, points: LeakPreviewPoint[], candidates: LeakPreviewCandidate[]): void
  clearDetection(): void
  setVisualizationVisible(visible: boolean): void
  setIgnoreAreasVisible(visible: boolean): void
  setBlockersVisible(visible: boolean): void
  selectCandidate(candidateId: string | null): void
  beginIgnoreAreaSelection(areaId?: string): void
  finishIgnoreAreaSelection(): void
  addIgnoreAreaRegion(clipBox: LeakPreviewIgnoreArea['regions'][number]): void
  setIgnoreAreaEnabled(areaId: string, enabled: boolean): void
  removeIgnoreArea(areaId: string): void
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
  setQuality: (quality) => setAndPersist({ quality }),
  toggleDirection: (direction) => setAndPersist({ directions: [direction] }),
  setJobId: (jobId) => set({ jobId }),
  setRunSignature: (runSignature) => set({ runSignature }),
  setDetection: (result, points, candidates) => set({
    result,
    points,
    candidates,
    visualizationVisible: true,
    selectedCandidateId: candidates[0]?.id ?? null,
  }),
  clearDetection: () => set({
    jobId: null,
    result: null,
    points: [],
    candidates: [],
    selectedCandidateId: null,
    runSignature: null,
  }),
  setVisualizationVisible: (visualizationVisible) => setAndPersist({ visualizationVisible }),
  setIgnoreAreasVisible: (ignoreAreasVisible) => setAndPersist({ ignoreAreasVisible }),
  setBlockersVisible: (blockersVisible) => setAndPersist({ blockersVisible }),
  selectCandidate: (selectedCandidateId) => set({ selectedCandidateId }),
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
  finishBlockerAreaSelection: () => set({ blockerAreaSelectionId: null }),
  restoreProjectState: (projectState) => {
    const restored = projectState ?? createDefaultLeakPreviewProjectState()
    set({
      ...structuredClone(restored),
      sourceEmitterIds: restored.sourceEmitterIds ?? [],
      detectionMode: restored.detectionMode ?? 'planar',
      sceneToken: null,
      sceneIdentity: null,
      jobId: null,
      result: null,
      points: [],
      candidates: [],
      selectedCandidateId: null,
      ignoreAreaSelectionArmed: false,
      activeIgnoreAreaId: null,
      blockerAreaSelectionId: null,
      runSignature: null,
    })
  },
  clear: () => set({
    detectionMode: 'escape_directions',
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
