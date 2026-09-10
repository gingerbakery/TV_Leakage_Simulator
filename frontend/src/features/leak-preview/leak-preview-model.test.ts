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
  leakPreviewRoiOffsetMm,
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
    expect(request.config.store_ray_paths).toBe(false)
    expect(request.config.receiver_importance_fraction).toBe(0.5)
    expect(request.config.bounce_receiver_importance_fraction).toBe(0.5)
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
    expect(request.config.max_depth).toBe(8)
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

    const point = detection.points[0].position
    const ignored = detectLeakPreviewCandidates(scene, result, {
      ignoreAreas: [{
        id: 'ignore-1',
        label: 'Ignore Area 01',
        enabled: true,
        clipBox: {
          plane: 'yz',
          xMin: -1,
          xMax: 1,
          yMin: point[1] - 100,
          yMax: point[1] + 100,
          zMin: point[2] - 100,
          zMax: point[2] + 100,
        },
      }],
    })
    expect(ignored.candidates).toHaveLength(0)
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
