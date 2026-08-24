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
  it('stops and detaches an automatic retry when the result window is closed', async () => {
    const result = createRayTraceResultFixture()
    const emitter = { ...result.emitters[0], ray_count: 100 }
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
  })
})
