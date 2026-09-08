import { describe, expect, it } from 'vitest'

import type {
  RayTraceRequest,
  RayTraceResult,
  RayTraceResultSourceContext,
  ScenePayload,
} from '@/api'
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

function createSourceContext(
  scene: ScenePayload,
  result: RayTraceResult,
): RayTraceResultSourceContext {
  const request: RayTraceRequest = {
    scene_token: scene.metadata.scene_token,
    project_name: 'model.step',
    emitters: structuredClone(result.emitters),
    receivers: structuredClone(result.receivers),
    optical_profiles: structuredClone(result.optical_profiles),
    optical_assignments: [],
    transform_rules: [],
    excluded_component_ids: [],
    roi_faces: [1, 2],
    config: {
      ray_count: result.config.ray_count,
      max_depth: result.config.max_depth,
      seed: result.config.seed,
      min_energy: result.config.min_energy,
      epsilon_mm: result.config.epsilon_mm,
      k_abs: result.config.k_abs,
      k_brdf: result.config.k_brdf,
      termination_mode: result.config.termination_mode,
      contribution_mode: result.config.contribution_mode,
      intersection_backend: result.config.intersection_backend,
      compute_backend: result.config.compute_backend,
      store_ray_paths: result.config.store_ray_paths,
      max_stored_paths: result.config.max_stored_paths,
      primary_sampling_strategy: result.config.primary_sampling_strategy,
      receiver_importance_fraction:
        result.config.receiver_importance_fraction,
      bounce_sampling_strategy: result.config.bounce_sampling_strategy,
      bounce_receiver_importance_fraction:
        result.config.bounce_receiver_importance_fraction,
    },
  }
  return {
    schema_version: 'ray-result-source.v1',
    cad_case_id: 'cad-case-1',
    cad_display_name: 'model.step',
    scene: {
      schema_version: 'ray-result-scene.v1',
      scene_token: scene.metadata.scene_token,
      scene_schema_version: scene.schema_version,
      face_count: scene.metadata.face_count,
      vertex_count: scene.metadata.vertex_count,
      component_count: scene.metadata.component_count,
      mesh_signature: 'mesh-fnv-pair-v1:test-signature',
    },
    requests: [request],
  }
}

function createProjectWithSourceContext() {
  const scene = createSceneFixture()
  const store = createWorkspaceStore()
  store.getState().actions.setActiveCad({
    path: 'model.step',
    displayName: 'model.step',
  })
  const result = createRayTraceResultFixture()
  result.source_context = createSourceContext(scene, result)
  return {
    project: createBitsamProject(
      scene,
      store.getState(),
      new Date('2026-09-04T00:00:00.000Z'),
      result,
    ),
    sourceContext: result.source_context,
  }
}

function mutableRecord(value: unknown): Record<string, unknown> {
  return value as Record<string, unknown>
}

function sourceContextRecord(
  project: ReturnType<typeof createProjectWithSourceContext>['project'],
): Record<string, unknown> {
  return mutableRecord(project.analysis_result?.source_context)
}

function sourceSceneRecord(context: Record<string, unknown>) {
  return mutableRecord(context.scene)
}

function firstSourceRequestRecord(context: Record<string, unknown>) {
  return mutableRecord((context.requests as unknown[])[0])
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
    expect(restored.analysis_result?.source_context).toBeUndefined()
  })

  it('round-trips the result source context without losing its scene or requests', () => {
    const { project, sourceContext } = createProjectWithSourceContext()

    const restored = parseBitsamProject(serializeBitsamProject(project))

    expect(restored.analysis_result?.source_context).toEqual(sourceContext)
    expect(restored.analysis_result?.source_context?.requests).toHaveLength(1)
    expect(
      restored.analysis_result?.source_context?.requests[0]?.roi_faces,
    ).toEqual([1, 2])
  })

  it('rejects malformed result source context identity and request structures', () => {
    const malformedCases: Array<[
      string,
      (context: Record<string, unknown>) => void,
    ]> = [
      [
        'source schema',
        (context) => {
          context.schema_version = 'ray-result-source.v2'
        },
      ],
      [
        'CAD case id',
        (context) => {
          context.cad_case_id = 42
        },
      ],
      [
        'CAD display name',
        (context) => {
          context.cad_display_name = ''
        },
      ],
      [
        'scene schema',
        (context) => {
          sourceSceneRecord(context).schema_version = 'ray-result-scene.v2'
        },
      ],
      [
        'scene token',
        (context) => {
          sourceSceneRecord(context).scene_token = ''
        },
      ],
      [
        'scene payload schema',
        (context) => {
          sourceSceneRecord(context).scene_schema_version = 'mesh-scene.v2'
        },
      ],
      [
        'face count',
        (context) => {
          sourceSceneRecord(context).face_count = -1
        },
      ],
      [
        'vertex count',
        (context) => {
          sourceSceneRecord(context).vertex_count = 1.5
        },
      ],
      [
        'component count',
        (context) => {
          sourceSceneRecord(context).component_count = '2'
        },
      ],
      [
        'mesh signature',
        (context) => {
          sourceSceneRecord(context).mesh_signature = ''
        },
      ],
      [
        'requests value',
        (context) => {
          context.requests = {}
        },
      ],
      [
        'empty requests',
        (context) => {
          context.requests = []
        },
      ],
      [
        'request scene token',
        (context) => {
          firstSourceRequestRecord(context).scene_token = 'different-token'
        },
      ],
      [
        'request project name',
        (context) => {
          firstSourceRequestRecord(context).project_name = null
        },
      ],
      [
        'emitters array',
        (context) => {
          firstSourceRequestRecord(context).emitters = null
        },
      ],
      [
        'receivers array',
        (context) => {
          firstSourceRequestRecord(context).receivers = null
        },
      ],
      [
        'profiles array',
        (context) => {
          firstSourceRequestRecord(context).optical_profiles = [{}]
        },
      ],
      [
        'assignments array',
        (context) => {
          firstSourceRequestRecord(context).optical_assignments = [{}]
        },
      ],
      [
        'transforms array',
        (context) => {
          firstSourceRequestRecord(context).transform_rules = [{}]
        },
      ],
      [
        'excluded array',
        (context) => {
          firstSourceRequestRecord(context).excluded_component_ids = ['1']
        },
      ],
      [
        'ROI array',
        (context) => {
          firstSourceRequestRecord(context).roi_faces = ['1']
        },
      ],
      [
        'request config',
        (context) => {
          firstSourceRequestRecord(context).config = {}
        },
      ],
    ]

    for (const [label, mutate] of malformedCases) {
      const { project } = createProjectWithSourceContext()
      mutate(sourceContextRecord(project))

      expect(
        () => parseBitsamProject(JSON.stringify(project)),
        label,
      ).toThrow('필수 데이터가 없거나 손상')
    }
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
