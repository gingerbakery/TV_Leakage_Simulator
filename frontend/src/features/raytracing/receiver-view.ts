import type { ReceiverSpec, ReceiverViewAngles, ScenePayload, Vec3 } from '@/api'
import type { ComponentTransformRule } from '@/stores'
import { getLeakPreviewBounds } from '@/features/leak-preview/leak-preview-geometry'
import { createDatumReceiver } from './ray-tracing-model'

export const defaultReceiverView: ReceiverViewAngles = { axis: 'neg_z', horizontal_deg: 0, vertical_deg: 0, range_deg: 0, step_deg: 1 }
export const receiverViewDirections = [
  ['neg_z', '후면 −Z'], ['pos_z', '전면 +Z'], ['pos_x', '+X'], ['neg_x', '−X'], ['pos_y', '+Y'], ['neg_y', '−Y'],
] as const
const axes: Record<ReceiverViewAngles['axis'], Vec3> = { pos_x: [1,0,0], neg_x: [-1,0,0], pos_y: [0,1,0], neg_y: [0,-1,0], pos_z: [0,0,1], neg_z: [0,0,-1] }
const dot = (a: Vec3, b: Vec3) => a.reduce((sum, value, i) => sum + value*b[i], 0)
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
const unit = (a: Vec3): Vec3 => a.map((value) => value / Math.hypot(...a)) as Vec3

/** Uses exactly the axis/azimuth/elevation convention of observer_probe.py. */
export function receiverViewDirection(view: ReceiverViewAngles): Vec3 {
  const w = axes[view.axis]
  const reference: Vec3 = Math.abs(w[1]) < .9 ? [0,1,0] : [0,0,1]
  const right = unit(cross(w, reference)), up = cross(right, w)
  const h = view.horizontal_deg*Math.PI/180, v = view.vertical_deg*Math.PI/180
  return unit(w.map((value, i) => value*Math.cos(h)*Math.cos(v) + right[i]*Math.sin(h)*Math.cos(v) + up[i]*Math.sin(v)) as Vec3)
}

export function receiverViewAnglePairs(view: ReceiverViewAngles): Array<[number, number]> {
  if (!axes[view.axis] || !Number.isFinite(view.horizontal_deg) || Math.abs(view.horizontal_deg) > 89.9 ||
      !Number.isFinite(view.vertical_deg) || Math.abs(view.vertical_deg) > 89.9 ||
      !Number.isFinite(view.range_deg) || view.range_deg < 0 || view.range_deg > 10 ||
      !Number.isFinite(view.step_deg) || view.step_deg < .1 || view.step_deg > 10) throw new Error('시야각과 범위·간격을 확인하세요.')
  const offsets = new Set([0, -view.range_deg, view.range_deg])
  for (let i = -Math.floor(view.range_deg/view.step_deg); i <= Math.floor(view.range_deg/view.step_deg); i++) offsets.add(i*view.step_deg)
  const values = (center: number) => [...new Set([...offsets].map((offset) => Number(Math.max(-89.9, Math.min(89.9, center+offset)).toFixed(8))))].sort((a,b) => a-b)
  const horizontal = values(view.horizontal_deg), vertical = values(view.vertical_deg)
  const count = horizontal.length*vertical.length
  if (count > 121) throw new Error(`각도 조합 ${count}개 · 한도 121개. 간격을 늘리거나 범위를 줄이세요.`)
  return horizontal.flatMap((h) => vertical.map((v): [number, number] => [h,v]))
}

export function createViewAngleReceiver(id: string, scene: ScenePayload, target: Vec3, view: ReceiverViewAngles,
  clearanceMm: number, transforms: ComponentTransformRule[] = [], offset: Vec3 = [0,0,0]): ReceiverSpec {
  receiverViewAnglePairs(view)
  const outward = receiverViewDirection(view)
  const bounds = getLeakPreviewBounds(scene, transforms)
  let support = 0
  for (const x of [bounds.minimum[0], bounds.maximum[0]]) for (const y of [bounds.minimum[1], bounds.maximum[1]]) for (const z of [bounds.minimum[2], bounds.maximum[2]]) {
    support = Math.max(support, dot([x-target[0], y-target[1], z-target[2]], outward))
  }
  const clearance = Math.max(.001, clearanceMm)
  const center = target.map((value, i) => value + outward[i]*(support+clearance) + offset[i]) as Vec3
  const normal = outward.map((value) => -value) as Vec3
  const w = axes[view.axis]
  const reference: Vec3 = Math.abs(w[1]) < .9 ? [0,1,0] : [0,0,1]
  const right = unit(cross(w, reference)), h = view.horizontal_deg*Math.PI/180
  const u = unit(w.map((value, i) => -value*Math.sin(h) + right[i]*Math.cos(h)) as Vec3)
  const v = unit(cross(u, outward))
  return { ...createDatumReceiver(id, center, [0,0,0]), center, normal, u_axis: u, v_axis: v,
    normal_flip: false, reference_mode: 'view_angle', view_angles: { ...view }, view_distance_mm: clearance,
    base_center: [...target], base_u_axis: u, base_v_axis: v, base_normal: normal, position_offset_mm: [...offset] }
}

export function expandReceiverView(receiver: ReceiverSpec, scene: ScenePayload, transforms: ComponentTransformRule[] = []): ReceiverSpec[] {
  if (!receiver.view_angles) return [receiver]
  return receiverViewAnglePairs(receiver.view_angles).map(([horizontal_deg, vertical_deg]) => ({
    ...receiver,
    ...createViewAngleReceiver(receiver.receiver_id, scene, receiver.base_center ?? receiver.center,
      { ...receiver.view_angles!, horizontal_deg, vertical_deg, range_deg: 0 }, receiver.view_distance_mm ?? 3, transforms, receiver.position_offset_mm),
    display_name: `${receiver.display_name} · ${horizontal_deg}° / ${vertical_deg}°`,
    width_mm: receiver.width_mm, height_mm: receiver.height_mm, resolution: [...receiver.resolution],
    acceptance_angle_deg: receiver.acceptance_angle_deg, normal_flip: receiver.normal_flip, enabled: receiver.enabled,
  }))
}
