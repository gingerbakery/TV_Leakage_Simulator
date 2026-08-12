import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react'
import type {
  RayTraceResult,
  ReceiverGrid,
  ReceiverSpec,
  ScenePayload,
} from '@/api'
import {
  Activity,
  Aperture,
  Download,
  FilePlus2,
  Grip,
  Layers3,
  Move,
  Save,
  Trash2,
  Upload,
  X,
} from 'lucide-react'

import { HelpTooltip } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

import {
  formatReceiverCoordinate,
  initialReceiverHeatmapViewport,
  receiverAxisTicksForRange,
  receiverHeatmapColor,
  receiverHeatmapDisplayValues,
  receiverHeatmapLayout,
  receiverHeatmapSample,
  receiverHeatmapViewportBounds,
  zoomReceiverHeatmapViewport,
  type ReceiverHeatmapSample,
} from './receiver-heatmap'
import { RaySectionImage } from './ray-section-image'

// Kill switch for the Ray Section View images in the Ray summary tab.
// This feature has a known limitation (the true filled-cap cross-section
// doesn't reliably close on every real-world STEP mesh - see
// docs/changes/2026-08-05_ray-section-view-report-image.md) that hasn't
// been fully resolved yet. If it causes trouble after this merges, flip
// this to `false` and ship that one-line change instead of reverting the
// whole merge (which would also undo the unrelated WORKFLOW accordion,
// Receiver color, and ROI datum-pick fixes bundled in the same commit).
const RAY_SECTION_VIEW_ENABLED = true

type ResultTab =
  | 'summary'
  | 'surface'
  | 'bounce'
  | 'receiver'
  | 'compare'

interface RayTraceResultWindowProps {
  open: boolean
  result: RayTraceResult | null
  scene?: ScenePayload
  roiFaceIds?: number[]
  cadDisplayName?: string
  reportCases?: Array<{
    caseId: string
    name: string
    cadName: string
    result: RayTraceResult
    note?: string
  }>
  onCaseMetadataChange?(caseId: string, name: string, note: string): void
  onOpenChange(open: boolean): void
}

interface AnalysisCase {
  case_id: string
  name: string
  cad_name: string
  note: string
  saved_at: string
  selected: boolean
  result: RayTraceResult
}

interface AnalysisCaseFile {
  format: 'tv-leakage-analysis-cases'
  schema_version: 'analysis-cases.v1'
  saved_at: string
  cases: AnalysisCase[]
}

function caseFlux(result: RayTraceResult): number {
  const summary = result.contribution_summary
  return (
    numeric(summary.direct_receiver_flux_lumen) +
    numeric(summary.reflected_receiver_flux_lumen)
  )
}

function percentChange(value: number, baseline: number): string {
  if (baseline === 0) return value === 0 ? '0.0%' : '—'
  const change = ((value - baseline) / Math.abs(baseline)) * 100
  return `${change > 0 ? '+' : ''}${change.toFixed(1)}%`
}

interface WindowFrame {
  x: number
  y: number
  width: number
  height: number
}

interface PointerOperation {
  kind: 'drag' | 'resize'
  startX: number
  startY: number
  frame: WindowFrame
}

interface ReceiverHeatmapHover extends ReceiverHeatmapSample {
  pointerXPercent: number
  pointerYPercent: number
}

function numeric(value: unknown): number {
  return Number.isFinite(Number(value)) ? Number(value) : 0
}

function objectValue(
  source: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  const value = source[key]
  return value && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : {}
}

function metricGroup(result: RayTraceResult, key: string) {
  return objectValue(result.metrics, key)
}

function formatMetric(value: unknown, digits = 3) {
  const number = numeric(value)
  const magnitude = Math.abs(number)
  if (magnitude > 0 && (magnitude >= 10_000 || magnitude < 0.001)) {
    return number.toExponential(3)
  }
  return number.toFixed(digits)
}

