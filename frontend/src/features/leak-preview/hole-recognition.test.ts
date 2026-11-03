import { expect, it, vi } from 'vitest'
import type { ScenePayload, Vec3 } from '@/api'
import cadPlate from '@/test/fixtures/hole-plate.scene.json'
import { createSceneFixture } from '@/test/scene-fixture'
import { findCadHoleLoops } from './hole-recognition'
import { createBlockGroup, expandBlockerGroups } from './blocker-groups'
import { pickRoundCadHole } from './hole-recognition'
import { PerspectiveCamera, Vector3 } from 'three'
import { leakPreviewBlockerCenter } from './leak-preview-model'

function annulus(ellipse = false) {
  const scene = createSceneFixture(), count = 32, angle = ellipse ? .4 : 0
  const points = (scale: number): Vec3[] => Array.from({ length: count }, (_, i) => {
    const t = i*Math.PI*2/count, x = Math.cos(t)*3*scale, y = Math.sin(t)*(ellipse ? 2 : 3)*scale
    return [10+x*Math.cos(angle)-y*Math.sin(angle), 20+x*Math.sin(angle)+y*Math.cos(angle), 3]
  })
  scene.mesh.vertices = [...points(1), ...points(1.7)]
  scene.mesh.faces = Array.from({ length: count }, (_, i): Array<[number,number,number]> => [
    [i, (i+1)%count, count+i], [(i+1)%count, count+(i+1)%count, count+i],
  ]).flat()
  scene.mesh.face_component_ids = scene.mesh.faces.map(() => 1)
  scene.mesh.feature_edge_segments = [points(1), points(1.7)].flatMap((loop) => loop.map((start, i) => ({ start, end: loop[(i+1)%count], component_id: 1 })))
  scene.components = [{ ...scene.components[0], face_indices: scene.mesh.faces.map((_,i) => i), bbox_min: [4,14,3], bbox_max: [16,26,3] }]
  scene.objects = scene.components
  return scene
}

it('recognizes a circular opening without confusing the outer circular rim for a hole', () => {
  const holes = findCadHoleLoops(annulus())
  expect(holes).toHaveLength(1)
  expect(holes[0].placement.baseCenter).toEqual([10,20,3])
  expect(holes[0].placement.widthMm).toBeCloseTo(6/Math.cos(Math.PI/48)+.002, 6)
  expect(holes[0].placement.heightMm).toBeCloseTo(6/Math.cos(Math.PI/48)+.002, 6)
  const group = createBlockGroup('Block 1', 'ellipse')
  const region = { ...group, ...holes[0].placement, regions: undefined }
  expect(leakPreviewBlockerCenter(region)).toEqual([10,20,3])
  expect(region).toMatchObject({ normal: [0,0,1], depthMm: 5, offsetMm: -2.5, reverse: false })
  expect(expandBlockerGroups([{ ...group, regions: [region] }])[0].groupId).toBe(group.id)
})

it('fits a rotated elliptical opening in transformed model coordinates', () => {
  const holes = findCadHoleLoops(annulus(true), [{ ruleId: 'move', targetType:'component', componentId:1, selectionMethod:'click', faceIds:[],
    move: { x:3,y:-4,z:7 }, tilt: { x:0,y:0,z:25 }, enabled:true }])
  expect(holes).toHaveLength(1)
  expect(holes[0].placement.baseCenter[0]).toBeCloseTo(13, 5)
  expect(holes[0].placement.baseCenter[1]).toBeCloseTo(16, 5)
  expect(holes[0].placement.baseCenter[2]).toBeCloseTo(10, 5)
  expect(holes[0].placement.widthMm).toBeCloseTo(6/Math.cos(Math.PI/48)+.002, 5)
  expect(holes[0].placement.heightMm).toBeCloseTo(4/Math.cos(Math.PI/48)+.002, 5)
})

it('rejects open rims, solid disks, hidden components and unsupported tilted holes', () => {
  const open = annulus()
  open.mesh.feature_edge_segments.shift()
  expect(findCadHoleLoops(open)).toEqual([])
  const disk = annulus()
  disk.mesh.vertices.push([10,20,3])
  for (let i = 0; i < 32; i++) { disk.mesh.faces.push([64,i,(i+1)%32]); disk.mesh.face_component_ids.push(1) }
  expect(findCadHoleLoops(disk)).toEqual([])
  expect(findCadHoleLoops(annulus(), [], [1])).toEqual([])
  expect(findCadHoleLoops(annulus(), [{ ruleId:'tilt', targetType:'component', componentId:1, selectionMethod:'click', faceIds:[],
    move:{x:0,y:0,z:0}, tilt:{x:90,y:0,z:0}, enabled:true }])).toEqual([])
})

