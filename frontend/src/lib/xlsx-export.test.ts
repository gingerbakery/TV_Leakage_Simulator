import { describe, expect, it } from 'vitest'

import { createExcelWorkbook } from './xlsx-export'

describe('xlsx export', () => {
  it('creates a standard OOXML workbook with typed cells and multiple sheets', async () => {
    const blob = createExcelWorkbook([
      {
        name: 'Compare Cases',
        rows: [['Case', 'Change (%)', 'Baseline'], ['CASE 01', -12.5, true]],
      },
      {
        name: 'Receiver Results',
        rows: [['Receiver', 'Peak (nit)'], ['Receiver 1', 8.25]],
      },
    ])
    const bytes = new Uint8Array(await blob.arrayBuffer())
    const archiveText = new TextDecoder().decode(bytes)

    expect(blob.type).toBe('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    expect([...bytes.slice(0, 4)]).toEqual([0x50, 0x4b, 0x03, 0x04])
    expect(archiveText).toContain('[Content_Types].xml')
    expect(archiveText).toContain('xl/worksheets/sheet2.xml')
    expect(archiveText).toContain('Compare Cases')
    expect(archiveText).toContain('<v>-12.5</v>')
    expect(archiveText).toContain('<v>1</v>')
  })
})
