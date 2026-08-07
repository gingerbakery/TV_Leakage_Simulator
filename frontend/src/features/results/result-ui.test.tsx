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
  it('opens the analysis window at the expanded default size', () => {
    const boundsSpy = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockReturnValue({
        bottom: 1000,
        height: 1000,
        left: 0,
        right: 1200,
        top: 0,
        width: 1200,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      })

    render(
      <div className="relative h-[1000px] w-[1200px]">
        <RayTraceResultWindow
          open
          result={createRayTraceResultFixture()}
          onOpenChange={vi.fn()}
        />
      </div>,
    )

    const dialog = screen.getByRole('dialog', {
      name: 'Ray Tracing Analysis Result',
    })
    expect(dialog.style.width).toBe('960px')
    expect(dialog.style.height).toBe('880px')

    boundsSpy.mockRestore()
  })

  it('shows result KPIs and applies ray path presets', () => {
    const onOpenAnalysis = vi.fn()
    render(
      <AppProviders>
        <ResultPanel
          job={createCompletedRayTraceJobFixture()}
          onOpenAnalysis={onOpenAnalysis}
        />
      </AppProviders>,
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

  it('renders receiver heatmaps with physical ratio and centered axes', () => {
    const result = createRayTraceResultFixture()
    result.receivers[0].width_mm = 40
    result.receivers[0].height_mm = 20
    const createImageData = vi.fn(
      (width: number, height: number) =>
        ({
          data: new Uint8ClampedArray(width * height * 4),
          height,
          width,
        }) as ImageData,
    )
    const putImageData = vi.fn()
    const contextSpy = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockReturnValue({
        createImageData,
        putImageData,
      } as unknown as CanvasRenderingContext2D)

    render(
      <div className="relative h-[700px] w-[1000px]">
        <RayTraceResultWindow
          open
          result={result}
          onOpenChange={vi.fn()}
        />
      </div>,
    )
    fireEvent.click(screen.getByRole('tab', { name: 'Receiver' }))

    const frame = screen.getByTestId('receiver_001-heatmap-frame')
    expect(frame.style.aspectRatio).toBe('40 / 20')
    const viewport = screen.getByTestId(
      'receiver_001-heatmap-viewport',
    )
    vi.spyOn(viewport, 'getBoundingClientRect').mockReturnValue({
      bottom: 200,
      height: 200,
      left: 0,
      right: 400,
      top: 0,
      width: 400,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    })
    const xAxis = screen.getByTestId('receiver_001-x-axis')
    const yAxis = screen.getByTestId('receiver_001-y-axis')
    expect(xAxis.querySelector('[data-axis-tick="0"]')).not.toBeNull()
    expect(yAxis.querySelector('[data-axis-tick="0"]')).not.toBeNull()
    expect(screen.getByText('X (mm)')).not.toBeNull()
    expect(screen.getByText('Y (mm)')).not.toBeNull()
    expect(createImageData).toHaveBeenCalledWith(2, 2)
    expect(putImageData).toHaveBeenCalledOnce()

    fireEvent.wheel(viewport, {
      clientX: 300,
      clientY: 50,
      deltaY: -300,
    })
    expect(
      screen.getByTestId('receiver_001-zoom').textContent,
    ).not.toBe('1.00×')
    const tooltip = screen.getByRole('tooltip')
    expect(frame.contains(tooltip)).toBe(true)
    expect(viewport.contains(tooltip)).toBe(false)
    expect(tooltip.textContent).toContain('10 mm')
    expect(tooltip.textContent).toContain('5 mm')
    expect(tooltip.textContent).toContain('0.004000 lm')

    fireEvent.click(screen.getByRole('button', { name: 'Reset view' }))
    expect(screen.getByTestId('receiver_001-zoom').textContent).toBe(
      '1.00×',
    )

    contextSpy.mockRestore()
  })
})
