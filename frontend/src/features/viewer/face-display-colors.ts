import { Color, Float32BufferAttribute, type BufferGeometry, type Material } from 'three'
import type { ScenePayload } from '@/api'
import type { FaceDisplayColor } from '@/stores'

export function resolveFaceDisplayColors(scene: ScenePayload, entries: FaceDisplayColor[]): Map<number, string> {
  const colors = new Map<number, string>()
  for (const entry of entries) {
    for (const faceId of entry.faceIds) {
      if (scene.mesh.face_component_ids[faceId] === entry.componentId) colors.set(faceId, entry.color)
    }
  }
  return colors
}

const colorCache = new WeakMap<BufferGeometry, { colors: ReadonlyMap<number, string>, base: string, applied: boolean }>()

export function applyFaceDisplayColors(geometry: BufferGeometry, colors: ReadonlyMap<number, string>, baseColor: Color): boolean {
  const base = baseColor.getHexString()
  const cached = colorCache.get(geometry)
  if (cached?.colors === colors && cached.base === base) return cached.applied
  const faceIds = geometry.userData.sourceFaceIds as number[]
  const applied = faceIds.some((faceId) => colors.has(faceId))
  if (applied) {
    const vertexCount = geometry.getAttribute('position').count
    const existing = geometry.getAttribute('color')
    const attribute = existing instanceof Float32BufferAttribute && existing.count === vertexCount
      ? existing : new Float32BufferAttribute(new Float32Array(vertexCount * 3), 3)
    const index = geometry.getIndex()
    const colorValues = new Map<string, Color>()
    for (const [triangleIndex, faceId] of faceIds.entries()) {
      const hex = colors.get(faceId)
      if (hex && !colorValues.has(hex)) colorValues.set(hex, new Color(hex))
      const color = hex ? colorValues.get(hex)! : baseColor
      for (let corner = 0; corner < 3; corner += 1) {
        const offset = triangleIndex * 3 + corner
        attribute.setXYZ(index ? index.getX(offset) : offset, color.r, color.g, color.b)
      }
    }
    geometry.setAttribute('color', attribute)
    attribute.needsUpdate = true
  }
  colorCache.set(geometry, { colors, base, applied })
  return applied
}

export function setDisplayVertexColors(material: Material, enabled: boolean): void {
  if (material.vertexColors === enabled) return
  material.vertexColors = enabled
  material.needsUpdate = true
}
