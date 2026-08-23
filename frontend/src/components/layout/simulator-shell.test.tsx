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
import { createWorkspaceStore, workspaceStore } from '@/stores'
import { createCompletedRayTraceJobFixture } from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'
import { matchSetupComponents } from '@/features/projects/copy-analysis-setup'
import {
  createBitsamProject,
  serializeBitsamProject,
} from '@/features/projects'

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

  it('keeps the workflow menu inside its column and exposes accessible width controls', () => {
    window.localStorage.removeItem('tv-leakage-workflow-sidebar-width')
    const view = renderShell()
    const layout = view.container.querySelector<HTMLElement>(
      '[data-simulator-workspace-layout]',
    )
    const sidebar = view.container.querySelector<HTMLElement>(
      '[data-workflow-sidebar]',
    )
    const workflowRegion = screen.getByRole('region', {
      name: 'Workflow menu',
    })
    const rayTracingStep = screen.getByRole('button', {
      name: 'Step 04 Ray Tracing',
    })

    expect(layout?.style.getPropertyValue('--workflow-sidebar-width')).toBe(
      '384px',
    )
    const sidebarClasses = sidebar?.className.split(' ') ?? []
    const workflowRegionClasses = workflowRegion.className.split(' ')
    expect(sidebarClasses).toContain('min-w-0')
    expect(sidebarClasses).toContain('overflow-x-clip')
    expect(sidebarClasses).toContain('lg:overflow-hidden')
    expect(workflowRegionClasses).toContain('overflow-x-clip')
    expect(workflowRegionClasses).toContain('lg:h-full')
    expect(workflowRegionClasses).toContain('lg:overflow-y-auto')
    expect(workflowRegionClasses).not.toContain('overflow-y-auto')
    expect(
      workflowRegionClasses.some((className) =>
        className.startsWith('h-[min('),
      ),
    ).toBe(false)
    expect(rayTracingStep.className).toContain('min-w-0')
    expect(rayTracingStep.className).not.toContain('-mx-')

    fireEvent.click(
      screen.getByRole('button', { name: '왼쪽 메뉴 넓게 보기' }),
    )
    expect(layout?.style.getPropertyValue('--workflow-sidebar-width')).toBe(
      '464px',
    )

    const resizeHandle = screen.getByRole('separator', {
      name: '왼쪽 메뉴 너비 조절',
    })
    expect(resizeHandle.getAttribute('aria-controls')).toBe(
      'workflow-sidebar',
    )
    expect(resizeHandle.getAttribute('aria-valuenow')).toBe('464')

    fireEvent.keyDown(resizeHandle, { key: 'Home' })
    expect(layout?.style.getPropertyValue('--workflow-sidebar-width')).toBe(
      '320px',
    )
    fireEvent.keyDown(resizeHandle, { key: 'End' })
    expect(layout?.style.getPropertyValue('--workflow-sidebar-width')).toBe(
      `${resizeHandle.getAttribute('aria-valuemax')}px`,
    )
    fireEvent.doubleClick(resizeHandle)
    expect(layout?.style.getPropertyValue('--workflow-sidebar-width')).toBe(
      '384px',
    )
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

  it('announces legacy CPU restoration and offers one-click GPU selection', async () => {
    const scene = createSceneFixture()
    apiHookState.scene = scene
    workspaceStore.getState().actions.setActiveCad({
      path: 'fixture.step',
      displayName: 'fixture.step',
    })
    const savedStore = createWorkspaceStore()
    savedStore.getState().actions.setActiveCad({
      path: 'fixture.step',
      displayName: 'fixture.step',
    })
    const project = createBitsamProject(
      scene,
      savedStore.getState(),
      new Date('2026-08-20T00:00:00.000Z'),
    )
    project.workspace.rayTraceConfig.intersection_backend = 'brute_force'
    delete (
      project.workspace.rayTraceConfig as Partial<
        typeof project.workspace.rayTraceConfig
      >
    ).compute_backend
    const file = new File(
      [serializeBitsamProject(project)],
      'legacy.bitsam',
      { type: 'application/vnd.bitsam+json' },
    )

    renderShell()
    fireEvent.change(screen.getByLabelText('BITSAM project file'), {
      target: { files: [file] },
    })

    const dialog = await screen.findByRole('dialog', {
      name: 'CPU 모드로 프로젝트를 불러왔습니다',
    })
    expect(dialog.textContent).toContain('CPU로 안전하게 복원했습니다')
    expect(workspaceStore.getState().rayTraceConfig.compute_backend).toBe('cpu')

    fireEvent.click(
      screen.getByRole('button', { name: 'NVIDIA CUDA GPU 선택' }),
    )

    expect(workspaceStore.getState().rayTraceConfig.compute_backend).toBe(
      'gpu_cuda',
    )
    expect(workspaceStore.getState().rayTraceConfig.intersection_backend).toBe(
      'bvh',
    )
    expect(
      screen.getByRole('button', { name: 'Step 04 Ray Tracing' })
        .getAttribute('aria-current'),
    ).toBe('step')
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
