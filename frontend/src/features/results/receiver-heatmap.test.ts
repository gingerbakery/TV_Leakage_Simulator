import { describe, expect, it } from 'vitest'

import type { ReceiverGrid } from '@/api'

import {
  formatReceiverCoordinate,
  initialReceiverHeatmapViewport,
  receiverAxisTicks,
  receiverAxisTicksForRange,
  receiverHeatmapColor,
  receiverHeatmapDisplayValues,
  receiverHeatmapLayout,
  receiverHeatmapPhysicalScale,
  receiverHeatmapSample,
  receiverHeatmapViewportBounds,
  zoomReceiverHeatmapViewport,
} from './receiver-heatmap'

describe('receiver heatmap geometry', () => {
  it('fits the plot while preserving the physical receiver ratio', () => {
    expect(receiverHeatmapLayout(60, 30)).toEqual({
      aspectRatio: 2,
      heightMm: 30,
      preferredWidthPx: 684,
      widthMm: 60,
    })
    expect(receiverHeatmapLayout(30, 60)).toEqual({
      aspectRatio: 0.5,
      heightMm: 60,
      preferredWidthPx: 288,
      widthMm: 30,
    })
  })

  it('uses one physical scale across differently sized receivers', () => {
    const scale = receiverHeatmapPhysicalScale([
      { width_mm: 5, height_mm: 3 },
      { width_mm: 10, height_mm: 10 },
    ])

    expect(scale).toBeCloseTo(57.6)
    expect(receiverHeatmapLayout(5, 3, scale).preferredWidthPx).toBeCloseTo(288)
    expect(receiverHeatmapLayout(10, 10, scale).preferredWidthPx).toBeCloseTo(576)
  })

  it('maps backend local positive Y to the top of the display', () => {
    const grid: ReceiverGrid = {
      receiver_id: 'receiver-test',
      resolution: [2, 2],
      bin_area_mm2: 1,
      flux_lumen: [
        [1, 2],
        [3, 4],
      ],
      hit_count: 4,
    }

    expect(receiverHeatmapDisplayValues(grid)).toEqual([3, 4, 1, 2])
  })

  it('formats compact millimeter coordinate labels', () => {
    expect(formatReceiverCoordinate(15)).toBe('15')
    expect(formatReceiverCoordinate(0.125)).toBe('0.125')
  })

  it('builds symmetric LightTools-style axis ticks', () => {
    expect(receiverAxisTicks(30)).toEqual([
      { label: '-15', positionPercent: 0, value: -15 },
      {
        label: '-10',
        positionPercent: 16.666666666666664,
        value: -10,
      },
      {
        label: '-5',
        positionPercent: 33.33333333333333,
        value: -5,
      },
      { label: '0', positionPercent: 50, value: 0 },
      {
        label: '5',
        positionPercent: 66.66666666666666,
        value: 5,
      },
      {
        label: '10',
        positionPercent: 83.33333333333334,
        value: 10,
      },
      { label: '15', positionPercent: 100, value: 15 },
    ])
  })

  it('rebuilds coordinate ticks for a zoomed receiver range', () => {
    const ticks = receiverAxisTicksForRange(-2.5, 2.5)
    expect(ticks[0]).toEqual({
      label: '-2.5',
      positionPercent: 0,
      value: -2.5,
    })
    expect(ticks.at(-1)).toEqual({
      label: '2.5',
      positionPercent: 100,
      value: 2.5,
    })
    expect(ticks.some((tick) => tick.value === 0)).toBe(true)
  })

  it('zooms around the mouse cursor without moving its target', () => {
    const pointerX = 0.75
    const pointerY = 0.25
    const nextViewport = zoomReceiverHeatmapViewport(
      initialReceiverHeatmapViewport,
      pointerX,
      pointerY,
      -300,
      32,
    )
    const bounds = receiverHeatmapViewportBounds(nextViewport)
    const anchoredX =
      bounds.minX + pointerX * (bounds.maxX - bounds.minX)
    const anchoredY =
      bounds.minY + pointerY * (bounds.maxY - bounds.minY)

    expect(nextViewport.zoom).toBeGreaterThan(1)
    expect(anchoredX).toBeCloseTo(pointerX)
    expect(anchoredY).toBeCloseTo(pointerY)
  })

  it('samples local coordinates and accumulated flux under the cursor', () => {
    const grid: ReceiverGrid = {
      receiver_id: 'receiver-test',
      resolution: [2, 2],
      bin_area_mm2: 1,
      flux_lumen: [
        [0.001, 0.002],
        [0.003, 0.004],
      ],
      hit_count: 4,
    }
    const sample = receiverHeatmapSample(
      grid,
      40,
      20,
      initialReceiverHeatmapViewport,
      0.75,
      0.25,
    )

    expect(sample).toMatchObject({
      column: 1,
      displayRow: 0,
      fluxDensityLumenPerMm2: 0.004,
      fluxLumen: 0.004,
      illuminanceLux: 4000,
      sourceRow: 1,
      xMm: 10,
      yMm: 5,
    })
  })

  it('uses a blue-to-red scientific heatmap palette', () => {
    expect(receiverHeatmapColor(0)).toEqual([8, 20, 190])
    expect(receiverHeatmapColor(0.4)).toEqual([0, 220, 255])
    expect(receiverHeatmapColor(1)).toEqual([238, 28, 20])
  })
})
