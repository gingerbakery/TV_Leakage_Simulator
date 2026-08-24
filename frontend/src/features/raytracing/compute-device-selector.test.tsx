// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ComputeDeviceSelector } from './compute-device-selector'

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

describe('compute device selector', () => {
  it('keeps equal CPU/GPU choices and a fixed-height status row across states', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <div style={{ width: 320 }}>
        <ComputeDeviceSelector
          value="cpu"
          disabled={false}
          pending={false}
          failed={false}
          onChange={onChange}
          onRetry={vi.fn()}
        />
      </div>,
    )

    const cpuButton = screen.getByRole('button', { name: 'CPU로 연산' })
    const gpuButton = screen.getByRole('button', { name: 'NVIDIA GPU로 연산' })
    expect(cpuButton.getAttribute('aria-pressed')).toBe('true')
    expect(gpuButton.getAttribute('aria-pressed')).toBe('false')
    expect(cpuButton.className).toContain('h-12')
    expect(gpuButton.className).toContain('h-12')
    expect(
      screen.getByRole('group', { name: '연산 장치 선택' }).className,
    ).toContain(
      'grid-cols-[repeat(auto-fit,minmax(min(8.25rem,100%),1fr))]',
    )
    const cpuStatus = screen.getByTestId('compute-device-status')
    expect(cpuStatus.className).toContain('h-14')
    expect(cpuStatus.textContent).toContain('CPU 선택됨 · 호환 모드')

    rerender(
      <div style={{ width: 320 }}>
        <ComputeDeviceSelector
          value="gpu_cuda"
          disabled={false}
          status={readyStatus}
          pending={false}
          failed={false}
          onChange={onChange}
          onRetry={vi.fn()}
        />
      </div>,
    )

    expect(screen.getByRole('button', { name: 'CPU로 연산' })).toBe(cpuButton)
    expect(screen.getByRole('button', { name: 'NVIDIA GPU로 연산' })).toBe(gpuButton)
    expect(cpuButton.getAttribute('aria-pressed')).toBe('false')
    expect(gpuButton.getAttribute('aria-pressed')).toBe('true')
    const gpuStatus = screen.getByTestId('compute-device-status')
    for (const fixedLayoutClass of [
      'h-14',
      'px-2.5',
      'py-1.5',
      'grid-rows-2',
    ]) {
      expect(cpuStatus.className).toContain(fixedLayoutClass)
      expect(gpuStatus.className).toContain(fixedLayoutClass)
    }
    const statusText = screen.getByText('NVIDIA GeForce RTX 3070')
    expect(statusText.className).toContain('row-start-2')
    expect(statusText.className).toContain('col-end-4')
    expect(statusText.getAttribute('data-status-detail')).toBe('device-name')
    expect(screen.getByLabelText('Selected compute device').textContent).toContain(
      '준비 완료 · NVIDIA GeForce RTX 3070',
    )
  })

  it('exposes the choices as one accessible control group', () => {
    render(
      <ComputeDeviceSelector
        value="cpu"
        disabled={false}
        pending={false}
        failed={false}
        onChange={vi.fn()}
        onRetry={vi.fn()}
      />,
    )

    const group = screen.getByRole('group', { name: '연산 장치 선택' })
    expect(group.querySelectorAll('button')).toHaveLength(2)
    expect(screen.getByRole('heading', { name: '연산 장치' })).not.toBeNull()
  })
})
