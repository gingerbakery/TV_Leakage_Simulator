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
import { createDatumEmitter } from './ray-tracing-model'
import { createEmitterAim } from './emitter-aim'

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

describe('RayTracingPanel Aim editing', () => {
  it('previews Aim, applies it, and restores the original distribution when switched off', async () => {
    const emitter = createDatumEmitter('emitter_001', [0, 0, 0], [0, 0, 0])
    emitter.direction_distribution = 'gaussian'
    const actions = workspaceStore.getState().actions
    act(() => {
      actions.addCadCase({ path: 'aim.step', displayName: 'aim.step' })
      actions.upsertEmitter(emitter)
    })
    const scene = createSceneFixture()
    render(<AppProviders><RayTracingPanel scene={scene} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    const summary = screen.getByText('Aim / Target')
    expect(summary.closest('details')?.open).toBe(false)
    fireEvent.click(summary)
    fireEvent.click(screen.getByLabelText('Enable Aim Area'))
    expect((screen.getByLabelText('Emitter direction distribution') as HTMLSelectElement).disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Aim shape'), { target: { value: 'circle' } })
    fireEvent.change(screen.getByLabelText('Aim diameter (mm)'), { target: { value: '6' } })
    await waitFor(() => expect(workspaceStore.getState().placementPreviewEmitter?.aim).toMatchObject({ enabled: true, shape: 'circle', radius_mm: 3 }))
    expect(workspaceStore.getState().emitters[0].aim).toBeUndefined()
    fireEvent.click(screen.getByRole('button', { name: 'Save Emitter' }))
    await waitFor(() => expect(workspaceStore.getState().emitters[0].aim?.radius_mm).toBe(3))
    expect(workspaceStore.getState().emitters[0].luminance_nit).toBe(500)
    expect(workspaceStore.getState().placementPreviewEmitter).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    fireEvent.click(screen.getByText('Aim / Target'))
    fireEvent.click(screen.getByLabelText('Enable Aim Area'))
    const distribution = screen.getByLabelText('Emitter direction distribution') as HTMLSelectElement
    expect(distribution.disabled).toBe(false)
    expect(distribution.value).toBe('gaussian')
    fireEvent.click(screen.getByRole('button', { name: 'Save Emitter' }))
    expect(workspaceStore.getState().emitters[0].aim?.enabled).toBe(false)
  })

  it('discards edited Target coordinates on Cancel and refuses a zero-size Target', async () => {
    const emitter = createDatumEmitter('emitter_001', [0, 0, 0], [0, 0, 0])
    emitter.aim = { ...createEmitterAim([0, 0, 30]), enabled: true }
    act(() => {
      workspaceStore.getState().actions.addCadCase({ path: 'aim.step', displayName: 'aim.step' })
      workspaceStore.getState().actions.upsertEmitter(emitter)
    })
    render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    fireEvent.click(screen.getByText('Aim / Target'))
    fireEvent.keyDown(screen.getByText('Aim / Target'), { key: 'Enter' })
    expect(screen.getByRole('button', { name: 'Save Emitter' })).not.toBeNull()
    fireEvent.change(screen.getByLabelText('Aim position X'), { target: { value: '-1.25' } })
    await waitFor(() => expect(workspaceStore.getState().placementPreviewEmitter?.aim?.center[0]).toBe(-1.25))
    fireEvent.change(screen.getByLabelText('Aim tilt Y'), { target: { value: '30' } })
    await waitFor(() => expect(workspaceStore.getState().placementPreviewEmitter?.aim?.u_axis[2]).toBeCloseTo(-0.5))
    fireEvent.click(screen.getByLabelText('Show Aim Target'))
    expect(workspaceStore.getState().placementPreviewEmitter?.aim?.show_in_viewer).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Reset Target' }))
    expect(workspaceStore.getState().placementPreviewEmitter?.aim).toMatchObject({ enabled: true, u_axis: [1, 0, 0], show_in_viewer: true })
    fireEvent.change(screen.getByLabelText('Aim width (mm)'), { target: { value: '0' } })
    expect((screen.getByRole('button', { name: 'Save Emitter' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(workspaceStore.getState().emitters[0].aim).toEqual(emitter.aim)
    await waitFor(() => expect(workspaceStore.getState().placementPreviewEmitter).toBeNull())
  })
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
