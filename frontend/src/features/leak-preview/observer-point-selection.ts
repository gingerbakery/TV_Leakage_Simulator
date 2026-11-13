import type { Vec3 } from '@/api'
import { leakPreviewRoiMinimumSizeMm, verifiedLeakPreviewSurfacePoints, type LeakPreviewCandidate, type LeakPreviewPoint, type ObserverProbeSummary } from './leak-preview-model'

export interface ObserverPointSelection { position: Vec3; angles: [number, number] }
const key = (position: Vec3, angles: [number, number]) => JSON.stringify([position, angles])

type Observation = { cell: ObserverProbeSummary['cells'][number]; view: ObserverProbeSummary['views'][number];
  point: LeakPreviewPoint; selection: ObserverPointSelection }
export type ObserverObservationIndex = Map<string, Map<string, Observation>>

/** Build once per result/point array; selecting a Point must not rescan the CAD
 * result or reconstruct endpoint maps for every mouse click. */
export function createObserverObservationIndex(points: LeakPreviewPoint[], summary: ObserverProbeSummary | null): ObserverObservationIndex {
  const index: ObserverObservationIndex = new Map()
  if (!summary) return index
  const endpoints = new Map<string, LeakPreviewPoint[]>()
  for (const point of verifiedLeakPreviewSurfacePoints(points, summary)) {
    if (!point.candidateId || !point.observerAngles) continue
    const identity = key(point.position, point.observerAngles)
    endpoints.set(identity, [...(endpoints.get(identity) ?? []), point])
  }
  for (const cell of summary.cells) {
    const view=summary.views[cell.view_index], position=cell.representative_path?.at(-2)
    if (cell.path_validation?.status !== 'valid' || !view || !position) continue
    const angles: [number,number]=[view.horizontal_deg,view.vertical_deg], identity=key(position,angles)
    for (const point of endpoints.get(identity) ?? []) {
      let entries=index.get(point.candidateId!)
      if (!entries) { entries=new Map(); index.set(point.candidateId!,entries) }
      const previous=entries.get(identity)
      if (!previous || (cell.luminance_nit ?? 0) > (previous.cell.luminance_nit ?? 0)) entries.set(identity,{cell,view,point,selection:{position,angles}})
    }
  }
  return index
}

export function candidateObservations(candidateId: string, points: LeakPreviewPoint[], summary: ObserverProbeSummary | null, index?: ObserverObservationIndex) {
  const entries = (index ?? createObserverObservationIndex(points,summary)).get(candidateId)
  return entries ? [...entries.values()] : []
}

/** Highest retained cell mean, never a claim about untested angles or all paths. */
export function highestObservedLuminance(candidateId: string, points: LeakPreviewPoint[], summary: ObserverProbeSummary | null, index?: ObserverObservationIndex): ObserverPointSelection | null {
  if (summary?.measure !== 'luminance_cd_m2') return null
  const observations = candidateObservations(candidateId, points, summary, index)
  observations.sort((a,b) => (b.cell.luminance_nit ?? 0) - (a.cell.luminance_nit ?? 0))
  return observations[0]?.selection ?? null
}

/** Resolve a transient selection without rewriting grouped/raw optical results. */
export function resolveObserverPoint(candidate: LeakPreviewCandidate | undefined, selection: ObserverPointSelection | null,
  points: LeakPreviewPoint[], summary: ObserverProbeSummary | null, index?: ObserverObservationIndex): LeakPreviewCandidate | undefined {
  if (!candidate || !selection || !summary || !candidate.observerEvidence) return candidate
  const match = (index ?? createObserverObservationIndex(points,summary)).get(candidate.id)?.get(key(selection.position, selection.angles))
  if (!match) return candidate
  const {cell,view} = match
  const center = cell.aperture?.position ?? selection.position
  const resolution = view.grid_resolution ?? [summary.settings.resolution, summary.settings.resolution]
  const footprint = view.observer_center.map((value,i) => value +
    view.u_axis[i]*(cell.column+.5-resolution[0]/2)*view.spacing_mm[0] +
    view.v_axis[i]*(cell.row+.5-resolution[1]/2)*view.spacing_mm[1]) as Vec3
  return { ...candidate, center, representativePath: cell.representative_path,
    apertureId: cell.aperture?.id, aperturePosition: cell.aperture?.position,
    apertureMethod: cell.aperture?.method, apertureVariationMm: cell.aperture?.uncertainty_mm,
    normal: view.direction.map(value => -value) as Vec3, uAxis: view.u_axis, vAxis: view.v_axis,
    widthMm: Math.max(leakPreviewRoiMinimumSizeMm, view.spacing_mm[0]+10),
    heightMm: Math.max(leakPreviewRoiMinimumSizeMm, view.spacing_mm[1]+10),
    peakLuminanceNit: cell.luminance_nit, peakDirectLuminanceNit: cell.direct_luminance_nit,
    peakReflectedLuminanceNit: cell.reflected_luminance_nit,
    observerEvidence: { ...candidate.observerEvidence, horizontalDeg: view.horizontal_deg, verticalDeg: view.vertical_deg,
      direction: view.direction, receiverCenter: footprint, footprintCenter: footprint,
      footprintWidthMm: view.spacing_mm[0], footprintHeightMm: view.spacing_mm[1],
      directCount: cell.direct_count, reflectedCount: cell.reflected_count } }
}
