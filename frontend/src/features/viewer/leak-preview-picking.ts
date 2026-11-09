import type { ObserverPointSelection } from '@/features/leak-preview/observer-point-selection'
import type { LeakPreviewPoint } from '@/features/leak-preview/leak-preview-model'
import { Camera, Mesh, Object3D, Points, Raycaster, Triangle, Vector2, Vector3 } from 'three'
import { leakPreviewDisplayStyle } from './leak-preview-display-style'

// Preview dots have a fixed pixel size. Keep picking in screen space, but
// reject physically occluded endpoints before selecting their candidate.
export function pickLeakPreviewPoint(
  root: Object3D,
  camera: Camera,
  pointer: { x: number; y: number },
  viewport: { width: number; height: number },
  isVisible?: (point: Vector3, distanceSquared: number) => boolean,
): { candidateId: string; observation?: ObserverPointSelection } | null {
  if (viewport.width <= 0 || viewport.height <= 0) return null
  root.updateWorldMatrix(true, true)
  camera.updateWorldMatrix(true, false)
  const projected = new Vector3()
  const world = new Vector3()
  const candidates: Array<{ id: string; point: Vector3; distance: number; depth: number; observation?: ObserverPointSelection }> = []
  const guides: Mesh[] = []
  root.traverseVisible((object) => {
    if (object instanceof Mesh && object.layers.test(camera.layers) && typeof object.userData.leakCandidateId === 'string') guides.push(object)
    if (!(object instanceof Points) || !object.layers.test(camera.layers)) return
    const ids: Array<string | undefined> | undefined = object.userData.leakCandidateIds
    const positions = object.geometry.getAttribute('position')
    if (!ids || !positions) return
    for (let index = 0; index < positions.count; index++) {
      const id = ids[index]
      if (!id) continue
      world.fromBufferAttribute(positions, index).applyMatrix4(object.matrixWorld)
      projected.copy(world).project(camera)
      if (projected.z < -1 || projected.z > 1 || !Number.isFinite(projected.z)) continue
      const x = (projected.x + 1) * viewport.width / 2
      const y = (1 - projected.y) * viewport.height / 2
      const distance = (x - pointer.x) ** 2 + (y - pointer.y) ** 2
      if (distance > leakPreviewDisplayStyle.pickRadius ** 2) continue
      const source = (object.userData.leakPoints as LeakPreviewPoint[] | undefined)?.[index]
      const observation = source?.observerAngles ? {position: source.position, angles: source.observerAngles} : undefined
      candidates.push({ id, point: world.clone(), distance, depth: projected.z, observation })
    }
  })
  // Cheap screen projection first. Expensive CAD checks stop at the first
  // visible nearest candidate instead of raycasting every overlapping point.
  candidates.sort((a, b) => a.distance - b.distance || a.depth - b.depth)
  for (const candidate of candidates) if (!isVisible || isVisible(candidate.point, candidate.distance)) return {candidateId:candidate.id, observation:candidate.observation}
  // The exit gradient is part of the same selectable Leak, not a separate point.
  const raycaster = new Raycaster()
  raycaster.setFromCamera(new Vector2(pointer.x / viewport.width * 2 - 1, 1 - pointer.y / viewport.height * 2), camera)
  for (const hit of raycaster.intersectObjects(guides, false)) {
    const mesh = hit.object as Mesh, face = hit.face
    const position = mesh.geometry.getAttribute('position'), color = mesh.geometry.getAttribute('color')
    if (!face || !color || color.itemSize !== 4) continue
    const barycentric = Triangle.getBarycoord(mesh.worldToLocal(hit.point.clone()),
      new Vector3().fromBufferAttribute(position, face.a), new Vector3().fromBufferAttribute(position, face.b),
      new Vector3().fromBufferAttribute(position, face.c), new Vector3())
    if (!barycentric || barycentric.dot(new Vector3(color.getW(face.a), color.getW(face.b), color.getW(face.c))) < .02) continue
    if (isVisible && !isVisible(hit.point, 0)) continue
    return {candidateId:mesh.userData.leakCandidateId}
  }
  return null
}

/** Compatibility API for callers that only need the owning grouped candidate. */
export function pickLeakPreviewCandidate(...args: Parameters<typeof pickLeakPreviewPoint>): string | null {
  return pickLeakPreviewPoint(...args)?.candidateId ?? null
}
