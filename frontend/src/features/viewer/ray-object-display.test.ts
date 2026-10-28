import { BufferGeometry, Group, LineBasicMaterial, LineSegments, Mesh, MeshBasicMaterial, Plane, Vector3 } from 'three'
import { expect, it } from 'vitest'
import { applyRayObjectOcclusion } from './ray-object-display'

it('applies CAD occlusion to nested emitter and receiver faces, outlines, and arrows without changing opacity or section clipping', () => {
  const placements = new Group()
  const plane = new Plane(new Vector3(1, 0, 0), -5)
  const face = new MeshBasicMaterial({ depthTest: false, transparent: true, opacity: 0.28, clippingPlanes: [plane] })
  const line = new LineBasicMaterial({ depthTest: false })
  const receiver = new MeshBasicMaterial({ depthTest: true, opacity: 0.14 })
  const nested = new Group()
  nested.add(new Mesh(new BufferGeometry(), [face, receiver]), new LineSegments(new BufferGeometry(), line))
  placements.add(nested)
  applyRayObjectOcclusion(placements)
  expect([face.depthTest, line.depthTest, receiver.depthTest]).toEqual([true, true, true])
  expect(face.opacity).toBe(0.28)
  expect(receiver.opacity).toBe(0.14)
  expect(face.clippingPlanes?.[0]).toBe(plane)
  const version = face.version
  applyRayObjectOcclusion(placements)
  expect(face.version).toBe(version)
  const recreated = new MeshBasicMaterial({ depthTest: false })
  nested.add(new Mesh(new BufferGeometry(), recreated))
  applyRayObjectOcclusion(placements)
  expect(recreated.depthTest).toBe(true)
})
