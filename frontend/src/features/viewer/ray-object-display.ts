import type { Material, Object3D } from 'three'

/** Ray objects must respect the CAD depth buffer, including lines and arrows. */
export function applyRayObjectOcclusion(root: Object3D): void {
  root.traverse((object) => {
    const material = (object as Object3D & { material?: Material | Material[] }).material
    if (!material) return
    for (const entry of Array.isArray(material) ? material : [material]) {
      if (entry.depthTest) continue
      entry.depthTest = true
      entry.needsUpdate = true
    }
  })
}
