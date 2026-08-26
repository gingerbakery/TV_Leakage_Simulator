// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { RayTraceJob } from '@/api'
import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import {
  createCompletedRayTraceJobFixture,
  createRayTraceResultFixture,
} from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'

import { RayTracingPanel } from './ray-tracing-panel'

const apiHookState = vi.hoisted(() => ({
  job: undefined as RayTraceJob | undefined,
  start: vi.fn(),
  stop: vi.fn(),
}))

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    useRayTraceJobQuery: () => ({
      data: apiHookState.job,
      error: null,
    }),
    useStartRayTraceMutation: () => ({
      mutateAsync: apiHookState.start,
      isPending: false,
      error: null,
    }),
    useStopRayTraceMutation: () => ({
      mutate: apiHookState.stop,
      isPending: false,
    }),
  }
})

afterEach(() => {
  cleanup()
  apiHookState.job = undefined
  apiHookState.start.mockReset()
  apiHookState.stop.mockReset()
  workspaceStore.getState().actions.resetWorkspace()
})

describe('RayTracingPanel Auto convergence', () => {
  it('stops a closed-window retry without reusing its cancel token on a later run', async () => {
    const result = createRayTraceResultFixture()
    const emitter = { ...result.emitters[0], ray_count: 100, seed: 7 }
    const receiver = result.receivers[0]
    const actions = workspaceStore.getState().actions

    act(() => {
      actions.addCadCase({ path: 'auto.step', displayName: 'auto.step' })
      actions.upsertEmitter(emitter)
      actions.upsertReceiver(receiver)
      actions.setRayTraceConfig({
        ...result.config,
        auto_convergence: true,
        convergence_target_percent: 5,
        max_convergence_multiplier: 8,
      })
    })

    apiHookState.start
      .mockResolvedValueOnce({ job_id: 'job-1' })
      .mockResolvedValueOnce({ job_id: 'job-auto-retry' })
      .mockResolvedValueOnce({ job_id: 'job-2' })
      .mockResolvedValueOnce({ job_id: 'job-2-auto-retry' })

    const { rerender } = render(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={0}
        />
      </AppProviders>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Run Ray Tracing' }))
    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(1))

    const completed = createCompletedRayTraceJobFixture()
    completed.job_id = 'job-1'
    completed.result.total_rays = 100
    completed.total_rays = 100
    const receiverMetrics = completed.result.metrics.receiver_001 as Record<
      string,
      unknown
    >
    completed.result.metrics.receiver_001 = {
      ...receiverMetrics,
      hit_count: 100,
      error_estimate_percent: 12,
      peak_area_error_estimate_percent: 14,
    }
    apiHookState.job = completed

    rerender(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={0}
        />
      </AppProviders>,
    )

    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(2))
    expect(apiHookState.start.mock.calls[0][0].request.config).toMatchObject({
      ray_count: 100,
      seed: 42,
    })
    expect(apiHookState.start.mock.calls[0][0].request.emitters[0].seed).toBe(7)
    expect(apiHookState.start.mock.calls[1][0].request.config).toMatchObject({
      ray_count: 100,
      seed: 1_000_045,
    })
    expect(apiHookState.start.mock.calls[1][0].request.emitters[0].seed).toBe(
      1_000_010,
    )
    await waitFor(() =>
      expect(workspaceStore.getState().activeRayTraceJobId).toBe(
        'job-auto-retry',
      ),
    )

    rerender(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={1}
        />
      </AppProviders>,
    )

    await waitFor(() =>
      expect(apiHookState.stop).toHaveBeenCalledWith({
        jobId: 'job-auto-retry',
      }),
    )
    expect(workspaceStore.getState().activeRayTraceJobId).toBeNull()

    apiHookState.job = undefined
    rerender(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={1}
        />
      </AppProviders>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Run Ray Tracing' }))
    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(3))
    await waitFor(() =>
      expect(workspaceStore.getState().activeRayTraceJobId).toBe('job-2'),
    )

    const secondCompleted = createCompletedRayTraceJobFixture()
    secondCompleted.job_id = 'job-2'
    secondCompleted.result.total_rays = 100
    secondCompleted.total_rays = 100
    secondCompleted.result.metrics.receiver_001 = {
      ...(secondCompleted.result.metrics.receiver_001 as Record<
        string,
        unknown
      >),
      hit_count: 100,
      error_estimate_percent: 12,
      peak_area_error_estimate_percent: 14,
    }
    apiHookState.job = secondCompleted

    rerender(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={1}
        />
      </AppProviders>,
    )

    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(4))
    expect(apiHookState.start.mock.calls[2][0].request.config).toMatchObject({
      ray_count: 100,
      seed: 42,
    })
    expect(apiHookState.start.mock.calls[3][0].request.config).toMatchObject({
      ray_count: 100,
      seed: 1_000_045,
    })
    await waitFor(() =>
      expect(workspaceStore.getState().activeRayTraceJobId).toBe(
        'job-2-auto-retry',
      ),
    )
  })
})
