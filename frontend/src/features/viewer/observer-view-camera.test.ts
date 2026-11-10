import { expect, it } from 'vitest'
import { Box3, Raycaster, Vector2, Vector3 } from 'three'
import { ObserverViewCamera, captureObserverCamera, observerViewPose, restoreObserverCamera } from './observer-view-camera'
import { detectLeakPreviewCandidates } from '@/features/leak-preview/leak-preview-model'
import { createSceneFixture } from '@/test/scene-fixture'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'

it('uses parallel picking rays, preserves projection after resize, and restores the previous camera', () => {
  const camera = new ObserverViewCamera(45, 2, .01, 1000)
  camera.position.set(2, 3, 40)
  camera.up.set(0, 1, 0)
  const target = new Vector3(2, 3, 0)
  camera.lookAt(target)
  camera.zoom = 1.7
  camera.updateProjectionMatrix()
  const saved = captureObserverCamera(camera, target)
  camera.zoom = 1
  camera.viewHeight = 20
  camera.setParallel(true)
  camera.updateMatrixWorld(true)
  const first = new Raycaster(), second = new Raycaster()
  first.setFromCamera(new Vector2(-.5, 0), camera)
  second.setFromCamera(new Vector2(.5, 0), camera)
  expect(first.ray.direction.distanceTo(second.ray.direction)).toBeLessThan(1e-10)
  expect(first.ray.origin.distanceTo(second.ray.origin)).toBeCloseTo(20)
  camera.aspect = 1
  camera.updateProjectionMatrix()
  expect(camera.right - camera.left).toBe(20)
  camera.position.set(100, 200, 300)
  target.set(10, 10, 10)
  restoreObserverCamera(camera, target, saved)
  expect(camera.isPerspectiveCamera).toBe(true)
  expect(camera.isOrthographicCamera).toBe(false)
  expect(camera.position.toArray()).toEqual(saved.camera.position.toArray())
  expect(camera.quaternion.toArray()).toEqual(saved.camera.quaternion.toArray())
  expect(camera.zoom).toBe(1.7)
  expect(target.toArray()).toEqual(saved.target.toArray())
})

it('places the camera outside the model along the saved outward direction and centers the detected point', () => {
  const base = detectLeakPreviewCandidates(createSceneFixture(), createRayTraceResultFixture()).candidates[0]
  const bounds = new Box3(new Vector3(-50, -50, -50), new Vector3(50, 50, 50))
  const candidate = { ...base, vAxis: [0,1,0] as [number,number,number],
    representativePath: [[1,2,3], [1,2,100]] as [number,number,number][],
    observerEvidence: { ...base.observerEvidence!, direction: [0,0,1] as [number,number,number] } }
  const pose = observerViewPose(candidate, bounds, 2)!
  expect(pose.target.toArray()).toEqual([1,2,3])
  expect(pose.position.z).toBeGreaterThan(bounds.max.z)
  const camera = new ObserverViewCamera(45, 2, pose.near, pose.far)
  camera.position.copy(pose.position)
  camera.up.copy(pose.up)
  camera.lookAt(pose.target)
  camera.viewHeight = pose.height
  camera.setParallel(true)
  camera.updateMatrixWorld(true)
  const projected = pose.target.clone().project(camera)
  expect(projected.x).toBeCloseTo(0)
  expect(projected.y).toBeCloseTo(0)
  expect(observerViewPose({ ...candidate, observerEvidence: undefined }, bounds, 2)).toBeNull()
})
