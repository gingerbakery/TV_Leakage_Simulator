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
    // Keep +Y upward. Looking from -Z then places -X on screen-right.
    up: [0, 1, 0],
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
    direction: [0, -1, 0],
    up: [0, 0, 1],
  },
  '-ZX': {
    direction: [0, 1, 0],
    up: [0, 0, 1],
  },
}

export const ISO_CAMERA_AXES = {
  // Balanced ISO: +Y is vertical while +X and +Z project symmetrically
  // down-right/down-left, keeping the three axes close to 120 degrees apart.
  direction: [1, 1, 1] as AxisVector,
  up: [0, 1, 0] as AxisVector,
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
