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
  it('embeds PNG bytes with worksheet drawing and media relationships', async () => {
    const png = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])
    const blob = createExcelWorkbook([{ name: 'Heatmap', rows: [['Case', 'Receiver']],
      images: [{ png, width: 800, height: 600, row: 5 }],
    }])
    const text = new TextDecoder().decode(await blob.arrayBuffer())
    expect(text).toContain('xl/media/image1-1.png')
    expect(text).toContain('xl/drawings/drawing1.xml')
    expect(text).toContain('xl/worksheets/_rels/sheet1.xml.rels')
    expect(text).toContain('<drawing r:id="rIdDrawing"/>')
    expect(text).toContain('Target="../media/image1-1.png"')
    expect(text).toContain('<xdr:row>5</xdr:row>')
    expect(text).toContain('cx="7620000" cy="5715000"')
    expect(text).toContain('Extension="png" ContentType="image/png"')
    const bytes = new Uint8Array(await blob.arrayBuffer())
    expect(bytes.some((_, index) => png.every((byte, offset) => bytes[index + offset] === byte))).toBe(true)
  })
})
