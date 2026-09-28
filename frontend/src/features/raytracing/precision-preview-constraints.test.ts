import { describe, expect, it } from 'vitest'

import { buildPrecisionPreviewConstraints } from './precision-preview-constraints'

describe('precision Preview constraints', () => {
  const blocker = {
    id: 'board',
    label: 'Main Board',
    enabled: true,
    baseCenter: [1, 2, 3] as [number, number, number],
    uAxis: [1, 0, 0] as [number, number, number],
    vAxis: [0, 1, 0] as [number, number, number],
    normal: [0, 0, 1] as [number, number, number],
    widthMm: 20,
    heightMm: 10,
    offsetMm: 2,
    depthMm: 4,
    reverse: false,
  }
  const allowedArea = {
    id: 'pem-holes',
    label: 'Allowed Area 01',
    enabled: true,
    regions: [{
      plane: 'xy' as const,
      xMin: 10,
      xMax: 14,
      yMin: 20,
      yMax: 26,
      zMin: 30,
      zMax: 32,
    }],
  }

  it('keeps both precision constraint groups off by default', () => {
    expect(buildPrecisionPreviewConstraints(
      [blocker], [allowedArea], false, false,
    )).toEqual([])
  })

  it('converts enabled Blockers and Allowed Areas to absorbing volumes', () => {
    const result = buildPrecisionPreviewConstraints(
      [blocker], [allowedArea], true, true,
    )

    expect(result).toHaveLength(3)
    expect(result[0]).toMatchObject({
      blocker_id: 'precision-blocker:board',
      center: [1, 2, 7],
      width_mm: 20,
      height_mm: 10,
      depth_mm: 4,
    })
    expect(result[1]).toMatchObject({
      blocker_id: 'precision-allowed-area:pem-holes:0:min',
      center: [12, 23, 30],
      width_mm: 4,
      height_mm: 6,
      depth_mm: 0.1,
    })
    expect(result[2]).toMatchObject({
      blocker_id: 'precision-allowed-area:pem-holes:0:max',
      center: [12, 23, 32],
      depth_mm: 0.1,
    })
  })
})
