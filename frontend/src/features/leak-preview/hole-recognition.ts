import type { ScenePayload, Vec3 } from '@/api'
import type { ComponentTransformRule } from '@/stores'
import { createLeakPreviewPointTransform } from './leak-preview-geometry'
import { HOLE_PLUG_DEPTH_MM, HOLE_PLUG_DIAMETER_SCALE } from './blocker-groups'
import type { LeakPreviewBlocker } from './leak-preview-model'

export interface CadHoleLoop {
  componentId: number
  points: Vec3[]
  placement: Partial<LeakPreviewBlocker> & { baseCenter: Vec3 }
}

export function pickRoundCadHole(
  holes: CadHoleLoop[], pointer: { x:number; y:number },
  project: (point:Vec3) => { x:number; y:number; z:number },
  isVisible: (hole:CadHoleLoop) => boolean,
): CadHoleLoop['placement'] | null {
  const matches = holes.flatMap((hole) => {
    const { widthMm = 0, heightMm = 0 } = hole.placement
    if (Math.abs(widthMm-heightMm) > Math.max(widthMm,heightMm)*.005) return []
    const center = project(hole.placement.baseCenter)
    if (center.z < -1 || center.z > 1) return []
    const radius = Math.max(...hole.points.map((p) => {
      const point = project(p)
      return Math.hypot(point.x-center.x, point.y-center.y)
    }))
    const distance = Math.hypot(center.x-pointer.x, center.y-pointer.y)
    return distance <= Math.max(12, radius*.9) ? [{ hole, distance, depth:center.z }] : []
  }).sort((a,b) => a.distance-b.distance || a.depth-b.depth)
  let chosen = matches.find(({ hole }) => isVisible(hole))
  if (!chosen) return null
  // A through hole has front/back rims at the same XY location. Perspective
  // can move their projected centers by a few pixels; always anchor the
  // nearest visible entrance, rather than whichever center is a pixel closer.
  for (const candidate of matches) {
    if (candidate.hole.componentId === chosen.hole.componentId && candidate.depth < chosen.depth &&
      Math.hypot(candidate.hole.placement.baseCenter[0]-chosen.hole.placement.baseCenter[0], candidate.hole.placement.baseCenter[1]-chosen.hole.placement.baseCenter[1]) < .01 &&
      Math.abs(candidate.hole.placement.widthMm!-chosen.hole.placement.widthMm!) < .02 && isVisible(candidate.hole)) chosen = candidate
  }
  const match = chosen.hole
  const diameter = Math.max(match.placement.widthMm!, match.placement.heightMm!) * HOLE_PLUG_DIAMETER_SCALE
  return { ...match.placement, widthMm:diameter, heightMm:diameter,
    uAxis:[1,0,0], vAxis:[0,1,0], normal:[0,0,1], reverse:false }
}
const tolerance = 1e-4

function leastSquares(rows: number[][], targets: number[]): number[] | null {
  const size = rows[0].length
  const matrix = Array.from({ length:size }, (_, i) => Array.from({ length:size+1 }, (_, j) =>
    rows.reduce((sum, row, k) => sum+row[i]*(j === size ? targets[k] : row[j]), 0)))
  for (let i = 0; i < size; i++) {
    let pivot = i
    for (let j = i+1; j < size; j++) if (Math.abs(matrix[j][i]) > Math.abs(matrix[pivot][i])) pivot = j
    if (Math.abs(matrix[pivot][i]) < 1e-12) return null
    ;[matrix[i],matrix[pivot]] = [matrix[pivot],matrix[i]]
    const divisor = matrix[i][i]
    for (let j = i; j <= size; j++) matrix[i][j] /= divisor
    for (let row = 0; row < size; row++) if (row !== i) {
      const factor = matrix[row][i]
      for (let j = i; j <= size; j++) matrix[row][j] -= factor*matrix[i][j]
    }
  }
  return matrix.map((row) => row[size])
}

