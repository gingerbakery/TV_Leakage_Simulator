import type { RayTraceRequest } from '@/api'

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical)
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .filter(([,v])=>v!==undefined).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>[k,canonical(v)]))
  return value
}
/** Compare both server-frozen and live requests under the same JSON rules.
 * The server physical hash additionally identifies prepared geometry/materials.
 * Legacy results without a snapshot have unknown identity, never a match.
 */
export function sameComparisonInput(saved: RayTraceRequest|undefined, current: RayTraceRequest): boolean {
  if (!saved) return false
  const { project_name: _savedName, ...a } = saved
  const { project_name: _currentName, ...b } = current
  return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b))
}
