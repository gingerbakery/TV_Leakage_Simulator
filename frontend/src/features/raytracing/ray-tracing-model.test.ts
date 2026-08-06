import { describe, expect, it } from 'vitest'

import { defaultRayTraceConfig } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'

import {
  axesFromNormal,
  buildRayTraceRequest,
  createCurrentViewReceiver,
  createDatumEmitter,
  createDatumReceiver,
  createFaceEmitter,
  nextSpecId,
  planeAxesFromRotation,
  rotationFromPlaneAxes,
} from './ray-tracing-model'

describe('ray tracing model', () => {
  it('builds stable emitter and current-view receiver contracts', () => {
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
    })
  })

  it('applies Current View Receiver position and tilt adjustments', () => {
    const receiver = createCurrentViewReceiver(
      'receiver_001',
      {
        target: [10, 20, 30],
        normal: [0, 0, -1],
        uAxis: [1, 0, 0],
        vAxis: [0, -1, 0],
      },
      50,
      [4, -2, 3],
      [0, 0, 90],
    )

    expect(receiver).toMatchObject({
      base_center: [10, 20, 80],
      center: [14, 18, 83],
      position_offset_mm: [4, -2, 3],
      tilt_xyz_deg: [0, 0, 90],
    })
    expect(receiver.u_axis?.[0]).toBeCloseTo(0)
    expect(receiver.u_axis?.[1]).toBeCloseTo(1)
    expect(receiver.v_axis?.[0]).toBeCloseTo(1)
    expect(receiver.v_axis?.[1]).toBeCloseTo(0)
    expect(receiver.normal).toEqual([0, 0, -1])
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
    expect(Math.abs(uAxis[0] * vAxis[0] + uAxis[1] * vAxis[1] + uAxis[2] * vAxis[2])).toBeCloseTo(0)
    expect(Math.hypot(...uAxis)).toBeCloseTo(1)
    expect(Math.hypot(...vAxis)).toBeCloseTo(1)
  })

  it('includes active ROI, optical assignments, transforms and exclusions', () => {
    const scene = createSceneFixture()
    const emitter = createFaceEmitter('emitter_001', [0])
    emitter.ray_count = 2_000
    const receiver = createCurrentViewReceiver(
      'receiver_001',
      {
        target: [0, 0, 0],
        normal: [0, 0, -1],
        uAxis: [1, 0, 0],
        vAxis: [0, -1, 0],
      },
      10,
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
          baseMaterialId: 'black_pc_resin',
          surfaceId: 'matte_black_resin',
          profileId: '',
          bsdfAssetId: '',
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
      excludedComponentIds: [8],
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
    expect(request.excluded_component_ids).toEqual([8, 9])
    expect(request.config.ray_count).toBe(12_000)
    expect(request.optical_profiles).toHaveLength(1)
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
