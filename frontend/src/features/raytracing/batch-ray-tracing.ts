import {
  apiClient,
  type LeakageApiClient,
  type RayTraceJob,
  type RayTraceRequest,
  type RayTraceResult,
  type ScenePayload,
} from '@/api'
import {
  cadCaseRayTraceSourcePaths,
  snapshotCadCasesForRayTrace,
  type CadCaseRayTraceSnapshot,
  type WorkspaceStoreApi,
} from '@/stores'

import {
  buildRayTraceRequest,
  convergenceSegmentSeed,
  mergeConvergenceRayTraceResults,
  metricErrorPercent,
  receiverMeetsStatisticalTarget,
} from './ray-tracing-model'
import { isGpuCudaStatusReady } from './gpu-cuda-status'
import { buildPrecisionPreviewConstraints } from './precision-preview-constraints'

type BatchApi = Pick<
  LeakageApiClient,
  | 'getScene'
  | 'getGpuCudaStatus'
  | 'startRayTrace'
  | 'getRayTraceJob'
  | 'stopRayTrace'
>

export type BatchRayTraceCaseStatus =
  | 'pending'
  | 'loading_scene'
  | 'queued'
  | 'preparing'
  | 'running'
  | 'finalizing'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface BatchRayTraceCaseState {
  caseId: string
  name: string
  status: BatchRayTraceCaseStatus
  progress: number
  phaseDetail: string
  jobId: string | null
  runId: string | null
  error: string | null
  geometryCacheHit: boolean | null
  segmentIndex: number
  rayMultiplier: number
}

export type BatchRayTraceStatus =
  | 'idle'
  | 'running'
  | 'completed'
  | 'completed_with_errors'
  | 'cancelled'

export interface BatchRayTraceState {
  batchId: string | null
  status: BatchRayTraceStatus
  currentCaseId: string | null
  progress: number
  startedAt: number | null
  completedAt: number | null
  cases: BatchRayTraceCaseState[]
}

export interface BatchRayTracingController {
  run(caseIds: Iterable<string>): Promise<BatchRayTraceState>
  cancel(): Promise<void>
  getState(): BatchRayTraceState
  isRunning(): boolean
}

type AuxiliaryBlockers = NonNullable<RayTraceRequest['preview_blockers']>

export interface BatchRayTracingControllerOptions {
  store: WorkspaceStoreApi
  client?: BatchApi
  pollIntervalMs?: number
  onStateChange?(state: BatchRayTraceState): void
  getAuxiliaryBlockers?(snapshot: CadCaseRayTraceSnapshot): AuxiliaryBlockers
  now?(): number
  sleep?(milliseconds: number, signal: AbortSignal): Promise<void>
}

const terminalCaseStatuses = new Set<BatchRayTraceCaseStatus>([
  'completed',
  'failed',
  'cancelled',
])

function cloneState(state: BatchRayTraceState): BatchRayTraceState {
  return structuredClone(state)
}

function abortedError(): Error {
  const error = new Error('Batch Ray Tracing이 중단되었습니다.')
  error.name = 'AbortError'
  return error
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function clampProgress(progress: number): number {
  return Math.max(0, Math.min(1, progress))
}

function segmentCaseProgress(
  progress: number,
  autoConvergence: boolean,
  completedMultiplier: number,
  segmentMultiplier: number,
  maximumMultiplier: number,
): number {
  const normalizedProgress = clampProgress(progress)
  if (!autoConvergence) return normalizedProgress
  return clampProgress(
    (
      completedMultiplier +
      normalizedProgress * segmentMultiplier
    ) / Math.max(1, maximumMultiplier),
  )
}

class BatchStopUnconfirmedError extends Error {
  constructor(jobId: string, originalError: unknown, stopError: unknown) {
    super(
      `Backend 작업 ${jobId}의 중단을 확인하지 못해 Batch를 안전하게 종료했습니다. ` +
      `원래 오류: ${errorMessage(originalError)} / 중단 오류: ${errorMessage(stopError)}`,
    )
    this.name = 'BatchStopUnconfirmedError'
  }
}

function defaultSleep(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(abortedError())
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', stop)
      resolve()
    }, milliseconds)
    const stop = () => {
      window.clearTimeout(timer)
      reject(abortedError())
    }
    signal.addEventListener('abort', stop, { once: true })
  })
}

