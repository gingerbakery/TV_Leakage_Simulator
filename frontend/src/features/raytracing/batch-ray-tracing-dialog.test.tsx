// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'

import {
  BatchRayTracingDialog,
} from './batch-ray-tracing-dialog'
import type { BatchRayTracingManager } from './batch-ray-tracing-manager'
import { RayTracingPanel } from './ray-tracing-panel'

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub

function addTwoCases() {
  const actions = workspaceStore.getState().actions
  actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
  actions.addCadCase({ path: 'case-b.step', displayName: 'case-b.step' })
  return workspaceStore.getState().cadCases.map((item) => item.caseId)
}

function managerFixture(
  overrides: Partial<BatchRayTracingManager> = {},
): BatchRayTracingManager {
  return {
    open: true,
    state: {
      batchId: null,
      status: 'idle',
      currentCaseId: null,
      progress: 0,
      startedAt: null,
      completedAt: null,
      cases: [],
    },
    selectedCaseIds: [],
    error: null,
    cancelPending: false,
    isRunning: false,
    openDialog: vi.fn(),
    setOpen: vi.fn(),
    setCaseSelected: vi.fn(),
    selectCases: vi.fn(),
    run: vi.fn().mockResolvedValue(undefined),
    cancel: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  workspaceStore.getState().actions.resetWorkspace()
})

describe('BatchRayTracingDialog', () => {
  it('selects Cases and starts a multi-Case run', async () => {
    const caseIds = addTwoCases()
    const manager = managerFixture({ selectedCaseIds: caseIds })

    render(<BatchRayTracingDialog manager={manager} />)

    const dialog = screen.getByRole('dialog', { name: 'Batch Ray Tracing' })
    expect(within(dialog).getByText('case-a.step')).not.toBeNull()
    expect(within(dialog).getByText('case-b.step')).not.toBeNull()
    fireEvent.click(
      within(dialog).getByRole('checkbox', { name: '전체 Case 선택' }),
    )
    expect(manager.selectCases).toHaveBeenCalledWith([])

    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Run 2 Cases' }),
    )
    expect(manager.run).toHaveBeenCalledOnce()
  })

  it('shows live progress, per-Case failure, auto-save status, and cancel', () => {
    const caseIds = addTwoCases()
    const manager = managerFixture({
      selectedCaseIds: caseIds,
      isRunning: true,
      state: {
        batchId: 'batch-1',
        status: 'running',
        currentCaseId: caseIds[1],
        progress: 0.65,
        startedAt: 1,
        completedAt: null,
        cases: [
          {
            caseId: caseIds[0],
            name: 'CASE 01',
            status: 'completed',
            progress: 1,
            phaseDetail: 'Ray Tracing 완료',
            jobId: null,
            runId: 'run-a',
            error: null,
            geometryCacheHit: false,
            segmentIndex: 0,
            rayMultiplier: 1,
          },
          {
            caseId: caseIds[1],
            name: 'CASE 02',
            status: 'failed',
            progress: 1,
            phaseDetail: 'Ray Tracing 실패',
            jobId: null,
            runId: null,
            error: 'Receiver 설정을 확인해 주세요.',
            geometryCacheHit: null,
            segmentIndex: 0,
            rayMultiplier: 1,
          },
        ],
      },
    })

    render(<BatchRayTracingDialog manager={manager} />)

    expect(screen.getByText('65%')).not.toBeNull()
    expect(screen.getByText('Results stored in Cases')).not.toBeNull()
    expect(screen.getByText('Receiver 설정을 확인해 주세요.')).not.toBeNull()
    expect(
      (screen.getByRole('checkbox', { name: 'CASE 01 선택' }) as HTMLInputElement)
        .disabled,
    ).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Cancel Batch' }))
    expect(manager.cancel).toHaveBeenCalledOnce()
  })

  it('blocks Batch start while a single Ray Tracing run is active', () => {
    const caseIds = addTwoCases()
    const manager = managerFixture({ selectedCaseIds: caseIds })

    render(
      <BatchRayTracingDialog
        manager={manager}
        singleRayTraceRunning
      />,
    )

    expect(
      (screen.getByRole('button', { name: 'Run 2 Cases' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true)
    expect(screen.getByText(/단일 Ray Tracing이 실행 중입니다/)).not.toBeNull()
  })

  it('blocks the single Run action while Batch is active', () => {
    addTwoCases()
    const openBatch = vi.fn()

    render(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          batchRayTracingRunning
          batchRayTracingProgress={0.42}
          onOpenBatchRayTracing={openBatch}
        />
      </AppProviders>,
    )

    const singleRun = screen.getByRole('button', { name: 'Run Ray Tracing' })
    expect((singleRun as HTMLButtonElement).disabled).toBe(true)
    expect(singleRun.getAttribute('title')).toBe('Batch Ray Tracing이 실행 중입니다.')
    fireEvent.click(screen.getByRole('button', { name: 'Batch Run' }))
    expect(openBatch).toHaveBeenCalledOnce()
    expect(screen.getByText('Batch 42%')).not.toBeNull()
  })
})
