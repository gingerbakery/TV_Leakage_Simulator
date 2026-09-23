import type { RayTraceRequest, Vec3 } from '@/api'
import type {
  LeakPreviewBlocker,
  LeakPreviewIgnoreArea,
} from '@/features/leak-preview/leak-preview-model'

type TraceBlocker = NonNullable<RayTraceRequest['preview_blockers']>[number]
const allowedBoundaryThicknessMm = 0.1

function addScaled(origin: Vec3, axis: Vec3, scale: number): Vec3 {
  return [
    origin[0] + axis[0] * scale,
    origin[1] + axis[1] * scale,
    origin[2] + axis[2] * scale,
  ]
}

function blockerCenter(blocker: LeakPreviewBlocker): Vec3 {
  const direction = blocker.reverse
    ? blocker.normal.map((value) => -value) as Vec3
    : blocker.normal
  return addScaled(
    blocker.baseCenter,
    direction,
    blocker.offsetMm + blocker.depthMm / 2,
  )
}

export function buildPrecisionPreviewConstraints(
  blockers: LeakPreviewBlocker[],
  allowedAreas: LeakPreviewIgnoreArea[],
  applyBlockers: boolean,
  applyAllowedAreas: boolean,
): TraceBlocker[] {
  const result: TraceBlocker[] = []
  if (applyBlockers) {
    result.push(...blockers
      .filter((blocker) => blocker.enabled)
      .map((blocker) => ({
        blocker_id: `precision-blocker:${blocker.id}`,
        center: blockerCenter(blocker),
        u_axis: blocker.uAxis,
        v_axis: blocker.vAxis,
        normal: blocker.reverse
          ? blocker.normal.map((value) => -value) as Vec3
          : blocker.normal,
        width_mm: Math.max(blocker.widthMm, 1e-6),
        height_mm: Math.max(blocker.heightMm, 1e-6),
        depth_mm: Math.max(blocker.depthMm, 1e-6),
        enabled: true,
      })))
  }
  if (applyAllowedAreas) {
    for (const area of allowedAreas.filter((candidate) => candidate.enabled)) {
      area.regions.forEach((region, regionIndex) => {
        const zMin = region.zMin ?? 0
        const zMax = region.zMax ?? zMin
        const centerX = (region.xMin + region.xMax) / 2
        const centerY = (region.yMin + region.yMax) / 2
        const centerZ = (zMin + zMax) / 2
        const id = `precision-allowed-area:${area.id}:${regionIndex}`
        if (region.plane === 'xy') {
          for (const [side, z] of [['min', zMin], ['max', zMax]] as const) {
            result.push({
              blocker_id: `${id}:${side}`,
              center: [centerX, centerY, z],
              u_axis: [1, 0, 0], v_axis: [0, 1, 0], normal: [0, 0, 1],
              width_mm: Math.max(Math.abs(region.xMax - region.xMin), 1e-6),
              height_mm: Math.max(Math.abs(region.yMax - region.yMin), 1e-6),
              depth_mm: allowedBoundaryThicknessMm,
              enabled: true,
            })
          }
        } else if (region.plane === 'yz') {
          for (const [side, x] of [['min', region.xMin], ['max', region.xMax]] as const) {
            result.push({
              blocker_id: `${id}:${side}`,
              center: [x, centerY, centerZ],
              u_axis: [0, 1, 0], v_axis: [0, 0, 1], normal: [1, 0, 0],
              width_mm: Math.max(Math.abs(region.yMax - region.yMin), 1e-6),
              height_mm: Math.max(Math.abs(zMax - zMin), 1e-6),
              depth_mm: allowedBoundaryThicknessMm,
              enabled: true,
            })
          }
        } else if (region.plane === 'zx') {
          for (const [side, y] of [['min', region.yMin], ['max', region.yMax]] as const) {
            result.push({
              blocker_id: `${id}:${side}`,
              center: [centerX, y, centerZ],
              u_axis: [0, 0, 1], v_axis: [1, 0, 0], normal: [0, 1, 0],
              width_mm: Math.max(Math.abs(zMax - zMin), 1e-6),
              height_mm: Math.max(Math.abs(region.xMax - region.xMin), 1e-6),
              depth_mm: allowedBoundaryThicknessMm,
              enabled: true,
            })
          }
        } else {
          result.push({
            blocker_id: id,
            center: [centerX, centerY, centerZ],
            u_axis: [1, 0, 0], v_axis: [0, 1, 0], normal: [0, 0, 1],
            width_mm: Math.max(Math.abs(region.xMax - region.xMin), 1e-6),
            height_mm: Math.max(Math.abs(region.yMax - region.yMin), 1e-6),
            depth_mm: Math.max(Math.abs(zMax - zMin), 1e-6),
            enabled: true,
          })
        }
      })
    }
  }
  return result
}
