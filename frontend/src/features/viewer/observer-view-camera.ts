import { Box3, Matrix4, PerspectiveCamera, Vector3 } from 'three'
import type { LeakPreviewCandidate } from '@/features/leak-preview/leak-preview-model'
import type { ReceiverSpec } from '@/api'

/** One navigation camera with switchable projection, shared by rendering and picking. */
export class ObserverViewCamera extends PerspectiveCamera {
  override isPerspectiveCamera = true
  isOrthographicCamera = false
  left = -1
  right = 1
  top = 1
  bottom = -1
  viewHeight = 1

  setParallel(enabled: boolean) {
    this.isOrthographicCamera = enabled
    this.isPerspectiveCamera = !enabled
    this.updateProjectionMatrix()
  }

  override updateProjectionMatrix() {
    if (!this.isOrthographicCamera) { super.updateProjectionMatrix(); return }
    this.top = this.viewHeight / 2
    this.bottom = -this.top
    this.right = this.top * this.aspect
    this.left = -this.right
    this.projectionMatrix.copy(new Matrix4().makeOrthographic(
      this.left / this.zoom, this.right / this.zoom, this.top / this.zoom,
      this.bottom / this.zoom, this.near, this.far,
    ))
    this.projectionMatrixInverse.copy(this.projectionMatrix).invert()
  }
}

export function observerViewPose(candidate: LeakPreviewCandidate, bounds: Box3, aspect: number, receiver?: ReceiverSpec) {
  const evidence = candidate.observerEvidence
  const point = candidate.representativePath?.at(-2)
  if (!evidence || !point || bounds.isEmpty()) return null
  const outward = new Vector3(...evidence.direction)
  if (![...point, ...outward.toArray()].every(Number.isFinite) || outward.lengthSq() < 1e-12) return null
  outward.normalize()
  const target = new Vector3(...point)
  // Project world X into the image plane to define horizontal, rather than
  // inheriting the arbitrary U/V basis of the reverse-tracing sampler.
  const right = new Vector3(1, 0, 0).addScaledVector(outward, -outward.x)
  if (right.lengthSq() < 1e-8) right.set(0, 0, 1).addScaledVector(outward, -outward.z)
  right.normalize()
  const up = new Vector3().crossVectors(outward, right).normalize()
  if (up.y < 0) { up.negate(); right.negate() }
  const size = bounds.getSize(new Vector3()).length()
  let support = 0, halfWidth = 0, halfHeight = 0
  for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) {
    const relative = new Vector3(x, y, z).sub(target)
    support = Math.max(support, relative.dot(outward))
    halfWidth = Math.max(halfWidth, Math.abs(relative.dot(right)))
    halfHeight = Math.max(halfHeight, Math.abs(relative.dot(up)))
  }
  const receiverDistance = receiver ? new Vector3(...receiver.center).sub(target).dot(outward) : 0
  const distance = Math.max(receiverDistance, support + Math.max(receiver?.view_distance_mm ?? 3, .01))
  return { target, up, position: target.clone().addScaledVector(outward, distance),
    height: Math.max(halfWidth / Math.max(aspect, .1), halfHeight, .5) * 2.24,
    near: .01, far: distance + Math.max(size, 1) * 3 }
}

export function captureObserverCamera(camera: ObserverViewCamera, target: Vector3) {
  return { camera: camera.clone(), target: target.clone(), parallel: camera.isOrthographicCamera, height: camera.viewHeight }
}

export function restoreObserverCamera(camera: ObserverViewCamera, target: Vector3, saved: ReturnType<typeof captureObserverCamera>) {
  camera.copy(saved.camera)
  camera.viewHeight = saved.height
  camera.setParallel(saved.parallel)
  target.copy(saved.target)
}
