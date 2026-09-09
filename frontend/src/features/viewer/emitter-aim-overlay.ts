import { ArrowHelper, BufferGeometry, DoubleSide, Group, LineBasicMaterial, LineSegments, Mesh, MeshBasicMaterial, Vector3 } from 'three'
import type { EmitterSpec, Vec3 } from '@/api'
import { emitterAimBoundary } from '@/features/raytracing/emitter-aim'

export function createEmitterAimOverlay(emitter: EmitterSpec, sourceCenter: Vec3): Group | null {
  const aim = emitter.aim
  if (!emitter.enabled || !aim?.enabled || !aim.show_in_viewer) return null
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
