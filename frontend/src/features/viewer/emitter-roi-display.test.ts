import { ArrowHelper, BoxGeometry, Group, LineBasicMaterial, LineSegments, Mesh, MeshBasicMaterial, Plane, ShaderLib, Vector3, type Material } from 'three'
import { expect, it } from 'vitest'
import { clipEmitterDisplayToRoi, emitterPointInRoi } from './emitter-roi-display'
import type { RoiClipBox } from '@/stores'

const boxes: RoiClipBox[] = [
  { plane: 'xyz', xMin: 0, xMax: 1, yMin: 0, yMax: 1, zMin: 0, zMax: 1 },
  { plane: 'xyz', xMin: 3, xMax: 4, yMin: 0, yMax: 1, zMin: 0, zMax: 1 },
]

it('keeps the union of ROI boxes in world coordinates and respects projected/legacy boxes', () => {
  expect(emitterPointInRoi(new Vector3(.5,.5,.5), boxes)).toBe(true)
  expect(emitterPointInRoi(new Vector3(3.5,.5,.5), boxes)).toBe(true)
  expect(emitterPointInRoi(new Vector3(2,.5,.5), boxes)).toBe(false)
  expect(emitterPointInRoi(new Vector3(.5,.5,1000), [{ ...boxes[0], plane: 'xy' }])).toBe(true)
  expect(emitterPointInRoi(new Vector3(.5,.5,1000), [{ ...boxes[0], plane: undefined }])).toBe(true)
  expect(emitterPointInRoi(new Vector3(1000,.5,.5), [{ ...boxes[0], plane: 'yz' }])).toBe(true)
  expect(emitterPointInRoi(new Vector3(.5,1000,.5), [{ ...boxes[0], plane: 'zx' }])).toBe(true)
  expect(emitterPointInRoi(new Vector3(1000,1000,1000), [])).toBe(true)
})

it('clips planes, outlines and direction arrows without changing geometry, placement or Section', () => {
  const root = new Group()
  const section = new Plane(new Vector3(0,0,1), -.5)
  const material = new MeshBasicMaterial({ clippingPlanes: [section] })
  const geometry = new BoxGeometry(10,10,10)
  const positions = geometry.attributes.position.array.slice()
  const mesh = new Mesh(geometry, material)
  mesh.position.set(3,0,0)
  mesh.rotation.z = Math.PI/4
  root.add(mesh, new LineSegments(geometry, new LineBasicMaterial()), new ArrowHelper(new Vector3(0,0,1)))
  clipEmitterDisplayToRoi(root, boxes)
  expect(geometry.attributes.position.array).toEqual(positions)
  expect(mesh.position.x).toBe(3)
  expect(mesh.rotation.z).toBe(Math.PI/4)
  expect(material.clippingPlanes).toEqual([section])
  root.traverse((object) => {
    const value = (object as Mesh).material
    if (!value) return
    for (const entry of Array.isArray(value) ? value : [value]) {
      const shader = { uniforms: {}, vertexShader: ShaderLib.basic.vertexShader, fragmentShader: ShaderLib.basic.fragmentShader } as Parameters<Material['onBeforeCompile']>[0]
      entry.onBeforeCompile(shader, {} as Parameters<Material['onBeforeCompile']>[1])
      expect(shader.vertexShader).toContain('(modelMatrix * vec4(transformed, 1.0)).xyz')
      expect(shader.fragmentShader).toContain('#include <clipping_planes_fragment>')
      expect(shader.fragmentShader).toContain(' || ')
      expect(shader.fragmentShader).toContain('discard;')
      expect(entry.customProgramCacheKey()).toContain('emitter-roi-union:')
    }
  })
})

it('leaves the full display shader unchanged when ROI is disabled', () => {
  const material = new MeshBasicMaterial()
  const root = new Mesh(new BoxGeometry(), material)
  const compile = material.onBeforeCompile
  clipEmitterDisplayToRoi(root, [])
  expect(material.onBeforeCompile).toBe(compile)
})
