import { describe, expect, it } from 'vitest'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { resultExcelDetailSheets } from './result-excel-details'

describe('complete report Excel sheets', () => {
  it('exports all report tabs, raw heatmap cells and every stored event field', () => {
    const result = createRayTraceResultFixture()
    result.receivers[0].width_mm = 10
    result.receivers[0].height_mm = 6
    const sheets = resultExcelDetailSheets([{ name: 'Case A', cad_name: 'assembly.step', result }])
    expect(sheets.map((sheet) => sheet.name)).toEqual([
      'Ray Summary', 'Surface Optical', 'Multi-bounce', 'Receiver Details',
      'Compute Performance', 'Convergence History', 'All Recorded Metrics', 'Detailed Run Setup',
      'Heatmap Cells', 'Stored Ray Paths',
    ])
    const cells = sheets.find((sheet) => sheet.name === 'Heatmap Cells')!.rows
    expect(cells).toHaveLength(5)
    expect(cells[1].slice(7, 11)).toEqual([-2.5, -1.5, 1, 0.001])
    expect(cells[1][13]).toBeCloseTo(1000 * result.config.k_abs * result.config.k_brdf / Math.PI)
    expect(cells[4].slice(7, 9)).toEqual([2.5, 1.5])
    const paths = sheets.find((sheet) => sheet.name === 'Stored Ray Paths')!.rows
    expect(paths.some((row) => row[5] === 'point.0')).toBe(true)
    expect(paths.some((row) => row[5] === 'incoming_energy_lumen')).toBe(true)
    const details = sheets.find((sheet) => sheet.name === 'Receiver Details')!.rows
    expect(details.some((row) => row[3] === 'receivers.0.width_mm' && row[4] === 10)).toBe(true)
  })
})
