import { describe, expect, it } from 'vitest'

import {
  createDatumEmitter,
  createDatumReceiver,
  createFaceEmitter,
} from '@/features/raytracing/ray-tracing-model'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'

import {
  createWorkspaceStore,
  defaultRayPathDisplayFilters,
  defaultRayTraceConfig,
  maxReflectionDepth,
  type WorkspaceProjectState,
} from './workspace-store'

describe('workspace store', () => {
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

  it('accepts deep-cavity reflection settings and clamps the V1 limit', () => {
    const store = createWorkspaceStore()
    const { actions } = store.getState()

    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      max_depth: 10,
    })
    expect(store.getState().rayTraceConfig.max_depth).toBe(10)

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
