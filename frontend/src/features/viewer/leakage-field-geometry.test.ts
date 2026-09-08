import { describe, expect, it } from 'vitest'

import { createLeakageFieldGeometry } from './leakage-field-geometry'

describe('createLeakageFieldGeometry', () => {
  it('keeps each halo on its recorded exit face and offsets it outward', () => {
    const result = createLeakageFieldGeometry([
      {
        key: 'x-node',
        face: 'x_max',
        position: [10, 4, 5],
        direction: [2, 0, 0],
        displayStrength: 0.25,
      },
      {
        key: 'z-node',
        face: 'z_max',
        position: [3, 4, 10],
        direction: [0, 0, 1],
        displayStrength: 0.75,
      },
    ], [], { diameterMm: 2, ribbonWidthMm: 1, surfaceOffsetMm: 0.01 })

    const positions = result.geometry.getAttribute('position')
    const directions = result.geometry.getAttribute('leakDirection')
    const strengths = result.geometry.getAttribute('leakStrength')

    expect(result.sampleCount).toBe(2)
    expect(result.edgeCount).toBe(0)
    expect(result.focus).toEqual([6.5, 4, 7.5])
    expect(positions.count).toBe(12)
    for (let index = 0; index < 6; index += 1) {
      expect(positions.getX(index)).toBeCloseTo(10.01)
      expect(positions.getZ(index + 6)).toBeCloseTo(10.01)
    }
    expect(directions.getX(0)).toBe(1)
    expect(strengths.getX(0)).toBe(0.25)
    expect(strengths.getX(6)).toBe(0.75)

    result.geometry.dispose()
  })

  it('returns empty geometry when every supplied node is invalid', () => {
    const result = createLeakageFieldGeometry([
      {
        key: 'invalid-node',
        face: 'z_max',
        position: [0, 0, 0],
        direction: [0, 0, 0],
        displayStrength: 1,
      },
    ], [], { diameterMm: 1.5, ribbonWidthMm: 1, surfaceOffsetMm: 0 })

    expect(result.sampleCount).toBe(0)
    expect(result.focus).toBeNull()
    expect(result.geometry.getAttribute('position').count).toBe(0)

    result.geometry.dispose()
  })

  it('adds a ribbon only for an edge whose endpoints share one face', () => {
    const nodes = [
      {
        key: 'a',
        face: 'z_max' as const,
        position: [1, 2, 10] as [number, number, number],
        direction: [0, 0, 1] as [number, number, number],
        displayStrength: 0.4,
      },
      {
        key: 'b',
        face: 'z_max' as const,
        position: [2, 2, 10] as [number, number, number],
        direction: [0, 0, 1] as [number, number, number],
        displayStrength: 0.8,
      },
    ]
    const result = createLeakageFieldGeometry(
      nodes,
      [{ from: 'a', to: 'b' }, { from: 'a', to: 'missing' }],
      { diameterMm: 2, ribbonWidthMm: 1, surfaceOffsetMm: 0.01 },
    )

    expect(result.sampleCount).toBe(2)
    expect(result.edgeCount).toBe(1)
    expect(result.geometry.getAttribute('position').count).toBe(18)
    expect(
      Array.from(
        { length: 6 },
        (_, index) => result.geometry.getAttribute('fieldKind').getX(index + 12),
      ),
    ).toEqual(Array(6).fill(1))

    result.geometry.dispose()
  })
})
