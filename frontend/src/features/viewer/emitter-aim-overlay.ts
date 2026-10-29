import { ArrowHelper, BufferGeometry, DoubleSide, Group, LineBasicMaterial, LineSegments, Mesh, MeshBasicMaterial, Vector3 } from 'three'
import type { EmitterSpec, SceneComponent, ScenePayload, Vec3 } from '@/api'
import { emitterAimBoundary, emitterSphereDirection, isEmitterAimValid } from '@/features/raytracing/emitter-aim'

export interface BodyEmitterDirectionFrame {
  center: Vec3
  normal: Vec3
  arrowLength: number
}

/** Representative outward directions for a closed CAD Body.
 * The ray tracer emits from every authored face normal; six bounding-box
 * arrows communicate that body-wide contract without duplicating millions of
 * triangle-normal arrows in the Viewer. */
export function bodyEmitterDirectionFrames(
  scene: ScenePayload,
  component: SceneComponent,
): BodyEmitterDirectionFrame[] {
  const minimum = component.bbox_min
  const maximum = component.bbox_max
  const center = minimum.map(
    (value, axis) => (value + maximum[axis]) * 0.5,
  ) as Vec3
  const size = minimum.map(
    (value, axis) => Math.max(0, maximum[axis] - value),
  ) as Vec3
  const largestSize = Math.max(...size)
  if (!Number.isFinite(largestSize) || largestSize <= 1e-9) return []
  const arrowLength = Math.max(2, Math.min(18, largestSize * 0.12))
  const candidates = new Map<number, { score: number; frame: BodyEmitterDirectionFrame }>()
  const stride = Math.max(1, Math.floor(component.face_indices.length / 8192))
  for (let index = 0; index < component.face_indices.length; index += stride) {
    const faceId = component.face_indices[index]
    const faceCenter = scene.mesh.face_centroids[faceId]
    const sourceNormal = scene.mesh.face_normals[faceId]
    if (!faceCenter || !sourceNormal) continue
    const length = Math.hypot(...sourceNormal)
    if (!Number.isFinite(length) || length <= 1e-12) continue
    const normal = sourceNormal.map((value) => value / length) as Vec3
    const axis = [0, 1, 2].reduce((best, candidate) =>
      Math.abs(normal[candidate]) > Math.abs(normal[best]) ? candidate : best,
    )
    const sign = normal[axis] < 0 ? -1 : 1
    const bucket = axis * 2 + (sign > 0 ? 1 : 0)
    const score = sign * faceCenter[axis]
    if ((candidates.get(bucket)?.score ?? -Infinity) >= score) continue
    candidates.set(bucket, {
      score,
      frame: {
        center: [...faceCenter] as Vec3,
        normal,
        arrowLength,
      },
    })
  }
  // Extremely small/legacy payloads can lack face normals. Keep a visible
  // fallback, but normal production CAD uses the authored face normals above.
  if (candidates.size === 0) {
    for (let axis = 0; axis < 3; axis += 1) {
      for (const sign of [-1, 1] as const) {
        const origin = [...center] as Vec3
        origin[axis] = sign < 0 ? minimum[axis] : maximum[axis]
        const normal: Vec3 = [0, 0, 0]
        normal[axis] = sign
        const bucket = axis * 2 + (sign > 0 ? 1 : 0)
        candidates.set(bucket, {
          score: sign * origin[axis],
          frame: { center: origin, normal, arrowLength },
        })
      }
    }
  }
  return [...candidates.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, candidate]) => candidate.frame)
}

