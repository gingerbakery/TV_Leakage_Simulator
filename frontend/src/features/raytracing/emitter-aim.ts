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
    mode: 'area',
    sphere_upper_deg: 0,
    sphere_lower_deg: 180,
    sphere_alpha_deg: 0,
    sphere_beta_deg: 0,
  }
}

export function isEmitterAimValid(aim: EmitterAimSpec): boolean {
  const mode = aim.mode ?? 'area'
  const upper = aim.sphere_upper_deg ?? 0
  const lower = aim.sphere_lower_deg ?? 180
  const lengthU = Math.hypot(...aim.u_axis)
  const lengthV = Math.hypot(...aim.v_axis)
  const dot = aim.u_axis.reduce((sum, value, axis) => sum + value * aim.v_axis[axis], 0)
  return (mode === 'area' || mode === 'sphere')
    && aim.distribution === (mode === 'sphere' ? 'uniform_solid_angle' : 'uniform_target_area')
    && [upper, lower, aim.sphere_alpha_deg ?? 0, aim.sphere_beta_deg ?? 0].every(Number.isFinite)
    && (mode !== 'sphere' || (upper >= 0 && upper < lower && lower <= 180) || (upper === 0 && lower === 0))
    && lengthU > 1e-12 && lengthV > 1e-12 && Math.abs(dot / (lengthU * lengthV)) <= 1e-6
    && [...aim.center, ...aim.u_axis, ...aim.v_axis].every(Number.isFinite)
    && [aim.width_mm, aim.height_mm, aim.radius_mm].every(
      (value) => Number.isFinite(value) && (mode === 'sphere' || value > 0),
    )
}

export function setEmitterAimMode(aim: EmitterAimSpec, mode: 'off' | 'area' | 'sphere'): EmitterAimSpec {
  if (mode === 'off') return { ...aim, enabled: false }
  return { ...aim, enabled: true, mode, distribution: mode === 'sphere' ? 'uniform_solid_angle' : 'uniform_target_area' }
}

export function emitterSphereDirection(aim: EmitterAimSpec, polarDeg = 0, azimuthDeg = 0): Vec3 {
  const alpha = ((aim.sphere_alpha_deg ?? 0) % 360) * Math.PI / 180
  const beta = ((aim.sphere_beta_deg ?? 0) % 360) * Math.PI / 180
  const polar = polarDeg * Math.PI / 180
  const azimuth = azimuthDeg * Math.PI / 180
  const localX = Math.sin(polar) * Math.cos(azimuth)
  const localY = Math.sin(polar) * Math.sin(azimuth)
  const localZ = Math.cos(polar)
  const rotatedY = Math.cos(alpha) * localY - Math.sin(alpha) * localZ
  const rotatedZ = Math.sin(alpha) * localY + Math.cos(alpha) * localZ
  return [Math.cos(beta) * localX + Math.sin(beta) * rotatedZ, rotatedY, -Math.sin(beta) * localX + Math.cos(beta) * rotatedZ]
}

export function emitterAimBoundary(aim: EmitterAimSpec): Vec3[] {
  if (!isEmitterAimValid(aim) || aim.mode === 'sphere') return []
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
