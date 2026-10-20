import { describe, expect, it } from 'vitest'

import type { ReceiverGrid } from '@/api'

import {
  receiverGridLuminanceValues,
  receiverHeatmap3dRgba,
} from './receiver-heatmap-3d'

describe('3D Receiver Heatmap texture', () => {
  it('keeps the Report Heatmap top-to-bottom row orientation', () => {
    const grid: ReceiverGrid = {
      receiver_id: 'receiver_001',
      resolution: [2, 2],
      bin_area_mm2: 1,
      flux_lumen: [[1, 2], [3, 4]],
      hit_count: 4,
    }

    const values = receiverGridLuminanceValues(grid, Math.PI * 1e-6, 1)
    expect(values).toEqual([3, 4, 1, 2])
  })

  it('uses the same blue-to-red color normalization as the Report Heatmap', () => {
    const pixels = receiverHeatmap3dRgba([0, 100], {
      minNit: 0,
      maxNit: 100,
    })
    expect([...pixels.slice(0, 4)]).toEqual([8, 20, 190, 255])
    expect([...pixels.slice(4, 8)]).toEqual([238, 28, 20, 255])
  })

  it('applies the Report View Cone multiplier without changing the base grid', () => {
    const grid: ReceiverGrid = {
      receiver_id: 'receiver_001',
      resolution: [1, 1],
      bin_area_mm2: 1,
      flux_lumen: [[2]],
      hit_count: 1,
    }

    expect(receiverGridLuminanceValues(grid, Math.PI * 1e-6, 1, 4)).toEqual([8])
  })

  it('supports a grayscale 3D texture', () => {
    const pixels = receiverHeatmap3dRgba(
      [0, 100],
      { minNit: 0, maxNit: 100 },
      'mono',
    )
    expect([...pixels.slice(0, 4)]).toEqual([0, 0, 0, 255])
    expect([...pixels.slice(4, 8)]).toEqual([255, 255, 255, 255])
  })
})
