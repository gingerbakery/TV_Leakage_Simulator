import { describe, expect, it } from 'vitest'

import type {
  RayHit,
  RayTraceConfig,
  RayTraceRequest,
  RayTraceResult,
  ScenePayload,
  TransformRule,
  Vec3,
} from '@/api'
import { createRayTraceSceneMeshSignature } from '@/features/raytracing/ray-result-source-context'
import { createDatumEmitter } from '@/features/raytracing/ray-tracing-model'

import {
  buildPrototypeLeakagePreviewData,
  prototypeLeakagePreviewUnavailableReason,
} from './leakage-preview-data'

const config: RayTraceConfig = {
  ray_count: 10,
  max_depth: 1,
  seed: 42,
  min_energy: 0,
  epsilon_mm: 1e-4,
  k_abs: 0.12,
  k_brdf: 1,
  termination_mode: 'threshold',
  contribution_mode: 'summary',
  intersection_backend: 'bvh',
  compute_backend: 'cpu',
  store_ray_paths: true,
  max_stored_paths: 10,
}

function createScene(maximumX = 10): ScenePayload {
  const vertices: Vec3[] = [
    [0, 0, 0],
    [maximumX, 0, 0],
    [maximumX, 10, 0],
    [0, 10, 0],
    [0, 0, 10],
    [maximumX, 0, 10],
    [maximumX, 10, 10],
    [0, 10, 10],
  ]
  const faces: [number, number, number][] = [
    [0, 1, 2],
    [0, 2, 3],
    [4, 6, 5],
    [4, 7, 6],
    [0, 5, 1],
    [0, 4, 5],
    [3, 2, 6],
    [3, 6, 7],
    [0, 3, 7],
    [0, 7, 4],
    [1, 5, 6],
    [1, 6, 2],
  ]
  const faceIds = faces.map((_, index) => index)
  const component = {
    object_id: 1,
    component_id: 1,
    object_name: 'Inline box',
    component_name: 'Inline box',
    face_indices: faceIds,
    face_count: faces.length,
    area_mm2: 0,
    bbox_min: [0, 0, 0] as Vec3,
    bbox_max: [maximumX, 10, 10] as Vec3,
    is_truncated: false,
    color: null,
  }
  return {
    schema_version: 'mesh-scene.v1',
    units: { length: 'mm' },
    coordinate_system: {
      handedness: 'right',
      axes: { x: 'model_x', y: 'model_y', z: 'model_z' },
    },
    mesh: {
      vertices,
      faces,
      face_ids: faceIds,
      face_component_ids: faces.map(() => 1),
      face_material_ids: faces.map(() => ''),
      face_normals: faces.map(() => [0, 0, 1] as Vec3),
      face_centroids: faces.map(() => [0, 0, 0] as Vec3),
      face_areas_mm2: faces.map(() => 0),
      feature_edge_segments: [],
    },
    objects: [component],
    components: [component],
    metadata: {
      face_count: faces.length,
      vertex_count: vertices.length,
      component_count: 1,
      source_file: 'inline.step',
      synthetic: true,
      import_note: 'inline prototype_aabb fixture',
      receiver_face_hint: [],
      scene_token: 'inline-scene',
    },
  }
}

function hit(
  point: Vec3,
  eventType: string,
  incomingEnergyLumen = 0.25,
): RayHit {
  return {
    face_index: eventType === 'surface' ? 0 : -1,
    component_id: eventType === 'surface' ? 0 : null,
    material_id: null,
    point,
    normal: [0, 0, 1],
    distance_mm: 1,
    incoming_energy_lumen: incomingEnergyLumen,
    outgoing_energy_lumen: incomingEnergyLumen,
    depth: 0,
    event_type: eventType,
    receiver_id: eventType === 'receiver' ? 'receiver-inline' : null,
    optical_profile_id: null,
    reflectance: null,
    scatter_model: null,
    optical_assignment_source: null,
    ray_kind: 'direct',
  }
}

interface RequestOptions {
  roiFaces?: number[]
  transformRules?: TransformRule[]
  excludedComponentIds?: number[]
}

function createRequest({
  roiFaces,
  transformRules = [],
  excludedComponentIds = [],
}: RequestOptions = {}): RayTraceRequest {
  return {
    scene_token: 'inline-scene',
    project_name: 'inline.step',
    emitters: [],
    receivers: [],
    optical_profiles: [],
    optical_assignments: [],
    transform_rules: transformRules,
    excluded_component_ids: excludedComponentIds,
    ...(roiFaces === undefined ? {} : { roi_faces: roiFaces }),
    config: { ...config },
  }
}

