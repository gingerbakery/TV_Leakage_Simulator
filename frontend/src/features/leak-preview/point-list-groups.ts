import type { LeakPreviewCandidate } from './leak-preview-model'
import { leakPathPattern, similarLeakPathPatterns } from './leak-path-pattern'

export type PointListGrouping = 'none' | 'route'
export interface PointListGroup { id: string; label: string; members: LeakPreviewCandidate[] }

/** Presentation only: never merge or discard optical evidence or candidates. */
export function pointListGroups(candidates: LeakPreviewCandidate[], mode: PointListGrouping, tolerance: number): PointListGroup[] {
  if (mode === 'none') return []
  const groups: PointListGroup[] = []
  const byStart = new Map<string, Array<{ pattern: ReturnType<typeof leakPathPattern>; group: PointListGroup }>>()
  const scale = Math.max(1, tolerance * 2)
  for (const candidate of candidates) {
    const pattern = leakPathPattern(candidate.representativePath, tolerance)
    let group: PointListGroup | undefined
    const cell = pattern?.start.map(value => Math.floor(value / scale))
    if (mode === 'route' && cell) {
      search: for (let x=-1; x<=1; x++) for (let y=-1; y<=1; y++) for (let z=-1; z<=1; z++) {
        for (const entry of byStart.get(`${cell[0]+x}:${cell[1]+y}:${cell[2]+z}`) ?? []) {
          if (similarLeakPathPatterns(pattern, entry.pattern, tolerance)) { group = entry.group; break search }
        }
      }
    }
    if (!group) {
      const id = `point-list-${candidate.id}`
      group = { id, label: `경로 그룹${groups.length+1}`, members: [] }
      groups.push(group)
      if (cell) {
        const key = cell.join(':')
        byStart.set(key, [...(byStart.get(key) ?? []), { pattern, group }])
      }
    }
    group.members.push(candidate)
  }
  return groups
}
