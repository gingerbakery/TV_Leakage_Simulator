import type { Material, Object3D, Vector3 } from 'three'
import type { RoiClipBox } from '@/stores'

function limits(box: RoiClipBox): [number, number][] {
  const plane = box.zUnbounded ? 'xy' : box.plane ?? 'xy'
  return [
    plane === 'yz' ? [-Infinity, Infinity] : [box.xMin, box.xMax],
    plane === 'zx' ? [-Infinity, Infinity] : [box.yMin, box.yMax],
    plane === 'xy' ? [-Infinity, Infinity] : [box.zMin ?? 0, box.zMax ?? 0],
  ]
}

export function emitterPointInRoi(point: Pick<Vector3, 'x' | 'y' | 'z'>, boxes: RoiClipBox[]): boolean {
  return boxes.length === 0 || boxes.some((box) => limits(box).every(([lo, hi], axis) => {
    const value = [point.x, point.y, point.z][axis]
    return value >= lo && value <= hi
  }))
}

/** Display-only union clipping, independent of the existing Section planes.
 * World coordinates cover transformed planes, outlines, arrows and Aim lobes.
 * Shader clipping retains the authored geometry and simulation inputs.
 */
export function clipEmitterDisplayToRoi(root: Object3D, boxes: RoiClipBox[]): void {
  root.userData.emitterRoiBoxes = boxes
  if (boxes.length === 0) return
  const number = (value: number) => value.toExponential(12)
  const inside = boxes.map((box) => '(' + limits(box).flatMap(([lo, hi], axis) =>
    Number.isFinite(lo) && Number.isFinite(hi)
      ? [`(vEmitterRoiWorld.${'xyz'[axis]} >= ${number(lo)} && vEmitterRoiWorld.${'xyz'[axis]} <= ${number(hi)})`]
      : [],
  ).join(' && ') + ')').join(' || ')
  const visited = new Set<Material>()
  root.traverse((object) => {
    const material = (object as Object3D & { material?: Material | Material[] }).material
    if (!material) return
    for (const entry of Array.isArray(material) ? material : [material]) {
      if (visited.has(entry)) continue
      visited.add(entry)
      const previous = entry.onBeforeCompile
      const previousKey = entry.customProgramCacheKey.bind(entry)
      const cacheKey = previousKey()
      entry.onBeforeCompile = (shader, renderer) => {
        previous.call(entry, shader, renderer)
        shader.vertexShader = shader.vertexShader
          .replace('#include <common>', '#include <common>\nvarying vec3 vEmitterRoiWorld;')
          .replace('#include <project_vertex>', '#include <project_vertex>\nvEmitterRoiWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;')
        shader.fragmentShader = shader.fragmentShader
          .replace('#include <common>', '#include <common>\nvarying vec3 vEmitterRoiWorld;')
          .replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>\nif (!(${inside})) discard;`)
      }
      entry.customProgramCacheKey = () => `${cacheKey}:emitter-roi-union:${inside}`
      entry.needsUpdate = true
    }
  })
}
