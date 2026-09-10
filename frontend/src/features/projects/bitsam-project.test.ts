import { describe, expect, it } from 'vitest'

import { createWorkspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'

import {
  BitsamProjectError,
  bitsamDownloadFileName,
  bitsamFileExtension,
  compareBitsamProjectScene,
  createBitsamSettingsOnlyState,
  createBitsamProject,
  createBitsamProjectFromLoadedProject,
  parseBitsamProject,
  serializeBitsamProject,
} from './bitsam-project'

function createProjectFixture() {
  const store = createWorkspaceStore()
  const activeCad = {
    path: 'C:\\company-secret\\tv-corner.step',
    displayName: 'tv-corner.step',
  }
  const actions = store.getState().actions
  actions.setActiveCad(activeCad)
  actions.renameComponent(1, 'Chassis Rear')
  actions.setHiddenComponentIds([2])
  actions.setRayTraceConfig({
    ...store.getState().rayTraceConfig,
    ray_count: 25_000,
    max_depth: 8,
  })
  actions.setSelectedFaceIds([3])
  actions.setSelectedComponentIds([1])
  actions.setActiveRayTraceJobId('temporary-job')

  return {
    activeCad,
    project: createBitsamProject(
      createSceneFixture(),
      store.getState(),
      new Date('2026-07-29T01:02:03.000Z'),
    ),
  }
}

describe('BITSAM project format', () => {
  it('round-trips face colors, rejects invalid entries and drops CAD-bound colors for a different model', () => {
    const { project } = createProjectFixture()
    project.workspace.faceColorOverrides = [{ componentId: 1, faceIds: [0, 1], color: '#ef4444' }]
    const restored = parseBitsamProject(serializeBitsamProject(project))
    const store = createWorkspaceStore()
    store.getState().actions.restoreProjectState(restored.workspace)
    expect(store.getState().faceColorOverrides).toEqual(project.workspace.faceColorOverrides)
    expect(createBitsamSettingsOnlyState(restored).workspace.faceColorOverrides).toEqual([])
    expect(() => parseBitsamProject(serializeBitsamProject(project).replace('#ef4444', 'invalid'))).toThrow(BitsamProjectError)
    const legacy = JSON.parse(serializeBitsamProject(project))
    delete legacy.workspace.faceColorOverrides
    store.getState().actions.restoreProjectState(parseBitsamProject(JSON.stringify(legacy)).workspace)
    expect(store.getState().faceColorOverrides).toEqual([])
  })
  it('round-trips coordinate-volume ROI and independent surface properties', () => {
    const store = createWorkspaceStore()
    const scene = createSceneFixture()
    const actions = store.getState().actions
    actions.setActiveCad({ path: 'corner.step', displayName: 'corner.step' })
    actions.addRoiScope({ source: 'box', view: 'coordinate',
      clipBox: { plane: 'xyz', xMin: 0, xMax: 40, yMin: 10, yMax: 35, zMin: -1, zMax: 50 },
      components: [{ componentId: 1, componentName: 'STEP Solid 1', faceIds: [0, 1, 2], areaMm2: 100,
        bboxMin: { x: 0, y: 0, z: 0 }, bboxMax: { x: 50, y: 50, z: 20 } }],
    })
    for (const faceId of [0, 2]) actions.upsertMaterialAssignment({
      assignmentId: `surface-${faceId}`, componentId: 1, targetType: 'faces', faceIds: [faceId],
      baseMaterialId: 'pc_black', surfaceId: faceId === 0 ? 'high_gloss_resin' : 'matte_black_resin',
      profileId: '', bsdfAssetId: '', enabled: true,
    })
    const project = createBitsamProject(scene, store.getState())
    const restored = parseBitsamProject(serializeBitsamProject(project))
    expect(restored.workspace.roiScopes).toEqual(project.workspace.roiScopes)
    expect(restored.workspace.materialAssignments).toEqual(project.workspace.materialAssignments)
    expect(() => parseBitsamProject(serializeBitsamProject(project).replace('"plane": "xyz"', '"plane": "invalid"'))).toThrow(BitsamProjectError)
  })

  it('round-trips persistent simulation state without local CAD paths', () => {
    const { project } = createProjectFixture()
    const serialized = serializeBitsamProject(project)
    const restored = parseBitsamProject(serialized)

    expect(restored).toEqual(project)
    expect(restored.schema_version).toBe('bitsam-project.v1')
    expect(restored.cad.display_name).toBe('tv-corner.step')
    expect(restored.workspace).toMatchObject({
      hiddenComponentIds: [2],
      componentNameOverrides: { 1: 'Chassis Rear' },
      rayTraceConfig: {
        ray_count: 25_000,
        max_depth: 8,
      },
    })
    expect(serialized).not.toContain('company-secret')
    expect(serialized).not.toContain('selectedFaceIds')
    expect(serialized).not.toContain('activeRayTraceJobId')
  })

  it('round-trips the explicit CUDA compute backend', () => {
    const store = createWorkspaceStore()
    store.getState().actions.setActiveCad({
      path: 'gpu-model.step',
      displayName: 'gpu-model.step',
    })
    store.getState().actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      compute_backend: 'gpu_cuda',
      max_depth: 1000,
    })
    const project = createBitsamProject(
      createSceneFixture(),
      store.getState(),
      new Date('2026-08-20T00:00:00.000Z'),
    )

    const restored = parseBitsamProject(serializeBitsamProject(project))

    expect(restored.workspace.rayTraceConfig.compute_backend).toBe(
      'gpu_cuda',
    )
    const reloadedStore = createWorkspaceStore()
    reloadedStore.getState().actions.restoreProjectState(restored.workspace)
    expect(reloadedStore.getState().rayTraceConfig.max_depth).toBe(1000)
  })

  it('re-saves a loaded project with an edited Move rule without a live CAD scene', () => {
    const { project } = createProjectFixture()
    project.workspace.transformRules = [
      {
        ruleId: 'component:1',
        componentId: 1,
        targetType: 'component',
        selectionMethod: 'click',
        faceIds: [],
        move: { x: 1, y: 2, z: 3 },
        tilt: { x: 0, y: 0, z: 0 },
        enabled: true,
      },
    ]
    const loaded = parseBitsamProject(serializeBitsamProject(project))
    const store = createWorkspaceStore()
    store.getState().actions.restoreProjectState(loaded.workspace)
    store.getState().actions.upsertTransformRule({
      ...store.getState().transformRules[0],
      move: { x: 11, y: -4.5, z: 8 },
    })

    const saved = createBitsamProjectFromLoadedProject(
      loaded,
      store.getState(),
      new Date('2026-09-07T00:00:00.000Z'),
    )
    const restored = parseBitsamProject(serializeBitsamProject(saved))

    expect(restored.cad).toEqual(loaded.cad)
    expect(restored.workspace.transformRules[0].move).toEqual({
      x: 11,
      y: -4.5,
      z: 8,
    })
  })

  it('round-trips user-saved optical profiles', () => {
    const store = createWorkspaceStore()
    store.getState().actions.setActiveCad({
      path: 'material-model.step',
      displayName: 'material-model.step',
    })
    store.getState().actions.addCustomOpticalProfile({
      id: 'custom-pc-white',
      name: 'Measured PC White',
      baseMaterialId: 'pc_white',
      surfaceId: 'normal',
      bsdfAssetId: '',
      opticalOverride: {
        reflectance: 0.92,
        loss: 0.08,
        specularRatio: 0.4,
        diffuseRatio: 0.6,
      },
    })
    const project = createBitsamProject(
      createSceneFixture(),
      store.getState(),
    )

    const restored = parseBitsamProject(serializeBitsamProject(project))
    expect(restored.workspace.customOpticalProfiles).toEqual(
      store.getState().customOpticalProfiles,
    )
  })

  it('loads legacy projects without compute_backend and restores CPU safely', () => {
    const { project } = createProjectFixture()
    const legacy = structuredClone(project)
    delete (
      legacy.workspace.rayTraceConfig as Partial<
        typeof legacy.workspace.rayTraceConfig
      >
    ).compute_backend

    const restored = parseBitsamProject(JSON.stringify(legacy))
    expect(restored.workspace.rayTraceConfig.compute_backend).toBeUndefined()
    const store = createWorkspaceStore()
    store.getState().actions.restoreProjectState(restored.workspace)

    expect(store.getState().rayTraceConfig.compute_backend).toBe('cpu')
  })

  it('loads legacy projects without saved optical profiles', () => {
    const { project } = createProjectFixture()
    delete (
      project.workspace as Partial<typeof project.workspace>
    ).customOpticalProfiles

    const restored = parseBitsamProject(JSON.stringify(project))
    const store = createWorkspaceStore()
    store.getState().actions.restoreProjectState(restored.workspace)

    expect(store.getState().customOpticalProfiles).toEqual([])
  })

  it('uses the custom .bitsam extension', () => {
    const { project } = createProjectFixture()

    expect(bitsamFileExtension).toBe('.bitsam')
    expect(bitsamDownloadFileName(project)).toBe(
      'tv-corner.bitsam',
    )
  })

  it('accepts the same geometry and warns about renamed CAD files', () => {
    const { project } = createProjectFixture()
    const compatibility = compareBitsamProjectScene(
      project,
      createSceneFixture(),
      {
        path: 'C:\\uploads\\renamed.step',
        displayName: 'renamed.step',
      },
    )

    expect(compatibility.compatible).toBe(true)
    expect(compatibility.reasons).toEqual([])
    expect(compatibility.warnings).toHaveLength(1)
  })

  it('rejects a project when the loaded CAD geometry differs', () => {
    const { activeCad, project } = createProjectFixture()
    const scene = createSceneFixture()
    scene.components[0].bbox_max = [61, 60, 10]

    const compatibility = compareBitsamProjectScene(
      project,
      scene,
      activeCad,
    )

    expect(compatibility.compatible).toBe(false)
    expect(compatibility.reasons).toContain(
      '부품 ID 또는 형상 경계 정보가 다릅니다.',
    )
  })

  it('round-trips the latest analysis result and stored ray paths', () => {
    const store = createWorkspaceStore()
    store.getState().actions.setActiveCad({
      path: 'model.step',
      displayName: 'model.step',
    })
    const result = createRayTraceResultFixture()
    const project = createBitsamProject(
      createSceneFixture(),
      store.getState(),
      new Date('2026-08-13T00:00:00.000Z'),
      result,
    )

    const restored = parseBitsamProject(serializeBitsamProject(project))
    expect(restored.analysis_result?.run_id).toBe(result.run_id)
    expect(restored.analysis_result?.stored_paths).toEqual(result.stored_paths)
    expect(restored.analysis_result?.receiver_grids).toEqual(
      result.receiver_grids,
    )
  })

  it('restores geometry-independent settings for a different CAD', () => {
    const { project } = createProjectFixture()
    project.workspace.emitters = [
      {
        emitter_id: 'face-emitter',
        emitter_type: 'face',
      } as (typeof project.workspace.emitters)[number],
      {
        emitter_id: 'datum-emitter',
        emitter_type: 'datum_plane',
      } as (typeof project.workspace.emitters)[number],
    ]
    project.workspace.receivers = [
      {
        receiver_id: 'datum-receiver',
        placement_mode: 'datum_plane',
      } as (typeof project.workspace.receivers)[number],
      {
        receiver_id: 'view-receiver',
        placement_mode: 'current_view',
      } as (typeof project.workspace.receivers)[number],
    ]

    const restored = createBitsamSettingsOnlyState(project)

    expect(restored.workspace.rayTraceConfig.ray_count).toBe(25_000)
    expect(restored.workspace.emitters.map((item) => item.emitter_id)).toEqual([
      'datum-emitter',
    ])
    expect(restored.workspace.receivers.map((item) => item.receiver_id)).toEqual([
      'datum-receiver',
    ])
    expect(restored.workspace.hiddenComponentIds).toEqual([])
    expect(restored.workspace.componentNameOverrides).toEqual({})
    expect(restored.workspace.customOpticalProfiles).toEqual(
      project.workspace.customOpticalProfiles,
    )
    expect(restored.restoredDatumEmitters).toBe(1)
    expect(restored.restoredDatumReceivers).toBe(1)
    expect(restored.skippedGeometryItems).toBeGreaterThan(0)
  })

  it('reports malformed and unsupported project files', () => {
    expect(() => parseBitsamProject('{broken')).toThrow(
      BitsamProjectError,
    )

    const { project } = createProjectFixture()
    const unsupported = {
      ...project,
      schema_version: 'bitsam-project.v99',
    }
    expect(() =>
      parseBitsamProject(JSON.stringify(unsupported)),
    ).toThrow('지원하지 않는 BITSAM 버전')

    const damaged = {
      ...project,
      workspace: {
        ...project.workspace,
        emitters: [{}],
      },
    }
    expect(() =>
      parseBitsamProject(JSON.stringify(damaged)),
    ).toThrow('필수 데이터가 없거나 손상')
  })
})
