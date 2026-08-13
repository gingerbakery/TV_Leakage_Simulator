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
  Grip,
  Layers3,
  Move,
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
  baseline_case_id?: string | null
  cases: AnalysisCase[]
}

function caseFlux(result: RayTraceResult): number {
  const summary = result.contribution_summary
  return (
    numeric(summary.direct_receiver_flux_lumen) +
    numeric(summary.reflected_receiver_flux_lumen)
  )
}

function caseLuminance(result: RayTraceResult): {
  peakNit: number
  meanNit: number
  lightAreaMm2: Record<1 | 5 | 10, number>
  lightAreaRatio: Record<1 | 5 | 10, number>
} {
  let peakNit = 0
  let weightedMean = 0
  let totalArea = 0
  for (const receiver of result.receivers) {
    const metrics = objectValue(result.metrics, receiver.receiver_id)
    const receiverPeak = numeric(metrics.peak_nit_est)
    const receiverMean = numeric(metrics.mean_nit_est)
    const area = Math.max(0, receiver.width_mm * receiver.height_mm)
    peakNit = Math.max(peakNit, receiverPeak)
    weightedMean += receiverMean * area
    totalArea += area
  }
  const meanNit = totalArea > 0 ? weightedMean / totalArea : 0
  const thresholds = [1, 5, 10] as const
  const lightAreaMm2: Record<1 | 5 | 10, number> = { 1: 0, 5: 0, 10: 0 }
  let sampledAreaMm2 = 0
  for (const grid of result.receiver_grids) {
    const binAreaMm2 = Math.max(0, numeric(grid.bin_area_mm2))
    const binAreaM2 = binAreaMm2 * 1e-6
    for (const row of grid.flux_lumen) {
      for (const flux of row) {
        sampledAreaMm2 += binAreaMm2
        const nit =
          binAreaM2 > 0
            ? (result.config.k_abs * result.config.k_brdf * numeric(flux)) /
              binAreaM2 /
              Math.PI
            : 0
        for (const threshold of thresholds) {
          if (peakNit > 0 && nit >= peakNit * (threshold / 100)) {
            lightAreaMm2[threshold] += binAreaMm2
          }
        }
      }
    }
  }
  return {
    peakNit,
    meanNit,
    lightAreaMm2,
    lightAreaRatio: {
      1: sampledAreaMm2 > 0 ? (lightAreaMm2[1] / sampledAreaMm2) * 100 : 0,
      5: sampledAreaMm2 > 0 ? (lightAreaMm2[5] / sampledAreaMm2) * 100 : 0,
      10: sampledAreaMm2 > 0 ? (lightAreaMm2[10] / sampledAreaMm2) * 100 : 0,
    },
  }
}

function receiverLightAreas(
  result: RayTraceResult,
  receiverId: string,
): Record<1 | 5 | 10, number> {
  const areas: Record<1 | 5 | 10, number> = { 1: 0, 5: 0, 10: 0 }
  const grid = result.receiver_grids.find(
    (candidate) => candidate.receiver_id === receiverId,
  )
  if (!grid) return areas
  const peakNit = numeric(
    objectValue(result.metrics, receiverId).peak_nit_est,
  )
  if (peakNit <= 0) return areas
  const binAreaMm2 = Math.max(0, numeric(grid.bin_area_mm2))
  const binAreaM2 = binAreaMm2 * 1e-6
  if (binAreaM2 <= 0) return areas
  for (const row of grid.flux_lumen) {
    for (const flux of row) {
      const nit =
        (result.config.k_abs * result.config.k_brdf * numeric(flux)) /
        binAreaM2 /
        Math.PI
      for (const threshold of [1, 5, 10] as const) {
        if (nit >= peakNit * (threshold / 100)) {
          areas[threshold] += binAreaMm2
        }
      }
    }
  }
  return areas
}

