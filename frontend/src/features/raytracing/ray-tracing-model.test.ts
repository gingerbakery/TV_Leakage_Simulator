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
  metricErrorPercent,
  nextSpecId,
  planeAxesFromRotation,
  rayObjectDisplayName,
  receiverMeetsStatisticalTarget,
  rotationFromPlaneAxes,
} from './ray-tracing-model'

describe('ray tracing model', () => {
  it('weights termination losses across unequal segments and preserves unavailable diagnostics', () => {
    const first = createRayTraceResultFixture()
    const second = createRayTraceResultFixture()
    first.total_rays = 100
    second.total_rays = 300
    first.metrics._termination_summary = {
      unpropagated_surface_flux_lumen: 0.4, energy_cutoff_upper_bound_lumen: 0.5, depth_limit_count: 2,
    }
    second.metrics._termination_summary = {
      unpropagated_surface_flux_lumen: 0.2, energy_cutoff_upper_bound_lumen: 0.3, depth_limit_count: 5,
    }
    expect(mergeConvergenceRayTraceResults(first, second).metrics._termination_summary).toMatchObject({
      unpropagated_surface_flux_lumen: 0.25, energy_cutoff_upper_bound_lumen: 0.35, depth_limit_count: 7,
    })
    first.metrics._termination_summary = { unpropagated_surface_flux_lumen: null }
    expect(mergeConvergenceRayTraceResults(first, second).metrics._termination_summary).toMatchObject({
      unpropagated_surface_flux_lumen: null,
    })
    delete first.metrics._termination_summary
    expect(mergeConvergenceRayTraceResults(first, second).metrics._termination_summary).toBeUndefined()
    first.config.min_energy_basis = 'initial_ray_fraction'
    expect(() => mergeConvergenceRayTraceResults(first, second)).toThrow(/Termination policy/)
  })

  it.each([0, -0.001, NaN, Infinity])('rejects an invalid bright-area moment (%s) even when the Peak cell is valid', (missingMoment) => {
    const first = createRayTraceResultFixture()
    first.total_rays = 10_000
    first.receiver_grids[0] = {
      receiver_id: 'receiver_001', resolution: [2, 1], bin_area_mm2: 1,
      flux_lumen: [[1, 0.5]], hit_count: 1500,
      flux_squared_lumen2: 0.0015, flux_squared_lumen2_grid: [[0.001, missingMoment]],
    }
    const second = structuredClone(first)
    second.receiver_grids[0].flux_squared_lumen2_grid = [[0.001, 0.0005]]
    const merged = mergeConvergenceRayTraceResults(first, second)
    const metric = merged.metrics.receiver_001 as Record<string, unknown>
    expect(metric.total_flux_lumen).toBe(1.5)
    expect(metric.peak_error_estimate_percent).toBeLessThan(5)
    expect(metric.peak_area_error_estimate_percent).toBeNull()
    expect(receiverMeetsStatisticalTarget(metric, 5)).toBe(false)
    expect(receiverMeetsStatisticalTarget(metric, 100)).toBe(false)
  })

  it('rejects a noisy Peak even when total and bright-area errors are small', () => {
    const metric = {
      hit_count: 1000, peak_effective_sample_count: 100,
      error_estimate_percent: 0.5, peak_area_error_estimate_percent: 0.8,
      peak_error_estimate_percent: 10,
    }
    expect(receiverMeetsStatisticalTarget(metric, 5)).toBe(false)
    expect(receiverMeetsStatisticalTarget({ ...metric, peak_error_estimate_percent: 4 }, 5)).toBe(true)
    for (const value of [undefined, null, '', NaN, Infinity, -1]) {
      expect(metricErrorPercent(value)).toBe(Infinity)
      expect(receiverMeetsStatisticalTarget({ ...metric, peak_error_estimate_percent: value }, 5)).toBe(false)
    }
  })

  it('requires two stable cumulative Peak changes for automatic convergence', () => {
    const metric = {
      hit_count: 1000, peak_effective_sample_count: 100,
      error_estimate_percent: 1, peak_area_error_estimate_percent: 1,
      peak_error_estimate_percent: 4,
    }
    for (const changes of [[], [1], [8, 1], [null, 1]]) {
      expect(receiverMeetsStatisticalTarget({ ...metric, peak_recent_change_percent: changes }, 5, true)).toBe(false)
    }
    expect(receiverMeetsStatisticalTarget({ ...metric, peak_recent_change_percent: [2, 1] }, 5, true)).toBe(true)
    expect(receiverMeetsStatisticalTarget({ ...metric, peak_effective_sample_count: 2 }, 5)).toBe(false)
  })

  it('preserves unavailable legacy moments through convergence merging', () => {
    const first = createRayTraceResultFixture()
    first.receiver_grids[0].flux_squared_lumen2_grid = undefined
    first.receiver_grids[0].flux_squared_lumen2 = undefined
    const merged = mergeConvergenceRayTraceResults(first, createRayTraceResultFixture())
    const metric = merged.metrics.receiver_001 as Record<string, unknown>
    expect(metric.peak_error_estimate_percent).toBeNull()
    expect(receiverMeetsStatisticalTarget(metric, 5)).toBe(false)
  })

  it('keeps the raw Peak and tracks its changes without smoothing', () => {
    const first = createRayTraceResultFixture()
    const second = structuredClone(first)
    const merged = mergeConvergenceRayTraceResults(first, second)
    const next = mergeConvergenceRayTraceResults(merged, structuredClone(first))
    const metric = next.metrics.receiver_001 as Record<string, unknown>
    const grid = next.receiver_grids[0]
    const rawPeak = Math.max(...grid.flux_lumen.flat()) * next.config.k_abs * next.config.k_brdf /
      (grid.bin_area_mm2 * 1e-6) / Math.PI
    expect(metric.peak_nit_est).toBeCloseTo(rawPeak)
    expect(metric.peak_recent_change_percent).toHaveLength(2)
  })

  it.each([[20, 30, -15], [-45, 10, 90], [0, 90, 30], [0, -90, -30]])(
    'recovers Target rotation from its two persisted axes: %s, %s, %s',
    (rotationX, rotationY, rotationZ) => {
      const axes = planeAxesFromRotation([rotationX, rotationY, rotationZ])
      const restored = rotationFromPlaneAxes(axes.uAxis, axes.vAxis, null)
      const recovered = planeAxesFromRotation(restored)
      for (const axis of ['uAxis', 'vAxis', 'normal'] as const) {
        recovered[axis].forEach((value, index) => expect(value).toBeCloseTo(axes[axis][index], 10))
      }
    },
  )

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
          clipBox: {
            plane: 'xyz',
            xMin: 0.25,
            xMax: 0.75,
            yMin: 0.1,
            yMax: 0.9,
            zMin: -0.5,
            zMax: 0.5,
          },
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
    expect(request.roi_clip_boxes).toEqual([{
      x_min: 0.25,
      x_max: 0.75,
      y_min: 0.1,
      y_max: 0.9,
      z_min: -0.5,
      z_max: 0.5,
    }])
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
