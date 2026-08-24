// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { GpuCudaHelpDialog } from './gpu-cuda-help-dialog'

afterEach(cleanup)

describe('GPU CUDA help dialog', () => {
  it('keeps setup guidance behind an accessible icon button', async () => {
    render(<GpuCudaHelpDialog />)

    expect(screen.queryByText('CUDA Toolkit 13.1')).toBeNull()

    const trigger = screen.getByRole('button', {
      name: 'GPU CUDA 가속 도움말 열기',
    })
    fireEvent.click(trigger)

    const dialog = await screen.findByRole('dialog', {
      name: 'NVIDIA CUDA GPU 가속',
    })
    expect(dialog).not.toBeNull()
    expect(screen.getByText('요구사항')).not.toBeNull()
    expect(screen.getByText('설치 · 검증')).not.toBeNull()
    expect(screen.getByText('안전한 fallback')).not.toBeNull()
    expect(screen.getByText('CHECK_GPU_CUDA.bat')).not.toBeNull()
    expect(screen.getByText('docs/gpu-cuda-user-guide.md')).not.toBeNull()

    fireEvent.keyDown(document, { key: 'Escape' })

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(document.activeElement).toBe(trigger)
    })
  })

  it('closes from the visible confirmation action', async () => {
    render(<GpuCudaHelpDialog />)

    fireEvent.click(
      screen.getByRole('button', { name: 'GPU CUDA 가속 도움말 열기' }),
    )
    expect(await screen.findByRole('dialog')).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '확인' }))

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
    })
  })
})
