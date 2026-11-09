import { expect, it } from 'vitest'
import { BufferGeometry, DoubleSide, Float32BufferAttribute, Group, Mesh, MeshBasicMaterial, OrthographicCamera, PerspectiveCamera, Points, PointsMaterial, Vector3 } from 'three'
import { pickLeakPreviewCandidate, pickLeakPreviewPoint } from './leak-preview-picking'
import { exitGuideGeometry } from './leak-preview-exit-guides'

function dots(positions = [0, 0, 0], ids: Array<string | undefined> = ['leak-one']) {
  const root = new Group()
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new Float32BufferAttribute(positions, 3))
  const points = new Points(geometry, new PointsMaterial({ depthTest: false }))
  points.userData.leakCandidateIds = ids
  root.add(points)
  return { root, points }
}

function camera(distance = 10) {
  const camera = new PerspectiveCamera(60, 2, .1, 1000)
  camera.position.z = distance
  camera.lookAt(0, 0, 0)
  return camera
}

const viewport = { width: 800, height: 400 }

it('keeps a generous 12 px click radius without enlarging the rendered dot', () => {
  const { root } = dots()
  for (const distance of [2, 10, 300]) {
    expect(pickLeakPreviewCandidate(root, camera(distance), { x: 411, y: 200 }, viewport)).toBe('leak-one')
    expect(pickLeakPreviewCandidate(root, camera(distance), { x: 413, y: 200 }, viewport)).toBeNull()
  }
})

it('checks nearby points in screen-distance order and stops after the first visible one', () => {
  const positions = Array.from({length:1000}, (_,i) => [i/10000,0,0]).flat()
  const { root } = dots(positions, Array.from({length:1000},(_,i)=>`point-${i}`))
  let checks = 0
  expect(pickLeakPreviewCandidate(root, camera(), {x:400,y:200}, viewport, () => { checks++; return true })).toBe('point-0')
  expect(checks).toBe(1)
})

it('retains exact observation identity for two points owned by the same grouped candidate', () => {
  const {root,points}=dots([0,0,0,1,0,0],['group','group'])
  points.userData.leakPoints=[{position:[0,0,0],observerAngles:[0,0]}, {position:[1,0,0],observerAngles:[45,0]}]
  const view=camera();view.updateWorldMatrix(true,false)
  const projected=new Vector3(1,0,0).project(view)
  expect(pickLeakPreviewPoint(root,view,{x:(projected.x+1)*viewport.width/2,y:200},viewport))
    .toEqual({candidateId:'group',observation:{position:[1,0,0],angles:[45,0]}})
})

it('chooses the closest dot on screen and the nearest depth for overlapping dots', () => {
  const { root } = dots([.1, 0, 0, 0, 0, 0, 0, 0, 1], ['offset', 'far', 'near'])
  expect(pickLeakPreviewCandidate(root, camera(), { x: 400, y: 200 }, viewport)).toBe('near')
})

it('ignores hidden, clipped-by-frustum, and unassociated points', () => {
  const { root, points } = dots([0, 0, 20, 0, 0, -1100, 0, 0, 0], ['behind', 'beyond', undefined])
  expect(pickLeakPreviewCandidate(root, camera(), { x: 400, y: 200 }, viewport)).toBeNull()
  points.userData.leakCandidateIds[2] = 'visible'
  points.visible = false
  expect(pickLeakPreviewCandidate(root, camera(), { x: 400, y: 200 }, viewport)).toBeNull()
  points.visible = true
  root.visible = false
  expect(pickLeakPreviewCandidate(root, camera(), { x: 400, y: 200 }, viewport)).toBeNull()
})

it('uses the rendered point mapping after filtering or removal and respects camera layers', () => {
  const { root, points } = dots([0, 0, 0], ['remaining-candidate'])
  const view = camera()
  expect(pickLeakPreviewCandidate(root, view, { x: 400, y: 200 }, viewport)).toBe('remaining-candidate')
  points.layers.set(1)
  expect(pickLeakPreviewCandidate(root, view, { x: 400, y: 200 }, viewport)).toBeNull()
  root.remove(points)
  expect(pickLeakPreviewCandidate(root, view, { x: 400, y: 200 }, viewport)).toBeNull()
})

it('projects transformed points with orthographic cameras in CSS viewport pixels', () => {
  const { root, points } = dots()
  root.position.set(2, 1, 0)
  points.position.x = 1
  const view = new OrthographicCamera(-10, 10, 5, -5, .1, 100)
  view.position.z = 10
  view.lookAt(new Vector3())
  expect(pickLeakPreviewCandidate(root, view, { x: 520, y: 160 }, viewport)).toBe('leak-one')
  expect(pickLeakPreviewCandidate(root, view, { x: 400, y: 200 }, viewport)).toBeNull()
})

it('rejects occluded dots before choosing an overlapping visible candidate', () => {
  const { root } = dots([0,0,0, 0,0,1], ['covered','visible'])
  const isVisible = (point: Vector3) => point.z === 1
  expect(pickLeakPreviewCandidate(root, camera(), {x:400,y:200}, viewport, isVisible)).toBe('visible')
  expect(pickLeakPreviewCandidate(root, camera(), {x:400,y:200}, viewport, () => false)).toBeNull()
})

it('selects a visible exit gradient as its Leak and rejects invisible tails and covered guides', () => {
  const root = new Group()
  const guide = new Mesh(exitGuideGeometry([-1,0,0],[1,0,0],.2,.6), new MeshBasicMaterial({ side: DoubleSide, vertexColors: true }))
  guide.userData.leakCandidateId = 'same-leak'
  root.add(guide)
  expect(pickLeakPreviewCandidate(root, camera(), { x: 400, y: 200 }, viewport)).toBe('same-leak')
  const view = camera()
  view.updateWorldMatrix(true, false)
  const tail = new Vector3(.99, 0, 0).project(view)
  expect(pickLeakPreviewCandidate(root, view, { x: (tail.x + 1) * viewport.width / 2, y: 200 }, viewport)).toBeNull()
  expect(pickLeakPreviewCandidate(root, camera(), { x: 400, y: 200 }, viewport, () => false)).toBeNull()
  guide.userData.leakCandidateId = undefined
  expect(pickLeakPreviewCandidate(root, camera(), { x: 400, y: 200 }, viewport)).toBeNull()
})
