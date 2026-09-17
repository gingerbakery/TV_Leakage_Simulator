// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { resultExcelImageSheets } from './result-excel-images'
import { receiverHeatmapColor } from './receiver-heatmap'

afterEach(() => vi.restoreAllMocks())

describe('Excel Receiver Heatmap images', () => {
  it('creates separate labeled images for every Case and Receiver with local +Y at the top', () => {
    const colors: string[] = []
    let color = ''
    const context = new Proxy({}, {
      get: (_, property) => property === 'fillRect' ? vi.fn(() => colors.push(color)) : vi.fn(),
      set: (_, property, value) => { if (property === 'fillStyle') color = value; return true },
    })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as CanvasRenderingContext2D)
    vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,iVBORw0KGgo=')
    const result = createRayTraceResultFixture()
    result.receivers[0].width_mm = 10
    result.receivers[0].height_mm = 6
    const cases = [{ name: 'A', cad_name: 'a.step', result }, { name: 'B', cad_name: 'b.step', result }]
    const sheets = resultExcelImageSheets(cases, { mode: 'auto', minNit: 0, maxNit: 1 })
    expect(sheets).toHaveLength(2)
    expect(sheets[0].rows[1].slice(0, 3)).toEqual(['A', 'Main receiver', 'a.step'])
    expect(sheets[1].rows[1][0]).toBe('B')
    expect(sheets[0].images![0]).toMatchObject({ width: 800, height: 600, row: 5 })
    expect(sheets[0].images![0].png).toEqual(new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]))
    // Source row 1 (positive local Y) is drawn first, not row 0.
    expect(colors[1]).toBe(`rgb(${receiverHeatmapColor(Math.sqrt(0.003 / 0.004)).join(',')})`)
    const custom = resultExcelImageSheets(cases, { mode: 'customize', minNit: 2, maxNit: 20 })
    expect(custom[0].rows[3].slice(0, 3)).toEqual(['customize', 2, 20])
    const compare = resultExcelImageSheets(cases, { mode: 'compare', minNit: 0, maxNit: 1, correspondingPeak: () => 1000 })
    expect(compare[0].rows[3][2]).toBe(1000)
    expect(compare[1].rows[3][2]).toBe(1000)
  })
  it('reports unavailable Canvas instead of silently omitting images', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    expect(() => resultExcelImageSheets([{ name: 'A', cad_name: 'a.step', result: createRayTraceResultFixture() }],
      { mode: 'auto', minNit: 0, maxNit: 10 })).toThrow('Canvas')
  })
})
