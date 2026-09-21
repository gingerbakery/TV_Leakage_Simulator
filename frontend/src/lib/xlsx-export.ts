export type ExcelCellValue = string | number | boolean | null | undefined

export interface ExcelSheetDefinition {
  name: string
  rows: ExcelCellValue[][]
  columnWidths?: number[]
  images?: Array<{ png: Uint8Array; width: number; height: number; row?: number; column?: number }>
}

const encoder = new TextEncoder()

function escapeXml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;')
}

function columnName(index: number): string {
  let value = index + 1
  let result = ''
  while (value > 0) {
    value -= 1
    result = String.fromCharCode(65 + value % 26) + result
    value = Math.floor(value / 26)
  }
  return result
}

function safeSheetNames(sheets: ExcelSheetDefinition[]): string[] {
  const used = new Set<string>()
  return sheets.map((sheet, index) => {
    const base = (sheet.name.replace(/[\\/?*:[\]]/g, ' ').trim() || `Sheet ${index + 1}`).slice(0, 31)
    let candidate = base
    let suffix = 2
    while (used.has(candidate.toLowerCase())) {
      const marker = ` ${suffix++}`
      candidate = `${base.slice(0, 31 - marker.length)}${marker}`
    }
    used.add(candidate.toLowerCase())
    return candidate
  })
}

function cellXml(value: ExcelCellValue, address: string, header: boolean): string {
  const style = header ? ' s="1"' : ''
  if (typeof value === 'number' && Number.isFinite(value)) {
    return `<c r="${address}"${style}><v>${value}</v></c>`
  }
  if (typeof value === 'boolean') {
    return `<c r="${address}" t="b"${style}><v>${value ? 1 : 0}</v></c>`
  }
  const text = value == null ? '' : String(value)
  return `<c r="${address}" t="inlineStr"${style}><is><t xml:space="preserve">${escapeXml(text)}</t></is></c>`
}

function worksheetXml(sheet: ExcelSheetDefinition): string {
  const columnCount = sheet.rows.reduce((maximum, row) => Math.max(maximum, row.length), 1)
  const widths = Array.from({ length: columnCount }, (_, column) => {
    const requested = sheet.columnWidths?.[column]
    if (requested != null) return Math.max(6, Math.min(60, requested))
    const contentWidth = Math.max(
      6,
      ...sheet.rows.slice(0, 200).map((row) => String(row[column] ?? '').length + 2),
    )
    return Math.min(32, contentWidth)
  })
  const columns = widths.map((width, index) =>
    `<col min="${index + 1}" max="${index + 1}" width="${width}" customWidth="1"/>`,
  ).join('')
  const rows = sheet.rows.map((row, rowIndex) => {
    const cells = row.map((value, columnIndex) =>
      cellXml(value, `${columnName(columnIndex)}${rowIndex + 1}`, rowIndex === 0),
    ).join('')
    return `<row r="${rowIndex + 1}">${cells}</row>`
  }).join('')
  const lastCell = `${columnName(columnCount - 1)}${Math.max(1, sheet.rows.length)}`
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">` +
    `<dimension ref="A1:${lastCell}"/>` +
    `<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>` +
    `<cols>${columns}</cols><sheetData>${rows}</sheetData>` +
    (sheet.rows.length > 0 ? `<autoFilter ref="A1:${columnName(columnCount - 1)}${sheet.rows.length}"/>` : '') +
    (sheet.images?.length ? '<drawing r:id="rIdDrawing"/>' : '') + `</worksheet>`
}

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff
  for (const byte of bytes) {
    crc ^= byte
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1))
    }
  }
  return (crc ^ 0xffffffff) >>> 0
}

function concat(parts: Uint8Array[]): Uint8Array {
  const output = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0))
  let offset = 0
  for (const part of parts) {
    output.set(part, offset)
    offset += part.length
  }
  return output
}

