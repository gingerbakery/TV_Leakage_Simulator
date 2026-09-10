import { useStore } from 'zustand'
import { createStore } from 'zustand/vanilla'

import type { RayTraceResult } from '@/api'
import type {
  LeakPreviewCandidate,
  LeakPreviewIgnoreArea,
  LeakPreviewPoint,
  LeakPreviewQuality,
} from './leak-preview-model'

interface LeakPreviewState {
  sceneToken: string | null
  sourceFaceIds: number[]
  quality: LeakPreviewQuality
  jobId: string | null
  result: RayTraceResult | null
  points: LeakPreviewPoint[]
  candidates: LeakPreviewCandidate[]
  selectedCandidateId: string | null
  ignoreAreaSelectionArmed: boolean
  ignoreAreas: LeakPreviewIgnoreArea[]
  runSignature: string | null
  ensureScene(sceneToken: string): void
  setSourceFaceIds(faceIds: number[]): void
  setQuality(quality: LeakPreviewQuality): void
  setJobId(jobId: string | null): void
  setRunSignature(signature: string | null): void
  setDetection(result: RayTraceResult, points: LeakPreviewPoint[], candidates: LeakPreviewCandidate[]): void
  clearDetection(): void
  selectCandidate(candidateId: string | null): void
  setIgnoreAreaSelectionArmed(armed: boolean): void
  addIgnoreArea(area: Omit<LeakPreviewIgnoreArea, 'id' | 'label' | 'enabled'>): void
  setIgnoreAreaEnabled(areaId: string, enabled: boolean): void
  removeIgnoreArea(areaId: string): void
  clear(): void
}

const store = createStore<LeakPreviewState>()((set) => ({
  sceneToken: null,
  sourceFaceIds: [],
  quality: 'balanced',
  jobId: null,
  result: null,
  points: [],
  candidates: [],
  selectedCandidateId: null,
  ignoreAreaSelectionArmed: false,
  ignoreAreas: [],
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
          ignoreAreas: [],
          runSignature: null,
        }),
  setSourceFaceIds: (sourceFaceIds) => set({ sourceFaceIds: [...new Set(sourceFaceIds)] }),
  setQuality: (quality) => set({ quality }),
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
  setIgnoreAreaSelectionArmed: (ignoreAreaSelectionArmed) => set({ ignoreAreaSelectionArmed }),
  addIgnoreArea: ({ clipBox }) => set((state) => {
    const sequence = state.ignoreAreas.length + 1
    return {
      ignoreAreaSelectionArmed: false,
      ignoreAreas: [
        ...state.ignoreAreas,
        {
          id: `leak-ignore-${Date.now()}-${sequence}`,
          label: `Ignore Area ${String(sequence).padStart(2, '0')}`,
          clipBox: { ...clipBox },
          enabled: true,
        },
      ],
    }
  }),
  setIgnoreAreaEnabled: (areaId, enabled) => set((state) => ({
    ignoreAreas: state.ignoreAreas.map((area) =>
      area.id === areaId ? { ...area, enabled } : area,
    ),
  })),
  removeIgnoreArea: (areaId) => set((state) => ({
    ignoreAreas: state.ignoreAreas.filter((area) => area.id !== areaId),
  })),
  clear: () => set({
    sceneToken: null,
    sourceFaceIds: [],
    jobId: null,
    result: null,
    points: [],
    candidates: [],
    selectedCandidateId: null,
    ignoreAreaSelectionArmed: false,
    ignoreAreas: [],
    runSignature: null,
  }),
}))

export function useLeakPreviewStore<T>(selector: (state: LeakPreviewState) => T): T {
  return useStore(store, selector)
}

export const leakPreviewStore = store
