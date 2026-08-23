// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppProviders } from '@/app/providers'
import { apiClient, type GpuCudaStatus } from '@/api'
import { workspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'

import {
  createDatumEmitter,
  createDatumReceiver,
  createFaceEmitter,
} from './ray-tracing-model'
import { RayTracingPanel } from './ray-tracing-panel'

const readyStatus = {
  available: true,
  reason_code: null,
  device_name: 'NVIDIA RTX Test',
  compute_capability: '8.6',
  device_id: 0,
  numba_version: '0.66.0',
  toolkit_layout: 'windows_cuda13_x64_compat',
  strict_float64: true,
  kernel_executed: true,
  kernel_verified: true,
  preflight_scope: 'production_ray_bvh',
  provider_contract: 'strict_float64_bvh_v1',
} as const

function setupRayObjects(kind: 'datum' | 'face' | 'polygon' = 'datum') {
  const actions = workspaceStore.getState().actions
  const datum = createDatumEmitter(
    'emitter_001',
    [0, 0, 0],
    [0, 0, 0],
  )
  actions.upsertEmitter(
    kind === 'datum'
      ? datum
      : kind === 'polygon'
        ? { ...datum, surface_construction: 'polygon_auto' }
        : createFaceEmitter('emitter_001', [0]),
  )
  actions.upsertReceiver(
    createDatumReceiver('receiver_001', [0, 0, 20], [0, 0, 0]),
  )
}

function openRunOptions() {
  fireEvent.click(screen.getByText('Run Options').closest('summary')!)
}

function openAdvancedOptions() {
  fireEvent.click(screen.getByText('고급 옵션').closest('summary')!)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  workspaceStore.getState().actions.resetWorkspace()
})

