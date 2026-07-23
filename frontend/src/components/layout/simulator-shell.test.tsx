// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { SimulatorShell } from './simulator-shell'

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub

if (!globalThis.PointerEvent) {
  globalThis.PointerEvent = MouseEvent as typeof PointerEvent
}

if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.setPointerCapture = () => undefined
  Element.prototype.releasePointerCapture = () => undefined
}

afterEach(cleanup)

describe('SimulatorShell', () => {
  it('switches the active workflow section without wiring feature data', () => {
    render(<SimulatorShell />)

    fireEvent.click(screen.getByRole('button', { name: /Step 05 Material/ }))

    expect(
      screen.getByText(
        '표면 광학 속성과 부품 assignment를 관리합니다.',
      ),
    ).not.toBeNull()
  })

  it('opens the common migration boundary dialog', async () => {
    render(<SimulatorShell />)

    fireEvent.click(screen.getByRole('button', { name: /Layout guide/ }))

    expect(
      await screen.findByRole('dialog', {
        name: 'Layout migration boundary',
      }),
    ).not.toBeNull()
  })

  it('returns focus to the viewer target after a context action dialog', async () => {
    render(<SimulatorShell />)
    const target = screen.getByTestId('component-context-target')

    fireEvent.contextMenu(target)
    fireEvent.click(
      await screen.findByRole('menuitem', { name: 'Material' }),
    )
    fireEvent.keyDown(
      await screen.findByRole('dialog', { name: 'Material assignment' }),
      { key: 'Escape' },
    )

    await waitFor(() => {
      expect(document.activeElement).toBe(target)
    })
  })
})
