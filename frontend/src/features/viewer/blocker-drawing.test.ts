import { expect, it } from 'vitest'
import { OrthographicCamera, PerspectiveCamera, Vector3 } from 'three'
import { projectBlockerDrag } from './blocker-drawing'
import { createBlockGroup } from '@/features/leak-preview/blocker-groups'

it('projects a drag onto the selected side face even when all corners are outside the CAD', () => {
  const camera = new PerspectiveCamera(45, 1, .1, 1000)
  camera.position.set(100, 0, 0); camera.lookAt(0, 0, 0); camera.updateMatrixWorld()
  const group = createBlockGroup('Block 1', 'box')
  const patch = projectBlockerDrag(camera, { width: 800, height: 800 },
    { startX: 100, startY: 150, currentX: 700, currentY: 650 }, group, { point: [3,0,0], normal: [1,0,0] })!
  expect(patch).not.toBeNull()
  expect(patch.normal![0]).toBeCloseTo(1,10)
  expect(patch.normal![1]).toBeCloseTo(0,10)
  expect(patch.normal![2]).toBeCloseTo(0,10)
  expect(patch.baseCenter![0]).toBeCloseTo(3, 10)
  expect(patch.widthMm).toBeGreaterThan(1)
  expect(patch.heightMm).toBeGreaterThan(1)
  expect(patch.uAxis![0]).toBeCloseTo(0, 10)
  expect(patch.vAxis![0]).toBeCloseTo(0, 10)
  expect(group.regions).toEqual([])
})

it('uses a hit only for depth, including an edge-on face, and rejects missing depth or zero size', () => {
  const camera = new PerspectiveCamera(45, 1, .1, 1000)
  camera.position.set(0,0,100); camera.lookAt(0,0,0); camera.updateMatrixWorld()
  const group = createBlockGroup('Block 1', 'box')
  const viewport = { width: 800, height: 800 }
  const drag = { startX: 100, startY: 150, currentX: 700, currentY: 650 }
  expect(projectBlockerDrag(camera, viewport, drag, group, null)).toBeNull()
  expect(projectBlockerDrag(camera, viewport, drag, group, { point: [3,0,0], normal: [1,0,0] })?.normal).toEqual([0,0,1])
  expect(projectBlockerDrag(camera, viewport, { ...drag, currentX: drag.startX }, group, { point: [0,0,3], normal: [0,0,1] })).toBeNull()
})

it('creates a flat screen-aligned Block in the -XY view on a sloping CAD face', () => {
  const camera = new OrthographicCamera(-40,40,40,-40,.1,1000)
  camera.position.set(0,0,-100); camera.lookAt(0,0,0); camera.updateMatrixWorld()
  const group=createBlockGroup('Block 1','box')
  const patch=projectBlockerDrag(camera,{width:800,height:800},
    {startX:200,startY:250,currentX:600,currentY:550},group,
    {point:[5,8,7],normal:[Math.SQRT1_2,0,Math.SQRT1_2]})!
  expect(patch.baseCenter![2]).toBeCloseTo(7,10)
  expect(patch.normal![2]).toBeCloseTo(-1,10)
  expect(patch.uAxis![2]).toBeCloseTo(0,10)
  expect(patch.vAxis![2]).toBeCloseTo(0,10)
  expect(patch.widthMm).toBeCloseTo(40,10)
  expect(patch.heightMm).toBeCloseTo(30,10)
  expect(new Vector3(...patch.uAxis!).cross(new Vector3(...patch.vAxis!)).dot(new Vector3(...patch.normal!))).toBeCloseTo(1,10)
})

it('follows camera roll and rotation while preserving drag depth and existing Block settings', () => {
  const camera=new PerspectiveCamera(45,1,.1,1000)
  camera.position.set(40,30,-100); camera.lookAt(0,0,0); camera.rotateZ(.4); camera.updateMatrixWorld()
  const group={...createBlockGroup('Block 1','box'),depthMm:7,offsetMm:-2,reverse:true}
  const drag={startX:200,startY:250,currentX:600,currentY:550}
  const anchor={point:[0,0,7] as [number,number,number],normal:[1,0,0] as [number,number,number]}
  const patch=projectBlockerDrag(camera,{width:800,height:800},drag,group,anchor)!
  const outward=camera.getWorldDirection(new Vector3()).negate()
  expect(new Vector3(...patch.normal!).dot(outward)).toBeCloseTo(1,10)
  expect(new Vector3(...patch.baseCenter!).sub(new Vector3(...anchor.point)).dot(outward)).toBeCloseTo(0,10)
  expect(new Vector3(...patch.uAxis!).dot(new Vector3(1,0,0).applyQuaternion(camera.quaternion))).toBeCloseTo(1,10)
  expect(patch).toMatchObject({depthMm:7,offsetMm:-2,reverse:true})
  expect(projectBlockerDrag(camera,{width:800,height:800},
    {startX:drag.currentX,startY:drag.currentY,currentX:drag.startX,currentY:drag.startY},group,anchor)).toEqual(patch)
})