function createResult(
  scene: ScenePayload,
  storedPaths: RayHit[][],
  requestOptions: RequestOptions = {},
): RayTraceResult {
  return {
    run_id: 'run-inline',
    config,
    emitters: [],
    receivers: [],
    receiver_grids: [],
    optical_profiles: [],
    total_rays: 10,
    receiver_hit_count: 0,
    surface_hit_count: 0,
    terminated_ray_count: 10,
    contribution_summary: {
      schema_version: 'rt-contribution.v1',
      direct_receiver_hit_count: 0,
      direct_receiver_flux_lumen: 0,
      reflected_receiver_hit_count: 0,
      reflected_receiver_flux_lumen: 0,
      receivers: {},
      components: {},
      faces: {},
      materials: {},
      lobes: {},
      depths: {},
    },
    runtime_sec: 0,
    stored_paths: storedPaths,
    metrics: {},
    source_context: {
      schema_version: 'ray-result-source.v1',
      cad_case_id: 'case-inline',
      cad_display_name: 'inline.step',
      scene: {
        schema_version: 'ray-result-scene.v1',
        scene_token: scene.metadata.scene_token,
        scene_schema_version: scene.schema_version,
        face_count: scene.metadata.face_count,
        vertex_count: scene.metadata.vertex_count,
        component_count: scene.metadata.component_count,
        mesh_signature: createRayTraceSceneMeshSignature(scene),
      },
      requests: [createRequest(requestOptions)],
    },
  }
}


function createZeroPowerResult(scene: ScenePayload, count = 104): RayTraceResult {
  const emitter = { ...createDatumEmitter('zero-source', [2, 2, 2], [0, 0, 0]), power_mode: 'total' as const, power_lumen: 0 }
  const result = createResult(scene, Array.from({ length: count }, (_, index) => [
    hit([2, 2, 2], 'emitter', 0),
    hit([5, 2 + index % 7, 5], 'surface', 0),
    { ...hit([15, 2 + index % 7, 5], 'receiver', 0), receiver_flux_lumen: 0 },
  ]))
  result.total_rays = 500000
  result.receiver_hit_count = count
  result.emitters = [emitter]
  result.source_context!.requests[0].emitters = [structuredClone(emitter)]
  result.receiver_grids = [{ receiver_id: 'receiver-inline', resolution: [1, 1], bin_area_mm2: 1,
    flux_lumen: [[0]], hit_count: count, flux_squared_lumen2: 0, flux_squared_lumen2_grid: [[0]] }]
  result.metrics = { 'receiver-inline': { hit_count: count, total_flux_lumen: 0,
    peak_nit_est: 0, mean_nit_est: 0, p95_nit_est: 0 } }
  result.contribution_summary.direct_receiver_hit_count = count
  return result
}

