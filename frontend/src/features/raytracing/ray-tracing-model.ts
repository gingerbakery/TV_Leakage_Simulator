import type {
  EmitterSpec,
  OpticalAssignment,
  OpticalProfile,
  RayTraceConfigRequest,
  RayTraceRequest,
  RayTraceResult,
  ReceiverGrid,
  ReceiverSpec,
  ScenePayload,
  Vec3,
} from '@/api'
import {
  compileOpticalProfile,
} from '@/features/materials'
import type {
  ComponentTransformRule,
  MaterialAssignment,
  RoiScope,
} from '@/stores'

export interface ViewerCameraFrame {
  target: Vec3
  normal: Vec3
  uAxis: Vec3
  vAxis: Vec3
}

export interface RayTraceRequestSource {
  scene: ScenePayload
  projectName: string
  emitters: EmitterSpec[]
  receivers: ReceiverSpec[]
  materialAssignments: MaterialAssignment[]
  transformRules: ComponentTransformRule[]
  excludedComponentIds: number[]
  deletedComponentIds: number[]
  roiScopes: RoiScope[]
  config: RayTraceConfigRequest
}

const convergenceAccumulationContract =
  'independent_segment_weighted_v1'

interface ConvergenceAccumulationEvidence {
  contract: typeof convergenceAccumulationContract
  segment_count: number
  segment_rays: number[]
  segment_seeds: number[]
  segment_emitter_seeds: Record<string, (number | null)[]>
  segment_compute_states: string[]
  total_rays: number
  avoided_retrace_rays: number
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function finiteNumber(value: unknown, fallback = 0): number {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : fallback
}

function convergenceEvidence(result: RayTraceResult): ConvergenceAccumulationEvidence {
  const stored = result.metrics._convergence_accumulation
  if (isRecord(stored) && stored.contract === convergenceAccumulationContract) {
    const segmentRays = Array.isArray(stored.segment_rays)
      ? stored.segment_rays.map((value) => Math.max(0, Math.trunc(finiteNumber(value))))
      : [result.total_rays]
    const segmentSeeds = Array.isArray(stored.segment_seeds)
      ? stored.segment_seeds.map((value) => Math.trunc(finiteNumber(value)))
      : [result.config.seed]
    const segmentComputeStates = Array.isArray(stored.segment_compute_states)
      ? stored.segment_compute_states.map(String)
      : [String(result.metrics._performance_summary && isRecord(result.metrics._performance_summary)
        ? result.metrics._performance_summary.compute_execution_state ?? 'unknown'
        : 'unknown')]
    const storedEmitterSeeds = isRecord(stored.segment_emitter_seeds)
      ? stored.segment_emitter_seeds
      : {}
    const segmentEmitterSeeds = Object.fromEntries(
      Object.entries(storedEmitterSeeds).map(([emitterId, values]) => [
        emitterId,
        Array.isArray(values)
          ? values.map((value) => value === null ? null : Math.trunc(finiteNumber(value)))
          : [],
      ]),
    )
    return {
      contract: convergenceAccumulationContract,
      segment_count: segmentRays.length,
      segment_rays: segmentRays,
      segment_seeds: segmentSeeds,
      segment_emitter_seeds: segmentEmitterSeeds,
      segment_compute_states: segmentComputeStates,
      total_rays: segmentRays.reduce((sum, value) => sum + value, 0),
      avoided_retrace_rays: Math.max(
        0,
        Math.trunc(finiteNumber(stored.avoided_retrace_rays)),
      ),
    }
  }
  const performance = result.metrics._performance_summary
  return {
    contract: convergenceAccumulationContract,
    segment_count: 1,
    segment_rays: [result.total_rays],
    segment_seeds: [result.config.seed],
    segment_emitter_seeds: Object.fromEntries(
      result.emitters.map((emitter) => [emitter.emitter_id, [emitter.seed]]),
    ),
    segment_compute_states: [
      isRecord(performance)
        ? String(performance.compute_execution_state ?? 'unknown')
        : 'unknown',
    ],
    total_rays: result.total_rays,
    avoided_retrace_rays: 0,
  }
}

function withAccumulationEvidence(result: RayTraceResult): RayTraceResult {
  const next = structuredClone(result)
  next.metrics._convergence_accumulation = convergenceEvidence(next)
  return next
}

function mergeWeightedNumber(
  key: string,
  previous: number,
  current: number,
  previousRays: number,
  currentRays: number,
): number {
  if (key.endsWith('count') || key.endsWith('_count')) {
    return previous + current
  }
  if (key.includes('flux_lumen') || key.endsWith('_lumen')) {
    return (
      previous * previousRays + current * currentRays
    ) / (previousRays + currentRays)
  }
  if (key.startsWith('max_')) return Math.max(previous, current)
  return current
}

function mergeContributionRecord(
  previous: Record<string, unknown> | undefined,
  current: Record<string, unknown> | undefined,
  previousRays: number,
  currentRays: number,
): Record<string, unknown> {
  const merged: Record<string, unknown> = {}
  const keys = new Set([
    ...Object.keys(previous ?? {}),
    ...Object.keys(current ?? {}),
  ])
  for (const key of keys) {
    const previousValue = previous?.[key]
    const currentValue = current?.[key]
    if (isRecord(previousValue) || isRecord(currentValue)) {
      merged[key] = mergeContributionRecord(
        isRecord(previousValue) ? previousValue : undefined,
        isRecord(currentValue) ? currentValue : undefined,
        previousRays,
        currentRays,
      )
      continue
    }
    if (typeof previousValue === 'number' || typeof currentValue === 'number') {
      merged[key] = mergeWeightedNumber(
        key,
        finiteNumber(previousValue),
        finiteNumber(currentValue),
        previousRays,
        currentRays,
      )
      continue
    }
    merged[key] = structuredClone(currentValue ?? previousValue)
  }
  return merged
}

function assertCompatibleReceiverGrid(
  previous: ReceiverGrid,
  current: ReceiverGrid,
): void {
  if (
    previous.resolution[0] !== current.resolution[0] ||
    previous.resolution[1] !== current.resolution[1] ||
    Math.abs(previous.bin_area_mm2 - current.bin_area_mm2) > 1e-12
  ) {
    throw new Error(
      `Receiver grid changed during convergence: ${current.receiver_id}`,
    )
  }
}

function mergeReceiverGrid(
  previous: ReceiverGrid,
  current: ReceiverGrid,
  previousRays: number,
  currentRays: number,
): ReceiverGrid {
  assertCompatibleReceiverGrid(previous, current)
  const totalRays = previousRays + currentRays
  const previousScale = previousRays / totalRays
  const currentScale = currentRays / totalRays
  const rows = current.resolution[1]
  const columns = current.resolution[0]
  const previousSquared = previous.flux_squared_lumen2_grid ?? []
  const currentSquared = current.flux_squared_lumen2_grid ?? []
  return {
    ...structuredClone(current),
    flux_lumen: Array.from({ length: rows }, (_, row) =>
      Array.from({ length: columns }, (_, column) =>
        finiteNumber(previous.flux_lumen[row]?.[column]) * previousScale +
        finiteNumber(current.flux_lumen[row]?.[column]) * currentScale,
      ),
    ),
    hit_count: previous.hit_count + current.hit_count,
    flux_squared_lumen2:
      finiteNumber(previous.flux_squared_lumen2) * previousScale ** 2 +
      finiteNumber(current.flux_squared_lumen2) * currentScale ** 2,
    flux_squared_lumen2_grid: Array.from({ length: rows }, (_, row) =>
      Array.from({ length: columns }, (_, column) =>
        finiteNumber(previousSquared[row]?.[column]) * previousScale ** 2 +
        finiteNumber(currentSquared[row]?.[column]) * currentScale ** 2,
      ),
    ),
  }
}

function receiverMetrics(
  grid: ReceiverGrid,
  totalRays: number,
  kAbs: number,
  kBrdf: number,
): Record<string, unknown> {
  const values = grid.flux_lumen.flat()
  const binAreaM2 = Math.max(grid.bin_area_mm2 * 1e-6, 1e-18)
  const nits = values.map((flux) => kAbs * kBrdf * flux / binAreaM2 / Math.PI)
  const sortedNits = [...nits].sort((left, right) => left - right)
  const totalFlux = values.reduce((sum, value) => sum + value, 0)
  const relativeErrorPercent = (flux: number, squared: number) => {
    if (totalRays <= 1 || flux <= 0) return 100
    const relativeVariance = Math.max(
      0,
      (totalRays * squared / (flux * flux) - 1) / (totalRays - 1),
    )
    return Math.sqrt(relativeVariance) * 100
  }
  const peakThreshold = Math.max(...values, 0) * 0.05
  let peakAreaFlux = 0
  let peakAreaSquaredFlux = 0
  for (let row = 0; row < grid.resolution[1]; row += 1) {
    for (let column = 0; column < grid.resolution[0]; column += 1) {
      const flux = finiteNumber(grid.flux_lumen[row]?.[column])
      if (flux >= peakThreshold && flux > 0) {
        peakAreaFlux += flux
        peakAreaSquaredFlux += finiteNumber(
          grid.flux_squared_lumen2_grid?.[row]?.[column],
        )
      }
    }
  }
  const minimumConvergenceHits = 30
  const heatmapBinCount = values.length
  const hitsPerBin = heatmapBinCount > 0 ? grid.hit_count / heatmapBinCount : 0
  const recommendedHitCount = Math.ceil(heatmapBinCount * 5)
  const estimatedRays = (targetHits: number) =>
    totalRays > 0 && grid.hit_count > 0
      ? Math.ceil(totalRays * targetHits / grid.hit_count)
      : null
  const heatmapQuality = grid.hit_count <= 0
    ? 'no_hits'
    : hitsPerBin < 1
      ? 'sparse'
      : hitsPerBin < 5
        ? 'noisy'
        : hitsPerBin < 20
          ? 'usable'
          : 'stable'
  return {
    peak_nit_est: Math.max(...nits, 0),
    mean_nit_est: nits.length > 0
      ? nits.reduce((sum, value) => sum + value, 0) / nits.length
      : 0,
    p95_nit_est: sortedNits.length > 0
      ? sortedNits[Math.min(
          sortedNits.length - 1,
          Math.ceil(sortedNits.length * 0.95) - 1,
        )]
      : 0,
    total_flux_lumen: totalFlux,
    hit_count: grid.hit_count,
    area_above_zero_mm2:
      values.filter((value) => value > 0).length * grid.bin_area_mm2,
    error_estimate_percent: relativeErrorPercent(
      totalFlux,
      finiteNumber(grid.flux_squared_lumen2),
    ),
    peak_area_error_estimate_percent: relativeErrorPercent(
      peakAreaFlux,
      peakAreaSquaredFlux,
    ),
    error_estimate_sample_count: totalRays,
    receiver_hit_rate: totalRays > 0 ? grid.hit_count / totalRays : 0,
    minimum_convergence_hits: minimumConvergenceHits,
    estimated_rays_for_minimum_hits: estimatedRays(minimumConvergenceHits),
    statistical_quality: grid.hit_count <= 0
      ? 'no_hits'
      : grid.hit_count < minimumConvergenceHits
        ? 'insufficient_hits'
        : 'estimated',
    heatmap_bin_count: heatmapBinCount,
    heatmap_hits_per_bin: hitsPerBin,
    minimum_usable_heatmap_hits_per_bin: 5,
    recommended_heatmap_hit_count: recommendedHitCount,
    estimated_rays_for_usable_heatmap: estimatedRays(recommendedHitCount),
    heatmap_quality: heatmapQuality,
  }
}

export function convergenceSegmentSeed(
  baseSeed: number,
  segmentIndex: number,
): number {
  const modulus = 2_147_483_647
  const normalizedBase = ((Math.trunc(baseSeed) % modulus) + modulus) % modulus
  return (normalizedBase + Math.max(0, Math.trunc(segmentIndex)) * 1_000_003) % modulus
}

export function mergeConvergenceRayTraceResults(
  previous: RayTraceResult | null | undefined,
  current: RayTraceResult,
): RayTraceResult {
  if (!previous) return withAccumulationEvidence(current)
  const previousRays = previous.total_rays
  const currentRays = current.total_rays
  if (previousRays <= 0 || currentRays <= 0) {
    throw new Error('Convergence segments must contain positive ray counts')
  }
  const totalRays = previousRays + currentRays
  const previousGrids = new Map(
    previous.receiver_grids.map((grid) => [grid.receiver_id, grid]),
  )
  const receiverGrids = current.receiver_grids.map((grid) => {
    const previousGrid = previousGrids.get(grid.receiver_id)
    if (!previousGrid) {
      throw new Error(`Receiver changed during convergence: ${grid.receiver_id}`)
    }
    return mergeReceiverGrid(previousGrid, grid, previousRays, currentRays)
  })
  if (receiverGrids.length !== previous.receiver_grids.length) {
    throw new Error('Receiver set changed during convergence')
  }

  const previousEvidence = convergenceEvidence(previous)
  const currentEvidence = convergenceEvidence(current)
  const segmentRays = [
    ...previousEvidence.segment_rays,
    ...currentEvidence.segment_rays,
  ]
  const segmentSeeds = [
    ...previousEvidence.segment_seeds,
    ...currentEvidence.segment_seeds,
  ]
  const segmentComputeStates = [
    ...previousEvidence.segment_compute_states,
    ...currentEvidence.segment_compute_states,
  ]
  const emitterIds = new Set([
    ...Object.keys(previousEvidence.segment_emitter_seeds),
    ...Object.keys(currentEvidence.segment_emitter_seeds),
  ])
  const segmentEmitterSeeds = Object.fromEntries(
    [...emitterIds].map((emitterId) => [
      emitterId,
      [
        ...(previousEvidence.segment_emitter_seeds[emitterId] ?? []),
        ...(currentEvidence.segment_emitter_seeds[emitterId] ?? []),
      ],
    ]),
  )
  let cumulativeRays = 0
  let legacyFullRerunRays = 0
  for (const rays of segmentRays) {
    cumulativeRays += rays
    legacyFullRerunRays += cumulativeRays
  }
  const contributionSummary = mergeContributionRecord(
    previous.contribution_summary as unknown as Record<string, unknown>,
    current.contribution_summary as unknown as Record<string, unknown>,
    previousRays,
    currentRays,
  ) as unknown as RayTraceResult['contribution_summary']
  const metrics = structuredClone(current.metrics)
  for (const summaryKey of ['_reflection_summary', '_optical_summary']) {
    const previousSummary = previous.metrics[summaryKey]
    const currentSummary = current.metrics[summaryKey]
    if (isRecord(previousSummary) || isRecord(currentSummary)) {
      metrics[summaryKey] = mergeContributionRecord(
        isRecord(previousSummary) ? previousSummary : undefined,
        isRecord(currentSummary) ? currentSummary : undefined,
        previousRays,
        currentRays,
      )
    }
  }
  for (const grid of receiverGrids) {
    const currentMetric = metrics[grid.receiver_id]
    metrics[grid.receiver_id] = {
      ...(isRecord(currentMetric) ? currentMetric : {}),
      ...receiverMetrics(
        grid,
        totalRays,
        current.config.k_abs,
        current.config.k_brdf,
      ),
    }
  }
  metrics._contribution_summary = structuredClone(contributionSummary)
  const previousPerformance = isRecord(previous.metrics._performance_summary)
    ? previous.metrics._performance_summary
    : {}
  const currentPerformance = isRecord(current.metrics._performance_summary)
    ? current.metrics._performance_summary
    : {}
  const gpuUsed = segmentComputeStates.some((state) =>
    state === 'gpu_active' || state === 'gpu_mixed')
  const aggregateComputeState = gpuUsed
    ? segmentComputeStates.every((state) => state === 'gpu_active')
      ? 'gpu_active'
      : 'gpu_mixed'
    : segmentComputeStates.at(-1) ?? 'unknown'
  const cumulativeCount = (key: string) =>
    finiteNumber(previousPerformance[key]) + finiteNumber(currentPerformance[key])
  metrics._performance_summary = {
    ...structuredClone(currentPerformance),
    rays_per_sec: totalRays / Math.max(previous.runtime_sec + current.runtime_sec, 1e-12),
    compute_execution_state: aggregateComputeState,
    compute_execution_reason: 'convergence_segment_aggregate',
    gpu_cuda_gpu_attempt_count: cumulativeCount('gpu_cuda_gpu_attempt_count'),
    gpu_cuda_gpu_attempt_ray_count: cumulativeCount('gpu_cuda_gpu_attempt_ray_count'),
    gpu_cuda_gpu_success_count: cumulativeCount('gpu_cuda_gpu_success_count'),
    gpu_cuda_gpu_success_ray_count: cumulativeCount('gpu_cuda_gpu_success_ray_count'),
    gpu_cuda_hybrid_cpu_attempt_count: cumulativeCount('gpu_cuda_hybrid_cpu_attempt_count'),
    gpu_cuda_hybrid_cpu_attempt_ray_count: cumulativeCount('gpu_cuda_hybrid_cpu_attempt_ray_count'),
    gpu_cuda_hybrid_cpu_success_count: cumulativeCount('gpu_cuda_hybrid_cpu_success_count'),
    gpu_cuda_hybrid_cpu_success_ray_count: cumulativeCount('gpu_cuda_hybrid_cpu_success_ray_count'),
    gpu_resident_wavefront_success_count: cumulativeCount('gpu_resident_wavefront_success_count'),
    gpu_resident_wavefront_success_primary_count: cumulativeCount('gpu_resident_wavefront_success_primary_count'),
    gpu_resident_wavefront_fallback_count: cumulativeCount('gpu_resident_wavefront_fallback_count'),
    gpu_resident_wavefront_fallback_primary_count: cumulativeCount('gpu_resident_wavefront_fallback_primary_count'),
    gpu_summary_accumulator_success_count: cumulativeCount('gpu_summary_accumulator_success_count'),
    convergence_accumulation_contract: convergenceAccumulationContract,
    convergence_segment_count: segmentRays.length,
    convergence_total_rays: totalRays,
    convergence_total_runtime_sec: previous.runtime_sec + current.runtime_sec,
  }
  metrics._convergence_accumulation = {
    contract: convergenceAccumulationContract,
    segment_count: segmentRays.length,
    segment_rays: segmentRays,
    segment_seeds: segmentSeeds,
    segment_emitter_seeds: segmentEmitterSeeds,
    segment_compute_states: segmentComputeStates,
    total_rays: totalRays,
    avoided_retrace_rays: Math.max(0, legacyFullRerunRays - totalRays),
  } satisfies ConvergenceAccumulationEvidence
  const maxStoredPaths = Math.max(0, current.config.max_stored_paths)
  const storedPaths = [
    ...previous.stored_paths.map((path) => structuredClone(path)),
    ...current.stored_paths.map((path) => structuredClone(path)),
  ].slice(0, maxStoredPaths)
  const previousEmitters = new Map(
    previous.emitters.map((emitter) => [emitter.emitter_id, emitter]),
  )
  const emitters = current.emitters.map((emitter) => {
    const previousEmitter = previousEmitters.get(emitter.emitter_id)
    return {
      ...structuredClone(emitter),
      ray_count: emitter.ray_count + (previousEmitter?.ray_count ?? 0),
      seed: previousEmitter ? previousEmitter.seed : emitter.seed,
    }
  })

  return {
    ...structuredClone(current),
    config: {
      ...structuredClone(current.config),
      ray_count: totalRays,
      seed: previous.config.seed,
    },
    emitters,
    receiver_grids: receiverGrids,
    total_rays: totalRays,
    receiver_hit_count:
      previous.receiver_hit_count + current.receiver_hit_count,
    surface_hit_count: previous.surface_hit_count + current.surface_hit_count,
    terminated_ray_count:
      previous.terminated_ray_count + current.terminated_ray_count,
    contribution_summary: contributionSummary,
    runtime_sec: previous.runtime_sec + current.runtime_sec,
    stored_paths: storedPaths,
    metrics,
  }
}

function toRadians(value: number): number {
  return (value * Math.PI) / 180
}

function toDegrees(value: number): number {
  return (value * 180) / Math.PI
}

function rotateX([x, y, z]: Vec3, angle: number): Vec3 {
  const cosine = Math.cos(angle)
  const sine = Math.sin(angle)
  return [x, y * cosine - z * sine, y * sine + z * cosine]
}

function rotateY([x, y, z]: Vec3, angle: number): Vec3 {
  const cosine = Math.cos(angle)
  const sine = Math.sin(angle)
  return [x * cosine + z * sine, y, -x * sine + z * cosine]
}

function rotateZ([x, y, z]: Vec3, angle: number): Vec3 {
  const cosine = Math.cos(angle)
  const sine = Math.sin(angle)
  return [x * cosine - y * sine, x * sine + y * cosine, z]
}

function rotateVector(vector: Vec3, rotationDeg: Vec3): Vec3 {
  return rotateZ(
    rotateY(
      rotateX(vector, toRadians(rotationDeg[0])),
      toRadians(rotationDeg[1]),
    ),
    toRadians(rotationDeg[2]),
  )
}

export function planeAxesFromRotation(rotationDeg: Vec3): {
  normal: Vec3
  uAxis: Vec3
  vAxis: Vec3
} {
  return {
    uAxis: rotateVector([1, 0, 0], rotationDeg),
    vAxis: rotateVector([0, 1, 0], rotationDeg),
    normal: rotateVector([0, 0, 1], rotationDeg),
  }
}

export function rotationFromPlaneAxes(
  uAxis: Vec3 | null,
  vAxis: Vec3 | null,
  normal: Vec3 | null,
): Vec3 {
  if (!uAxis || !vAxis || !normal) return [0, 0, 0]
  const rotationY = Math.asin(
    Math.max(-1, Math.min(1, -uAxis[2])),
  )
  const cosineY = Math.cos(rotationY)
  const rotationX =
    Math.abs(cosineY) > 1e-7
      ? Math.atan2(vAxis[2], normal[2])
      : 0
  const rotationZ =
    Math.abs(cosineY) > 1e-7
      ? Math.atan2(uAxis[1], uAxis[0])
      : Math.atan2(-vAxis[0], vAxis[1])
  return [
    toDegrees(rotationX),
    toDegrees(rotationY),
    toDegrees(rotationZ),
  ]
}

export function nextSpecId(
  prefix: 'emitter' | 'receiver',
  currentIds: Iterable<string>,
): string {
  let maximum = 0
  const pattern = new RegExp(`^${prefix}_(\\d+)$`)
  for (const id of currentIds) {
    const match = pattern.exec(id)
    if (match) maximum = Math.max(maximum, Number(match[1]) || 0)
  }
  return `${prefix}_${String(maximum + 1).padStart(3, '0')}`
}

/**
 * Converts internal ray-object IDs into consistent user-facing labels while
 * preserving names explicitly entered by the user.
 */
export function rayObjectDisplayName(
  kind: 'emitter' | 'receiver',
  objectId: string,
  displayName?: string | null,
): string {
  const label = displayName?.trim() || objectId.trim()
  const match = new RegExp(`^${kind}[\\s_-]*0*(\\d+)$`, 'i').exec(label)
  const kindLabel = kind === 'emitter' ? 'Emitter' : 'Receiver'
  if (match) return `${kindLabel} ${Number(match[1])}`
  return label || kindLabel
}

export function createFaceEmitter(
  emitterId: string,
  faceIds: number[],
): EmitterSpec {
  return {
    emitter_id: emitterId,
    emitter_type: 'face',
    face_indices: [...new Set(faceIds)].sort((left, right) => left - right),
    normal_mode: 'face_normal',
    // The visible/trace direction is the direction a user looks from the
    // Receiver front into the product. With the stored right-handed U/V
    // frame this flip makes local +X screen-right and local +Y screen-up.
    normal_flip: true,
    custom_normal: null,
    direction_distribution: 'lambertian',
    gaussian_sigma_deg: 12,
    power_mode: 'set_luminance',
    power_lumen: 1,
    power_density_lm_per_m2: 100,
    luminance_nit: 500,
    center: null,
    u_axis: null,
    v_axis: null,
    width_mm: null,
    height_mm: null,
    reference_mode: null,
    surface_construction: 'rectangular_fit',
    polygon_vertices: [],
    reference_vertex_indices: [],
    reference_edge_vertex_indices: [],
    reference_vertex_points: [],
    reference_edge_points: [],
    ray_count: 10_000,
    seed: null,
    enabled: true,
  }
}

export function createDatumEmitter(
  emitterId: string,
  center: Vec3,
  rotationDeg: Vec3,
): EmitterSpec {
  const axes = planeAxesFromRotation(rotationDeg)
  return {
    ...createFaceEmitter(emitterId, []),
    emitter_type: 'datum_plane',
    normal_mode: 'custom',
    custom_normal: axes.normal,
    center,
    u_axis: axes.uAxis,
    v_axis: axes.vAxis,
    width_mm: 20,
    height_mm: 20,
  }
}

function vectorLength(vector: Vec3): number {
  return Math.sqrt(
    vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2],
  )
}

