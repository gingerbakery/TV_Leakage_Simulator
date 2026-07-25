import { describe, expect, it } from 'vitest'
import type { RayHit, Vec3 } from '@/api'
import {
  defaultRayPathDisplayFilters,
  type RayPathDisplayFilters,
} from '@/stores'

import {
  buildRayPathVisualization,
  receiverPathFilter,
} from './ray-paths'

function hit(
  point: Vec3,
  eventType: string,
  rayKind: string | null = null,
): RayHit {
  return {
    face_index: -1,
    component_id: null,
    material_id: null,
    point,
    normal: [0, 0, 1],
    distance_mm: 1,
    incoming_energy_lumen: 1,
    outgoing_energy_lumen: 1,
    depth: 0,
    event_type: eventType,
    receiver_id: eventType === 'receiver' ? 'receiver_001' : null,
    optical_profile_id: null,
    reflectance: null,
    scatter_model: null,
    optical_assignment_source: null,
    ray_kind: rayKind,
  }
}

const directReceiverPath = [
  hit([0, 0, 0], 'emitter', 'direct'),
  hit([0, 0, 10], 'receiver', 'direct'),
]
const reflectedReceiverPath = [
  hit([0, 0, 0], 'emitter', 'direct'),
  hit([0, 0, 5], 'surface', 'direct'),
  hit([4, 0, 10], 'receiver', 'specular'),
]
const reflectedMissPath = [
  hit([0, 0, 0], 'emitter', 'direct'),
  hit([0, 0, 5], 'surface', 'direct'),
  hit([6, 0, 9], 'escaped', 'gaussian'),
]

describe('ray path visualization', () => {
  it('separates direct and reflected Receiver paths', () => {
    expect(receiverPathFilter(directReceiverPath)).toBe(
      'receiver_direct',
    )
    expect(receiverPathFilter(reflectedReceiverPath)).toBe(
      'receiver_reflected',
    )
  })

  it('builds independently filterable colored segment groups', () => {
    const visualization = buildRayPathVisualization(
      [directReceiverPath, reflectedReceiverPath, reflectedMissPath],
      defaultRayPathDisplayFilters,
    )

    expect(visualization).toMatchObject({
      totalPathCount: 3,
      visiblePathCount: 3,
    })
    expect(visualization.groups.receiver_direct).toHaveLength(1)
    expect(visualization.groups.receiver_reflected).toHaveLength(2)
    expect(visualization.groups.direct).toHaveLength(1)
    expect(visualization.groups.gaussian).toHaveLength(1)
  })

  it('applies the Receiver-only preset without recomputation', () => {
    const receiverOnly: RayPathDisplayFilters = {
      receiver_direct: true,
      receiver_reflected: true,
      direct: false,
      specular: false,
      lambertian: false,
      gaussian: false,
    }
    const visualization = buildRayPathVisualization(
      [directReceiverPath, reflectedReceiverPath, reflectedMissPath],
      receiverOnly,
    )

    expect(visualization.visiblePathCount).toBe(2)
    expect(visualization.groups.direct).toHaveLength(0)
    expect(visualization.groups.gaussian).toHaveLength(0)
  })
})
