import { expect, it } from 'vitest'
import { PerspectiveCamera } from 'three'
import { projectBlockerDrag } from './blocker-drawing'
import { createBlockGroup } from '@/features/leak-preview/blocker-groups'

it('projects a drag onto the selected side face even when all corners are outside the CAD', () => {
  const camera = new PerspectiveCamera(45, 1, .1, 1000)
  camera.position.set(100, 0, 0); camera.lookAt(0, 0, 0); camera.updateMatrixWorld()
  const group = createBlockGroup('Block 1', 'box')
  const patch = projectBlockerDrag(camera, { width: 800, height: 800 },
    { startX: 100, startY: 150, currentX: 700, currentY: 650 }, group, { point: [3,0,0], normal: [1,0,0] })!
  expect(patch).not.toBeNull()
  expect(patch.normal).toEqual([1,0,0])
  expect(patch.baseCenter![0]).toBeCloseTo(3, 10)
  expect(patch.widthMm).toBeGreaterThan(1)
  expect(patch.heightMm).toBeGreaterThan(1)
  expect(patch.uAxis![0]).toBeCloseTo(0, 10)
  expect(patch.vAxis![0]).toBeCloseTo(0, 10)
  expect(group.regions).toEqual([])
})

it('requires a real surface and rejects edge-on or zero-size rectangles', () => {
  const camera = new PerspectiveCamera(45, 1, .1, 1000)
  camera.position.set(0,0,100); camera.lookAt(0,0,0); camera.updateMatrixWorld()
  const group = createBlockGroup('Block 1', 'box')
  const viewport = { width: 800, height: 800 }
  const drag = { startX: 100, startY: 150, currentX: 700, currentY: 650 }
  expect(projectBlockerDrag(camera, viewport, drag, group, null)).toBeNull()
  expect(projectBlockerDrag(camera, viewport, drag, group, { point: [3,0,0], normal: [1,0,0] })).toBeNull()
  expect(projectBlockerDrag(camera, viewport, { ...drag, currentX: drag.startX }, group, { point: [0,0,3], normal: [0,0,1] })).toBeNull()
})
