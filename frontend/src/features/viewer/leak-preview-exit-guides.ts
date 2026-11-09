import { BufferGeometry, Float32BufferAttribute, Vector3 } from 'three'
import type { Vec3 } from '@/api'
import type { LeakPreviewPoint, ObserverProbeSummary } from '@/features/leak-preview/leak-preview-model'

/** A display guide along an audited clear segment, never a new optical ray. */
export function leakPreviewExitGuides(points: LeakPreviewPoint[], summary: ObserverProbeSummary | null, maxLength: number) {
  const visible = new Map(points.map(point => [JSON.stringify([point.position, point.observerAngles]), point]))
  const guides = new Map<string, { start: Vec3; end: Vec3; strength: number; candidateId?: string }>()
  for (const cell of summary?.cells ?? []) {
    if (cell.path_validation?.status !== 'valid' || !cell.aperture) continue
    const path = cell.representative_path, view = summary!.views[cell.view_index]
    if (!path || path.length < 2 || !view) continue
    const point = visible.get(JSON.stringify([path.at(-2), [view.horizontal_deg, view.vertical_deg]]))
    if (!point) continue
    const inside = new Vector3(...path.at(-2)!), observer = new Vector3(...path.at(-1)!)
    const delta = observer.clone().sub(inside), length = delta.length()
    if (length < 1e-8) continue
    const direction = delta.divideScalar(length), exit = new Vector3(...cell.aperture.position)
    const distance = exit.clone().sub(inside).dot(direction)
    // Fail closed on off-path or out-of-segment provenance. Do not invent exits.
    if (distance <= 0 || distance >= length || inside.clone().addScaledVector(direction, distance).distanceTo(exit) > 1e-4) continue
    const end = exit.clone().addScaledVector(direction, Math.min(maxLength, length - distance))
    const key = JSON.stringify([cell.aperture.id, point.observerAngles, point.candidateId])
    const guide = { start: exit.toArray() as Vec3, end: end.toArray() as Vec3, strength: point.relativeStrength, candidateId: point.candidateId }
    if (!guides.has(key) || guide.strength > guides.get(key)!.strength) guides.set(key, guide)
  }
  return [...guides.values()]
}

/** Two crossed soft ribbons; alpha fades both across and along the segment. */
export function exitGuideGeometry(start: Vec3, end: Vec3, width: number, opacity: number): BufferGeometry {
  const origin = new Vector3(...start), delta = new Vector3(...end).sub(origin)
  const direction = delta.clone().normalize()
  const reference = Math.abs(direction.y) < .9 ? new Vector3(0, 1, 0) : new Vector3(1, 0, 0)
  const u = direction.clone().cross(reference).normalize(), v = direction.clone().cross(u).normalize()
  const positions: number[] = [], colors: number[] = []
  for (const side of [u, v]) for (let step = 0; step < 12; step++) for (let band = 0; band < 2; band++) {
    const vertices = [[step, band], [step + 1, band], [step + 1, band + 1], [step, band], [step + 1, band + 1], [step, band + 1]]
    for (const [s, b] of vertices) {
      const t = s / 12, cross = b - 1
      const position = origin.clone().addScaledVector(delta, t).addScaledVector(side, cross * width * (.75 + .25 * t))
      positions.push(...position.toArray())
      colors.push(1, .48, .06, (b === 1 ? 1 : 0) * (1 - t) ** 2 * opacity)
    }
  }
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new Float32BufferAttribute(positions, 3))
  geometry.setAttribute('color', new Float32BufferAttribute(colors, 4))
  return geometry
}
