import type { RayTraceResult, ReceiverGrid } from '@/api'

import {
  receiverHeatmapColor,
  receiverHeatmapDisplayValues,
} from './receiver-heatmap'

export type ReceiverHeatmap3dColorMode = 'color' | 'mono'

export interface ReceiverHeatmap3dScale {
  minNit: number
  maxNit: number
}

export function receiverGridLuminanceValues(
  grid: ReceiverGrid,
  kAbs: number,
  kBrdf: number,
  multiplier = 1,
): number[] {
  const binAreaM2 = Math.max(grid.bin_area_mm2 * 1e-6, 1e-18)
  const scale = (Math.max(0, kAbs) * Math.max(0, kBrdf)) /
    (binAreaM2 * Math.PI)
  return receiverHeatmapDisplayValues(grid).map(
    (value) => value * scale * multiplier,
  )
}

export function receiverHeatmap3dCommonPeak(result: RayTraceResult): number {
  let peak = 0
  for (const grid of result.receiver_grids) {
    const values = receiverGridLuminanceValues(
      grid,
      result.config.k_abs,
      result.config.k_brdf,
    )
    for (const value of values) peak = Math.max(peak, value)
  }
  return peak
}

export function receiverHeatmap3dRgba(
  values: number[],
  scale: ReceiverHeatmap3dScale,
  colorMode: ReceiverHeatmap3dColorMode = 'color',
): Uint8ClampedArray {
  const pixels = new Uint8ClampedArray(values.length * 4)
  const span = Math.max(scale.maxNit - scale.minNit, 1e-12)
  for (let index = 0; index < values.length; index += 1) {
    const normalized = Math.sqrt(Math.min(1, Math.max(
      0,
      (values[index] - scale.minNit) / span,
    )))
    const gray = Math.round(normalized * 255)
    const [red, green, blue] =
      colorMode === 'mono'
        ? [gray, gray, gray]
        : receiverHeatmapColor(normalized)
    const offset = index * 4
    pixels[offset] = red
    pixels[offset + 1] = green
    pixels[offset + 2] = blue
    pixels[offset + 3] = 255
  }
  return pixels
}