describe('buildPrototypeLeakagePreviewData', () => {
  it('finds the receiver-bound segment exit with a slab intersection', () => {
    const scene = createScene()
    const result = createResult(scene, [
      [hit([5, 5, 5], 'surface'), hit([15, 7, 5], 'receiver', 0.25)],
    ])

    const preview = buildPrototypeLeakagePreviewData(result, scene)

    expect(preview.status).toBe('ready')
    expect(preview.method).toBe('prototype_aabb')
    expect(preview.samples).toHaveLength(1)
    expect(preview.samples[0]).toMatchObject({
      runId: 'run-inline',
      pathIndex: 0,
      receiverId: 'receiver-inline',
      exitPoint: [10, 6, 5],
      exitFaces: ['x_max'],
      weight: 0.25,
    })
    expect(preview.samples[0].outgoingDirection[0]).toBeCloseTo(0.9805806757)
    expect(preview.samples[0].outgoingDirection[1]).toBeCloseTo(0.1961161351)
    expect(preview.samples[0].outgoingDirection[2]).toBe(0)
  })

  it('returns a ready prototype with zero samples for a closed result', () => {
    const scene = createScene()
    const result = createResult(scene, [
      [hit([2, 2, 2], 'surface'), hit([8, 8, 8], 'receiver')],
      [hit([5, 5, 5], 'surface'), hit([15, 5, 5], 'escaped')],
    ])

    expect(buildPrototypeLeakagePreviewData(result, scene)).toEqual({
      status: 'ready',
      method: 'prototype_aabb',
      bounds: {
        minimum: [0, 0, 0],
        maximum: [10, 10, 10],
      },
      coverage: {
        resultReceiverHitCount: 0,
        receiverGridHitCount: 0,
        storedReceiverPathCount: 1,
        validCrossingCount: 0,
        fullCapture: false,
        receivers: [{
          receiverId: 'receiver-inline',
          hitCount: 0,
          storedPathCount: 1,
          validCrossingCount: 0,
          complete: false,
        }],
      },
      samples: [],
    })
  })

  it('uses the transformed trace-mesh AABB, rotation order, and pivot rules', () => {
    const scene = createScene(20)
    const defaultPivotRule: TransformRule = {
      rule_id: 'default-pivot',
      target_type: 'component',
      object_id: 1,
      label: 'Default pivot',
      enabled: true,
      move: { x: 100, y: 0, z: 0 },
      tilt: { x: 0, y: 0, z: 90 },
    }
    const defaultPivotResult = createResult(
      scene,
      [[hit([110, 0, 5], 'surface'), hit([120, 0, 5], 'receiver', 0.4)]],
      { transformRules: [defaultPivotRule] },
    )
    const explicitPivotRule: TransformRule = {
      ...defaultPivotRule,
      rule_id: 'explicit-pivot',
      label: 'Explicit pivot',
      move: { x: 30, y: 0, z: 0 },
      pivot: { x: 0, y: 0, z: 0 },
    }
    const explicitPivotResult = createResult(
      scene,
      [[hit([25, 10, 5], 'surface'), hit([35, 10, 5], 'receiver', 0.3)]],
      { transformRules: [explicitPivotRule] },
    )
    const orderedRotationRule: TransformRule = {
      ...defaultPivotRule,
      rule_id: 'ordered-rotation',
      label: 'Ordered rotation',
      move: { x: 0, y: 0, z: 0 },
      tilt: { x: 90, y: 0, z: 90 },
    }
    const orderedRotationResult = createResult(
      scene,
      [[hit([10, 10, 5], 'surface'), hit([10, 20, 5], 'receiver', 0.2)]],
      { transformRules: [orderedRotationRule] },
    )

    const defaultPivotPreview = buildPrototypeLeakagePreviewData(
      defaultPivotResult,
      scene,
    )
    const explicitPivotPreview = buildPrototypeLeakagePreviewData(
      explicitPivotResult,
      scene,
    )
    const orderedRotationPreview = buildPrototypeLeakagePreviewData(
      orderedRotationResult,
      scene,
    )

    expect(defaultPivotPreview.status).toBe('ready')
    expect(defaultPivotPreview.status === 'ready' && defaultPivotPreview.bounds)
      .toEqual({ minimum: [105, -5, 0], maximum: [115, 15, 10] })
    expect(defaultPivotPreview.samples[0]?.exitPoint[0]).toBeCloseTo(115)
    expect(defaultPivotPreview.samples[0]?.exitPoint[1]).toBeCloseTo(0)
    expect(defaultPivotPreview.samples[0]?.exitPoint[2]).toBeCloseTo(5)
    expect(explicitPivotPreview.status).toBe('ready')
    expect(explicitPivotPreview.samples[0]?.exitPoint[0]).toBeCloseTo(30)
    expect(explicitPivotPreview.samples[0]?.exitPoint[1]).toBeCloseTo(10)
    expect(explicitPivotPreview.samples[0]?.exitPoint[2]).toBeCloseTo(5)
    expect(orderedRotationPreview.status).toBe('ready')
    expect(orderedRotationPreview.samples[0]?.exitPoint[0]).toBeCloseTo(10)
    expect(orderedRotationPreview.samples[0]?.exitPoint[1]).toBeCloseTo(15)
    expect(orderedRotationPreview.samples[0]?.exitPoint[2]).toBeCloseTo(5)
  })

  it('excludes zero-energy receiver paths from the preview samples', () => {
    const scene = createScene()
    const result = createResult(scene, [
      [hit([5, 5, 5], 'surface'), hit([15, 5, 5], 'receiver', 0)],
      [hit([5, 5, 5], 'surface'), hit([15, 5, 5], 'receiver', 0.1)],
    ])

    const preview = buildPrototypeLeakagePreviewData(result, scene)

    expect(preview.status).toBe('ready')
    expect(preview.samples).toHaveLength(1)
    expect(preview.samples[0]).toMatchObject({ pathIndex: 1, weight: 0.1 })
  })

  it('fails closed when the source request used ROI faces', () => {
    const scene = createScene()
    const result = createResult(scene, [
      [hit([5, 5, 5], 'surface'), hit([15, 5, 5], 'receiver')],
    ], { roiFaces: [2] })

    expect(buildPrototypeLeakagePreviewData(result, scene)).toEqual({
      status: 'unsupported',
      method: 'prototype_aabb',
      reason: 'roi_trace_not_supported_by_prototype_aabb',
      samples: [],
    })
  })

  it('fails closed when the request excluded trace components', () => {
    const scene = createScene()
    const result = createResult(
      scene,
      [[hit([5, 5, 5], 'surface'), hit([15, 5, 5], 'receiver')]],
      { excludedComponentIds: [1] },
    )

    expect(buildPrototypeLeakagePreviewData(result, scene)).toEqual({
      status: 'unsupported',
      method: 'prototype_aabb',
      reason: 'excluded_components_not_supported_by_prototype_aabb',
      samples: [],
    })
  })

  it('distinguishes missing receiver path samples from a verified zero-hit result', () => {
    const scene = createScene()
    const missingStoredPaths = createResult(scene, [])
    missingStoredPaths.receiver_hit_count = 4
    const receiverInsideBounds = createResult(scene, [
      [hit([2, 2, 2], 'surface'), hit([8, 8, 8], 'receiver')],
    ])
    receiverInsideBounds.receiver_hit_count = 1

    expect(
      prototypeLeakagePreviewUnavailableReason(missingStoredPaths),
    ).toBe('stored_receiver_paths_not_available')
    expect(
      buildPrototypeLeakagePreviewData(missingStoredPaths, scene),
    ).toEqual({
      status: 'unsupported',
      method: 'prototype_aabb',
      reason: 'stored_receiver_paths_not_available',
      samples: [],
    })
    expect(
      buildPrototypeLeakagePreviewData(receiverInsideBounds, scene),
    ).toEqual({
      status: 'unsupported',
      method: 'prototype_aabb',
      reason: 'receiver_path_samples_missing',
      samples: [],
    })
  })

  it('fails closed when the result scene signature differs', () => {
    const sourceScene = createScene()
    const currentScene = createScene(11)
    const result = createResult(sourceScene, [])

    expect(buildPrototypeLeakagePreviewData(result, currentScene)).toEqual({
      status: 'unsupported',
      method: 'prototype_aabb',
      reason: 'scene_mesh_signature_mismatch',
      samples: [],
    })
  })

  it('excludes malformed and non-crossing paths without rejecting valid data', () => {
    const scene = createScene()
    const invalidWeight = hit([15, 5, 5], 'receiver')
    invalidWeight.incoming_energy_lumen = Number.NaN
    const result = createResult(scene, [
      [hit([15, 5, 5], 'receiver')],
      [hit([12, 5, 5], 'surface'), hit([15, 5, 5], 'receiver')],
      [hit([5, 5, 5], 'surface'), hit([9, 5, 5], 'receiver')],
      [hit([5, 5, 5], 'surface'), hit([15, 5, 5], 'escaped')],
      [hit([Number.NaN, 5, 5], 'surface'), hit([15, 5, 5], 'receiver')],
      [hit([5, 5, 5], 'surface'), invalidWeight],
      [hit([5, 5, 5], 'surface'), hit([15, 5, 5], 'receiver', 0.5)],
    ])

    const preview = buildPrototypeLeakagePreviewData(result, scene)

    expect(preview.status).toBe('ready')
    expect(preview.samples).toHaveLength(1)
    expect(preview.samples[0]).toMatchObject({
      pathIndex: 6,
      receiverId: 'receiver-inline',
      exitPoint: [10, 5, 5],
      exitFaces: ['x_max'],
      outgoingDirection: [1, 0, 0],
      weight: 0.5,
    })
  })

  it('marks edge and corner exits as ambiguous instead of selecting one face', () => {
    const scene = createScene()
    const edgeResult = createResult(scene, [
      [hit([5, 5, 5], 'surface'), hit([15, 15, 5], 'receiver')],
    ])
    const cornerResult = createResult(scene, [
      [hit([5, 5, 5], 'surface'), hit([15, 15, 15], 'receiver')],
    ])

    const edgePreview = buildPrototypeLeakagePreviewData(edgeResult, scene)
    const cornerPreview = buildPrototypeLeakagePreviewData(cornerResult, scene)

    expect(edgePreview.status).toBe('ready')
    expect(edgePreview.samples[0]?.exitPoint).toEqual([10, 10, 5])
    expect(edgePreview.samples[0]?.exitFaces).toEqual(['x_max', 'y_max'])
    expect(cornerPreview.status).toBe('ready')
    expect(cornerPreview.samples[0]?.exitPoint).toEqual([10, 10, 10])
    expect(cornerPreview.samples[0]?.exitFaces).toEqual([
      'x_max',
      'y_max',
      'z_max',
    ])
  })

  it('allows continuous rendering only when receiver hits, stored paths, and valid crossings match', () => {
    const scene = createScene()
    const complete = createResult(scene, [
      [hit([5, 5, 5], 'surface'), hit([15, 5, 5], 'receiver')],
      [hit([5, 6, 5], 'surface'), hit([15, 6, 5], 'receiver')],
    ])
    complete.receiver_hit_count = 2
    complete.receiver_grids = [{
      receiver_id: 'receiver-inline',
      resolution: [1, 1],
      bin_area_mm2: 1,
      flux_lumen: [[0.5]],
      hit_count: 2,
    }]
    const truncated = structuredClone(complete)
    truncated.stored_paths.pop()

    const completePreview = buildPrototypeLeakagePreviewData(complete, scene)
    const truncatedPreview = buildPrototypeLeakagePreviewData(truncated, scene)

    expect(completePreview.status).toBe('ready')
    expect(completePreview.status === 'ready' && completePreview.coverage)
      .toMatchObject({
        resultReceiverHitCount: 2,
        receiverGridHitCount: 2,
        storedReceiverPathCount: 2,
        validCrossingCount: 2,
        fullCapture: true,
        receivers: [{
          receiverId: 'receiver-inline',
          hitCount: 2,
          storedPathCount: 2,
          validCrossingCount: 2,
          complete: true,
        }],
      })
    expect(truncatedPreview.status).toBe('ready')
    expect(truncatedPreview.status === 'ready' && truncatedPreview.coverage)
      .toMatchObject({
        storedReceiverPathCount: 1,
        validCrossingCount: 1,
        fullCapture: false,
        receivers: [{ complete: false }],
      })
  })
  it('shows a verified zero-power run as empty light while preserving geometric crossing coverage', () => {
    const scene = createScene()
    const result = createZeroPowerResult(scene)
    const before = structuredClone(result)
    const preview = buildPrototypeLeakagePreviewData(result, scene)

    expect(preview.status).toBe('ready')
    expect(preview.samples).toEqual([])
    expect(preview.status === 'ready' && preview.coverage).toMatchObject({
      resultReceiverHitCount: 104, receiverGridHitCount: 104, storedReceiverPathCount: 104,
      validCrossingCount: 104, zeroEnergyCrossingCount: 104, fullCapture: true,
      receivers: [{ receiverId: 'receiver-inline', hitCount: 104, storedPathCount: 104,
        validCrossingCount: 104, zeroEnergyCrossingCount: 104, complete: true }],
    })
    expect(result).toEqual(before)
  })

  it.each([
    ['positive source request', (result: RayTraceResult) => { result.source_context!.requests[0].emitters[0].power_lumen = 1 }],
    ['missing emitted-power evidence', (result: RayTraceResult) => { result.emitters = [] }],
    ['truncated receiver paths', (result: RayTraceResult) => { result.stored_paths.pop() }],
    ['missing receiver flux', (result: RayTraceResult) => { delete result.stored_paths[0].at(-1)!.receiver_flux_lumen }],
    ['nonzero saved incident energy', (result: RayTraceResult) => { result.stored_paths[0][0].incoming_energy_lumen = 1e-9 }],
    ['nonfinite saved incident energy', (result: RayTraceResult) => { result.stored_paths[0][0].incoming_energy_lumen = NaN }],
    ['nonfinite saved geometry', (result: RayTraceResult) => { result.stored_paths[0][1].point[0] = NaN }],
    ['receiver without a scene exit', (result: RayTraceResult) => { result.stored_paths[0].at(-1)!.point = [7, 3, 5] }],
    ['nonzero receiver grid energy', (result: RayTraceResult) => { result.receiver_grids[0].flux_lumen[0][0] = 1e-9 }],
    ['malformed receiver grid', (result: RayTraceResult) => { result.receiver_grids[0].flux_lumen = [] }],
    ['nonzero receiver metric', (result: RayTraceResult) => { (result.metrics['receiver-inline'] as Record<string, unknown>).total_flux_lumen = 1e-9 }],
    ['missing receiver metric', (result: RayTraceResult) => { delete result.metrics['receiver-inline'] }],
    ['nonzero summary energy', (result: RayTraceResult) => { result.contribution_summary.direct_receiver_flux_lumen = 1e-9 }],
    ['inconsistent receiver count', (result: RayTraceResult) => { result.receiver_grids[0].hit_count += 1 }],
  ] as const)('does not mistake %s for verified no light', (_name, mutate) => {
    const scene = createScene()
    const result = createZeroPowerResult(scene)
    mutate(result)
    expect(buildPrototypeLeakagePreviewData(result, scene)).toMatchObject({
      status: 'unsupported', reason: 'receiver_path_samples_missing', samples: [],
    })
  })

})
