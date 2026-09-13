// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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
      wavefront_residency: 'gpu_resident',
      gpu_resident_wavefront_attempt_count: 4,
      gpu_resident_wavefront_success_count: 4,
      gpu_cuda_hybrid_cpu_success_count: 2,
      monte_carlo_contract: 'cpu_gpu_deterministic_batch_v1',
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
    const toggle = screen.getByRole('button', { name: 'Compute device · GPU 활성 · CPU 보조' })
    const details = document.getElementById(toggle.getAttribute('aria-controls')!)!
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(details.hidden).toBe(true)
    expect(screen.queryByRole('region', { name: '연산 장치 상세 정보' })).toBeNull()
    expect(toggle.textContent).not.toContain('GPU requested')
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(details.hidden).toBe(false)
    expect(screen.getByRole('region', { name: '연산 장치 상세 정보' })).not.toBeNull()
    expect(screen.getByText('GPU requested')).not.toBeNull()
    expect(screen.getByText('CUDA batches · 4/4')).not.toBeNull()
    expect(screen.getByText('GPU Resident · 4/4')).not.toBeNull()
    expect(screen.getByText('NVIDIA RTX Test')).not.toBeNull()
    expect(screen.getByText('CPU/GPU 동일 샘플 계약')).not.toBeNull()
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(details.hidden).toBe(true)
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
    const toggle = screen.getByRole('button', { name: 'Compute device · CPU 대체 실행 · GPU 미사용' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(alert.className).toContain('bg-orange-500/8')
    fireEvent.click(toggle)
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

  it('also starts collapsed for ordinary CPU results without changing the displayed device', () => {
    render(<ComputeExecutionStatus configuredBackend="cpu" performance={{ compute_backend: 'cpu', intersection_provider: 'numba_cpu' }} />)
    const toggle = screen.getByRole('button', { name: 'Compute device · CPU 실행' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(toggle)
    expect(screen.getByText('CPU requested')).not.toBeNull()
    expect(screen.getByText('Provider · numba_cpu')).not.toBeNull()
    expect(screen.queryByText('GPU requested')).toBeNull()
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

  it('warns when an old GPU result lacks the parity contract', () => {
    render(
      <ComputeExecutionStatus
        configuredBackend="gpu_cuda"
        performance={{
          compute_backend: 'gpu_cuda',
          compute_execution_state: 'gpu_active',
          intersection_provider: 'gpu_cuda',
          gpu_cuda_gpu_attempt_count: 2,
          gpu_cuda_gpu_success_count: 2,
        }}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Compute device · GPU 활성' }))
    expect(
      screen.getByText(/동일 샘플 정확도 계약이 기록되지 않은 이전 결과/),
    ).not.toBeNull()
  })
})
