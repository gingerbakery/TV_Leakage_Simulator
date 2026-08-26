import { describe, expect, it } from 'vitest'

import type { ScenePayload } from '@/api'

import { roiClippedSurfaceCentroid } from './roi-clipped-geometry'

describe('roiClippedSurfaceCentroid', () => {
  it('centers a Datum Plane on the clipped ROI surface, not the source triangle', () => {
    const scene = {
      mesh: {
        vertices: [
          [0, 0, 0],
          [100, 0, 0],
          [0, 100, 0],
        ],
        faces: [[0, 1, 2]],
      },
    } as unknown as ScenePayload

    const center = roiClippedSurfaceCentroid(scene, [0], [
      {
        plane: 'xy',
        xMin: 10,
        xMax: 20,
        yMin: 10,
        yMax: 20,
      },
    ])

    expect(center?.[0]).toBeCloseTo(15, 6)
    expect(center?.[1]).toBeCloseTo(15, 6)
    expect(center?.[2]).toBeCloseTo(0, 6)
  })
})
