import { ShapeUtils, Vector2, Vector3 } from 'three'

import type { ScenePayload } from '@/api'

interface Segment3 {
  a: Vector3
  b: Vector3
}

interface Loop3 {
  points: Vector3[]
}

// How close two intersection points need to be (in mm) to be treated as
// the same point when chaining triangle-plane intersections into closed
// loops. STEP tessellation isn't always perfectly welded at patch seams,
// so this needs to be forgiving enough to bridge tiny gaps without
// merging genuinely distinct points.
const weldToleranceMm = 0.02
const weldToleranceMmSq = weldToleranceMm * weldToleranceMm
const planeEpsilon = 1e-6

/**
 * Assigns stable keys to points that are within `weldToleranceMm` of each
 * other. A naive "round each coordinate to a grid cell" approach silently
 * fails whenever two truly-coincident points straddle a grid cell
 * boundary (a classic snapping bug) - two intersection points computed
 * from adjacent triangles sharing an edge are numerically close but not
 * bitwise identical, so this happens often enough to break most loops.
 * Comparing actual distances instead - fine at these point counts
 * (typically a few hundred per section) - side-steps that entirely.
 */
class PointWelder {
  private readonly clusters: { point: Vector3; key: string }[] = []
  private nextId = 0

  keyOf(point: Vector3): string {
    for (const cluster of this.clusters) {
      if (cluster.point.distanceToSquared(point) <= weldToleranceMmSq) {
        return cluster.key
      }
    }
    const key = `p${this.nextId}`
    this.nextId += 1
    this.clusters.push({ point, key })
    return key
  }
}

/**
 * Where a plane (through `origin`, normal `planeNormal`) crosses a
 * triangle's boundary - null if the triangle doesn't straddle the plane.
 * A plane can only cross a triangle's 3 edges at exactly 2 points (or 0),
 * so this always returns a single segment when it returns anything.
 */
function intersectTriangleWithPlane(
  a: Vector3,
  b: Vector3,
  c: Vector3,
  origin: Vector3,
  planeNormal: Vector3,
): Segment3 | null {
  const da = a.clone().sub(origin).dot(planeNormal)
  const db = b.clone().sub(origin).dot(planeNormal)
  const dc = c.clone().sub(origin).dot(planeNormal)
  const edges: [Vector3, number, Vector3, number][] = [
    [a, da, b, db],
    [b, db, c, dc],
    [c, dc, a, da],
  ]
  const points: Vector3[] = []
  for (const [p1, d1, p2, d2] of edges) {
    const crosses =
      (d1 > planeEpsilon && d2 < -planeEpsilon) ||
      (d1 < -planeEpsilon && d2 > planeEpsilon)
    if (!crosses) continue
    const t = d1 / (d1 - d2)
    points.push(p1.clone().lerp(p2, t))
  }
  if (points.length === 2) return { a: points[0], b: points[1] }
  return null
}

/**
 * Chains unordered intersection segments into closed loops by walking
 * shared endpoints. In a watertight cut, every intersection point is
 * shared by exactly two segments; segments that don't close into a loop
 * (e.g. because the underlying face set is missing the faces that would
 * connect them - a possible outcome of ROI-scoped face selection) are
 * silently dropped rather than drawn as a broken/open shape.
 */
function chainSegmentsIntoLoops(segments: Segment3[]): Loop3[] {
  const keyToPoint = new Map<string, Vector3>()
  const welder = new PointWelder()

  const keyOf = (p: Vector3): string => {
    const key = welder.keyOf(p)
    if (!keyToPoint.has(key)) keyToPoint.set(key, p)
    return key
  }

  const adjacency = new Map<string, string[]>()
  for (const segment of segments) {
    const keyA = keyOf(segment.a)
    const keyB = keyOf(segment.b)
    if (keyA === keyB) continue
    if (!adjacency.has(keyA)) adjacency.set(keyA, [])
    if (!adjacency.has(keyB)) adjacency.set(keyB, [])
    adjacency.get(keyA)!.push(keyB)
    adjacency.get(keyB)!.push(keyA)
  }

  // Real-world tessellations rarely close perfectly - a handful of points
  // end up with degree 1 (a dangling dead end from a small gap in the
  // face set). A naive walk that happens to start from one of those dead
  // ends can never close, but by the time it gives up it has already
  // marked a bunch of otherwise-good edges "visited" - permanently
  // breaking loops elsewhere that never had anything wrong with them.
  // Pruning dangling chains first (repeatedly removing degree-1 points
  // until none remain) guarantees every point the walk below encounters
  // has degree >= 2, so it can never be poisoned this way.
  const removeEdge = (k1: string, k2: string) => {
    const list1 = adjacency.get(k1)
    if (list1) {
      const index = list1.indexOf(k2)
      if (index >= 0) list1.splice(index, 1)
    }
    const list2 = adjacency.get(k2)
    if (list2) {
      const index = list2.indexOf(k1)
      if (index >= 0) list2.splice(index, 1)
    }
  }
  // A point with degree > 2 is a real branch/junction (or, more likely on
  // messy real-world tessellations, an artifact of two unrelated points
  // landing in the same weld cluster) - walking through it greedily could
  // produce a self-crossing or otherwise wrong-shaped loop, which would
  // be more misleading than showing no cap at all. Removing its edges
  // (and re-running leaf pruning, since that can create new degree-1
  // points) leaves nothing but a clean union of simple cycles for the
  // walk below to trace.
  let pruned = true
  while (pruned) {
    pruned = false
    for (const [key, neighbors] of adjacency) {
      if (neighbors.length === 1) {
        removeEdge(key, neighbors[0])
        pruned = true
      } else if (neighbors.length > 2) {
        for (const neighbor of [...neighbors]) removeEdge(key, neighbor)
        pruned = true
      }
    }
  }

  const edgeKey = (k1: string, k2: string) => (k1 < k2 ? `${k1}|${k2}` : `${k2}|${k1}`)
  const visitedEdges = new Set<string>()
  const loops: Loop3[] = []

  for (const [startKey, neighbors] of adjacency) {
    for (const nextKey of neighbors) {
      const startEdge = edgeKey(startKey, nextKey)
      if (visitedEdges.has(startEdge)) continue
      visitedEdges.add(startEdge)

      const loopKeys = [startKey, nextKey]
      let currentKey = nextKey
      let closed = currentKey === startKey
      let guard = 0
      while (!closed && guard++ < 100000) {
        const currentNeighbors = adjacency.get(currentKey) ?? []
        let advanced = false
        for (const candidate of currentNeighbors) {
          const candidateEdge = edgeKey(currentKey, candidate)
          if (visitedEdges.has(candidateEdge)) continue
          visitedEdges.add(candidateEdge)
          currentKey = candidate
          advanced = true
          break
        }
        if (!advanced) break
        loopKeys.push(currentKey)
        if (currentKey === startKey) closed = true
      }

      if (closed && loopKeys.length >= 4) {
        const points = loopKeys.slice(0, -1).map((key) => keyToPoint.get(key)!)
        loops.push({ points })
      }
    }
  }
  return loops
}

