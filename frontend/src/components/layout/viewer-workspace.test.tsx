// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { workspaceStore } from '@/stores'

import { ViewerWorkspace } from './viewer-workspace'

afterEach(() => {
  cleanup()
  workspaceStore.getState().actions.resetWorkspace()
})

describe('ViewerWorkspace display controls', () => {
  it('keeps the viewer toolbar bounded and wraps camera presets on narrow screens', () => {
    const view = render(<ViewerWorkspace />)
    const toolbar = view.container.querySelector<HTMLElement>(
      '[data-viewer-toolbar]',
    )
    const cameraPresets = screen.getByLabelText('Camera presets')
    const renderModes = screen.getByLabelText('Render modes')

    expect(toolbar?.className).toContain('w-full')
    expect(toolbar?.className).toContain('min-w-0')
    expect(cameraPresets.className).toContain('grid-cols-4')
    expect(cameraPresets.className).toContain('min-[390px]:flex')
    expect(renderModes.className).toContain('max-w-full')
    expect(renderModes.className).toContain('flex-wrap')
  })

  it('enables surface transparency only for surface render modes', () => {
    render(<ViewerWorkspace />)

    const transparencySlider = screen.getByRole('slider', {
      name: 'Surface transparency',
    }) as HTMLInputElement

    expect(transparencySlider.disabled).toBe(false)
    expect(transparencySlider.value).toBe('0')

    fireEvent.change(transparencySlider, {
      target: { value: '45' },
    })
    expect(transparencySlider.value).toBe('45')

    fireEvent.click(
      screen.getByRole('button', { name: 'Wireframe' }),
    )
    expect(transparencySlider.disabled).toBe(true)

    fireEvent.click(
      screen.getByRole('button', { name: 'Surface' }),
    )
    expect(transparencySlider.disabled).toBe(false)
  })
})
