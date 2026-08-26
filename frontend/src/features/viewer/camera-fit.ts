import {
  Box3,
  MathUtils,
  PerspectiveCamera,
  Vector3,
} from 'three'

export interface CameraFitResult {
  target: Vector3
  distance: number
}

export function fitPerspectiveCameraToBounds(
  camera: PerspectiveCamera,
  bounds: Box3,
  aspect: number,
  cameraOffsetDirection: Vector3,
  requestedUp: Vector3,
  margin = 1.12,
): CameraFitResult {
  const target = bounds.getCenter(new Vector3())
  const size = bounds.getSize(new Vector3())
  const maxDimension = Math.max(size.x, size.y, size.z, 1)
  const offsetDirection = cameraOffsetDirection.clone().normalize()
  if (offsetDirection.lengthSq() < 0.01) {
    offsetDirection.set(1, 1, 1).normalize()
  }
  const forward = offsetDirection.clone().negate()
  let upHint = requestedUp.clone().normalize()
  if (Math.abs(forward.dot(upHint)) > 0.995) {
    upHint = Math.abs(forward.y) < 0.995
      ? new Vector3(0, 1, 0)
      : new Vector3(0, 0, 1)
  }
  const right = new Vector3().crossVectors(forward, upHint).normalize()
  const up = new Vector3().crossVectors(right, forward).normalize()
  const verticalTangent = Math.max(
    Math.tan(MathUtils.degToRad(camera.fov) / 2),
    1e-4,
  )
  const horizontalTangent = verticalTangent * Math.max(aspect, 0.1)
  let distance = maxDimension * 0.05

  for (const x of [bounds.min.x, bounds.max.x]) {
    for (const y of [bounds.min.y, bounds.max.y]) {
      for (const z of [bounds.min.z, bounds.max.z]) {
        const relative = new Vector3(x, y, z).sub(target)
        const towardCamera = relative.dot(offsetDirection)
        distance = Math.max(
          distance,
          towardCamera +
            (Math.abs(relative.dot(right)) * margin) / horizontalTangent,
          towardCamera +
            (Math.abs(relative.dot(up)) * margin) / verticalTangent,
        )
      }
    }
  }

  distance = Math.max(distance, maxDimension * 0.05)
  camera.aspect = aspect
  camera.position.copy(target).addScaledVector(offsetDirection, distance)
  camera.up.copy(up)
  camera.near = Math.max(distance / 1000, 0.01)
  camera.far = Math.max(distance * 20, maxDimension * 10, 1000)
  camera.lookAt(target)
  camera.updateProjectionMatrix()
  camera.updateMatrixWorld(true)
  return { target, distance }
}
