import { describe, expect, it } from 'vitest'

import { defaultRayTraceConfig } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'

import {
  buildRayTraceRequest,
  createCurrentViewReceiver,
  createDatumEmitter,
  createFaceEmitter,
  nextSpecId,
  planeAxesFromRotation,
} from './ray-tracing-model'

describe('ray tracing model', () => {
  it('builds stable emitter and current-view receiver contracts', () => {
    expect(nextSpecId('emitter', ['emitter_001', 'emitter_004'])).toBe(
      'emitter_005',
    )
    const axes = planeAxesFromRotation([90, 0, 0])
    expect(axes.normal[1]).toBeCloseTo(-1)
    expect(axes.vAxis[2]).toBeCloseTo(1)

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
          move: { x: 1, y: 0, z: 0 },
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
    expect(request.transform_rules).toHaveLength(1)
  })
})
