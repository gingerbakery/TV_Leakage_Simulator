import type { SceneComponent } from '@/api'

export type AxisCameraPreset =
  | 'XY'
  | '-XY'
  | 'YZ'
  | '-YZ'
  | 'ZX'
  | '-ZX'

export type DisplayCameraPreset =
  | 'Fit'
  | 'Iso'
  | AxisCameraPreset

type AxisVector = readonly [number, number, number]

interface AxisCameraPresetAxes {
  direction: AxisVector
  up: AxisVector
}

const axisCameraPresetAxes: Record<
  AxisCameraPreset,
  AxisCameraPresetAxes
> = {
  XY: {
    direction: [0, 0, 1],
    up: [0, 1, 0],
  },
  '-XY': {
    direction: [0, 0, -1],
    up: [0, -1, 0],
  },
  YZ: {
    direction: [1, 0, 0],
    up: [0, 1, 0],
  },
  '-YZ': {
    direction: [-1, 0, 0],
    up: [0, 1, 0],
  },
  ZX: {
    direction: [0, 1, 0],
    up: [0, 0, 1],
  },
  '-ZX': {
    direction: [0, -1, 0],
    up: [0, 0, 1],
  },
}

export const componentColorPalette = [
  0x64748b, 0x526b7a, 0x475569, 0x5b6473, 0x45606d, 0x667085,
]

/**
 * CAD-authored component color wins when present; otherwise cycles a neutral
 * fallback palette. A separate user display-color override is applied by the
 * viewer and is intentionally independent from optical Material Assignment.
 */
export function resolveComponentColor(
  component: SceneComponent | undefined,
  index: number,
): number {
  if (component?.color) {
    const parsed = Number.parseInt(component.color.replace('#', ''), 16)
    if (!Number.isNaN(parsed)) return parsed
  }
  return componentColorPalette[
    Math.max(0, index) % componentColorPalette.length
  ]
}

export function resolveComponentColorHex(
  component: SceneComponent | undefined,
  index: number,
): string {
  return `#${resolveComponentColor(component, index)
    .toString(16)
    .padStart(6, '0')}`
}

export const DEFAULT_CAMERA_FOV_DEGREES = 42
export const AXIS_CAMERA_FOV_DEGREES = 0.75

export function getAxisCameraPresetAxes(
  preset: AxisCameraPreset,
): AxisCameraPresetAxes {
  return axisCameraPresetAxes[preset]
}

export function cameraFovForPreset(
  preset: DisplayCameraPreset,
  currentFov: number,
): number {
  if (preset === 'Fit') return currentFov
  if (preset === 'Iso') return DEFAULT_CAMERA_FOV_DEGREES
  return AXIS_CAMERA_FOV_DEGREES
}

export function surfaceOpacityFromTransparency(
  transparencyPercent: number,
): number {
  const finiteTransparency = Number.isFinite(transparencyPercent)
    ? transparencyPercent
    : 0
  const clampedTransparency = Math.min(
    90,
    Math.max(0, finiteTransparency),
  )
  return 1 - clampedTransparency / 100
}
