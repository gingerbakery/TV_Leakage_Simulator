import { Camera, Plane, Raycaster, Vector2, Vector3 } from 'three'
import type { Vec3 } from '@/api'
import { resizeLeakPreviewBlockerOnFace, type LeakPreviewBlocker } from '@/features/leak-preview/leak-preview-model'

export interface BlockerDrawingSurface { point: Vec3; normal: Vec3 }
export interface BlockerDrag { startX: number; startY: number; currentX: number; currentY: number }

export function projectBlockerDrag(camera: Camera, viewport: { width: number; height: number }, drag: BlockerDrag,
  blocker: LeakPreviewBlocker, surface: BlockerDrawingSurface | null) {
  if (!surface || viewport.width < 1 || viewport.height < 1 || Math.hypot(...surface.normal) < 1e-12) return null
  const normal = new Vector3(...surface.normal).normalize()
  const plane = new Plane().setFromNormalAndCoplanarPoint(normal, new Vector3(...surface.point))
  const caster = new Raycaster()
  const x0 = Math.min(drag.startX, drag.currentX), x1 = Math.max(drag.startX, drag.currentX)
  const y0 = Math.min(drag.startY, drag.currentY), y1 = Math.max(drag.startY, drag.currentY)
  const points: Vec3[] = []
  for (const [x,y] of [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]) {
    caster.setFromCamera(new Vector2(x/viewport.width*2-1, -y/viewport.height*2+1), camera)
    const point = caster.ray.intersectPlane(plane, new Vector3())
    if (!point) return null
    points.push([point.x, point.y, point.z])
  }
  const unitNormal: Vec3 = [normal.x, normal.y, normal.z]
  const patch = resizeLeakPreviewBlockerOnFace({ ...blocker, baseCenter: surface.point, normal: unitNormal }, points)
  return patch ? { ...patch, normal: unitNormal } : null
}
