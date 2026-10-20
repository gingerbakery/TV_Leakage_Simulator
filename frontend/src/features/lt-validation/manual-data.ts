import type { ValidationDataset, ValidationReceiver, ValidationSource, Vec3 } from './types'

export interface ManualDraft {
  name: string
  raw: '' | 'raw' | 'smoothed'
  unit: '' | 'flux_lm' | 'illuminance_lx'
  matrix: string
  tableFormat: 'matrix' | 'coordinates'
  uEdges: string
  vEdges: string
  center: string
  uAxis: string
  vAxis: string
  normal: string
  acceptance: string
  aperture: '' | 'none'
  total: string
  tolerance: string
  sources: string
  coordinateOrderConfirmed: boolean
  extractedOrder?: string
}

export function createManualDraft(dataset: ValidationDataset | null, receiver: ValidationReceiver | undefined): ManualDraft {
  const vector = (value: number[] | null | undefined) => value?.join(', ') ?? ''
  return {
    name: receiver?.name ?? '',
    raw: receiver?.raw === true ? 'raw' : receiver?.raw === false ? 'smoothed' : '',
    unit: receiver?.flux_lm && receiver.quantity === 'photometric' ? 'flux_lm' : '',
    matrix: receiver?.flux_lm?.map((row) => row.join('\t')).join('\n') ?? '',
    tableFormat: 'matrix',
    uEdges: vector(receiver?.u_edges_mm), vEdges: vector(receiver?.v_edges_mm),
    center: vector(receiver?.center_mm), uAxis: vector(receiver?.u_axis),
    vAxis: vector(receiver?.v_axis), normal: vector(receiver?.normal),
    acceptance: receiver?.acceptance_angle_deg?.toString() ?? '',
    aperture: receiver?.aperture ?? '', total: receiver?.total_flux_lm?.toString() ?? '',
    tolerance: receiver?.total_flux_tolerance_lm?.toString() ?? '',
    sources: JSON.stringify(dataset?.sources ?? [], null, 2),
    coordinateOrderConfirmed: Boolean(receiver?.flux_lm && receiver.u_edges_mm && receiver.v_edges_mm),
  }
}

function numbers(text: string, label: string): number[] | null {
  if (!text.trim()) return null
  if (/^[ ]*\t|\t[ ]*$|\t[ ]*\t/.test(text)) throw new Error(`${label}: 빈 TSV 셀이나 빈 좌표를 0으로 간주할 수 없습니다.`)
  const normalized = text.trim().replace(/\u2212/g, '-')
  if (/^[,;]|[,;]$|[,;]\s*[,;]/.test(normalized)) throw new Error(`${label}: 빈 셀이나 빈 좌표를 0으로 간주할 수 없습니다.`)
  const values = normalized.split(/[\s,;]+/).map(Number)
  if (values.some((value) => !Number.isFinite(value))) throw new Error(`${label}: 유한한 숫자만 입력하세요.`)
  return values
}

function vector(text: string, label: string): Vec3 | null {
  const values = numbers(text, label)
  if (values && values.length !== 3) throw new Error(`${label}: X, Y, Z 세 값이 필요합니다.`)
  return values as Vec3 | null
}

function scalar(text: string, label: string): number | null {
  if (!text.trim()) return null
  const value = Number(text)
  if (!Number.isFinite(value) || value < 0) throw new Error(`${label}: 0 이상의 유한한 숫자가 필요합니다.`)
  return value
}

