import { describe, expect, it } from 'vitest'

import type { Vec3 } from '@/api'

import type {
  LeakageAabbFace,
  LeakagePreviewSample,
} from './leakage-preview-data'
import {
  buildLeakagePreviewConnectivity,
  buildPrototypeLeakageFieldSelection,
  type LeakageConnectivityOptions,
} from './leakage-preview-connectivity'

const options: LeakageConnectivityOptions = {
  maxNeighborDistance: 1.01,
  minDirectionDot: 0.8,
  commonWeightScale: 4,
}

function sample(
  pathIndex: number,
  exitPoint: Vec3,
  outgoingDirection: Vec3 = [0, 0, 1],
  weight = 1,
  exitFaces: LeakageAabbFace[] = ['z_max'],
): LeakagePreviewSample {
  return {
    runId: 'run-connectivity',
    pathIndex,
    receiverId: 'receiver-main',
    exitPoint,
    exitFaces,
    outgoingDirection,
    weight,
  }
}

function edgeKeys(result: ReturnType<typeof buildLeakagePreviewConnectivity>) {
  return result.edges.map((edge) => `${edge.from}->${edge.to}`)
}

describe('leakage preview connectivity', () => {
  it('connects a straight run through local neighbors only', () => {
    const result = buildLeakagePreviewConnectivity(
      [
        sample(0, [1, 1, 10]),
        sample(1, [2, 1, 10]),
        sample(2, [3, 1, 10]),
      ],
      options,
    )

    expect(result.nodes.map((node) => node.face)).toEqual([
      'z_max',
      'z_max',
      'z_max',
    ])
    expect(edgeKeys(result)).toEqual([
      'run-connectivity:0->run-connectivity:1',
      'run-connectivity:1->run-connectivity:2',
    ])
    expect(result.components).toHaveLength(1)
  })

  it('prunes a long straight edge when an intermediate sample is closer', () => {
    const result = buildLeakagePreviewConnectivity(
      [
        sample(0, [1, 1, 10]),
        sample(1, [2, 1, 10]),
        sample(2, [3, 1, 10]),
      ],
      { ...options, maxNeighborDistance: 2.01 },
    )

    expect(edgeKeys(result)).toEqual([
      'run-connectivity:0->run-connectivity:1',
      'run-connectivity:1->run-connectivity:2',
    ])
  })

  it('keeps a same-face L shape connected around its corner', () => {
    const result = buildLeakagePreviewConnectivity(
      [
        sample(0, [1, 1, 10]),
        sample(1, [2, 1, 10]),
        sample(2, [3, 1, 10]),
        sample(3, [3, 2, 10]),
        sample(4, [3, 3, 10]),
      ],
      options,
    )

    expect(result.edges).toHaveLength(4)
    expect(result.components).toMatchObject([
      { face: 'z_max', edgeCount: 4, totalWeight: 5 },
    ])
  })

  it('prunes an L-corner diagonal even when it is within the gap limit', () => {
    const result = buildLeakagePreviewConnectivity(
      [
        sample(0, [1, 1, 10]),
        sample(1, [2, 1, 10]),
        sample(2, [2, 2, 10]),
      ],
      { ...options, maxNeighborDistance: 1.5 },
    )

    expect(edgeKeys(result)).toEqual([
      'run-connectivity:0->run-connectivity:1',
      'run-connectivity:1->run-connectivity:2',
    ])
  })

  it('does not bridge a large empty interval', () => {
    const result = buildLeakagePreviewConnectivity(
      [
        sample(0, [1, 1, 10]),
        sample(1, [2, 1, 10]),
        sample(2, [7, 1, 10]),
        sample(3, [8, 1, 10]),
      ],
      options,
    )

    expect(result.edges).toHaveLength(2)
    expect(result.components.map((component) => component.nodeKeys.length)).toEqual([
      2,
      2,
    ])
  })

  it('never connects samples assigned to different extraction faces', () => {
    const result = buildLeakagePreviewConnectivity(
      [
        sample(0, [5, 5, 10], [1, 0, 0], 1, ['x_max']),
        sample(1, [5, 5, 10], [0, 0, 1], 1, ['z_max']),
      ],
      { ...options, maxNeighborDistance: 100 },
    )

    expect(result.nodes.map((node) => node.face)).toEqual(['x_max', 'z_max'])
    expect(result.edges).toEqual([])
    expect(result.components).toHaveLength(2)
  })

  it('does not connect nearby samples with opposite outgoing directions', () => {
    const result = buildLeakagePreviewConnectivity(
      [
        sample(0, [1, 1, 10], [0, 0, 1]),
        sample(1, [1.5, 1, 10], [0, 0, -1]),
      ],
      options,
    )

    expect(result.nodes.map((node) => node.face)).toEqual(['z_max', 'z_max'])
    expect(result.edges).toEqual([])
    expect(result.components).toHaveLength(2)
  })

  it('does not let a direction-incompatible midpoint prune a valid edge', () => {
    const result = buildLeakagePreviewConnectivity(
      [
        sample(0, [1, 1, 10], [0, 0, 1]),
        sample(1, [2, 1, 10], [0, 0, -1]),
        sample(2, [3, 1, 10], [0, 0, 1]),
      ],
      { ...options, maxNeighborDistance: 2.01 },
    )

    expect(edgeKeys(result)).toEqual([
      'run-connectivity:0->run-connectivity:2',
    ])
  })

  it('retains equal-length triangle edges instead of pruning every tie', () => {
    const result = buildLeakagePreviewConnectivity(
      [
        sample(0, [1, 1, 10]),
        sample(1, [2, 1, 10]),
        sample(2, [1.5, 1 + Math.sqrt(3) / 2, 10]),
      ],
      { ...options, maxNeighborDistance: 1.01 },
    )

    expect(result.edges).toHaveLength(3)
    expect(result.components).toMatchObject([{ edgeCount: 3 }])
  })

  it('keeps a distant outlier as an isolated energy-bearing sample', () => {
    const result = buildLeakagePreviewConnectivity(
      [
        sample(0, [1, 1, 10]),
        sample(1, [1.5, 1, 10]),
        sample(2, [9, 9, 10], [0, 0, 1], 0.25),
      ],
      options,
    )

    expect(result.edges).toHaveLength(1)
    expect(result.components.map((component) => component.nodeKeys.length)).toEqual([
      2,
      1,
    ])
    expect(result.energy.acceptedWeight).toBe(2.25)
  })

  it('preserves accepted energy and uses one caller-owned display scale', () => {
    const invalidDirection = sample(3, [4, 1, 10], [0, 0, 0], 4)
    const result = buildLeakagePreviewConnectivity(
      [
        sample(0, [1, 1, 10], [0, 0, 1], 1),
        sample(1, [2, 1, 10], [0, 0, 1], 2),
        sample(2, [3, 1, 10], [0, 0, 1], 3),
        invalidDirection,
      ],
      options,
    )
    const comparison = buildLeakagePreviewConnectivity(
      [sample(4, [5, 1, 10], [0, 0, 1], 4)],
      options,
    )

    expect(result.nodes.map((node) => node.displayStrength)).toEqual([
      0.25,
      0.5,
      0.75,
    ])
    expect(comparison.nodes[0].displayStrength).toBe(1)
    expect(result.commonWeightScale).toBe(4)
    expect(comparison.commonWeightScale).toBe(4)
    expect(result.energy).toEqual({
      validInputWeight: 10,
      acceptedWeight: 6,
      rejectedWeight: 4,
    })
    expect(
      result.components.reduce(
        (sum, component) => sum + component.totalWeight,
        0,
      ),
    ).toBe(result.energy.acceptedWeight)
    expect(result.rejected).toMatchObject([
      {
        key: 'run-connectivity:3',
        reason: 'invalid_direction',
        weight: 4,
      },
    ])
  })

  it('rejects an ambiguous AABB corner for raw point fallback', () => {
    const corner = sample(
      0,
      [10, 10, 10],
      [1, 1, 1],
      2,
      ['x_max', 'y_max', 'z_max'],
    )
    const result = buildLeakagePreviewConnectivity([corner], options)

    expect(result.nodes).toEqual([])
    expect(result.rejected).toEqual([
      {
        key: 'run-connectivity:0',
        reason: 'ambiguous_exit_face',
        weight: 2,
        sample: corner,
      },
    ])
    expect(result.energy).toEqual({
      validInputWeight: 2,
      acceptedWeight: 0,
      rejectedWeight: 2,
    })
  })

  it('is invariant to input sample order', () => {
    const samples = [
      sample(4, [10, 5, 5], [1, 0, 0], 0.5, ['x_max']),
      sample(2, [3, 1, 10], [0, 0, 1], 1),
      sample(0, [1, 1, 10], [0, 0, 1], 1e16),
      sample(3, [8, 8, 10], [0, 0, -1], 0.25),
      sample(5, [10, 10, 10], [1, 1, 1], 1, ['x_max', 'z_max']),
      sample(1, [2, 1, 10], [0, 0, 1], 1),
    ]

    const pruningOptions = { ...options, maxNeighborDistance: 2.01 }

    const forward = buildLeakagePreviewConnectivity(samples, pruningOptions)
    const reversed = buildLeakagePreviewConnectivity(
      [...samples].reverse(),
      pruningOptions,
    )

    expect(reversed).toEqual(forward)
    expect(forward.rejected[0].weight).toBe(1)
  })

  it('uses the same complete connected-component threshold as the renderer label', () => {
    const connected = Array.from({ length: 8 }, (_, index) =>
      sample(index, [index, 1, 10]),
    )
    const scattered = Array.from({ length: 8 }, (_, index) =>
      sample(index, [index * 2, 1, 10]),
    )

    expect(
      buildPrototypeLeakageFieldSelection(connected, true)
        .continuousNodeKeys.size,
    ).toBe(8)
    expect(
      buildPrototypeLeakageFieldSelection(scattered, true)
        .continuousNodeKeys.size,
    ).toBe(0)
    expect(
      buildPrototypeLeakageFieldSelection(connected, false),
    ).toMatchObject({ connectivity: null })
    expect(
      buildPrototypeLeakageFieldSelection(connected, false)
        .continuousNodeKeys.size,
    ).toBe(0)
  })
})
