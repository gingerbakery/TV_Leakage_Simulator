import type { Vec3 } from '@/api'

const distance = (a: Vec3, b: Vec3) => Math.hypot(...a.map((v,i) => v-b[i]))
export function leakPathPattern(path: Vec3[] | undefined, tolerance: number) {
  if (!path || path.length < 2) return null
  // Camera location varies with viewing angle. Preserve the interior route.
  const route = path.slice(0,-1)
  const simplified: Vec3[] = []
  for (const point of route) {
    if (simplified.length && distance(point, simplified.at(-1)!) < 1e-8) continue
    while (simplified.length >= 2) {
      const a = simplified.at(-2)!, b = simplified.at(-1)!
      const ab = b.map((v,i) => v-a[i]), ac = point.map((v,i) => v-a[i])
      const length2 = ac.reduce((s,v) => s+v*v, 0)
      const t = length2 ? ab.reduce((s,v,i) => s+v*ac[i],0)/length2 : -1
      const error = Math.hypot(...ab.map((v,i) => v-t*ac[i]))
      if (t <= 0 || t >= 1 || error > tolerance*.1) break
      simplified.pop()
    }
    simplified.push(point)
  }
  if (!simplified.length) return null
  const lengths = simplified.slice(1).map((p,i) => distance(p,simplified[i]))
  const total = lengths.reduce((s,v) => s+v,0)
  const samples = Array.from({length: 7}, (_,index) => {
    let position = total*index/6
    for (let j=0; j<lengths.length; j++) {
      if (position <= lengths[j] || j === lengths.length-1) {
        const fraction = lengths[j] ? position/lengths[j] : 0
        return simplified[j].map((v,i) => v+(simplified[j+1][i]-v)*fraction) as Vec3
      }
      position -= lengths[j]
    }
    return simplified[0]
  })
  return { vertices: simplified.length, start: simplified[0], end: simplified.at(-1)!, samples }
}
export function similarLeakPathPatterns(a: ReturnType<typeof leakPathPattern>, b: ReturnType<typeof leakPathPattern>, tolerance: number) {
  // Sampling compares the physical route, independent of how many vertices
  // the representative ray retained (including small reflection variations).
  return !!a && !!b && distance(a.end,b.end) <= tolerance && distance(a.start,b.start) <= tolerance*2 &&
    a.samples.every((p,i) => distance(p,b.samples[i]) <= tolerance*2)
}
