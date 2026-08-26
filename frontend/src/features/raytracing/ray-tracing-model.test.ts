import { describe, expect, it } from 'vitest'

import { defaultRayTraceConfig } from '@/stores'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'

import {
  axesFromNormal,
  buildRayTraceRequest,
  convergenceSegmentSeed,
  createCurrentViewReceiver,
  createDatumEmitter,
  createDatumReceiver,
  createFaceEmitter,
  mergeConvergenceRayTraceResults,
  nextSpecId,
  planeAxesFromRotation,
  rayObjectDisplayName,
  rotationFromPlaneAxes,
} from './ray-tracing-model'

describe('ray tracing model', () => {
  it('derives deterministic independent seeds for convergence segments', () => {
    expect(convergenceSegmentSeed(42, 0)).toBe(42)
    expect(convergenceSegmentSeed(42, 1)).toBe(1_000_045)
    expect(convergenceSegmentSeed(42, 2)).toBe(2_000_048)
  })

  it('reuses convergence segments with weighted flux and squared sums', () => {
    const first = createRayTraceResultFixture()
    first.run_id = 'segment-1'
    first.total_rays = 100
    first.runtime_sec = 1
    first.config.ray_count = 100
    first.config.seed = convergenceSegmentSeed(42, 0)
    first.emitters[0].ray_count = 100
    first.receiver_grids[0] = {
      receiver_id: 'receiver_001',
      resolution: [1, 1],
      bin_area_mm2: 1,
      flux_lumen: [[2]],
      hit_count: 10,
      flux_squared_lumen2: 0.04,
      flux_squared_lumen2_grid: [[0.04]],
    }
    first.contribution_summary.direct_receiver_hit_count = 10
    first.contribution_summary.direct_receiver_flux_lumen = 2

    const second = structuredClone(first)
    second.run_id = 'segment-2'
    second.runtime_sec = 2
    second.config.seed = convergenceSegmentSeed(42, 1)
    second.receiver_grids[0].flux_lumen = [[4]]
    second.receiver_grids[0].hit_count = 20
    second.receiver_grids[0].flux_squared_lumen2 = 0.16
    second.receiver_grids[0].flux_squared_lumen2_grid = [[0.16]]
    second.receiver_hit_count = 20
    second.contribution_summary.direct_receiver_hit_count = 20
    second.contribution_summary.direct_receiver_flux_lumen = 4

    const merged = mergeConvergenceRayTraceResults(first, second)

    expect(merged.run_id).toBe('segment-2')
    expect(merged.total_rays).toBe(200)
    expect(merged.runtime_sec).toBe(3)
    expect(merged.config.seed).toBe(42)
    expect(merged.emitters[0].ray_count).toBe(200)
    expect(merged.receiver_grids[0].flux_lumen[0][0]).toBeCloseTo(3)
    expect(merged.receiver_grids[0].flux_squared_lumen2).toBeCloseTo(0.05)
    expect(merged.receiver_grids[0].hit_count).toBe(30)
    expect(merged.contribution_summary.direct_receiver_hit_count).toBe(30)
    expect(merged.contribution_summary.direct_receiver_flux_lumen).toBeCloseTo(3)
    expect(merged.metrics.receiver_001).toMatchObject({
      total_flux_lumen: 3,
      hit_count: 30,
      error_estimate_sample_count: 200,
    })
    expect(merged.metrics._convergence_accumulation).toMatchObject({
      contract: 'independent_segment_weighted_v1',
      segment_count: 2,
      segment_rays: [100, 100],
      segment_seeds: [42, 1_000_045],
      total_rays: 200,
      avoided_retrace_rays: 100,
    })
  })

  it('processes only eight base samples for a 1-2-4-8 convergence schedule', () => {
    const segmentRays = [100, 100, 200, 400]
    let accumulated: ReturnType<typeof mergeConvergenceRayTraceResults> | null = null
    for (const [index, rays] of segmentRays.entries()) {
      const segment = createRayTraceResultFixture()
      segment.run_id = `segment-${index + 1}`
      segment.total_rays = rays
      segment.config.ray_count = rays
      segment.config.seed = convergenceSegmentSeed(42, index)
      segment.emitters[0].ray_count = rays
      accumulated = mergeConvergenceRayTraceResults(accumulated, segment)
    }

    expect(accumulated?.total_rays).toBe(800)
    expect(accumulated?.metrics._convergence_accumulation).toMatchObject({
      segment_rays: segmentRays,
      total_rays: 800,
      avoided_retrace_rays: 700,
    })
  })

  it('formats internal ray object IDs without changing custom names', () => {
    expect(rayObjectDisplayName('receiver', 'receiver_001')).toBe(
      'Receiver 1',
    )
    expect(
      rayObjectDisplayName('receiver', 'receiver_002', 'receiver_002'),
    ).toBe('Receiver 2')
    expect(
      rayObjectDisplayName('receiver', 'receiver_002', 'Right corner'),
    ).toBe('Right corner')
    expect(rayObjectDisplayName('emitter', 'emitter_003')).toBe(
      'Emitter 3',
    )
  })

  it('builds stable emitter and datum Receiver contracts', () => {
    expect(nextSpecId('emitter', ['emitter_001', 'emitter_004'])).toBe(
      'emitter_005',
    )
    const axes = planeAxesFromRotation([90, 0, 0])
    expect(axes.normal[1]).toBeCloseTo(-1)
    expect(axes.vAxis[2]).toBeCloseTo(1)
    const rotation = rotationFromPlaneAxes(
      axes.uAxis,
      axes.vAxis,
      axes.normal,
    )
    expect(rotation[0]).toBeCloseTo(90)
    expect(rotation[1]).toBeCloseTo(0)
    expect(rotation[2]).toBeCloseTo(0)
    const compoundAxes = planeAxesFromRotation([20, -30, 45])
    const compoundRotation = rotationFromPlaneAxes(
      compoundAxes.uAxis,
      compoundAxes.vAxis,
      compoundAxes.normal,
    )
    expect(compoundRotation[0]).toBeCloseTo(20)
    expect(compoundRotation[1]).toBeCloseTo(-30)
    expect(compoundRotation[2]).toBeCloseTo(45)

    const receiver = createDatumReceiver(
      'receiver_001',
      [10, 20, 80],
      [0, 0, 0],
    )
    expect(receiver).toMatchObject({
      placement_mode: 'datum_plane',
      center: [10, 20, 80],
      view_distance_mm: null,
    })
  })

  it('creates a Current View Receiver from the captured camera frame', () => {
    const receiver = createCurrentViewReceiver(
      'receiver_001',
      {
        target: [10, 20, 30],
        normal: [0, 0, -1],
        uAxis: [1, 0, 0],
        vAxis: [0, -1, 0],
      },
      50,
    )
    expect(receiver).toMatchObject({
      placement_mode: 'current_view',
      center: [10, 20, 80],
      view_distance_mm: 50,
      u_axis: [1, 0, 0],
      v_axis: [0, 1, 0],
      normal_flip: true,
    })
  })

  it('offsets a datum plane receiver from its base center without a pivot', () => {
    const receiver = createDatumReceiver(
      'receiver_001',
      [100, 0, 0],
      [0, 0, 90],
      [0, 5, 0],
    )

    expect(receiver).toMatchObject({
      base_center: [100, 0, 0],
      // No custom pivot - tilt reorients the plane in place, the 90deg Z
      // rotation must not move the center away from base + offset.
      center: [100, 5, 0],
      position_offset_mm: [0, 5, 0],
      tilt_xyz_deg: [0, 0, 90],
      pivot: null,
    })
    // A Z-axis rotation leaves the canonical Z-facing normal unchanged;
    // it's the in-plane u/v axes that visibly rotate.
    expect(receiver.normal).toEqual([0, 0, 1])
    expect(receiver.u_axis?.[0]).toBeCloseTo(0)
    expect(receiver.u_axis?.[1]).toBeCloseTo(1)
  })

  it('revolves a datum plane receiver around a custom tilt pivot', () => {
    const receiver = createDatumReceiver(
      'receiver_001',
      [100, 0, 0],
      [0, 0, 90],
      [0, 0, 0],
      [0, 0, 0],
    )

    // Same 90deg Z rotation as the no-pivot case above, but pivoting
    // around the world origin instead of the receiver's own position -
    // (100,0,0) must swing to (0,100,0), not stay put.
    expect(receiver.center[0]).toBeCloseTo(0)
    expect(receiver.center[1]).toBeCloseTo(100)
    expect(receiver.center[2]).toBeCloseTo(0)
    expect(receiver.pivot).toEqual([0, 0, 0])
  })

  it('derives a stable in-plane basis from a picked face normal', () => {
    const { uAxis, vAxis } = axesFromNormal([0, 0, 1])
    expect(uAxis).toEqual([1, 0, 0])
    expect(vAxis).toEqual([0, 1, 0])
    expect(Math.abs(uAxis[0] * vAxis[0] + uAxis[1] * vAxis[1] + uAxis[2] * vAxis[2])).toBeCloseTo(0)
    expect(Math.hypot(...uAxis)).toBeCloseTo(1)
    expect(Math.hypot(...vAxis)).toBeCloseTo(1)
  })

  it('includes active ROI, optical assignments, transforms and exclusions', () => {
    const scene = createSceneFixture()
    const emitter = createFaceEmitter('emitter_001', [0])
    emitter.ray_count = 2_000
    const receiver = createDatumReceiver(
      'receiver_001',
      [0, 0, 10],
      [0, 0, 0],
    )

    const request = buildRayTraceRequest({
      scene,
      projectName: 'fixture',
      emitters: [emitter, createDatumEmitter('emitter_002', [0, 0, 0], [0, 0, 0])],
      receivers: [receiver],
      materialAssignments: [
        {
          assignmentId: 'material-part-1',
          componentId: 1,
          targetType: 'part',
          faceIds: [],
          baseMaterialId: 'pc_black',
          surfaceId: 'matte_black_resin',
          profileId: '',
          bsdfAssetId: '',
          opticalOverride: {
            reflectance: 0.2,
            loss: 0.8,
            specularRatio: 0.35,
            diffuseRatio: 0.65,
          },
          enabled: true,
        },
      ],
      transformRules: [
        {
          ruleId: 'move-1',
          componentId: 1,
          targetType: 'component',
          selectionMethod: 'click',
          faceIds: [],
          move: { x: 1.5, y: 0, z: 0 },
          tilt: { x: 0, y: 0, z: 5 },
          enabled: true,
        },
      ],
      excludedComponentIds: [1, 8],
      deletedComponentIds: [9],
      roiScopes: [
        {
          id: 'roi-1',
          scopeId: 'ROI-1',
          source: 'box',
          view: 'front_xy',
          active: true,
          components: [
            {
              componentId: 1,
              componentName: 'Part',
              faceIds: [0, 1],
              areaMm2: 1,
              bboxMin: { x: 0, y: 0, z: 0 },
              bboxMax: { x: 1, y: 1, z: 1 },
            },
          ],
        },
      ],
      config: defaultRayTraceConfig,
    })

    expect(request.roi_faces).toEqual([0, 1])
    expect(request.excluded_component_ids).toEqual([1, 8, 9])
    expect(request.config.ray_count).toBe(12_000)
    expect(request.config).not.toHaveProperty('auto_convergence')
    expect(request.config).not.toHaveProperty('convergence_target_percent')
    expect(request.config).not.toHaveProperty('max_convergence_multiplier')
    expect(request.config.primary_sampling_strategy).toBe('source')
    expect(request.config.receiver_importance_fraction).toBe(0.5)
    expect(request.optical_profiles).toHaveLength(1)
    expect(request.optical_profiles[0]).toMatchObject({
      reflectance: 0.2,
      absorption: 0.8,
      specular_ratio: 0.35,
      diffuse_ratio: 0.65,
    })
    expect(request.optical_assignments[0]).toMatchObject({
      component_id: 1,
      target_type: 'part',
    })
    expect(request.emitters).toHaveLength(2)
    expect(request.receivers).toHaveLength(1)
    expect(request.transform_rules).toEqual([
      expect.objectContaining({
        target_type: 'component',
        object_id: 1,
        move: { x: 1.5, y: 0, z: 0 },
      }),
    ])
  })
})