export function buildManualDataset(draft: ManualDraft, original: ValidationDataset | null, selectedId: string | null): ValidationDataset {
  const uEdges = numbers(draft.uEdges, 'U 셀 경계')
  const vEdges = numbers(draft.vEdges, 'V 셀 경계')
  for (const [label, edges] of [['U', uEdges], ['V', vEdges]] as const) {
    if (edges && (edges.length < 2 || edges.some((value, index) => index > 0 && value <= edges[index - 1]))) {
      throw new Error(`${label} 셀 경계는 작은 값부터 중복 없이 입력하세요.`)
    }
  }
  let flux: number[][] | null = null
  if (draft.matrix.trim()) {
    if (!draft.unit) throw new Error('원시 표의 단위를 선택하세요.')
    if (draft.tableFormat === 'matrix' && !draft.coordinateOrderConfirmed) throw new Error('행·열의 좌표 순서를 확인하세요.')
    if (!uEdges || !vEdges) throw new Error('원시 표에는 U/V 셀 경계가 필요합니다.')
    const lines = draft.matrix.split(/\r?\n/)
    while (lines.length && /^[ ]*$/.test(lines[0])) lines.shift()
    while (lines.length && /^[ ]*$/.test(lines.at(-1)!)) lines.pop()
    let matrix: number[][]
    if (draft.tableFormat === 'coordinates') {
      if (/^[ ]*\t|\t[ ]*$|\t[ ]*\t/.test(lines[0])) throw new Error('U 중심좌표: 빈 TSV 셀을 사용할 수 없습니다.')
      const headingText = lines[0].replace(/^[ ]*\S+?[\s,;]+/, '')
      const headings = numbers(headingText, 'U 중심좌표') ?? []
      const rows = lines.slice(1).map((line) => numbers(line, '좌표 포함 표') ?? [])
      if (headings.length !== uEdges.length - 1 || rows.length !== vEdges.length - 1 || rows.some((row) => row.length !== headings.length + 1) || headings.some((value) => !Number.isFinite(value))) {
        throw new Error('좌표 포함 표: 첫 행은 Y\\X와 U 중심좌표, 첫 열은 V 중심좌표여야 합니다.')
      }
      const columns = headings.map((value, index) => ({ value, index })).sort((a, b) => a.value - b.value)
      rows.sort((a, b) => a[0] - b[0])
      if (columns.some((item, index) => Math.abs(item.value - (uEdges[index] + uEdges[index + 1]) / 2) > 1e-6) || rows.some((row, index) => Math.abs(row[0] - (vEdges[index] + vEdges[index + 1]) / 2) > 1e-6)) {
        throw new Error('표의 U/V 중심좌표가 입력한 셀 경계와 일치하지 않습니다.')
      }
      matrix = rows.map((row) => columns.map((column) => row[column.index + 1]))
    } else {
      matrix = lines.map((line) => numbers(line, '원시 표') ?? [])
    }
    if (matrix.length !== vEdges.length - 1 || matrix.some((row) => row.length !== uEdges.length - 1)) {
      throw new Error(`원시 표는 V ${vEdges.length - 1}행 × U ${uEdges.length - 1}열이어야 합니다.`)
    }
    if (matrix.some((row) => row.some((value) => value < 0))) throw new Error('원시 표에는 음수 수광량을 사용할 수 없습니다.')
    flux = matrix.map((row, v) => row.map((value, u) => draft.unit === 'flux_lm' ? value : value * (uEdges[u + 1] - uEdges[u]) * (vEdges[v + 1] - vEdges[v]) / 1e6))
  }
  let sources: ValidationSource[]
  try {
    const parsed: unknown = JSON.parse(draft.sources)
    if (!Array.isArray(parsed)) throw new Error('array required')
    sources = parsed.map((source) => normalizeSource(source))
  } catch {
    throw new Error('광원 조건을 확인하세요. 숫자와 X, Y, Z 벡터가 유효해야 하며, JSON은 광원 객체 배열이어야 합니다.')
  }
  const previous = original?.receivers.find((receiver) => receiver.id === selectedId)
  const receiver: ValidationReceiver = {
    id: previous?.id ?? 'manual-lt-receiver', name: draft.name.trim() || 'LT receiver',
    quantity: previous?.quantity === 'radiometric' ? 'radiometric' : draft.unit ? 'photometric' : previous?.quantity ?? 'unknown',
    raw: draft.raw === 'raw' ? true : draft.raw === 'smoothed' ? false : null,
    flux_lm: flux, u_edges_mm: uEdges, v_edges_mm: vEdges,
    center_mm: vector(draft.center, '리시버 중심'), u_axis: vector(draft.uAxis, 'U 방향'),
    v_axis: vector(draft.vAxis, 'V 방향'), normal: vector(draft.normal, '수광 법선'),
    acceptance_angle_deg: scalar(draft.acceptance, '수광각'), aperture: draft.aperture || null,
    total_flux_lm: scalar(draft.total, 'TotalPower'),
    ...(draft.tolerance.trim() ? { total_flux_tolerance_lm: scalar(draft.tolerance, '총광속 반올림 허용량')! } : {}),
    issues: previous?.issues ?? [],
    provenance: { ...previous?.provenance, manual_entry: true, input_unit: draft.unit || null, table_format: draft.tableFormat, coordinate_order_confirmed: draft.tableFormat === 'coordinates' || draft.coordinateOrderConfirmed, ...(draft.extractedOrder ? { user_confirmed_extracted_order: draft.extractedOrder } : {}) },
  }
  return {
    schema: 'lt-validation.v1', kind: 'manual', filename: original?.filename ?? 'manual-lt.json',
    receivers: previous && original ? original.receivers.map((item) => item.id === receiver.id ? receiver : item) : [...(original?.receivers ?? []), receiver],
    sources, issues: original?.issues ?? [],
    provenance: { ...original?.provenance, manually_supplemented: true },
  }
}