function fitLoop(points: Vec3[]): CadHoleLoop['placement'] | null {
  if (points.length < 5) return null
  const mean = points.reduce((sum, p) => sum.map((v, i) => v + p[i] / points.length) as Vec3, [0, 0, 0] as Vec3)
  if (points.some((p) => Math.abs(p[2] - mean[2]) > tolerance)) return null
  const unit = Math.max(...points.map((p) => Math.hypot(p[0]-mean[0], p[1]-mean[1])))
  if (unit < .01) return null
  const local = points.map((p) => [(p[0]-mean[0])/unit, (p[1]-mean[1])/unit])
  const circle = leastSquares(local.map(([x,y]) => [2*x,2*y,1]), local.map(([x,y]) => x*x+y*y))
  if (!circle) return null
  let cx = circle[0], cy = circle[1], angle = 0
  const radius = Math.sqrt(circle[2]+cx*cx+cy*cy)
  let radii = [radius*unit,radius*unit]
  if (!Number.isFinite(radius) || local.some(([x,y]) => Math.abs(Math.hypot(x-cx,y-cy)/radius-1) > 1e-4)) {
    if (points.length < 8) return null
    const conic = leastSquares(local.map(([x,y]) => [x*x,x*y,y*y,x,y]), local.map(() => 1))
    if (!conic) return null
    const [a,b,c,d,e] = conic, det = a*c-b*b/4
    if (a <= 0 || c <= 0 || det <= 1e-10) return null
    cx = -(c*d-b*e/2)/(2*det); cy = -(a*e-b*d/2)/(2*det)
    const k = 1+a*cx*cx+b*cx*cy+c*cy*cy, spread = Math.hypot(a-c,b)
    radii = [Math.sqrt(k/((a+c-spread)/2))*unit, Math.sqrt(k/((a+c+spread)/2))*unit]
    angle = .5*Math.atan2(b,a-c)+Math.PI/2
  }
  const u: Vec3 = [Math.cos(angle), Math.sin(angle), 0], v: Vec3 = [-u[1], u[0], 0]
  const center: Vec3 = [mean[0]+cx*unit,mean[1]+cy*unit,mean[2]]
  const projected = points.map((p) => [(p[0]-center[0])*u[0]+(p[1]-center[1])*u[1], (p[0]-center[0])*v[0]+(p[1]-center[1])*v[1]])
  if (!radii.every(Number.isFinite) || Math.min(...radii) < .01 || Math.min(...radii)/Math.max(...radii) < .1) return null
  const angles: number[] = []
  let maxRadius = 1
  for (const p of projected) {
    const x = p[0]/radii[0], y = p[1]/radii[1]
    if (Math.abs(x*x+y*y-1) > .035) return null
    maxRadius = Math.max(maxRadius, Math.hypot(x,y))
    angles.push(Math.atan2(y, x))
  }
  // Reject sparse, self-crossing, or incomplete outlines, even if their
  // vertices happen to lie on an ellipse.
  let winding = 0
  for (let i = 0; i < angles.length; i++) {
    const delta = Math.atan2(Math.sin(angles[(i+1)%angles.length]-angles[i]), Math.cos(angles[(i+1)%angles.length]-angles[i]))
    if (Math.abs(delta) > Math.PI/2 + 1e-6 || (winding && delta*winding < 0)) return null
    winding += delta
  }
  if (Math.abs(Math.abs(winding) - Math.PI*2) > .01) return null
  // The trace bridge uses a 48-sided cylinder. Circumscribe the CAD rim
  // instead of leaving small gaps between its vertices and the plug facets.
  const scale = maxRadius/Math.cos(Math.PI/48)
  return { shape: 'ellipse', baseCenter: center,
    uAxis: u, vAxis: v, normal: [0,0,1], widthMm: radii[0]*2*scale+.002, heightMm: radii[1]*2*scale+.002,
    depthMm: HOLE_PLUG_DEPTH_MM, offsetMm: -HOLE_PLUG_DEPTH_MM/2, reverse: false }
}

/** Conservatively recognizes Z-axis round/elliptical openings, not arbitrary
 * CAD holes. A closed edge loop alone is insufficient: its center must be
 * empty at the rim plane on the same component. Interior probes also reject
 * outer rims of annular parts, whose centers alone would look like a hole.
 */
