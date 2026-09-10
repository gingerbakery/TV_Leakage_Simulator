import { describe, expect, it } from 'vitest'
import { createWorkspaceStore } from './workspace-store'
import { normalizeFaceDisplayColors } from './face-display-color'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'

describe('independent face display color state', () => {
  it('replaces and resets only requested faces without touching optical state or results', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.setActiveCad({ path: 'a.step', displayName: 'a.step' })
    actions.upsertMaterialAssignment({ assignmentId: 'face', componentId: 1, targetType: 'faces', faceIds: [0, 1],
      baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin', profileId: '', bsdfAssetId: '', enabled: true })
    actions.setActiveCadCaseResult(createRayTraceResultFixture())
    actions.setActiveRayTraceJobId('completed-job')
    const before = store.getState()
    actions.setFaceColor(1, [0, 1], '#EF4444')
    actions.setFaceColor(1, [2], '#EF4444')
    expect(store.getState().faceColorOverrides).toEqual([{ componentId: 1, faceIds: [0, 1, 2], color: '#ef4444' }])
    actions.setFaceColor(1, [1], '#22c55e')
    actions.setFaceColor(2, [3], '#2563eb')
    actions.setFaceColor(1, [0], null)
    expect(store.getState().faceColorOverrides).toEqual([
      { componentId: 1, faceIds: [2], color: '#ef4444' },
      { componentId: 1, faceIds: [1], color: '#22c55e' },
      { componentId: 2, faceIds: [3], color: '#2563eb' },
    ])
    const faceColors = store.getState().faceColorOverrides
    actions.setComponentColor(1, '#a855f7')
    expect(store.getState().faceColorOverrides).toBe(faceColors)
    expect(store.getState().materialAssignments).toBe(before.materialAssignments)
    expect(store.getState().rayTraceConfig).toBe(before.rayTraceConfig)
    expect(store.getState().activeRayTraceJobId).toBe(before.activeRayTraceJobId)
    expect(store.getState().cadCases).toBe(before.cadCases)
    expect(store.getState().restoredRayTraceResult).toBe(before.restoredRayTraceResult)
    actions.deleteComponent(1, [0, 1, 2])
    expect(store.getState().faceColorOverrides).toEqual([{ componentId: 2, faceIds: [3], color: '#2563eb' }])
  })

  it('isolates cases and does not transfer triangle IDs with Copy Setup', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'a.step', displayName: 'a.step' })
    const firstCaseId = store.getState().activeCadCaseId!
    actions.setFaceColor(1, [0, 1], '#ef4444')
    actions.addCadCase({ path: 'b.step', displayName: 'b.step' })
    const secondCaseId = store.getState().activeCadCaseId!
    expect(store.getState().faceColorOverrides).toEqual([])
    actions.setFaceColor(2, [3], '#22c55e')
    actions.setActiveCadCase(firstCaseId)
    expect(store.getState().faceColorOverrides).toEqual([{ componentId: 1, faceIds: [0, 1], color: '#ef4444' }])
    actions.copyActiveSetupToCases([{ caseId: secondCaseId, componentIdMap: { 1: 2 } }])
    actions.setActiveCadCase(secondCaseId)
    expect(store.getState().faceColorOverrides).toEqual([])
    actions.setActiveCadCase(firstCaseId)
    actions.setActiveCad({ path: 'different.step', displayName: 'different.step' })
    expect(store.getState().faceColorOverrides).toEqual([])
  })

  it('normalizes duplicated IDs, hexadecimal colors and conflicting imported groups', () => {
    expect(normalizeFaceDisplayColors([
      { componentId: 1, faceIds: [0, 1, 1, -1, 0.5], color: '#EF4444' },
      { componentId: 1, faceIds: [1], color: '#22C55E' },
      { componentId: -1, faceIds: [2], color: '#ffffff' },
      { componentId: 1, faceIds: [2], color: 'red' },
    ])).toEqual([{ componentId: 1, faceIds: [0], color: '#ef4444' }, { componentId: 1, faceIds: [1], color: '#22c55e' }])
  })
})
