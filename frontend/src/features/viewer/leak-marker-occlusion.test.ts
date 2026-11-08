import { afterEach, expect, it, vi } from 'vitest'
import { BoxGeometry, Group, Mesh, MeshBasicMaterial, Path, PerspectiveCamera, Points, Shape, ShapeGeometry, Vector3, type WebGLRenderer } from 'three'
import { LeakMarkerOcclusion } from './leak-marker-occlusion'

const passes: LeakMarkerOcclusion[] = []
afterEach(() => { passes.splice(0).forEach(pass => pass.dispose()) })
function setup() {
  const pass = new LeakMarkerOcclusion()
  passes.push(pass)
  const camera = new PerspectiveCamera(40, 1, .01, 100)
  camera.position.z = 10
  camera.lookAt(0, 0, 0)
  return { pass, camera }
}

it('uses hidden transparent CAD as an occluder without changing its display state', () => {
  const { pass, camera } = setup()
  const wall = new Mesh(new BoxGeometry(4, 4, .2), new MeshBasicMaterial({ transparent: true, opacity: .1, depthWrite: false }))
  wall.position.z = 1
  const parent = new Group()
  parent.visible = false
  parent.add(wall)
  const geometryDisposed = vi.spyOn(wall.geometry, 'dispose')
  pass.sync([wall])
  expect(pass.isPointVisible(new Vector3(), camera)).toBe(false)
  expect(parent.visible).toBe(false)
  expect(wall.material.opacity).toBe(.1)
  expect(wall.material.depthWrite).toBe(false)
  // Analysis deletion/exclusion removes the physical source, unlike Hide.
  pass.sync([])
  expect(pass.isPointVisible(new Vector3(), camera)).toBe(true)
  pass.dispose()
  expect(geometryDisposed).not.toHaveBeenCalled()
})

it('retains visible internal points through a real opening and honors a local cover', () => {
  const { pass, camera } = setup()
  const outline = new Shape().moveTo(-3,-3).lineTo(3,-3).lineTo(3,3).lineTo(-3,3).closePath()
  const hole = new Path().moveTo(-1,-1).lineTo(-1,1).lineTo(1,1).lineTo(1,-1).closePath()
  outline.holes.push(hole)
  const frame = new Mesh(new ShapeGeometry(outline), new MeshBasicMaterial())
  frame.position.z = 1
  frame.visible = false
  pass.sync([frame])
  expect(pass.isPointVisible(new Vector3(0,0,0), camera)).toBe(true)
  expect(pass.isPointVisible(new Vector3(2,0,0), camera)).toBe(false)
  const cover = new Mesh(new BoxGeometry(1,2,.1), new MeshBasicMaterial())
  cover.position.set(.5,0,1.2)
  cover.visible = false
  pass.sync([frame, cover])
  expect(pass.isPointVisible(new Vector3(.5,0,0), camera)).toBe(false)
  expect(pass.isPointVisible(new Vector3(-.5,0,0), camera)).toBe(true)
  // Moving an existing proxy must not leave its old position blocking the hole.
  cover.position.x = 10
  pass.sync([frame, cover])
  expect(pass.isPointVisible(new Vector3(.5,0,0), camera)).toBe(true)
})

it('preserves the colour buffer and renderer state for the dedicated depth/marker pass', () => {
  const { pass, camera } = setup()
  const renderer = { autoClear: true, clearDepth: vi.fn(), render: vi.fn() }
  pass.render(renderer as unknown as WebGLRenderer, camera)
  expect(renderer.clearDepth).not.toHaveBeenCalled()
  pass.markers.add(new Points())
  const wall = new Mesh(new BoxGeometry(), new MeshBasicMaterial())
  pass.sync([wall])
  renderer.render.mockImplementation(scene => {
    expect(renderer.autoClear).toBe(false)
    const occluder = scene.children.find((child: unknown) => child instanceof Mesh) as Mesh
    const material = occluder.material as MeshBasicMaterial
    expect(occluder.geometry).toBe(wall.geometry)
    expect(material.colorWrite).toBe(false)
    expect(material.depthWrite).toBe(true)
    expect(material.clippingPlanes).toBeNull()
  })
  pass.render(renderer as unknown as WebGLRenderer, camera)
  expect(renderer.clearDepth).toHaveBeenCalledOnce()
  expect(renderer.render).toHaveBeenCalledOnce()
  expect(renderer.autoClear).toBe(true)
})