function normalizeVector(vector: Vec3): Vec3 {
  const length = vectorLength(vector)
  if (length < 1e-9) return [0, 0, 1]
  return [vector[0] / length, vector[1] / length, vector[2] / length]
}

function crossVector(a: Vec3, b: Vec3): Vec3 {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ]
}

function dotVector(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

/** Canonical (u, v) basis for a given face normal, used to turn a picked
 * CAD face into a Rotation X/Y/Z the datum-plane editor can display and
 * further adjust - same "pick any stable perpendicular" approach as most
 * CAD tools use when only a normal (no in-plane reference) is available. */
export function axesFromNormal(
  normal: Vec3,
  preferredRight: Vec3 = [1, 0, 0],
): {
  uAxis: Vec3
  vAxis: Vec3
} {
  const n = normalizeVector(normal)
  // Project the current Viewer screen-right direction onto the Receiver
  // plane. A face normal alone cannot define an in-plane X/Y orientation;
  // this additional reference prevents arbitrary 90-degree rotations.
  const candidates: Vec3[] = [
    preferredRight,
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ]
  const preferred =
    candidates.find((candidate) => {
      const projection = dotVector(candidate, n)
      const projected: Vec3 = [
        candidate[0] - n[0] * projection,
        candidate[1] - n[1] * projection,
        candidate[2] - n[2] * projection,
      ]
      return vectorLength(projected) > 1e-6
    }) ?? [1, 0, 0]
  const projection = dotVector(preferred, n)
  const uAxis = normalizeVector([
    preferred[0] - n[0] * projection,
    preferred[1] - n[1] * projection,
    preferred[2] - n[2] * projection,
  ])
  const vAxis = crossVector(n, uAxis)
  return { uAxis, vAxis }
}

export function createDatumReceiver(
  receiverId: string,
  baseCenter: Vec3,
  rotationDeg: Vec3,
  positionOffset: Vec3 = [0, 0, 0],
  pivot: Vec3 | null = null,
): ReceiverSpec {
  const axes = planeAxesFromRotation(rotationDeg)
  const centerBeforeTilt: Vec3 = [
    baseCenter[0] + positionOffset[0],
    baseCenter[1] + positionOffset[1],
    baseCenter[2] + positionOffset[2],
  ]
  const pivotPoint = pivot ?? centerBeforeTilt
  // The axes above already encode the full absolute orientation (rotated
  // straight from the canonical [0,0,1]/[1,0,0]/[0,1,0] frame), so only
  // the position needs a pivot correction: revolve the offset-from-pivot
  // by the same rotation to find where the plane ends up. With no custom
  // pivot (pivot === centerBeforeTilt) this delta is zero and the center
  // is unaffected by rotationDeg, same as before this field existed.
  const delta: Vec3 = [
    centerBeforeTilt[0] - pivotPoint[0],
    centerBeforeTilt[1] - pivotPoint[1],
    centerBeforeTilt[2] - pivotPoint[2],
  ]
  const rotatedDelta = rotateVector(delta, rotationDeg)
  const center: Vec3 = [
    pivotPoint[0] + rotatedDelta[0],
    pivotPoint[1] + rotatedDelta[1],
    pivotPoint[2] + rotatedDelta[2],
  ]
  return {
    receiver_id: receiverId,
    receiver_type: 'rectangle',
    display_name: rayObjectDisplayName('receiver', receiverId),
    placement_mode: 'datum_plane',
    center,
    normal: axes.normal,
    u_axis: axes.uAxis,
    v_axis: axes.vAxis,
    width_mm: 30,
    height_mm: 30,
    resolution: [80, 24],
    acceptance_angle_deg: 90,
    normal_flip: false,
    reference_mode: null,
    reference_vertex_indices: [],
    reference_edge_vertex_indices: [],
    reference_vertex_points: [],
    reference_edge_points: [],
    view_distance_mm: null,
    base_center: baseCenter,
    base_u_axis: null,
    base_v_axis: null,
    base_normal: null,
    position_offset_mm: positionOffset,
    tilt_xyz_deg: rotationDeg,
    pivot,
    enabled: true,
  }
}

export function createCurrentViewReceiver(
  receiverId: string,
  frame: ViewerCameraFrame,
  distanceMm: number,
  positionOffset: Vec3 = [0, 0, 0],
  tiltDeg: Vec3 = [0, 0, 0],
): ReceiverSpec {
  const distance = Math.max(0.001, distanceMm)
  const baseCenter: Vec3 = [
    frame.target[0] - frame.normal[0] * distance,
    frame.target[1] - frame.normal[1] * distance,
    frame.target[2] - frame.normal[2] * distance,
  ]
  const center: Vec3 = [
    baseCenter[0] + positionOffset[0],
    baseCenter[1] + positionOffset[1],
    baseCenter[2] + positionOffset[2],
  ]
  const viewingNormal = rotateVector(frame.normal, tiltDeg)
  const uAxis = rotateVector(frame.uAxis, tiltDeg)
  const vAxis = rotateVector(
    [-frame.vAxis[0], -frame.vAxis[1], -frame.vAxis[2]],
    tiltDeg,
  )
  const opposite = (value: number) => value === 0 ? 0 : -value
  const normal: Vec3 = viewingNormal.map(opposite) as Vec3
  return {
    ...createDatumReceiver(receiverId, center, [0, 0, 0]),
    placement_mode: 'current_view',
    center,
    normal,
    u_axis: uAxis,
    v_axis: vAxis,
    normal_flip: true,
    view_distance_mm: distance,
    base_center: baseCenter,
    base_u_axis: [...frame.uAxis],
    base_v_axis: [...frame.vAxis],
    base_normal: [...frame.normal],
    position_offset_mm: [...positionOffset],
    tilt_xyz_deg: [...tiltDeg],
  }
}

function buildOpticalPayload(assignments: MaterialAssignment[]): {
  profiles: OpticalProfile[]
  assignments: OpticalAssignment[]
} {
  const profiles = new Map<string, OpticalProfile>()
  const opticalAssignments: OpticalAssignment[] = []

  for (const [priority, assignment] of assignments.entries()) {
    if (!assignment.enabled) continue
    const profileId =
      assignment.profileId.trim() || `compiled-${assignment.assignmentId}`
    const compiled = compileOpticalProfile(
      assignment.baseMaterialId,
      assignment.surfaceId,
    )
    const custom = assignment.opticalOverride
    profiles.set(profileId, {
      profile_id: profileId,
      reflectance: custom?.reflectance ?? compiled.reflectance,
      absorption: custom?.loss ?? compiled.loss,
      specular_ratio: custom?.specularRatio ?? compiled.specularRatio,
      diffuse_ratio: custom?.diffuseRatio ?? compiled.diffuseRatio,
      scatter_model: compiled.scatterModel,
      roughness: compiled.roughness,
      gaussian_sigma_deg: compiled.scatterSigmaDeg,
      bsdf_asset_id: assignment.bsdfAssetId || null,
      notes: `Compiled from ${assignment.baseMaterialId} / ${assignment.surfaceId}`,
    })
    opticalAssignments.push({
      assignment_id: assignment.assignmentId,
      target_type: assignment.targetType,
      component_id: assignment.componentId,
      profile_id: profileId,
      face_indices:
        assignment.targetType === 'faces' ? assignment.faceIds : [],
      priority,
      enabled: true,
    })
  }

  return {
    profiles: [...profiles.values()],
    assignments: opticalAssignments,
  }
}

function activeRoiFaces(
  scopes: RoiScope[],
  deletedComponentIds: Set<number>,
): number[] {
  return [
    ...new Set(
      scopes
        .filter((scope) => scope.active)
        .flatMap((scope) =>
          scope.components
            .filter(
              (component) =>
                !deletedComponentIds.has(component.componentId),
            )
            .flatMap((component) => component.faceIds),
        ),
    ),
  ].sort((left, right) => left - right)
}

export function buildRayTraceRequest({
  scene,
  projectName,
  emitters,
  receivers,
  materialAssignments,
  transformRules,
  excludedComponentIds,
  deletedComponentIds,
  roiScopes,
  config,
}: RayTraceRequestSource): RayTraceRequest {
  const enabledEmitters = emitters.filter((emitter) => emitter.enabled)
  const enabledReceivers = receivers.filter((receiver) => receiver.enabled)
  const totalRayCount = enabledEmitters.reduce(
    (sum, emitter) => sum + Math.max(1, emitter.ray_count),
    0,
  )
  const optical = buildOpticalPayload(materialAssignments)
  const deleted = new Set(deletedComponentIds)
  const roiFaces = activeRoiFaces(roiScopes, deleted)
  const {
    auto_convergence: _autoConvergence,
    convergence_target_percent: _convergenceTarget,
    max_convergence_multiplier: _maxConvergenceMultiplier,
    ...backendConfig
  } = config

  return {
    scene_token: scene.metadata.scene_token,
    project_name: projectName.trim() || 'TV-Leakage-Direct',
    emitters: enabledEmitters,
    receivers: enabledReceivers,
    optical_profiles: optical.profiles,
    optical_assignments: optical.assignments,
    transform_rules: transformRules
      .filter(
        (rule) =>
          rule.enabled &&
          rule.targetType === 'component' &&
          !deleted.has(rule.componentId),
      )
      .map((rule) => ({
        rule_id: rule.ruleId,
        target_type: 'component',
        object_id: rule.componentId,
        label: rule.ruleId,
        enabled: true,
        move: rule.move,
        tilt: rule.tilt,
        ...(rule.pivot ? { pivot: rule.pivot } : {}),
      })),
    excluded_component_ids: [
      ...new Set([
        ...excludedComponentIds,
        ...deletedComponentIds,
      ]),
    ].sort((left, right) => left - right),
    ...(roiFaces.length > 0 ? { roi_faces: roiFaces } : {}),
    config: {
      ...backendConfig,
      ray_count: Math.max(1, totalRayCount),
    },
  }
}
