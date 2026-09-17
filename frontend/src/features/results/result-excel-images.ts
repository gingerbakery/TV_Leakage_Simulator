import type { RayTraceResult, ReceiverSpec } from '@/api'
import type { ExcelSheetDefinition } from '@/lib/xlsx-export'
import { receiverHeatmapColor } from './receiver-heatmap'

interface ExportCase { name: string; cad_name: string; result: RayTraceResult }
interface ImageOptions {
  mode: 'auto' | 'compare' | 'customize'
  minNit: number
  maxNit: number
  correspondingPeak?: (result: RayTraceResult, receiver: ReceiverSpec) => number
}

/** Render complete, uncropped Receiver maps, independent of the open tab/zoom. */
export function resultExcelImageSheets(cases: ExportCase[], options: ImageOptions): ExcelSheetDefinition[] {
  const sheets: ExcelSheetDefinition[] = []
  for (const item of cases) {
    for (const receiver of item.result.receivers) {
      const grid = item.result.receiver_grids.find((candidate) => candidate.receiver_id === receiver.receiver_id)
      if (!grid) continue
      const factor = item.result.config.k_abs * item.result.config.k_brdf / (Math.max(grid.bin_area_mm2 * 1e-6, 1e-18) * Math.PI)
      const values = grid.flux_lumen.flat().map((flux) => flux * factor)
      const peak = values.reduce((maximum, value) => Math.max(maximum, value), 0)
      const minNit = options.mode === 'customize' ? Math.max(0, options.minNit) : 0
      const maxNit = options.mode === 'customize' ? Math.max(minNit + 1e-6, options.maxNit)
        : options.mode === 'compare' ? Math.max(peak, 1e-6, ...cases.map((candidate) =>
          options.correspondingPeak?.(candidate.result, receiver) ?? 0)) : Math.max(peak, 1e-6)
      const canvas = document.createElement('canvas')
      canvas.width = 800
      canvas.height = 600
      const context = canvas.getContext('2d')
      if (!context) throw new Error('Heatmap 이미지 생성에 필요한 Canvas를 사용할 수 없습니다.')
      context.fillStyle = '#ffffff'
      context.fillRect(0, 0, 800, 600)
      context.fillStyle = '#172033'
      context.font = 'bold 18px Arial'
      const title = `${item.name} / ${receiver.display_name || receiver.receiver_id}`
      context.fillText(title, 30, 28, 740)
      context.font = '14px Arial'
      context.fillText(`${receiver.width_mm} × ${receiver.height_mm} mm · ${options.mode} · ${minNit.toPrecision(4)}–${maxNit.toPrecision(4)} nit`, 30, 52)
      const ratio = Math.max(receiver.width_mm, 1e-6) / Math.max(receiver.height_mm, 1e-6)
      const width = Math.min(640, 400 * ratio)
      const height = width / ratio
      const left = 80 + (640 - width) / 2
      const top = 80 + (400 - height) / 2
      const [columns, rows] = grid.resolution
      for (let y = 0; y < rows; y += 1) {
        for (let x = 0; x < columns; x += 1) {
          const value = (grid.flux_lumen[rows - 1 - y]?.[x] ?? 0) * factor
          const normalized = Math.sqrt(Math.min(1, Math.max(0, (value - minNit) / (maxNit - minNit))))
          context.fillStyle = `rgb(${receiverHeatmapColor(normalized).join(',')})`
          context.fillRect(left + x * width / columns, top + y * height / rows,
            width / columns + 0.5, height / rows + 0.5)
        }
      }
      context.strokeStyle = '#172033'
      context.strokeRect(left, top, width, height)
      context.fillStyle = '#172033'
      context.textAlign = 'center'
      for (let tick = 0; tick <= 4; tick += 1) {
        context.fillText(((tick / 4 - 0.5) * receiver.width_mm).toFixed(2), left + width * tick / 4, top + height + 22)
      }
      context.fillText('Receiver Local X (mm)', left + width / 2, top + height + 44)
      context.textAlign = 'right'
      for (let tick = 0; tick <= 4; tick += 1) {
        context.fillText(((0.5 - tick / 4) * receiver.height_mm).toFixed(2), left - 10, top + height * tick / 4 + 5)
      }
      context.save()
      context.translate(left - 60, top + height / 2)
      context.rotate(-Math.PI / 2)
      context.textAlign = 'center'
      context.fillText('Receiver Local Y (mm)', 0, 0)
      context.restore()
      for (let x = 0; x < 400; x += 1) {
        context.fillStyle = `rgb(${receiverHeatmapColor(x / 399).join(',')})`
        context.fillRect(200 + x, 550, 1, 16)
      }
      context.fillStyle = '#172033'
      context.textAlign = 'left'
      context.fillText(`${minNit.toPrecision(4)} nit`, 200, 585)
      context.textAlign = 'right'
      context.fillText(`${maxNit.toPrecision(4)} nit`, 600, 585)
      const encoded = canvas.toDataURL('image/png').split(',')[1]
      const png = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0))
      sheets.push({ name: `Heatmap ${sheets.length + 1}`, columnWidths: [24, 32, 24, 24],
        rows: [['Case', 'Receiver', 'CAD', 'Run ID'],
          [item.name, receiver.display_name || receiver.receiver_id, item.cad_name, item.result.run_id],
          ['Scale mode', 'Minimum (nit)', 'Maximum (nit)', 'Peak (nit)'],
          [options.mode, minNit, maxNit, peak]],
        images: [{ png, width: 800, height: 600, row: 5 }],
      })
    }
  }
  return sheets
}
