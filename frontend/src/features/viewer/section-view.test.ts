import { Box3, Vector3 } from 'three'
import { describe, expect, it } from 'vitest'

import { viewerSectionAxisNormal, viewerSectionPlane } from './section-view'

describe('viewer section plane', () => {
  const bounds = new Box3(
    new Vector3(-10, -5, -2),
    new Vector3(10, 5, 2),
  )

  it('moves the clipping plane across the visible bounds', () => {
    const middle = viewerSectionPlane(bounds, new Vector3(1, 0, 0), 0, false)
    const end = viewerSectionPlane(bounds, new Vector3(1, 0, 0), 1, false)

    expect(middle?.center.x).toBe(0)
    expect(middle?.halfSpan).toBe(10)
    expect(end?.center.x).toBe(10)
  })

  it('reverses the kept section side without moving the plane', () => {
    const normal = viewerSectionPlane(bounds, new Vector3(0, 0, -1), 0.25, false)
    const reversed = viewerSectionPlane(bounds, new Vector3(0, 0, -1), 0.25, true)

    expect(reversed?.center).toEqual(normal?.center)
    expect(reversed?.plane.normal.toArray()).toEqual(
      normal?.plane.normal.clone().negate().toArray(),
    )
  })

  it('maps X, Y, and Z section controls to the matching world axes', () => {
    expect(viewerSectionAxisNormal('x').toArray()).toEqual([1, 0, 0])
    expect(viewerSectionAxisNormal('y').toArray()).toEqual([0, 1, 0])
    expect(viewerSectionAxisNormal('z').toArray()).toEqual([0, 0, 1])
  })
})
