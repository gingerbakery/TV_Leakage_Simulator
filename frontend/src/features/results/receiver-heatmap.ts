import type { ReceiverGrid } from '@/api'

const maximumPlotWidthPx = 684
const maximumPlotHeightPx = 576

export interface ReceiverHeatmapLayout {
  aspectRatio: number
  heightMm: number
  preferredWidthPx: number
  widthMm: number
}

export interface ReceiverAxisTick {
  label: string
  positionPercent: number
  value: number
}

export interface ReceiverHeatmapViewport {
  centerX: number
  centerY: number
  zoom: number
}

export interface ReceiverHeatmapViewportBounds {
  maxX: number
  maxY: number
  minX: number
  minY: number
}

export interface ReceiverHeatmapSample {
  column: number
  displayRow: number
  fluxDensityLumenPerMm2: number
  fluxLumen: number
  illuminanceLux: number
  sourceRow: number
  xMm: number
  yMm: number
}

export type ReceiverHeatmapColor = readonly [
  red: number,
  green: number,
  blue: number,
]

export const initialReceiverHeatmapViewport: ReceiverHeatmapViewport = {
  centerX: 0.5,
  centerY: 0.5,
  zoom: 1,
}

const heatmapColorStops: ReadonlyArray<
  readonly [position: number, color: ReceiverHeatmapColor]
> = [
  [0, [8, 20, 190]],
  [0.2, [0, 82, 255]],
  [0.4, [0, 220, 255]],
  [0.55, [0, 235, 110]],
  [0.7, [250, 235, 0]],
  [0.85, [255, 112, 0]],
  [1, [238, 28, 20]],
]