function jobCaseStatus(job: RayTraceJob): BatchRayTraceCaseStatus {
  if (job.status === 'queued') return 'queued'
  if (job.status === 'failed') return 'failed'
  if (job.status === 'cancelled') return 'cancelled'
  if (job.status === 'completed') {
    return job.phase === 'stopped' ? 'cancelled' : 'completed'
  }
  if (job.phase === 'preparing') return 'preparing'
  if (job.phase === 'aggregating' || job.phase === 'serializing') {
    return 'finalizing'
  }
  return 'running'
}

function publishedJobCaseStatus(job: RayTraceJob): BatchRayTraceCaseStatus {
  const status = jobCaseStatus(job)
  // A backend-completed segment is not a completed Case until local
  // convergence merge, freshness validation, and final result commit finish.
  return status === 'completed' ? 'finalizing' : status
}

function batchSourceKey(snapshot: CadCaseRayTraceSnapshot): string {
  return JSON.stringify(cadCaseRayTraceSourcePaths(snapshot))
}

function convergenceHistoryEntry(
  result: RayTraceResult,
  snapshot: CadCaseRayTraceSnapshot,
) {
  const metrics = snapshot.workspaceState.receivers
    .filter((receiver) => receiver.enabled)
    .map((receiver) => {
      const value = result.metrics[receiver.receiver_id]
      return value && typeof value === 'object'
        ? value as Record<string, unknown>
        : {}
    })
  return {
    metrics,
    history: {
      rays: result.total_rays,
      totalError: Math.max(
        ...metrics.map((value) =>
          metricErrorPercent(value.error_estimate_percent)),
      ),
      peakError: Math.max(
        ...metrics.map((value) =>
          metricErrorPercent(value.peak_error_estimate_percent)),
      ),
      peakNit: Math.max(
        ...metrics.map((value) => Number(value.peak_nit_est) || 0),
        0,
      ),
      flux: metrics.reduce(
        (sum, value) => sum + (Number(value.total_flux_lumen) || 0),
        0,
      ),
    },
  }
}

function requestForSegment(
  snapshot: CadCaseRayTraceSnapshot,
  scene: ScenePayload,
  rayMultiplier: number,
  segmentIndex: number,
  auxiliaryBlockers: AuxiliaryBlockers,
): RayTraceRequest {
  const workspace = snapshot.workspaceState
  const request = buildRayTraceRequest({
    scene,
    projectName: snapshot.name || snapshot.cad.displayName,
    emitters: workspace.emitters.map((emitter) => ({
      ...emitter,
      ray_count: Math.max(1, Math.trunc(emitter.ray_count * rayMultiplier)),
    })),
    receivers: workspace.receivers,
    materialAssignments: workspace.materialAssignments,
    bsdfAssets: workspace.bsdfAssets,
    transformRules: workspace.transformRules,
    excludedComponentIds: workspace.excludedComponentIds,
    deletedComponentIds: workspace.deletedComponentIds,
    roiScopes: workspace.roiScopes,
    config: workspace.rayTraceConfig,
    auxiliaryBlockers,
  })
  if (workspace.rayTraceConfig.auto_convergence) {
    request.config.seed = convergenceSegmentSeed(
      workspace.rayTraceConfig.seed,
      segmentIndex,
    )
    request.emitters = request.emitters.map((emitter) => ({
      ...emitter,
      seed: emitter.seed === null
        ? null
        : convergenceSegmentSeed(emitter.seed, segmentIndex),
    }))
  }
  return request
}

