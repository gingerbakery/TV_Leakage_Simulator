import { Line3, Triangle, Vector3 } from 'three'
import type { EmitterSpec, ReceiverSpec, ScenePayload, Vec3 } from '@/api'
import { LOCAL_FORWARD_EMITTER_ID } from '@/features/raytracing/ray-tracing-model'
import type { ComponentTransformRule, RoiClipBox } from '@/stores'
import { createLeakPreviewPointTransform } from './leak-preview-geometry'

function emitsAlong(source: EmitterSpec, normal: Vector3, direction: Vector3): boolean {
  if (source.direction_distribution === 'isotropic' || source.emission_direction === 'both') return true
  const reversed = source.emission_direction === 'reverse' || (source.emission_direction == null && source.normal_flip)
  return direction.dot(normal) * (reversed ? -1 : 1) > 0
}

export function localForwardEmitter(
  scene: ScenePayload, sources: EmitterSpec[], path: Vec3[],
  transforms: ComponentTransformRule[],
): EmitterSpec {
  if (path.length < 2) throw new Error('저장된 대표 경로가 없습니다.')
  const origin = new Vector3(...path[0]), next = new Vector3(...path[1])
  const direction = next.clone().sub(origin)
  if (direction.lengthSq() < 1e-8) {
    throw new Error('광원에서 출발하는 유효한 경로 구간이 없습니다.')
  }
  direction.normalize()
  const transform = createLeakPreviewPointTransform(scene, transforms)
  const matches: Array<{ source: EmitterSpec; face?: number; patch?: Partial<EmitterSpec> }> = []
  const nearest = new Vector3()
  for (const source of sources) {
    if (source.emitter_type === 'face') {
      const bodies = new Set(source.source_component_ids ?? [])
      const faces = new Set(source.face_indices)
      if (bodies.size) scene.mesh.face_component_ids.forEach((body, face) => {
        if (body != null && bodies.has(body)) faces.add(face)
      })
      for (const face of faces) {
        const vertices = scene.mesh.faces[face]
        if (!vertices) continue
        const body = scene.mesh.face_component_ids[face]
        const triangle = new Triangle(...vertices.map(vertex => new Vector3(...(body == null
          ? scene.mesh.vertices[vertex] : transform(body, scene.mesh.vertices[vertex])))) as [Vector3, Vector3, Vector3])
        const normal = triangle.getNormal(new Vector3())
        const emissionNormal = source.custom_normal && source.normal_mode === 'custom'
          ? new Vector3(...source.custom_normal) : normal
        if (!emitsAlong(source, emissionNormal, direction)) continue
        if (triangle.closestPointToPoint(origin, nearest).distanceToSquared(origin) > .0025) continue
        matches.push({ source, face })
        break
      }
      continue
    }
    if (!source.center || !source.u_axis || !source.v_axis) continue
    const u = new Vector3(...source.u_axis).normalize()
    const v = new Vector3(...source.v_axis).addScaledVector(u, -u.dot(new Vector3(...source.v_axis))).normalize()
    const normal = u.clone().cross(v)
    if (!emitsAlong(source, source.custom_normal ? new Vector3(...source.custom_normal) : normal, direction)) continue
    const delta = origin.clone().sub(new Vector3(...source.center))
    if (Math.abs(delta.dot(normal)) > .05) continue
    const points = source.polygon_vertices.map(point => new Vector3(...point))
    const inside = source.surface_construction === 'polygon_auto'
      ? points.slice(1, -1).some((point, index) =>
        new Triangle(points[0], point, points[index + 2]).closestPointToPoint(origin, nearest).distanceToSquared(origin) <= .0025)
      : Math.abs(delta.dot(u)) <= (source.width_mm ?? 0) / 2 + .05 &&
        Math.abs(delta.dot(v)) <= (source.height_mm ?? 0) / 2 + .05
    if (!inside) continue
    const center = origin.clone().addScaledVector(normal, -delta.dot(normal))
    const edgeClearance = source.surface_construction === 'polygon_auto'
      ? Math.min(...points.map((point, index) => new Line3(point, points[(index + 1) % points.length])
        .closestPointToPoint(center, true, nearest).distanceTo(center)))
      : Math.min((source.width_mm ?? 0) / 2 - Math.abs(delta.dot(u)), (source.height_mm ?? 0) / 2 - Math.abs(delta.dot(v)))
    const halfSize = Math.min(3, edgeClearance / Math.SQRT2)
    const patch: Partial<EmitterSpec> | undefined = halfSize > .01 ? {
      emitter_type: 'datum_plane', center: center.toArray(),
      width_mm: 2 * halfSize, height_mm: 2 * halfSize,
      surface_construction: 'rectangular_fit', polygon_vertices: [], reference_mode: null,
      reference_vertex_indices: [], reference_edge_vertex_indices: [], reference_vertex_points: [], reference_edge_points: [],
    } : undefined
    matches.push({ source, patch })
  }
  if (matches.length !== 1) throw new Error(matches.length
    ? '경로 시작점에 광원이 겹칩니다. Preview에서 광원을 하나씩 선택해 다시 검색하세요.'
    : '경로 시작점에 일치하는 광원면이 없습니다. Preview를 다시 검색하세요.')
  const { source, face, patch } = matches[0]
  if (face === undefined && !patch) throw new Error('광원 경계에 도달한 경로입니다. 광원면 안쪽에 도달한 표본을 선택하세요.')
  return {
    ...source, ...patch, emitter_id: LOCAL_FORWARD_EMITTER_ID, enabled: true, visible: true,
    array_group_id: null, array_index: null, array_power_basis: null,
    ...(face === undefined ? {} : { face_indices: [face], source_component_ids: undefined }),
  }
}

export function localForwardEmissionTriangle(
  scene: ScenePayload, emitter: EmitterSpec, transforms: ComponentTransformRule[],
): Vec3[] {
  if (emitter.emitter_type !== 'face') return []
  const faceId = emitter.face_indices[0]
  const body = scene.mesh.face_component_ids[faceId]
  const transform = createLeakPreviewPointTransform(scene, transforms)
  return (scene.mesh.faces[faceId] ?? []).map(index => {
    const point = scene.mesh.vertices[index]
    return body == null ? point : transform(body, point)
  })
}

export function localForwardRoi(point: Vec3, receiver: ReceiverSpec, path: Vec3[] = [], marginMm = 1): RoiClipBox {
  const u = receiver.u_axis ?? [1, 0, 0], v = receiver.v_axis ?? [0, 1, 0]
  const patch = point.map((coordinate, axis) => {
    const offset = Math.abs(u[axis]) * receiver.width_mm / 2 + Math.abs(v[axis]) * receiver.height_mm / 2 + marginMm
    return [
      Math.min(coordinate - offset, ...path.map(position => position[axis] - marginMm)),
      Math.max(coordinate + offset, ...path.map(position => position[axis] + marginMm)),
    ]
  })
  return { plane: 'xyz', xMin: patch[0][0], xMax: patch[0][1], yMin: patch[1][0], yMax: patch[1][1],
    zMin: patch[2][0], zMax: patch[2][1] }
}
