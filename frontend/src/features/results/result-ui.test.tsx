// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { workspaceStore } from '@/stores'
import {
  createCompletedRayTraceJobFixture,
  createRayTraceResultFixture,
} from '@/test/raytrace-fixture'

import { ResultPanel } from './result-panel'
import { RayTraceResultWindow } from './result-window'

afterEach(() => {
  cleanup()
  workspaceStore.getState().actions.resetWorkspace()
})

describe('Step 11 result UI', () => {
  it('shows result KPIs and applies ray path presets', () => {
    const onOpenAnalysis = vi.fn()
    render(
      <ResultPanel
        job={createCompletedRayTraceJobFixture()}
        onOpenAnalysis={onOpenAnalysis}
      />,
    )

    expect(screen.getByText('12.000%')).not.toBeNull()
    expect(screen.getByText('2/2')).not.toBeNull()
    expect(
      Object.values(workspaceStore.getState().rayPathDisplayFilters),
    ).toEqual([true, true, false, false, false, false])
    fireEvent.click(screen.getByRole('button', { name: 'All off' }))
    expect(screen.getByText('0/2')).not.toBeNull()
    expect(
      Object.values(workspaceStore.getState().rayPathDisplayFilters),
    ).toEqual([false, false, false, false, false, false])

    fireEvent.click(
      screen.getByRole('button', { name: '분석 결과 보기' }),
    )
    expect(onOpenAnalysis).toHaveBeenCalledOnce()
  })

  it('opens the movable analysis window and switches result tabs', () => {
    const onOpenChange = vi.fn()
    render(
      <div className="relative h-[700px] w-[1000px]">
        <RayTraceResultWindow
          open
          result={createRayTraceResultFixture()}
          onOpenChange={onOpenChange}
        />
      </div>,
    )

    expect(
      screen.getByRole('dialog', {
        name: 'Ray Tracing Analysis Result',
      }),
    ).not.toBeNull()
    expect(screen.getByText('12.000%')).not.toBeNull()

    fireEvent.click(
      screen.getByRole('tab', { name: /Surface optical/ }),
    )
    expect(screen.getByText('0.003 lm')).not.toBeNull()

    fireEvent.click(
      screen.getByRole('button', { name: 'Close result window' }),
    )
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
