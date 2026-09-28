import { describe, expect, it } from 'vitest'

import { screenTriangleIntersectsBox } from './screen-box-selection'

const box = { minX: 10, maxX: 30, minY: 10, maxY: 30 }

describe('screenTriangleIntersectsBox', () => {
  it('detects a triangle vertex inside the selection box', () => {
    expect(screenTriangleIntersectsBox([
      { x: 20, y: 20 },
      { x: 40, y: 20 },
      { x: 20, y: 40 },
    ], box)).toBe(true)
  })

  it('detects a box fully inside a large projected triangle', () => {
    expect(screenTriangleIntersectsBox([
      { x: 0, y: 0 },
      { x: 50, y: 0 },
      { x: 25, y: 60 },
    ], box)).toBe(true)
  })

  it('rejects a projected triangle outside the selection box', () => {
    expect(screenTriangleIntersectsBox([
      { x: 40, y: 40 },
      { x: 60, y: 40 },
      { x: 50, y: 60 },
    ], box)).toBe(false)
  })
})
