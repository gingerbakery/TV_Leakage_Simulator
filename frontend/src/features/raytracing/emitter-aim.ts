import type { EmitterAimSpec, Vec3 } from '@/api'

export function createEmitterAim(center: Vec3): EmitterAimSpec {
  return {
    enabled: false,
    shape: 'rectangle',
    center: [...center],
    u_axis: [1, 0, 0],
    v_axis: [0, 1, 0],
    width_mm: 20,
    height_mm: 20,
    radius_mm: 10,
    show_in_viewer: true,
    distribution: 'uniform_target_area',
    power_reference: 'aim_region',
  }
}

export function isEmitterAimValid(aim: EmitterAimSpec): boolean {
  return [...aim.center, ...aim.u_axis, ...aim.v_axis].every(Number.isFinite)
    && [aim.width_mm, aim.height_mm, aim.radius_mm].every(
      (value) => Number.isFinite(value) && value > 0,
    )
}

export function emitterAimBoundary(aim: EmitterAimSpec): Vec3[] {
  if (!isEmitterAimValid(aim)) return []
  const offsets = aim.shape === 'circle'
    ? Array.from({ length: 64 }, (_, index) => {
      const angle = index * Math.PI * 2 / 64
      return [Math.cos(angle) * aim.radius_mm, Math.sin(angle) * aim.radius_mm]
    })
    : [
      [-aim.width_mm / 2, -aim.height_mm / 2],
      [aim.width_mm / 2, -aim.height_mm / 2],
      [aim.width_mm / 2, aim.height_mm / 2],
      [-aim.width_mm / 2, aim.height_mm / 2],
    ]
  return offsets.map(([offsetU, offsetV]) => aim.center.map(
    (coordinate, axis) => coordinate + offsetU * aim.u_axis[axis] + offsetV * aim.v_axis[axis],
  ) as Vec3)
}
