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
