import { describe, expect, it } from 'vitest'

import { createWorkspaceStore } from './workspace-store'

describe('workspace store', () => {
  it('normalizes selection IDs and supports toggling', () => {
    const store = createWorkspaceStore()
    const { actions } = store.getState()

    actions.setSelectedFaceIds([5, 2, 5, -1, 1.5, 0])
    expect(store.getState().selectedFaceIds).toEqual([0, 2, 5])

    actions.toggleSelectedFaceId(2)
    actions.toggleSelectedFaceId(3)
    expect(store.getState().selectedFaceIds).toEqual([0, 3, 5])
  })

  it('resets scene-scoped state when the active CAD changes', () => {
    const store = createWorkspaceStore()
    const { actions } = store.getState()

    actions.setActiveCad({
      path: 'C:\\uploads\\first.step',
      displayName: 'first.step',
    })
    actions.setSelectedComponentIds([1, 2])
    actions.setHiddenComponentIds([3])
    actions.setExcludedComponentIds([4])
    actions.setDeletedComponentIds([5])
    actions.setActiveRayTraceJobId('job-1')

    actions.setActiveCad({
      path: 'C:\\uploads\\second.step',
      displayName: 'second.step',
    })

    expect(store.getState()).toMatchObject({
      activeCad: {
        path: 'C:\\uploads\\second.step',
        displayName: 'second.step',
      },
      selectedComponentIds: [],
      hiddenComponentIds: [],
      excludedComponentIds: [],
      deletedComponentIds: [],
      activeRayTraceJobId: null,
    })
  })

  it('can clear scene state without forgetting the active CAD', () => {
    const store = createWorkspaceStore()
    const { actions } = store.getState()
    const cad = {
      path: 'C:\\uploads\\frame.step',
      displayName: 'frame.step',
    }

    actions.setActiveCad(cad)
    actions.setSelectedFaceIds([10])
    actions.setActiveRayTraceJobId('job-2')
    actions.clearSceneState()

    expect(store.getState().activeCad).toEqual(cad)
    expect(store.getState().selectedFaceIds).toEqual([])
    expect(store.getState().activeRayTraceJobId).toBeNull()
  })

  it('creates isolated stores for tests and future workspace instances', () => {
    const first = createWorkspaceStore()
    const second = createWorkspaceStore()

    first.getState().actions.setSelectedComponentIds([7])

    expect(first.getState().selectedComponentIds).toEqual([7])
    expect(second.getState().selectedComponentIds).toEqual([])
  })
})
