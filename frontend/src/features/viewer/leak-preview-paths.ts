import type { Vec3 } from '@/api'
import type { LeakPreviewCandidate } from '@/features/leak-preview/leak-preview-model'

export function leakPreviewArrowSegments(candidate: LeakPreviewCandidate, observerLength: number) {
  const path = candidate.representativePath
  const segments: { start: Vec3; end: Vec3; kind: 'ray' | 'observer' }[] = []
  if (path) for (let i = 1; i < path.length; i++) segments.push({ start: path[i-1], end: path[i], kind: 'ray' })
  // Direction annotation starts at the stored observer endpoint, outside the
  // traced segment. Never extend an unverified guide through assembly solids.
  if (!path || path.length < 2) return segments
  const origin = path.at(-1)!
  const previous = path.at(-2)!
  const raw = origin.map((value, i) => value - previous[i])
  const norm = Math.hypot(...raw)
  if (norm > 1e-12) segments.push({ start: origin, end: origin.map((value, i) => value + raw[i]/norm * observerLength) as Vec3, kind: 'observer' })
  return segments
}
