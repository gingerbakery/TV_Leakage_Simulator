import type { LeakPreviewCandidate, LeakPreviewPoint, ObserverProbeSummary } from './leak-preview-model'
import { createObserverObservationIndex, resolveObserverPoint } from './observer-point-selection'
import { verifiedLeakPreviewSurfacePoints } from './leak-preview-model'

export interface LeakDisplayFilter {
  maxReflections: number | null
  minNit: number
  minBrightnessRatio: number
  topPercent?: number
}
export const defaultLeakDisplayFilter: LeakDisplayFilter = { maxReflections: null, minNit: 0, minBrightnessRatio: 0 }

/** Display evidence only. Never change raw paths, cell sums or simulation inputs. */
export function filterLeakDisplay(points: LeakPreviewPoint[], candidates: LeakPreviewCandidate[],
  summary: ObserverProbeSummary | null, filter: LeakDisplayFilter) {
  const hasNit = summary?.measure === 'luminance_cd_m2'
  const minimumNit = hasNit ? filter.minNit : 0
  const topPercent = filter.topPercent ?? 100
  if (filter.maxReflections === null && minimumNit <= 0 && filter.minBrightnessRatio <= 0 && topPercent >= 100) return { points, candidates }
  const displayedPoints = summary ? verifiedLeakPreviewSurfacePoints(points, summary) : points
  const index = createObserverObservationIndex(displayedPoints, summary)
  const strength = (cell: ObserverProbeSummary['cells'][number]) =>
    hasNit ? (cell.luminance_nit ?? 0) : cell.direct_count + cell.reflected_count
  let maximumStrength = 0
  for (const entries of index.values()) for (const {cell} of entries.values()) maximumStrength = Math.max(maximumStrength, strength(cell))
  let maximumRelativeStrength = 0
  for (const candidate of candidates) maximumRelativeStrength = Math.max(maximumRelativeStrength, candidate.relativeStrength)
  for (const point of points) maximumRelativeStrength = Math.max(maximumRelativeStrength, point.relativeStrength)
  const minimumStrength = maximumStrength * filter.minBrightnessRatio
  const minimumRelativeStrength = maximumRelativeStrength * filter.minBrightnessRatio
  const visible = new Set<LeakPreviewPoint>()
  const displayed: LeakPreviewCandidate[] = []
  const scores = new Map<string, number>()
  for (const candidate of candidates) {
    const entries = index.get(candidate.id)
    if (!entries?.size) {
      // Legacy and partially audited results can have a candidate score but
      // no linked observation. Preserve the candidate without adding markers.
      const depth = candidate.representativePath ? candidate.representativePath.length-2 : candidate.maxReflectionCount
      if (candidate.relativeStrength < minimumRelativeStrength ||
        (minimumNit > 0 && (candidate.peakLuminanceNit ?? 0) < minimumNit) ||
        (filter.maxReflections !== null && (depth === null || depth > filter.maxReflections))) continue
      displayed.push(candidate)
      scores.set(candidate.id, hasNit ? candidate.peakLuminanceNit ?? candidate.relativeStrength : candidate.relativeStrength)
      continue
    }
    const observations = [...entries.values()].filter(({cell}) => {
      const depth = cell.representative_path ? cell.representative_path.length-2 : null
      return (filter.maxReflections === null || depth !== null && depth <= filter.maxReflections) &&
        (minimumNit <= 0 || (cell.luminance_nit ?? 0) >= minimumNit) && strength(cell) >= minimumStrength
    })
    if (!observations.length) continue
    let peak = 0
    for (const observation of observations) {
      visible.add(observation.point)
      peak = Math.max(peak, strength(observation.cell))
    }
    scores.set(candidate.id, peak)
    observations.sort((a,b) => Number(b.cell.representative_path?.length === 2)-Number(a.cell.representative_path?.length === 2) ||
      strength(b.cell)-strength(a.cell))
    displayed.push(resolveObserverPoint(candidate, observations[0].selection, points, summary, index)!)
  }
  const ranked = topPercent < 100 ? [...displayed]
    .sort((a, b) => scores.get(b.id)! - scores.get(a.id)!)
    .slice(0, Math.ceil(displayed.length * topPercent / 100)) : displayed
  const ids = new Set(ranked.map(candidate => candidate.id))
  if (!summary) {
    return { points: points.filter(point => point.candidateId && ids.has(point.candidateId) &&
      point.relativeStrength >= minimumRelativeStrength), candidates: ranked }
  }
  // The observation index owns validated copies of marker points. Compare by
  // identity fields so raw arrays remain untouched, including legacy markers.
  const key = (point: LeakPreviewPoint) => JSON.stringify([point.candidateId,point.position,point.observerAngles])
  const keys = new Set([...visible].map(key))
  return { points: displayedPoints.filter(point => point.candidateId && ids.has(point.candidateId) && keys.has(key(point))), candidates: ranked }
}
