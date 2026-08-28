import { Box3, Plane, Vector3 } from 'three'

export type ViewerSectionAxis = 'x' | 'y' | 'z'

export interface ViewerSectionPlane {
  center: Vector3
  halfSpan: number
  normal: Vector3
  plane: Plane
  visualSize: number
}

export function viewerSectionAxisNormal(axis: ViewerSectionAxis): Vector3 {
  if (axis === 'x') return new Vector3(1, 0, 0)
  if (axis === 'y') return new Vector3(0, 1, 0)
  return new Vector3(0, 0, 1)
}

export function viewerSectionPlane(
  bounds: Box3,
  requestedNormal: Vector3,
  offsetRatio: number,
  reverse: boolean,
): ViewerSectionPlane | null {
  if (bounds.isEmpty()) return null
  const boundsCenter = bounds.getCenter(new Vector3())
  const boundsSize = bounds.getSize(new Vector3())
  const normal = requestedNormal.clone().normalize()
  if (normal.lengthSq() < 0.01) normal.set(0, 0, -1)
  let halfSpan = 0
  for (const x of [bounds.min.x, bounds.max.x]) {
    for (const y of [bounds.min.y, bounds.max.y]) {
      for (const z of [bounds.min.z, bounds.max.z]) {
        halfSpan = Math.max(
          halfSpan,
          Math.abs(new Vector3(x, y, z).sub(boundsCenter).dot(normal)),
        )
      }
    }
  }
  halfSpan = Math.max(halfSpan, Math.max(boundsSize.x, boundsSize.y, boundsSize.z) * 0.01, 1e-3)
  const center = boundsCenter
    .clone()
    .addScaledVector(normal, Math.max(-1, Math.min(1, offsetRatio)) * halfSpan)
  const clippingNormal = reverse ? normal.clone().negate() : normal.clone()
  return {
    center,
    halfSpan,
    normal,
    plane: new Plane(clippingNormal, -clippingNormal.dot(center)),
    visualSize: Math.max(boundsSize.length() * 1.25, 1),
  }
}
