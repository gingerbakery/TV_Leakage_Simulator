import { describe, expect, it } from 'vitest'

import { createBitsamProject } from '@/features/projects/bitsam-project'
import { createBsdfFixture } from '@/test/bsdf-fixture'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'

import { createWorkspaceStore, snapshotCadCasesForRayTrace } from './workspace-store'

function createBsdfCase(store: ReturnType<typeof createWorkspaceStore>, name: string) {
  const actions = store.getState().actions
  actions.addCadCase({ path: `${name}.step`, displayName: name })
  actions.upsertMaterialAssignment({
    assignmentId: `${name}-part`, componentId: 1, targetType: 'part', faceIds: [],
    baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin', profileId: '',
    bsdfAssetId: createBsdfFixture().id, enabled: true,
  })
  actions.upsertReceiver(createRayTraceResultFixture().receivers[0])
  return store.getState().activeCadCaseId!
}

describe('shared material library integration', () => {
  it.each(['transport', 'data'] as const)('rejects conflicting imported BSDF %s before changing state', (field) => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addBsdfAsset(createBsdfFixture())
    createBsdfCase(store, 'existing')
    const result = createRayTraceResultFixture()
    actions.setActiveCadCaseResult(result)
    const project = createBitsamProject(createSceneFixture(), store.getState())
    const incoming = structuredClone(project.workspace)
    const asset = incoming.bsdfAssets![0]
    if (field === 'transport') {
      asset.transport = { model: 'isotropic_no_shift', wavelengthNm: 550, aoiExtrapolation: 'clamp' }
    } else {
      asset.data.rt[0].reflectance = 0.6
    }
    const before = store.getState()
    expect(() => actions.restoreProjectState(incoming)).toThrow(/BSDF/)
    expect(store.getState()).toBe(before)
    expect(store.getState().cadCases[0].latestResult).toEqual(result)
    expect(() => actions.restoreProjectState(project.workspace)).not.toThrow()
  })

  it('invalidates affected active and inactive results but preserves unrelated Cases and job tracking', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addBsdfAsset(createBsdfFixture())
    const first = createBsdfCase(store, 'first')
    const result = createRayTraceResultFixture()
    actions.setActiveRayTraceJobId('old-first')
    actions.setActiveCadCaseResult(result)
    const second = createBsdfCase(store, 'second')
    actions.setActiveCadCaseResult(result)
    actions.addCadCase({ path: 'unrelated.step', displayName: 'unrelated' })
    const unrelated = store.getState().activeCadCaseId!
    actions.upsertReceiver(result.receivers[0])
    actions.setActiveCadCaseResult(result)
    actions.setActiveCadCase(second)
    actions.setActiveRayTraceJobId('old-second')
    actions.setRestoredRayTraceResult(result)
    const [snapshot] = snapshotCadCasesForRayTrace(store.getState(), [second])
    actions.setCadCaseRayTraceJob(second, 'running-batch')
    actions.setBatchRayTraceActive(true)
    actions.addBsdfAsset({ ...createBsdfFixture(), simulationReady: true,
      transport: { model: 'isotropic_no_shift', wavelengthNm: 550, aoiExtrapolation: 'clamp' } })

    for (const caseId of [first, second]) {
      expect(store.getState().cadCases.find((item) => item.caseId === caseId)).toMatchObject({
        latestResult: null, latestJobId: null,
      })
    }
    expect(store.getState().cadCases.find((item) => item.caseId === unrelated)?.latestResult).toEqual(result)
    expect(store.getState().activeRayTraceJobId).toBeNull()
    expect(store.getState().restoredRayTraceResult).toBeNull()
    expect(store.getState().cadCases.find((item) => item.caseId === second)?.batchJobId).toBe('running-batch')
    expect(store.getState().batchRayTraceActive).toBe(true)
    expect(actions.setCadCaseRayTraceResult(second, result, snapshot.setupHash)).toBe('stale')
    expect(store.getState().cadCases.find((item) => item.caseId === second)?.batchJobId).toBeNull()
  })

  it('keeps completed and in-flight results valid when only unused library assets change', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addBsdfAsset(createBsdfFixture())
    const caseId = createBsdfCase(store, 'first')
    const result = createRayTraceResultFixture()
    actions.setActiveCadCaseResult(result)
    const [snapshot] = snapshotCadCasesForRayTrace(store.getState(), [caseId])
    actions.setCadCaseRayTraceJob(caseId, 'batch-job')
    const unused = { ...createBsdfFixture(), id: `bsdf-${'b'.repeat(64)}` }
    actions.addBsdfAsset(unused)
    actions.addBsdfAsset({ ...unused, simulationReady: true })
    expect(actions.validateCadCaseRayTraceSetup(caseId, snapshot.setupHash)).toBe('current')
    expect(store.getState().cadCases[0].latestResult).toEqual(result)
    expect(actions.setCadCaseRayTraceResult(caseId, result, snapshot.setupHash)).toBe('saved')
  })
})