function positiveDimension(value: number): number {
  return Number.isFinite(value) && value > 0 ? value : 1
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

export function receiverHeatmapLayout(
  widthMm: number,
  heightMm: number,
  physicalScalePxPerMm?: number,
): ReceiverHeatmapLayout {
  const safeWidth = positiveDimension(widthMm)
  const safeHeight = positiveDimension(heightMm)
  const aspectRatio = safeWidth / safeHeight
  const preferredWidthPx =
    physicalScalePxPerMm !== undefined && physicalScalePxPerMm > 0
      ? safeWidth * physicalScalePxPerMm
      : Math.min(
          maximumPlotWidthPx,
          maximumPlotHeightPx * aspectRatio,
        )
  return {
    aspectRatio,
    heightMm: safeHeight,
    preferredWidthPx,
    widthMm: safeWidth,
  }
}

export function receiverHeatmapPhysicalScale(
  receivers: ReadonlyArray<{ width_mm: number; height_mm: number }>,
): number | undefined {
  if (receivers.length === 0) return undefined
  const maximumWidthMm = Math.max(
    ...receivers.map((receiver) => positiveDimension(receiver.width_mm)),
  )
  const maximumHeightMm = Math.max(
    ...receivers.map((receiver) => positiveDimension(receiver.height_mm)),
  )
  return Math.min(
    maximumPlotWidthPx / maximumWidthMm,
    maximumPlotHeightPx / maximumHeightMm,
  )
}

export function receiverHeatmapDisplayValues(
  grid: ReceiverGrid,
): number[] {
  const columns = Math.max(1, grid.resolution[0])
  const rows = Math.max(1, grid.resolution[1])
  const values: number[] = []
  for (let displayRow = 0; displayRow < rows; displayRow += 1) {
    const sourceRow = rows - 1 - displayRow
    for (let column = 0; column < columns; column += 1) {
      const value = Number(grid.flux_lumen[sourceRow]?.[column] ?? 0)
      values.push(Number.isFinite(value) ? Math.max(0, value) : 0)
    }
  }
  return values
}

export function formatReceiverCoordinate(value: number): string {
  const magnitude = Math.abs(value)
  const digits =
    magnitude >= 1 ? 3 : magnitude >= 0.01 ? 4 : 6
  return Number(value.toFixed(digits)).toString()
}

function niceTickStep(range: number, targetIntervals = 6): number {
  const roughStep = range / Math.max(1, targetIntervals)
  const exponent = 10 ** Math.floor(Math.log10(roughStep))
  const fraction = roughStep / exponent
  const niceFraction =
    fraction <= 1
      ? 1
      : fraction <= 2
        ? 2
        : fraction <= 2.5
          ? 2.5
          : fraction <= 5
            ? 5
            : 10
  return niceFraction * exponent
}

export function receiverAxisTicksForRange(
  minimumValue: number,
  maximumValue: number,
): ReceiverAxisTick[] {
  const safeMinimum = Number.isFinite(minimumValue)
    ? minimumValue
    : -0.5
  const safeMaximum =
    Number.isFinite(maximumValue) && maximumValue > safeMinimum
      ? maximumValue
      : safeMinimum + 1
  const safeSpan = safeMaximum - safeMinimum
  const step = niceTickStep(safeSpan)
  const values = new Set<number>([safeMinimum, safeMaximum])
  const firstTick = Math.ceil(safeMinimum / step) * step
  for (
    let value = firstTick;
    value <= safeMaximum + step * 1e-6;
    value += step
  ) {
    values.add(Number(value.toFixed(9)))
  }
  return [...values]
    .sort((left, right) => left - right)
    .map((value) => ({
      label: formatReceiverCoordinate(value),
      positionPercent:
        ((value - safeMinimum) / safeSpan) * 100,
      value,
    }))
}

export function receiverAxisTicks(spanMm: number): ReceiverAxisTick[] {
  const safeSpan = positiveDimension(spanMm)
  const halfSpan = safeSpan / 2
  return receiverAxisTicksForRange(-halfSpan, halfSpan)
}

function normalizedReceiverHeatmapViewport(
  viewport: ReceiverHeatmapViewport,
  maximumZoom = 128,
): ReceiverHeatmapViewport {
  const zoom = clamp(
    Number.isFinite(viewport.zoom) ? viewport.zoom : 1,
    1,
    Math.max(1, maximumZoom),
  )
  const halfVisibleSpan = 0.5 / zoom
  return {
    centerX: clamp(
      Number.isFinite(viewport.centerX) ? viewport.centerX : 0.5,
      halfVisibleSpan,
      1 - halfVisibleSpan,
    ),
    centerY: clamp(
      Number.isFinite(viewport.centerY) ? viewport.centerY : 0.5,
      halfVisibleSpan,
      1 - halfVisibleSpan,
    ),
    zoom,
  }
}

export function receiverHeatmapViewportBounds(
  viewport: ReceiverHeatmapViewport,
): ReceiverHeatmapViewportBounds {
  const normalized = normalizedReceiverHeatmapViewport(viewport)
  const halfVisibleSpan = 0.5 / normalized.zoom
  return {
    maxX: normalized.centerX + halfVisibleSpan,
    maxY: normalized.centerY + halfVisibleSpan,
    minX: normalized.centerX - halfVisibleSpan,
    minY: normalized.centerY - halfVisibleSpan,
  }
}

export function zoomReceiverHeatmapViewport(
  viewport: ReceiverHeatmapViewport,
  pointerX: number,
  pointerY: number,
  wheelDeltaY: number,
  maximumZoom = 128,
): ReceiverHeatmapViewport {
  const normalized = normalizedReceiverHeatmapViewport(
    viewport,
    maximumZoom,
  )
  const bounds = receiverHeatmapViewportBounds(normalized)
  const localX = clamp(pointerX, 0, 1)
  const localY = clamp(pointerY, 0, 1)
  const anchorX =
    bounds.minX + localX * (bounds.maxX - bounds.minX)
  const anchorY =
    bounds.minY + localY * (bounds.maxY - bounds.minY)
  const nextZoom = clamp(
    normalized.zoom * Math.exp(-wheelDeltaY * 0.002),
    1,
    Math.max(1, maximumZoom),
  )
  const nextVisibleSpan = 1 / nextZoom
  return normalizedReceiverHeatmapViewport(
    {
      centerX:
        anchorX - localX * nextVisibleSpan + nextVisibleSpan / 2,
      centerY:
        anchorY - localY * nextVisibleSpan + nextVisibleSpan / 2,
      zoom: nextZoom,
    },
    maximumZoom,
  )
}

export function receiverHeatmapSample(
  grid: ReceiverGrid,
  widthMm: number,
  heightMm: number,
  viewport: ReceiverHeatmapViewport,
  pointerX: number,
  pointerY: number,
): ReceiverHeatmapSample {
  const columns = Math.max(1, grid.resolution[0])
  const rows = Math.max(1, grid.resolution[1])
  const bounds = receiverHeatmapViewportBounds(viewport)
  const normalizedX =
    bounds.minX +
    clamp(pointerX, 0, 1) * (bounds.maxX - bounds.minX)
  const normalizedDisplayY =
    bounds.minY +
    clamp(pointerY, 0, 1) * (bounds.maxY - bounds.minY)
  const column = Math.min(
    columns - 1,
    Math.floor(normalizedX * columns),
  )
  const displayRow = Math.min(
    rows - 1,
    Math.floor(normalizedDisplayY * rows),
  )
  const sourceRow = rows - 1 - displayRow
  const rawFlux = Number(grid.flux_lumen[sourceRow]?.[column] ?? 0)
  const fluxLumen =
    Number.isFinite(rawFlux) && rawFlux > 0 ? rawFlux : 0
  const binAreaMm2 =
    Number.isFinite(grid.bin_area_mm2) && grid.bin_area_mm2 > 0
      ? grid.bin_area_mm2
      : 0
  const fluxDensityLumenPerMm2 =
    binAreaMm2 > 0 ? fluxLumen / binAreaMm2 : 0
  return {
    column,
    displayRow,
    fluxDensityLumenPerMm2,
    fluxLumen,
    illuminanceLux: fluxDensityLumenPerMm2 * 1_000_000,
    sourceRow,
    xMm: (normalizedX - 0.5) * positiveDimension(widthMm),
    yMm:
      (0.5 - normalizedDisplayY) *
      positiveDimension(heightMm),
  }
}

export function receiverHeatmapColor(
  normalizedValue: number,
): ReceiverHeatmapColor {
  const normalized = Math.min(
    1,
    Math.max(0, Number.isFinite(normalizedValue) ? normalizedValue : 0),
  )
  for (let index = 1; index < heatmapColorStops.length; index += 1) {
    const [rightPosition, rightColor] = heatmapColorStops[index]
    const [leftPosition, leftColor] = heatmapColorStops[index - 1]
    if (normalized > rightPosition) continue
    const interval = Math.max(rightPosition - leftPosition, 1e-12)
    const mix = (normalized - leftPosition) / interval
    return [
      Math.round(leftColor[0] + (rightColor[0] - leftColor[0]) * mix),
      Math.round(leftColor[1] + (rightColor[1] - leftColor[1]) * mix),
      Math.round(leftColor[2] + (rightColor[2] - leftColor[2]) * mix),
    ]
  }
  return heatmapColorStops[heatmapColorStops.length - 1][1]
}