export function findCadHoleLoops(scene: ScenePayload, transforms: ComponentTransformRule[] = [], unavailable: number[] = []): CadHoleLoop[] {
  const hidden = new Set(unavailable)
  const transform = createLeakPreviewPointTransform(scene, transforms)
  const groups = new Map<number, { points: Vec3[]; links: Map<number, Set<number>>; buckets: Map<string, number[]> }>()
  for (const edge of scene.mesh.feature_edge_segments) {
    const id = edge.component_id
    if (id === null || hidden.has(id)) continue
    const a = transform(id, edge.start), b = transform(id, edge.end)
    if (Math.abs(a[2]-b[2]) > tolerance) continue
    let group = groups.get(id)
    if (!group) { group = { points: [], links: new Map(), buckets: new Map() }; groups.set(id, group) }
    const keyOf = (p: Vec3) => {
      const cell = p.map((v) => Math.floor(v/tolerance))
      for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) for (let z = -1; z <= 1; z++) {
        for (const candidate of group!.buckets.get(`${cell[0]+x},${cell[1]+y},${cell[2]+z}`) ?? []) {
          if (Math.hypot(...p.map((v, i) => v-group!.points[candidate][i])) <= tolerance) return candidate
        }
      }
      const index = group!.points.length, key = cell.join(',')
      group!.points.push(p)
      group!.buckets.set(key, [...(group!.buckets.get(key) ?? []), index])
      return index
    }
    const ia = keyOf(a), ib = keyOf(b)
    if (ia === ib) continue
    if (!group.links.has(ia)) group.links.set(ia, new Set())
    if (!group.links.has(ib)) group.links.set(ib, new Set())
    group.links.get(ia)!.add(ib); group.links.get(ib)!.add(ia)
  }
  const candidates: CadHoleLoop[] = []
  for (const [componentId, group] of groups) {
    const visited = new Set<number>()
    for (const start of group.links.keys()) {
      if (visited.has(start)) continue
      const indices: number[] = [], pending = [start]
      while (pending.length) {
        const key = pending.pop()!
        if (visited.has(key)) continue
        visited.add(key); indices.push(key)
        for (const next of group.links.get(key) ?? []) if (!visited.has(next)) pending.push(next)
      }
      if (indices.some((key) => group.links.get(key)!.size !== 2)) continue
      const ordered = [start]
      let previous = -1, current = start
      do {
        const next = [...group.links.get(current)!].find((key) => key !== previous)!
        previous = current; current = next
        if (current !== start) ordered.push(current)
      } while (current !== start && ordered.length <= indices.length)
      if (current !== start || ordered.length !== indices.length) continue
      const points = ordered.map((key) => group.points[key]), placement = fitLoop(points)
      if (placement) candidates.push({ componentId, points, placement })
    }
  }
  if (!candidates.length) return []
  const candidateComponentIds = new Set(candidates.map((hole) => hole.componentId))
  // Index candidate centers and rims. Each planar triangle tests the few holes
  // inside its XY bounds, rather than scanning the full mesh per hole.
  const cellSize = Math.max(.1, ...candidates.map((hole) => Math.max(hole.placement.widthMm!, hole.placement.heightMm!)))
  const byCell = new Map<string, Set<number>>(), blocked = new Set<number>(), supported = new Set<number>()
  const interiorProbes = candidates.map((hole) => [hole.placement.baseCenter, ...hole.points.map((p) =>
    p.map((v, i) => hole.placement.baseCenter[i]+(v-hole.placement.baseCenter[i])*.99) as Vec3)])
  candidates.forEach((hole, index) => {
    for (const p of [...interiorProbes[index], ...hole.points]) {
      const key = `${hole.componentId}:${Math.floor(p[0]/cellSize)},${Math.floor(p[1]/cellSize)}`
      if (!byCell.has(key)) byCell.set(key, new Set())
      byCell.get(key)!.add(index)
    }
  })
  scene.mesh.faces.forEach((face, faceId) => {
    const id = scene.mesh.face_component_ids[faceId]
    if (id === null || !candidateComponentIds.has(id)) return
    const triangle = face.map((index) => transform(id, scene.mesh.vertices[index]))
    if (Math.max(...triangle.map((p) => p[2]))-Math.min(...triangle.map((p) => p[2])) > tolerance) return
    const cross = (a: Vec3, b: Vec3, x: Vec3) => (b[0]-a[0])*(x[1]-a[1])-(b[1]-a[1])*(x[0]-a[0])
    if (Math.abs(cross(triangle[0], triangle[1], triangle[2])) < 1e-12) return
    const x0 = Math.floor(Math.min(...triangle.map((p) => p[0]))/cellSize), x1 = Math.floor(Math.max(...triangle.map((p) => p[0]))/cellSize)
    const y0 = Math.floor(Math.min(...triangle.map((p) => p[1]))/cellSize), y1 = Math.floor(Math.max(...triangle.map((p) => p[1]))/cellSize)
    const indices = new Set<number>()
    if ((x1-x0+1)*(y1-y0+1) > candidates.length*4) {
      candidates.forEach((hole, index) => { if (hole.componentId === id) indices.add(index) })
    } else for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) for (const index of byCell.get(`${id}:${x},${y}`) ?? []) indices.add(index)
    for (const index of indices) {
      if (blocked.has(index)) continue
      const p = candidates[index].placement.baseCenter
      if (Math.abs(p[2]-triangle[0][2]) > tolerance) continue
      const contains = (point: Vec3) => {
        const signs = triangle.map((a, i) => cross(a, triangle[(i+1)%3], point))
        return signs.every((v) => v >= -1e-9) || signs.every((v) => v <= 1e-9)
      }
      if (interiorProbes[index].some(contains)) blocked.add(index)
      else if (!supported.has(index) && candidates[index].points.some(contains)) supported.add(index)
    }
  })
  return candidates.filter((_, index) => !blocked.has(index) && supported.has(index))
}