function ReceiverHeatmap({
  grid,
  receiver,
}: {
  grid: ReceiverGrid
  receiver: ReceiverSpec
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [viewport, setViewport] = useState({
    ...initialReceiverHeatmapViewport,
  })
  const [hover, setHover] =
    useState<ReceiverHeatmapHover | null>(null)
  const layout = receiverHeatmapLayout(
    receiver.width_mm,
    receiver.height_mm,
  )
  const columns = Math.max(1, grid.resolution[0])
  const rows = Math.max(1, grid.resolution[1])
  const maximumZoom = Math.min(
    128,
    Math.max(columns, rows, 1),
  )
  const viewportBounds = receiverHeatmapViewportBounds(viewport)
  const xMinimumMm =
    (viewportBounds.minX - 0.5) * layout.widthMm
  const xMaximumMm =
    (viewportBounds.maxX - 0.5) * layout.widthMm
  const yMinimumMm =
    (0.5 - viewportBounds.maxY) * layout.heightMm
  const yMaximumMm =
    (0.5 - viewportBounds.minY) * layout.heightMm
  const xTicks = receiverAxisTicksForRange(
    xMinimumMm,
    xMaximumMm,
  )
  const yTicks = receiverAxisTicksForRange(
    yMinimumMm,
    yMaximumMm,
  )

  useEffect(() => {
    setViewport({ ...initialReceiverHeatmapViewport })
    setHover(null)
  }, [grid, receiver.height_mm, receiver.width_mm])

  useEffect(() => {
    const canvas = canvasRef.current
    const context = canvas?.getContext('2d')
    if (!canvas || !context) return
    canvas.width = columns
    canvas.height = rows
    const values = receiverHeatmapDisplayValues(grid)
    const peak = Math.max(...values, 0)
    const image = context.createImageData(columns, rows)
    for (let index = 0; index < columns * rows; index += 1) {
      const normalized =
        peak > 0 ? Math.sqrt((values[index] || 0) / peak) : 0
      const pixel = index * 4
      const [red, green, blue] = receiverHeatmapColor(normalized)
      image.data[pixel] = red
      image.data[pixel + 1] = green
      image.data[pixel + 2] = blue
      image.data[pixel + 3] = 255
    }
    context.putImageData(image, 0, 0)
  }, [columns, grid, rows])

  const pointerPosition = (
    element: HTMLDivElement,
    clientX: number,
    clientY: number,
  ) => {
    const bounds = element.getBoundingClientRect()
    if (bounds.width <= 0 || bounds.height <= 0) return null
    return {
      x: Math.min(
        1,
        Math.max(0, (clientX - bounds.left) / bounds.width),
      ),
      y: Math.min(
        1,
        Math.max(0, (clientY - bounds.top) / bounds.height),
      ),
    }
  }

  const updateHover = (
    element: HTMLDivElement,
    clientX: number,
    clientY: number,
    nextViewport = viewport,
  ) => {
    const pointer = pointerPosition(element, clientX, clientY)
    if (!pointer) return
    setHover({
      ...receiverHeatmapSample(
        grid,
        layout.widthMm,
        layout.heightMm,
        nextViewport,
        pointer.x,
        pointer.y,
      ),
      pointerXPercent: pointer.x * 100,
      pointerYPercent: pointer.y * 100,
    })
  }

  const handleWheel = (
    event: ReactWheelEvent<HTMLDivElement>,
  ) => {
    event.preventDefault()
    const pointer = pointerPosition(
      event.currentTarget,
      event.clientX,
      event.clientY,
    )
    if (!pointer) return
    const nextViewport = zoomReceiverHeatmapViewport(
      viewport,
      pointer.x,
      pointer.y,
      event.deltaY,
      maximumZoom,
    )
    setViewport(nextViewport)
    updateHover(
      event.currentTarget,
      event.clientX,
      event.clientY,
      nextViewport,
    )
  }

  const resetViewport = () => {
    setViewport({ ...initialReceiverHeatmapViewport })
    setHover(null)
  }

  return (
    <div className="mt-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-1 text-[0.6rem] text-muted-foreground">
        <span>Flux distribution · Receiver local plane</span>
        <div className="flex items-center gap-2">
          <span>
            {formatReceiverCoordinate(layout.widthMm)} ×{' '}
            {formatReceiverCoordinate(layout.heightMm)} mm
          </span>
          <span
            data-testid={`${grid.receiver_id}-zoom`}
            className="rounded border border-border bg-background/55 px-1.5 py-0.5 font-mono text-foreground"
          >
            {viewport.zoom.toFixed(2)}×
          </span>
          <button
            type="button"
            className="rounded border border-border px-1.5 py-0.5 text-foreground transition-colors hover:bg-muted disabled:cursor-default disabled:opacity-35"
            disabled={viewport.zoom <= 1}
            onClick={resetViewport}
          >
            Reset view
          </button>
        </div>
      </div>
      <div
        className="mx-auto grid max-w-full grid-cols-[minmax(0,1fr)_3.75rem] grid-rows-[auto_2.75rem]"
        style={{
          width: `${layout.preferredWidthPx + 60}px`,
        }}
      >
        <div
          data-testid={`${grid.receiver_id}-heatmap-frame`}
          data-width-mm={layout.widthMm}
          data-height-mm={layout.heightMm}
          className="relative min-w-0 overflow-visible"
          style={{
            aspectRatio: `${layout.widthMm} / ${layout.heightMm}`,
          }}
        >
          <div
            data-testid={`${grid.receiver_id}-heatmap-viewport`}
            title="Wheel to zoom at the cursor. Double-click to reset."
            className="absolute inset-0 cursor-crosshair touch-none overflow-hidden border border-slate-300/75 bg-[#0814be]"
            onDoubleClick={resetViewport}
            onPointerLeave={() => setHover(null)}
            onPointerMove={(event) =>
              updateHover(
                event.currentTarget,
                event.clientX,
                event.clientY,
              )
            }
            onWheel={handleWheel}
          >
            <canvas
              ref={canvasRef}
              aria-label={`${grid.receiver_id} flux heatmap`}
              className="absolute max-w-none select-none"
              style={{
                height: `${viewport.zoom * 100}%`,
                left: `${
                  -viewportBounds.minX * viewport.zoom * 100
                }%`,
                top: `${
                  -viewportBounds.minY * viewport.zoom * 100
                }%`,
                width: `${viewport.zoom * 100}%`,
              }}
            />
          </div>
          {hover ? (
            <div
              role="tooltip"
              data-testid={`${grid.receiver_id}-heatmap-tooltip`}
              className="pointer-events-none absolute z-20 w-48 rounded-md border border-slate-500/70 bg-slate-950/95 p-2 text-[0.58rem] text-slate-100 shadow-xl"
              style={{
                left: `${hover.pointerXPercent}%`,
                top: `${hover.pointerYPercent}%`,
                transform: `translate(${
                  hover.pointerXPercent > 62
                    ? 'calc(-100% - 10px)'
                    : '10px'
                }, ${
                  hover.pointerYPercent > 62
                    ? 'calc(-100% - 10px)'
                    : '10px'
                })`,
              }}
            >
              <div className="mb-1 flex items-center justify-between border-b border-slate-700 pb-1 font-semibold">
                <span>Receiver sample</span>
                <span className="font-mono text-slate-400">
                  C{hover.column + 1} · R{hover.displayRow + 1}
                </span>
              </div>
              <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 tabular-nums">
                <span className="text-slate-400">X</span>
                <span className="text-right font-mono">
                  {formatReceiverCoordinate(hover.xMm)} mm
                </span>
                <span className="text-slate-400">Y</span>
                <span className="text-right font-mono">
                  {formatReceiverCoordinate(hover.yMm)} mm
                </span>
                <span className="text-slate-400">
                  Incident flux
                </span>
                <span className="text-right font-mono">
                  {formatMetric(hover.fluxLumen, 6)} lm
                </span>
                <span className="text-slate-400">
                  Flux density
                </span>
                <span className="text-right font-mono">
                  {formatMetric(
                    hover.fluxDensityLumenPerMm2,
                    6,
                  )}{' '}
                  lm/mm²
                </span>
                <span className="text-slate-400">
                  Illuminance
                </span>
                <span className="text-right font-mono">
                  {formatMetric(hover.illuminanceLux)} lx
                </span>
              </div>
            </div>
          ) : null}
        </div>
        <div
          data-testid={`${grid.receiver_id}-y-axis`}
          aria-label="Receiver Y axis in millimeters"
          className="relative text-[0.58rem] tabular-nums text-muted-foreground"
        >
          {yTicks.map((tick) => (
            <div
              key={tick.value}
              data-axis-tick={tick.value}
              className="absolute left-0 flex -translate-y-1/2 items-center"
              style={{
                top: `${100 - tick.positionPercent}%`,
              }}
            >
              <span className="h-px w-1.5 bg-slate-300/80" />
              <span className="ml-1">{tick.label}</span>
            </div>
          ))}
          <span className="absolute top-1/2 right-0 -translate-y-1/2 rotate-90 whitespace-nowrap text-[0.6rem] font-medium text-foreground">
            Y (mm)
          </span>
        </div>
        <div
          data-testid={`${grid.receiver_id}-x-axis`}
          aria-label="Receiver X axis in millimeters"
          className="relative text-[0.58rem] tabular-nums text-muted-foreground"
        >
          {xTicks.map((tick) => (
            <div
              key={tick.value}
              data-axis-tick={tick.value}
              className="absolute top-0 -translate-x-1/2 text-center"
              style={{
                left: `${tick.positionPercent}%`,
              }}
            >
              <span className="mx-auto block h-1.5 w-px bg-slate-300/80" />
              <span className="mt-0.5 block">{tick.label}</span>
            </div>
          ))}
          <span className="absolute right-0 bottom-0 left-0 text-center text-[0.6rem] font-medium text-foreground">
            X (mm)
          </span>
        </div>
        <div aria-hidden="true" />
      </div>
    </div>
  )
}

function Stat({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <div className="rounded-lg border border-border bg-background/45 p-2.5">
      <div className="text-[0.62rem] text-muted-foreground">{label}</div>
      <div className="mt-1 text-sm font-semibold">{value}</div>
    </div>
  )
}

export function RayTraceResultWindow({
  open,
  result: liveResult,
  scene,
  roiFaceIds,
  cadDisplayName = 'CAD model',
  reportCases = [],
  onCaseMetadataChange,
  onOpenChange,
}: RayTraceResultWindowProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const operationRef = useRef<PointerOperation | null>(null)
  const [tab, setTab] = useState<ResultTab>('summary')
  const [analysisCases, setAnalysisCases] = useState<AnalysisCase[]>([])
  const [reportCaseId, setReportCaseId] = useState<string | null>(null)
  const [caseName, setCaseName] = useState('')
  const [caseNote, setCaseNote] = useState('')
  const caseFileInputRef = useRef<HTMLInputElement>(null)
  const [frame, setFrame] = useState<WindowFrame>({
    x: 24,
    y: 58,
    width: 960,
    height: 880,
  })

  useEffect(() => {
    if (reportCases.length === 0) return
    setAnalysisCases((current) => {
      const merged = new Map(current.map((item) => [item.case_id, item]))
      for (const item of reportCases) {
        const existing = merged.get(item.caseId)
        merged.set(item.caseId, {
          case_id: item.caseId,
          name: item.name,
          cad_name: item.cadName,
          note: item.note ?? existing?.note ?? '',
          saved_at: new Date().toISOString(),
          selected: existing?.selected ?? true,
          result: structuredClone(item.result),
        })
      }
      return [...merged.values()]
    })
    setReportCaseId((current) => current ?? reportCases[0]?.caseId ?? null)
  }, [reportCases])

  useEffect(() => {
    if (!open) return
    const viewportWidth = window.innerWidth
    const viewportHeight = window.innerHeight
    setFrame((current) => ({
      x: Math.max(12, Math.min(current.x, viewportWidth - 340)),
      y: Math.max(12, Math.min(current.y, viewportHeight - 260)),
      width: Math.min(current.width, Math.max(320, viewportWidth - 24)),
      height: Math.min(
        current.height,
        Math.max(260, viewportHeight - 24),
      ),
    }))
  }, [open])

  useEffect(() => {
    if (!open) return
    const move = (event: PointerEvent) => {
      const operation = operationRef.current
      if (!operation) return
      const viewportWidth = window.innerWidth
      const viewportHeight = window.innerHeight
      const deltaX = event.clientX - operation.startX
      const deltaY = event.clientY - operation.startY
      if (operation.kind === 'drag') {
        setFrame({
          ...operation.frame,
          x: Math.max(
            8,
            Math.min(
              operation.frame.x + deltaX,
              viewportWidth - operation.frame.width - 8,
            ),
          ),
          y: Math.max(
            8,
            Math.min(
              operation.frame.y + deltaY,
              viewportHeight - operation.frame.height - 8,
            ),
          ),
        })
      } else {
        setFrame({
          ...operation.frame,
          width: Math.max(
            320,
            Math.min(
              operation.frame.width + deltaX,
              viewportWidth - operation.frame.x - 8,
            ),
          ),
          height: Math.max(
            260,
            Math.min(
              operation.frame.height + deltaY,
              viewportHeight - operation.frame.y - 8,
            ),
          ),
        })
      }
    }
    const stop = () => {
      operationRef.current = null
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
    window.addEventListener('pointercancel', stop)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
    }
  }, [open])

  const result =
    analysisCases.find((item) => item.case_id === reportCaseId)?.result ??
    liveResult
  if (!open || !result) return null

  const begin = (
    event: ReactPointerEvent,
    kind: PointerOperation['kind'],
  ) => {
    operationRef.current = {
      kind,
      startX: event.clientX,
      startY: event.clientY,
      frame,
    }
    event.preventDefault()
  }
  const contribution = result.contribution_summary
  const performance = metricGroup(result, '_performance_summary')
  const reflection = metricGroup(result, '_reflection_summary')
  const optical = metricGroup(result, '_optical_summary')
  const componentRows = Object.entries(contribution.components)
    .map(([name, value]) => ({
      name,
      values:
        value && typeof value === 'object'
          ? (value as Record<string, unknown>)
          : {},
    }))
    .sort(
      (left, right) =>
        numeric(right.values.receiver_flux_lumen) -
        numeric(left.values.receiver_flux_lumen),
    )
    .slice(0, 12)
  const tabs: { id: ResultTab; label: string; icon: typeof Activity }[] = [
    { id: 'summary', label: 'Ray summary', icon: Activity },
    { id: 'surface', label: 'Surface optical', icon: Layers3 },
    { id: 'bounce', label: 'Multi-bounce', icon: Move },
    { id: 'receiver', label: 'Receiver', icon: Aperture },
    { id: 'compare', label: 'Compare cases', icon: Layers3 },
  ]
  const hitRatio =
    result.total_rays > 0
      ? result.receiver_hit_count / result.total_rays
      : 0
  const selectedCases = analysisCases.filter((item) => item.selected)
  const baselineCase = selectedCases[0] ?? null

  const saveCurrentCase = () => {
    const nextName = caseName.trim() || `Case ${analysisCases.length + 1}`
    const nextCase: AnalysisCase = {
      case_id: `${result.run_id}-${Date.now()}`,
      name: nextName,
      cad_name: cadDisplayName,
      note: caseNote.trim(),
      saved_at: new Date().toISOString(),
      selected: true,
      result: structuredClone(result),
    }
    setAnalysisCases((current) => [...current, nextCase])
    setCaseName('')
    setCaseNote('')
  }

  const exportCases = () => {
    const cases = selectedCases.length > 0 ? selectedCases : analysisCases
    if (cases.length === 0) return
    const payload: AnalysisCaseFile = {
      format: 'tv-leakage-analysis-cases',
      schema_version: 'analysis-cases.v1',
      saved_at: new Date().toISOString(),
      cases,
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json',
    })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `ray-analysis-${new Date().toISOString().slice(0, 10)}.bitsam-report`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const importCases = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return
    try {
      const parsed = JSON.parse(await file.text()) as Partial<AnalysisCaseFile>
      if (
        parsed.format !== 'tv-leakage-analysis-cases' ||
        parsed.schema_version !== 'analysis-cases.v1' ||
        !Array.isArray(parsed.cases)
      ) {
        throw new Error('Invalid analysis case file')
      }
      setAnalysisCases((current) => {
        const merged = new Map(current.map((item) => [item.case_id, item]))
        for (const item of parsed.cases ?? []) {
          if (!item?.case_id || !item.result?.run_id) continue
          merged.set(item.case_id, { ...item, selected: true })
        }
        return [...merged.values()]
      })
      setTab('compare')
    } catch {
      window.alert('지원되는 .bitsam-report 파일이 아닙니다.')
    }
  }

  const updateCaseMetadata = (
    caseId: string,
    patch: { name?: string; note?: string },
  ) => {
    setAnalysisCases((current) =>
      current.map((item) => {
        if (item.case_id !== caseId) return item
        const next = { ...item, ...patch }
        onCaseMetadataChange?.(caseId, next.name, next.note)
        return next
      }),
    )
  }

  return (
    <div
      ref={rootRef}
      role="dialog"
      aria-label="Ray Tracing Analysis Result"
      className="fixed z-50 flex overflow-hidden rounded-xl border border-border bg-background/96 shadow-2xl shadow-black/55 backdrop-blur-xl"
      style={{
        left: frame.x,
        top: frame.y,
        width: frame.width,
        height: frame.height,
      }}
    >
      <div className="flex min-w-0 flex-1 flex-col">
        <div
          className="flex cursor-move items-center justify-between gap-3 border-b border-border bg-muted/25 px-3 py-2.5"
          onPointerDown={(event) => {
            if (
              event.target instanceof HTMLElement &&
              event.target.closest(
                'button, select, input, textarea, option, [role="combobox"]',
              )
            ) {
              return
            }
            begin(event, 'drag')
          }}
        >
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">
              Ray Tracing Analysis Result
            </div>
            <div className="text-[0.62rem] text-muted-foreground">
              {result.run_id} · {result.runtime_sec.toFixed(3)} s
            </div>
          </div>
          <div className="flex items-center gap-2">
            {analysisCases.length > 0 ? (
              <select
                aria-label="Report active case"
                className="h-7 max-w-52 cursor-pointer rounded-md border border-border bg-background px-2 text-[0.68rem]"
                value={reportCaseId ?? ''}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => event.stopPropagation()}
                onChange={(event) => setReportCaseId(event.currentTarget.value)}
              >
                {analysisCases.map((item) => (
                  <option key={item.case_id} value={item.case_id}>
                    {item.name} · {item.cad_name}
                  </option>
                ))}
              </select>
            ) : null}
            <Badge className="bg-primary/12 text-primary">Complete</Badge>
            <Button
              size="icon-xs"
              variant="ghost"
              aria-label="Close result window"
              onClick={() => onOpenChange(false)}
            >
              <X />
            </Button>
          </div>
        </div>

        <div
          className="flex gap-1 overflow-x-auto border-b border-border bg-background/80 p-1.5"
          role="tablist"
          aria-label="Result categories"
        >
          {tabs.map(({ id, label, icon: Icon }) => (
            <Button
              key={id}
              role="tab"
              aria-selected={tab === id}
              size="xs"
              variant={tab === id ? 'secondary' : 'ghost'}
              onClick={() => setTab(id)}
            >
              <Icon />
              {label}
            </Button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {tab === 'compare' ? (
            <div className="space-y-3">
              <section className="rounded-xl border border-primary/20 bg-primary/5 p-3">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <FilePlus2 className="size-4 text-primary" />
                  Save current result as a case
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto]">
                  <input
                    aria-label="Analysis case name"
                    className="h-9 rounded-md border border-border bg-background px-3 text-xs"
                    value={caseName}
                    placeholder={`Case name · ${cadDisplayName}`}
                    onChange={(event) => setCaseName(event.currentTarget.value)}
                  />
                  <input
                    aria-label="Analysis case note"
                    className="h-9 rounded-md border border-border bg-background px-3 text-xs"
                    value={caseNote}
                    placeholder="Structure change / memo"
                    onChange={(event) => setCaseNote(event.currentTarget.value)}
                  />
                  <Button size="sm" onClick={saveCurrentCase}>
                    <Save /> Save case
                  </Button>
                </div>
              </section>

              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-xs text-muted-foreground">
                  첫 번째 체크 Case가 증감률 기준입니다. 체크 해제하면 비교 Report에서 제외됩니다.
                </div>
                <div className="flex gap-2">
                  <input
                    ref={caseFileInputRef}
                    type="file"
                    accept=".bitsam-report,application/json"
                    className="hidden"
                    aria-label="Load analysis cases"
                    onChange={importCases}
                  />
                  <Button
                    size="xs"
                    variant="outline"
                    onClick={() => caseFileInputRef.current?.click()}
                  >
                    <Upload /> Load report
                  </Button>
                  <Button
                    size="xs"
                    variant="outline"
                    disabled={analysisCases.length === 0}
                    onClick={exportCases}
                  >
                    <Download /> Save report
                  </Button>
                </div>
              </div>

              {analysisCases.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border p-8 text-center text-xs text-muted-foreground">
                  현재 결과를 Case로 저장하거나 기존 `.bitsam-report` 파일을 불러오세요.
                </div>
              ) : (
                <div className="overflow-x-auto rounded-xl border border-border">
                  <table className="w-full min-w-[900px] border-collapse text-xs">
                    <thead className="bg-muted/45 text-left">
                      <tr>
                        <th className="p-2">Compare</th>
                        <th className="p-2">Case / CAD</th>
                        <th className="p-2 text-right">Total rays</th>
                        <th className="p-2 text-right">Receiver hits</th>
                        <th className="p-2 text-right">Hit ratio</th>
                        <th className="p-2 text-right">Total flux</th>
                        <th className="p-2 text-right">vs baseline</th>
                        <th className="p-2 text-right">Surface hits</th>
                        <th className="p-2 text-right">Runtime</th>
                        <th className="p-2 text-right">Paths</th>
                        <th className="p-2" />
                      </tr>
                    </thead>
                    <tbody>
                      {analysisCases.map((item) => {
                        const itemHitRatio = item.result.total_rays > 0
                          ? item.result.receiver_hit_count / item.result.total_rays
                          : 0
                        const flux = caseFlux(item.result)
                        const baselineFlux = baselineCase
                          ? caseFlux(baselineCase.result)
                          : 0
                        return (
                          <tr
                            key={item.case_id}
                            className={`${item.selected ? 'border-t border-border bg-primary/[0.035]' : 'border-t border-border opacity-55'} ${reportCaseId === item.case_id ? 'ring-1 ring-inset ring-primary/50' : ''} cursor-pointer`}
                            onClick={(event) => {
                              if (
                                event.target instanceof HTMLElement &&
                                event.target.closest('input, button, select, textarea')
                              ) return
                              setReportCaseId(item.case_id)
                            }}
                          >
                            <td className="p-2 text-center">
                              <input
                                type="checkbox"
                                aria-label={`Compare ${item.name}`}
                                checked={item.selected}
                                onPointerDown={(event) => event.stopPropagation()}
                                onClick={(event) => event.stopPropagation()}
                                onChange={(event) => {
                                  const selected = event.currentTarget.checked
                                  setAnalysisCases((current) =>
                                    current.map((candidate) =>
                                      candidate.case_id === item.case_id
                                        ? { ...candidate, selected }
                                        : candidate,
                                    ),
                                  )
                                }}
                              />
                            </td>
                            <td className="p-2">
                              <input
                                aria-label={`Case name ${item.case_id}`}
                                className="h-7 w-full min-w-36 rounded border border-transparent bg-transparent px-1 font-semibold hover:border-border focus:border-primary focus:bg-background"
                                value={item.name}
                                onClick={(event) => event.stopPropagation()}
                                onChange={(event) =>
                                  updateCaseMetadata(item.case_id, {
                                    name: event.currentTarget.value,
                                  })
                                }
                              />
                              <div className="text-[0.62rem] text-muted-foreground">{item.cad_name}</div>
                              <input
                                aria-label={`Case condition ${item.case_id}`}
                                className="mt-1 h-6 w-full min-w-36 rounded border border-transparent bg-transparent px-1 text-[0.62rem] text-muted-foreground hover:border-border focus:border-primary focus:bg-background"
                                value={item.note}
                                placeholder="조건 또는 변경 내용 입력"
                                onClick={(event) => event.stopPropagation()}
                                onChange={(event) =>
                                  updateCaseMetadata(item.case_id, {
                                    note: event.currentTarget.value,
                                  })
                                }
                              />
                            </td>
                            <td className="p-2 text-right tabular-nums">{item.result.total_rays.toLocaleString()}</td>
                            <td className="p-2 text-right tabular-nums">{item.result.receiver_hit_count.toLocaleString()}</td>
                            <td className="p-2 text-right tabular-nums">{(itemHitRatio * 100).toFixed(3)}%</td>
                            <td className="p-2 text-right tabular-nums">{formatMetric(flux)} lm</td>
                            <td className="p-2 text-right font-semibold tabular-nums">{baselineCase ? percentChange(flux, baselineFlux) : '—'}</td>
                            <td className="p-2 text-right tabular-nums">{item.result.surface_hit_count.toLocaleString()}</td>
                            <td className="p-2 text-right tabular-nums">{item.result.runtime_sec.toFixed(2)} s</td>
                            <td className="p-2 text-right tabular-nums">{item.result.stored_paths.length.toLocaleString()}</td>
                            <td className="p-2 text-right">
                              <Button
                                size="icon-xs"
                                variant="ghost"
                                aria-label={`Delete ${item.name}`}
                                onClick={() => setAnalysisCases((current) => current.filter((candidate) => candidate.case_id !== item.case_id))}
                              >
                                <Trash2 />
                              </Button>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : null}
          {tab === 'summary' ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Stat
                  label="Total rays"
                  value={result.total_rays.toLocaleString()}
                />
                <Stat
                  label="Receiver hits"
                  value={result.receiver_hit_count.toLocaleString()}
                />
                <Stat
                  label="Hit ratio"
                  value={`${(hitRatio * 100).toFixed(3)}%`}
                />
                <Stat
                  label="Surface interactions"
                  value={result.surface_hit_count.toLocaleString()}
                />
                <Stat
                  label="Direct flux"
                  value={`${formatMetric(
                    contribution.direct_receiver_flux_lumen,
                  )} lm`}
                />
                <Stat
                  label="Reflected flux"
                  value={`${formatMetric(
                    contribution.reflected_receiver_flux_lumen,
                  )} lm`}
                />
                <Stat
                  label="Ray rate"
                  value={`${Math.round(
                    numeric(performance.rays_per_sec),
                  ).toLocaleString()} /s`}
                />
                <Stat
                  label="Stored paths"
                  value={result.stored_paths.length.toLocaleString()}
                />
              </div>
              <p className="rounded-lg border border-border bg-muted/20 p-3 text-xs leading-5 text-muted-foreground">
                Intersection backend ·{' '}
                {String(
                  performance.intersection_backend ??
                    result.config.intersection_backend,
                ).toUpperCase()}
                {' · '}BVH build{' '}
                {formatMetric(performance.bvh_build_sec)} s
              </p>
              {RAY_SECTION_VIEW_ENABLED && scene ? (
                <div className="space-y-2">
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
                    Ray Section View
                    <HelpTooltip label="Ray Section View 도움말">
                      각 Receiver의 법선과 수직 방향을 지나는 단면으로 CAD를
                      잘라, 그 Receiver에 도달한 ray(직접·반사)를 함께
                      보여주는 정적 이미지입니다. 일부 형상에서는 단면이
                      완전히 닫히지 않을 수 있어 참고용으로 사용하세요.
                    </HelpTooltip>
                  </div>
                  <div className="grid gap-3">
                    {result.receivers
                      .filter((receiver) => receiver.enabled)
                      .map((receiver) => (
                        <RaySectionImage
                          key={receiver.receiver_id}
                          scene={scene}
                          receiver={receiver}
                          storedPaths={result.stored_paths}
                          roiFaceIds={roiFaceIds}
                        />
                      ))}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}

          {tab === 'surface' ? (
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-2">
                <Stat
                  label="Surface hits"
                  value={Math.round(
                    numeric(optical.surface_hit_count),
                  ).toLocaleString()}
                />
                <Stat
                  label="Unassigned"
                  value={Math.round(
                    numeric(optical.unassigned_surface_hit_count),
                  ).toLocaleString()}
                />
                <Stat
                  label="Components"
                  value={componentRows.length.toLocaleString()}
                />
              </div>
              {componentRows.length === 0 ? (
                <p className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-muted-foreground">
                  Detailed contribution mode에서 component 기여도가
                  표시됩니다.
                </p>
              ) : (
                <div className="overflow-hidden rounded-lg border border-border">
                  {componentRows.map(({ name, values }) => (
                    <div
                      key={name}
                      className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-3 border-b border-border px-3 py-2 text-xs last:border-b-0"
                    >
                      <span className="truncate font-medium">{name}</span>
                      <span className="text-muted-foreground">
                        {Math.round(
                          numeric(values.receiver_hit_count),
                        ).toLocaleString()}{' '}
                        hits
                      </span>
                      <span className="font-mono">
                        {formatMetric(values.receiver_flux_lumen)} lm
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : null}

          {tab === 'bounce' ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Stat
                  label="Direct hits"
                  value={contribution.direct_receiver_hit_count.toLocaleString()}
                />
                <Stat
                  label="Reflected hits"
                  value={contribution.reflected_receiver_hit_count.toLocaleString()}
                />
                <Stat
                  label="Blocked"
                  value={Math.round(
                    numeric(reflection.reflection_blocked_count),
                  ).toLocaleString()}
                />
                <Stat
                  label="Escaped"
                  value={Math.round(
                    numeric(reflection.reflection_escaped_count),
                  ).toLocaleString()}
                />
              </div>
              <div className="overflow-hidden rounded-lg border border-border">
                {(['specular', 'gaussian', 'lambertian'] as const).map(
                  (name) => {
                    const values = objectValue(
                      contribution.lobes,
                      name,
                    )
                    return (
                      <div
                        key={name}
                        className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-border px-3 py-2 text-xs capitalize last:border-b-0"
                      >
                        <span className="font-medium">{name}</span>
                        <span className="text-muted-foreground">
                          {Math.round(
                            numeric(values.receiver_hit_count),
                          ).toLocaleString()}{' '}
                          hits
                        </span>
                        <span className="font-mono">
                          {formatMetric(values.receiver_flux_lumen)} lm
                        </span>
                      </div>
                    )
                  },
                )}
              </div>
            </div>
          ) : null}

          {tab === 'receiver' ? (
            <div className="grid gap-3">
              {result.receivers.map((receiver) => {
                const values = objectValue(
                  result.metrics,
                  receiver.receiver_id,
                )
                const grid = result.receiver_grids.find(
                  (candidate) =>
                    candidate.receiver_id === receiver.receiver_id,
                )
                return (
                  <section
                    key={receiver.receiver_id}
                    className="rounded-lg border border-border bg-background/40 p-3"
                  >
                    <div className="text-xs font-semibold">
                      {receiver.display_name || receiver.receiver_id}
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-1.5">
                      <Stat
                        label="Peak nit_est"
                        value={formatMetric(values.peak_nit_est)}
                      />
                      <Stat
                        label="Mean nit_est"
                        value={formatMetric(values.mean_nit_est)}
                      />
                      <Stat
                        label="Flux"
                        value={`${formatMetric(
                          values.total_flux_lumen,
                        )} lm`}
                      />
                    </div>
                    {grid ? (
                      <ReceiverHeatmap
                        grid={grid}
                        receiver={receiver}
                      />
                    ) : null}
                  </section>
                )
              })}
            </div>
          ) : null}
        </div>
      </div>
      <button
        type="button"
        aria-label="Resize result window"
        className="absolute right-0 bottom-0 flex size-6 cursor-nwse-resize items-center justify-center text-muted-foreground hover:text-foreground"
        onPointerDown={(event) => begin(event, 'resize')}
      >
        <Grip className="size-3.5" />
      </button>
    </div>
  )
}
