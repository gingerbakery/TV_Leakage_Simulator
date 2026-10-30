import type { RoiScope } from '@/stores'
import type { LeakPreviewPoint } from '@/features/leak-preview/leak-preview-model'
import { emitterPointInRoi } from './emitter-roi-display'

/** Display filtering only: preserve the detection result for ROI toggles. */
export function leakPreviewPointsInRoi(points: LeakPreviewPoint[], scopes: RoiScope[]): LeakPreviewPoint[] {
  const boxes = scopes.flatMap((scope) => scope.active && scope.clipBox ? [scope.clipBox] : [])
  if (boxes.length === 0) return points
  return points.filter(({ position: [x, y, z] }) => emitterPointInRoi({ x, y, z }, boxes))
}
