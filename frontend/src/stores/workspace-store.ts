import { useStore } from 'zustand'
import { createStore, type StoreApi } from 'zustand/vanilla'

export interface ActiveCad {
  path: string
  displayName: string
}

export interface WorkspaceSnapshot {
  activeCad: ActiveCad | null
  selectedFaceIds: number[]
  selectedComponentIds: number[]
  hiddenComponentIds: number[]
  excludedComponentIds: number[]
  deletedComponentIds: number[]
  activeRayTraceJobId: string | null
}

export interface WorkspaceActions {
  setActiveCad(cad: ActiveCad | null): void
  setSelectedFaceIds(faceIds: Iterable<number>): void
  toggleSelectedFaceId(faceId: number): void
  setSelectedComponentIds(componentIds: Iterable<number>): void
  toggleSelectedComponentId(componentId: number): void
  setHiddenComponentIds(componentIds: Iterable<number>): void
  setExcludedComponentIds(componentIds: Iterable<number>): void
  setDeletedComponentIds(componentIds: Iterable<number>): void
  setActiveRayTraceJobId(jobId: string | null): void
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

function createSceneSnapshot(): Omit<WorkspaceSnapshot, 'activeCad'> {
  return {
    selectedFaceIds: [],
    selectedComponentIds: [],
    hiddenComponentIds: [],
    excludedComponentIds: [],
    deletedComponentIds: [],
    activeRayTraceJobId: null,
  }
}

function createWorkspaceSnapshot(): WorkspaceSnapshot {
  return {
    activeCad: null,
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
        set({ excludedComponentIds: normalizeIds(componentIds) })
      },
      setDeletedComponentIds: (componentIds) => {
        set({ deletedComponentIds: normalizeIds(componentIds) })
      },
      setActiveRayTraceJobId: (activeRayTraceJobId) => {
        set({ activeRayTraceJobId })
      },
      clearSceneState: () => {
        set(createSceneSnapshot())
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
  selectedFaceIds: (state: WorkspaceStore) => state.selectedFaceIds,
  selectedComponentIds: (state: WorkspaceStore) =>
    state.selectedComponentIds,
  hiddenComponentIds: (state: WorkspaceStore) => state.hiddenComponentIds,
  excludedComponentIds: (state: WorkspaceStore) =>
    state.excludedComponentIds,
  deletedComponentIds: (state: WorkspaceStore) =>
    state.deletedComponentIds,
  activeRayTraceJobId: (state: WorkspaceStore) =>
    state.activeRayTraceJobId,
  actions: (state: WorkspaceStore) => state.actions,
}
