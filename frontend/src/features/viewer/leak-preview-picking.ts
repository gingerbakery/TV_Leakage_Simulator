import { Camera, Object3D, Points, Vector3 } from 'three'

// Preview dots are drawn through CAD with a fixed pixel size. Pick in screen
// space as well, so their visible halo has the same click area at every zoom.
export function pickLeakPreviewCandidate(
  root: Object3D,
  camera: Camera,
  pointer: { x: number; y: number },
  viewport: { width: number; height: number },
): string | null {
  if (viewport.width <= 0 || viewport.height <= 0) return null
  root.updateWorldMatrix(true, true)
  camera.updateWorldMatrix(true, false)
  const projected = new Vector3()
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
      projected.fromBufferAttribute(positions, index).applyMatrix4(object.matrixWorld).project(camera)
      if (projected.z < -1 || projected.z > 1 || !Number.isFinite(projected.z)) continue
      const x = (projected.x + 1) * viewport.width / 2
      const y = (1 - projected.y) * viewport.height / 2
      const distance = (x - pointer.x) ** 2 + (y - pointer.y) ** 2
      if (distance > 13 ** 2) continue
      if (distance < bestDistance || (distance === bestDistance && projected.z < bestDepth)) {
        bestId = id
        bestDistance = distance
        bestDepth = projected.z
      }
    }
  })
  return bestId
}
