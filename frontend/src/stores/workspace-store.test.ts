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
      baseMaterialId: 'black_pc_resin',
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
    actions.setEmitterEnabled('emitter_001', false)

    expect(store.getState()).toMatchObject({
      emitters: [
        {
          emitter_id: 'emitter_001',
          face_indices: [2, 4],
          enabled: false,
        },
      ],
      activeRayTraceJobId: null,
    })
  })
})
