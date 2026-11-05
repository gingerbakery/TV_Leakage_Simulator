import { Camera, Plane, Raycaster, Vector2, Vector3 } from 'three'
import type { Vec3 } from '@/api'
import type { LeakPreviewBlocker } from '@/features/leak-preview/leak-preview-model'

export interface BlockerDrawingSurface { point: Vec3; normal: Vec3 }
export interface BlockerDrag { startX: number; startY: number; currentX: number; currentY: number }

export function projectBlockerDrag(camera: Camera, viewport: { width: number; height: number }, drag: BlockerDrag,
  blocker: LeakPreviewBlocker, surface: BlockerDrawingSurface | null) {
  if (!surface || viewport.width < 1 || viewport.height < 1 ||
    ![...surface.point, viewport.width, viewport.height, ...Object.values(drag)].every(Number.isFinite) ||
    drag.startX === drag.currentX || drag.startY === drag.currentY) return null
  // The CAD hit supplies depth only. Winding/slope must not tilt a screen box.
  const normal = camera.getWorldDirection(new Vector3()).negate().normalize()
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
  const u = new Vector3(...points[1]).sub(new Vector3(...points[0]))
  const v = new Vector3(...points[0]).sub(new Vector3(...points[3]))
  const widthMm = u.length(), heightMm = v.length()
  if (widthMm < .01 || heightMm < .01) return null
  u.normalize(); v.normalize()
  const center = new Vector3(...points[0]).add(new Vector3(...points[2])).multiplyScalar(.5)
  return { baseCenter: center.toArray() as Vec3, normal: normal.toArray() as Vec3,
    uAxis: u.toArray() as Vec3, vAxis: v.toArray() as Vec3, widthMm, heightMm,
    depthMm: blocker.depthMm, offsetMm: blocker.offsetMm, reverse: blocker.reverse }
}