function comparisonConditionMismatches(
  result: RayTraceResult,
  baseline: RayTraceResult,
): string[] {
  const mismatches: string[] = []
  const equivalent = (left: unknown, right: unknown): boolean => {
    if (typeof left === 'number' && typeof right === 'number') {
      const scale = Math.max(1, Math.abs(left), Math.abs(right))
      return Math.abs(left - right) <= scale * 1e-6
    }
    if (Array.isArray(left) && Array.isArray(right)) {
      return left.length === right.length &&
        left.every((value, index) => equivalent(value, right[index]))
    }
    return left === right || (left == null && right == null)
  }
  const different = (left: unknown, right: unknown) =>
    !equivalent(left, right)
  const traceFields = [
    ['Ray count', 'ray_count'],
    ['Max reflection', 'max_depth'],
    ['Seed', 'seed'],
    ['Minimum energy', 'min_energy'],
    ['Intersection epsilon', 'epsilon_mm'],
    ['k_abs', 'k_abs'],
    ['k_brdf', 'k_brdf'],
    ['Termination mode', 'termination_mode'],
  ] as const
  for (const [label, key] of traceFields) {
    if (different(result.config[key], baseline.config[key])) {
      mismatches.push(`Ray 설정 · ${label}`)
    }
  }

  const emitters = result.emitters.filter((item) => item.enabled)
  const baselineEmitters = baseline.emitters.filter((item) => item.enabled)
  if (emitters.length !== baselineEmitters.length) {
    mismatches.push('Emitter · 활성 개수')
  }
  const commonEmitterFields = [
    ['형식', 'emitter_type'],
    ['방향 반전', 'normal_flip'],
    ['분포 방식', 'direction_distribution'],
    ['Power mode', 'power_mode'],
    ['중심 위치', 'center'],
    ['U축', 'u_axis'],
    ['V축', 'v_axis'],
    ['폭', 'width_mm'],
    ['높이', 'height_mm'],
    ['Ray count', 'ray_count'],
    ['Seed', 'seed'],
  ] as const
  for (let index = 0; index < Math.min(emitters.length, baselineEmitters.length); index += 1) {
    const emitter = emitters[index]
    const baseEmitter = baselineEmitters[index]
    for (const [label, key] of commonEmitterFields) {
      if (different(emitter[key], baseEmitter[key])) {
        mismatches.push(`Emitter ${index + 1} · ${label}`)
      }
    }
    if (
      emitter.normal_mode === 'custom' &&
      baseEmitter.normal_mode === 'custom' &&
      different(emitter.custom_normal, baseEmitter.custom_normal)
    ) mismatches.push(`Emitter ${index + 1} · 사용자 방향`)
    if (
      emitter.direction_distribution === 'gaussian' &&
      baseEmitter.direction_distribution === 'gaussian' &&
      different(emitter.gaussian_sigma_deg, baseEmitter.gaussian_sigma_deg)
    ) mismatches.push(`Emitter ${index + 1} · Gaussian Sigma`)
    if (emitter.power_mode === baseEmitter.power_mode) {
      const powerField = emitter.power_mode === 'set_luminance'
        ? ['휘도', 'luminance_nit'] as const
        : emitter.power_mode === 'power_per_area'
          ? ['Power density', 'power_density_lm_per_m2'] as const
          : ['Total power', 'power_lumen'] as const
      if (different(emitter[powerField[1]], baseEmitter[powerField[1]])) {
        mismatches.push(`Emitter ${index + 1} · ${powerField[0]}`)
      }
    }
  }

  const receivers = result.receivers.filter((item) => item.enabled)
  const baselineReceivers = baseline.receivers.filter((item) => item.enabled)
  if (receivers.length !== baselineReceivers.length) {
    mismatches.push('Receiver · 활성 개수')
  }
  const receiverFields = [
    ['중심 위치', 'center'],
    ['법선 방향', 'normal'],
    ['폭', 'width_mm'],
    ['높이', 'height_mm'],
    ['해상도', 'resolution'],
    ['수광 각도', 'acceptance_angle_deg'],
    ['방향 반전', 'normal_flip'],
  ] as const
  for (let index = 0; index < Math.min(receivers.length, baselineReceivers.length); index += 1) {
    const receiver = receivers[index]
    const baseReceiver = baselineReceivers[index]
    for (const [label, key] of receiverFields) {
      if (different(receiver[key], baseReceiver[key])) {
        mismatches.push(`Receiver ${index + 1} · ${label}`)
      }
    }
    const axesParallel = (
      left: readonly number[] | null,
      right: readonly number[] | null,
    ) => {
      if (left === null || right === null) return left === right
      if (left.length !== 3 || right.length !== 3) return false
      const leftLength = Math.hypot(left[0], left[1], left[2])
      const rightLength = Math.hypot(right[0], right[1], right[2])
      if (leftLength <= 1e-12 || rightLength <= 1e-12) return false
      const cosine =
        (left[0] * right[0] + left[1] * right[1] + left[2] * right[2]) /
        (leftLength * rightLength)
      // +axis and -axis describe the same physical Receiver edge. Only an
      // actual in-plane rotation should invalidate a structural comparison.
      return Math.abs(Math.abs(cosine) - 1) <= 1e-6
    }
    if (
      !axesParallel(receiver.u_axis, baseReceiver.u_axis) ||
      !axesParallel(receiver.v_axis, baseReceiver.v_axis)
    ) {
      mismatches.push(`Receiver ${index + 1} · 면 회전 방향`)
    }
  }
  return mismatches
}

