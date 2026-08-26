import { Box3, PerspectiveCamera, Vector3 } from 'three'
import { describe, expect, it } from 'vitest'

import { fitPerspectiveCameraToBounds } from './camera-fit'

function projectedBounds(
  bounds: Box3,
  camera: PerspectiveCamera,
): { maxX: number; maxY: number } {
  let maxX = 0
  let maxY = 0
  for (const x of [bounds.min.x, bounds.max.x]) {
    for (const y of [bounds.min.y, bounds.max.y]) {
      for (const z of [bounds.min.z, bounds.max.z]) {
        const projected = new Vector3(x, y, z).project(camera)
        maxX = Math.max(maxX, Math.abs(projected.x))
        maxY = Math.max(maxY, Math.abs(projected.y))
      }
    }
  }
  return { maxX, maxY }
}

describe('viewer camera fit', () => {
  it.each([
    ['wide ROI', new Box3(new Vector3(-500, -20, -5), new Vector3(500, 20, 5)), 2.4],
    ['tall ROI', new Box3(new Vector3(-15, -450, -5), new Vector3(15, 450, 5)), 0.65],
    ['deep ISO ROI', new Box3(new Vector3(-80, -45, -300), new Vector3(120, 75, 350)), 1.7],
  ])('keeps every %s corner inside the viewport', (_name, bounds, aspect) => {
    const camera = new PerspectiveCamera(42, aspect, 0.01, 10000)
    fitPerspectiveCameraToBounds(
      camera,
      bounds,
      aspect,
      new Vector3(1, 1, 1),
      new Vector3(0, 1, 0),
    )

    const projected = projectedBounds(bounds, camera)
    expect(projected.maxX).toBeLessThanOrEqual(1)
    expect(projected.maxY).toBeLessThanOrEqual(1)
  })
})
