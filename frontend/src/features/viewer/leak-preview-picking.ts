import { Camera, Object3D, Points, Vector3 } from 'three'

// Preview dots have a fixed pixel size. Keep picking in screen space, but
// reject physically occluded endpoints before selecting their candidate.
export function pickLeakPreviewCandidate(
  root: Object3D,
  camera: Camera,
  pointer: { x: number; y: number },
  viewport: { width: number; height: number },
  isVisible?: (point: Vector3) => boolean,
): string | null {
  if (viewport.width <= 0 || viewport.height <= 0) return null
  root.updateWorldMatrix(true, true)
  camera.updateWorldMatrix(true, false)
  const projected = new Vector3()
  const world = new Vector3()
  let bestId: string | null = null
  let bestDistance = Infinity
  let bestDepth = Infinity
  root.traverseVisible((object) => {
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
      if (distance > 13 ** 2) continue
      if (isVisible && !isVisible(world)) continue
      if (distance < bestDistance || (distance === bestDistance && projected.z < bestDepth)) {
        bestId = id
        bestDistance = distance
        bestDepth = projected.z
      }
    }
  })
  return bestId
}
