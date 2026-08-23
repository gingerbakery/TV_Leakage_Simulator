// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { GpuCudaStatus } from '@/api'

import { GpuCudaReadiness } from './gpu-cuda-readiness'

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub

afterEach(cleanup)

const readyStatus = {
  available: true,
  reason_code: null,
  device_name: 'NVIDIA GeForce RTX 3070',
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

describe('GPU CUDA readiness', () => {
  it('shows the probed device and strict compute contract', () => {
    render(
      <GpuCudaReadiness
        status={readyStatus}
        pending={false}
        failed={false}
        onRetry={vi.fn()}
      />,
    )

    expect(screen.getByRole('status').textContent).toContain(
      '준비 완료 · NVIDIA GeForce RTX 3070',
    )
    expect(screen.queryByText('GPU 검증 상세')).toBeNull()
    expect(screen.queryByText('Strict FP64')).toBeNull()
    expect(screen.getByRole('button', { name: 'GPU 상세 정보' })).not.toBeNull()
    expect(screen.getByTestId('compute-device-status').className).toContain(
      'h-14',
    )
    expect(screen.getByTestId('compute-device-status').className).toContain(
      'grid-rows-2',
    )
    expect(
      screen
        .getByText('NVIDIA GeForce RTX 3070')
        .getAttribute('data-status-detail'),
    ).toBe('device-name')

    fireEvent.focus(screen.getByRole('button', { name: 'GPU 상세 정보' }))

    return waitFor(() => {
      expect(screen.getByText('GPU 검증 상세')).not.toBeNull()
      expect(
        screen.getByText(/장치 · NVIDIA GeForce RTX 3070/),
      ).not.toBeNull()
      expect(screen.getByText(/상태 · 준비 완료/)).not.toBeNull()
      expect(screen.getByText(/Compute capability · 8.6/)).not.toBeNull()
      expect(screen.getByText(/FP64 · Strict FP64/)).not.toBeNull()
      expect(screen.getByText(/Scope · production_ray_bvh/)).not.toBeNull()
    })
  })

  it('rejects an otherwise-ready response from an older server without a production scope', () => {
    render(
      <GpuCudaReadiness
        status={
          {
            ...readyStatus,
            preflight_scope: undefined,
          } as unknown as GpuCudaStatus
        }
        pending={false}
        failed={false}
        onRetry={vi.fn()}
      />,
    )

    expect(screen.getByRole('alert').textContent).toContain(
      'GPU 검사 버전이 호환되지 않음',
    )
    expect(screen.queryByText(/준비 완료/)).toBeNull()
    expect(screen.getByRole('button', { name: 'GPU 상세 정보' })).not.toBeNull()
  })

  it('explains an unavailable toolkit and exposes the friendly reason in details', async () => {
    const onRetry = vi.fn()
    render(
      <GpuCudaReadiness
        status={{
          ...readyStatus,
          available: false,
          reason_code: 'cuda_toolkit_not_found',
          device_name: null,
          compute_capability: null,
          strict_float64: false,
          kernel_verified: false,
        }}
        pending={false}
        failed={false}
        onRetry={onRetry}
      />,
    )

    expect(screen.getByRole('alert').textContent).toContain(
      'CUDA Toolkit을 찾을 수 없음',
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'GPU 준비 상태 다시 확인' }),
    )
    expect(onRetry).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: 'CPU로 전환' })).toBeNull()
    expect(screen.getByRole('button', { name: 'GPU 상세 정보' })).not.toBeNull()

    fireEvent.focus(screen.getByRole('button', { name: 'GPU 상세 정보' }))

    await waitFor(() => {
      expect(screen.getByText(/장치 · 확인되지 않음/)).not.toBeNull()
      expect(
        screen.getByText(/상태 · CUDA Toolkit을 찾을 수 없음/),
      ).not.toBeNull()
    })
  })

  it('fails closed when the strict provider contract is missing', () => {
    render(
      <GpuCudaReadiness
        status={
          {
            ...readyStatus,
            provider_contract: undefined,
          } as unknown as GpuCudaStatus
        }
        pending={false}
        failed={false}
        onRetry={vi.fn()}
      />,
    )

    expect(screen.getByRole('alert').textContent).toContain(
      'GPU 검사 버전이 호환되지 않음',
    )
    expect(screen.queryByText(/준비 완료/)).toBeNull()
  })
})
