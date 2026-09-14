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
} from './leak-preview-model'

interface LeakPreviewState {
  sceneToken: string | null
  sourceMode: 'face' | 'body'
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
  selectedCandidateId: string | null
  ignoreAreaSelectionArmed: boolean
  activeIgnoreAreaId: string | null
  ignoreAreas: LeakPreviewIgnoreArea[]
  blockers: LeakPreviewBlocker[]
  blockerAreaSelectionId: string | null
  runSignature: string | null
  ensureScene(sceneToken: string): void
  setSourceFaceIds(faceIds: number[]): void
  setSourceBody(componentIds: number[], faceCount: number): void
  setSourceMode(mode: 'face' | 'body'): void
  setQuality(quality: LeakPreviewQuality): void
  toggleDirection(direction: LeakPreviewDirection): void
  setJobId(jobId: string | null): void
  setRunSignature(signature: string | null): void
  setDetection(result: RayTraceResult, points: LeakPreviewPoint[], candidates: LeakPreviewCandidate[]): void
  clearDetection(): void
  setVisualizationVisible(visible: boolean): void
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
  clear(): void
}

const store = createStore<LeakPreviewState>()((set) => ({
  sceneToken: null,
  sourceMode: 'face',
  sourceFaceIds: [],
  sourceComponentIds: [],
  sourceBodyFaceCount: 0,
  quality: 'balanced',
  directions: ['pos_z'],
  jobId: null,
  result: null,
  points: [],
  candidates: [],
  visualizationVisible: true,
  selectedCandidateId: null,
  ignoreAreaSelectionArmed: false,
  activeIgnoreAreaId: null,
  ignoreAreas: [],
  blockers: [],
  blockerAreaSelectionId: null,
  runSignature: null,
  ensureScene: (sceneToken) => set((state) =>
    state.sceneToken === sceneToken
      ? state
      : {
          sceneToken,
          sourceMode: 'face',
          sourceFaceIds: [],
          sourceComponentIds: [],
          sourceBodyFaceCount: 0,
          jobId: null,
          result: null,
          points: [],
          candidates: [],
          visualizationVisible: true,
          selectedCandidateId: null,
          ignoreAreaSelectionArmed: false,
          activeIgnoreAreaId: null,
          ignoreAreas: [],
          blockers: [],
          blockerAreaSelectionId: null,
          runSignature: null,
        }),
  setSourceFaceIds: (sourceFaceIds) => set({
    sourceMode: 'face',
    sourceFaceIds: [...new Set(sourceFaceIds)],
    sourceComponentIds: [],
    sourceBodyFaceCount: 0,
  }),
  setSourceBody: (sourceComponentIds, sourceBodyFaceCount) => set({
    sourceMode: 'body',
    sourceComponentIds: [...new Set(sourceComponentIds)],
    sourceFaceIds: [],
    sourceBodyFaceCount: Math.max(0, Math.trunc(sourceBodyFaceCount)),
  }),
  setSourceMode: (sourceMode) => set({ sourceMode }),
  setQuality: (quality) => set({ quality }),
  toggleDirection: (direction) => set({ directions: [direction] }),
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
  setVisualizationVisible: (visualizationVisible) => set({ visualizationVisible }),
  selectCandidate: (selectedCandidateId) => set({ selectedCandidateId }),
  beginIgnoreAreaSelection: (areaId) => set((state) => {
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
  finishIgnoreAreaSelection: () => set((state) => ({
    ignoreAreaSelectionArmed: false,
    activeIgnoreAreaId: null,
    ignoreAreas: state.ignoreAreas.filter((area) => area.regions.length > 0),
  })),
  addIgnoreAreaRegion: (clipBox) => set((state) => ({
    ignoreAreas: state.ignoreAreas.map((area) =>
      area.id === state.activeIgnoreAreaId
        ? { ...area, regions: [...area.regions, { ...clipBox }] }
        : area,
    ),
  })),
  setIgnoreAreaEnabled: (areaId, enabled) => set((state) => ({
    ignoreAreas: state.ignoreAreas.map((area) =>
      area.id === areaId ? { ...area, enabled } : area,
    ),
  })),
  removeIgnoreArea: (areaId) => set((state) => ({
    ignoreAreas: state.ignoreAreas.filter((area) => area.id !== areaId),
    ...(state.activeIgnoreAreaId === areaId
      ? { activeIgnoreAreaId: null, ignoreAreaSelectionArmed: false }
      : {}),
  })),
  addBlocker: (blocker) => set((state) => ({
    blockers: [...state.blockers, blocker],
  })),
  updateBlocker: (blockerId, patch) => set((state) => ({
    blockers: state.blockers.map((blocker) =>
      blocker.id === blockerId ? { ...blocker, ...patch, id: blocker.id } : blocker,
    ),
  })),
  removeBlocker: (blockerId) => set((state) => ({
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
  clear: () => set({
    sceneToken: null,
    sourceMode: 'face',
    sourceFaceIds: [],
    sourceComponentIds: [],
    sourceBodyFaceCount: 0,
    directions: ['pos_z'],
    jobId: null,
    result: null,
    points: [],
    candidates: [],
    visualizationVisible: true,
    selectedCandidateId: null,
    ignoreAreaSelectionArmed: false,
    activeIgnoreAreaId: null,
    ignoreAreas: [],
    blockers: [],
    blockerAreaSelectionId: null,
    runSignature: null,
  }),
}))

export function useLeakPreviewStore<T>(selector: (state: LeakPreviewState) => T): T {
  return useStore(store, selector)
}

export const leakPreviewStore = store
