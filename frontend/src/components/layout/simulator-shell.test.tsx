// @vitest-environment jsdom

import {
  act,
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
import { createWorkspaceStore, workspaceStore, type MaterialAssignment } from '@/stores'
import { buildRayTraceRequest } from '@/features/raytracing'
import { createCompletedRayTraceJobFixture } from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'
import {
  matchSetupComponents,
  sceneComponentMatchMetadata,
} from '@/features/projects/copy-analysis-setup'
import {
  createBitsamProject,
  parseBitsamProject,
  serializeBitsamProject,
} from '@/features/projects'

import { SimulatorShell } from './simulator-shell'
import * as portableProjects from '@/features/projects/portable-project'

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
  it('shows the compact save summary with the actual saved result', async () => {
    const scene = createSceneFixture()
    apiHookState.scene = scene
    const actions = workspaceStore.getState().actions
    actions.addCadCase({ path: 'original.step', displayName: 'original.step' })
    actions.setRestoredRayTraceResult(createCompletedRayTraceJobFixture().result!)
    const save = vi.spyOn(portableProjects, 'savePortableProject').mockResolvedValueOnce({
      cancelled: false, traceCached: true, downloaded: false,
    })
    try {
      renderShell()
      fireEvent.click(screen.getByRole('button', { name: 'Save BITSAM project' }))
      const dialog = await screen.findByRole('dialog', { name: '저장 완료' })
      expect(within(dialog).getByText('해석 설정 및 결과')).not.toBeNull()
      expect(within(dialog).getByText('원본 CAD 및 모델 형상')).not.toBeNull()
      expect(save.mock.calls[0][0].analysis_result).toBeTruthy()
      fireEvent.click(within(dialog).getByRole('button', { name: '확인' }))
      await waitFor(() => expect(screen.queryByRole('dialog', { name: '저장 완료' })).toBeNull())
    } finally {
      save.mockRestore()
    }
  })

  it('does not show a save confirmation after the picker is cancelled', async () => {
    apiHookState.scene = createSceneFixture()
    workspaceStore.getState().actions.addCadCase({ path: 'original.step', displayName: 'original.step' })
    const save = vi.spyOn(portableProjects, 'savePortableProject').mockResolvedValueOnce({ cancelled: true })
    try {
      renderShell()
      fireEvent.click(screen.getByRole('button', { name: 'Save BITSAM project' }))
      await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
      expect(screen.queryByRole('dialog', { name: '저장 완료' })).toBeNull()
    } finally {
      save.mockRestore()
    }
  })

  it('loads a portable CAD as an independent case and keeps its saved result', async () => {
    const scene = createSceneFixture()
    apiHookState.scene = scene
    workspaceStore.getState().actions.addCadCase({ path: 'existing.step', displayName: 'existing.step' })
    const savedStore = createWorkspaceStore()
    savedStore.getState().actions.setActiveCad({ path: 'original.step', displayName: 'original.step' })
    const result = createCompletedRayTraceJobFixture().result!
    const project = createBitsamProject(scene, savedStore.getState(), new Date(), result)
    project.workspace.faceColorOverrides = [{ componentId: 1, faceIds: [0, 1], color: '#ef4444' }]
    const load = vi.spyOn(portableProjects, 'loadPortableProject').mockResolvedValueOnce({
      project, scene, cad: { path: 'restored/original.step', displayName: 'original.step' }, traceCached: true,
    })
    try {
      renderShell()
      const file = new File([new Uint8Array([80, 75, 3, 4])], 'restored.bitsam')
      fireEvent.change(screen.getByLabelText('BITSAM project file'), { target: { files: [file] } })
      const dialog = await screen.findByRole('dialog', { name: '불러오기 완료' })
      expect(within(dialog).getByText('restored.bitsam')).not.toBeNull()
      expect(within(dialog).getByText('CAD 모델 형상')).not.toBeNull()
      expect(within(dialog).getByText('해석 설정 및 결과')).not.toBeNull()
      expect(dialog.className).toContain('sm:max-w-[26.25rem]')
      expect(workspaceStore.getState().activeCad?.path).toBe('restored/original.step')
      expect(workspaceStore.getState().cadCases).toHaveLength(2)
      expect(workspaceStore.getState().cadCases[0].cad.path).toBe('existing.step')
      expect(workspaceStore.getState().restoredRayTraceResult).toEqual(result)
      expect(workspaceStore.getState().faceColorOverrides).toEqual(project.workspace.faceColorOverrides)
      expect(workspaceStore.getState().activeRayTraceJobId).toBeNull()
      fireEvent.click(within(dialog).getByRole('button', { name: '확인' }))
      await waitFor(() => expect(screen.queryByRole('dialog', { name: '불러오기 완료' })).toBeNull())
      // Imported results have no live job, but must remain inspectable after closing.
      const resultStep = screen.getByRole('button', { name: 'Step 05 Result' })
      if (resultStep.getAttribute('aria-expanded') !== 'true') fireEvent.click(resultStep)
      const closeResult = screen.queryByRole('button', { name: 'Close result window' })
      if (closeResult) fireEvent.click(closeResult)
      fireEvent.click(screen.getByRole('button', { name: '분석 결과 보기' }))
      expect(await screen.findByRole('dialog', { name: 'Ray Tracing Analysis Result' })).not.toBeNull()
      fireEvent.click(screen.getByRole('button', { name: 'Close result window' }))
      expect(screen.queryByRole('dialog', { name: 'Ray Tracing Analysis Result' })).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: 'All off' }))
      expect(Object.values(workspaceStore.getState().rayPathDisplayFilters).every((visible) => !visible)).toBe(true)
      fireEvent.click(screen.getByRole('button', { name: 'All on' }))
      expect(Object.values(workspaceStore.getState().rayPathDisplayFilters).every(Boolean)).toBe(true)
      fireEvent.click(screen.getByRole('button', { name: '분석 결과 보기' }))
      expect(await screen.findByRole('dialog', { name: 'Ray Tracing Analysis Result' })).not.toBeNull()
      expect(workspaceStore.getState().activeRayTraceJobId).toBeNull()
    } finally {
      load.mockRestore()
    }
  })

  it.each([false, true])(
    'saves edited portable settings with the new result and restores both (case switch: %s)',
    async (switchCases) => {
      const scene = createSceneFixture()
      apiHookState.scene = scene
      const savedStore = createWorkspaceStore()
      const savedActions = savedStore.getState().actions
      savedActions.setActiveCad({ path: 'after.step', displayName: 'after.step' })
      const oldResult = createCompletedRayTraceJobFixture().result!
      oldResult.run_id = 'before-manual-edits'
      oldResult.emitters[0].center = [4, 1, 15]
      oldResult.receivers[0].center = [5.7, 9, 17.1]
      oldResult.receivers[0].acceptance_angle_deg = 30
      savedActions.upsertEmitter(oldResult.emitters[0])
      savedActions.upsertReceiver(oldResult.receivers[0])
      const originalAssignments: MaterialAssignment[] = [
        {
          assignmentId: 'material-part-chr', componentId: 1,
          targetType: 'part', faceIds: [], baseMaterialId: 'secc_bare',
          surfaceId: 'metal_gloss', profileId: '', bsdfAssetId: '', enabled: true,
          opticalOverride: { reflectance: 0.3, loss: 0.7, specularRatio: 1, diffuseRatio: 0 },
        },
        {
          assignmentId: 'material-part-fmb', componentId: 2,
          targetType: 'part', faceIds: [], baseMaterialId: 'pc_white',
          surfaceId: 'high_gloss_resin', profileId: '', bsdfAssetId: '', enabled: true,
          opticalOverride: { reflectance: 0.25, loss: 0.75, specularRatio: 0.98, diffuseRatio: 0.02 },
        },
      ]
      originalAssignments.forEach(savedActions.upsertMaterialAssignment)
      const originalProject = createBitsamProject(scene, savedStore.getState(), new Date(), oldResult)
      const load = vi.spyOn(portableProjects, 'loadPortableProject').mockResolvedValueOnce({
        project: originalProject, scene,
        cad: { path: 'restored/after.step', displayName: 'after.step' }, traceCached: true,
      })
      const save = vi.spyOn(portableProjects, 'savePortableProject').mockResolvedValueOnce({
        cancelled: false, traceCached: true, downloaded: false,
      })
      try {
        renderShell()
        fireEvent.change(screen.getByLabelText('BITSAM project file'), {
          target: { files: [new File([new Uint8Array([80, 75, 3, 4])], 'original.bitsam')] },
        })
        const loaded = await screen.findByRole('dialog', { name: '불러오기 완료' })
        fireEvent.click(within(loaded).getByRole('button', { name: '확인' }))
        const closeOldResult = screen.queryByRole('button', { name: 'Close result window' })
        if (closeOldResult) fireEvent.click(closeOldResult)
        const actions = workspaceStore.getState().actions
        const editedAssignments: MaterialAssignment[] = originalAssignments.map((assignment) => ({
          ...assignment,
          opticalOverride: assignment.componentId === 1
            ? { reflectance: 0.3, loss: 0.7, specularRatio: 0, diffuseRatio: 1 }
            : { reflectance: 0.3, loss: 0.7, specularRatio: 0.98, diffuseRatio: 0.02 },
        }))
        act(() => {
          editedAssignments.forEach(actions.upsertMaterialAssignment)
          actions.upsertEmitter({ ...workspaceStore.getState().emitters[0], center: [4.5, 1, 15] })
          actions.upsertReceiver({
            ...workspaceStore.getState().receivers[0],
            center: [5.15, 9, 17.1], base_center: [4.65, 9, 16.1],
            position_offset_mm: [0.5, 0, 1], acceptance_angle_deg: 90,
          })
        })
        const editedState = workspaceStore.getState()
        const request = buildRayTraceRequest({
          scene, projectName: 'manual-edits', emitters: editedState.emitters,
          receivers: editedState.receivers, materialAssignments: editedState.materialAssignments,
          transformRules: editedState.transformRules, excludedComponentIds: editedState.excludedComponentIds,
          deletedComponentIds: editedState.deletedComponentIds, roiScopes: editedState.roiScopes,
          config: editedState.rayTraceConfig,
        })
        expect(request.optical_profiles).toEqual(expect.arrayContaining([
          expect.objectContaining({ reflectance: 0.3, specular_ratio: 0, diffuse_ratio: 1 }),
          expect.objectContaining({ reflectance: 0.3, specular_ratio: 0.98, diffuse_ratio: 0.02 }),
        ]))
        const completedJob = createCompletedRayTraceJobFixture()
        completedJob.job_id = 'new-manually-edited-job'
        completedJob.result = {
          ...completedJob.result!, run_id: 'after-manual-edits',
          emitters: structuredClone(request.emitters), receivers: structuredClone(request.receivers),
          optical_profiles: structuredClone(request.optical_profiles),
        }
        act(() => {
          apiHookState.rayTraceJob = completedJob
          actions.setActiveRayTraceJobId(completedJob.job_id)
        })
        await waitFor(() => expect(workspaceStore.getState().cadCases.find(
          (item) => item.caseId === workspaceStore.getState().activeCadCaseId,
        )?.latestResult?.run_id).toBe('after-manual-edits'))
        const closeNewResult = screen.queryByRole('button', { name: 'Close result window' })
        if (closeNewResult) fireEvent.click(closeNewResult)
        if (switchCases) {
          const editedCaseId = workspaceStore.getState().activeCadCaseId!
          act(() => {
            apiHookState.rayTraceJob = undefined
            actions.addCadCase({ path: 'another.step', displayName: 'another.step' })
          })
          act(() => actions.setActiveCadCase(editedCaseId))
        }
        fireEvent.click(screen.getByRole('button', { name: 'Save BITSAM project' }))
        const saved = await screen.findByRole('dialog', { name: '저장 완료' })
        const savedProject = parseBitsamProject(serializeBitsamProject(save.mock.calls[0][0]))
        expect(savedProject.workspace.materialAssignments).toEqual(editedAssignments)
        expect(savedProject.workspace.emitters[0].center).toEqual([4.5, 1, 15])
        expect(savedProject.workspace.receivers[0]).toMatchObject({
          center: [5.15, 9, 17.1], base_center: [4.65, 9, 16.1], acceptance_angle_deg: 90,
        })
        expect(savedProject.analysis_result).toMatchObject({
          run_id: 'after-manual-edits', optical_profiles: request.optical_profiles,
          emitters: request.emitters, receivers: request.receivers,
        })
        expect(originalProject.workspace.materialAssignments).toEqual(originalAssignments)
        fireEvent.click(within(saved).getByRole('button', { name: '확인' }))
        apiHookState.rayTraceJob = undefined
        load.mockResolvedValueOnce({
          project: savedProject, scene,
          cad: { path: 'reloaded/after.step', displayName: 'after.step' }, traceCached: true,
        })
        fireEvent.change(screen.getByLabelText('BITSAM project file'), {
          target: { files: [new File([new Uint8Array([80, 75, 3, 4])], 'edited.bitsam')] },
        })
        await screen.findByRole('dialog', { name: '불러오기 완료' })
        const restoredState = workspaceStore.getState()
        expect(restoredState.materialAssignments).toEqual(editedAssignments)
        expect(restoredState.emitters).toEqual(savedProject.workspace.emitters)
        expect(restoredState.receivers).toEqual(savedProject.workspace.receivers)
        expect(restoredState.restoredRayTraceResult).toEqual(savedProject.analysis_result)
      } finally {
        load.mockRestore()
        save.mockRestore()
      }
    },
  )

  it('uses the same completion dialog for a compatible legacy project', async () => {
    const scene = createSceneFixture()
    apiHookState.scene = scene
    workspaceStore.getState().actions.setActiveCad({ path: 'fixture.step', displayName: 'fixture.step' })
    const project = createBitsamProject(scene, workspaceStore.getState())
    project.workspace.rayTraceConfig.compute_backend = 'cpu'
    renderShell()
    fireEvent.change(screen.getByLabelText('BITSAM project file'), {
      target: { files: [new File([serializeBitsamProject(project)], 'fixture.bitsam')] },
    })
    const dialog = await screen.findByRole('dialog', { name: '불러오기 완료' })
    expect(within(dialog).getByText('해석 설정')).not.toBeNull()
    expect(within(dialog).queryByText('해석 설정 및 결과')).toBeNull()
    expect(within(dialog).getByText('불러온 항목')).not.toBeNull()
  })

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
      breakdown: {
        name: 2,
        geometry: 0,
        componentId: 0,
        orderedFallback: 0,
      },
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
    const modelImportStep = screen.getByRole('button', {
      name: 'Step 01 Model Import',
    })
    const modelImportHeader = modelImportStep.closest<HTMLElement>(
      '[data-workflow-section-header="model-import"]',
    )
    const modelImportTitleRow = modelImportHeader?.querySelector(
      '[data-workflow-section-title-row]',
    )
    const modelImportContent = view.container.querySelector<HTMLElement>(
      '[data-workflow-section-content="model-import"] > div',
    )
    const modelImportHelp = within(modelImportHeader as HTMLElement).getByRole(
      'button',
      { name: 'Model Import 도움말' },
    )

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
    expect(workflowRegion.style.scrollbarGutter).toBe('stable')
    expect(
      workflowRegionClasses.some((className) =>
        className.startsWith('h-[min('),
      ),
    ).toBe(false)
    expect(rayTracingStep.className).toContain('min-w-0')
    expect(rayTracingStep.className).not.toContain('-mx-')
    expect(
      view.container.querySelectorAll('[data-workflow-section-help]'),
    ).toHaveLength(7)
    expect(modelImportTitleRow?.contains(modelImportHelp)).toBe(true)
    expect(modelImportHelp.className).toContain('size-4')
    expect(modelImportHelp.querySelector('svg')?.className.baseVal).toContain(
      'size-3',
    )
    const modelImportExpanded = modelImportStep.getAttribute('aria-expanded')
    fireEvent.click(modelImportHelp)
    expect(modelImportStep.getAttribute('aria-expanded')).toBe(
      modelImportExpanded,
    )
    expect(modelImportContent?.className).toContain('rounded-lg')
    expect(modelImportContent?.className).toContain('border')
    expect(modelImportContent?.className).toContain('bg-slate-200/70')
    expect(modelImportContent?.className).not.toContain('border-l-2')

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

  it('restores archived results for the same CAD name after tessellation changes', async () => {
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
    const savedResult = createCompletedRayTraceJobFixture().result!
    const project = createBitsamProject(
      scene,
      savedStore.getState(),
      new Date('2026-08-20T00:00:00.000Z'),
      savedResult,
    )
    project.cad.fingerprint.face_count += 1
    const file = new File(
      [serializeBitsamProject(project)],
      'fixture.bitsam',
      { type: 'application/vnd.bitsam+json' },
    )

    renderShell()
    fireEvent.change(screen.getByLabelText('BITSAM project file'), {
      target: { files: [file] },
    })

    const dialog = await screen.findByRole('dialog', {
      name: '설정 조건만 불러왔습니다',
    })
    expect(dialog.textContent).toContain('Stored Ray')
    await waitFor(() =>
      expect(workspaceStore.getState().restoredRayTraceResult?.run_id).toBe(
        savedResult.run_id,
      ),
    )
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
    actions.setCadCaseComponentMatchMetadata(
      targetCaseId,
      sceneComponentMatchMetadata(targetScene),
    )
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
    expect(getScene).not.toHaveBeenCalled()
    expect(
      workspaceStore.getState().cadCases.find(
        (item) => item.caseId === targetCaseId,
      )?.workspaceState?.componentNameOverrides,
    ).toEqual({ 100: 'Renamed chassis' })
    getScene.mockRestore()
  })

  it('copies only Run option without loading target CAD metadata or replacing other settings', async () => {
    apiHookState.scene = createSceneFixture()
    const actions = workspaceStore.getState().actions
    actions.addCadCase({ path: 'source.step', displayName: 'Source' })
    const sourceId = workspaceStore.getState().activeCadCaseId!
    actions.setRayTraceConfig({ ...workspaceStore.getState().rayTraceConfig, max_depth: 73 })
    actions.addCadCase({ path: 'target.step', displayName: 'Target' })
    const targetId = workspaceStore.getState().activeCadCaseId!
    actions.renameComponent(1, 'Keep target name')
    actions.setActiveCadCase(sourceId)
    const before = structuredClone(workspaceStore.getState().cadCases.find((item) => item.caseId === targetId)!.workspaceState!)
    const getScene = vi.spyOn(apiClient, 'getScene').mockRejectedValue(new Error('Must not load target Scene'))
    renderShell()
    fireEvent.click(screen.getByRole('button', { name: 'Copy Setup' }))
    const dialog = screen.getByRole('dialog', { name: 'Copy Analysis Setup' })
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Receiver' }))
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Emitter' }))
    const targetLabel = within(dialog).getByText('Target').closest('label')!
    fireEvent.click(targetLabel.querySelector('input')!)
    fireEvent.click(screen.getByRole('button', { name: 'Copy to 1 Cases' }))
    await screen.findByRole('dialog', { name: 'Copy Setup Complete' })
    expect(getScene).not.toHaveBeenCalled()
    expect(workspaceStore.getState().cadCases.find((item) => item.caseId === targetId)!.workspaceState).toEqual({
      ...before, rayTraceConfig: workspaceStore.getState().rayTraceConfig,
    })
    getScene.mockRestore()
  })

  it('loads legacy Copy Setup target Scenes sequentially', async () => {
    const sourceScene = createSceneFixture()
    apiHookState.scene = sourceScene
    const actions = workspaceStore.getState().actions
    actions.addCadCase({ path: 'source.step', displayName: 'source.step' })
    const sourceCaseId = workspaceStore.getState().activeCadCaseId!
    actions.addCadCase({ path: 'target-1.step', displayName: 'target-1.step' })
    actions.addCadCase({ path: 'target-2.step', displayName: 'target-2.step' })
    actions.setActiveCadCase(sourceCaseId)

    let activeRequests = 0
    let maximumActiveRequests = 0
    const getScene = vi.spyOn(apiClient, 'getScene').mockImplementation(
      async () => {
        activeRequests += 1
        maximumActiveRequests = Math.max(
          maximumActiveRequests,
          activeRequests,
        )
        await new Promise((resolve) => window.setTimeout(resolve, 5))
        activeRequests -= 1
        return structuredClone(sourceScene)
      },
    )

    renderShell()
    fireEvent.click(screen.getByRole('button', { name: 'Copy Setup' }))
    const copyDialog = screen.getByRole('dialog', {
      name: 'Copy Analysis Setup',
    })
    for (const name of ['target-1.step', 'target-2.step']) {
      const targetLabel = within(copyDialog).getByText(name).closest('label')
      fireEvent.click(targetLabel!.querySelector('input')!)
    }
    fireEvent.click(screen.getByRole('button', { name: 'Copy to 2 Cases' }))

    expect(
      await screen.findByRole('dialog', { name: 'Copy Setup Complete' }),
    ).not.toBeNull()
    expect(getScene).toHaveBeenCalledTimes(2)
    expect(maximumActiveRequests).toBe(1)
    getScene.mockRestore()
  })
})
