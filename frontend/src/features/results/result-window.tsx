import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react'
import type {
  RayHit,
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
import { getComponentDisplayName } from '@/features/components'
import { rayObjectDisplayName } from '@/features/raytracing/ray-tracing-model'
import { useWorkspaceStore, workspaceSelectors } from '@/stores'

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
  componentNameOverrides?: Record<number, string>
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

interface AnalysisReportSaveFileHandle {
  createWritable(): Promise<{
    write(data: Blob): Promise<void>
    close(): Promise<void>
  }>
}

type AnalysisReportSaveFilePickerWindow = Window & {
  showSaveFilePicker?: (options: {
    suggestedName: string
    types: Array<{
      description: string
      accept: Record<string, string[]>
    }>
  }) => Promise<AnalysisReportSaveFileHandle>
}

type ReceiverCompareScope = 'all' | number
type LuminanceScaleMode = 'auto' | 'compare' | 'customize'

interface LuminanceDisplayScale {
  mode: LuminanceScaleMode
  minNit: number
  maxNit: number
}

function receiversInDisplayOrder(receivers: ReceiverSpec[]): ReceiverSpec[] {
  return receivers
    .map((receiver, index) => ({ receiver, index }))
    .sort((left, right) => {
      const leftNumber = Number(left.receiver.receiver_id.match(/(\d+)(?!.*\d)/)?.[1])
      const rightNumber = Number(right.receiver.receiver_id.match(/(\d+)(?!.*\d)/)?.[1])
      const leftHasNumber = Number.isFinite(leftNumber)
      const rightHasNumber = Number.isFinite(rightNumber)
      if (leftHasNumber && rightHasNumber && leftNumber !== rightNumber) {
        return leftNumber - rightNumber
      }
      if (leftHasNumber !== rightHasNumber) return leftHasNumber ? -1 : 1
      return left.index - right.index
    })
    .map(({ receiver }) => receiver)
}

function scopedReceivers(
  result: RayTraceResult,
  receiverScope: ReceiverCompareScope,
): ReceiverSpec[] {
  const enabled = result.receivers.filter((item) => item.enabled)
  return receiverScope === 'all'
    ? enabled
    : enabled[receiverScope]
      ? [enabled[receiverScope]]
      : []
}

function caseFlux(
  result: RayTraceResult,
  receiverScope: ReceiverCompareScope = 'all',
): number {
  if (receiverScope !== 'all') {
    const receiver = scopedReceivers(result, receiverScope)[0]
    return receiver
      ? numeric(objectValue(result.metrics, receiver.receiver_id).total_flux_lumen)
      : 0
  }
  const summary = result.contribution_summary
  return (
    numeric(summary.direct_receiver_flux_lumen) +
    numeric(summary.reflected_receiver_flux_lumen)
  )
}

function caseReceiverHits(
  result: RayTraceResult,
  receiverScope: ReceiverCompareScope,
): number {
  if (receiverScope === 'all') return result.receiver_hit_count
  const receiver = scopedReceivers(result, receiverScope)[0]
  return receiver
    ? numeric(objectValue(result.metrics, receiver.receiver_id).hit_count)
    : 0
}

function caseLuminance(
  result: RayTraceResult,
  receiverScope: ReceiverCompareScope = 'all',
): {
  peakNit: number
  meanNit: number
  lightAreaMm2: Record<1 | 5 | 10, number>
} {
  let peakNit = 0
  let weightedMean = 0
  let totalArea = 0
  const receivers = scopedReceivers(result, receiverScope)
  const receiverIds = new Set(receivers.map((item) => item.receiver_id))
  for (const receiver of receivers) {
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
  for (const grid of result.receiver_grids.filter((item) => receiverIds.has(item.receiver_id))) {
    const binAreaMm2 = Math.max(0, numeric(grid.bin_area_mm2))
    const binAreaM2 = binAreaMm2 * 1e-6
    for (const row of grid.flux_lumen) {
      for (const flux of row) {
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
  }
}

function correspondingReceiverPeakNit(
  result: RayTraceResult,
  receiverId: string,
  receiverIndex: number,
): number {
  const ordered = receiversInDisplayOrder(result.receivers)
  const receiver =
    ordered.find((item) => item.receiver_id === receiverId) ??
    ordered[receiverIndex]
  return receiver
    ? numeric(objectValue(result.metrics, receiver.receiver_id).peak_nit_est)
    : 0
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
  receiverScope: ReceiverCompareScope = 'all',
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

  const receivers = scopedReceivers(result, receiverScope)
  const baselineReceivers = scopedReceivers(baseline, receiverScope)
  if (receiverScope === 'all' && receivers.length !== baselineReceivers.length) {
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
  receiverScope: ReceiverCompareScope = 'all',
): number | null {
  if (comparisonConditionMismatches(result, baseline, receiverScope).length > 0) {
    return null
  }
  const current = caseLuminance(result, receiverScope)
  const base = caseLuminance(baseline, receiverScope)
  const metrics = [
    { value: current.peakNit, baseline: base.peakNit, weight: 0.6 },
    { value: caseFlux(result, receiverScope), baseline: caseFlux(baseline, receiverScope), weight: 0.25 },
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

interface ReceiverRegion {
  minX: number
  maxX: number
  minY: number
  maxY: number
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
  luminanceScale,
  onLuminanceScaleModeChange,
  customScaleMinNit,
  customScaleMaxNit,
  onCustomScaleMinNitChange,
  onCustomScaleMaxNitChange,
  kAbs,
  kBrdf,
  storedPaths,
  runId,
  componentNames,
  errorTargetPercent,
  sampleCount,
  faceSourceIds,
}: {
  grid: ReceiverGrid
  receiver: ReceiverSpec
  luminanceScale: LuminanceDisplayScale
  onLuminanceScaleModeChange: (mode: LuminanceScaleMode) => void
  customScaleMinNit: number
  customScaleMaxNit: number
  onCustomScaleMinNitChange: (value: number) => void
  onCustomScaleMaxNitChange: (value: number) => void
  kAbs: number
  kBrdf: number
  storedPaths: RayHit[][]
  runId: string
  componentNames: Map<number, string>
  errorTargetPercent: number
  sampleCount: number
  faceSourceIds?: number[]
}) {
  const receiverLabel = rayObjectDisplayName(
    'receiver',
    receiver.receiver_id,
    receiver.display_name,
  )
  const highlightedSelection = useWorkspaceStore(
    workspaceSelectors.highlightedRayPathSelection,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [viewport, setViewport] = useState({
    ...initialReceiverHeatmapViewport,
  })
  const [hover, setHover] =
    useState<ReceiverHeatmapHover | null>(null)
  const [profileColumn, setProfileColumn] = useState(() =>
    Math.floor(Math.max(1, grid.resolution[0]) / 2),
  )
  const [profileDisplayRow, setProfileDisplayRow] = useState(() =>
    Math.floor(Math.max(1, grid.resolution[1]) / 2),
  )
  const [profileDragging, setProfileDragging] = useState(false)
  const [interactionMode, setInteractionMode] = useState<'profile' | 'region'>('profile')
  const [region, setRegion] = useState<ReceiverRegion | null>(null)
  const regionStartRef = useRef<{ x: number; y: number } | null>(null)
  const [displayMode, setDisplayMode] = useState<'luminance' | 'error'>('luminance')
  const [colorMode, setColorMode] = useState<'color' | 'mono'>('color')
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
    setProfileColumn(Math.floor(columns / 2))
    setProfileDisplayRow(Math.floor(rows / 2))
  }, [columns, grid, receiver.height_mm, receiver.width_mm, rows])

  const luminanceValues = useMemo(() => {
    const binAreaM2 = Math.max(grid.bin_area_mm2 * 1e-6, 1e-18)
    const scale = (kAbs * kBrdf) / (binAreaM2 * Math.PI)
    return receiverHeatmapDisplayValues(grid).map((value) => value * scale)
  }, [grid, kAbs, kBrdf])
  const errorValues = useMemo(() => {
    const squaredGrid = grid.flux_squared_lumen2_grid
    if (!squaredGrid) return []
    const n = Math.max(0, sampleCount)
    if (n <= 1) return []
    const values: number[] = []
    for (let displayRow = 0; displayRow < rows; displayRow += 1) {
      const sourceRow = rows - 1 - displayRow
      for (let column = 0; column < columns; column += 1) {
        const flux = numeric(grid.flux_lumen[sourceRow]?.[column])
        const squared = numeric(squaredGrid[sourceRow]?.[column])
        values.push(flux > 0 ? Math.sqrt(Math.max(0, (n * squared / (flux * flux) - 1) / (n - 1))) * 100 : 0)
      }
    }
    return values
  }, [columns, grid, rows, sampleCount])
  const xProfile = useMemo(
    () => Array.from({ length: columns }, (_, column) =>
      numeric(luminanceValues[profileDisplayRow * columns + column]),
    ),
    [columns, luminanceValues, profileDisplayRow],
  )
  const yProfile = useMemo(
    () => Array.from({ length: rows }, (_, displayRow) =>
      numeric(luminanceValues[displayRow * columns + profileColumn]),
    ).reverse(),
    [columns, luminanceValues, profileColumn, rows],
  )
  const regionAnalysis = useMemo(() => {
    if (!region) return null
    let selectedFlux = 0
    let selectedCells = 0
    for (let displayRow = 0; displayRow < rows; displayRow += 1) {
      const normalizedY = (displayRow + 0.5) / rows
      if (normalizedY < region.minY || normalizedY > region.maxY) continue
      const sourceRow = rows - 1 - displayRow
      for (let column = 0; column < columns; column += 1) {
        const normalizedX = (column + 0.5) / columns
        if (normalizedX < region.minX || normalizedX > region.maxX) continue
        selectedCells += 1
        selectedFlux += numeric(grid.flux_lumen[sourceRow]?.[column])
      }
    }
    const totalFlux = receiverHeatmapDisplayValues(grid).reduce(
      (sum, value) => sum + value,
      0,
    )
    const uAxis = receiver.u_axis ?? receiver.base_u_axis
    const vAxis = receiver.v_axis ?? receiver.base_v_axis
    const matchingPaths = storedPaths
      .map((path, pathIndex) => ({ path, pathIndex }))
      .filter(({ path }) => {
      const hit = [...path].reverse().find(
        (event) => event.event_type === 'receiver' && event.receiver_id === receiver.receiver_id,
      )
      if (!hit || !uAxis || !vAxis) return false
      const delta = hit.point.map((value, index) => value - receiver.center[index])
      const localU = delta.reduce((sum, value, index) => sum + value * uAxis[index], 0)
      const localV = delta.reduce((sum, value, index) => sum + value * vAxis[index], 0)
      const normalizedX = localU / receiver.width_mm + 0.5
      const normalizedY = 0.5 - localV / receiver.height_mm
      return normalizedX >= region.minX && normalizedX <= region.maxX &&
        normalizedY >= region.minY && normalizedY <= region.maxY
      })
    const directCount = matchingPaths.filter(
      ({ path }) => !path.some((event) => event.event_type === 'surface'),
    ).length
    const componentContributions = new Map<number, { count: number; flux: number }>()
    const faceContributions = new Map<number, { count: number; flux: number }>()
    const lobeContributions = new Map<string, { count: number; flux: number }>()
    const depthContributions = new Map<number, { count: number; flux: number }>()
    const sequences = new Map<
      string,
      { count: number; flux: number; pathIndices: number[] }
    >()
    for (const { path, pathIndex } of matchingPaths) {
      const receiverHit = [...path].reverse().find((event) => event.event_type === 'receiver')
      const pathFlux = numeric(receiverHit?.receiver_flux_lumen ?? receiverHit?.incoming_energy_lumen)
      const componentIds = path
        .filter((event) => event.event_type === 'surface' && event.component_id !== null)
        .map((event) => event.component_id as number)
      for (const componentId of new Set(componentIds)) {
        const current = componentContributions.get(componentId) ?? { count: 0, flux: 0 }
        current.count += 1
        current.flux += pathFlux
        componentContributions.set(componentId, current)
      }
      const surfaceEvents = path.filter((event) => event.event_type === 'surface')
      for (const faceId of new Set(surfaceEvents.map((event) => faceSourceIds?.[event.face_index] ?? event.face_index))) {
        const current = faceContributions.get(faceId) ?? { count: 0, flux: 0 }
        current.count += 1
        current.flux += pathFlux
        faceContributions.set(faceId, current)
      }
      const lobe = receiverHit?.ray_kind ?? (surfaceEvents.length > 0 ? 'reflected' : 'direct')
      const currentLobe = lobeContributions.get(lobe) ?? { count: 0, flux: 0 }
      currentLobe.count += 1
      currentLobe.flux += pathFlux
      lobeContributions.set(lobe, currentLobe)
      const depth = Math.max(0, ...surfaceEvents.map((event) => event.depth + 1))
      const currentDepth = depthContributions.get(depth) ?? { count: 0, flux: 0 }
      currentDepth.count += 1
      currentDepth.flux += pathFlux
      depthContributions.set(depth, currentDepth)
      const sequence = componentIds.length > 0
        ? componentIds.map((id) => componentNames.get(id) ?? `Component ${id}`).join(' → ')
        : 'Direct to Receiver'
      const currentSequence = sequences.get(sequence) ?? {
        count: 0,
        flux: 0,
        pathIndices: [],
      }
      currentSequence.count += 1
      currentSequence.flux += pathFlux
      currentSequence.pathIndices.push(pathIndex)
      sequences.set(sequence, currentSequence)
    }
    return {
      areaMm2: selectedCells * grid.bin_area_mm2,
      selectedFlux,
      fluxRatio: totalFlux > 0 ? selectedFlux / totalFlux : 0,
      matchingPathCount: matchingPaths.length,
      directCount,
      components: [...componentContributions.entries()]
        .sort((left, right) => right[1].flux - left[1].flux)
        .slice(0, 5),
      faces: [...faceContributions.entries()]
        .sort((left, right) => right[1].flux - left[1].flux)
        .slice(0, 5),
      lobes: [...lobeContributions.entries()].sort((left, right) => right[1].flux - left[1].flux),
      depths: [...depthContributions.entries()].sort((left, right) => left[0] - right[0]),
      sequences: [...sequences.entries()]
        .sort((left, right) => right[1].flux - left[1].flux)
        .slice(0, 5),
    }
  }, [columns, componentNames, faceSourceIds, grid, receiver, region, rows, storedPaths])

  useEffect(() => {
    const canvas = canvasRef.current
    const context = canvas?.getContext('2d')
    if (!canvas || !context) return
    canvas.width = columns
    canvas.height = rows
    const values = displayMode === 'error' && errorValues.length > 0
      ? errorValues
      : luminanceValues
    const luminanceSpan = Math.max(
      luminanceScale.maxNit - luminanceScale.minNit,
      1e-12,
    )
    const image = context.createImageData(columns, rows)
    for (let index = 0; index < columns * rows; index += 1) {
      const normalized = displayMode === 'error'
        ? Math.min(1, (values[index] || 0) / Math.max(errorTargetPercent * 2, 0.01))
        : Math.sqrt(Math.min(1, Math.max(
          0,
          (numeric(values[index]) - luminanceScale.minNit) / luminanceSpan,
        )))
      const pixel = index * 4
      const [red, green, blue] = colorMode === 'mono'
        ? [Math.round(normalized * 255), Math.round(normalized * 255), Math.round(normalized * 255)]
        : receiverHeatmapColor(normalized)
      image.data[pixel] = red
      image.data[pixel + 1] = green
      image.data[pixel + 2] = blue
      image.data[pixel + 3] = 255
    }
    context.putImageData(image, 0, 0)
  }, [
    colorMode,
    columns,
    displayMode,
    errorTargetPercent,
    errorValues,
    luminanceScale.maxNit,
    luminanceScale.minNit,
    luminanceValues,
    rows,
  ])

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

  const receiverPosition = (
    element: HTMLDivElement,
    clientX: number,
    clientY: number,
  ) => {
    const pointer = pointerPosition(element, clientX, clientY)
    if (!pointer) return null
    return {
      x: viewportBounds.minX + pointer.x * (viewportBounds.maxX - viewportBounds.minX),
      y: viewportBounds.minY + pointer.y * (viewportBounds.maxY - viewportBounds.minY),
    }
  }

  const updateRegion = (
    element: HTMLDivElement,
    clientX: number,
    clientY: number,
  ) => {
    const current = receiverPosition(element, clientX, clientY)
    const start = regionStartRef.current
    if (!current || !start) return
    setRegion({
      minX: Math.min(start.x, current.x),
      maxX: Math.max(start.x, current.x),
      minY: Math.min(start.y, current.y),
      maxY: Math.max(start.y, current.y),
    })
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

  const updateProfileSelection = (
    element: HTMLDivElement,
    clientX: number,
    clientY: number,
  ) => {
    const pointer = pointerPosition(element, clientX, clientY)
    if (!pointer) return
    const sample = receiverHeatmapSample(
      grid,
      layout.widthMm,
      layout.heightMm,
      viewport,
      pointer.x,
      pointer.y,
    )
    setProfileColumn(sample.column)
    setProfileDisplayRow(sample.displayRow)
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
      <div className="mb-2 flex flex-wrap items-center justify-between gap-1 text-xs text-muted-foreground">
        <div className="flex items-center gap-2">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span>좌표 기준 = Receiver Local X/Y</span>
            <span className="font-semibold text-red-500">
              −X 왼쪽 · +X 오른쪽
            </span>
            <span className="font-semibold text-emerald-500">
              +Y 위쪽 · −Y 아래쪽
            </span>
            <span>중심 0, 0</span>
            <HelpTooltip label="Receiver Heatmap 좌표 방향 도움말">
              Receiver Width는 X축, Height는 Y축이며,
              3D Viewer의 빨간 X+ 화살표와 녹색 Y+ 화살표가 Heatmap의
              오른쪽·위쪽과 각각 일치합니다. Flip receiving normal은
              수광 방향만 반전하며 Heatmap X/Y 좌표는 변경하지 않습니다.
            </HelpTooltip>
          </span>
          <div className="flex rounded-md border border-border bg-background/60 p-0.5">
            <button
              type="button"
              aria-pressed={interactionMode === 'profile'}
              className={`rounded px-2 py-0.5 ${interactionMode === 'profile' ? 'bg-primary/15 font-semibold text-primary' : ''}`}
              onClick={() => setInteractionMode('profile')}
            >
              Profile
            </button>
            <button
              type="button"
              aria-pressed={interactionMode === 'region'}
              className={`rounded px-2 py-0.5 ${interactionMode === 'region' ? 'bg-primary/15 font-semibold text-primary' : ''}`}
              onClick={() => setInteractionMode('region')}
            >
              Analyze area
            </button>
          </div>
          <div className="flex rounded-md border border-border bg-background/60 p-0.5">
            <button type="button" aria-pressed={displayMode === 'luminance'} className={`rounded px-2 py-0.5 ${displayMode === 'luminance' ? 'bg-primary/15 font-semibold text-primary' : ''}`} onClick={() => setDisplayMode('luminance')}>Luminance</button>
            <button type="button" aria-pressed={displayMode === 'error'} disabled={errorValues.length === 0} className={`rounded px-2 py-0.5 disabled:opacity-35 ${displayMode === 'error' ? 'bg-primary/15 font-semibold text-primary' : ''}`} onClick={() => setDisplayMode('error')}>Error map</button>
          </div>
          <div className="flex rounded-md border border-border bg-background/60 p-0.5">
            <button type="button" aria-pressed={colorMode === 'color'} className={`rounded px-2 py-0.5 ${colorMode === 'color' ? 'bg-primary/15 font-semibold text-primary' : ''}`} onClick={() => setColorMode('color')}>Color</button>
            <button type="button" aria-pressed={colorMode === 'mono'} className={`rounded px-2 py-0.5 ${colorMode === 'mono' ? 'bg-primary/15 font-semibold text-primary' : ''}`} onClick={() => setColorMode('mono')}>Mono</button>
          </div>
          <div className="flex items-center gap-1">
            <span className="font-semibold text-foreground">Scale</span>
            <HelpTooltip label="Heatmap / Profile display scale 설명">
              Auto는 현재 Receiver의 Peak에 맞춥니다. Compare는 선택한 Case들의 대응 Receiver 중 가장 높은 Peak를 공통 최댓값으로 사용합니다. Customize는 Heatmap과 X/Y Profile에 사용자 지정 nit 범위를 동일하게 적용합니다.
            </HelpTooltip>
            <div className="flex rounded-md border border-border bg-background/60 p-0.5">
              {(['auto', 'compare', 'customize'] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={luminanceScale.mode === mode}
                  className={`rounded px-2 py-0.5 ${luminanceScale.mode === mode ? 'bg-primary/15 font-semibold text-primary' : ''}`}
                  onClick={() => onLuminanceScaleModeChange(mode)}
                >
                  {mode === 'auto' ? 'Auto' : mode === 'compare' ? 'Compare' : 'Customize'}
                </button>
              ))}
            </div>
          </div>
          {luminanceScale.mode === 'customize' ? (
            <div className="flex items-center gap-1 rounded-md border border-border bg-background/40 px-1.5 py-0.5">
              <label className="flex items-center gap-1">
                Min
                <input
                  aria-label="Custom luminance scale minimum"
                  className="h-6 w-16 rounded border border-border bg-background px-1.5 font-mono text-foreground"
                  type="number"
                  min={0}
                  step={0.1}
                  value={customScaleMinNit}
                  onChange={(event) =>
                    onCustomScaleMinNitChange(
                      Math.max(0, numeric(event.currentTarget.value)),
                    )
                  }
                />
              </label>
              <label className="flex items-center gap-1">
                Max
                <input
                  aria-label="Custom luminance scale maximum"
                  className="h-6 w-16 rounded border border-border bg-background px-1.5 font-mono text-foreground"
                  type="number"
                  min={0.001}
                  step={0.1}
                  value={customScaleMaxNit}
                  onChange={(event) =>
                    onCustomScaleMaxNitChange(
                      Math.max(0.001, numeric(event.currentTarget.value)),
                    )
                  }
                />
              </label>
              <span>nit</span>
            </div>
          ) : null}
        </div>
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
        data-testid={`${grid.receiver_id}-luminance-scale`}
        data-scale-mode={luminanceScale.mode}
        data-scale-min-nit={luminanceScale.minNit}
        data-scale-max-nit={luminanceScale.maxNit}
        className="mb-2 flex items-center justify-end gap-2 text-xs text-muted-foreground"
      >
        <span className="font-medium text-foreground">
          {displayMode === 'error'
            ? 'Error scale'
            : `${luminanceScale.mode === 'auto' ? 'Auto' : luminanceScale.mode === 'compare' ? 'Compare' : 'Customize'} nit scale`}
        </span>
        <span className="font-mono">
          {displayMode === 'error' ? '0%' : `${formatMetric(luminanceScale.minNit)} nit`}
        </span>
        <span
          aria-hidden="true"
          className="h-2.5 w-32 rounded border border-border"
          style={{
            background: colorMode === 'mono'
              ? 'linear-gradient(to right, #000, #fff)'
              : 'linear-gradient(to right, #0814be, #1598ff, #59e36a, #ffe44d, #ff3b30)',
          }}
        />
        <span className="font-mono">
          {displayMode === 'error'
            ? `${formatMetric(errorTargetPercent * 2, 1)}%`
            : `${formatMetric(luminanceScale.maxNit)} nit`}
        </span>
      </div>
      <div
        className="mx-auto grid max-w-full grid-cols-[minmax(0,1fr)_4.5rem_minmax(11rem,14rem)] grid-rows-[auto_3.25rem_10rem] gap-x-2"
        style={{
          width: `${layout.preferredWidthPx + 292}px`,
        }}
      >
        <div
          data-testid={`${grid.receiver_id}-heatmap-frame`}
          data-width-mm={layout.widthMm}
          data-height-mm={layout.heightMm}
          className="relative col-start-1 row-start-1 min-w-0 overflow-visible"
          style={{
            aspectRatio: `${layout.widthMm} / ${layout.heightMm}`,
          }}
        >
          <div
            data-testid={`${grid.receiver_id}-heatmap-viewport`}
            title="마우스 휠로 커서 위치를 확대하고, 더블클릭하면 화면이 초기화됩니다."
            className="absolute inset-0 cursor-crosshair touch-none overflow-hidden border border-slate-300/75 bg-[#0814be]"
            onDoubleClick={resetViewport}
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture?.(event.pointerId)
              if (interactionMode === 'region') {
                regionStartRef.current = receiverPosition(
                  event.currentTarget,
                  event.clientX,
                  event.clientY,
                )
                updateRegion(event.currentTarget, event.clientX, event.clientY)
              } else {
                setProfileDragging(true)
                updateProfileSelection(event.currentTarget, event.clientX, event.clientY)
              }
            }}
            onPointerUp={(event) => {
              setProfileDragging(false)
              regionStartRef.current = null
              if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
                event.currentTarget.releasePointerCapture?.(event.pointerId)
              }
            }}
            onPointerCancel={() => setProfileDragging(false)}
            onPointerLeave={() => {
              setHover(null)
              setProfileDragging(false)
            }}
            onPointerMove={(event) => {
              updateHover(
                event.currentTarget,
                event.clientX,
                event.clientY,
              )
              if (profileDragging) {
                updateProfileSelection(event.currentTarget, event.clientX, event.clientY)
              }
              if (interactionMode === 'region' && regionStartRef.current) {
                updateRegion(event.currentTarget, event.clientX, event.clientY)
              }
            }}
            onWheel={handleWheel}
          >
            <canvas
              ref={canvasRef}
              aria-label={`${receiverLabel} Flux Heatmap`}
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
            {interactionMode === 'profile' ? <><div
              aria-hidden="true"
              className="pointer-events-none absolute inset-y-0 z-10 w-px bg-white shadow-[0_0_4px_1px_rgba(255,255,255,0.9)]"
              style={{
                left: `${(((profileColumn + 0.5) / columns - viewportBounds.minX) / (viewportBounds.maxX - viewportBounds.minX)) * 100}%`,
              }}
            />
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-x-0 z-10 h-px bg-white shadow-[0_0_4px_1px_rgba(255,255,255,0.9)]"
              style={{
                top: `${(((profileDisplayRow + 0.5) / rows - viewportBounds.minY) / (viewportBounds.maxY - viewportBounds.minY)) * 100}%`,
              }}
            /></> : null}
            {region ? (
              <div
                data-testid={`${grid.receiver_id}-analysis-region`}
                className="pointer-events-none absolute z-10 border-2 border-orange-300 bg-orange-300/20 shadow-[0_0_8px_rgba(251,146,60,0.8)]"
                style={{
                  left: `${((region.minX - viewportBounds.minX) / (viewportBounds.maxX - viewportBounds.minX)) * 100}%`,
                  top: `${((region.minY - viewportBounds.minY) / (viewportBounds.maxY - viewportBounds.minY)) * 100}%`,
                  width: `${((region.maxX - region.minX) / (viewportBounds.maxX - viewportBounds.minX)) * 100}%`,
                  height: `${((region.maxY - region.minY) / (viewportBounds.maxY - viewportBounds.minY)) * 100}%`,
                }}
              />
            ) : null}
          </div>
          {hover ? (
            <div
              role="tooltip"
              data-testid={`${grid.receiver_id}-heatmap-tooltip`}
              className="pointer-events-none absolute z-20 w-52 rounded-md border border-slate-500/70 bg-slate-950/95 p-2 text-xs text-slate-100 shadow-xl"
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
                {errorValues.length > 0 ? (
                  <>
                    <span className="text-slate-400">Pixel error</span>
                    <span className="text-right font-mono">
                      {formatMetric(errorValues[hover.displayRow * columns + hover.column], 2)}%
                    </span>
                  </>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
        <div
          data-testid={`${grid.receiver_id}-y-profile-frame`}
          className="relative col-start-3 row-start-1 min-h-0 overflow-hidden"
        >
          <div className="absolute inset-0">
            <ReceiverProfileChart
              axis="Y"
              values={yProfile}
              luminanceScale={luminanceScale}
              minimumMm={-layout.heightMm / 2}
              maximumMm={layout.heightMm / 2}
              fixedCoordinateMm={((profileColumn + 0.5) / columns - 0.5) * layout.widthMm}
            />
          </div>
        </div>
        <div
          data-testid={`${grid.receiver_id}-y-axis`}
          aria-label="Receiver Y axis in millimeters"
          className="relative col-start-2 row-start-1 text-xs tabular-nums text-muted-foreground"
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
          <span className="absolute top-1/2 right-0 -translate-y-1/2 rotate-90 whitespace-nowrap text-xs font-medium text-foreground">
            Y (mm)
          </span>
        </div>
        <div
          data-testid={`${grid.receiver_id}-x-axis`}
          aria-label="Receiver X axis in millimeters"
          className="relative col-start-1 row-start-2 text-xs tabular-nums text-muted-foreground"
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
          <span className="absolute right-0 bottom-0 left-0 text-center text-xs font-medium text-foreground">
            X (mm)
          </span>
        </div>
        <div className="col-start-1 row-start-3 min-w-0">
          <ReceiverProfileChart
            axis="X"
            values={xProfile}
            luminanceScale={luminanceScale}
            minimumMm={-layout.widthMm / 2}
            maximumMm={layout.widthMm / 2}
            fixedCoordinateMm={(0.5 - (profileDisplayRow + 0.5) / rows) * layout.heightMm}
          />
        </div>
      </div>
      {regionAnalysis ? (
        <details className="mt-3 rounded-lg border border-orange-300/45 bg-orange-50/5 p-3" open>
          <summary className="cursor-pointer text-sm font-semibold">
            Selected-area ray contribution
          </summary>
          <div className="mt-2 grid grid-cols-2 gap-1.5 md:grid-cols-4">
            <Stat label="Area" value={`${formatMetric(regionAnalysis.areaMm2)} mm²`} />
            <Stat label="Receiver Flux" value={`${formatMetric(regionAnalysis.selectedFlux)} lm`} />
            <Stat label="Flux share" value={`${formatMetric(regionAnalysis.fluxRatio * 100, 1)}%`} />
            <Stat label="Stored Paths" value={regionAnalysis.matchingPathCount.toLocaleString()} help="선택 영역에 도달한 대표 저장 경로 수입니다. 전체 Ray가 아니라 경로 원인을 확인하기 위해 저장된 진단용 표본입니다." />
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div>
              <div className="text-sm font-semibold">Path Type</div>
              <div className="mt-1 rounded-md border border-border bg-background/35 p-2 text-base">
                Direct {regionAnalysis.directCount.toLocaleString()} · Reflected{' '}
                {(regionAnalysis.matchingPathCount - regionAnalysis.directCount).toLocaleString()}
              </div>
              <div className="mt-2 text-sm font-semibold">Top Components</div>
              <div className="mt-1 space-y-1">
                {regionAnalysis.components.length > 0 ? regionAnalysis.components.map(([id, value]) => (
                  <div key={id} className="flex justify-between rounded border border-border px-2 py-1 text-base">
                    <span>{componentNames.get(id) ?? `Component ${id}`}</span>
                    <span className="font-mono">{value.count} paths · {formatMetric(value.flux)} lm</span>
                  </div>
                )) : <div className="popup-guide text-xs text-muted-foreground">No reflected stored path in this area.</div>}
              </div>
            </div>
            <div>
              <div className="text-sm font-semibold">Representative Sequences</div>
              <div className="mt-1 space-y-1">
                {regionAnalysis.sequences.length > 0 ? regionAnalysis.sequences.map(([sequence, value]) => {
                  const selected = highlightedSelection?.runId === runId &&
                    highlightedSelection.label === sequence
                  return (
                    <button
                      type="button"
                      key={sequence}
                      aria-pressed={selected}
                      className={`block w-full rounded border px-2 py-1 text-left text-base transition-colors ${selected ? 'border-orange-400 bg-orange-100 text-orange-950 dark:bg-orange-950/45 dark:text-orange-100' : 'border-border hover:border-orange-300 hover:bg-orange-50/60 dark:hover:bg-orange-950/20'}`}
                      onClick={() => actions.setHighlightedRayPathSelection(
                        selected
                          ? null
                          : {
                              runId,
                              pathIndices: value.pathIndices,
                              label: sequence,
                            },
                      )}
                    >
                      <div className="truncate font-medium" title={sequence}>{sequence}</div>
                      <div className="mt-0.5 font-mono text-muted-foreground">{value.count} paths · {formatMetric(value.flux)} lm</div>
                    </button>
                  )
                }) : <div className="popup-guide text-xs text-muted-foreground">Enable Store ray paths and rerun to diagnose this area.</div>}
              </div>
            </div>
          </div>
          <details className="mt-2 rounded-md border border-border bg-background/25 p-2">
            <summary className="cursor-pointer text-sm font-semibold">Detailed face, reflection type and bounce contribution</summary>
            <div className="mt-2 grid gap-2 md:grid-cols-3">
              <div>
                <div className="text-sm font-semibold text-muted-foreground">Reflection Type</div>
                {regionAnalysis.lobes.map(([name, value]) => <div key={name} className="flex justify-between text-base"><span className="capitalize">{name}</span><span>{value.count} · {formatMetric(value.flux)} lm</span></div>)}
              </div>
              <div>
                <div className="text-sm font-semibold text-muted-foreground">Reflection Count</div>
                {regionAnalysis.depths.map(([depth, value]) => <div key={depth} className="flex justify-between text-base"><span>{depth} Bounce</span><span>{value.count} · {formatMetric(value.flux)} lm</span></div>)}
              </div>
              <div>
                <div className="text-sm font-semibold text-muted-foreground">Top CAD Faces</div>
                {regionAnalysis.faces.map(([face, value]) => <div key={face} className="flex justify-between text-base"><span>Face {face}</span><span>{value.count} · {formatMetric(value.flux)} lm</span></div>)}
              </div>
            </div>
          </details>
          <p className="popup-guide mt-2 text-xs leading-4 text-muted-foreground">
            선택 영역의 면적과 Flux는 전체 Receiver Grid를 기준으로 계산합니다. Component와 경로 순서는 Stored paths만 사용하며 반사 경로의 원인을 확인하기 위한 진단값입니다.
          </p>
        </details>
      ) : interactionMode === 'region' ? (
        <p className="mt-2 rounded-md border border-dashed border-orange-300/50 p-2 text-center text-xs text-muted-foreground">
          Heatmap에서 빛샘을 확인할 영역을 사각형으로 드래그해 주세요.
        </p>
      ) : null}
    </div>
  )
}

function ReceiverProfileChart({
  axis,
  values,
  luminanceScale,
  minimumMm,
  maximumMm,
  fixedCoordinateMm,
}: {
  axis: 'X' | 'Y'
  values: number[]
  luminanceScale: LuminanceDisplayScale
  minimumMm: number
  maximumMm: number
  fixedCoordinateMm: number
}) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)
  const peak = Math.max(...values, 0)
  const scaleSpan = Math.max(
    luminanceScale.maxNit - luminanceScale.minNit,
    1e-12,
  )
  const points = values
    .map((value, index) => {
      const position = values.length <= 1 ? 0 : (index / (values.length - 1)) * 100
      const normalizedValue = Math.min(1, Math.max(
        0,
        (value - luminanceScale.minNit) / scaleSpan,
      ))
      const x = axis === 'X' ? position : normalizedValue * 38
      const y = axis === 'X' ? 38 - normalizedValue * 34 : 100 - position
      return `${x},${y}`
    })
    .join(' ')
  return (
    <div className={`h-full rounded-lg border border-border bg-muted/15 p-2 ${axis === 'Y' ? 'flex min-h-0 flex-col' : ''}`}>
      <div className={`mb-1 gap-1 text-sm ${axis === 'X' ? 'flex items-center justify-between' : 'space-y-0.5'}`}>
        <span className="font-semibold">{axis}축 휘도 프로파일</span>
        <span className="font-mono text-muted-foreground">
          {axis === 'X' ? 'Y' : 'X'}={formatReceiverCoordinate(fixedCoordinateMm)} mm · Peak {formatMetric(peak)} nit · Scale {formatMetric(luminanceScale.minNit)}–{formatMetric(luminanceScale.maxNit)} nit
        </span>
      </div>
      <div className={`relative ${axis === 'X' ? 'h-20 w-full' : 'min-h-0 w-full flex-1'}`}>
        <svg
          role="img"
          aria-label={`${axis}-axis luminance profile`}
          viewBox={axis === 'X' ? '0 0 100 42' : '0 0 42 100'}
          preserveAspectRatio="none"
          className="h-full w-full overflow-visible"
          onPointerLeave={() => setHoverIndex(null)}
          onPointerMove={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect()
            const ratio = axis === 'X'
              ? (event.clientX - bounds.left) / Math.max(bounds.width, 1)
              : 1 - (event.clientY - bounds.top) / Math.max(bounds.height, 1)
            setHoverIndex(Math.max(0, Math.min(values.length - 1, Math.round(ratio * (values.length - 1)))))
          }}
        >
          <path d={axis === 'X' ? 'M0 38 H100 M0 4 V38' : 'M0 0 V100 M0 100 H38'} fill="none" className="stroke-border" strokeWidth="0.5" />
          <polyline points={points} fill="none" className="stroke-primary" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        </svg>
        {hoverIndex !== null && values[hoverIndex] !== undefined ? (
          <div
            role="tooltip"
            className="pointer-events-none absolute z-10 whitespace-nowrap rounded border border-slate-500/70 bg-slate-950/95 px-2 py-1 text-xs text-slate-100 shadow-lg"
            style={axis === 'X'
              ? { left: `${values.length <= 1 ? 0 : hoverIndex / (values.length - 1) * 100}%`, top: 4, transform: 'translateX(-50%)' }
              : { left: 4, top: `${values.length <= 1 ? 100 : (1 - hoverIndex / (values.length - 1)) * 100}%`, transform: 'translateY(-50%)' }}
          >
            {formatReceiverCoordinate(minimumMm + (maximumMm - minimumMm) * (values.length <= 1 ? 0 : hoverIndex / (values.length - 1)))} mm · {formatMetric(values[hoverIndex])} nit
          </div>
        ) : null}
      </div>
      {axis === 'X' ? (
        <div className="flex justify-between font-mono text-xs text-muted-foreground">
          <span>{formatReceiverCoordinate(minimumMm)} mm</span>
          <span>{formatReceiverCoordinate(maximumMm)} mm</span>
        </div>
      ) : (
        <div className="flex justify-between font-mono text-xs text-muted-foreground">
          <span>{formatMetric(luminanceScale.minNit)}</span><span>{formatMetric(luminanceScale.maxNit)} nit</span>
        </div>
      )}
    </div>
  )
}

function Stat({
  label,
  value,
  help,
  className = '',
}: {
  label: string
  value: string
  help?: string
  className?: string
}) {
  return (
    <div className={`rounded-lg border border-border bg-background/45 p-2.5 ${className}`}>
      <div className="flex items-center gap-1 text-sm text-muted-foreground">
        {label}
        {help ? (
          <HelpTooltip label={`${label} 설명`}>{help}</HelpTooltip>
        ) : null}
      </div>
      <div className="mt-1 text-base font-semibold">{value}</div>
    </div>
  )
}

export function RayTraceResultWindow({
  open,
  result: liveResult,
  scene,
  componentNameOverrides = {},
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
  const [receiverCompareScope, setReceiverCompareScope] =
    useState<ReceiverCompareScope>('all')
  const [reportCaseId, setReportCaseId] = useState<string | null>(null)
  const [errorTargetPercent, setErrorTargetPercent] = useState(5)
  const [luminanceScaleMode, setLuminanceScaleMode] =
    useState<LuminanceScaleMode>('auto')
  const [customScaleMinNit, setCustomScaleMinNit] = useState(0)
  const [customScaleMaxNit, setCustomScaleMaxNit] = useState(10)
  const caseFileInputRef = useRef<HTMLInputElement>(null)
  const [frame, setFrame] = useState<WindowFrame>({
    x: 24,
    y: 58,
    width: 1120,
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
  useEffect(() => {
    if (result?.config.convergence_target_percent) {
      setErrorTargetPercent(result.config.convergence_target_percent)
    }
  }, [result?.config.convergence_target_percent, result?.run_id])
  if (!open || !result) return null
  const componentNames = new Map(
    (scene?.components ?? []).map((component) => [
      component.component_id,
      getComponentDisplayName(component, componentNameOverrides),
    ]),
  )

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
  const convergenceHistory = Array.isArray(result.metrics._convergence_history)
    ? result.metrics._convergence_history as Record<string, unknown>[]
    : []
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
  const receiverCompareOptions = (
    baselineCase?.result ?? result
  ).receivers.filter((item) => item.enabled)

  const exportCases = async () => {
    const cases = selectedCases.length > 0 ? selectedCases : analysisCases
    if (cases.length === 0) return
    const payload: AnalysisCaseFile = {
      format: 'tv-leakage-analysis-cases',
      schema_version: 'analysis-cases.v1',
      saved_at: new Date().toISOString(),
      baseline_case_id: baselineCase?.case_id ?? null,
      cases,
    }
    const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], {
      type: 'application/json',
    })
    const fileName = `ray-analysis-${new Date().toISOString().slice(0, 10)}.bitsam-report`
    const picker = (window as AnalysisReportSaveFilePickerWindow)
      .showSaveFilePicker
    if (picker) {
      try {
        const handle = await picker({
          suggestedName: fileName,
          types: [
            {
              description: 'BITSAM analysis report',
              accept: {
                'application/json': ['.bitsam-report'],
              },
            },
          ],
        })
        const writable = await handle.createWritable()
        await writable.write(blob)
        await writable.close()
        return
      } catch (error) {
        if (
          error instanceof DOMException &&
          error.name === 'AbortError'
        ) return
        window.alert(
          error instanceof Error
            ? `보고서 저장에 실패했습니다: ${error.message}`
            : '보고서 저장에 실패했습니다.',
        )
        return
      }
    }

    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = fileName
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
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
      className="simulator-popup-typography fixed z-50 flex overflow-hidden rounded-xl border border-border bg-background/96 shadow-2xl shadow-black/55 backdrop-blur-xl"
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
            <div className="truncate text-base font-semibold">
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
                className="h-7 max-w-52 cursor-pointer rounded-md border border-border bg-background px-2 text-sm"
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
              className="text-base"
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
                  <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    Receiver
                    <select
                      aria-label="Compare Receiver"
                      className="h-7 max-w-48 rounded-md border border-border bg-background px-2 text-xs text-foreground"
                      value={receiverCompareScope}
                      onChange={(event) =>
                        setReceiverCompareScope(
                          event.currentTarget.value === 'all'
                            ? 'all'
                            : Number(event.currentTarget.value),
                        )
                      }
                    >
                      <option value="all">All Receivers</option>
                      {receiverCompareOptions.map((receiver, index) => (
                        <option key={receiver.receiver_id} value={index}>
                          {rayObjectDisplayName(
                            'receiver',
                            receiver.receiver_id,
                            receiver.display_name,
                          )}
                        </option>
                      ))}
                    </select>
                  </label>
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
                  <table className="w-full min-w-[1020px] border-collapse text-base">
                    <thead className="bg-muted/45 text-left text-sm">
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
                        <th className="p-2 text-right">Hit Ratio</th>
                        <th className="p-2">
                          <span className="flex items-center justify-end gap-1">
                            Total Flux
                            <HelpTooltip label="Total flux 설명">
                              모든 Receiver에 도달한 전체 광량(lm)입니다. 값이 작을수록 유입된 빛샘 에너지가 적습니다.
                            </HelpTooltip>
                          </span>
                        </th>
                        <th className="p-2">
                          <span className="flex items-center justify-end gap-1">
                            Peak Nit
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
                        const scopedHits = caseReceiverHits(
                          item.result,
                          receiverCompareScope,
                        )
                        const itemHitRatio = item.result.total_rays > 0
                          ? scopedHits / item.result.total_rays
                          : 0
                        const flux = caseFlux(item.result, receiverCompareScope)
                        const luminance = caseLuminance(
                          item.result,
                          receiverCompareScope,
                        )
                        const score = baselineCase
                          ? leakageImprovementScore(
                              item.result,
                              baselineCase.result,
                              receiverCompareScope,
                            )
                          : null
                        const conditionMismatches = baselineCase
                          ? comparisonConditionMismatches(
                              item.result,
                              baselineCase.result,
                              receiverCompareScope,
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
                              <div className="truncate text-xs text-muted-foreground" title={item.cad_name}>{item.cad_name}</div>
                              <input
                                aria-label={`Case condition ${item.case_id}`}
                                className="mt-1 h-6 w-full min-w-0 rounded border border-transparent bg-transparent px-1 text-xs text-muted-foreground hover:border-border focus:border-primary focus:bg-background"
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
              <div className="popup-guide rounded-lg border border-border bg-muted/20 p-2.5 text-xs leading-5 text-muted-foreground">
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
                  label="Total Rays"
                  value={result.total_rays.toLocaleString()}
                  help="이번 해석에서 실제로 추적한 전체 Ray 개수입니다. Case 비교 시 동일해야 하는 기본 샘플 수입니다."
                />
                <Stat
                  label="Receiver Hits"
                  value={result.receiver_hit_count.toLocaleString()}
                  help="하나 이상의 Receiver에 도달한 Ray 개수입니다. 동일 Ray 수 조건에서 많을수록 Receiver 방향으로 빛이 더 많이 전달된 것입니다."
                />
                <Stat
                  label="Hit Ratio"
                  value={`${(hitRatio * 100).toFixed(3)}%`}
                  help="전체 Ray 중 Receiver에 도달한 비율입니다. 도달 빈도를 뜻하며 Ray마다 가진 광량 차이는 반영하지 않습니다."
                />
                <Stat
                  label="Surface Interactions"
                  value={result.surface_hit_count.toLocaleString()}
                  help="Ray가 추적 가능한 CAD 표면과 충돌한 누적 횟수입니다. 하나의 Ray가 여러 번 반사되면 여러 번 집계됩니다."
                />
                <Stat
                  label="Direct Flux"
                  value={`${formatMetric(
                    contribution.direct_receiver_flux_lumen,
                  )} lm`}
                  help="CAD 표면에서 반사되지 않고 Emitter에서 Receiver로 직접 도달한 전체 광량입니다."
                />
                <Stat
                  label="Reflected Flux"
                  value={`${formatMetric(
                    contribution.reflected_receiver_flux_lumen,
                  )} lm`}
                  help="한 번 이상 CAD 표면과 상호작용한 뒤 Receiver에 도달한 전체 광량입니다. 구조적 반사 경로의 영향을 나타냅니다."
                />
                <Stat
                  label="Ray Rate"
                  value={`${Math.round(
                    numeric(performance.rays_per_sec),
                  ).toLocaleString()} /s`}
                  help="초당 처리한 Ray 개수로, 해석 성능 확인용 값입니다. 빛샘 품질을 평가하는 광학 결과값은 아닙니다."
                />
                <Stat
                  label="Stored Paths"
                  value={result.stored_paths.length.toLocaleString()}
                  help="3D 경로 및 Section View 확인을 위해 저장된 대표 Ray 경로 수입니다. 추적된 모든 Ray 수와 같지 않을 수 있습니다."
                />
              </div>
              <p className="popup-guide flex items-center gap-1 rounded-lg border border-border bg-muted/20 p-3 text-xs leading-5 text-muted-foreground">
                Intersection backend
                <HelpTooltip label="Intersection backend 설명">
                  Ray와 CAD Mesh의 충돌을 검색한 계산 방식입니다. Cache Hit는 동일 형상 조건의 BVH를 재사용했음을 뜻하며, Rebuilt는 형상 변경으로 새로 생성했음을 뜻합니다.
                </HelpTooltip>
                {' · '}
                {String(
                  performance.intersection_backend ??
                    result.config.intersection_backend,
                ).toUpperCase()}
                {' · '}BVH build{' '}
                {formatMetric(performance.bvh_build_sec)} s
                {' · '}
                {performance.bvh_cache_hit ? 'Cache Hit' : 'Rebuilt'}
              </p>
              {RAY_SECTION_VIEW_ENABLED && scene ? (
                <div className="space-y-2">
                  <div className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
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
                  label="Surface Hits"
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
                  <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-3 border-b border-border bg-muted/25 px-3 py-2 text-sm text-muted-foreground">
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
                      className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-3 border-b border-border px-3 py-2 text-base last:border-b-0"
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
                <div className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-border bg-muted/25 px-3 py-2 text-sm text-muted-foreground">
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
                        className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-border px-3 py-2 text-base capitalize last:border-b-0"
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
              {convergenceHistory.length > 0 ? (
                <details className="rounded-lg border border-border bg-background/40 p-3">
                  <summary className="cursor-pointer text-sm font-semibold">Ray convergence history</summary>
                  <div className="mt-2 overflow-x-auto">
                    <div className="grid min-w-[520px] grid-cols-5 gap-2 text-base">
                      {['Rays', 'Total Error', 'Peak-area Error', 'Peak nit', 'Flux'].map((label) => <span key={label} className="font-semibold text-muted-foreground">{label}</span>)}
                      {convergenceHistory.flatMap((item, index) => [
                        <span key={`${index}-r`} className="font-mono">{Math.round(numeric(item.rays)).toLocaleString()}</span>,
                        <span key={`${index}-e`} className="font-mono">{formatMetric(item.totalError, 2)}%</span>,
                        <span key={`${index}-p`} className="font-mono">{formatMetric(item.peakError, 2)}%</span>,
                        <span key={`${index}-n`} className="font-mono">{formatMetric(item.peakNit)}</span>,
                        <span key={`${index}-f`} className="font-mono">{formatMetric(item.flux)} lm</span>,
                      ])}
                    </div>
                  </div>
                </details>
              ) : null}
              {receiversInDisplayOrder(result.receivers).map((receiver, receiverIndex) => {
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
                const currentPeakNit = numeric(values.peak_nit_est)
                const comparePeakNit = Math.max(
                  currentPeakNit,
                  ...selectedCases.map((item) =>
                    correspondingReceiverPeakNit(
                      item.result,
                      receiver.receiver_id,
                      receiverIndex,
                    ),
                  ),
                )
                const safeCustomMinNit = Math.max(0, customScaleMinNit)
                const safeCustomMaxNit = Math.max(
                  safeCustomMinNit + 1e-6,
                  customScaleMaxNit,
                )
                const luminanceScale: LuminanceDisplayScale =
                  luminanceScaleMode === 'customize'
                    ? {
                        mode: luminanceScaleMode,
                        minNit: safeCustomMinNit,
                        maxNit: safeCustomMaxNit,
                      }
                    : luminanceScaleMode === 'compare'
                      ? {
                          mode: luminanceScaleMode,
                          minNit: 0,
                          maxNit: Math.max(comparePeakNit, 1e-6),
                        }
                      : {
                          mode: luminanceScaleMode,
                          minNit: 0,
                          maxNit: Math.max(currentPeakNit, 1e-6),
                        }
                const totalError = typeof values.error_estimate_percent === 'number'
                  ? numeric(values.error_estimate_percent) : null
                const peakAreaError = typeof values.peak_area_error_estimate_percent === 'number'
                  ? numeric(values.peak_area_error_estimate_percent) : null
                const receiverHits = numeric(values.hit_count)
                const convergence = receiverHits < 30 || totalError === null || peakAreaError === null
                  ? { label: 'Insufficient samples', tone: 'border-amber-300/50 bg-amber-100/10 text-amber-700 dark:text-amber-300' }
                  : totalError <= errorTargetPercent && peakAreaError <= errorTargetPercent
                    ? { label: 'Converged', tone: 'border-emerald-400/45 bg-emerald-100/10 text-emerald-700 dark:text-emerald-300' }
                    : totalError <= errorTargetPercent * 2 && peakAreaError <= errorTargetPercent * 2
                      ? { label: 'Nearly converged', tone: 'border-sky-400/45 bg-sky-100/10 text-sky-700 dark:text-sky-300' }
                      : { label: 'Not converged', tone: 'border-rose-400/45 bg-rose-100/10 text-rose-700 dark:text-rose-300' }
                return (
                  <section
                    key={receiver.receiver_id}
                    className="rounded-lg border border-border bg-background/40 p-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="text-sm font-semibold">
                        {rayObjectDisplayName(
                          'receiver',
                          receiver.receiver_id,
                          receiver.display_name,
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <span className={`rounded-full border px-2 py-0.5 text-sm font-semibold ${convergence.tone}`}>{convergence.label}</span>
                        <label className="flex items-center gap-1 text-xs text-muted-foreground">
                          Target
                          <input aria-label="Convergence target percent" className="h-7 w-16 rounded border border-border bg-background px-1.5 font-mono text-foreground" type="number" min={0.1} max={100} step={0.5} value={errorTargetPercent} onChange={(event) => setErrorTargetPercent(Math.max(0.1, numeric(event.currentTarget.value)))} />%
                        </label>
                      </div>
                    </div>
                    <div className="mt-2 grid grid-cols-6 gap-1.5">
                      <Stat
                        className="order-1 col-span-3"
                        label="Peak-area Error"
                        value={peakAreaError === null || receiverHits <= 0 ? '—' : `${formatMetric(peakAreaError, 2)}%`}
                        help="Receiver Peak의 5% 이상인 셀 영역에 대한 Monte Carlo 상대 오차입니다. Total Flux Error와 Peak-area Error가 모두 목표 오차 이하일 때 Converged로 판단합니다."
                      />
                      <Stat
                        className="order-3 col-span-2"
                        label="Peak Nit"
                        value={formatMetric(values.peak_nit_est)}
                        help="이 Receiver Heatmap에서 가장 밝은 셀의 추정 휘도입니다. 국부적으로 가장 강한 빛샘 세기를 나타냅니다."
                      />
                      <Stat
                        className="order-4 col-span-2"
                        label="Mean Nit"
                        value={formatMetric(values.mean_nit_est)}
                        help="이 Receiver 전체 Heatmap 셀의 평균 추정 휘도입니다. 밝은 영역뿐 아니라 빛이 없는 셀도 포함합니다."
                      />
                      <Stat
                        className="order-5 col-span-2"
                        label="Flux"
                        value={`${formatMetric(
                          values.total_flux_lumen,
                        )} lm`}
                        help="이 Receiver에 도달한 전체 광량입니다. 밝기 세기와 영역을 종합한 에너지 값이며 Peak nit와 의미가 다릅니다."
                      />
                      <Stat
                        className="order-2 col-span-3"
                        label="Error Estimate"
                        value={
                          typeof values.error_estimate_percent === 'number' &&
                          numeric(values.total_flux_lumen) > 0
                            ? `${formatMetric(values.error_estimate_percent, 2)}%`
                            : '—'
                        }
                        help="Receiver 전체 Flux 추정값에 대한 Monte Carlo 1σ 상대 표준오차입니다. 값이 낮을수록 통계적으로 잘 수렴한 결과입니다. CAD 형상, 재질 물성 및 물리 모델 자체의 오차는 포함하지 않습니다."
                      />
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-1.5">
                      <div className="rounded-lg border border-border bg-muted/20 p-2">
                        <div className="flex items-center gap-1 text-sm text-muted-foreground">
                          광영역 @1%
                          <HelpTooltip label="광영역 1% 설명">
                            이 Receiver의 Peak nit 중 1% 이상인 Heatmap 셀 면적입니다. 사람 눈에 희미하게 보일 수 있는 약한 확산 영역까지 넓게 확인하는 참고값입니다.
                          </HelpTooltip>
                        </div>
                        <div className="mt-1 font-mono text-base font-semibold">
                          {formatMetric(lightAreas[1])} mm²
                        </div>
                      </div>
                      <div className="rounded-lg border border-primary/25 bg-primary/5 p-2">
                        <div className="flex items-center gap-1 text-sm text-muted-foreground">
                          광영역 @5%
                          <HelpTooltip label="Receiver 광영역 5% 설명">
                            이 Receiver의 Peak nit 중 5% 이상인 Heatmap 셀 면적입니다. Compare 점수에서 대표 광영역으로 사용하는 기준입니다.
                          </HelpTooltip>
                        </div>
                        <div className="mt-1 font-mono text-base font-semibold">
                          {formatMetric(lightAreas[5])} mm²
                        </div>
                      </div>
                      <div className="rounded-lg border border-border bg-muted/20 p-2">
                        <div className="flex items-center gap-1 text-sm text-muted-foreground">
                          광영역 @10%
                          <HelpTooltip label="광영역 10% 설명">
                            이 Receiver의 Peak nit 중 10% 이상인 Heatmap 셀 면적입니다. 비교적 강하고 선명한 빛샘 중심 영역을 나타냅니다.
                          </HelpTooltip>
                        </div>
                        <div className="mt-1 font-mono text-base font-semibold">
                          {formatMetric(lightAreas[10])} mm²
                        </div>
                      </div>
                    </div>
                    {grid ? (
                      <div className="mt-3">
                        <div className="mb-2 flex items-center gap-1 text-sm font-semibold text-muted-foreground">
                          Receiver Heatmap
                          <HelpTooltip label="Receiver Heatmap 설명">
                            Receiver의 로컬 X/Y 좌표별 입사 광량과 추정 휘도 분포입니다. 색상은 현재 Heatmap 내 상대적인 세기를 나타내며, 마우스를 올리면 셀 위치·광량·조도·휘도를 확인할 수 있습니다.
                          </HelpTooltip>
                        </div>
                        <ReceiverHeatmap
                          grid={grid}
                          receiver={receiver}
                          luminanceScale={luminanceScale}
                          onLuminanceScaleModeChange={setLuminanceScaleMode}
                          customScaleMinNit={customScaleMinNit}
                          customScaleMaxNit={customScaleMaxNit}
                          onCustomScaleMinNitChange={setCustomScaleMinNit}
                          onCustomScaleMaxNitChange={setCustomScaleMaxNit}
                          kAbs={result.config.k_abs}
                          kBrdf={result.config.k_brdf}
                          storedPaths={result.stored_paths}
                          runId={result.run_id}
                          componentNames={componentNames}
                          errorTargetPercent={errorTargetPercent}
                          sampleCount={Math.max(
                            0,
                            numeric(values.error_estimate_sample_count) ||
                              result.total_rays,
                          )}
                          faceSourceIds={scene?.mesh.face_source_ids}
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
