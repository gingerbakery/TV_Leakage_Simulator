import { describe, expect, it } from 'vitest'

import type { RayTraceResult } from '@/api'
import { createSceneFixture } from '@/test/scene-fixture'

import {
  buildLeakPreviewRequest,
  createCandidateReceiver,
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
    const precisionReceiver = createCandidateReceiver(detection.candidates[0], 1)
    expect(precisionReceiver.center[0]).toBeCloseTo(
      detection.candidates[0].center[0] + leakPreviewReceiverDistanceMm,
    )
    expect(precisionReceiver.view_distance_mm).toBe(leakPreviewReceiverDistanceMm)
    expect(precisionReceiver.base_center).toEqual(detection.candidates[0].center)
    expect(precisionReceiver.width_mm).toBeCloseTo(
      detection.candidates[0].widthMm + leakPreviewReceiverOffsetMm * 2,
    )
    expect(precisionReceiver.height_mm).toBeCloseTo(
      detection.candidates[0].heightMm + leakPreviewReceiverOffsetMm * 2,
    )

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