function pointInPolygon(point: Vector2, polygon: Vector2[]): boolean {
  let inside = false
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const pi = polygon[i]
    const pj = polygon[j]
    const crosses =
      pi.y > point.y !== pj.y > point.y &&
      point.x < ((pj.x - pi.x) * (point.y - pi.y)) / (pj.y - pi.y) + pi.x
    if (crosses) inside = !inside
  }
  return inside
}

/**
 * Computes the true filled cross-section of `faceIds` cut by the plane
 * through `origin` with normal `planeNormal` - the actual intersection
 * polygon(s) between the plane and the solid boundary, not just a view of
 * existing surface triangles from a clipped angle. Unlike a GPU clipping
 * plane (which can render nothing if every visible triangle happens to be
 * edge-on to the camera), this always produces a filled shape facing the
 * viewer whenever the selected faces form a watertight boundary crossing
 * the plane - because the shape *is* the intersection, not a projection.
 *
 * Returns world-space triangle positions ready for a BufferGeometry, or
 * null if no closed cross-section could be formed (e.g. the plane misses
 * the geometry entirely, or the face set is missing the faces needed to
 * close the loop - which can happen with a narrowly ROI-scoped selection).
 */
export function computeSectionCapTriangles(
  scene: ScenePayload,
  faceIds: Iterable<number>,
  origin: Vector3,
  planeNormal: Vector3,
  up: Vector3,
  right: Vector3,
): Float32Array | null {
  const segments: Segment3[] = []
  for (const faceId of faceIds) {
    const face = scene.mesh.faces[faceId]
    if (!face) continue
    const [ia, ib, ic] = face
    const va = scene.mesh.vertices[ia]
    const vb = scene.mesh.vertices[ib]
    const vc = scene.mesh.vertices[ic]
    if (!va || !vb || !vc) continue
    const segment = intersectTriangleWithPlane(
      new Vector3(...va),
      new Vector3(...vb),
      new Vector3(...vc),
      origin,
      planeNormal,
    )
    if (segment) segments.push(segment)
  }
  if (segments.length === 0) return null

  const loops = chainSegmentsIntoLoops(segments)
  if (loops.length === 0) return null

  const loops2d = loops.map((loop) => ({
    points3d: loop.points,
    points2d: loop.points.map((p) => {
      const rel = p.clone().sub(origin)
      return new Vector2(rel.dot(right), rel.dot(up))
    }),
  }))

  const sorted = [...loops2d].sort(
    (a, b) => Math.abs(ShapeUtils.area(b.points2d)) - Math.abs(ShapeUtils.area(a.points2d)),
  )
  const consumed = new Set<number>()
  const positions: number[] = []

  for (let i = 0; i < sorted.length; i += 1) {
    if (consumed.has(i)) continue
    const outer = sorted[i]
    const outerIsCw = ShapeUtils.isClockWise(outer.points2d)
    const holes: { points2d: Vector2[]; points3d: Vector3[] }[] = []
    for (let j = i + 1; j < sorted.length; j += 1) {
      if (consumed.has(j)) continue
      const candidate = sorted[j]
      const candidateIsCw = ShapeUtils.isClockWise(candidate.points2d)
      if (candidateIsCw === outerIsCw) continue
      if (!pointInPolygon(candidate.points2d[0], outer.points2d)) continue
      holes.push(candidate)
      consumed.add(j)
    }

    const combinedPoints3d = [
      ...outer.points3d,
      ...holes.flatMap((hole) => hole.points3d),
    ]
    const triangleIndices = ShapeUtils.triangulateShape(
      outer.points2d,
      holes.map((hole) => hole.points2d),
    )
    for (const [a, b, c] of triangleIndices) {
      const pa = combinedPoints3d[a]
      const pb = combinedPoints3d[b]
      const pc = combinedPoints3d[c]
      if (!pa || !pb || !pc) continue
      positions.push(pa.x, pa.y, pa.z, pb.x, pb.y, pb.z, pc.x, pc.y, pc.z)
    }
  }

  if (positions.length === 0) return null
  return new Float32Array(positions)
}