export function mapExtractedFlux(values: number[], uEdgesText: string, vEdgesText: string, fastAxis: string, uOrder: string, vOrder: string, rawDimensions: unknown): number[][] {
  const uEdges = numbers(uEdgesText, 'U 셀 경계')
  const vEdges = numbers(vEdgesText, 'V 셀 경계')
  if (!uEdges || !vEdges || uEdges.length < 2 || vEdges.length < 2) throw new Error('배열 배치 전에 U/V 셀 경계를 입력하세요.')
  if (!['U', 'V'].includes(fastAxis) || !['ascending', 'descending'].includes(uOrder) || !['ascending', 'descending'].includes(vOrder)) throw new Error('배열의 빠른 축과 U/V 좌표 순서를 모두 확인하세요.')
  const columns = uEdges.length - 1
  const rows = vEdges.length - 1
  if (!Array.isArray(rawDimensions) || rawDimensions.length !== 2 || !rawDimensions.every((value) => typeof value === 'number' && Number.isInteger(value) && value > 0) || !((rawDimensions[0] === columns && rawDimensions[1] === rows) || (rawDimensions[0] === rows && rawDimensions[1] === columns))) {
    throw new Error('입력 격자는 원본 mesh의 두 축 크기와 일치해야 합니다. 값 개수가 같아도 격자 모양을 바꿀 수 없습니다.')
  }
  if (values.length !== rows * columns) throw new Error(`추출 값 ${values.length}개와 입력 격자 ${rows}행 × ${columns}열이 다릅니다.`)
  return Array.from({ length: rows }, (_, v) => Array.from({ length: columns }, (_, u) => {
    const rawU = uOrder === 'ascending' ? u : columns - 1 - u
    const rawV = vOrder === 'ascending' ? v : rows - 1 - v
    return values[fastAxis === 'U' ? rawV * columns + rawU : rawU * rows + rawV]
  }))
}

function normalizeSource(value: unknown): ValidationSource {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('source object required')
  const source = value as Record<string, unknown>
  const sourceScalar = (field: unknown, label: string) => field == null ? null : scalar(String(field), label)
  const sourceVector = (field: unknown, label: string) => field == null ? null : vector(Array.isArray(field) ? field.join(',') : String(field), label)
  const sourceString = (field: unknown) => field == null || String(field).trim() === '' ? null : String(field).trim()
  const aim = source.aim && typeof source.aim === 'object' && !Array.isArray(source.aim) ? source.aim as Record<string, unknown> : null
  return {
    id: String(source.id ?? ''), name: String(source.name ?? ''),
    total_flux_lm: sourceScalar(source.total_flux_lm, '광원 총광속'),
    flux_reference: source.flux_reference === 'emitted' || source.flux_reference === 'aim_region' ? source.flux_reference : null,
    center_mm: sourceVector(source.center_mm, '광원 중심'), u_axis: sourceVector(source.u_axis, '광원 U'), v_axis: sourceVector(source.v_axis, '광원 V'), normal: sourceVector(source.normal, '광원 법선'),
    width_mm: sourceScalar(source.width_mm, '광원 너비'), height_mm: sourceScalar(source.height_mm, '광원 높이'), angular_distribution: sourceString(source.angular_distribution),
    aim: aim ? { center_mm: sourceVector(aim.center_mm, 'Aim 중심'), u_axis: sourceVector(aim.u_axis, 'Aim U'), v_axis: sourceVector(aim.v_axis, 'Aim V'), width_mm: sourceScalar(aim.width_mm, 'Aim 너비'), height_mm: sourceScalar(aim.height_mm, 'Aim 높이'), distribution: sourceString(aim.distribution) } : null,
  }
}