function createSphereOverlay(emitter: EmitterSpec, sourceCenter: Vec3): Group {
  const aim = emitter.aim!
  const root = new Group()
  root.name = `emitter-aim-${emitter.emitter_id}`
  const source = new Vector3(...sourceCenter)
  const radius = Math.max(4, Math.min(25, Math.max(emitter.width_mm ?? 16, emitter.height_mm ?? 16) * 0.75))
  const upper = aim.sphere_upper_deg ?? 0
  const lower = Math.min(aim.sphere_lower_deg ?? 180, aim.distribution === 'lambertian' ? 90 : 180)
  const color = 0xa78bfa
  const point = (polar: number, azimuth: number) => source.clone().addScaledVector(new Vector3(...emitterSphereDirection(aim, polar, azimuth)), radius)
  const edges: Vector3[] = []
  const triangles: Vector3[] = []
  const polarSteps = 16
  const azimuthSteps = 48
  if (lower > upper) {
    for (let row = 0; row <= polarSteps; row += 1) {
      const polar = upper + (lower - upper) * row / polarSteps
      const nextPolar = upper + (lower - upper) * (row + 1) / polarSteps
      for (let column = 0; column < azimuthSteps; column += 1) {
        const azimuth = column * 360 / azimuthSteps
        const nextAzimuth = (column + 1) * 360 / azimuthSteps
        if (row % 4 === 0) edges.push(point(polar, azimuth), point(polar, nextAzimuth))
        if (row < polarSteps) {
          if (column % 12 === 0) edges.push(point(polar, azimuth), point(nextPolar, azimuth))
          triangles.push(point(polar, azimuth), point(nextPolar, azimuth), point(polar, nextAzimuth),
            point(polar, nextAzimuth), point(nextPolar, azimuth), point(nextPolar, nextAzimuth))
        }
      }
    }
    if (upper > 0 || lower < 180) {
      for (const polar of new Set([upper, lower])) {
        for (const azimuth of [0, 90, 180, 270]) edges.push(source, point(polar, azimuth))
      }
    }
    const surface = new Mesh(new BufferGeometry().setFromPoints(triangles), new MeshBasicMaterial({
      color, transparent: true, opacity: 0.055, side: DoubleSide, depthWrite: false, depthTest: false, toneMapped: false,
    }))
    surface.name = 'aim-sphere-angular-region'
    surface.renderOrder = 85
    const outline = new LineSegments(new BufferGeometry().setFromPoints(edges), new LineBasicMaterial({
      color, transparent: true, opacity: 0.78, depthWrite: false, depthTest: false, toneMapped: false,
    }))
    outline.name = 'aim-sphere-boundary'
    outline.renderOrder = 86
    root.add(surface, outline)
  }
  if (upper === 0 && lower < 180) {
    const arrow = new ArrowHelper(new Vector3(...emitterSphereDirection(aim)), source, radius, color, radius * 0.12, radius * 0.05)
    arrow.name = 'aim-sphere-center-direction'
    root.add(arrow)
  }
  root.traverse((child) => { child.raycast = () => {} })
  return root
}

export function createEmitterAimOverlay(emitter: EmitterSpec, sourceCenter: Vec3): Group | null {
  const aim = emitter.aim
  if (emitter.visible === false || !emitter.enabled || !aim?.enabled || !aim.show_in_viewer) return null
  if (!isEmitterAimValid(aim)) return null
  if (aim.mode === 'sphere') return createSphereOverlay(emitter, sourceCenter)
  const boundary = emitterAimBoundary(aim).map((point) => new Vector3(...point))
  if (boundary.length < 3) return null
  const root = new Group()
  root.name = `emitter-aim-${emitter.emitter_id}`
  const targetCenter = new Vector3(...aim.center)
  const source = new Vector3(...sourceCenter)
  const edges: Vector3[] = []
  const triangles: Vector3[] = []
  for (let index = 0; index < boundary.length; index += 1) {
    const next = boundary[(index + 1) % boundary.length]
    edges.push(boundary[index], next)
    triangles.push(targetCenter, boundary[index], next)
  }
  const color = 0xa78bfa
  const surface = new Mesh(new BufferGeometry().setFromPoints(triangles), new MeshBasicMaterial({
    color, transparent: true, opacity: 0.12, side: DoubleSide, depthWrite: false, toneMapped: false,
  }))
  surface.name = 'aim-target-surface'
  surface.renderOrder = 85
  const outline = new LineSegments(new BufferGeometry().setFromPoints(edges), new LineBasicMaterial({
    color, transparent: true, opacity: 0.95, depthWrite: false, depthTest: false, toneMapped: false,
  }))
  outline.name = 'aim-target-boundary'
  outline.renderOrder = 86
  const guides: Vector3[] = []
  const stride = Math.max(1, Math.floor(boundary.length / 4))
  for (let index = 0; index < boundary.length; index += stride) guides.push(source, boundary[index])
  const guide = new LineSegments(new BufferGeometry().setFromPoints(guides), new LineBasicMaterial({
    color, transparent: true, opacity: 0.28, depthWrite: false, toneMapped: false,
  }))
  guide.name = 'aim-direction-guides'
  root.add(surface, outline, guide)
  const direction = targetCenter.clone().sub(source)
  const distance = direction.length()
  if (distance > 1e-8) {
    const arrow = new ArrowHelper(direction.normalize(), source, distance, color, Math.min(2, distance * 0.1), Math.min(0.8, distance * 0.04))
    arrow.name = 'aim-center-direction'
    root.add(arrow)
  }
  root.traverse((child) => { child.raycast = () => {} })
  return root
}