function leakageImprovementScore(
  result: RayTraceResult,
  baseline: RayTraceResult,
): number | null {
  if (comparisonConditionMismatches(result, baseline).length > 0) {
    return null
  }
  const current = caseLuminance(result)
  const base = caseLuminance(baseline)
  const metrics = [
    { value: current.peakNit, baseline: base.peakNit, weight: 0.6 },
    { value: caseFlux(result), baseline: caseFlux(baseline), weight: 0.25 },
    { value: current.lightAreaMm2[5], baseline: base.lightAreaMm2[5], weight: 0.15 },
  ]
  const usable = metrics.filter((metric) => metric.baseline > 0)
  const totalWeight = usable.reduce((sum, metric) => sum + metric.weight, 0)
  if (totalWeight === 0) return null
  const severityRatio = usable.reduce(
    (sum, metric) =>
      sum + (metric.weight / totalWeight) * (metric.value / metric.baseline),
    0,
  )
  return Math.max(0, Math.min(100, 100 / (1 + severityRatio)))
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
  help,
}: {
  label: string
  value: string
  help?: string
}) {
  return (
    <div className="rounded-lg border border-border bg-background/45 p-2.5">
      <div className="flex items-center gap-1 text-[0.62rem] text-muted-foreground">
        {label}
        {help ? (
          <HelpTooltip label={`${label} 설명`}>{help}</HelpTooltip>
        ) : null}
      </div>
      <div className="mt-1 text-sm font-semibold">{value}</div>
    </div>
  )
}

