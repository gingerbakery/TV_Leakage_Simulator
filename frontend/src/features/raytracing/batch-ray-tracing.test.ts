import { describe, expect, it, vi } from 'vitest'

import type {
  GpuCudaStatus,
  RayTraceJob,
  RayTraceRequest,
  RayTraceResult,
} from '@/api'
import {
  createWorkspaceStore,
  type WorkspaceStoreApi,
} from '@/stores'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'

import {
  createBatchRayTracingController,
  type BatchRayTracingControllerOptions,
} from './batch-ray-tracing'

type BatchClient = NonNullable<BatchRayTracingControllerOptions['client']>

const readyGpuStatus: GpuCudaStatus = {
  available: true,
  reason_code: null,
  device_name: 'Test GPU',
  compute_capability: '9.0',
  device_id: 0,
  numba_version: 'test',
  toolkit_layout: 'test',
  strict_float64: true,
  kernel_executed: true,
  kernel_verified: true,
  preflight_scope: 'production_ray_bvh',
  provider_contract: 'strict_float64_bvh_v1',
}

function queuedJob(jobId: string, totalRays = 10_000): RayTraceJob {
  return {
    job_id: jobId,
    status: 'queued',
    phase: 'queued',
    processed_rays: 0,
    total_rays: totalRays,
    progress: 0,
    elapsed_sec: 0,
    estimated_remaining_sec: null,
    rays_per_sec: 0,
    created_at: 1,
  }
}

function cancelledJob(jobId: string): RayTraceJob {
  return {
    job_id: jobId,
    status: 'cancelled',
    phase: 'stopped',
    processed_rays: 0,
    total_rays: 10_000,
    progress: 0,
    elapsed_sec: 0,
    estimated_remaining_sec: null,
    rays_per_sec: 0,
    created_at: 1,
    completed_at: 2,
  }
}

function failedJob(jobId: string, error: string): RayTraceJob {
  return {
    job_id: jobId,
    status: 'failed',
    phase: 'failed',
    processed_rays: 0,
    total_rays: 10_000,
    progress: 1,
    elapsed_sec: 0,
    estimated_remaining_sec: 0,
    rays_per_sec: 0,
    created_at: 1,
    completed_at: 2,
    error,
  }
}

function resultForRequest(
  request: RayTraceRequest,
  runId: string,
): RayTraceResult {
  const result = createRayTraceResultFixture()
  result.run_id = runId
  result.config = {
    ...result.config,
    ...structuredClone(request.config),
  }
  result.emitters = structuredClone(request.emitters)
  result.receivers = structuredClone(request.receivers)
  result.total_rays = request.emitters
    .filter((emitter) => emitter.enabled)
    .reduce((sum, emitter) => sum + emitter.ray_count, 0)
  return result
}

function completedJob(
  jobId: string,
  request: RayTraceRequest,
): RayTraceJob {
  const result = resultForRequest(request, `run-${jobId}`)
  return {
    job_id: jobId,
    status: 'completed',
    phase: 'completed',
    processed_rays: result.total_rays,
    total_rays: result.total_rays,
    progress: 1,
    elapsed_sec: result.runtime_sec,
    estimated_remaining_sec: 0,
    rays_per_sec: result.total_rays / result.runtime_sec,
    created_at: 1,
    completed_at: 2,
    geometry_cache_hit: true,
    result,
  }
}

function createClient(
  overrides: Partial<BatchClient> = {},
): BatchClient {
  return {
    getScene: vi.fn(async () => structuredClone(createSceneFixture())),
    getGpuCudaStatus: vi.fn(async () => readyGpuStatus),
    startRayTrace: vi.fn(async (request) =>
      queuedJob('job-default', request.emitters[0]?.ray_count)),
    getRayTraceJob: vi.fn(async () => {
      throw new Error('Unexpected Ray Tracing status request')
    }),
    stopRayTrace: vi.fn(async (jobId) => cancelledJob(jobId)),
    ...overrides,
  }
}