it('does not mistake an isolated wire circle or duplicated seam edges for an extra hole', () => {
  const wire = annulus()
  wire.mesh.faces = []; wire.mesh.face_component_ids = []
  expect(findCadHoleLoops(wire)).toEqual([])
  const scene = annulus()
  const edge = scene.mesh.feature_edge_segments[0]
  scene.mesh.feature_edge_segments.push({ ...edge, start: edge.end, end: edge.start })
  expect(findCadHoleLoops(scene)).toHaveLength(1)
})

it('recognizes all three openings on both sides of an actual imported STEP plate', () => {
  const base = createSceneFixture(), { components, ...mesh } = cadPlate
  const scene = { ...base, mesh:{ ...base.mesh, ...mesh }, components, objects:components } as ScenePayload
  const holes = findCadHoleLoops(scene)
  expect(holes).toHaveLength(6)
  for (const x of [-10,0,10]) for (const z of [-1,1]) {
    expect(holes.some((hole) => Math.abs(hole.placement.baseCenter[0]-x) < .05 &&
      Math.abs(hole.placement.baseCenter[1]) < .05 && Math.abs(hole.placement.baseCenter[2]-z) < 1e-5)).toBe(true)
  }
})

it.each([100,-100])('point-picks a circular hole from either XY view at Z=%s without tilt or rotation', (cameraZ) => {
  const base = createSceneFixture(), { components,...mesh } = cadPlate
  const scene = { ...base,mesh:{ ...base.mesh,...mesh },components,objects:components } as ScenePayload
  const holes = findCadHoleLoops(scene)
  const camera = new PerspectiveCamera(30,1,.01,10000)
  camera.position.set(0,0,cameraZ); camera.lookAt(0,0,0); camera.updateMatrixWorld(true)
  const project = (point:Vec3) => {
    const p = new Vector3(...point).project(camera)
    return { x:(p.x+1)*500, y:(1-p.y)*500, z:p.z }
  }
  const expectedZ = Math.sign(cameraZ)
  const pointer = project([-10,0,expectedZ])
  const placement = pickRoundCadHole(holes,{ x:pointer.x+2,y:pointer.y+1 },project,() => true)!
  expect(placement.baseCenter[0]).toBeCloseTo(-10,4)
  expect(placement.baseCenter[1]).toBeCloseTo(0,4)
  expect(placement.baseCenter[2]).toBe(expectedZ)
  expect(placement.widthMm).toBe(placement.heightMm)
  const entrance = holes.find((hole) => Math.abs(hole.placement.baseCenter[0]+10) < .01 && hole.placement.baseCenter[2] === expectedZ)!
  expect(placement.widthMm).toBeCloseTo(entrance.placement.widthMm!*3, 8)
  expect(placement).toMatchObject({ uAxis:[1,0,0],vAxis:[0,1,0],normal:[0,0,1],depthMm:5,offsetMm:-2.5 })
  expect(pickRoundCadHole(holes,project([10,0,expectedZ]),project,() => true)).toBeNull()
  expect(pickRoundCadHole(holes,pointer,project,() => false)).toBeNull()
})

it('checks visibility lazily and still prefers the nearer entrance of the selected bore', () => {
  const template = findCadHoleLoops(annulus())[0]
  const holes = Array.from({ length:100 }, (_, index) => ({ ...template, componentId:index,
    placement:{ ...template.placement, baseCenter:[10,20,index/1000] as Vec3 } }))
  const visible = vi.fn(() => true)
  expect(pickRoundCadHole(holes,{x:10,y:20},([x,y,z]) => ({x,y,z}),visible)).not.toBeNull()
  expect(visible).toHaveBeenCalledTimes(1)
  const front = { ...template, placement:{ ...template.placement,baseCenter:[10,20,-.5] as Vec3 } }
  const blockedFront = vi.fn((hole) => hole !== front)
  expect(pickRoundCadHole([template,front],{x:10,y:20},([x,y,z]) => ({x,y,z:z/10}),blockedFront)?.baseCenter).toEqual(template.placement.baseCenter)
  expect(pickRoundCadHole([template,front],{x:10,y:20},([x,y,z]) => ({x,y,z:z/10}),() => true)?.baseCenter).toEqual(front.placement.baseCenter)
})
