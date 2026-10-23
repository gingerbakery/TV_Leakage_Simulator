/** Portable LT ASCII bundle; tracing requires explicit transport options. */
export interface BsdfAsset {
  id: string
  format: 'lighttools-ascii-bsdf-v1'
  parserVersion: 1
  files: { name: string; text: string }[]
  data: {
    symmetry: 'unspecified'
    meshFormat: 'polar'
    valueType: 'bsdf_relative'
    wavelengthsNm: number[]
    incidenceAnglesDeg: number[]
    grids: {
      fileName: string
      wavelengthNm: number
      incidenceAngleDeg: number
      planeOfIncidenceDeg: number
      side: 'front' | 'back'
      longitudesDeg: number[]
      latitudesDeg: number[]
      values: number[][]
    }[]
    rt: { wavelengthNm: number; incidenceAngleDeg: number; reflectance: number; transmittance: number }[]
    refractiveIndex: number
  }
  warnings: string[]
  simulationReady: boolean
  transport?: {
    model: 'isotropic_no_shift'
    wavelengthNm: number
    aoiExtrapolation: 'clamp'
  }
}

export const emptyBsdfAssets: BsdfAsset[] = []
export const bsdfImportLimits = {
  maxFiles: 64,
  maxFileBytes: 8 * 1024 * 1024,
  maxTotalBytes: 32 * 1024 * 1024,
  maxTotalCells: 2_000_000,
} as const
export const bsdfPreparationMessage = 'BSDF 계산 옵션에서 등방성 · No Shift와 파장을 설정하세요. 현재 반사 전용 CPU·NVIDIA GPU / source sampling을 지원합니다.'

export function bsdfTransportProblem(asset: BsdfAsset): string | null {
  if (!asset.transport) return bsdfPreparationMessage
  if (asset.data.grids.some((grid) => grid.longitudesDeg[0] !== 0 || grid.longitudesDeg.at(-1) !== 360 ||
      grid.latitudesDeg[0] !== 90 || grid.latitudesDeg.at(-1) !== 0)) return '부분 산란 표는 보관만 가능합니다. 계산에는 0–90° / 0–360° 전체 반구가 필요합니다.'
  const wavelength = asset.transport.wavelengthNm
  if (!Number.isFinite(wavelength) || wavelength < Math.min(...asset.data.wavelengthsNm) || wavelength > Math.max(...asset.data.wavelengthsNm)) return '계산 파장이 BSDF 측정 범위를 벗어났습니다.'
  return null
}

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const numbers = (value: unknown): value is number[] => Array.isArray(value) && value.length > 0 && value.length <= 10000 && value.every(finite)
const ratio = (value: unknown): value is number => finite(value) && value >= 0 && value <= 1
const name = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 255 && !/[\\/\x00-\x1f]/.test(value) && value !== '.' && value !== '..'

/** Structural guard for project loading. The backend parses the source bundle on import. */
export function isBsdfAsset(value: unknown): value is BsdfAsset {
  if (!record(value) || typeof value.id !== 'string' || !/^bsdf-[0-9a-f]{64}$/.test(value.id) ||
      value.format !== 'lighttools-ascii-bsdf-v1' || value.parserVersion !== 1 || typeof value.simulationReady !== 'boolean' ||
      !Array.isArray(value.files) || value.files.length < 3 || value.files.length > bsdfImportLimits.maxFiles ||
      !value.files.every((file) => record(file) && name(file.name) && typeof file.text === 'string' && new TextEncoder().encode(file.text).length <= bsdfImportLimits.maxFileBytes) ||
      !Array.isArray(value.warnings) || value.warnings.length > 1000 || !value.warnings.every((warning) => typeof warning === 'string') ||
      !record(value.data)) return false
  if (value.transport !== undefined && (!record(value.transport) || value.transport.model !== 'isotropic_no_shift' ||
      value.transport.aoiExtrapolation !== 'clamp' || !finite(value.transport.wavelengthNm) || value.transport.wavelengthNm <= 0)) return false
  const files = value.files as BsdfAsset['files']
  const fileNames = new Set(files.map((file) => file.name.toLowerCase()))
  if (fileNames.size !== files.length || files.reduce((sum, file) => sum + new TextEncoder().encode(file.text).length, 0) > bsdfImportLimits.maxTotalBytes) return false
  const data = value.data
  if (data.symmetry !== 'unspecified' || data.meshFormat !== 'polar' || data.valueType !== 'bsdf_relative' ||
      !numbers(data.wavelengthsNm) || !data.wavelengthsNm.every((x) => x > 0) ||
      !numbers(data.incidenceAnglesDeg) || !data.incidenceAnglesDeg.every((x) => x >= 0 && x < 90) ||
      data.refractiveIndex !== 1 ||
      !Array.isArray(data.grids) || !data.grids.length || data.grids.length > 128 ||
      !Array.isArray(data.rt) || !data.rt.length || data.rt.length > 10000) return false
  let cells = 0
  const gridsValid = data.grids.every((grid) => {
    if (!record(grid) || !name(grid.fileName) || !fileNames.has(grid.fileName.toLowerCase()) ||
        !finite(grid.wavelengthNm) || grid.wavelengthNm <= 0 || !finite(grid.incidenceAngleDeg) || grid.incidenceAngleDeg < 0 || grid.incidenceAngleDeg >= 90 ||
        grid.planeOfIncidenceDeg !== 0 || grid.side !== 'front' ||
        !numbers(grid.longitudesDeg) || !numbers(grid.latitudesDeg) || !Array.isArray(grid.values)) return false
    cells += grid.longitudesDeg.length * grid.latitudesDeg.length
    const longitude = grid.longitudesDeg
    const latitude = grid.latitudesDeg
    return cells <= bsdfImportLimits.maxTotalCells && longitude.length * latitude.length <= 200_000 &&
      longitude.length >= 2 && latitude.length >= 2 &&
      longitude.every((x, index) => x >= 0 && x <= 360 && (index === 0 || x > longitude[index - 1])) &&
      latitude.every((x, index) => x >= 0 && x <= 90 && (index === 0 || x < latitude[index - 1])) &&
      grid.values.length === latitude.length &&
      grid.values.every((row) => numbers(row) && row.length === longitude.length && row.every((x) => x >= 0)) &&
      grid.values.some((row) => (row as number[]).some((x) => x > 0))
  })
  return gridsValid && data.rt.every((row) => record(row) && finite(row.wavelengthNm) && row.wavelengthNm > 0 &&
    finite(row.incidenceAngleDeg) && row.incidenceAngleDeg >= 0 && row.incidenceAngleDeg <= 90 &&
    ratio(row.reflectance) && row.transmittance === 0)
}

export function bsdfAssetName(asset: BsdfAsset): string {
  return asset.files.find((file) => file.text.trimStart().startsWith('#UDOP'))?.name ?? asset.files[0]?.name ?? asset.id
}