export function createBatchRayTracingController(
  options: BatchRayTracingControllerOptions,
): BatchRayTracingController {
  const client = options.client ?? apiClient
  const pollIntervalMs = Math.max(0, options.pollIntervalMs ?? 300)
  const now = options.now ?? Date.now
  const sleep = options.sleep ?? defaultSleep
  let abortController: AbortController | null = null
  let activeRun: Promise<BatchRayTraceState> | null = null
  let running = false
  let state: BatchRayTraceState = {
    batchId: null,
    status: 'idle',
    currentCaseId: null,
    progress: 0,
    startedAt: null,
    completedAt: null,
    cases: [],
  }

  const publish = () => {
    const count = state.cases.length
    state.progress = count === 0
      ? 0
      : state.cases.reduce(
          (sum, item) => sum + (
            terminalCaseStatuses.has(item.status) ? 1 : item.progress
          ),
          0,
        ) / count
    options.onStateChange?.(cloneState(state))
  }

  const updateCase = (
    caseId: string,
    patch: Partial<BatchRayTraceCaseState>,
  ) => {
    state.cases = state.cases.map((item) => {
      if (item.caseId !== caseId) return item
      const nextProgress = patch.progress === undefined
        ? item.progress
        : Math.max(item.progress, clampProgress(patch.progress))
      return { ...item, ...patch, progress: nextProgress }
    })
    publish()
  }

  const ensureNotAborted = (signal: AbortSignal) => {
    if (signal.aborted) throw abortedError()
  }

  const stopAndConfirmJob = async (
    jobId: string,
    originalError: unknown,
  ): Promise<void> => {
    let lastStopError: unknown = new Error('Backend 종료 상태를 확인하지 못했습니다.')
    try {
      let stoppedJob = await client.stopRayTrace(jobId)
      if (!['queued', 'running'].includes(stoppedJob.status)) return

      // A successful stop request only means that the backend accepted the
      // request. Wait for the worker to publish a terminal status before a
      // subsequent Case is allowed to start.
      const confirmationSignal = new AbortController().signal
      for (let attempt = 0; attempt < 20; attempt += 1) {
        try {
          stoppedJob = await client.getRayTraceJob(jobId)
          if (!['queued', 'running'].includes(stoppedJob.status)) return
          lastStopError = new Error(
            `Backend 작업이 아직 ${stoppedJob.status} 상태입니다.`,
          )
        } catch (error) {
          lastStopError = error
        }
        if (attempt < 19) {
          await sleep(Math.max(50, pollIntervalMs), confirmationSignal)
        }
      }
    } catch (error) {
      lastStopError = error
    }
    throw new BatchStopUnconfirmedError(
      jobId,
      originalError,
      lastStopError,
    )
  }

  const runJob = async (
    snapshot: CadCaseRayTraceSnapshot,
    request: RayTraceRequest,
    signal: AbortSignal,
    segmentIndex: number,
    rayMultiplier: number,
    completedMultiplier: number,
    maximumMultiplier: number,
    autoConvergence: boolean,
  ): Promise<RayTraceResult> => {
    ensureNotAborted(signal)
    // Do not abort the start request after dispatch: the server may already
    // have created a job while the response carrying its job_id is in flight.
    // We must receive that id and explicitly stop/confirm the worker instead.
    let job = await client.startRayTrace(request)
    if (signal.aborted) {
      await stopAndConfirmJob(job.job_id, abortedError())
      throw abortedError()
    }
    options.store.getState().actions.setCadCaseRayTraceJob(
      snapshot.caseId,
      job.job_id,
    )
    updateCase(snapshot.caseId, {
      jobId: job.job_id,
      status: publishedJobCaseStatus(job),
      progress: segmentCaseProgress(
        job.progress,
        autoConvergence,
        completedMultiplier,
        rayMultiplier,
        maximumMultiplier,
      ),
      phaseDetail: job.phase_detail ?? job.phase,
      geometryCacheHit: job.geometry_cache_hit ?? null,
      segmentIndex,
      rayMultiplier,
    })

    try {
      while (job.status === 'queued' || job.status === 'running') {
        await sleep(pollIntervalMs, signal)
        ensureNotAborted(signal)
        job = await client.getRayTraceJob(job.job_id, { signal })
        updateCase(snapshot.caseId, {
          status: publishedJobCaseStatus(job),
          progress: segmentCaseProgress(
            job.progress,
            autoConvergence,
            completedMultiplier,
            rayMultiplier,
            maximumMultiplier,
          ),
          phaseDetail: job.phase_detail ?? job.phase,
          geometryCacheHit: job.geometry_cache_hit ?? null,
        })
      }
      if (job.status === 'failed') throw new Error(job.error)
      if (job.status === 'cancelled' || job.phase === 'stopped') {
        throw signal.aborted
          ? abortedError()
          : new Error('Ray Tracing 작업이 외부 요청으로 중단되었습니다.')
      }
      if (job.status !== 'completed' || !job.result) {
        throw new Error('Ray Tracing 결과를 받지 못했습니다.')
      }
      return job.result
    } catch (error) {
      if (job.status === 'queued' || job.status === 'running') {
        await stopAndConfirmJob(job.job_id, error)
      }
      throw error
    } finally {
      options.store.getState().actions.setCadCaseRayTraceJob(
        snapshot.caseId,
        null,
      )
    }
  }

  const execute = async (
    snapshots: CadCaseRayTraceSnapshot[],
    signal: AbortSignal,
  ): Promise<BatchRayTraceState> => {
    const sceneCache = new Map<string, ScenePayload>()
    const rememberScene = (key: string, scene: ScenePayload) => {
      sceneCache.delete(key)
      sceneCache.set(key, scene)
      while (sceneCache.size > 3) {
        const oldestKey = sceneCache.keys().next().value
        if (oldestKey === undefined) break
        sceneCache.delete(oldestKey)
      }
    }
    let gpuFailure: string | null = null
    if (snapshots.some(
      (snapshot) =>
        snapshot.workspaceState.rayTraceConfig.compute_backend === 'gpu_cuda',
    )) {
      try {
        const gpuStatus = await client.getGpuCudaStatus({ signal })
        if (!isGpuCudaStatusReady(gpuStatus)) {
          gpuFailure = `NVIDIA CUDA GPU 준비 검사를 통과하지 못했습니다${
            gpuStatus.reason_code ? `: ${gpuStatus.reason_code}` : ''
          }. CPU fallback 없이 이 Case를 건너뜁니다.`
        }
      } catch (error) {
        if (isAbortError(error) || signal.aborted) throw abortedError()
        gpuFailure = `NVIDIA CUDA GPU 준비 상태를 확인하지 못했습니다: ${errorMessage(error)}. CPU fallback 없이 이 Case를 건너뜁니다.`
      }
    }

    for (const snapshot of snapshots) {
      ensureNotAborted(signal)
      state.currentCaseId = snapshot.caseId
      publish()
      const workspace = snapshot.workspaceState
      if (!workspace.emitters.some((emitter) => emitter.enabled)) {
        updateCase(snapshot.caseId, {
          status: 'failed',
          progress: 1,
          phaseDetail: '실행 조건 확인 실패',
          error: '활성 Emitter가 없습니다.',
        })
        continue
      }
      if (!workspace.receivers.some((receiver) => receiver.enabled)) {
        updateCase(snapshot.caseId, {
          status: 'failed',
          progress: 1,
          phaseDetail: '실행 조건 확인 실패',
          error: '활성 Receiver가 없습니다.',
        })
        continue
      }
      const requiresPreviewConstraints = Boolean(
        workspace.rayTraceConfig.apply_preview_blockers ||
        workspace.rayTraceConfig.apply_allowed_areas,
      )
      if (
        requiresPreviewConstraints &&
        !options.getAuxiliaryBlockers &&
        !snapshot.leakPreviewState
      ) {
        updateCase(snapshot.caseId, {
          status: 'failed',
          progress: 1,
          phaseDetail: '보조 형상 확인 실패',
          error: '이 Case는 Leak Preview 차광/허용 영역 적용이 켜져 있지만 Case별 보조 형상 데이터가 없습니다. 조건을 끄거나 정확한 Case 데이터를 연결한 뒤 다시 실행해 주세요.',
        })
        continue
      }
      if (
        workspace.rayTraceConfig.compute_backend === 'gpu_cuda' &&
        gpuFailure
      ) {
        updateCase(snapshot.caseId, {
          status: 'failed',
          progress: 1,
          phaseDetail: 'GPU 준비 검사 실패',
          error: gpuFailure,
        })
        continue
      }

      try {
        const sourceKey = batchSourceKey(snapshot)
        let scene = sceneCache.get(sourceKey)
        if (scene) rememberScene(sourceKey, scene)
        if (!scene) {
          updateCase(snapshot.caseId, {
            status: 'loading_scene',
            phaseDetail: 'CAD Scene 불러오는 중',
            progress: 0,
          })
          const [primaryPath, ...accessoryPaths] =
            cadCaseRayTraceSourcePaths(snapshot)
          scene = await client.getScene(
            primaryPath,
            { signal },
            accessoryPaths,
          )
          rememberScene(sourceKey, scene)
        }

        const auxiliaryBlockers = options.getAuxiliaryBlockers?.(snapshot) ??
          buildPrecisionPreviewConstraints(
            snapshot.leakPreviewState?.blockers ?? [],
            snapshot.leakPreviewState?.ignoreAreas ?? [],
            Boolean(workspace.rayTraceConfig.apply_preview_blockers),
            Boolean(workspace.rayTraceConfig.apply_allowed_areas),
          )
        let accumulatedResult: RayTraceResult | null = null
        const history: Record<string, number>[] = []
        let rayMultiplier = 1
        let segmentIndex = 0
        let currentMultiplier = 1
        const maximumMultiplier = Math.max(
          1,
          workspace.rayTraceConfig.max_convergence_multiplier ?? 8,
        )
        while (true) {
          ensureNotAborted(signal)
          const runSegment = async () => {
            const request = requestForSegment(
              snapshot,
              scene!,
              rayMultiplier,
              segmentIndex,
              auxiliaryBlockers,
            )
            return runJob(
              snapshot,
              request,
              signal,
              segmentIndex,
              rayMultiplier,
              currentMultiplier - rayMultiplier,
              maximumMultiplier,
              Boolean(workspace.rayTraceConfig.auto_convergence),
            )
          }
          let segmentResult: RayTraceResult
          try {
            segmentResult = await runSegment()
          } catch (error) {
            const cacheExpired =
              error instanceof Error &&
              error.message.includes('CAD scene cache expired')
            if (!cacheExpired || signal.aborted) throw error
            const [primaryPath, ...accessoryPaths] =
              cadCaseRayTraceSourcePaths(snapshot)
            scene = await client.getScene(
              primaryPath,
              { signal },
              accessoryPaths,
            )
            rememberScene(sourceKey, scene)
            segmentResult = await runSegment()
          }

          accumulatedResult = workspace.rayTraceConfig.auto_convergence
            ? mergeConvergenceRayTraceResults(
                accumulatedResult,
                segmentResult,
              )
            : segmentResult
          const evidence = convergenceHistoryEntry(
            accumulatedResult,
            snapshot,
          )
          history.push(evidence.history)
          accumulatedResult.metrics._convergence_history = structuredClone(history)
          const freshness = options.store.getState().actions
            .validateCadCaseRayTraceSetup(
              snapshot.caseId,
              snapshot.setupHash,
            )
          if (freshness === 'missing') {
            throw new Error('실행 중 Case가 삭제되어 결과를 저장하지 못했습니다.')
          }
          if (freshness === 'stale') {
            throw new Error('실행 중 Case 조건이 변경되어 오래된 결과를 저장하지 않았습니다.')
          }

          if (!workspace.rayTraceConfig.auto_convergence) break
          const target = workspace.rayTraceConfig.convergence_target_percent ?? 5
          const converged = evidence.metrics.length > 0 &&
            evidence.metrics.every((metric) =>
              receiverMeetsStatisticalTarget(metric, target, true))
          if (converged) break
          const nextMultiplier = currentMultiplier * 2
          if (nextMultiplier > maximumMultiplier) break
          rayMultiplier = nextMultiplier - currentMultiplier
          currentMultiplier = nextMultiplier
          segmentIndex += 1
          updateCase(snapshot.caseId, {
            phaseDetail: `Auto convergence ${segmentIndex + 1}차 실행 준비`,
            progress: segmentCaseProgress(
              0,
              true,
              currentMultiplier - rayMultiplier,
              rayMultiplier,
              maximumMultiplier,
            ),
            segmentIndex,
            rayMultiplier,
          })
        }

        if (!accumulatedResult) {
          throw new Error('Ray Tracing 결과를 받지 못했습니다.')
        }
        const stored = options.store.getState().actions.setCadCaseRayTraceResult(
          snapshot.caseId,
          accumulatedResult,
          snapshot.setupHash,
        )
        if (stored === 'missing') {
          throw new Error('실행 중 Case가 삭제되어 결과를 저장하지 못했습니다.')
        }
        if (stored === 'stale') {
          throw new Error('실행 중 Case 조건이 변경되어 오래된 결과를 저장하지 않았습니다.')
        }

        updateCase(snapshot.caseId, {
          status: 'completed',
          progress: 1,
          phaseDetail: workspace.rayTraceConfig.auto_convergence
            ? `Auto convergence 완료 · 누적 ${
                accumulatedResult.total_rays.toLocaleString()
              } rays`
            : 'Ray Tracing 완료',
          error: null,
          jobId: null,
          runId: accumulatedResult.run_id,
        })
      } catch (error) {
        options.store.getState().actions.setCadCaseRayTraceJob(
          snapshot.caseId,
          null,
        )
        if (error instanceof BatchStopUnconfirmedError) {
          updateCase(snapshot.caseId, {
            status: 'failed',
            progress: 1,
            phaseDetail: 'Backend 중단 확인 실패',
            error: errorMessage(error),
            jobId: null,
          })
          state.cases = state.cases.map((item) =>
            item.status === 'pending'
              ? {
                  ...item,
                  status: 'cancelled',
                  progress: 1,
                  phaseDetail: '이전 작업 중단 확인 불가로 실행 취소',
                }
              : item,
          )
          state.status = 'completed_with_errors'
          state.currentCaseId = null
          state.completedAt = now()
          publish()
          return cloneState(state)
        }
        if (isAbortError(error) || signal.aborted) {
          updateCase(snapshot.caseId, {
            status: 'cancelled',
            progress: 1,
            phaseDetail: '사용자 중단',
            error: null,
            jobId: null,
          })
          break
        }
        updateCase(snapshot.caseId, {
          status: 'failed',
          progress: 1,
          phaseDetail: 'Ray Tracing 실패',
          error: errorMessage(error),
          jobId: null,
        })
      }
    }

    if (signal.aborted) {
      state.cases = state.cases.map((item) =>
        item.status === 'pending'
          ? {
              ...item,
              status: 'cancelled',
              progress: 1,
              phaseDetail: '실행 전 중단',
            }
          : item,
      )
      state.status = 'cancelled'
    } else {
      state.status = state.cases.some((item) => item.status === 'failed')
        ? 'completed_with_errors'
        : 'completed'
    }
    state.currentCaseId = null
    state.completedAt = now()
    publish()
    return cloneState(state)
  }

  const run = async (caseIds: Iterable<string>) => {
    if (running || options.store.getState().batchRayTraceActive) {
      throw new Error('Batch Ray Tracing이 이미 실행 중입니다.')
    }
    if (options.store.getState().singleRayTraceRun) {
      throw new Error(
        '단일 Ray Tracing이 실행 중입니다. 완료하거나 중단한 뒤 Batch를 시작해 주세요.',
      )
    }
    const snapshots = snapshotCadCasesForRayTrace(
      options.store.getState(),
      caseIds,
    ).sort((left, right) => left.order - right.order)
    if (snapshots.length === 0) {
      throw new Error('실행할 Case를 하나 이상 선택해 주세요.')
    }
    running = true
    options.store.getState().actions.setBatchRayTraceActive(true)
    abortController = new AbortController()
    const startedAt = now()
    state = {
      batchId: `batch-${Math.trunc(startedAt)}-${snapshots.length}`,
      status: 'running',
      currentCaseId: null,
      progress: 0,
      startedAt,
      completedAt: null,
      cases: snapshots.map((snapshot) => ({
        caseId: snapshot.caseId,
        name: snapshot.name,
        status: 'pending',
        progress: 0,
        phaseDetail: '실행 대기 중',
        jobId: null,
        runId: null,
        error: null,
        geometryCacheHit: null,
        segmentIndex: 0,
        rayMultiplier: 1,
      })),
    }
    publish()
    const promise = execute(snapshots, abortController.signal)
    activeRun = promise
    try {
      return await promise
    } catch (error) {
      if (isAbortError(error) || abortController.signal.aborted) {
        state.cases = state.cases.map((item) =>
          terminalCaseStatuses.has(item.status)
            ? item
            : {
                ...item,
                status: 'cancelled',
                progress: 1,
                phaseDetail: '사용자 중단',
              },
        )
        state.status = 'cancelled'
        state.currentCaseId = null
        state.completedAt = now()
        publish()
        return cloneState(state)
      }
      throw error
    } finally {
      abortController = null
      activeRun = null
      running = false
      options.store.getState().actions.setBatchRayTraceActive(false)
    }
  }

  const cancel = async () => {
    const controller = abortController
    if (!controller || controller.signal.aborted) return
    const runPromise = activeRun
    controller.abort()
    if (runPromise) {
      try {
        await runPromise
      } catch {
        // `run` owns final error/status publication. Cancellation only waits
        // for the execution loop to stop touching subsequent Cases.
      }
    }
    const stopFailure = state.cases.find(
      (item) => item.phaseDetail === 'Backend 중단 확인 실패',
    )
    if (stopFailure?.error) {
      throw new Error(stopFailure.error)
    }
  }

  return {
    run,
    cancel,
    getState: () => cloneState(state),
    isRunning: () => running,
  }
}
