export interface FullViewPipSize { width: number; height: number }
export interface FullViewPipRect extends FullViewPipSize { x: number; y: number }
export const fullViewPipMargin = 14

// Both the WebGL viewport and its HTML frame use this CSS-pixel rectangle.
export function fullViewPipRect(viewport: FullViewPipSize, requested: FullViewPipSize | null): FullViewPipRect | null {
  const availableWidth = Math.floor(viewport.width) - fullViewPipMargin * 2
  const availableHeight = Math.floor(viewport.height) - fullViewPipMargin * 2
  if (availableWidth <= 24 || availableHeight <= 24) return null
  const clamp = (value: number, min: number, max: number) => Math.round(Math.max(Math.min(min, max), Math.min(max, value)))
  const width = requested
    ? clamp(requested.width, 120, availableWidth)
    : Math.min(220, Math.floor(viewport.width * .34), availableWidth)
  const height = requested
    ? clamp(requested.height, 90, availableHeight)
    : Math.min(160, Math.floor(viewport.height * .34), availableHeight)
  if (width <= 24 || height <= 24) return null
  return { x: Math.floor(viewport.width) - width - fullViewPipMargin,
    y: Math.floor(viewport.height) - height - fullViewPipMargin, width, height }
}
