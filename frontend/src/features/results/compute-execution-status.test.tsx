// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { resolveComputeExecution } from './compute-execution-model'
import { ComputeExecutionStatus } from './compute-execution-status'

afterEach(cleanup)

describe('compute execution status', () => {
  it('makes successful GPU execution and mixed CPU work explicit', () => {
    const performance = {
      compute_backend: 'gpu_cuda',
      intersection_provider: 'mixed',
      gpu_cuda_used: true,
      gpu_cuda_device_name: 'NVIDIA RTX Test',
      gpu_cuda_gpu_attempt_count: 4,
      gpu_cuda_gpu_success_count: 4,
      gpu_cuda_hybrid_cpu_success_count: 2,
    }

    expect(resolveComputeExecution('gpu_cuda', performance).state).toBe(
      'gpu-mixed',
    )
    render(
      <ComputeExecutionStatus
        configuredBackend="gpu_cuda"
        performance={performance}
      />,
    )

    expect(screen.getByRole('status').textContent).toContain(
      'Compute device · GPU 활성 · CPU 보조',
    )
    expect(screen.getByText('CUDA batches · 4/4')).not.toBeNull()
    expect(screen.getByText('NVIDIA RTX Test')).not.toBeNull()
  })

  it('warns when a GPU request fell back with zero CUDA batches', () => {
    const performance = {
      compute_backend: 'gpu_cuda',
      intersection_provider: 'python_cpu',
      gpu_cuda_used: false,
      gpu_cuda_gpu_attempt_count: 0,
      gpu_cuda_gpu_success_count: 0,
      intersection_provider_unavailable_reason: 'cuda_driver_unavailable',
    }

    expect(resolveComputeExecution('gpu_cuda', performance).state).toBe(
      'gpu-fallback',
    )
    render(
      <ComputeExecutionStatus
        configuredBackend="gpu_cuda"
        performance={performance}
      />,
    )

    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('CPU 대체 실행 · GPU 미사용')
    expect(alert.textContent).toContain('CUDA batches · 0/0')
    expect(alert.textContent).toContain('NVIDIA 드라이버 사용 불가')
  })

  it('distinguishes a missing GPU execution record from normal CPU mode', () => {
    expect(
      resolveComputeExecution('gpu_cuda', {
        compute_backend: 'gpu_cuda',
        intersection_provider: 'gpu_cuda',
        gpu_cuda_used: false,
      }).state,
    ).toBe('gpu-zero')
    expect(
      resolveComputeExecution('cpu', {
        compute_backend: 'cpu',
        intersection_provider: 'numba_cpu',
      }).state,
    ).toBe('cpu')
  })

  it('prefers the backend execution verdict while retaining legacy derivation', () => {
    const summary = resolveComputeExecution('gpu_cuda', {
      compute_backend: 'gpu_cuda',
      compute_execution_state: 'gpu_requested_cpu_only',
      compute_execution_reason: 'gpu_cuda_scalar_uses_python_cpu',
      intersection_provider: 'python_cpu',
      gpu_cuda_gpu_attempt_count: 0,
      gpu_cuda_gpu_success_count: 0,
    })

    expect(summary.state).toBe('gpu-fallback')
    expect(summary.reason).toContain('Emitter 형식은 CUDA batch를 지원하지 않아')
  })
})