function addConfiguredCase(
  store: WorkspaceStoreApi,
  path: string,
  options: {
    computeBackend?: 'cpu' | 'gpu_cuda'
    autoConvergence?: boolean
    maxConvergenceMultiplier?: number
  } = {},
): string {
  const actions = store.getState().actions
  actions.addCadCase({ path, displayName: path })
  const caseId = store.getState().activeCadCaseId!
  const fixture = createRayTraceResultFixture()
  actions.upsertEmitter(fixture.emitters[0])
  actions.upsertReceiver(fixture.receivers[0])
  actions.setRayTraceConfig({
    ...store.getState().rayTraceConfig,
    compute_backend: options.computeBackend ?? 'cpu',
    auto_convergence: options.autoConvergence ?? false,
    max_convergence_multiplier: options.maxConvergenceMultiplier ?? 8,
    seed: 42,
  })
  return caseId
}

const immediateSleep = async (_milliseconds: number, signal: AbortSignal) => {
  if (!signal.aborted) return
  const error = new Error('aborted')
  error.name = 'AbortError'
  throw error
}

describe('batch Ray Tracing controller', () => {
  it('runs Cases sequentially, reuses an identical ordered CAD scene, and saves by Case ID', async () => {
    const store = createWorkspaceStore()
    const firstCaseId = addConfiguredCase(store, 'shared.step')
    let actions = store.getState().actions
    actions.addAccessoryCad(firstCaseId, {
      path: 'shared-accessory.step',
      displayName: 'shared-accessory.step',
    })
    actions.addAccessoryCad(firstCaseId, {
      path: 'first-hidden.step',
      displayName: 'first-hidden.step',
    })
    const firstHidden = store.getState().cadCases
      .find((item) => item.caseId === firstCaseId)!
      .accessoryCads.at(-1)!
    actions.setAccessoryCadVisible(
      firstCaseId,
      firstHidden.accessoryId,
      false,
    )

    const secondCaseId = addConfiguredCase(store, 'shared.step')
    actions = store.getState().actions
    actions.addAccessoryCad(secondCaseId, {
      path: 'shared-accessory.step',
      displayName: 'shared-accessory.step',
    })
    actions.addAccessoryCad(secondCaseId, {
      path: 'second-hidden.step',
      displayName: 'second-hidden.step',
    })
    const secondHidden = store.getState().cadCases
      .find((item) => item.caseId === secondCaseId)!
      .accessoryCads.at(-1)!
    actions.setAccessoryCadVisible(
      secondCaseId,
      secondHidden.accessoryId,
      false,
    )

    const events: string[] = []
    const requests = new Map<string, RayTraceRequest>()
    const getScene = vi.fn(async () => {
      events.push('scene')
      return structuredClone(createSceneFixture())
    })
    const startRayTrace = vi.fn(async (request: RayTraceRequest) => {
      const jobId = `job-${requests.size + 1}`
      requests.set(jobId, structuredClone(request))
      events.push(`start:${jobId}`)
      return queuedJob(jobId, request.emitters[0]?.ray_count)
    })
    const getRayTraceJob = vi.fn(async (jobId: string) => {
      events.push(`status:${jobId}`)
      return completedJob(jobId, requests.get(jobId)!)
    })
    const stateChanges: string[] = []
    const client = createClient({
      getScene,
      startRayTrace,
      getRayTraceJob,
    })
    const controller = createBatchRayTracingController({
      store,
      client,
      sleep: immediateSleep,
      now: () => 123,
      onStateChange: (state) => stateChanges.push(state.status),
    })

    const finalState = await controller.run([firstCaseId, secondCaseId])

    expect(events).toEqual([
      'scene',
      'start:job-1',
      'status:job-1',
      'start:job-2',
      'status:job-2',
    ])
    expect(getScene).toHaveBeenCalledTimes(1)
    expect(getScene).toHaveBeenCalledWith(
      'shared.step',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ['shared-accessory.step'],
    )
    expect([...requests.values()].map((request) => request.scene_token))
      .toEqual(['fixture-token', 'fixture-token'])
    expect(finalState.status).toBe('completed')
    expect(finalState.progress).toBe(1)
    expect(finalState.cases.map((item) => item.status))
      .toEqual(['completed', 'completed'])
    expect(stateChanges.at(-1)).toBe('completed')
    expect(store.getState().activeCadCaseId).toBe(secondCaseId)
    expect(store.getState().cadCases.find(
      (item) => item.caseId === firstCaseId,
    )?.latestResult?.run_id).toBe('run-job-1')
    expect(store.getState().cadCases.find(
      (item) => item.caseId === secondCaseId,
    )?.latestResult?.run_id).toBe('run-job-2')
    expect(store.getState().cadCases.map((item) => item.batchJobId ?? null))
      .toEqual([null, null])
  })

  it('always runs selected Cases in their visible Case order', async () => {
    const store = createWorkspaceStore()
    const firstCaseId = addConfiguredCase(store, 'first.step')
    const secondCaseId = addConfiguredCase(store, 'second.step')
    const requests = new Map<string, RayTraceRequest>()
    const startRayTrace = vi.fn(async (request: RayTraceRequest) => {
      const jobId = `job-${requests.size + 1}`
      requests.set(jobId, structuredClone(request))
      return queuedJob(jobId)
    })
    const controller = createBatchRayTracingController({
      store,
      client: createClient({
        startRayTrace,
        getRayTraceJob: vi.fn(async (jobId: string) =>
          completedJob(jobId, requests.get(jobId)!)),
      }),
      sleep: immediateSleep,
    })

    const result = await controller.run([secondCaseId, firstCaseId])

    expect(result.cases.map((item) => item.caseId)).toEqual([
      firstCaseId,
      secondCaseId,
    ])
    expect(store.getState().batchRayTraceActive).toBe(false)
  })

  it('stops a non-terminal job after a polling failure before continuing', async () => {
    const store = createWorkspaceStore()
    const firstCaseId = addConfiguredCase(store, 'first.step')
    const secondCaseId = addConfiguredCase(store, 'second.step')
    const requests = new Map<string, RayTraceRequest>()
    const startRayTrace = vi.fn(async (request: RayTraceRequest) => {
      const jobId = `job-${requests.size + 1}`
      requests.set(jobId, structuredClone(request))
      return queuedJob(jobId)
    })
    const stopRayTrace = vi.fn(async (jobId: string) => cancelledJob(jobId))
    const controller = createBatchRayTracingController({
      store,
      client: createClient({
        startRayTrace,
        getRayTraceJob: vi.fn(async (jobId: string) => {
          if (jobId === 'job-1') throw new Error('polling disconnected')
          return completedJob(jobId, requests.get(jobId)!)
        }),
        stopRayTrace,
      }),
      sleep: immediateSleep,
    })

    const result = await controller.run([firstCaseId, secondCaseId])

    expect(stopRayTrace).toHaveBeenCalledWith('job-1')
    expect(startRayTrace).toHaveBeenCalledTimes(2)
    expect(result.cases.map((item) => item.status)).toEqual([
      'failed',
      'completed',
    ])
  })

  it('waits for confirmed backend termination before starting the next Case', async () => {
    const store = createWorkspaceStore()
    const firstCaseId = addConfiguredCase(store, 'first.step')
    const secondCaseId = addConfiguredCase(store, 'second.step')
    const requests = new Map<string, RayTraceRequest>()
    const events: string[] = []
    let firstStatusCalls = 0
    const startRayTrace = vi.fn(async (request: RayTraceRequest) => {
      const jobId = `job-${requests.size + 1}`
      requests.set(jobId, structuredClone(request))
      events.push(`start:${jobId}`)
      return queuedJob(jobId)
    })
    const controller = createBatchRayTracingController({
      store,
      client: createClient({
        startRayTrace,
        getRayTraceJob: vi.fn(async (jobId: string) => {
          if (jobId === 'job-1') {
            firstStatusCalls += 1
            if (firstStatusCalls === 1) {
              events.push('poll-error:job-1')
              throw new Error('polling disconnected')
            }
            if (firstStatusCalls === 2) {
              events.push('stopping:job-1')
              return queuedJob(jobId)
            }
            events.push('stopped:job-1')
            return cancelledJob(jobId)
          }
          events.push('completed:job-2')
          return completedJob(jobId, requests.get(jobId)!)
        }),
        stopRayTrace: vi.fn(async (jobId: string) => {
          events.push(`stop-request:${jobId}`)
          return queuedJob(jobId)
        }),
      }),
      sleep: immediateSleep,
    })

    const result = await controller.run([firstCaseId, secondCaseId])

    expect(events).toEqual([
      'start:job-1',
      'poll-error:job-1',
      'stop-request:job-1',
      'stopping:job-1',
      'stopped:job-1',
      'start:job-2',
      'completed:job-2',
    ])
    expect(result.cases.map((item) => item.status)).toEqual([
      'failed',
      'completed',
    ])
  })

  it('does not start another Case when backend stop cannot be confirmed', async () => {
    const store = createWorkspaceStore()
    const firstCaseId = addConfiguredCase(store, 'first.step')
    const secondCaseId = addConfiguredCase(store, 'second.step')
    const startRayTrace = vi.fn(async () => queuedJob('unsafe-job'))
    const controller = createBatchRayTracingController({
      store,
      client: createClient({
        startRayTrace,
        getRayTraceJob: vi.fn(async () => {
          throw new Error('polling disconnected')
        }),
        stopRayTrace: vi.fn(async () => {
          throw new Error('stop disconnected')
        }),
      }),
      sleep: immediateSleep,
    })

    const result = await controller.run([firstCaseId, secondCaseId])

    expect(startRayTrace).toHaveBeenCalledTimes(1)
    expect(result.status).toBe('completed_with_errors')
    expect(result.cases[0]).toMatchObject({
      status: 'failed',
      error: expect.stringContaining('중단을 확인하지 못해'),
    })
    expect(result.cases[1]).toMatchObject({
      status: 'cancelled',
      phaseDetail: '이전 작업 중단 확인 불가로 실행 취소',
    })
  })

  it('continues with the next Case after an individual Case fails', async () => {
    const store = createWorkspaceStore()
    const firstCaseId = addConfiguredCase(store, 'shared.step')
    const secondCaseId = addConfiguredCase(store, 'shared.step')
    const requests = new Map<string, RayTraceRequest>()
    const startRayTrace = vi.fn(async (request: RayTraceRequest) => {
      const jobId = `job-${requests.size + 1}`
      requests.set(jobId, structuredClone(request))
      return queuedJob(jobId)
    })
    const client = createClient({
      startRayTrace,
      getRayTraceJob: vi.fn(async (jobId: string) =>
        jobId === 'job-1'
          ? failedJob(jobId, 'first Case failed')
          : completedJob(jobId, requests.get(jobId)!)),
    })
    const controller = createBatchRayTracingController({
      store,
      client,
      sleep: immediateSleep,
    })

    const finalState = await controller.run([firstCaseId, secondCaseId])

    expect(finalState.status).toBe('completed_with_errors')
    expect(finalState.cases[0]).toMatchObject({
      caseId: firstCaseId,
      status: 'failed',
      error: 'first Case failed',
    })
    expect(finalState.cases[1]).toMatchObject({
      caseId: secondCaseId,
      status: 'completed',
      error: null,
    })
    expect(startRayTrace).toHaveBeenCalledTimes(2)
    expect(store.getState().cadCases.find(
      (item) => item.caseId === secondCaseId,
    )?.latestResult?.run_id).toBe('run-job-2')
    expect(store.getState().cadCases.map((item) => item.batchJobId ?? null))
      .toEqual([null, null])
  })

  it('cancels the active backend job and marks the remaining Cases cancelled', async () => {
    const store = createWorkspaceStore()
    const firstCaseId = addConfiguredCase(store, 'first.step')
    const secondCaseId = addConfiguredCase(store, 'second.step')
    const startRayTrace = vi.fn(async () => queuedJob('job-running'))
    const stopRayTrace = vi.fn(async (jobId: string) => cancelledJob(jobId))
    const waitForAbort = (_milliseconds: number, signal: AbortSignal) =>
      new Promise<void>((_resolve, reject) => {
        const rejectAbort = () => {
          const error = new Error('aborted')
          error.name = 'AbortError'
          reject(error)
        }
        if (signal.aborted) rejectAbort()
        else signal.addEventListener('abort', rejectAbort, { once: true })
      })
    const client = createClient({ startRayTrace, stopRayTrace })
    const controller = createBatchRayTracingController({
      store,
      client,
      sleep: waitForAbort,
    })

    const run = controller.run([firstCaseId, secondCaseId])
    await vi.waitFor(() => expect(startRayTrace).toHaveBeenCalledTimes(1))
    await controller.cancel()
    const finalState = await run

    expect(stopRayTrace).toHaveBeenCalledWith('job-running')
    expect(startRayTrace).toHaveBeenCalledTimes(1)
    expect(finalState.status).toBe('cancelled')
    expect(finalState.cases.map((item) => item.status))
      .toEqual(['cancelled', 'cancelled'])
    expect(finalState.cases[1].phaseDetail).toBe('실행 전 중단')
    expect(controller.isRunning()).toBe(false)
    expect(store.getState().cadCases.map((item) => item.batchJobId ?? null))
      .toEqual([null, null])
  })

  it('stops a backend job that is returned after cancellation raced with start', async () => {
    const store = createWorkspaceStore()
    const caseId = addConfiguredCase(store, 'start-race.step')
    let releaseStart!: (job: RayTraceJob) => void
    const startRayTrace = vi.fn(() => new Promise<RayTraceJob>((resolve) => {
      releaseStart = resolve
    }))
    const stopRayTrace = vi.fn(async (jobId: string) => cancelledJob(jobId))
    const client = createClient({ startRayTrace, stopRayTrace })
    const controller = createBatchRayTracingController({
      store,
      client,
      sleep: immediateSleep,
    })

    const run = controller.run([caseId])
    await vi.waitFor(() => expect(startRayTrace).toHaveBeenCalledTimes(1))
    const cancel = controller.cancel()
    releaseStart(queuedJob('late-job'))
    await cancel
    const finalState = await run

    expect(stopRayTrace).toHaveBeenCalledWith('late-job')
    expect(finalState.status).toBe('cancelled')
    expect(finalState.cases[0].status).toBe('cancelled')
  })

  it('fails closed when a user cancellation cannot confirm backend termination', async () => {
    const store = createWorkspaceStore()
    const caseId = addConfiguredCase(store, 'unsafe-cancel.step')
    const waitForAbort = (_milliseconds: number, signal: AbortSignal) =>
      new Promise<void>((_resolve, reject) => {
        const rejectAbort = () => {
          const error = new Error('aborted')
          error.name = 'AbortError'
          reject(error)
        }
        if (signal.aborted) rejectAbort()
        else signal.addEventListener('abort', rejectAbort, { once: true })
      })
    const startRayTrace = vi.fn(async () => queuedJob('unsafe-cancel-job'))
    const controller = createBatchRayTracingController({
      store,
      client: createClient({
        startRayTrace,
        stopRayTrace: vi.fn(async () => {
          throw new Error('stop channel unavailable')
        }),
      }),
      sleep: waitForAbort,
    })

    const run = controller.run([caseId])
    await vi.waitFor(() => expect(startRayTrace).toHaveBeenCalledTimes(1))
    await expect(controller.cancel()).rejects.toThrow('중단을 확인하지 못해')
    const finalState = await run

    expect(finalState.status).toBe('completed_with_errors')
    expect(finalState.cases[0]).toMatchObject({
      status: 'failed',
      phaseDetail: 'Backend 중단 확인 실패',
      error: expect.stringContaining('stop channel unavailable'),
    })
    expect(store.getState().batchRayTraceActive).toBe(false)
  })

  it('uses incremental auto-convergence segments and stores the exact merged result', async () => {
    const store = createWorkspaceStore()
    const caseId = addConfiguredCase(store, 'auto.step', {
      autoConvergence: true,
      maxConvergenceMultiplier: 2,
    })
    const requests = new Map<string, RayTraceRequest>()
    const startRayTrace = vi.fn(async (request: RayTraceRequest) => {
      const jobId = `job-${requests.size + 1}`
      requests.set(jobId, structuredClone(request))
      return queuedJob(jobId, request.emitters[0]?.ray_count)
    })
    const client = createClient({
      startRayTrace,
      getRayTraceJob: vi.fn(async (jobId: string) =>
        completedJob(jobId, requests.get(jobId)!)),
    })
    const controller = createBatchRayTracingController({
      store,
      client,
      sleep: immediateSleep,
    })

    const finalState = await controller.run([caseId])

    expect(startRayTrace).toHaveBeenCalledTimes(2)
    expect([...requests.values()].map((request) => request.config.seed))
      .toEqual([42, 1_000_045])
    expect([...requests.values()].map(
      (request) => request.emitters[0].ray_count,
    )).toEqual([10_000, 10_000])
    const result = store.getState().cadCases[0].latestResult!
    expect(result.total_rays).toBe(20_000)
    expect(result.emitters[0].ray_count).toBe(20_000)
    expect(result.metrics._convergence_accumulation).toMatchObject({
      segment_count: 2,
      segment_rays: [10_000, 10_000],
      total_rays: 20_000,
    })
    expect(result.metrics._convergence_history).toHaveLength(2)
    expect(finalState.cases[0]).toMatchObject({
      status: 'completed',
      segmentIndex: 1,
      rayMultiplier: 1,
    })
  })

  it('keeps Case and overall progress monotonic across auto-convergence segments', async () => {
    const store = createWorkspaceStore()
    const caseId = addConfiguredCase(store, 'auto-progress.step', {
      autoConvergence: true,
      maxConvergenceMultiplier: 4,
    })
    store.getState().actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      convergence_target_percent: 0.000_001,
    })
    const requests = new Map<string, RayTraceRequest>()
    const startRayTrace = vi.fn(async (request: RayTraceRequest) => {
      const jobId = `progress-${requests.size + 1}`
      requests.set(jobId, structuredClone(request))
      return queuedJob(jobId, request.emitters[0]?.ray_count)
    })
    const progressSnapshots: Array<{
      caseProgress: number
      overallProgress: number
    }> = []
    const controller = createBatchRayTracingController({
      store,
      client: createClient({
        startRayTrace,
        getRayTraceJob: vi.fn(async (jobId: string) =>
          completedJob(jobId, requests.get(jobId)!)),
      }),
      sleep: immediateSleep,
      onStateChange: (nextState) => {
        const caseState = nextState.cases[0]
        if (!caseState) return
        progressSnapshots.push({
          caseProgress: caseState.progress,
          overallProgress: nextState.progress,
        })
      },
    })

    await controller.run([caseId])

    expect(startRayTrace).toHaveBeenCalledTimes(3)
    for (let index = 1; index < progressSnapshots.length; index += 1) {
      expect(progressSnapshots[index].caseProgress).toBeGreaterThanOrEqual(
        progressSnapshots[index - 1].caseProgress,
      )
      expect(progressSnapshots[index].overallProgress).toBeGreaterThanOrEqual(
        progressSnapshots[index - 1].overallProgress,
      )
    }
    expect(progressSnapshots.some(
      ({ caseProgress }) => Math.abs(caseProgress - 0.25) < 1e-9,
    )).toBe(true)
    expect(progressSnapshots.some(
      ({ caseProgress }) => Math.abs(caseProgress - 0.5) < 1e-9,
    )).toBe(true)
    expect(progressSnapshots.at(-1)).toEqual({
      caseProgress: 1,
      overallProgress: 1,
    })
  })

  it('does not publish a partial convergence result when a later segment fails', async () => {
    const store = createWorkspaceStore()
    const caseId = addConfiguredCase(store, 'partial.step', {
      autoConvergence: true,
      maxConvergenceMultiplier: 2,
    })
    const requests = new Map<string, RayTraceRequest>()
    const startRayTrace = vi.fn(async (request: RayTraceRequest) => {
      if (startRayTrace.mock.calls.length === 2) {
        throw new Error('second segment failed')
      }
      requests.set('segment-1', structuredClone(request))
      return queuedJob('segment-1')
    })
    const client = createClient({
      startRayTrace,
      getRayTraceJob: vi.fn(async () =>
        completedJob('segment-1', requests.get('segment-1')!)),
    })
    const controller = createBatchRayTracingController({
      store,
      client,
      sleep: immediateSleep,
    })

    const finalState = await controller.run([caseId])

    expect(startRayTrace).toHaveBeenCalledTimes(2)
    expect(finalState).toMatchObject({
      status: 'completed_with_errors',
      cases: [{ status: 'failed', error: 'second segment failed' }],
    })
    expect(store.getState().cadCases[0].latestResult).toBeNull()
  })

  it('fails GPU Cases closed without CPU fallback while still running CPU Cases', async () => {
    const store = createWorkspaceStore()
    const gpuCaseId = addConfiguredCase(store, 'gpu.step', {
      computeBackend: 'gpu_cuda',
    })
    const cpuCaseId = addConfiguredCase(store, 'cpu.step')
    const unavailableGpu: GpuCudaStatus = {
      ...readyGpuStatus,
      available: false,
      reason_code: 'cuda_driver_missing',
      strict_float64: false,
      kernel_executed: false,
      kernel_verified: false,
    }
    const requests = new Map<string, RayTraceRequest>()
    const startRayTrace = vi.fn(async (request: RayTraceRequest) => {
      requests.set('cpu-job', structuredClone(request))
      return queuedJob('cpu-job')
    })
    const getGpuCudaStatus = vi.fn(async () => unavailableGpu)
    const client = createClient({
      getGpuCudaStatus,
      startRayTrace,
      getRayTraceJob: vi.fn(async () =>
        completedJob('cpu-job', requests.get('cpu-job')!)),
    })
    const controller = createBatchRayTracingController({
      store,
      client,
      sleep: immediateSleep,
    })

    const finalState = await controller.run([gpuCaseId, cpuCaseId])

    expect(getGpuCudaStatus).toHaveBeenCalledTimes(1)
    expect(startRayTrace).toHaveBeenCalledTimes(1)
    expect(requests.get('cpu-job')?.config.compute_backend).toBe('cpu')
    expect(finalState.status).toBe('completed_with_errors')
    expect(finalState.cases[0]).toMatchObject({
      status: 'failed',
      phaseDetail: 'GPU 준비 검사 실패',
    })
    expect(finalState.cases[0].error).toContain('CPU fallback 없이')
    expect(finalState.cases[1].status).toBe('completed')
  })

  it('fails closed when enabled preview constraints have no Case-specific provider', async () => {
    const store = createWorkspaceStore()
    const caseId = addConfiguredCase(store, 'constraints.step')
    const actions = store.getState().actions
    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      apply_preview_blockers: true,
      apply_allowed_areas: true,
    })
    actions.setCadCaseLeakPreviewState(caseId, undefined)
    actions.setCadCaseLeakPreviewState(caseId, undefined)
    const startRayTrace = vi.fn(async () => queuedJob('unexpected'))
    const client = createClient({ startRayTrace })
    const controller = createBatchRayTracingController({
      store,
      client,
      sleep: immediateSleep,
    })

    const finalState = await controller.run([caseId])

    expect(startRayTrace).not.toHaveBeenCalled()
    expect(finalState).toMatchObject({
      status: 'completed_with_errors',
      cases: [{
        status: 'failed',
        phaseDetail: '보조 형상 확인 실패',
      }],
    })
    expect(finalState.cases[0].error).toContain('Case별 보조 형상 데이터')
  })

  it('passes persisted Case-specific preview constraints without an external provider', async () => {
    const store = createWorkspaceStore()
    const caseId = addConfiguredCase(store, 'constraints.step')
    const actions = store.getState().actions
    actions.setRayTraceConfig({
      ...store.getState().rayTraceConfig,
      apply_preview_blockers: true,
    })
    actions.setCadCaseLeakPreviewState(caseId, {
      sourceMode: 'face', sourceFaceIds: [], sourceComponentIds: [], sourceBodyFaceCount: 0,
      quality: 'balanced', directions: ['pos_z'], visualizationVisible: true,
      ignoreAreasVisible: true, blockersVisible: true, ignoreAreas: [],
      blockers: [{
        id: 'batch-blocker', label: 'Batch blocker', enabled: true,
        baseCenter: [0, 0, 0], uAxis: [1, 0, 0], vAxis: [0, 1, 0], normal: [0, 0, 1],
        widthMm: 10, heightMm: 20, offsetMm: 0, depthMm: 1, reverse: false,
      }],
    })
    let request: RayTraceRequest | null = null
    const startRayTrace = vi.fn(async (next: RayTraceRequest) => {
      request = structuredClone(next)
      return queuedJob('constraint-job')
    })
    const client = createClient({
      startRayTrace,
      getRayTraceJob: vi.fn(async () =>
        completedJob('constraint-job', request!)),
    })
    const controller = createBatchRayTracingController({
      store,
      client,
      sleep: immediateSleep,
    })

    const finalState = await controller.run([caseId])

    expect(finalState.status).toBe('completed')
    expect(startRayTrace.mock.calls[0][0].preview_blockers).toEqual([{
      blocker_id: 'precision-blocker:batch-blocker',
      center: [0, 0, 0.5],
      u_axis: [1, 0, 0],
      v_axis: [0, 1, 0],
      normal: [0, 0, 1],
      width_mm: 10,
      height_mm: 20,
      depth_mm: 1,
      enabled: true,
    }])
  })

  it('reloads the scene token once when the backend scene cache expired', async () => {
    const store = createWorkspaceStore()
    const caseId = addConfiguredCase(store, 'retry.step')
    const firstScene = createSceneFixture()
    firstScene.metadata.scene_token = 'expired-token'
    const refreshedScene = createSceneFixture()
    refreshedScene.metadata.scene_token = 'fresh-token'
    const getScene = vi.fn()
      .mockResolvedValueOnce(firstScene)
      .mockResolvedValueOnce(refreshedScene)
    let successfulRequest: RayTraceRequest | null = null
    const startRayTrace = vi.fn(async (request: RayTraceRequest) => {
      if (startRayTrace.mock.calls.length === 1) {
        throw new Error('CAD scene cache expired')
      }
      successfulRequest = structuredClone(request)
      return queuedJob('retry-job')
    })
    const client = createClient({
      getScene,
      startRayTrace,
      getRayTraceJob: vi.fn(async () =>
        completedJob('retry-job', successfulRequest!)),
    })
    const controller = createBatchRayTracingController({
      store,
      client,
      sleep: immediateSleep,
    })

    const finalState = await controller.run([caseId])

    expect(getScene).toHaveBeenCalledTimes(2)
    expect(startRayTrace).toHaveBeenCalledTimes(2)
    expect(startRayTrace.mock.calls[0][0].scene_token).toBe('expired-token')
    expect(startRayTrace.mock.calls[1][0].scene_token).toBe('fresh-token')
    expect(finalState.status).toBe('completed')
  })
})