function zipStore(files: Array<{ path: string; content: string | Uint8Array }>): Uint8Array {
  const localParts: Uint8Array[] = []
  const centralParts: Uint8Array[] = []
  let offset = 0
  for (const file of files) {
    const name = encoder.encode(file.path)
    const data = typeof file.content === 'string' ? encoder.encode(file.content) : file.content
    const checksum = crc32(data)
    const local = new Uint8Array(30 + name.length)
    const localView = new DataView(local.buffer)
    localView.setUint32(0, 0x04034b50, true)
    localView.setUint16(4, 20, true)
    localView.setUint16(6, 0x0800, true)
    localView.setUint16(8, 0, true)
    localView.setUint32(14, checksum, true)
    localView.setUint32(18, data.length, true)
    localView.setUint32(22, data.length, true)
    localView.setUint16(26, name.length, true)
    local.set(name, 30)
    localParts.push(local, data)

    const central = new Uint8Array(46 + name.length)
    const centralView = new DataView(central.buffer)
    centralView.setUint32(0, 0x02014b50, true)
    centralView.setUint16(4, 20, true)
    centralView.setUint16(6, 20, true)
    centralView.setUint16(8, 0x0800, true)
    centralView.setUint16(10, 0, true)
    centralView.setUint32(16, checksum, true)
    centralView.setUint32(20, data.length, true)
    centralView.setUint32(24, data.length, true)
    centralView.setUint16(28, name.length, true)
    centralView.setUint32(42, offset, true)
    central.set(name, 46)
    centralParts.push(central)
    offset += local.length + data.length
  }
  const centralDirectory = concat(centralParts)
  const end = new Uint8Array(22)
  const endView = new DataView(end.buffer)
  endView.setUint32(0, 0x06054b50, true)
  endView.setUint16(8, files.length, true)
  endView.setUint16(10, files.length, true)
  endView.setUint32(12, centralDirectory.length, true)
  endView.setUint32(16, offset, true)
  return concat([...localParts, centralDirectory, end])
}

export function createExcelWorkbook(sheets: ExcelSheetDefinition[]): Blob {
  if (sheets.length === 0) throw new Error('Excel workbook requires at least one sheet')
  const names = safeSheetNames(sheets)
  const workbookSheets = names.map((name, index) =>
    `<sheet name="${escapeXml(name)}" sheetId="${index + 1}" r:id="rId${index + 1}"/>`,
  ).join('')
  const workbookRelationships = sheets.map((_, index) =>
    `<Relationship Id="rId${index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${index + 1}.xml"/>`,
  ).join('')
  const contentOverrides = sheets.map((_, index) =>
    `<Override PartName="/xl/worksheets/sheet${index + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`,
  ).join('')
  const drawingOverrides = sheets.flatMap((sheet, index) => sheet.images?.length
    ? [`<Override PartName="/xl/drawings/drawing${index + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>`] : []).join('')
  const files: Array<{ path: string; content: string | Uint8Array }> = [
    {
      path: '[Content_Types].xml',
      content: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${contentOverrides}${drawingOverrides}</Types>`,
    },
    {
      path: '_rels/.rels',
      content: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
    },
    {
      path: 'xl/workbook.xml',
      content: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${workbookSheets}</sheets></workbook>`,
    },
    {
      path: 'xl/_rels/workbook.xml.rels',
      content: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${workbookRelationships}<Relationship Id="rId${sheets.length + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`,
    },
    {
      path: 'xl/styles.xml',
      content: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1976D2"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`,
    },
    ...sheets.map((sheet, index) => ({
      path: `xl/worksheets/sheet${index + 1}.xml`,
      content: worksheetXml(sheet),
    })),
  ]
  const relationships = (body: string) => `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${body}</Relationships>`
  sheets.forEach((sheet, sheetIndex) => {
    if (!sheet.images?.length) return
    const number = sheetIndex + 1
    files.push({ path: `xl/worksheets/_rels/sheet${number}.xml.rels`, content: relationships(
      `<Relationship Id="rIdDrawing" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing${number}.xml"/>`,
    ) })
    const anchors = sheet.images.map((image, imageIndex) => {
      const id = imageIndex + 1
      files.push({ path: `xl/media/image${number}-${id}.png`, content: image.png })
      const cx = Math.round(image.width * 9525)
      const cy = Math.round(image.height * 9525)
      return `<xdr:oneCellAnchor><xdr:from><xdr:col>${image.column ?? 0}</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>${image.row ?? 4}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:ext cx="${cx}" cy="${cy}"/><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="${id}" name="Heatmap ${id}"/><xdr:cNvPicPr/></xdr:nvPicPr><xdr:blipFill><a:blip r:embed="rId${id}"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill><xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${cx}" cy="${cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic><xdr:clientData/></xdr:oneCellAnchor>`
    }).join('')
    files.push({ path: `xl/drawings/drawing${number}.xml`, content: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">${anchors}</xdr:wsDr>` })
    files.push({ path: `xl/drawings/_rels/drawing${number}.xml.rels`, content: relationships(sheet.images.map((_, index) =>
      `<Relationship Id="rId${index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image${number}-${index + 1}.png"/>`,
    ).join('')) })
  })
  const archive = zipStore(files)
  const blobBytes = new Uint8Array(archive.length)
  blobBytes.set(archive)
  return new Blob([blobBytes.buffer], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  })
}