describe('GPU execution UX', () => {
  it('does not probe on CPU, then preserves GPU and acceleration settings in the run request', async () => {
    setupRayObjects()
    act(() => {
      workspaceStore.getState().actions.setRayTraceConfig({
        ...workspaceStore.getState().rayTraceConfig,
        intersection_backend: 'brute_force',
      })
    })
    const queuedJob = {
      job_id: 'job-gpu-1',
      status: 'queued',
      phase: 'queued',
      processed_rays: 0,
      total_rays: 10_000,
      progress: 0,
      elapsed_sec: 0,
      estimated_remaining_sec: null,
      rays_per_sec: 0,
      created_at: 1,
    } as const
    const statusSpy = vi
      .spyOn(apiClient, 'getGpuCudaStatus')
      .mockResolvedValue(readyStatus)
    const startSpy = vi
      .spyOn(apiClient, 'startRayTrace')
      .mockResolvedValue(queuedJob)
    vi.spyOn(apiClient, 'getRayTraceJob').mockResolvedValue(queuedJob)

    render(
      <AppProviders>
        <RayTracingPanel scene={createSceneFixture()} cameraFrame={null} />
      </AppProviders>,
    )

    expect(screen.getByText('CPU 선택됨')).not.toBeNull()
    expect(
      screen.getByRole('button', { name: 'CPU로 연산' }).getAttribute(
        'aria-pressed',
      ),
    ).toBe('true')
    expect(
      screen.getByText('Run Options').closest('details')?.contains(
        screen.getByRole('button', { name: 'CPU로 연산' }),
      ),
    ).toBe(false)
    expect(statusSpy).not.toHaveBeenCalled()

    openRunOptions()
    const advancedOptions = screen.getByText('고급 옵션').closest('details')
    expect(advancedOptions?.open).toBe(false)
    openAdvancedOptions()
    expect(advancedOptions?.open).toBe(true)
    expect(screen.getByLabelText('Acceleration structure')).toHaveProperty(
      'value',
      'brute_force',
    )
    expect(
      screen.getByRole('option', {
        name: '직접 삼각형 검사 (Brute force · CPU 전용)',
      }),
    ).toHaveProperty('disabled', false)
    expect(screen.getByLabelText('Selected compute device').textContent).toContain(
      'CPU 선택됨 · 호환 모드',
    )

    fireEvent.click(screen.getByRole('button', { name: 'NVIDIA GPU로 연산' }))

    expect(await screen.findByText('NVIDIA RTX Test')).not.toBeNull()
    expect(screen.queryByText('GPU 검증 상세')).toBeNull()
    expect(screen.getByLabelText('Acceleration structure')).toHaveProperty(
      'value',
      'bvh',
    )
    expect(
      screen.getByRole('option', {
        name: '직접 삼각형 검사 (Brute force · CPU 전용)',
      }),
    ).toHaveProperty('disabled', true)
    expect(
      screen.getByText('GPU 선택 시 호환되는 고속 방식이 자동 적용됩니다.'),
    ).not.toBeNull()
    expect(screen.getByLabelText('Selected compute device').textContent).toContain(
      '준비 완료 · NVIDIA RTX Test',
    )
    expect(statusSpy).toHaveBeenCalledOnce()
    expect(statusSpy.mock.calls[0]?.[0]).toMatchObject({
      signal: expect.any(AbortSignal),
    })

    fireEvent.click(screen.getByRole('button', { name: 'Run Ray Tracing' }))

    await waitFor(() => {
      expect(startSpy).toHaveBeenCalledOnce()
    })
    const request = startSpy.mock.calls[0]?.[0]
    expect(request.config).toMatchObject({
      compute_backend: 'gpu_cuda',
      intersection_backend: 'bvh',
    })
  })

  it.each([
    ['Face', 'face'],
    ['Polygon auto', 'polygon'],
  ] as const)(
    'requires confirmation before unsupported %s emitters use the CPU scalar path',
    async (_label, emitterKind) => {
    setupRayObjects(emitterKind)
    act(() => {
      workspaceStore.getState().actions.setRayTraceConfig({
        ...workspaceStore.getState().rayTraceConfig,
        compute_backend: 'gpu_cuda',
      })
    })
    const queuedJob = {
      job_id: 'job-face-1',
      status: 'queued',
      phase: 'queued',
      processed_rays: 0,
      total_rays: 10_000,
      progress: 0,
      elapsed_sec: 0,
      estimated_remaining_sec: null,
      rays_per_sec: 0,
      created_at: 1,
    } as const
    vi.spyOn(apiClient, 'getGpuCudaStatus').mockResolvedValue(readyStatus)
    const startSpy = vi
      .spyOn(apiClient, 'startRayTrace')
      .mockResolvedValue(queuedJob)
    vi.spyOn(apiClient, 'getRayTraceJob').mockResolvedValue(queuedJob)

    render(
      <AppProviders>
        <RayTracingPanel scene={createSceneFixture()} cameraFrame={null} />
      </AppProviders>,
    )
    expect(await screen.findByText('NVIDIA RTX Test')).not.toBeNull()
    expect(screen.queryByText(/CPU 경로 Emitter 1개/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Run Ray Tracing' }))

    expect(
      await screen.findByRole('dialog', {
        name: '일부 Emitter는 CPU로 실행됩니다',
      }),
    ).not.toBeNull()
    expect(
      startSpy,
    ).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'CPU 경로 포함 실행' }))
    await waitFor(() => {
      expect(startSpy).toHaveBeenCalledOnce()
    })
    },
  )

  it('blocks an unverified GPU run and offers one-click CPU recovery', async () => {
    setupRayObjects()
    act(() => {
      workspaceStore.getState().actions.setRayTraceConfig({
        ...workspaceStore.getState().rayTraceConfig,
        compute_backend: 'gpu_cuda',
      })
    })
    const statusSpy = vi.spyOn(apiClient, 'getGpuCudaStatus').mockResolvedValue({
        ...readyStatus,
        available: false,
        reason_code: 'cuda_preflight_kernel_failed',
        kernel_executed: true,
        kernel_verified: false,
      })

    render(
      <AppProviders>
        <RayTracingPanel scene={createSceneFixture()} cameraFrame={null} />
      </AppProviders>,
    )

    expect(await screen.findByText(/GPU 자체 검사 실패/)).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Run Ray Tracing' })).toHaveProperty(
      'disabled',
      true,
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'GPU 준비 상태 다시 확인' }),
    )
    await waitFor(() => expect(statusSpy).toHaveBeenCalledTimes(2))
    expect(statusSpy.mock.calls[1]?.[0]).toEqual({ refresh: true })
    fireEvent.click(screen.getByRole('button', { name: 'CPU로 연산' }))
    expect(workspaceStore.getState().rayTraceConfig.compute_backend).toBe('cpu')
    expect(screen.getByRole('button', { name: 'Run Ray Tracing' })).toHaveProperty(
      'disabled',
      false,
    )
  })

  it('blocks an older server response that omits the production Ray/BVH scope', async () => {
    setupRayObjects()
    act(() => {
      workspaceStore.getState().actions.setRayTraceConfig({
        ...workspaceStore.getState().rayTraceConfig,
        compute_backend: 'gpu_cuda',
      })
    })
    vi.spyOn(apiClient, 'getGpuCudaStatus').mockResolvedValue({
      ...readyStatus,
      preflight_scope: undefined,
    } as unknown as GpuCudaStatus)

    render(
      <AppProviders>
        <RayTracingPanel scene={createSceneFixture()} cameraFrame={null} />
      </AppProviders>,
    )

    expect(
      await screen.findByText(/GPU 검사 버전이 호환되지 않음/),
    ).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Run Ray Tracing' })).toHaveProperty(
      'disabled',
      true,
    )
    expect(screen.getByLabelText('Selected compute device').textContent).toContain(
      'GPU 사용 불가',
    )
  })

  it('keeps Run disabled when a retry fails after a stale successful probe', async () => {
    setupRayObjects()
    act(() => {
      workspaceStore.getState().actions.setRayTraceConfig({
        ...workspaceStore.getState().rayTraceConfig,
        compute_backend: 'gpu_cuda',
      })
    })
    vi.spyOn(apiClient, 'getGpuCudaStatus')
      .mockResolvedValueOnce(readyStatus)
      .mockRejectedValueOnce(new Error('probe failed'))

    render(
      <AppProviders>
        <RayTracingPanel scene={createSceneFixture()} cameraFrame={null} />
      </AppProviders>,
    )

    expect(await screen.findByText('NVIDIA RTX Test')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Run Ray Tracing' })).toHaveProperty(
      'disabled',
      false,
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'GPU 준비 상태 다시 확인' }),
    )

    expect(await screen.findByText('상태 확인 실패')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Run Ray Tracing' })).toHaveProperty(
      'disabled',
      true,
    )
    expect(screen.getByLabelText('Selected compute device').textContent).toContain(
      'GPU 사용 불가 · 상태 확인 실패',
    )
  })
})
