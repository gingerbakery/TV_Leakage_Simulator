// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import { createCompletedRayTraceJobFixture } from '@/test/raytrace-fixture'

import { SimulatorShell } from './simulator-shell'

const apiHookState = vi.hoisted(() => ({
  rayTraceJob: undefined as unknown,
}))

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    useSceneQuery: () => ({
      data: undefined,
      error: null,
      isPending: false,
    }),
    useRayTraceJobQuery: () => ({
      data: apiHookState.rayTraceJob,
      error: null,
    }),
  }
})

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
  apiHookState.rayTraceJob = undefined
  workspaceStore.getState().actions.resetWorkspace()
})

describe('SimulatorShell', () => {
  it('renders the empty CAD boundary and switches feature panels', () => {
    renderShell()

    expect(screen.getByText('Empty workspace')).not.toBeNull()
    expect(screen.getByText('v1.0.0 · React')).not.toBeNull()
    expect(
      screen.queryByRole('button', { name: /Step 04 Transform/ }),
    ).toBeNull()
    expect(
      screen.queryByRole('button', { name: /Step 05 Material/ }),
    ).toBeNull()
    expect(
      screen.getByRole('button', { name: /Step 04 Ray tracing/ }),
    ).not.toBeNull()

    fireEvent.click(
      screen.getByRole('button', { name: 'Applied Settings' }),
    )

    expect(screen.getByText('No assignments')).not.toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Transform' }))

    expect(screen.getByText('No transform rules')).not.toBeNull()
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

  it('moves from Ray tracing to Result when tracing completes', () => {
    const view = renderShell()
    const rayTracingStep = screen.getByRole('button', {
      name: 'Step 04 Ray tracing',
    })
    const resultStep = screen.getByRole('button', {
      name: 'Step 05 Result',
    })

    fireEvent.click(rayTracingStep)
    expect(rayTracingStep.getAttribute('aria-current')).toBe('step')

    apiHookState.rayTraceJob = createCompletedRayTraceJobFixture()
    view.rerender(
      <AppProviders>
        <SimulatorShell />
      </AppProviders>,
    )

    expect(resultStep.getAttribute('aria-current')).toBe('step')
  })
})
