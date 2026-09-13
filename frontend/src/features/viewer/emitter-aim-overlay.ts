import { ArrowHelper, BufferGeometry, DoubleSide, Group, LineBasicMaterial, LineSegments, Mesh, MeshBasicMaterial, Vector3 } from 'three'
import type { EmitterSpec, Vec3 } from '@/api'
import { emitterAimBoundary, emitterSphereDirection, isEmitterAimValid } from '@/features/raytracing/emitter-aim'

function createSphereOverlay(emitter: EmitterSpec, sourceCenter: Vec3): Group {
  const aim = emitter.aim!
  const root = new Group()
  root.name = `emitter-aim-${emitter.emitter_id}`
  const source = new Vector3(...sourceCenter)
  const radius = Math.max(4, Math.min(25, Math.max(emitter.width_mm ?? 16, emitter.height_mm ?? 16) * 0.75))
  const upper = aim.sphere_upper_deg ?? 0
  const lower = aim.sphere_lower_deg ?? 180
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
      color, transparent: true, opacity: 0.07, side: DoubleSide, depthWrite: false, toneMapped: false,
    }))
    surface.name = 'aim-sphere-angular-region'
    const outline = new LineSegments(new BufferGeometry().setFromPoints(edges), new LineBasicMaterial({
      color, transparent: true, opacity: 0.65, depthWrite: false, toneMapped: false,
    }))
    outline.name = 'aim-sphere-boundary'
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
  if (!emitter.enabled || !aim?.enabled || !aim.show_in_viewer) return null
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
