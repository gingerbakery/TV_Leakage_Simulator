import type { Vec3 } from '@/api'
import type { LeakPreviewCandidate } from '@/features/leak-preview/leak-preview-model'

export function leakPreviewArrowSegments(candidate: LeakPreviewCandidate, observerLength: number) {
  const path = candidate.representativePath
  const segments: { start: Vec3; end: Vec3; kind: 'ray' | 'observer' }[] = []
  if (path) for (let i = 1; i < path.length; i++) segments.push({ start: path[i-1], end: path[i], kind: 'ray' })
  const origin = path?.at(-2) ?? candidate.center
  const raw = candidate.observerEvidence?.direction ?? (path && path.length >= 2
    ? path.at(-1)!.map((value, i) => value - origin[i]) : candidate.normal.map((value) => -value))
  const norm = Math.hypot(...raw)
  if (norm > 1e-12) segments.push({ start: origin, end: origin.map((value, i) => value + raw[i]/norm * observerLength) as Vec3, kind: 'observer' })
  return segments
}
