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
