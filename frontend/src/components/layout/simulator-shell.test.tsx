// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'

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

function renderShell() {
  return render(
    <AppProviders>
      <SimulatorShell />
    </AppProviders>,
  )
}

afterEach(() => {
  cleanup()
  workspaceStore.getState().actions.resetWorkspace()
})

describe('SimulatorShell', () => {
  it('renders the empty CAD boundary and switches feature panels', () => {
    renderShell()

    expect(screen.getByText('Empty workspace')).not.toBeNull()
    expect(screen.getByText('v1.0.0 · React')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Step 05 Material/ }))

    expect(screen.getByText('No assignments')).not.toBeNull()
  })

  it('opens the common feature migration boundary dialog', async () => {
    renderShell()

    fireEvent.click(screen.getByRole('button', { name: /Layout guide/ }))

    expect(
      await screen.findByRole('dialog', {
        name: 'Feature migration boundary',
      }),
    ).not.toBeNull()
  })
})
