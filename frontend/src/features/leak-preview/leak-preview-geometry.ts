import type { ScenePayload, Vec3 } from '@/api'
import type { ComponentTransformRule } from '@/stores'

type PointTransform = (componentId: number, point: Vec3) => Vec3

function componentCenter(
  scene: ScenePayload,
  componentId: number,
): Vec3 {
  const component = scene.components.find(
    (item) => item.component_id === componentId,
  )
  if (!component) return [0, 0, 0]
  return [
    (component.bbox_min[0] + component.bbox_max[0]) / 2,
    (component.bbox_min[1] + component.bbox_max[1]) / 2,
    (component.bbox_min[2] + component.bbox_max[2]) / 2,
  ]
}

/** Mirrors the backend's component rotation order (X, then Y, then Z). */
export function createLeakPreviewPointTransform(
  scene: ScenePayload,
  transformRules: ComponentTransformRule[] = [],
): PointTransform {
  const rules = new Map(
    transformRules
      .filter((rule) => rule.enabled && rule.targetType === 'component')
      .map((rule) => {
        const rx = rule.tilt.x * Math.PI / 180
        const ry = rule.tilt.y * Math.PI / 180
        const rz = rule.tilt.z * Math.PI / 180
        return [
          rule.componentId,
          {
            pivot: rule.pivot
              ? [rule.pivot.x, rule.pivot.y, rule.pivot.z] as Vec3
              : componentCenter(scene, rule.componentId),
            move: [rule.move.x, rule.move.y, rule.move.z] as Vec3,
            sinX: Math.sin(rx),
            cosX: Math.cos(rx),
            sinY: Math.sin(ry),
            cosY: Math.cos(ry),
            sinZ: Math.sin(rz),
            cosZ: Math.cos(rz),
            rotateX: Math.abs(rx) > 1e-12,
            rotateY: Math.abs(ry) > 1e-12,
            rotateZ: Math.abs(rz) > 1e-12,
          },
        ] as const
      }),
  )
  return (componentId, point) => {
    const rule = rules.get(componentId)
    if (!rule) return point
    const pivot = rule.pivot
    let x = point[0] - pivot[0]
    let y = point[1] - pivot[1]
    let z = point[2] - pivot[2]
    if (rule.rotateX) {
      const nextY = y * rule.cosX - z * rule.sinX
      const nextZ = y * rule.sinX + z * rule.cosX
      y = nextY
      z = nextZ
    }
    if (rule.rotateY) {
      const nextX = x * rule.cosY + z * rule.sinY
      const nextZ = -x * rule.sinY + z * rule.cosY
      x = nextX
      z = nextZ
    }
    if (rule.rotateZ) {
      const nextX = x * rule.cosZ - y * rule.sinZ
      const nextY = x * rule.sinZ + y * rule.cosZ
      x = nextX
      y = nextY
    }
    return [
      x + pivot[0] + rule.move[0],
      y + pivot[1] + rule.move[1],
      z + pivot[2] + rule.move[2],
    ]
  }
}

function componentCorners(minimum: Vec3, maximum: Vec3): Vec3[] {
  return [
    [minimum[0], minimum[1], minimum[2]],
    [maximum[0], minimum[1], minimum[2]],
    [minimum[0], maximum[1], minimum[2]],
    [maximum[0], maximum[1], minimum[2]],
    [minimum[0], minimum[1], maximum[2]],
    [maximum[0], minimum[1], maximum[2]],
    [minimum[0], maximum[1], maximum[2]],
    [maximum[0], maximum[1], maximum[2]],
  ]
}

/** Component metadata already carries full-model bounds. Using it avoids a
 * second traversal of every tessellated vertex on large STEP assemblies. */
export function getLeakPreviewBounds(
  scene: ScenePayload,
  transformRules: ComponentTransformRule[] = [],
): {
  center: Vec3
  size: Vec3
  minimum: Vec3
  maximum: Vec3
} {
  let minimum: Vec3 = [Infinity, Infinity, Infinity]
  let maximum: Vec3 = [-Infinity, -Infinity, -Infinity]
  const transformPoint = createLeakPreviewPointTransform(scene, transformRules)
  for (const component of scene.components) {
    for (const point of componentCorners(component.bbox_min, component.bbox_max)) {
      const transformed = transformPoint(component.component_id, point)
      for (let axis = 0; axis < 3; axis += 1) {
        minimum[axis] = Math.min(minimum[axis], transformed[axis])
        maximum[axis] = Math.max(maximum[axis], transformed[axis])
      }
    }
  }
  if (!Number.isFinite(minimum[0])) {
    minimum = [0, 0, 0]
    maximum = [1, 1, 1]
  }
  const size: Vec3 = [
    Math.max(maximum[0] - minimum[0], 1),
    Math.max(maximum[1] - minimum[1], 1),
    Math.max(maximum[2] - minimum[2], 1),
  ]
  return {
    minimum,
    maximum,
    size,
    center: [
      (minimum[0] + maximum[0]) / 2,
      (minimum[1] + maximum[1]) / 2,
      (minimum[2] + maximum[2]) / 2,
    ],
  }
}
