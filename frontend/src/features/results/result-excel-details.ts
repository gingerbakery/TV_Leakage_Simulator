import type { RayTraceResult } from '@/api'
import type { ExcelCellValue, ExcelSheetDefinition } from '@/lib/xlsx-export'

interface ExportCase {
  name: string
  cad_name: string
  result: RayTraceResult
}

type Row = ExcelCellValue[]

function flatten(value: unknown, path = ''): Array<[string, ExcelCellValue]> {
  if (value !== null && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, child]) =>
      flatten(child, path ? `${path}.${key}` : key))
  }
  return [[path, typeof value === 'number' || typeof value === 'boolean'
    ? value : value == null ? null : String(value)]]
}

function detailRows(cases: ExportCase[], pick: (result: RayTraceResult) => unknown): Row[] {
  return cases.flatMap((item) => flatten(pick(item.result)).map(([key, value]) =>
    [item.name, item.cad_name, item.result.run_id, key, value]))
}

/** Keep every recorded value, including fields not yet displayed by the UI. */
export function resultExcelDetailSheets(cases: ExportCase[]): ExcelSheetDefinition[] {
  const detailHeader = ['Case', 'CAD', 'Run ID', 'Metric / Field', 'Value']
  const sheets: ExcelSheetDefinition[] = [
    {
      name: 'Ray Summary',
      rows: [[
        'Case', 'CAD', 'Run ID', 'Runtime (s)', 'Total Rays', 'Receiver Hits',
        'Hit Ratio (%)', 'Surface Interactions', 'Terminated Rays', 'Stored Paths',
        'Direct Hits', 'Reflected Hits', 'Direct Flux (lm)', 'Reflected Flux (lm)',
      ], ...cases.map(({ name, cad_name, result: r }) => [
        name, cad_name, r.run_id, r.runtime_sec, r.total_rays, r.receiver_hit_count,
        r.total_rays > 0 ? r.receiver_hit_count / r.total_rays * 100 : 0,
        r.surface_hit_count, r.terminated_ray_count, r.stored_paths.length,
        r.contribution_summary.direct_receiver_hit_count,
        r.contribution_summary.reflected_receiver_hit_count,
        r.contribution_summary.direct_receiver_flux_lumen,
        r.contribution_summary.reflected_receiver_flux_lumen,
      ])],
    },
    { name: 'Surface Optical', rows: [detailHeader, ...detailRows(cases, (r) => ({
      optical: r.metrics._optical_summary,
      components: r.contribution_summary.components,
      faces: r.contribution_summary.faces,
      materials: r.contribution_summary.materials,
      optical_profiles: r.optical_profiles,
    }))] },
    { name: 'Multi-bounce', rows: [detailHeader, ...detailRows(cases, (r) => ({
      reflection: r.metrics._reflection_summary,
      lobes: r.contribution_summary.lobes,
      depths: r.contribution_summary.depths,
    }))] },
    { name: 'Receiver Details', rows: [detailHeader, ...detailRows(cases, (r) => ({
      receivers: r.receivers,
      metrics: Object.fromEntries(r.receivers.map((receiver) =>
        [receiver.receiver_id, r.metrics[receiver.receiver_id]])),
      contribution: r.contribution_summary.receivers,
    }))] },
    { name: 'Compute Performance', rows: [detailHeader, ...detailRows(cases,
      (r) => r.metrics._performance_summary)] },
    { name: 'Convergence History', rows: [detailHeader, ...detailRows(cases,
      (r) => r.metrics._convergence_history)] },
    { name: 'All Recorded Metrics', rows: [detailHeader, ...detailRows(cases,
      (r) => r.metrics)] },
    { name: 'Detailed Run Setup', rows: [detailHeader, ...detailRows(cases,
      (r) => ({ config: r.config, emitters: r.emitters, optical_profiles: r.optical_profiles }))] },
  ]

  const heatmapRows: Row[] = []
  const pathRows: Row[] = []
  for (const item of cases) {
    const r = item.result
    for (const grid of r.receiver_grids) {
      const receiver = r.receivers.find((candidate) => candidate.receiver_id === grid.receiver_id)
      const n = Number((r.metrics[grid.receiver_id] as Record<string, unknown> | undefined)
        ?.error_estimate_sample_count ?? r.total_rays)
      grid.flux_lumen.forEach((row, y) => row.forEach((flux, x) => {
        const squared = grid.flux_squared_lumen2_grid?.[y]?.[x]
        const variance = squared != null && n > 1
          ? Math.max(0, (n * squared - flux * flux) / (n - 1)) : null
        const illuminance = grid.bin_area_mm2 > 0 ? flux / (grid.bin_area_mm2 * 1e-6) : 0
        heatmapRows.push([
          item.name, item.cad_name, r.run_id, receiver?.display_name ?? grid.receiver_id,
          grid.receiver_id, x + 1, y + 1,
          receiver ? ((x + 0.5) / grid.resolution[0] - 0.5) * receiver.width_mm : null,
          receiver ? ((y + 0.5) / grid.resolution[1] - 0.5) * receiver.height_mm : null,
          grid.bin_area_mm2, flux,
          grid.bin_area_mm2 > 0 ? flux / grid.bin_area_mm2 : 0,
          illuminance, illuminance * r.config.k_abs * r.config.k_brdf / Math.PI,
          squared ?? null,
          variance != null && flux > 0 ? Math.sqrt(variance) / flux * 100 : null,
        ])
      }))
    }
    r.stored_paths.forEach((path, pathIndex) => path.forEach((event, eventIndex) => {
      // Export all event fields rather than only those drawn in Section View.
      for (const [field, value] of flatten(event)) {
        pathRows.push([item.name, item.cad_name, r.run_id, pathIndex + 1, eventIndex + 1, field, value])
      }
    }))
  }
  sheets.push({ name: 'Heatmap Cells', rows: [[
    'Case', 'CAD', 'Run ID', 'Receiver', 'Receiver ID', 'Column', 'Source Row',
    'Local X (mm)', 'Local Y (mm)', 'Cell Area (mm²)', 'Incident Flux (lm)',
    'Flux Density (lm/mm²)', 'Illuminance (lx)', 'Luminance (nit)',
    'Flux Squared (lm²)', 'Pixel Error (%)',
  ], ...heatmapRows] })
  sheets.push({ name: 'Stored Ray Paths', rows: [[
    'Case', 'CAD', 'Run ID', 'Path', 'Event', 'Field', 'Value',
  ], ...pathRows] })
  // Excel supports 1,048,576 rows including the header. Split large exports
  // instead of silently dropping heatmap cells, contribution rows or paths.
  const maxDataRows = 1_048_575
  return sheets.flatMap((sheet) => {
    if (sheet.rows[0] === detailHeader) sheet.columnWidths = [18, 24, 24, 60, 24]
    if (sheet.rows.length <= maxDataRows + 1) return [sheet]
    const result: ExcelSheetDefinition[] = []
    for (let start = 1, part = 1; start < sheet.rows.length; start += maxDataRows, part += 1) {
      result.push({ ...sheet, name: `${sheet.name} ${part}`,
        rows: [sheet.rows[0], ...sheet.rows.slice(start, start + maxDataRows)] })
    }
    return result
  })
}
