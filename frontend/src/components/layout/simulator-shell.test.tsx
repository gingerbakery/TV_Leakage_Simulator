// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api'
import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import { createCompletedRayTraceJobFixture } from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'
import { matchSetupComponents } from '@/features/projects/copy-analysis-setup'

import { SimulatorShell } from './simulator-shell'

const apiHookState = vi.hoisted(() => ({
  rayTraceJob: undefined as unknown,
  scene: undefined as ReturnType<typeof createSceneFixture> | undefined,
}))

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    useSceneQuery: () => ({
      data: apiHookState.scene,
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
  apiHookState.scene = undefined
  workspaceStore.getState().actions.resetWorkspace()
})

describe('SimulatorShell', () => {
  it('matches Copy Setup Components by CAD name instead of numeric ID', () => {
    const source = createSceneFixture()
    const target = structuredClone(source)
    target.components = target.components.map((component, index) => ({
      ...component,
      component_id: (index + 1) * 100,
      component_name: ` ${component.component_name.toUpperCase()} `,
    }))
    target.objects = target.components

    expect(matchSetupComponents(source, target)).toEqual({
      componentIdMap: { 1: 100, 2: 200 },
      matched: 2,
      unmatched: 0,
    })
  })

  it('renders the empty CAD boundary and switches feature panels', () => {
    renderShell()

    expect(screen.getByText('Empty workspace')).not.toBeNull()
    expect(
      (
        screen.getByRole('button', {
          name: 'Save BITSAM project',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true)
    expect(
      screen.getByRole('button', { name: 'Load BITSAM project' }),
    ).not.toBeNull()
    expect(screen.getByText('v1.0.0 · React')).not.toBeNull()
    expect(
      screen.queryByRole('button', { name: /Step 04 Transform/ }),
    ).toBeNull()
    expect(
      screen.queryByRole('button', { name: /Step 05 Material/ }),
    ).toBeNull()
    expect(
      screen.getByRole('button', { name: /Step 04 Ray Tracing/ }),
    ).not.toBeNull()

    fireEvent.click(
      screen.getByRole('button', { name: 'Applied Settings' }),
    )

    expect(screen.getByText('No assignments')).not.toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Transform' }))

    expect(screen.getByText('No transform rules')).not.toBeNull()
  })

  it('opens the Manual Guide placeholder dialog', async () => {
    renderShell()

    fireEvent.click(screen.getByRole('button', { name: 'Manual Guide' }))

    expect(
      await screen.findByRole('dialog', {
        name: 'Manual Guide',
      }),
    ).not.toBeNull()
  })

  it('moves from Ray tracing to Result when tracing completes', () => {
    const view = renderShell()
    const rayTracingStep = screen.getByRole('button', {
      name: 'Step 04 Ray Tracing',
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

  it('stores a restored result once without entering a render loop', async () => {
    const result = createCompletedRayTraceJobFixture().result
    const actions = workspaceStore.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    actions.upsertReceiver(result.receivers[0])
    actions.setRestoredRayTraceResult(result)
    const saveResult = vi.spyOn(actions, 'setActiveCadCaseResult')

    renderShell()

    await waitFor(() => expect(saveResult).toHaveBeenCalledTimes(1))
    saveResult.mockRestore()
  })

  it('copies the full setup with safe Component name matching', async () => {
    const sourceScene = createSceneFixture()
    const targetScene = structuredClone(sourceScene)
    targetScene.components = targetScene.components.map((component, index) => ({
      ...component,
      component_id: (index + 1) * 100,
    }))
    targetScene.objects = targetScene.components
    apiHookState.scene = sourceScene
    const actions = workspaceStore.getState().actions
    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const sourceCaseId = workspaceStore.getState().activeCadCaseId!
    actions.renameComponent(1, 'Renamed chassis')
    actions.addCadCase({ path: 'case-b.step', displayName: 'case-b.step' })
    const targetCaseId = workspaceStore.getState().activeCadCaseId!
    actions.setActiveCadCase(sourceCaseId)
    const getScene = vi.spyOn(apiClient, 'getScene').mockResolvedValue(targetScene)

    renderShell()
    fireEvent.click(screen.getByRole('button', { name: 'Copy Setup' }))
    const copyDialog = screen.getByRole('dialog', {
      name: 'Copy Analysis Setup',
    })
    const targetLabel = within(copyDialog).getByText('case-b.step').closest('label')
    fireEvent.click(targetLabel!.querySelector('input')!)
    fireEvent.click(screen.getByRole('button', { name: 'Copy to 1 Cases' }))

    expect(
      await screen.findByRole('dialog', { name: 'Copy Setup Complete' }),
    ).not.toBeNull()
    expect(getScene).toHaveBeenCalledWith('case-b.step')
    expect(
      workspaceStore.getState().cadCases.find(
        (item) => item.caseId === targetCaseId,
      )?.workspaceState?.componentNameOverrides,
    ).toEqual({ 100: 'Renamed chassis' })
    getScene.mockRestore()
  })
})
