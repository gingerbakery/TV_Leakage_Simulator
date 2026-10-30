import { describe, expect, it } from 'vitest'

import { createEmitterAim } from '@/features/raytracing/emitter-aim'
import { createBsdfFixture } from '@/test/bsdf-fixture'
import { createDatumEmitter, createFaceEmitter } from '@/features/raytracing/ray-tracing-model'
import type { RayTraceResult } from '@/api'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'

import {
  buildLeakPreviewRequest,
  getLeakPreviewBounds,
  createEscapeEnclosureReceivers,
  escapeDirectionSummary,
  observerProbeSummary,
  defaultObserverSettings,
  createCandidateReceiver,
  candidateRoiWithSources,
  createLeakPreviewBlockerFromFaces,
  createLeakPreviewReceivers,
  detectLeakPreviewCandidates,
  leakPreviewReceiverDistanceMm,
  leakPreviewReceiverOffsetMm,
  leakPreviewRoiOffsetMm,
  resolveLeakPreviewRoiFaces,
  resizeLeakPreviewBlockerOnFace,
} from './leak-preview-model'

describe('whole-set leak preview', () => {
  it('reuses Ray Tracing face, body and datum emitters while preserving power and aiming settings', () => {
    const face = { ...createFaceEmitter('emitter_001', [0]), power_lumen: 12, ray_count: 20, normal_flip: true }
    const body = { ...createFaceEmitter('emitter_002', []), source_component_ids: [1], power_lumen: 8, ray_count: 30 }
    const datum = { ...createDatumEmitter('emitter_003', [1, 2, 3], [10, 20, 30]), power_lumen: 3,
      direction_distribution: 'gaussian' as const, gaussian_sigma_deg: 7, ray_count: 50, aim: { ...createEmitterAim([0, 0, 10]), enabled: true } }
    const emitters = [face, body, datum]
    const original = structuredClone(emitters)
    const request = buildLeakPreviewRequest({ scene: createSceneFixture(), sourceFaceIds: [], existingEmitters: emitters,
      quality: 'fast', computeBackend: 'cpu', materialAssignments: [], transformRules: [], excludedComponentIds: [], deletedComponentIds: [] })
    expect(request.emitters.map((emitter) => emitter.emitter_id)).toEqual(emitters.map((emitter) => emitter.emitter_id))
    expect(request.emitters.map((emitter) => emitter.ray_count)).toEqual([20000, 30000, 50000])
    expect(request.emitters[0]).toMatchObject({ power_lumen: 12, normal_flip: true, face_indices: [0] })
    expect(request.emitters[1]).toMatchObject({ source_component_ids: [1], power_lumen: 8 })
    expect(request.emitters[2]).toMatchObject({ center: [1, 2, 3], direction_distribution: 'gaussian', gaussian_sigma_deg: 7, power_lumen: 3 })
    expect(request.emitters[2].aim).toEqual(datum.aim)
    expect(emitters).toEqual(original)
  })

  it('preserves BSDF tables and uses CPU Source sampling for Preview only', () => {
    const asset = createBsdfFixture()
    asset.transport = { model: 'isotropic_no_shift', wavelengthNm: 550, aoiExtrapolation: 'clamp' }
    const options = { scene: createSceneFixture(), sourceFaceIds: [0], quality: 'fast' as const, computeBackend: 'cpu' as const,
      materialAssignments: [{ assignmentId: 'bsdf-face', componentId: 1, targetType: 'faces' as const, faceIds: [0],
        baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin', profileId: '', bsdfAssetId: asset.id, enabled: true }],
      bsdfAssets: [asset], transformRules: [], excludedComponentIds: [], deletedComponentIds: [] }
    const request = buildLeakPreviewRequest(options)
    expect(request.config).toMatchObject({ compute_backend: 'cpu', primary_sampling_strategy: 'source', bounce_sampling_strategy: 'source' })
    expect(request.bsdf_assets).toEqual([asset])
    expect(request.optical_profiles.some((profile) => profile.bsdf_asset_id === asset.id)).toBe(true)
    expect(() => buildLeakPreviewRequest({ ...options, bsdfAssets: [] })).toThrow('BSDF')
  })

  it('requests full final-escape collection with the requested compute device and no physical Allowed blockers', () => {
    const request = buildLeakPreviewRequest({ scene: createSceneFixture(), sourceFaceIds: [0], quality: 'fast', computeBackend: 'gpu_cuda',
      detectionMode: 'escape_directions', directions: ['neg_z'], materialAssignments: [], transformRules: [], excludedComponentIds: [], deletedComponentIds: [],
      allowedAreas: [{ id: 'hole', label: 'Hole', enabled: true, regions: [{ plane: 'xy', xMin: 1, xMax: 2, yMin: 1, yMax: 2 }] }],
    })
    expect(request.escape_collection?.direction).toBe('neg_z')
    expect(request.escape_collection?.allowed_regions).toHaveLength(1)
    expect(request.preview_blockers).toEqual([])
    expect(request.receivers).toHaveLength(6)
    expect(request.config).toMatchObject({ compute_backend: 'gpu_cuda', primary_sampling_strategy: 'source', bounce_sampling_strategy: 'source' })
  })

  it('keeps hidden Emitters, Allowed and Blockers in the trace and strips display metadata', () => {
    const emitter = createDatumEmitter('emitter_001', [0,0,0], [0,0,0])
    const area = { id: 'allowed', label: 'Allowed', enabled: true, regions: [{ plane: 'xy' as const, xMin: 0, xMax: 1, yMin: 0, yMax: 1 }] }
    const blocker = { id: 'block', label: 'Block', enabled: true, baseCenter: [0,0,0] as [number,number,number], uAxis: [1,0,0] as [number,number,number], vAxis: [0,1,0] as [number,number,number], normal: [0,0,1] as [number,number,number], widthMm: 1, heightMm: 1, offsetMm: 0, depthMm: 1, reverse: false }
    const options = { scene: createSceneFixture(), sourceFaceIds: [], quality: 'fast' as const, computeBackend: 'cpu' as const,
      detectionMode: 'escape_directions' as const, materialAssignments: [], transformRules: [], excludedComponentIds: [], deletedComponentIds: [],
      existingEmitters: [emitter], allowedAreas: [area], blockers: [blocker] }
    const shown = buildLeakPreviewRequest(options)
    const hidden = buildLeakPreviewRequest({ ...options, existingEmitters: [{ ...emitter, visible: false }], allowedAreas: [{ ...area, visible: false }], blockers: [{ ...blocker, visible: false }] })
    expect(hidden).toEqual(shown)
    expect(hidden.emitters).toHaveLength(1)
    expect(hidden.emitters[0]).not.toHaveProperty('visible')
    expect(hidden.escape_collection?.allowed_regions).toHaveLength(1)
    expect(hidden.preview_blockers).toHaveLength(1)
  })

  it('uses exact all-ray escape positions and extreme-angle bins even when no paths are stored', () => {
    const scene = createSceneFixture()
    const result = createRayTraceResultFixture()
    result.receivers = createEscapeEnclosureReceivers(scene)
    result.receiver_grids = result.receivers.map((receiver, index) => ({ receiver_id: receiver.receiver_id,
      resolution: [1, 1] as [number, number], bin_area_mm2: 1, flux_lumen: [[index === 0 ? 1 : 0]], hit_count: index === 0 ? 1 : 0 }))
    result.stored_paths = []
    const position = result.receivers[0].center
    result.metrics._escape_direction_summary = { contract: 'final_escape_directions_v1', direction: 'neg_z',
      escaped_count: 1, selected_count: 1, excluded_count: 0,
      bins: [0,30,60,80].map((min_deg, i) => ({ min_deg, max_deg: [30,60,80,90][i], count: i === 3 ? 1 : 0, flux_lumen: i === 3 ? 1 : 0 })),
      cells: [{ receiver_id: result.receivers[0].receiver_id, row: 0, column: 0, position, count: 1, grazing_count: 1, min_depth: 0, max_depth: 0, angle_sum_deg: 90 }],
    }
    const detection = detectLeakPreviewCandidates(scene, result)
    expect(detection.points).toHaveLength(1)
    expect(detection.points[0].position).toEqual(position)
    expect(detection.candidates[0].meanExitAngleDeg).toBe(90)
    expect(escapeDirectionSummary(result)?.bins[3].count).toBe(1)
    ;(result.metrics._escape_direction_summary as { selected_count: number }).selected_count = 2
    expect(escapeDirectionSummary(result)).toBeNull()
  })

  it('keeps weak normal escapes and more than eight disconnected directional candidates', () => {
    const scene = createSceneFixture()
    const result = createRayTraceResultFixture()
    result.receivers = createEscapeEnclosureReceivers(scene)
    const id = result.receivers[0].receiver_id
    result.receiver_grids = result.receivers.map((receiver, index) => ({ receiver_id: receiver.receiver_id,
      resolution: [19, 1] as [number, number], bin_area_mm2: 1,
      flux_lumen: [Array.from({ length: 19 }, (_, column) => index === 0 && column % 2 === 0 ? (column === 0 ? 1 : 1e-8) : 0)],
      hit_count: index === 0 ? 10 : 0 }))
    result.stored_paths = []
    result.metrics._escape_direction_summary = { contract: 'final_escape_directions_v1', direction: 'neg_z',
      escaped_count: 10, selected_count: 10, excluded_count: 0,
      bins: [0, 30, 60, 80].map((min_deg, i) => ({ min_deg, max_deg: [30, 60, 80, 90][i], count: i === 0 ? 10 : 0, flux_lumen: i === 0 ? 1 + 9e-8 : 0 })),
      cells: Array.from({ length: 10 }, (_, i) => ({ receiver_id: id, row: 0, column: i * 2,
        position: [i * 1000, 0, 0], count: 1, grazing_count: 0, min_depth: 0, max_depth: 0, angle_sum_deg: 0 })),
    }
    expect(escapeDirectionSummary(result)).not.toBeNull()
    const detection = detectLeakPreviewCandidates(scene, result)
    expect(detection.points).toHaveLength(10)
    expect(detection.candidates).toHaveLength(10)
    expect(detection.candidates.filter((candidate) => candidate.fluxLumen < 1e-7)).toHaveLength(9)
  })

  it('requests observer sweeps and rejects excessive budgets before starting', () => {
    const options = { scene: createSceneFixture(), sourceFaceIds: [0], quality: 'fast' as const, computeBackend: 'cpu' as const,
      detectionMode: 'escape_directions' as const, materialAssignments: [], transformRules: [], excludedComponentIds: [], deletedComponentIds: [] }
    const observer = { ...defaultObserverSettings, enabled: true, horizontal_deg: -88.9, vertical_deg: 15 }
    expect(buildLeakPreviewRequest({ ...options, observer }).observer_probe).toMatchObject({ horizontal_deg: -88.9, vertical_deg: 15 })
    expect(buildLeakPreviewRequest({ ...options, observer: { ...observer, enabled: false } }).observer_probe).toBeUndefined()
    expect(() => buildLeakPreviewRequest({ ...options, observer: { ...observer, range_deg: 10, step_deg: .1 } })).toThrow('한도 초과')
  })

  it('finds observer candidates even when no forward rays hit, without fabricating lumen', () => {
    const scene = createSceneFixture()
    const result = createRayTraceResultFixture()
    result.receivers = createEscapeEnclosureReceivers(scene)
    result.receiver_grids = result.receivers.map((receiver) => ({ receiver_id: receiver.receiver_id,
      resolution: [1, 1] as [number, number], bin_area_mm2: 1, flux_lumen: [[0]], hit_count: 0 }))
    result.metrics._observer_probe_summary = {
      contract: 'observer_path_support_v1', execution_backend: 'cpu', measure: 'path_support_counts',
      settings: { ...defaultObserverSettings, resolution: 8 }, attempted_count: 128, planned_count: 128,
      direct_count: 1, reflected_count: 2, excluded_count: 0, depth_limited_count: 0, complete: true, stopped: false, runtime_sec: .1,
      views: [{ horizontal_deg: 85, vertical_deg: 20, direction: [Math.sin(85*Math.PI/180), 0, -Math.cos(85*Math.PI/180)], observer_center: [1000,0,-100], u_axis: [0, 1, 0], v_axis: [0, 0, 1], spacing_mm: [1, 1], direct_count: 1, reflected_count: 2 }],
      cells: [{ view_index: 0, row: 0, column: 0, position: [1, 2, 3], direct_count: 1, reflected_count: 2, min_depth: 0, max_depth: 2 }],
    }
    expect(observerProbeSummary(result)).not.toBeNull()
    const detection = detectLeakPreviewCandidates(scene, result)
    expect(detection.points).toHaveLength(1)
    expect(detection.candidates).toHaveLength(1)
    const receiver = createCandidateReceiver(detection.candidates[0], 1)
    const observationDirection = detection.candidates[0].observerEvidence!.direction
    expect(receiver.center).toEqual(detection.candidates[0].observerEvidence!.receiverCenter)
    expect(receiver.normal).toEqual(observationDirection.map((value) => -value))
    expect(detection.candidates[0]).toMatchObject({ fluxLumen: 0, peakFluxLumen: 0, sampledPathCount: 3,
      observerEvidence: { horizontalDeg: 85, verticalDeg: 20, directCount: 1, reflectedCount: 2 } })
    const probe = result.metrics._observer_probe_summary as Record<string, unknown>
    probe.execution_backend = 'gpu_cuda'
    probe.requested_backend = 'gpu_cuda'
    probe.sample_contract = 'observer_per_ray_seed_batch_v1'
    probe.performance = { compute_execution_state: 'gpu_requested_cpu_only', gpu_cuda_gpu_success_count: 0 }
    expect(observerProbeSummary(result)).toBeNull()
    probe.performance = { compute_execution_state: 'gpu_active', gpu_cuda_gpu_success_count: 2 }
    expect(observerProbeSummary(result)).not.toBeNull()
    ;(result.metrics._observer_probe_summary as { reflected_count: number }).reflected_count = 3
    expect(observerProbeSummary(result)).toBeNull()
  })

  it('detects a rear 45-degree path independently of the screen camera', () => {
    const scene = createSceneFixture()
    const bounds = getLeakPreviewBounds(scene)
    const receiver = createLeakPreviewReceivers(scene, [], ['neg_z'])[0]
    const minimumZ = bounds.center[2] - bounds.size[2] / 2
    const margin = minimumZ - receiver.center[2]
    const offset = Math.max(1, bounds.size[2] / 4)
    const result = createRayTraceResultFixture()
    result.receivers = [receiver]
    result.receiver_grids = [{ receiver_id: receiver.receiver_id, resolution: [1, 1], bin_area_mm2: 1, flux_lumen: [[1]], hit_count: 1 }]
    const source = result.stored_paths[0][0]
    result.stored_paths = [[
      { ...source, point: [bounds.center[0] - offset, bounds.center[1], minimumZ + offset], event_type: 'emitter', depth: 0 },
      { ...source, point: [bounds.center[0] + margin, bounds.center[1], receiver.center[2]], event_type: 'receiver', receiver_id: receiver.receiver_id, depth: 0 },
    ]]
    expect(receiver.acceptance_angle_deg).toBe(90)
    const detection = detectLeakPreviewCandidates(scene, result)
    expect(detection.candidates).toHaveLength(1)
    expect(detection.candidates[0].meanExitAngleDeg).toBeCloseTo(45)
    expect(detection.candidates[0].center[2]).toBeCloseTo(minimumZ)
    const ignored = detectLeakPreviewCandidates(scene, result, { ignoreAreas: [{
      id: 'rear-hole', label: 'Rear hole', enabled: true,
      regions: [{ plane: 'xy', xMin: bounds.center[0] - 1, xMax: bounds.center[0] + 1,
        yMin: bounds.center[1] - 1, yMax: bounds.center[1] + 1, zMin: minimumZ, zMax: minimumZ }],
    }] })
    expect(ignored.points).toHaveLength(0)
    expect(ignored.candidates).toHaveLength(0)
  })

  it('passes active Allowed Areas into tracing before receiver aggregation', () => {
    const request = buildLeakPreviewRequest({ scene: createSceneFixture(), sourceFaceIds: [0], quality: 'fast', computeBackend: 'cpu',
      materialAssignments: [], transformRules: [], excludedComponentIds: [], deletedComponentIds: [],
      allowedAreas: [{ id: 'assembly-hole', label: 'Assembly hole', enabled: true, regions: [{
        plane: 'xy', xMin: -5, xMax: 5, yMin: -5, yMax: 5, zMin: 0, zMax: 20,
      }] }],
    })
    expect(request.preview_blockers).toHaveLength(2)
    expect(request.preview_blockers?.map((item) => item.center[2])).toEqual([0, 20])
    expect(request.preview_blockers?.every((item) => item.blocker_id.startsWith('precision-allowed-area:assembly-hole:'))).toBe(true)
  })

  it('builds an isolated two-sided source and six automatic enclosure receivers', () => {
    const scene = createSceneFixture()
    const request = buildLeakPreviewRequest({
      scene,
      sourceFaceIds: [0],
      quality: 'fast',
      computeBackend: 'cpu',
      materialAssignments: [],
      transformRules: [],
      excludedComponentIds: [],
      deletedComponentIds: [],
    })

    expect(request.roi_faces).toBeUndefined()
    expect(request.geometry_mode).toBe('preview')
    expect(request.emitters).toHaveLength(2)
    expect(request.emitters.map((emitter) => emitter.normal_flip)).toEqual([
      false,
      true,
    ])
    expect(request.emitters.reduce((sum, emitter) => sum + emitter.ray_count, 0)).toBe(100_000)
    expect(request.receivers).toHaveLength(6)
    expect(request.config.max_depth).toBe(3)
    expect(request.config.contribution_mode).toBe('summary')
    expect(request.config.store_ray_paths).toBe(true)
    expect(request.config.max_stored_paths).toBe(4_000)
    expect(request.config.primary_sampling_strategy).toBe('receiver_mis')
    expect(request.config.receiver_importance_fraction).toBe(0.65)
    expect(request.config.bounce_sampling_strategy).toBe('receiver_mis')
    expect(request.config.bounce_receiver_importance_fraction).toBe(0.55)
    expect(request.optical_profiles[0]).toMatchObject({
      profile_id: 'default',
      reflectance: 0.35,
    })
  })

  it('uses the deeper scan budget for narrow multi-bounce leaks', () => {
    const scene = createSceneFixture()
    const request = buildLeakPreviewRequest({
      scene,
      sourceFaceIds: [0],
      quality: 'deep',
      computeBackend: 'cpu',
      materialAssignments: [],
      transformRules: [],
      excludedComponentIds: [],
      deletedComponentIds: [],
    })

    expect(request.emitters.reduce((sum, emitter) => sum + emitter.ray_count, 0)).toBe(1_000_000)
    expect(request.config.max_depth).toBe(20)
  })

  it('creates enclosure receivers only for the selected exterior directions', () => {
    const scene = createSceneFixture()
    const request = buildLeakPreviewRequest({
      scene,
      sourceFaceIds: [0],
      quality: 'fast',
      directions: ['pos_z', 'neg_y'],
      computeBackend: 'cpu',
      materialAssignments: [],
      transformRules: [],
      excludedComponentIds: [],
      deletedComponentIds: [],
    })

    expect(request.receivers.map((receiver) => receiver.receiver_id)).toEqual([
      '__leak_preview_neg_y',
      '__leak_preview_pos_z',
    ])
  })

  it('creates an editable planar blocker and sends it to Preview tracing', () => {
    const scene = createSceneFixture()
    const blocker = createLeakPreviewBlockerFromFaces(scene, [0], 1)
    expect(blocker).not.toBeNull()
    expect(blocker?.depthMm).toBe(1.5)

    const request = buildLeakPreviewRequest({
      scene,
      sourceFaceIds: [0],
      quality: 'fast',
      computeBackend: 'cpu',
      materialAssignments: [],
      transformRules: [],
      excludedComponentIds: [],
      deletedComponentIds: [],
      blockers: blocker ? [{ ...blocker, offsetMm: 2, depthMm: 3 }] : [],
    })

    expect(request.preview_blockers).toHaveLength(1)
    expect(request.preview_blockers?.[0]).toMatchObject({
      width_mm: blocker?.widthMm,
      height_mm: blocker?.heightMm,
      depth_mm: 3,
      enabled: true,
    })
  })

  it('converts a Body light source into one two-sided virtual plane', () => {
    const request = buildLeakPreviewRequest({
      scene: createSceneFixture(),
      sourceFaceIds: [],
      sourceComponentIds: [1],
      quality: 'fast',
      directions: ['pos_z'],
      computeBackend: 'cpu',
      materialAssignments: [],
      transformRules: [],
      excludedComponentIds: [],
      deletedComponentIds: [],
    })

    expect(request.emitters).toHaveLength(2)
    expect(request.emitters.map((emitter) => emitter.emitter_type)).toEqual([
      'datum_plane',
      'datum_plane',
    ])
    expect(request.emitters[0].face_indices).toEqual([])
    expect(request.emitters[0].source_component_ids).toBeUndefined()
    expect(request.emitters[0]).toMatchObject({
      center: [30, 30, 5],
      u_axis: [1, 0, 0],
      v_axis: [0, 1, 0],
      width_mm: 60,
      height_mm: 60,
      reference_mode: 'leak_preview_body_plane',
      emission_direction: 'forward',
    })
    expect(request.emitters[1].emission_direction).toBe('reverse')
    expect(request.emitters.reduce((sum, emitter) => sum + emitter.power_lumen, 0)).toBeCloseTo(1)
    expect(request.emitters.reduce((sum, emitter) => sum + emitter.ray_count, 0)).toBe(100_000)
    expect(request.receivers).toHaveLength(1)
  })

  it('resizes a blocker from a dragged rectangle on its CAD reference plane', () => {
    const blocker = createLeakPreviewBlockerFromFaces(createSceneFixture(), [0], 1)
    expect(blocker).not.toBeNull()
    const point = (u: number, v: number): [number, number, number] => [
      blocker!.baseCenter[0] + blocker!.uAxis[0] * u + blocker!.vAxis[0] * v,
      blocker!.baseCenter[1] + blocker!.uAxis[1] * u + blocker!.vAxis[1] * v,
      blocker!.baseCenter[2] + blocker!.uAxis[2] * u + blocker!.vAxis[2] * v,
    ]
    const resized = resizeLeakPreviewBlockerOnFace(blocker!, [
      point(-5, -2),
      point(5, -2),
      point(5, 2),
      point(-5, 2),
    ])
    expect(resized).not.toBeNull()
    expect(resized?.widthMm).toBeCloseTo(10)
    expect(resized?.heightMm).toBeCloseTo(4)
  })

  it('aligns a dragged blocker with the rectangle instead of the CAD triangle diagonal', () => {
    const blocker = createLeakPreviewBlockerFromFaces(createSceneFixture(), [0], 1)!
    const diagonal = Math.SQRT1_2
    blocker.uAxis = [diagonal, diagonal, 0]
    blocker.vAxis = [-diagonal, diagonal, 0]
    blocker.normal = [0, 0, 1]
    blocker.baseCenter = [0, 0, 5]
    const resized = resizeLeakPreviewBlockerOnFace(blocker, [
      [2, 3, 5], [12, 3, 5], [12, 7, 5], [2, 7, 5],
    ])
    expect(resized).toMatchObject({
      uAxis: [1, 0, 0], vAxis: [0, 1, 0],
      baseCenter: [7, 5, 5], widthMm: 10, heightMm: 4,
    })
    expect(blocker.uAxis).toEqual([diagonal, diagonal, 0])
  })

  it('aligns a dragged blocker on a tilted CAD plane', () => {
    const blocker = createLeakPreviewBlockerFromFaces(createSceneFixture(), [0], 1)!
    const d = Math.SQRT1_2
    blocker.normal = [0, -d, d]
    blocker.baseCenter = [0, 0, 0]
    const resized = resizeLeakPreviewBlockerOnFace(blocker, [
      [0, 0, 0], [10, 0, 0], [10, 4 * d, 4 * d], [0, 4 * d, 4 * d],
    ])!
    expect(resized.uAxis).toEqual([1, 0, 0])
    expect(resized.vAxis![1]).toBeCloseTo(d)
    expect(resized.vAxis![2]).toBeCloseTo(d)
    expect(resized.widthMm).toBeCloseTo(10)
    expect(resized.heightMm).toBeCloseTo(4)
  })

  it('turns exterior receiver cells into ranked 3D candidates', () => {
    const scene = createSceneFixture()
    const receiver = createLeakPreviewReceivers(scene)[0]
    receiver.resolution = [3, 3]
    const flux = [
      [0, 0, 0],
      [0, 1, 0.7],
      [0, 0, 0],
    ]
    const result = {
      run_id: 'preview-test',
      config: {
        ray_count: 100,
        max_depth: 2,
        seed: 42,
        min_energy: 1e-9,
        epsilon_mm: 0.001,
        k_abs: 1,
        k_brdf: 1,
        angle_dependent_reflectance: false,
        termination_mode: 'russian_roulette',
        contribution_mode: 'summary',
        intersection_backend: 'auto',
        compute_backend: 'cpu',
        store_ray_paths: false,
        max_stored_paths: 0,
      },
      emitters: [],
      receivers: [receiver],
      receiver_grids: [{
        receiver_id: receiver.receiver_id,
        resolution: [3, 3],
        bin_area_mm2: 1,
        flux_lumen: flux,
        hit_count: 2,
      }],
      optical_profiles: [],
      total_rays: 100,
      receiver_hit_count: 2,
      surface_hit_count: 0,
      terminated_ray_count: 98,
      contribution_summary: {
        schema_version: 'rt-contribution.v1',
        direct_receiver_hit_count: 2,
        direct_receiver_flux_lumen: 1.7,
        reflected_receiver_hit_count: 0,
        reflected_receiver_flux_lumen: 0,
        receivers: {},
        components: {},
        faces: {},
        materials: {},
        lobes: {},
        depths: {},
      },
      runtime_sec: 0.1,
      stored_paths: [],
      metrics: {},
    } satisfies RayTraceResult

    const detection = detectLeakPreviewCandidates(scene, result)
    expect(detection.points).toHaveLength(2)
    expect(detection.candidates).toHaveLength(1)
    expect(detection.candidates[0].relativeStrength).toBe(1)
    expect(detection.candidates[0].cellCount).toBe(2)
    expect(detection.candidates[0].clipBox.plane).toBe('xyz')
    expect(detection.candidates[0].widthMm).toBeCloseTo(
      receiver.width_mm * 2 / 3 + leakPreviewRoiOffsetMm * 2,
    )
    const narrowReceiver = { ...receiver, width_mm: 12, height_mm: 12 }
    const narrowDetection = detectLeakPreviewCandidates(scene, {
      ...result,
      receivers: [narrowReceiver],
    })
    expect(narrowDetection.candidates[0].widthMm).toBe(30)
    expect(narrowDetection.candidates[0].heightMm).toBe(30)
    expect(narrowDetection.candidates[0].clipBox.yMax - narrowDetection.candidates[0].clipBox.yMin).toBeCloseTo(30)
    expect((narrowDetection.candidates[0].clipBox.zMax ?? 0) - (narrowDetection.candidates[0].clipBox.zMin ?? 0)).toBeCloseTo(30)
    const precisionReceiver = createCandidateReceiver(detection.candidates[0], 1)
    const roi = detection.candidates[0].clipBox
    expect(precisionReceiver.center[0]).toBeCloseTo(
      detection.candidates[0].center[0] + leakPreviewReceiverDistanceMm,
    )
    expect(precisionReceiver.view_distance_mm).toBe(leakPreviewReceiverDistanceMm)
    expect(precisionReceiver.base_center).toEqual(detection.candidates[0].center)
    expect(precisionReceiver.position_offset_mm).toEqual([
      0,
      precisionReceiver.center[1] - detection.candidates[0].center[1],
      precisionReceiver.center[2] - detection.candidates[0].center[2],
    ])
    expect(precisionReceiver.width_mm).toBeCloseTo(
      roi.yMax - roi.yMin - leakPreviewReceiverOffsetMm * 2,
    )
    expect(precisionReceiver.height_mm).toBeCloseTo(
      (roi.zMax ?? 0) - (roi.zMin ?? 0) - leakPreviewReceiverOffsetMm * 2,
    )
    expect(precisionReceiver.center[1] - precisionReceiver.width_mm / 2).toBeCloseTo(roi.yMin + 2)
    expect(precisionReceiver.center[1] + precisionReceiver.width_mm / 2).toBeCloseTo(roi.yMax - 2)
    expect(precisionReceiver.center[2] - precisionReceiver.height_mm / 2).toBeCloseTo((roi.zMin ?? 0) + 2)
    expect(precisionReceiver.center[2] + precisionReceiver.height_mm / 2).toBeCloseTo((roi.zMax ?? 0) - 2)
    expect(precisionReceiver.center[0]).toBeLessThanOrEqual(roi.xMax)
    expect(precisionReceiver.center[0]).toBeGreaterThanOrEqual(roi.xMin)
    const narrowPrecisionReceiver = createCandidateReceiver(narrowDetection.candidates[0], 2)
    expect(narrowPrecisionReceiver.width_mm).toBeCloseTo(26)
    expect(narrowPrecisionReceiver.height_mm).toBeCloseTo(26)

    const point = detection.points[0].position
    const ignored = detectLeakPreviewCandidates(scene, result, {
      ignoreAreas: [{
        id: 'ignore-1',
        label: 'Allowed Area 01',
        enabled: true,
        regions: [{
          plane: 'yz',
          xMin: -1,
          xMax: 1,
          yMin: point[1] - 100,
          yMax: point[1] + 100,
          zMin: point[2] - 100,
          zMax: point[2] + 100,
        }],
      }],
    })
    expect(ignored.candidates).toHaveLength(0)
  })

  it('keeps a weak grazing Front path and plots it on the Front CAD envelope', () => {
    const scene = createSceneFixture()
    const receiver = createLeakPreviewReceivers(scene, [], ['pos_z'])[0]
    receiver.resolution = [5, 5]
    const hitPoint = [
      receiver.center[0] - receiver.width_mm * 0.4,
      receiver.center[1] - receiver.height_mm * 0.4,
      receiver.center[2],
    ] as [number, number, number]
    const previousPoint = [
      hitPoint[0],
      hitPoint[1] + Math.sqrt(3) * 2,
      hitPoint[2] - 2,
    ] as [number, number, number]
    const hit = (overrides: Partial<RayTraceResult['stored_paths'][number][number]>) => ({
      face_index: -1,
      component_id: null,
      material_id: null,
      point: [0, 0, 0] as [number, number, number],
      normal: [0, 0, 1] as [number, number, number],
      distance_mm: 1,
      incoming_energy_lumen: 0.001,
      outgoing_energy_lumen: 0.001,
      depth: 3,
      event_type: 'surface',
      receiver_id: null,
      optical_profile_id: null,
      reflectance: null,
      scatter_model: null,
      optical_assignment_source: null,
      ray_kind: null,
      ...overrides,
    })
    const flux = Array.from({ length: 5 }, () => Array(5).fill(0)) as number[][]
    flux[0][0] = 0.001
    flux[4][4] = 1
    const baseResult = {
      run_id: 'preview-grazing-test',
      config: {
        ray_count: 100,
        max_depth: 20,
        seed: 42,
        min_energy: 1e-9,
        epsilon_mm: 0.001,
        k_abs: 1,
        k_brdf: 1,
        angle_dependent_reflectance: false,
        termination_mode: 'russian_roulette',
        contribution_mode: 'summary',
        intersection_backend: 'auto',
        compute_backend: 'cpu',
        store_ray_paths: true,
        max_stored_paths: 4_000,
      },
      emitters: [],
      receivers: [receiver],
      receiver_grids: [{
        receiver_id: receiver.receiver_id,
        resolution: [5, 5] as [number, number],
        bin_area_mm2: 1,
        flux_lumen: flux,
        hit_count: 2,
      }],
      optical_profiles: [],
      total_rays: 100,
      receiver_hit_count: 2,
      surface_hit_count: 1,
      terminated_ray_count: 98,
      contribution_summary: {
        schema_version: 'rt-contribution.v1' as const,
        direct_receiver_hit_count: 1,
        direct_receiver_flux_lumen: 1,
        reflected_receiver_hit_count: 1,
        reflected_receiver_flux_lumen: 0.001,
        receivers: {}, components: {}, faces: {}, materials: {}, lobes: {}, depths: {},
      },
      runtime_sec: 0.1,
      stored_paths: [[
        hit({ point: previousPoint }),
        hit({
          point: hitPoint,
          event_type: 'receiver',
          receiver_id: receiver.receiver_id,
          receiver_flux_lumen: 0.001,
        }),
      ]],
      metrics: {},
    } satisfies RayTraceResult

    const detection = detectLeakPreviewCandidates(scene, baseResult, { quality: 'deep' })
    const grazingCandidate = detection.candidates.find((candidate) => candidate.grazingPathCount > 0)
    expect(grazingCandidate).toBeDefined()
    expect(grazingCandidate?.sampledPathCount).toBe(1)
    expect(grazingCandidate?.meanExitAngleDeg).toBeCloseTo(60)
    expect(grazingCandidate?.center[2]).toBeCloseTo(20)
  })

  it('finds ROI faces from authoritative mesh ownership when Component face lists are truncated', () => {
    const scene = createSceneFixture()
    scene.components[1].face_indices = []
    scene.components[1].is_truncated = true

    const faceIds = resolveLeakPreviewRoiFaces(
      scene,
      {
        plane: 'xyz',
        xMin: 4,
        xMax: 56,
        yMin: 4,
        yMax: 56,
        zMin: 9,
        zMax: 21,
      },
      [],
      [],
    )

    expect(faceIds).toEqual(expect.arrayContaining([3, 4]))
  })

  it('places every generated Receiver 3 mm outside its selected enclosure side', () => {
    const receivers = createLeakPreviewReceivers(createSceneFixture())
    for (const [index, receiver] of receivers.entries()) {
      const candidate = {
        id: `candidate-${index}`,
        label: receiver.display_name,
        receiverId: receiver.receiver_id,
        center: [10, 20, 30] as [number, number, number],
        normal: receiver.normal,
        uAxis: receiver.u_axis!,
        vAxis: receiver.v_axis!,
        widthMm: 10,
        heightMm: 8,
        fluxLumen: 1,
        peakFluxLumen: 1,
        relativeStrength: 1,
        cellCount: 1,
        sampledPathCount: 0,
        grazingPathCount: 0,
        minReflectionCount: null,
        maxReflectionCount: null,
        meanExitAngleDeg: null,
        clipBox: { plane: 'xyz' as const, xMin: 0, xMax: 1, yMin: 0, yMax: 1, zMin: 0, zMax: 1 },
      }
      const generated = createCandidateReceiver(candidate, index + 1)
      const displacement = generated.center.map((value, axis) =>
        value - candidate.center[axis],
      ) as [number, number, number]
      const outward = receiver.normal.map((value) => -value)
      const outwardDistance = displacement.reduce((sum, value, axis) =>
        sum + value * outward[axis], 0)
      expect(outwardDistance).toBeCloseTo(3)
      expect(generated.normal.reduce((sum, value, axis) =>
        sum + value * outward[axis], 0)).toBeCloseTo(-1)
    }
  })

  it('builds the six detection planes from transformed Component bounds', () => {
    const scene = createSceneFixture()
    const transformed = createLeakPreviewReceivers(scene, [{
      ruleId: 'move-component-1',
      componentId: 1,
      targetType: 'component',
      selectionMethod: 'click',
      faceIds: [],
      move: { x: 100, y: 0, z: 0 },
      tilt: { x: 0, y: 0, z: 0 },
      enabled: true,
    }])

    expect(transformed[0].display_name).toBe('Preview Right')
    expect(transformed[0].center[0]).toBeCloseTo(162)
    expect(transformed[1].display_name).toBe('Preview Left')
  })
})

it('extends a detector ROI to contain face, body and distant virtual emission surfaces', () => {
  const scene = createSceneFixture()
  const result = createRayTraceResultFixture()
  result.receivers = [createEscapeEnclosureReceivers(scene)[0]]
  result.receiver_grids = [{ receiver_id: result.receivers[0].receiver_id, resolution: [1, 1], bin_area_mm2: 1, flux_lumen: [[1]], hit_count: 1 }]
  const candidate = detectLeakPreviewCandidates(scene, result).candidates[0]
  const original = structuredClone(candidate.clipBox)
  const face = createFaceEmitter('face', [0])
  const body = { ...createFaceEmitter('body', []), source_component_ids: [2] }
  const plane = { ...createDatumEmitter('plane', [-100, -100, -100], [0, 0, 0]), width_mm: 20, height_mm: 10 }
  const clip = candidateRoiWithSources(scene, candidate, [face, body, plane])
  expect(clip.xMin).toBeLessThanOrEqual(-110)
  expect(clip.yMin).toBeLessThanOrEqual(-105)
  expect(clip.zMin).toBeLessThan(-100)
  expect(clip.zMax).toBeGreaterThan(20)
  expect(clip.xMax).toBeGreaterThan(60)
  expect(candidate.clipBox).toEqual(original)
})

it('creates Preview ROI with unlimited Z even without result emitter metadata', () => {
  const scene = createSceneFixture()
  const result = createRayTraceResultFixture()
  result.receivers = [createEscapeEnclosureReceivers(scene)[0]]
  result.receiver_grids = [{ receiver_id: result.receivers[0].receiver_id, resolution: [1, 1], bin_area_mm2: 1, flux_lumen: [[1]], hit_count: 1 }]
  const candidate = detectLeakPreviewCandidates(scene, result).candidates[0]
  candidate.clipBox = { plane: 'xyz', xMin: 0, xMax: 60, yMin: 0, yMax: 60, zMin: 0, zMax: 1 }
  const clip = candidateRoiWithSources(scene, candidate, [])
  expect(clip).toMatchObject({ plane: 'xy', zUnbounded: true, xMin: 0, xMax: 60, yMin: 0, yMax: 60 })
  expect(clip.zMax).toBeGreaterThan(20)
  // Even stale display depths must not exclude deep CAD faces.
  expect(resolveLeakPreviewRoiFaces(scene, { ...clip, zMin: 0, zMax: 1 }, [], [])).toEqual([0, 1, 2, 3, 4])
})
