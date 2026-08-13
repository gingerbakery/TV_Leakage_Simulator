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
  vi.unstubAllGlobals()
  workspaceStore.getState().actions.resetWorkspace()
})

describe('Step 11 result UI', () => {
  it('switches the detailed report with the header case selector', () => {
    const first = createRayTraceResultFixture()
    const second = {
      ...createRayTraceResultFixture(),
      run_id: 'run-second-case',
      runtime_sec: 2.5,
    }
    render(
      <RayTraceResultWindow
        open
        result={first}
        reportCases={[
          { caseId: 'case-1', name: 'CASE 01', cadName: 'a.step', result: first },
          { caseId: 'case-2', name: 'CASE 02', cadName: 'b.step', result: second },
        ]}
        onOpenChange={vi.fn()}
      />,
    )

    const selector = screen.getByRole('combobox', {
      name: 'Report active case',
    })
    fireEvent.pointerDown(selector)
    fireEvent.change(selector, { target: { value: 'case-2' } })
    expect(screen.getByText(/run-second-case/)).not.toBeNull()
  })

  it('lets the user choose the comparison baseline case', () => {
    const first = createRayTraceResultFixture()
    const second = {
      ...createRayTraceResultFixture(),
      run_id: 'run-second-baseline',
    }
    render(
      <RayTraceResultWindow
        open
        result={first}
        reportCases={[
          { caseId: 'case-1', name: 'CASE 01', cadName: 'a.step', result: first },
          { caseId: 'case-2', name: 'CASE 02', cadName: 'b.step', result: second },
        ]}
        onOpenChange={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: 'Compare cases' }))
    const firstBaseline = screen.getByRole('radio', {
      name: 'Set CASE 01 as baseline',
    })
    const secondBaseline = screen.getByRole('radio', {
      name: 'Set CASE 02 as baseline',
    })
    expect(firstBaseline).toHaveProperty('checked', true)
    fireEvent.click(secondBaseline)
    expect(secondBaseline).toHaveProperty('checked', true)
    expect(firstBaseline).toHaveProperty('checked', false)
  })

  it('compares results for the selected Receiver area', () => {
    const result = createRayTraceResultFixture()
    const receiverTwo = {
      ...structuredClone(result.receivers[0]),
      receiver_id: 'receiver_002',
      display_name: 'Right corner',
    }
    result.receivers.push(receiverTwo)
    result.receiver_grids.push({
      receiver_id: receiverTwo.receiver_id,
      resolution: [2, 2],
      bin_area_mm2: 1,
      flux_lumen: [[0.005, 0.005], [0.005, 0.005]],
      hit_count: 7,
    })
    result.metrics.receiver_002 = {
      peak_nit_est: 6,
      mean_nit_est: 5,
      total_flux_lumen: 0.02,
      hit_count: 7,
    }
    render(
      <RayTraceResultWindow
        open
        result={result}
        reportCases={[
          { caseId: 'case-1', name: 'CASE 01', cadName: 'a.step', result },
        ]}
        onOpenChange={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: 'Compare cases' }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Compare receiver' }), {
      target: { value: '1' },
    })
    expect(screen.getByText('0.020 lm')).not.toBeNull()
    expect(screen.getByText('6.000')).not.toBeNull()
  })

  it('uses the automatically synchronized CAD result as a comparison case', () => {
    const result = createRayTraceResultFixture()
    const onCaseMetadataChange = vi.fn()
    render(
      <RayTraceResultWindow
        open
        result={result}
        reportCases={[
          {
            caseId: 'case-1',
            name: 'Baseline structure',
            cadName: 'structure-a.step',
            note: 'Original chassis',
            result,
          },
        ]}
        onCaseMetadataChange={onCaseMetadataChange}
        onOpenChange={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: 'Compare cases' }))
    const caseNameInput = screen.getByDisplayValue('Baseline structure')
    expect(caseNameInput).not.toBeNull()
    expect(screen.getByText('structure-a.step')).not.toBeNull()
    expect(screen.getByDisplayValue('Original chassis')).not.toBeNull()
    const compareCheckbox = screen.getByRole('checkbox', {
      name: 'Compare Baseline structure',
    })
    expect(compareCheckbox).toHaveProperty('checked', true)
    expect(screen.getByText('50.0')).not.toBeNull()
    expect(screen.getByText('4.000 mm²')).not.toBeNull()
    fireEvent.click(compareCheckbox)
    expect(compareCheckbox).toHaveProperty('checked', false)
    expect(
      screen.getByRole('dialog', { name: 'Ray Tracing Analysis Result' }),
    ).not.toBeNull()
    fireEvent.change(caseNameInput, {
      target: { value: 'Updated baseline' },
    })
    expect(screen.getByDisplayValue('Updated baseline')).not.toBeNull()
    expect(onCaseMetadataChange).toHaveBeenCalled()
    expect(screen.getByText('빛샘 개선 점수')).not.toBeNull()
    expect(screen.getByText('광영역(@5%)')).not.toBeNull()
  })

  it('opens the analysis window at the expanded default size', () => {
    vi.stubGlobal('innerWidth', 1200)
    vi.stubGlobal('innerHeight', 1000)

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

    expect(screen.getByText('광영역 @1%')).not.toBeNull()
    expect(screen.getByText('광영역 @5%')).not.toBeNull()
    expect(screen.getByText('광영역 @10%')).not.toBeNull()
    expect(
      screen.getByRole('button', { name: 'Receiver 광영역 5% 설명' }),
    ).not.toBeNull()

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
    expect(screen.getByText('Error Estimate')).not.toBeNull()
    expect(screen.getByText('2.75%')).not.toBeNull()
    expect(screen.getByText('Converged')).not.toBeNull()
    expect(
      screen.getByRole('img', { name: 'X-axis luminance profile' }),
    ).not.toBeNull()
    expect(
      screen.getByRole('img', { name: 'Y-axis luminance profile' }),
    ).not.toBeNull()
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
    fireEvent.pointerDown(viewport, {
      clientX: 20,
      clientY: 180,
      pointerId: 1,
    })
    expect(screen.getByText(/Y=.*mm · 최대/)).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Error map' }))
    expect(screen.getByRole('button', { name: 'Error map' }).getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: 'Analyze area' }))
    fireEvent.pointerDown(viewport, { clientX: 100, clientY: 50, pointerId: 2 })
    fireEvent.pointerMove(viewport, { clientX: 300, clientY: 150, pointerId: 2 })
    fireEvent.pointerUp(viewport, { clientX: 300, clientY: 150, pointerId: 2 })
    expect(screen.getByText('Selected-area ray contribution')).not.toBeNull()
    expect(screen.getByTestId('receiver_001-analysis-region')).not.toBeNull()
    expect(tooltip.textContent).toContain('Incident flux')
    expect(tooltip.textContent).toContain('Pixel error')

    fireEvent.click(screen.getByRole('button', { name: 'Reset view' }))
    expect(screen.getByTestId('receiver_001-zoom').textContent).toBe(
      '1.00×',
    )

    contextSpy.mockRestore()
  })
})
