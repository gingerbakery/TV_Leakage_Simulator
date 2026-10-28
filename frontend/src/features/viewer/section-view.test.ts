import { Box3, Vector3, Group, Mesh, MeshBasicMaterial, BufferGeometry, LineSegments, LineBasicMaterial, Plane } from 'three'
import { describe, expect, it } from 'vitest'

import {
  setObjectClippingPlane,
  viewerSectionAxisNormal,
  viewerSectionAxisRange,
  viewerSectionOffsetRatio,
  viewerSectionPlane,
  viewerSectionPositionMm,
} from './section-view'

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

  it('converts between slider ratio and an absolute axis position in mm', () => {
    const range = viewerSectionAxisRange(bounds, 'x')
    expect(range).toEqual({ minMm: -10, maxMm: 10 })
    expect(viewerSectionPositionMm(range!, 0.25)).toBe(2.5)
    expect(viewerSectionOffsetRatio(range!, -5)).toBe(-0.5)
    expect(viewerSectionOffsetRatio(range!, 50)).toBe(1)
  })
})


describe('section clipping for emitter placements', () => {
  it('clips nested emitter faces and arrows, follows the moving plane, and restores them when disabled', () => {
    const placements = new Group()
    const emitter = new Group()
    const face = new MeshBasicMaterial()
    const edge = new LineBasicMaterial()
    emitter.add(new Mesh(new BufferGeometry(), face), new LineSegments(new BufferGeometry(), edge))
    placements.add(emitter)
    const plane = new Plane(new Vector3(1, 0, 0), 0)
    setObjectClippingPlane(placements, plane)
    expect(face.clippingPlanes?.[0]).toBe(plane)
    expect(edge.clippingPlanes?.[0]).toBe(plane)
    plane.constant = -5
    plane.negate()
    expect(face.clippingPlanes?.[0].constant).toBe(5)
    const addedEmitterMaterial = new MeshBasicMaterial()
    placements.add(new Mesh(new BufferGeometry(), addedEmitterMaterial))
    setObjectClippingPlane(placements, plane)
    expect(addedEmitterMaterial.clippingPlanes?.[0]).toBe(plane)
    setObjectClippingPlane(placements, null)
    for (const material of [face, edge, addedEmitterMaterial]) expect(material.clippingPlanes).toBeNull()
  })
})
