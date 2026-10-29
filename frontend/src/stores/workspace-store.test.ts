import { createBsdfFixture } from '@/test/bsdf-fixture'
import { createSceneFixture } from '@/test/scene-fixture'
import { createBitsamProject, parseBitsamProject, serializeBitsamProject } from '@/features/projects/bitsam-project'
import { describe, expect, it } from 'vitest'

import {
  createDatumEmitter,
  createDatumReceiver,
  createFaceEmitter,
} from '@/features/raytracing/ray-tracing-model'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'

import {
  cadCaseRayTraceSetupHash,
  createWorkspaceStore,
  defaultRayPathDisplayFilters,
  defaultRayTraceConfig,
  maxReflectionDepth,
  snapshotCadCasesForRayTrace,
  type WorkspaceProjectState,
} from './workspace-store'

describe('workspace store', () => {
  it('keeps jobs, results and batch identity when an Emitter is hidden', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'visibility.step', displayName: 'Visibility' })
    actions.upsertEmitter(createDatumEmitter('emitter_001', [0,0,0], [0,0,0]))
    actions.setActiveRayTraceJobId('running')
    const result = createRayTraceResultFixture()
    const id = store.getState().activeCadCaseId!
    const before = snapshotCadCasesForRayTrace(store.getState(), [id])[0].setupHash
    actions.setEmitterVisible('emitter_001', false)
    expect(store.getState().emitters[0]).toMatchObject({ enabled: true, visible: false })
    expect(store.getState().activeRayTraceJobId).toBe('running')
    actions.setRestoredRayTraceResult(result)
    const cached = store.getState().restoredRayTraceResult
    actions.setEmitterVisible('emitter_001', true)
    expect(store.getState().restoredRayTraceResult).toBe(cached)
    expect(snapshotCadCasesForRayTrace(store.getState(), [id])[0].setupHash).toBe(before)
  })

  it('shares BSDF and saved materials across all Case lifecycle paths while keeping assignments separate', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    const asset = createBsdfFixture()
    const profile = { id: 'shared-material', name: 'Shared BSDF', baseMaterialId: 'pc_black',
      surfaceId: 'matte_black_resin', bsdfAssetId: asset.id }
    actions.addBsdfAsset(asset)
    actions.addCustomOpticalProfile(profile)
    actions.addCadCase({ path: 'case1.step', displayName: 'CASE1' })
    const first = store.getState().activeCadCaseId!
    actions.upsertMaterialAssignment({ assignmentId: 'case1-part', componentId: 1,
      targetType: 'part', faceIds: [], ...profile, profileId: profile.id, enabled: true })
    actions.addCadCase({ path: 'case2.step', displayName: 'CASE2' })
    const second = store.getState().activeCadCaseId!
    expect(store.getState().bsdfAssets).toEqual([asset])
    expect(store.getState().customOpticalProfiles).toEqual([profile])
    expect(store.getState().materialAssignments).toEqual([])
    const updated = { ...asset, simulationReady: true,
      transport: { model: 'isotropic_no_shift' as const, wavelengthNm: 550, aoiExtrapolation: 'clamp' as const } }
    actions.addBsdfAsset(updated)
    actions.addCustomOpticalProfile({ ...profile, name: 'Updated material' })
    for (const item of store.getState().cadCases) {
      expect(item.workspaceState?.bsdfAssets).toEqual([updated])
      expect(item.workspaceState?.customOpticalProfiles[0].name).toBe('Updated material')
    }
    actions.setActiveCadCase(first)
    expect(store.getState().materialAssignments[0].bsdfAssetId).toBe(asset.id)
    expect(store.getState().bsdfAssets).toEqual([updated])
    actions.removeCustomOpticalProfile(profile.id)
    actions.setCadCaseVisible(second, true)
    expect(store.getState().customOpticalProfiles).toEqual([])
    expect(store.getState().materialAssignments).toEqual([])
    actions.removeCadCase(second)
    expect(store.getState().bsdfAssets).toEqual([updated])
    actions.removeCadCase(first)
    expect(store.getState().bsdfAssets).toEqual([updated])
    actions.setActiveCad({ path: 'again.step', displayName: 'Again' })
    expect(store.getState().bsdfAssets).toEqual([updated])
    actions.resetWorkspace()
    expect(store.getState().bsdfAssets).toEqual([])
  })

  it('merges imported project libraries and saves the full shared library from another Case', () => {
    const source = createWorkspaceStore()
    const asset = createBsdfFixture()
    source.getState().actions.addCadCase({ path: 'source.step', displayName: 'Source' })
    source.getState().actions.addBsdfAsset(asset)
    const profile = { id: 'imported', name: 'Imported material', baseMaterialId: 'pc_black',
      surfaceId: 'matte_black_resin', bsdfAssetId: asset.id }
    source.getState().actions.addCustomOpticalProfile(profile)
    const project = parseBitsamProject(serializeBitsamProject(createBitsamProject(createSceneFixture(), source.getState())))
    const target = createWorkspaceStore()
    const actions = target.getState().actions
    actions.addCustomOpticalProfile({ ...profile, id: 'existing', name: 'Existing material' })
    actions.addBsdfAsset({ ...asset, id: `bsdf-${'b'.repeat(64)}` })
    actions.addCadCase({ path: 'target.step', displayName: 'Target' })
    actions.restoreProjectState(project.workspace)
    actions.addCadCase({ path: 'case2.step', displayName: 'CASE2' })
    const saved = parseBitsamProject(serializeBitsamProject(createBitsamProject(createSceneFixture(), target.getState())))
    expect(saved.workspace.customOpticalProfiles.map((item) => item.id)).toEqual(['existing', 'imported'])
    expect(saved.workspace.bsdfAssets).toHaveLength(2)
    expect(saved.workspace.materialAssignments).toEqual([])
    // Loading a legacy/empty Case setup must not erase registered library entries.
    actions.restoreProjectState({ ...project.workspace, bsdfAssets: [], customOpticalProfiles: [] })
    expect(target.getState().bsdfAssets).toHaveLength(2)
    expect(target.getState().customOpticalProfiles).toHaveLength(2)
  })

  it('accepts up to 50,000 stored diagnostic paths', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions

    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      max_stored_paths: 50_000,
    })
    expect(store.getState().rayTraceConfig.max_stored_paths).toBe(50_000)

    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      max_stored_paths: 75_000,
    })
    expect(store.getState().rayTraceConfig.max_stored_paths).toBe(50_000)
  })

  it('uses relative termination for new cases without reinterpreting legacy units', () => {
    const store = createWorkspaceStore()
    expect(store.getState().rayTraceConfig.min_energy_basis).toBe('initial_ray_fraction')
    const legacy = { ...defaultRayTraceConfig, min_energy: 1e-9 }
    delete legacy.min_energy_basis
    store.getState().actions.setRayTraceConfig(legacy)
    expect(store.getState().rayTraceConfig.min_energy_basis).toBe('absolute_lumen')
    expect(store.getState().rayTraceConfig.min_energy).toBe(1e-9)
  })

  it('adds, hides and removes Accessory CAD inside the active Case', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'main.step', displayName: 'main.step' })
    const caseId = store.getState().activeCadCaseId!
    actions.addAccessoryCad(caseId, { path: 'board.step', displayName: 'board.step' })
    let accessory = store.getState().cadCases[0].accessoryCads[0]
    expect(accessory).toMatchObject({ visible: true, cad: { path: 'board.step' } })
    actions.setAccessoryCadVisible(caseId, accessory.accessoryId, false)
    expect(store.getState().cadCases[0].accessoryCads[0].visible).toBe(false)
    actions.removeAccessoryCad(caseId, accessory.accessoryId)
    expect(store.getState().cadCases[0].accessoryCads).toEqual([])
  })

  it('restores Accessory CAD identity and visibility from a Workspace package', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'main.step', displayName: 'main.step' })
    const caseId = store.getState().activeCadCaseId!
    actions.restoreCadCaseAccessories(caseId, [{
      accessoryId: 'saved-shield',
      cad: { path: 'restored/shield.step', displayName: 'Shield.step' },
      visible: false,
    }])

    expect(store.getState().cadCases[0].accessoryCads).toEqual([{
      accessoryId: 'saved-shield',
      cad: { path: 'restored/shield.step', displayName: 'Shield.step' },
      visible: false,
    }])
  })

  it('keeps editable Case names and captions exactly as entered', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseId = store.getState().activeCadCaseId!

    actions.updateCadCaseMetadata(
      caseId,
      'CASE 01 개선 구조',
      'Front gap 0.3 mm / 보강 Rib 적용',
    )

    expect(store.getState().cadCases[0]).toMatchObject({
      name: 'CASE 01 개선 구조',
      note: 'Front gap 0.3 mm / 보강 Rib 적용',
    })
  })

  it('creates independent Cases when the same CAD path is imported again', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    const cad = { path: 'same-model.step', displayName: 'same-model.step' }

    actions.addCadCase(cad)
    const firstCaseId = store.getState().activeCadCaseId
    actions.setHiddenComponentIds([3])
    actions.addCadCase(cad)

    const state = store.getState()
    expect(state.cadCases).toHaveLength(2)
    expect(state.cadCases.map((item) => item.cad.path)).toEqual([
      cad.path,
      cad.path,
    ])
    expect(state.activeCadCaseId).not.toBe(firstCaseId)
    expect(state.cadCases.map((item) => item.visible)).toEqual([false, true])
    expect(state.hiddenComponentIds).toEqual([])

    actions.setActiveCadCase(firstCaseId!)
    expect(store.getState().hiddenComponentIds).toEqual([3])
  })

  it('normalizes CPU-only brute force to BVH whenever GPU is selected', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      compute_backend: 'cpu',
      intersection_backend: 'brute_force',
    })
    expect(store.getState().rayTraceConfig.intersection_backend).toBe(
      'brute_force',
    )

    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      compute_backend: 'gpu_cuda',
    })

    expect(store.getState().rayTraceConfig).toMatchObject({
      compute_backend: 'gpu_cuda',
      intersection_backend: 'bvh',
    })
  })

  it('keeps simulation settings isolated when switching CAD cases', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseA = store.getState().activeCadCaseId!
    actions.setHiddenComponentIds([3])
    actions.setExcludedComponentIds([4])
    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      ray_count: 25_000,
      max_depth: 7,
    })

    actions.addCadCase({ path: 'case-b.step', displayName: 'case-b.step' })
    const caseB = store.getState().activeCadCaseId!
    expect(caseB).not.toBe(caseA)
    expect(store.getState().hiddenComponentIds).toEqual([])
    actions.setHiddenComponentIds([8])
    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      ray_count: 5_000,
      max_depth: 2,
    })

    actions.setActiveCadCase(caseA)
    expect(store.getState().hiddenComponentIds).toEqual([3])
    expect(store.getState().excludedComponentIds).toEqual([4])
    expect(store.getState().rayTraceConfig).toMatchObject({
      ray_count: 25_000,
      max_depth: 7,
    })

    actions.setActiveCadCase(caseB)
    expect(store.getState().hiddenComponentIds).toEqual([8])
    expect(store.getState().rayTraceConfig).toMatchObject({
      ray_count: 5_000,
      max_depth: 2,
    })
  })

  it('captures immutable batch snapshots including unsaved active Case edits', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseA = store.getState().activeCadCaseId!
    actions.addAccessoryCad(caseA, {
      path: 'board.step',
      displayName: 'board.step',
    })
    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      ray_count: 44_000,
      max_depth: 8,
    })
    actions.addCadCase({ path: 'case-b.step', displayName: 'case-b.step' })
    const caseB = store.getState().activeCadCaseId!
    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      ray_count: 77_000,
      max_depth: 11,
    })

    const snapshots = snapshotCadCasesForRayTrace(
      store.getState(),
      [caseB, caseA, caseB],
    )

    expect(snapshots.map((item) => item.caseId)).toEqual([caseB, caseA])
    expect(snapshots[0].workspaceState.rayTraceConfig).toMatchObject({
      ray_count: 77_000,
      max_depth: 11,
    })
    expect(snapshots[1].workspaceState.rayTraceConfig).toMatchObject({
      ray_count: 44_000,
      max_depth: 8,
    })
    expect(snapshots[1].accessoryCads[0].cad.path).toBe('board.step')

    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      ray_count: 1,
    })
    expect(snapshots[0].workspaceState.rayTraceConfig.ray_count).toBe(77_000)
  })

  it('uses a stable analysis-only setup hash', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case.step', displayName: 'case.step' })
    const snapshot = snapshotCadCasesForRayTrace(
      store.getState(),
      [store.getState().activeCadCaseId!],
    )[0]
    const original = snapshot.setupHash

    actions.setHiddenComponentIds([3])
    actions.setComponentColor(3, '#123456')
    const displayOnly = snapshotCadCasesForRayTrace(
      store.getState(),
      [snapshot.caseId],
    )[0]
    expect(displayOnly.setupHash).toBe(original)

    const changed = structuredClone(displayOnly.workspaceState)
    changed.rayTraceConfig.max_depth += 1
    expect(
      cadCaseRayTraceSetupHash(
        displayOnly.cad,
        displayOnly.accessoryCads,
        changed,
      ),
    ).not.toBe(original)
  })

  it('invalidates results only when enabled Leak Preview constraints affect tracing', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'preview.step', displayName: 'preview.step' })
    const caseId = store.getState().activeCadCaseId!
    const result = createRayTraceResultFixture()
    const preview = structuredClone(
      store.getState().cadCases[0].leakPreviewState!,
    )
    preview.blockers = [{
      id: 'rib', label: 'Rib', enabled: true,
      baseCenter: [0, 0, 0], uAxis: [1, 0, 0], vAxis: [0, 1, 0], normal: [0, 0, 1],
      widthMm: 10, heightMm: 10, offsetMm: 0, depthMm: 1, reverse: false,
    }]

    actions.setActiveCadCaseResult(result)
    actions.setCadCaseLeakPreviewState(caseId, preview)
    expect(store.getState().cadCases[0].latestResult?.run_id).toBe(result.run_id)

    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      apply_preview_blockers: true,
    })
    actions.setActiveCadCaseResult(result)
    actions.setCadCaseLeakPreviewState(caseId, {
      ...preview,
      blockers: [{ ...preview.blockers[0], widthMm: 12 }],
    })
    expect(store.getState().cadCases[0].latestResult).toBeNull()
  })

  it('stores a batch job and result against an inactive Case and rejects stale results', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseA = store.getState().activeCadCaseId!
    actions.upsertReceiver(createRayTraceResultFixture().receivers[0])
    actions.addCadCase({ path: 'case-b.step', displayName: 'case-b.step' })
    const caseB = store.getState().activeCadCaseId!
    const [snapshotA, snapshotB] = snapshotCadCasesForRayTrace(
      store.getState(),
      [caseA, caseB],
    )

    actions.setCadCaseRayTraceJob(caseA, 'batch-job-a')
    expect(
      store.getState().cadCases.find((item) => item.caseId === caseA)?.batchJobId,
    ).toBe('batch-job-a')
    expect(store.getState().activeCadCaseId).toBe(caseB)
    expect(store.getState().activeRayTraceJobId).toBeNull()
    actions.setActiveCadCase(caseA)
    expect(store.getState().activeRayTraceJobId).toBeNull()
    expect(
      store.getState().cadCases.find((item) => item.caseId === caseA)?.batchJobId,
    ).toBe('batch-job-a')
    actions.setActiveCadCase(caseB)
    expect(
      store.getState().cadCases.find((item) => item.caseId === caseA)?.batchJobId,
    ).toBe('batch-job-a')
    expect(
      actions.validateCadCaseRayTraceSetup(caseA, snapshotA.setupHash),
    ).toBe('current')

    const result = createRayTraceResultFixture()
    expect(
      actions.setCadCaseRayTraceResult(caseA, result, snapshotA.setupHash),
    ).toBe('saved')
    expect(store.getState().activeCadCaseId).toBe(caseB)
    expect(
      store.getState().cadCases.find((item) => item.caseId === caseA)?.latestResult?.run_id,
    ).toBe(result.run_id)
    expect(
      store.getState().cadCases.find((item) => item.caseId === caseA)?.batchJobId,
    ).toBeNull()

    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      max_depth: store.getState().rayTraceConfig.max_depth + 1,
    })
    const staleResult = structuredClone(result)
    staleResult.run_id = 'stale-run'
    actions.setCadCaseRayTraceJob(caseB, 'batch-job-b')
    expect(
      actions.validateCadCaseRayTraceSetup(caseB, snapshotB.setupHash),
    ).toBe('stale')
    expect(
      actions.setCadCaseRayTraceResult(caseB, staleResult, snapshotB.setupHash),
    ).toBe('stale')
    expect(
      store.getState().cadCases.find((item) => item.caseId === caseB)?.latestResult,
    ).toBeNull()
    expect(
      store.getState().cadCases.find((item) => item.caseId === caseB)?.batchJobId,
    ).toBeNull()
  })

  it('detaches an active Case from older single-run sources when Batch saves a result', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseId = store.getState().activeCadCaseId!
    const oldResult = createRayTraceResultFixture()
    oldResult.run_id = 'old-single-run'
    actions.upsertReceiver(oldResult.receivers[0])
    actions.setActiveRayTraceJobId('old-single-job')
    actions.setActiveCadCaseResult(oldResult)
    actions.setRestoredRayTraceResult(oldResult)
    const [snapshot] = snapshotCadCasesForRayTrace(
      store.getState(),
      [caseId],
    )
    const batchResult = structuredClone(oldResult)
    batchResult.run_id = 'new-batch-run'

    expect(actions.setCadCaseRayTraceResult(
      caseId,
      batchResult,
      snapshot.setupHash,
    )).toBe('saved')

    const state = store.getState()
    const restoredCase = state.cadCases.find((item) => item.caseId === caseId)
    expect(restoredCase?.latestJobId).toBeNull()
    expect(restoredCase?.latestResult?.run_id).toBe('new-batch-run')
    expect(state.activeRayTraceJobId).toBeNull()
    expect(state.restoredRayTraceResult).toBeNull()
  })

  it('keeps Case topology locked during execution and clears only the expected single job', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseA = store.getState().activeCadCaseId!
    actions.addCadCase({ path: 'case-b.step', displayName: 'case-b.step' })
    const caseB = store.getState().activeCadCaseId!

    actions.setSingleRayTraceRun({ caseId: caseB, jobId: 'segment-1' })
    actions.setActiveCadCase(caseA)
    actions.setCadCaseVisible(caseA, true)
    actions.removeCadCase(caseA)
    actions.addCadCase({ path: 'blocked.step', displayName: 'blocked.step' })
    actions.addAccessoryCad(caseB, {
      path: 'blocked-accessory.step',
      displayName: 'blocked-accessory.step',
    })

    expect(store.getState()).toMatchObject({
      activeCadCaseId: caseB,
      singleRayTraceRun: { caseId: caseB, jobId: 'segment-1' },
    })
    expect(store.getState().cadCases.map((item) => item.caseId)).toEqual([
      caseA,
      caseB,
    ])
    expect(store.getState().cadCases[1].accessoryCads).toEqual([])

    // A late terminal event from the previous convergence segment must not
    // clear ownership after the next segment has already started.
    actions.setSingleRayTraceRun({ caseId: caseB, jobId: 'segment-2' })
    actions.clearSingleRayTraceRun('segment-1')
    expect(store.getState().singleRayTraceRun?.jobId).toBe('segment-2')
    actions.clearSingleRayTraceRun('segment-2')
    expect(store.getState().singleRayTraceRun).toBeNull()

    actions.setBatchRayTraceActive(true)
    actions.setActiveCadCase(caseA)
    actions.removeCadCase(caseA)
    actions.addCadCase({ path: 'also-blocked.step', displayName: 'also-blocked.step' })
    expect(store.getState().activeCadCaseId).toBe(caseB)
    expect(store.getState().cadCases).toHaveLength(2)
    actions.setBatchRayTraceActive(false)
  })

  it('stores lightweight Component matching metadata with its CAD Case', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseId = store.getState().activeCadCaseId!

    actions.setCadCaseComponentMatchMetadata(caseId, [
      {
        component_id: 7,
        component_name: 'Front frame',
        object_name: 'Front frame',
        face_count: 12,
        area_mm2: 350,
        bbox_min: [0, 0, 0],
        bbox_max: [10, 20, 3],
      },
    ])

    expect(
      store.getState().cadCases.find((item) => item.caseId === caseId)
        ?.componentMatchMetadata,
    ).toEqual([
      expect.objectContaining({
        component_id: 7,
        component_name: 'Front frame',
      }),
    ])
  })

  it('restores each CAD case ray job across show/hide and case switching', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseA = store.getState().activeCadCaseId!
    actions.setActiveRayTraceJobId('ray-job-a')

    actions.setCadCaseVisible(caseA, false)
    expect(store.getState().activeRayTraceJobId).toBe('ray-job-a')

    actions.addCadCase({ path: 'case-b.step', displayName: 'case-b.step' })
    const caseB = store.getState().activeCadCaseId!
    actions.setActiveRayTraceJobId('ray-job-b')

    actions.setActiveCadCase(caseA)
    expect(store.getState().activeRayTraceJobId).toBe('ray-job-a')
    actions.setActiveCadCase(caseB)
    expect(store.getState().activeRayTraceJobId).toBe('ray-job-b')
  })

  it('preserves Ray results when an unchanged Emitter is applied again', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseId = store.getState().activeCadCaseId!
    const emitter = createFaceEmitter('emitter-001', [11, 10, 11])
    actions.upsertEmitter(emitter)
    actions.setActiveRayTraceJobId('ray-job-a')
    const result = createRayTraceResultFixture()
    actions.setActiveCadCaseResult(result)

    actions.upsertEmitter(structuredClone(emitter))

    const state = store.getState()
    expect(state.activeRayTraceJobId).toBe('ray-job-a')
    expect(
      state.cadCases.find((item) => item.caseId === caseId)?.latestResult?.run_id,
    ).toBe(result.run_id)
  })

  it('preserves unchecked Receiver results and replaces only the recalculated Receiver', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseId = store.getState().activeCadCaseId!
    const result = createRayTraceResultFixture()
    const receiverOne = result.receivers[0]
    const receiverTwo = {
      ...structuredClone(receiverOne),
      receiver_id: 'receiver_002',
      display_name: 'Receiver 2',
    }
    result.receivers = [receiverOne, receiverTwo]
    result.receiver_grids.push({
      ...structuredClone(result.receiver_grids[0]),
      receiver_id: receiverTwo.receiver_id,
    })
    result.metrics[receiverTwo.receiver_id] = {
      ...structuredClone(
        result.metrics[receiverOne.receiver_id] as Record<string, unknown>,
      ),
      peak_nit_est: 8,
    }
    actions.upsertReceiver(receiverOne)
    actions.upsertReceiver(receiverTwo)
    actions.setActiveCadCaseResult(result)

    actions.toggleComponentVisibility(1)
    expect(
      store.getState().cadCases.find((item) => item.caseId === caseId)
        ?.latestResult?.run_id,
    ).toBe(result.run_id)

    actions.setReceiverEnabled(receiverOne.receiver_id, false)
    expect(
      store.getState().cadCases.find((item) => item.caseId === caseId)
        ?.latestResult?.receivers.map((receiver) => receiver.receiver_id),
    ).toEqual([receiverOne.receiver_id, receiverTwo.receiver_id])

    const updatedReceiverTwo = {
      ...receiverTwo,
      width_mm: receiverTwo.width_mm + 1,
    }
    actions.upsertReceiver(updatedReceiverTwo)
    const resultAfterEdit = store.getState().cadCases.find(
      (item) => item.caseId === caseId,
    )?.latestResult
    expect(resultAfterEdit?.receivers.map((receiver) => receiver.receiver_id)).toEqual([
      receiverOne.receiver_id,
    ])
    expect(store.getState().activeRayTraceJobId).toBeNull()

    const receiverTwoRun = structuredClone(result)
    receiverTwoRun.run_id = 'run-test-002'
    receiverTwoRun.receivers = [updatedReceiverTwo]
    receiverTwoRun.receiver_grids = receiverTwoRun.receiver_grids.filter(
      (grid) => grid.receiver_id === receiverTwo.receiver_id,
    )
    receiverTwoRun.metrics = {
      _performance_summary: structuredClone(result.metrics._performance_summary),
      [receiverTwo.receiver_id]: {
        ...structuredClone(
          result.metrics[receiverTwo.receiver_id] as Record<string, unknown>,
        ),
        peak_nit_est: 6,
      },
    }
    receiverTwoRun.stored_paths = []
    actions.setActiveCadCaseResult(receiverTwoRun)

    const merged = store.getState().cadCases.find(
      (item) => item.caseId === caseId,
    )?.latestResult
    expect(merged).toBeDefined()
    const mergedResult = merged!
    expect(mergedResult.receivers.map((receiver) => receiver.receiver_id)).toEqual([
      receiverOne.receiver_id,
      receiverTwo.receiver_id,
    ])
    expect(
      (mergedResult.metrics[receiverOne.receiver_id] as Record<string, number>)
        .peak_nit_est,
    ).toBe(12.5)
    expect(
      (mergedResult.metrics[receiverTwo.receiver_id] as Record<string, number>)
        .peak_nit_est,
    ).toBe(6)
  })

  it('removes one Receiver result from a Case without deleting its setup', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseId = store.getState().activeCadCaseId!
    const result = createRayTraceResultFixture()
    const receiverOne = result.receivers[0]
    const receiverTwo = {
      ...structuredClone(receiverOne),
      receiver_id: 'receiver_002',
      display_name: 'Side Receiver',
    }
    actions.upsertReceiver(receiverOne)
    actions.upsertReceiver(receiverTwo)
    result.receivers = [receiverOne, receiverTwo]
    result.metrics[receiverTwo.receiver_id] = structuredClone(
      result.metrics[receiverOne.receiver_id],
    )
    result.receiver_grids.push({
      ...structuredClone(result.receiver_grids[0]),
      receiver_id: receiverTwo.receiver_id,
    })
    actions.setActiveCadCaseResult(result)

    actions.removeCadCaseReceiverResult(caseId, receiverTwo.receiver_id)

    const state = store.getState()
    expect(state.receivers.map((receiver) => receiver.receiver_id)).toContain(
      receiverTwo.receiver_id,
    )
    const savedResult = state.cadCases.find(
      (item) => item.caseId === caseId,
    )?.latestResult
    expect(savedResult?.receivers.map((receiver) => receiver.receiver_id)).toEqual([
      receiverOne.receiver_id,
    ])
    expect(savedResult?.receiver_grids).toHaveLength(1)
    expect(savedResult?.metrics[receiverTwo.receiver_id]).toBeUndefined()
  })

  it.each([
    ['receivers'], ['emitters'], ['runOptions'],
    ['receivers', 'emitters'], ['receivers', 'runOptions'], ['emitters', 'runOptions'],
    ['receivers', 'emitters', 'runOptions'], [],
  ] as import('./workspace-store').CopySetupSection[][])('copies only selected setup sections: %j', (...sections) => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'source.step', displayName: 'Source' })
    const sourceId = store.getState().activeCadCaseId!
    actions.upsertReceiver({ ...createDatumReceiver('source-rx', [1, 2, 3], [0, 0, 0]), source_face_indices: [10] })
    actions.upsertEmitter(createFaceEmitter('source-led', [10]))
    actions.setRayTraceConfig({ ...store.getState().rayTraceConfig, max_depth: 77 })
    actions.addCadCase({ path: 'target.step', displayName: 'Target' })
    const targetId = store.getState().activeCadCaseId!
    actions.upsertReceiver(createDatumReceiver('target-rx', [9, 8, 7], [0, 0, 0]))
    actions.upsertEmitter(createDatumEmitter('target-led', [9, 8, 7], [0, 0, 0]))
    actions.renameComponent(1, 'Target component')
    actions.setHiddenComponentIds([1])
    actions.setActiveCadCase(sourceId)
    const before = structuredClone(store.getState().cadCases.find((item) => item.caseId === targetId)!)
    actions.copyActiveSetupToCases([{ caseId: targetId, componentIdMap: {} }], sections)
    const after = store.getState().cadCases.find((item) => item.caseId === targetId)!
    if (sections.length === 0) { expect(after).toEqual(before); return }
    const expected = structuredClone(before.workspaceState!)
    if (sections.includes('receivers')) expected.receivers = store.getState().receivers.map((item) => ({ ...item, source_face_indices: [] }))
    if (sections.includes('emitters')) expected.emitters = store.getState().emitters.map((item) => ({ ...item, face_indices: [], source_face_indices: [] }))
    if (sections.includes('runOptions')) expected.rayTraceConfig = store.getState().rayTraceConfig
    expect(after.workspaceState).toEqual(expected)
    expect(store.getState().activeCadCaseId).toBe(sourceId)
  })

  it('links preview constraints and ACC to selected Cases, with independent editing and unlink', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'source.step', displayName: 'Source' })
    const sourceId = store.getState().activeCadCaseId!
    const preview = structuredClone(store.getState().cadCases[0].leakPreviewState!)
    preview.ignoreAreas = [{ id: 'allowed', label: 'Allowed', enabled: true, regions: [] }]
    preview.blockers = [{ id: 'block', label: 'Block', enabled: true,
      baseCenter: [0, 0, 0], uAxis: [1, 0, 0], vAxis: [0, 1, 0], normal: [0, 0, 1],
      widthMm: 10, heightMm: 10, offsetMm: 0, depthMm: 1, reverse: false }]
    actions.setCadCaseLeakPreviewState(sourceId, preview)
    actions.addAccessoryCad(sourceId, { path: 'shared.step', displayName: 'ACC' })
    actions.addCadCase({ path: 'target.step', displayName: 'Target' })
    const targetId = store.getState().activeCadCaseId!
    actions.upsertReceiver(createDatumReceiver('keep-rx', [1, 2, 3], [0, 0, 0]))
    actions.addCadCase({ path: 'other.step', displayName: 'Other' })
    const otherId = store.getState().activeCadCaseId!
    actions.addCadCase({ path: 'untouched.step', displayName: 'Untouched' })
    const untouchedId = store.getState().activeCadCaseId!
    actions.setActiveCadCase(sourceId)
    const getCase = (id: string) => store.getState().cadCases.find((item) => item.caseId === id)!
    actions.copyActiveSetupToCases([targetId, otherId].map((caseId) => ({ caseId, componentIdMap: {} })), ['allowedAreas', 'blockers', 'accessoryCads'])
    expect(getCase(targetId).setupLinks).toEqual({ allowedAreas: sourceId, blockers: sourceId, accessoryCads: sourceId })
    expect(getCase(targetId).workspaceState?.receivers[0].receiver_id).toBe('keep-rx')
    expect(getCase(targetId).accessoryCads[0].cad.path).toBe('shared.step')
    expect(getCase(targetId).leakPreviewState?.sourceFaceIds).toEqual([])
    expect(getCase(untouchedId).accessoryCads).toEqual([])
    expect(getCase(untouchedId).leakPreviewState?.blockers).toEqual([])
    actions.setActiveCadCase(targetId)
    actions.setActiveCadCaseResult(createRayTraceResultFixture())
    actions.setCadCaseLeakPreviewState(sourceId, { ...preview, blockers: [{ ...preview.blockers[0], widthMm: 25 }] })
    expect(getCase(targetId).latestResult).toBeNull()
    expect(getCase(targetId).leakPreviewState?.blockers[0].widthMm).toBe(25)
    expect(getCase(otherId).leakPreviewState?.blockers[0].widthMm).toBe(25)
    expect(getCase(targetId).leakPreviewState?.blockers).not.toBe(getCase(sourceId).leakPreviewState?.blockers)
    actions.setCadCaseLeakPreviewState(targetId, { ...getCase(targetId).leakPreviewState!, ignoreAreas: [] })
    expect(getCase(targetId).setupLinks?.allowedAreas).toBeUndefined()
    expect(getCase(targetId).setupLinks?.blockers).toBe(sourceId)
    actions.addAccessoryCad(targetId, { path: 'own.step', displayName: 'Own ACC' })
    expect(getCase(targetId).setupLinks?.accessoryCads).toBeUndefined()
    actions.addAccessoryCad(sourceId, { path: 'new.step', displayName: 'New ACC' })
    expect(getCase(otherId).accessoryCads.map((item) => item.cad.path)).toEqual(['shared.step', 'new.step'])
    expect(getCase(targetId).accessoryCads.map((item) => item.cad.path)).toEqual(['shared.step', 'own.step'])
    actions.unlinkCaseSetup(otherId, 'blockers')
    actions.setCadCaseLeakPreviewState(sourceId, { ...preview, blockers: [] })
    expect(getCase(otherId).leakPreviewState?.blockers[0].widthMm).toBe(25)
    expect(getCase(targetId).leakPreviewState?.blockers).toEqual([])
    actions.removeCadCase(sourceId)
    expect(getCase(otherId).setupLinks?.accessoryCads).toBeUndefined()
    expect(getCase(otherId).accessoryCads).toHaveLength(2)
  })

  it('copies analysis setup while requiring CAD Surface Emitter face reselection', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const sourceCaseId = store.getState().activeCadCaseId!
    actions.upsertEmitter(createFaceEmitter('face-emitter', [10, 11]))
    actions.upsertEmitter(
      createDatumEmitter('datum-emitter', [1, 2, 3], [0, 0, 0]),
    )
    actions.upsertReceiver(
      createDatumReceiver('receiver-1', [4, 5, 6], [0, 0, 0]),
    )
    actions.renameComponent(2, 'Chassis rear')
    actions.setExcludedComponentIds([2])
    actions.upsertMaterialAssignment({
      assignmentId: 'part-material',
      componentId: 2,
      targetType: 'part',
      faceIds: [],
      baseMaterialId: 'pc_black',
      surfaceId: 'matte_black_resin',
      profileId: '',
      bsdfAssetId: '',
      enabled: true,
    })
    actions.upsertMaterialAssignment({
      assignmentId: 'face-material',
      componentId: 3,
      targetType: 'faces',
      faceIds: [10],
      baseMaterialId: 'pc_white',
      surfaceId: 'normal',
      profileId: '',
      bsdfAssetId: '',
      enabled: true,
    })
    actions.upsertTransformRule({
      ruleId: 'component-transform',
      componentId: 2,
      targetType: 'component',
      selectionMethod: 'click',
      faceIds: [],
      move: { x: 1, y: 2, z: 3 },
      tilt: { x: 0, y: 0, z: 5 },
      enabled: true,
    })
    actions.upsertTransformRule({
      ruleId: 'face-transform',
      componentId: 3,
      targetType: 'faces',
      selectionMethod: 'click',
      faceIds: [10],
      move: { x: 1, y: 0, z: 0 },
      tilt: { x: 0, y: 0, z: 0 },
      enabled: true,
    })
    actions.addCadCase({ path: 'case-b.step', displayName: 'case-b.step' })
    const targetCaseId = store.getState().activeCadCaseId!
    actions.setActiveCadCase(sourceCaseId)

    actions.copyActiveSetupToCases([
      {
        caseId: targetCaseId,
        componentIdMap: { 2: 20, 3: 30 },
      },
    ])
    const copied = store.getState().cadCases.find(
      (item) => item.caseId === targetCaseId,
    )?.workspaceState

    expect(copied?.emitters.find((item) => item.emitter_id === 'face-emitter')?.face_indices).toEqual([])
    expect(copied?.emitters.find((item) => item.emitter_id === 'datum-emitter')?.center).toEqual([1, 2, 3])
    expect(copied?.receivers[0]?.center).toEqual([4, 5, 6])
    expect(copied?.hiddenComponentIds).toEqual([])
    expect(copied?.excludedComponentIds).toEqual([20])
    expect(copied?.componentNameOverrides).toEqual({ 20: 'Chassis rear' })
    expect(copied?.materialAssignments).toMatchObject([
      { assignmentId: 'part-material', componentId: 20, faceIds: [] },
    ])
    expect(copied?.transformRules).toMatchObject([
      { ruleId: 'component-transform', componentId: 20, faceIds: [] },
    ])
  })

  it('removes an imported CAD case and safely activates the next case', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseA = store.getState().activeCadCaseId!
    actions.addCadCase({ path: 'case-b.step', displayName: 'case-b.step' })
    const caseB = store.getState().activeCadCaseId!
    actions.addCadCase({ path: 'case-c.step', displayName: 'case-c.step' })
    const caseC = store.getState().activeCadCaseId!

    actions.setActiveCadCase(caseB)
    actions.removeCadCase(caseB)
    expect(store.getState().activeCadCaseId).toBe(caseC)
    expect(store.getState().activeCad?.path).toBe('case-c.step')
    expect(store.getState().cadCases.map((item) => item.order)).toEqual([1, 2])

    actions.removeCadCase(caseA)
    actions.removeCadCase(caseC)
    expect(store.getState().activeCad).toBeNull()
    expect(store.getState().activeCadCaseId).toBeNull()
    expect(store.getState().cadCases).toEqual([])
  })

  it('normalizes selection IDs and supports toggling', () => {
    const store = createWorkspaceStore()
    const { actions } = store.getState()

    actions.setSelectedFaceIds([5, 2, 5, -1, 1.5, 0])
    expect(store.getState().selectedFaceIds).toEqual([0, 2, 5])

    actions.toggleSelectedFaceId(2)
    actions.toggleSelectedFaceId(3)
    expect(store.getState().selectedFaceIds).toEqual([0, 3, 5])

    actions.setEmitterFaceSelectionArmed(true)
    expect(store.getState().emitterFaceSelectionArmed).toBe(true)
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

  it('clears transient face selection intent on scene reset and CAD case changes', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'one.step', displayName: 'one.step' })
    const firstCaseId = store.getState().activeCadCaseId!
    actions.setFaceSelection([1, 2], [3])
    expect(store.getState().selectionKind).toBe('faces')
    actions.addCadCase({ path: 'two.step', displayName: 'two.step' })
    expect(store.getState().selectionKind).toBeNull()
    actions.setFaceSelection([4], [5])
    actions.setActiveCadCase(firstCaseId)
    expect(store.getState().selectionKind).toBeNull()
    actions.setFaceSelection([1], [3])
    actions.clearSceneState()
    expect(store.getState().selectionKind).toBeNull()
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

  it('accepts up to 1000 reflections and clamps values outside the limit', () => {
    const store = createWorkspaceStore()
    const { actions } = store.getState()

    expect(maxReflectionDepth).toBe(1000)
    for (const depth of [0, 10, 20, 50, 100, 300, 1000]) {
      actions.setRayTraceConfig({
        ...store.getState().rayTraceConfig,
        max_depth: depth,
      })
      expect(store.getState().rayTraceConfig.max_depth).toBe(depth)
    }

    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      max_depth: maxReflectionDepth + 50,
    })
    expect(store.getState().rayTraceConfig.max_depth).toBe(
      maxReflectionDepth,
    )
  })

  it('does not publish unchanged placement preview objects', () => {
    const store = createWorkspaceStore()
    const { actions } = store.getState()
    const preview = createDatumReceiver(
      '__preview_receiver__',
      [10, 20, 30],
      [0, 0, 0],
    )
    let updateCount = 0
    const unsubscribe = store.subscribe(() => {
      updateCount += 1
    })

    actions.setPlacementPreviewReceiver(preview)
    expect(updateCount).toBe(1)

    actions.setPlacementPreviewReceiver({
      ...preview,
      center: [...preview.center],
    })
    expect(updateCount).toBe(1)

    actions.setPlacementPreviewReceiver({
      ...preview,
      center: [10, 20, 31],
    })
    expect(updateCount).toBe(2)
    unsubscribe()
  })

  it('owns component, material, and transform feature state', () => {
    const store = createWorkspaceStore()
    const { actions } = store.getState()

    actions.renameComponent(2, 'Chassis rear')
    actions.toggleComponentVisibility(2)
    actions.toggleComponentTraceability(2)
    actions.upsertMaterialAssignment({
      assignmentId: 'material-part-2',
      componentId: 2,
      targetType: 'part',
      faceIds: [],
      baseMaterialId: 'pc_black',
      surfaceId: 'matte_black_resin',
      profileId: '',
      bsdfAssetId: '',
      enabled: true,
    })
    actions.upsertTransformRule({
      ruleId: 'transform-component-2',
      componentId: 2,
      targetType: 'component',
      selectionMethod: 'click',
      faceIds: [],
      move: { x: 1, y: 2, z: 3 },
      tilt: { x: 0, y: 0, z: 5 },
      enabled: true,
    })

    expect(store.getState()).toMatchObject({
      componentNameOverrides: { 2: 'Chassis rear' },
      hiddenComponentIds: [2],
      excludedComponentIds: [2],
    })
    expect(store.getState().materialAssignments).toHaveLength(1)
    expect(store.getState().transformRules).toHaveLength(1)

    actions.deleteComponent(2, [3, 4])

    expect(store.getState()).toMatchObject({
      deletedComponentIds: [2],
      hiddenComponentIds: [],
      excludedComponentIds: [],
      materialAssignments: [],
      transformRules: [],
    })
  })

  it('owns multi-scope ROI activation and clears it with scene state', () => {
    const store = createWorkspaceStore()
    const { actions } = store.getState()

    actions.setRoiDraftLabel('bottom-corner')
    actions.setRoiBoxSelectionArmed(true)
    actions.addRoiScope({
      label: store.getState().roiDraftLabel,
      source: 'box',
      view: 'front_xy',
      clipBox: { xMin: 10, xMax: 20, yMin: 30, yMax: 40 },
      components: [
        {
          componentId: 2,
          componentName: 'Rear cover',
          faceIds: [4, 3, 4],
          areaMm2: 24.5,
          bboxMin: { x: 10, y: 30, z: 0 },
          bboxMax: { x: 20, y: 40, z: 5 },
        },
      ],
    })

    expect(store.getState()).toMatchObject({
      roiScopeSequence: 1,
      roiDraftLabel: '',
      roiBoxSelectionArmed: false,
      roiScopes: [
        {
          id: 'roi-1',
          scopeId: 'bottom-corner',
          active: true,
          components: [{ faceIds: [3, 4] }],
        },
      ],
    })

    actions.setRoiScopeActive('roi-1', false)
    expect(store.getState().roiScopes[0].active).toBe(false)

    actions.clearSceneState()
    expect(store.getState().roiScopes).toEqual([])
    expect(store.getState().roiScopeSequence).toBe(0)
  })

  it('owns Step 10 placement and invalidates completed tracing state', () => {
    const store = createWorkspaceStore()
    const { actions } = store.getState()

    actions.upsertEmitter({
      emitter_id: 'emitter_001',
      emitter_type: 'face',
      face_indices: [4, 4, 2],
      normal_mode: 'face_normal',
      normal_flip: false,
      custom_normal: null,
      direction_distribution: 'lambertian',
      gaussian_sigma_deg: 12,
      power_mode: 'total',
      power_lumen: 1,
      power_density_lm_per_m2: 100,
      center: null,
      u_axis: null,
      v_axis: null,
      width_mm: null,
      height_mm: null,
      reference_mode: null,
      surface_construction: 'rectangular_fit',
      polygon_vertices: [],
      reference_vertex_indices: [],
      reference_edge_vertex_indices: [],
      reference_vertex_points: [],
      reference_edge_points: [],
      ray_count: 1000,
      seed: null,
      enabled: true,
    })
    actions.setActiveRayTraceJobId('job-1')
    actions.setEmitterRayCount(2500)
    actions.setEmitterEnabled('emitter_001', false)

    expect(store.getState()).toMatchObject({
      emitters: [
        {
          emitter_id: 'emitter_001',
          face_indices: [2, 4],
          ray_count: 2500,
          enabled: false,
        },
      ],
      activeRayTraceJobId: null,
    })
  })

  it('restores project state while clearing transient editor state', () => {
    const store = createWorkspaceStore()
    const { actions } = store.getState()
    const cad = {
      path: 'C:\\uploads\\tv-corner.step',
      displayName: 'tv-corner.step',
    }
    const projectState: WorkspaceProjectState = {
      hiddenComponentIds: [3],
      excludedComponentIds: [4],
      deletedComponentIds: [5],
      componentNameOverrides: { 3: 'Frame Middle' },
      componentColorOverrides: { 3: '#336699' },
      faceColorOverrides: [],
      materialAssignments: [],
      customOpticalProfiles: [],
      transformRules: [],
      roiScopes: [],
      roiScopeSequence: 0,
      emitters: [],
      receivers: [],
      rayTraceConfig: {
        ...defaultRayTraceConfig,
        ray_count: 50_000,
        max_depth: 10,
      },
      rayPathDisplayFilters: {
        ...defaultRayPathDisplayFilters,
        direct: true,
      },
    }

    actions.setActiveCad(cad)
    actions.setSelectedFaceIds([10])
    actions.setSelectedComponentIds([3])
    actions.setRoiBoxSelectionArmed(true)
    actions.setEmitterFaceSelectionArmed(true)
    actions.setPlacementPreviewReceiver(
      createDatumReceiver(
        '__preview_receiver__',
        [10, 20, 30],
        [0, 0, 0],
      ),
    )
    actions.setActiveRayTraceJobId('stale-job')
    actions.restoreProjectState(projectState)

    expect(store.getState()).toMatchObject({
      activeCad: cad,
      hiddenComponentIds: [3],
      excludedComponentIds: [4],
      deletedComponentIds: [5],
      componentNameOverrides: { 3: 'Frame Middle' },
      componentColorOverrides: { 3: '#336699' },
      selectedFaceIds: [],
      selectedComponentIds: [],
      roiBoxSelectionArmed: false,
      emitterFaceSelectionArmed: false,
      placementPreviewReceiver: null,
      activeRayTraceJobId: null,
      rayTraceConfig: {
        ray_count: 50_000,
        max_depth: 10,
      },
      rayPathDisplayFilters: {
        direct: true,
      },
    })
  })
})
