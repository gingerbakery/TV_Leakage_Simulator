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
import { allLeakPreviewDirections } from './leak-preview-model'

interface LeakPreviewState {
  sceneToken: string | null
  sourceFaceIds: number[]
  quality: LeakPreviewQuality
  directions: LeakPreviewDirection[]
  jobId: string | null
  result: RayTraceResult | null
  points: LeakPreviewPoint[]
  candidates: LeakPreviewCandidate[]
  selectedCandidateId: string | null
  ignoreAreaSelectionArmed: boolean
  activeIgnoreAreaId: string | null
  ignoreAreas: LeakPreviewIgnoreArea[]
  blockers: LeakPreviewBlocker[]
  runSignature: string | null
  ensureScene(sceneToken: string): void
  setSourceFaceIds(faceIds: number[]): void
  setQuality(quality: LeakPreviewQuality): void
  toggleDirection(direction: LeakPreviewDirection): void
  setJobId(jobId: string | null): void
  setRunSignature(signature: string | null): void
  setDetection(result: RayTraceResult, points: LeakPreviewPoint[], candidates: LeakPreviewCandidate[]): void
  clearDetection(): void
  selectCandidate(candidateId: string | null): void
  beginIgnoreAreaSelection(areaId?: string): void
  finishIgnoreAreaSelection(): void
  addIgnoreAreaRegion(clipBox: LeakPreviewIgnoreArea['regions'][number]): void
  setIgnoreAreaEnabled(areaId: string, enabled: boolean): void
  removeIgnoreArea(areaId: string): void
  addBlocker(blocker: LeakPreviewBlocker): void
  updateBlocker(blockerId: string, patch: Partial<LeakPreviewBlocker>): void
  removeBlocker(blockerId: string): void
  clear(): void
}

const store = createStore<LeakPreviewState>()((set) => ({
  sceneToken: null,
  sourceFaceIds: [],
  quality: 'balanced',
  directions: [...allLeakPreviewDirections],
  jobId: null,
  result: null,
  points: [],
  candidates: [],
  selectedCandidateId: null,
  ignoreAreaSelectionArmed: false,
  activeIgnoreAreaId: null,
  ignoreAreas: [],
  blockers: [],
  runSignature: null,
  ensureScene: (sceneToken) => set((state) =>
    state.sceneToken === sceneToken
      ? state
      : {
          sceneToken,
          sourceFaceIds: [],
          jobId: null,
          result: null,
          points: [],
          candidates: [],
          selectedCandidateId: null,
          ignoreAreaSelectionArmed: false,
          activeIgnoreAreaId: null,
          ignoreAreas: [],
          blockers: [],
          runSignature: null,
        }),
  setSourceFaceIds: (sourceFaceIds) => set({ sourceFaceIds: [...new Set(sourceFaceIds)] }),
  setQuality: (quality) => set({ quality }),
  toggleDirection: (direction) => set((state) => {
    const selected = state.directions.includes(direction)
    if (selected && state.directions.length === 1) return state
    return {
      directions: selected
        ? state.directions.filter((value) => value !== direction)
        : [...state.directions, direction],
    }
  }),
  setJobId: (jobId) => set({ jobId }),
  setRunSignature: (runSignature) => set({ runSignature }),
  setDetection: (result, points, candidates) => set({
    result,
    points,
    candidates,
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
  })),
  clear: () => set({
    sceneToken: null,
    sourceFaceIds: [],
    directions: [...allLeakPreviewDirections],
    jobId: null,
    result: null,
    points: [],
    candidates: [],
    selectedCandidateId: null,
    ignoreAreaSelectionArmed: false,
    activeIgnoreAreaId: null,
    ignoreAreas: [],
    blockers: [],
    runSignature: null,
  }),
}))

export function useLeakPreviewStore<T>(selector: (state: LeakPreviewState) => T): T {
  return useStore(store, selector)
}

export const leakPreviewStore = store