export function RayTraceResultWindow({
  open,
  result: liveResult,
  scene,
  roiFaceIds,
  reportCases = [],
  onCaseMetadataChange,
  onOpenChange,
}: RayTraceResultWindowProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const operationRef = useRef<PointerOperation | null>(null)
  const [tab, setTab] = useState<ResultTab>('summary')
  const [analysisCases, setAnalysisCases] = useState<AnalysisCase[]>([])
  const [baselineCaseId, setBaselineCaseId] = useState<string | null>(null)
  const [reportCaseId, setReportCaseId] = useState<string | null>(null)
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
    setBaselineCaseId((current) => {
      if (
        current &&
        analysisCases.some((item) => item.case_id === current && item.selected)
      ) return current
      return analysisCases.find((item) => item.selected)?.case_id ?? null
    })
  }, [analysisCases])

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
  const baselineCase =
    selectedCases.find((item) => item.case_id === baselineCaseId) ??
    selectedCases[0] ??
    null

  const exportCases = () => {
    const cases = selectedCases.length > 0 ? selectedCases : analysisCases
    if (cases.length === 0) return
    const payload: AnalysisCaseFile = {
      format: 'tv-leakage-analysis-cases',
      schema_version: 'analysis-cases.v1',
      saved_at: new Date().toISOString(),
      baseline_case_id: baselineCase?.case_id ?? null,
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
      if (typeof parsed.baseline_case_id === 'string') {
        setBaselineCaseId(parsed.baseline_case_id)
      }
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
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-xs text-muted-foreground">
                  Import CAD Case의 마지막 Ray 결과를 자동 비교합니다. Compare 대상을 체크하고 Baseline을 직접 선택하세요.
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
                  <table className="w-full min-w-[1020px] border-collapse text-xs">
                    <thead className="bg-muted/45 text-left">
                      <tr>
                        <th className="p-2">Compare</th>
                        <th className="p-2 text-center">Baseline</th>
                        <th className="w-40 p-2">Case / CAD</th>
                        <th className="p-2">
                          <span className="flex items-center justify-end gap-1">
                            빛샘 개선 점수
                            <HelpTooltip label="빛샘 개선 점수 설명">
                              Baseline 50점을 기준으로 Peak nit 60%, Total flux 25%, 광영역(@5%) 15%를 가중 평가합니다. 점수가 높을수록 빛샘이 개선된 구조입니다.
                            </HelpTooltip>
                          </span>
                        </th>
                        <th className="p-2 text-center">비교 조건</th>
                        <th className="p-2 text-right">Hit ratio</th>
                        <th className="p-2">
                          <span className="flex items-center justify-end gap-1">
                            Total flux
                            <HelpTooltip label="Total flux 설명">
                              모든 Receiver에 도달한 전체 광량(lm)입니다. 값이 작을수록 유입된 빛샘 에너지가 적습니다.
                            </HelpTooltip>
                          </span>
                        </th>
                        <th className="p-2">
                          <span className="flex items-center justify-end gap-1">
                            Peak nit
                            <HelpTooltip label="Peak nit 설명">
                              Receiver Heatmap에서 가장 밝은 지점의 추정 휘도입니다. 체감상 강하게 보이는 국부 빛샘을 나타냅니다.
                            </HelpTooltip>
                          </span>
                        </th>
                        <th className="p-2">
                          <span className="flex items-center justify-end gap-1">
                            광영역(@5%)
                            <HelpTooltip label="광영역 5% 설명">
                              해당 Case의 최대 Peak nit 중 5% 이상인 Receiver Heatmap 셀의 실제 면적 합계(mm²)입니다.
                            </HelpTooltip>
                          </span>
                        </th>
                        <th className="p-2" />
                      </tr>
                    </thead>
                    <tbody>
                      {analysisCases.map((item) => {
                        const itemHitRatio = item.result.total_rays > 0
                          ? item.result.receiver_hit_count / item.result.total_rays
                          : 0
                        const flux = caseFlux(item.result)
                        const luminance = caseLuminance(item.result)
                        const score = baselineCase
                          ? leakageImprovementScore(item.result, baselineCase.result)
                          : null
                        const conditionMismatches = baselineCase
                          ? comparisonConditionMismatches(
                              item.result,
                              baselineCase.result,
                            )
                          : []
                        const conditionsMatch = Boolean(baselineCase) &&
                          conditionMismatches.length === 0
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
                            <td className="p-2 text-center">
                              <input
                                type="radio"
                                name="analysis-baseline"
                                aria-label={`Set ${item.name} as baseline`}
                                checked={baselineCase?.case_id === item.case_id}
                                onPointerDown={(event) => event.stopPropagation()}
                                onClick={(event) => event.stopPropagation()}
                                onChange={() => {
                                  setBaselineCaseId(item.case_id)
                                  setAnalysisCases((current) =>
                                    current.map((candidate) =>
                                      candidate.case_id === item.case_id
                                        ? { ...candidate, selected: true }
                                        : candidate,
                                    ),
                                  )
                                }}
                              />
                            </td>
                            <td className="w-40 max-w-40 p-2">
                              <input
                                aria-label={`Case name ${item.case_id}`}
                                className="h-7 w-full min-w-0 rounded border border-transparent bg-transparent px-1 font-semibold hover:border-border focus:border-primary focus:bg-background"
                                value={item.name}
                                onClick={(event) => event.stopPropagation()}
                                onChange={(event) =>
                                  updateCaseMetadata(item.case_id, {
                                    name: event.currentTarget.value,
                                  })
                                }
                              />
                              <div className="truncate text-[0.62rem] text-muted-foreground" title={item.cad_name}>{item.cad_name}</div>
                              <input
                                aria-label={`Case condition ${item.case_id}`}
                                className="mt-1 h-6 w-full min-w-0 rounded border border-transparent bg-transparent px-1 text-[0.62rem] text-muted-foreground hover:border-border focus:border-primary focus:bg-background"
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
                            <td className="p-2 text-right text-base font-bold tabular-nums">
                              {score === null ? '—' : score.toFixed(1)}
                            </td>
                            <td className="p-2 text-center">
                              <span className="inline-flex items-center gap-1">
                                <Badge variant={conditionsMatch ? 'secondary' : 'destructive'}>
                                  {conditionsMatch ? '일치' : '불일치'}
                                </Badge>
                                <HelpTooltip label={`${item.name} 비교 조건 설명`}>
                                  {conditionsMatch ? (
                                    'Baseline과 Ray, Emitter, Receiver 설정 조건이 모두 일치합니다.'
                                  ) : (
                                    <span className="block">
                                      <span className="mb-1 block font-semibold">Baseline과 다른 설정</span>
                                      {conditionMismatches.map((mismatch) => (
                                        <span key={mismatch} className="block">• {mismatch}</span>
                                      ))}
                                    </span>
                                  )}
                                </HelpTooltip>
                              </span>
                            </td>
                            <td className="p-2 text-right tabular-nums">{(itemHitRatio * 100).toFixed(3)}%</td>
                            <td className="p-2 text-right tabular-nums">{formatMetric(flux)} lm</td>
                            <td className="p-2 text-right tabular-nums">{formatMetric(luminance.peakNit)}</td>
                            <td className="p-2 text-right font-semibold tabular-nums">{formatMetric(luminance.lightAreaMm2[5])} mm²</td>
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
              <div className="rounded-lg border border-border bg-muted/20 p-2.5 text-[0.68rem] leading-5 text-muted-foreground">
                <p>• 빛샘 개선 점수는 Baseline 50점을 기준으로 Peak nit 60%, Total flux 25%, 광영역(@5%) 15%를 반영합니다.</p>
                <p className="pl-3">(점수가 높을수록 개선된 구조입니다.)</p>
                <p>• 동일 Receiver/Emitter 설정 조건 기준으로 빛샘 개선 점수가 평가됩니다.</p>
              </div>
            </div>
          ) : null}
          {tab === 'summary' ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Stat
                  label="Total rays"
                  value={result.total_rays.toLocaleString()}
                  help="이번 해석에서 실제로 추적한 전체 Ray 개수입니다. Case 비교 시 동일해야 하는 기본 샘플 수입니다."
                />
                <Stat
                  label="Receiver hits"
                  value={result.receiver_hit_count.toLocaleString()}
                  help="하나 이상의 Receiver에 도달한 Ray 개수입니다. 동일 Ray 수 조건에서 많을수록 Receiver 방향으로 빛이 더 많이 전달된 것입니다."
                />
                <Stat
                  label="Hit ratio"
                  value={`${(hitRatio * 100).toFixed(3)}%`}
                  help="전체 Ray 중 Receiver에 도달한 비율입니다. 도달 빈도를 뜻하며 Ray마다 가진 광량 차이는 반영하지 않습니다."
                />
                <Stat
                  label="Surface interactions"
                  value={result.surface_hit_count.toLocaleString()}
                  help="Ray가 추적 가능한 CAD 표면과 충돌한 누적 횟수입니다. 하나의 Ray가 여러 번 반사되면 여러 번 집계됩니다."
                />
                <Stat
                  label="Direct flux"
                  value={`${formatMetric(
                    contribution.direct_receiver_flux_lumen,
                  )} lm`}
                  help="CAD 표면에서 반사되지 않고 Emitter에서 Receiver로 직접 도달한 전체 광량입니다."
                />
                <Stat
                  label="Reflected flux"
                  value={`${formatMetric(
                    contribution.reflected_receiver_flux_lumen,
                  )} lm`}
                  help="한 번 이상 CAD 표면과 상호작용한 뒤 Receiver에 도달한 전체 광량입니다. 구조적 반사 경로의 영향을 나타냅니다."
                />
                <Stat
                  label="Ray rate"
                  value={`${Math.round(
                    numeric(performance.rays_per_sec),
                  ).toLocaleString()} /s`}
                  help="초당 처리한 Ray 개수로, 해석 성능 확인용 값입니다. 빛샘 품질을 평가하는 광학 결과값은 아닙니다."
                />
                <Stat
                  label="Stored paths"
                  value={result.stored_paths.length.toLocaleString()}
                  help="3D 경로 및 Section View 확인을 위해 저장된 대표 Ray 경로 수입니다. 추적된 모든 Ray 수와 같지 않을 수 있습니다."
                />
              </div>
              <p className="flex items-center gap-1 rounded-lg border border-border bg-muted/20 p-3 text-xs leading-5 text-muted-foreground">
                Intersection backend
                <HelpTooltip label="Intersection backend 설명">
                  Ray와 CAD Mesh의 충돌을 검색한 계산 방식입니다. BVH build는 가속 구조 생성 시간이며 빛샘 결과값이 아니라 성능 진단값입니다.
                </HelpTooltip>
                {' · '}
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
                  help="Ray가 CAD 표면에 충돌한 누적 횟수입니다. 반사 깊이가 증가할수록 한 Ray에서 여러 Surface hit가 발생할 수 있습니다."
                />
                <Stat
                  label="Unassigned"
                  value={Math.round(
                    numeric(optical.unassigned_surface_hit_count),
                  ).toLocaleString()}
                  help="Material optical profile이 지정되지 않은 표면에서 발생한 충돌 수입니다. 값이 있으면 기본 광학값이 적용됐는지 확인해야 합니다."
                />
                <Stat
                  label="Components"
                  value={componentRows.length.toLocaleString()}
                  help="Receiver 도달 경로에 기여한 것으로 상세 집계된 Component 수입니다. 전체 CAD Component 수와 다를 수 있습니다."
                />
              </div>
              {componentRows.length === 0 ? (
                <p className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-muted-foreground">
                  Detailed contribution mode에서 component 기여도가
                  표시됩니다.
                </p>
              ) : (
                <div className="overflow-hidden rounded-lg border border-border">
                  <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-3 border-b border-border bg-muted/25 px-3 py-2 text-[0.62rem] text-muted-foreground">
                    <span className="flex items-center gap-1">
                      Component
                      <HelpTooltip label="Surface component 설명">
                        Receiver 도달 Ray가 상호작용한 기구 Component입니다.
                      </HelpTooltip>
                    </span>
                    <span className="flex items-center gap-1">
                      Receiver hits
                      <HelpTooltip label="Component Receiver hits 설명">
                        해당 Component를 거친 뒤 Receiver에 도달한 Ray 경로 수입니다.
                      </HelpTooltip>
                    </span>
                    <span className="flex items-center gap-1">
                      Flux
                      <HelpTooltip label="Component flux 설명">
                        해당 Component 경로가 Receiver에 전달한 광량 기여도입니다.
                      </HelpTooltip>
                    </span>
                  </div>
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
                  help="CAD 표면 반사 없이 Receiver에 직접 도달한 Ray 수입니다."
                />
                <Stat
                  label="Reflected hits"
                  value={contribution.reflected_receiver_hit_count.toLocaleString()}
                  help="한 번 이상 CAD 표면에서 반사 또는 산란된 뒤 Receiver에 도달한 Ray 수입니다."
                />
                <Stat
                  label="Blocked"
                  value={Math.round(
                    numeric(reflection.reflection_blocked_count),
                  ).toLocaleString()}
                  help="표면 충돌 후 유효한 다음 경로를 만들지 못하고 차단된 반사 시도 수입니다."
                />
                <Stat
                  label="Escaped"
                  value={Math.round(
                    numeric(reflection.reflection_escaped_count),
                  ).toLocaleString()}
                  help="반사 후 CAD 구조 밖으로 빠져나가 Receiver에 도달하지 않은 경로 수입니다."
                />
              </div>
              <div className="overflow-hidden rounded-lg border border-border">
                <div className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-border bg-muted/25 px-3 py-2 text-[0.62rem] text-muted-foreground">
                  <span className="flex items-center gap-1">
                    Reflection model
                    <HelpTooltip label="Reflection model 설명">
                      Receiver에 도달한 반사 경로를 Specular, Gaussian, Lambertian 산란 방식별로 분류합니다.
                    </HelpTooltip>
                  </span>
                  <span className="flex items-center gap-1">
                    Receiver hits
                    <HelpTooltip label="Reflection Receiver hits 설명">
                      해당 반사 방식으로 마지막 분류된 Receiver 도달 Ray 수입니다.
                    </HelpTooltip>
                  </span>
                  <span className="flex items-center gap-1">
                    Flux
                    <HelpTooltip label="Reflection flux 설명">
                      해당 반사 방식의 경로가 Receiver에 전달한 전체 광량입니다.
                    </HelpTooltip>
                  </span>
                </div>
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
                        <span className="flex items-center gap-1 font-medium">
                          {name}
                          <HelpTooltip label={`${name} 반사 설명`}>
                            {name === 'specular'
                              ? '거울 반사처럼 입사각과 반사각을 따라 한 방향으로 집중된 반사 경로입니다.'
                              : name === 'gaussian'
                                ? '정반사 방향을 중심으로 설정된 Sigma 각도만큼 퍼지는 반사 경로입니다.'
                                : '표면 법선 반구 방향으로 넓게 확산되는 난반사 경로입니다.'}
                          </HelpTooltip>
                        </span>
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
                const lightAreas = receiverLightAreas(
                  result,
                  receiver.receiver_id,
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
                        help="이 Receiver Heatmap에서 가장 밝은 셀의 추정 휘도입니다. 국부적으로 가장 강한 빛샘 세기를 나타냅니다."
                      />
                      <Stat
                        label="Mean nit_est"
                        value={formatMetric(values.mean_nit_est)}
                        help="이 Receiver 전체 Heatmap 셀의 평균 추정 휘도입니다. 밝은 영역뿐 아니라 빛이 없는 셀도 포함합니다."
                      />
                      <Stat
                        label="Flux"
                        value={`${formatMetric(
                          values.total_flux_lumen,
                        )} lm`}
                        help="이 Receiver에 도달한 전체 광량입니다. 밝기 세기와 영역을 종합한 에너지 값이며 Peak nit와 의미가 다릅니다."
                      />
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-1.5">
                      <div className="rounded-lg border border-border bg-muted/20 p-2">
                        <div className="flex items-center gap-1 text-[0.62rem] text-muted-foreground">
                          광영역 @1%
                          <HelpTooltip label="광영역 1% 설명">
                            이 Receiver의 Peak nit 중 1% 이상인 Heatmap 셀 면적입니다. 사람 눈에 희미하게 보일 수 있는 약한 확산 영역까지 넓게 확인하는 참고값입니다.
                          </HelpTooltip>
                        </div>
                        <div className="mt-1 font-mono text-xs font-semibold">
                          {formatMetric(lightAreas[1])} mm²
                        </div>
                      </div>
                      <div className="rounded-lg border border-primary/25 bg-primary/5 p-2">
                        <div className="flex items-center gap-1 text-[0.62rem] text-muted-foreground">
                          광영역 @5%
                          <HelpTooltip label="Receiver 광영역 5% 설명">
                            이 Receiver의 Peak nit 중 5% 이상인 Heatmap 셀 면적입니다. Compare 점수에서 대표 광영역으로 사용하는 기준입니다.
                          </HelpTooltip>
                        </div>
                        <div className="mt-1 font-mono text-xs font-semibold">
                          {formatMetric(lightAreas[5])} mm²
                        </div>
                      </div>
                      <div className="rounded-lg border border-border bg-muted/20 p-2">
                        <div className="flex items-center gap-1 text-[0.62rem] text-muted-foreground">
                          광영역 @10%
                          <HelpTooltip label="광영역 10% 설명">
                            이 Receiver의 Peak nit 중 10% 이상인 Heatmap 셀 면적입니다. 비교적 강하고 선명한 빛샘 중심 영역을 나타냅니다.
                          </HelpTooltip>
                        </div>
                        <div className="mt-1 font-mono text-xs font-semibold">
                          {formatMetric(lightAreas[10])} mm²
                        </div>
                      </div>
                    </div>
                    {grid ? (
                      <div className="mt-3">
                        <div className="mb-2 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
                          Receiver Heatmap
                          <HelpTooltip label="Receiver Heatmap 설명">
                            Receiver의 로컬 X/Y 좌표별 입사 광량과 추정 휘도 분포입니다. 색상은 현재 Heatmap 내 상대적인 세기를 나타내며, 마우스를 올리면 셀 위치·광량·조도·휘도를 확인할 수 있습니다.
                          </HelpTooltip>
                        </div>
                        <ReceiverHeatmap
                          grid={grid}
                          receiver={receiver}
                        />
                      </div>
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
