// Display-only studio presets. These never enter RayTraceRequest or optical materials.
export type LeakageExteriorColor = 'black' | 'gray' | 'silver'
export type LeakageExteriorFinish = 'matte' | 'satin'

const exteriorColors: Record<LeakageExteriorColor, number> = {
  black: 0x34383e,
  gray: 0x747980,
  silver: 0xb6bcc4,
}

export function leakageExteriorStyle(
  color: LeakageExteriorColor,
  finish: LeakageExteriorFinish,
) {
  return {
    color: exteriorColors[color],
    metalness: color === 'silver' ? 0.35 : 0.08,
    roughness: finish === 'satin' ? 0.38 : 0.82,
  }
}
