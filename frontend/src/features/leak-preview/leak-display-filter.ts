import type { LeakPreviewCandidate, LeakPreviewPoint, ObserverProbeSummary } from './leak-preview-model'
import { createObserverObservationIndex, resolveObserverPoint } from './observer-point-selection'
import { verifiedLeakPreviewSurfacePoints } from './leak-preview-model'

export interface LeakDisplayFilter {
  maxReflections: number | null
  minNit: number
  minBrightnessRatio: number
}
export const defaultLeakDisplayFilter: LeakDisplayFilter = { maxReflections: null, minNit: 0, minBrightnessRatio: 0 }

/** Display evidence only. Never change raw paths, cell sums or simulation inputs. */
export function filterLeakDisplay(points: LeakPreviewPoint[], candidates: LeakPreviewCandidate[],
  summary: ObserverProbeSummary | null, filter: LeakDisplayFilter) {
  if (filter.maxReflections === null && filter.minNit <= 0 && filter.minBrightnessRatio <= 0) return { points, candidates }
  const displayedPoints = summary ? verifiedLeakPreviewSurfacePoints(points, summary) : points
  const index = createObserverObservationIndex(displayedPoints, summary)
  let maximumNit = 0
  for (const entries of index.values()) for (const {cell} of entries.values()) maximumNit = Math.max(maximumNit, cell.luminance_nit ?? 0)
  const minimumNit = Math.max(filter.minNit, maximumNit*filter.minBrightnessRatio)
  const needsNit = filter.minNit > 0 || filter.minBrightnessRatio > 0
  const visible = new Set<LeakPreviewPoint>()
  const displayed: LeakPreviewCandidate[] = []
  for (const candidate of candidates) {
    const observations = [...(index.get(candidate.id)?.values() ?? [])].filter(({cell}) => {
      const depth = cell.representative_path ? cell.representative_path.length-2 : null
      return (filter.maxReflections === null || depth !== null && depth <= filter.maxReflections) &&
        (!needsNit || summary?.measure === 'luminance_cd_m2' && cell.luminance_nit !== undefined && cell.luminance_nit >= minimumNit)
    })
    if (summary) {
      if (!observations.length) continue
      for (const observation of observations) visible.add(observation.point)
      observations.sort((a,b) => Number(b.cell.representative_path?.length === 2)-Number(a.cell.representative_path?.length === 2) ||
        (b.cell.luminance_nit ?? 0)-(a.cell.luminance_nit ?? 0))
      displayed.push(resolveObserverPoint(candidate, observations[0].selection, points, summary, index)!)
    } else {
      const depth = candidate.representativePath ? candidate.representativePath.length-2 : candidate.maxReflectionCount
      if (needsNit || filter.maxReflections !== null && (depth === null || depth > filter.maxReflections)) continue
      displayed.push(candidate)
    }
  }
  if (!summary) {
    const ids = new Set(displayed.map(candidate => candidate.id))
    return { points: points.filter(point => point.candidateId && ids.has(point.candidateId)), candidates: displayed }
  }
  // The observation index owns validated copies of marker points. Compare by
  // identity fields so raw arrays remain untouched, including legacy markers.
  const key = (point: LeakPreviewPoint) => JSON.stringify([point.candidateId,point.position,point.observerAngles])
  const keys = new Set([...visible].map(key))
  return { points: displayedPoints.filter(point => keys.has(key(point))), candidates: displayed }
}
