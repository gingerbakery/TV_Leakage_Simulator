import { Box3, Matrix4, PerspectiveCamera, Vector3 } from 'three'
import type { LeakPreviewCandidate } from '@/features/leak-preview/leak-preview-model'

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

export function observerViewPose(candidate: LeakPreviewCandidate, bounds: Box3, aspect: number) {
  const evidence = candidate.observerEvidence
  const point = candidate.representativePath?.at(-2)
  if (!evidence || !point || bounds.isEmpty()) return null
  const outward = new Vector3(...evidence.direction)
  if (![...point, ...outward.toArray()].every(Number.isFinite) || outward.lengthSq() < 1e-12) return null
  outward.normalize()
  const target = new Vector3(...point)
  const up = new Vector3(...candidate.vAxis).normalize()
  const size = bounds.getSize(new Vector3()).length()
  const distance = target.distanceTo(bounds.getCenter(new Vector3())) + Math.max(size, 1) * 1.5
  return { target, up, position: target.clone().addScaledVector(outward, distance),
    height: Math.max(candidate.widthMm / Math.max(aspect, .1), candidate.heightMm, 10) * 3,
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
