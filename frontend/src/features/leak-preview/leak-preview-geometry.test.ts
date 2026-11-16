import { expect, it } from 'vitest'
import type { Vec3 } from '@/api'
import { createSceneFixture } from '@/test/scene-fixture'
import { receiverFootprintDistance } from './leak-preview-geometry'

function sceneWithTriangles(vertices: Vec3[], faces: number[][]) {
  const scene = createSceneFixture()
  scene.mesh.vertices = vertices
  scene.mesh.faces = faces as typeof scene.mesh.faces
  scene.mesh.face_component_ids = faces.map(() => 1)
  scene.components = [{ ...scene.components[0], bbox_min: [-100,-100,0], bbox_max: [100,100,100], is_truncated: false }]
  return scene
}

const origin: Vec3 = [0,0,0], outward: Vec3 = [0,0,1], u: Vec3 = [1,0,0], v: Vec3 = [0,1,0]

it('keeps receivers close when distant geometry lies outside their footprint', () => {
  const scene = sceneWithTriangles([[-1,-1,0], [1,-1,0], [0,1,0], [50,50,100], [60,50,100], [50,60,100]], [[0,1,2], [3,4,5]])
  expect(receiverFootprintDistance(scene, origin, outward, u, v, 4, 4, 3)).toBe(3)
})

it('clears triangles crossing the footprint even when all vertices and the center ray miss', () => {
  const scene = sceneWithTriangles([[-10,1,12], [10,1,12], [0,10,12]], [[0,1,2]])
  expect(receiverFootprintDistance(scene, origin, outward, u, v, 4, 4, 3)).toBe(15)
  expect(receiverFootprintDistance(scene, origin, outward, u, v, 1, 1, 3)).toBe(3)
})

it('uses conservative bounds for truncated or absent component meshes', () => {
  const scene = sceneWithTriangles([[-1,-1,0], [1,-1,0], [0,1,0]], [[0,1,2]])
  scene.components[0].is_truncated = true
  expect(receiverFootprintDistance(scene, origin, outward, u, v, 4, 4, 3)).toBe(103)
  scene.components[0].is_truncated = false
  scene.mesh.faces = []
  scene.mesh.face_component_ids = []
  expect(receiverFootprintDistance(scene, origin, outward, u, v, 4, 4, 3)).toBe(103)
})

it('includes component transforms when clearing the footprint', () => {
  const scene = sceneWithTriangles([[-1,-1,0], [1,-1,0], [0,1,0]], [[0,1,2]])
  expect(receiverFootprintDistance(scene, origin, outward, u, v, 4, 4, 3, [{
    ruleId: 'move', componentId: 1, targetType: 'component', enabled: true, selectionMethod: 'click', faceIds: [],
    move: { x: 0, y: 0, z: 7 }, tilt: { x: 0, y: 0, z: 0 },
  }])).toBe(10)
})
