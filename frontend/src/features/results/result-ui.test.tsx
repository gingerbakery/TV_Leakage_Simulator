// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import {
  createCompletedRayTraceJobFixture,
  createRayTraceResultFixture,
} from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'

import { ResultPanel } from './result-panel'
import { RayTraceResultWindow } from './result-window'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  workspaceStore.getState().actions.resetWorkspace()
})

describe('Step 11 result UI', () => {
  it('separates the actual compute device from the acceleration structure', () => {
    const result = createRayTraceResultFixture()
    result.config.compute_backend = 'gpu_cuda'
    result.metrics._performance_summary = {
      ...(result.metrics._performance_summary as Record<string, unknown>),
      compute_backend: 'gpu_cuda',
      compute_execution_state: 'gpu_active',
      compute_execution_reason: null,
      intersection_provider: 'gpu_cuda',
      gpu_cuda_used: true,
      gpu_cuda_device_name: 'NVIDIA RTX Test',
      gpu_cuda_gpu_attempt_count: 3,
      gpu_cuda_gpu_success_count: 3,
    }

    render(
      <RayTraceResultWindow
        open
        result={result}
        onOpenChange={vi.fn()}
      />,
    )

    expect(
      screen.getByLabelText('Compute execution status').textContent,
    ).toContain('Compute device · GPU 활성')
    expect(screen.getByText('CUDA batches · 3/3')).not.toBeNull()
    const accelerationHelp = screen.getByRole('button', {
      name: 'Acceleration structure 설명',
    })
    expect(accelerationHelp.closest('p')?.textContent).toContain(
      'Acceleration structure',
    )
    expect(screen.queryByText('Intersection backend')).toBeNull()
  })

  it('shows a formatted Receiver name instead of its internal ID', () => {
    const result = createRayTraceResultFixture()
    result.receivers[0].display_name = 'receiver_001'

    render(
      <RayTraceResultWindow
        open
        result={result}
        onOpenChange={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('tab', { name: 'Receiver' }))

    expect(screen.getByText('Receiver 1')).not.toBeNull()
    expect(screen.queryByText('receiver_001')).toBeNull()
  })

  it('orders Receiver result cards and heatmaps by receiver number', () => {
    const result = createRayTraceResultFixture()
    const receiverTwo = structuredClone(result.receivers[0])
    receiverTwo.receiver_id = 'receiver_002'
    receiverTwo.display_name = 'Receiver 2'
    const receiverOne = structuredClone(result.receivers[0])
    receiverOne.receiver_id = 'receiver_001'
    receiverOne.display_name = 'Receiver 1'
    result.receivers = [receiverTwo, receiverOne]
    result.receiver_grids = [
      { ...structuredClone(result.receiver_grids[0]), receiver_id: 'receiver_002' },
      { ...structuredClone(result.receiver_grids[0]), receiver_id: 'receiver_001' },
    ]
    result.metrics.receiver_002 = structuredClone(result.metrics.receiver_001)

    render(
      <RayTraceResultWindow
        open
        result={result}
        onOpenChange={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('tab', { name: 'Receiver' }))

    const first = screen.getByText('Receiver 1')
    const second = screen.getByText('Receiver 2')
    expect(
      first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0)
  })

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
    fireEvent.change(screen.getByRole('combobox', { name: 'Compare Receiver' }), {
      target: { value: 'name:right corner' },
    })
    expect(screen.getByText('0.020 lm')).not.toBeNull()
    expect(screen.getByText('6.000')).not.toBeNull()
  })

  it('matches Receivers by visible name before checking their geometry', () => {
    const baseline = createRayTraceResultFixture()
    baseline.receivers[0].display_name = 'Front Receiver'
    const sideReceiver = {
      ...structuredClone(baseline.receivers[0]),
      receiver_id: 'receiver_002',
      display_name: 'Side Receiver',
      center: [25, 0, 0] as [number, number, number],
      width_mm: 12,
    }
    baseline.receivers.push(sideReceiver)
    baseline.metrics[sideReceiver.receiver_id] = structuredClone(
      baseline.metrics[baseline.receivers[0].receiver_id],
    )
    const comparison = structuredClone(baseline)
    comparison.run_id = 'run-reordered-receivers'
    comparison.receivers = [
      comparison.receivers[1],
      comparison.receivers[0],
    ]

    render(
      <RayTraceResultWindow
        open
        result={baseline}
        reportCases={[
          { caseId: 'case-1', name: 'CASE 01', cadName: 'a.step', result: baseline },
          { caseId: 'case-2', name: 'CASE 02', cadName: 'b.step', result: comparison },
        ]}
        onOpenChange={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: 'Compare cases' }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Compare Receiver' }), {
      target: { value: 'name:front receiver' },
    })
    expect(screen.getAllByText('50.0')).toHaveLength(2)
  })

  it('keeps geometry validation after matching Receivers by name', () => {
    const baseline = createRayTraceResultFixture()
    baseline.receivers[0].display_name = 'Front Receiver'
    const comparison = structuredClone(baseline)
    comparison.run_id = 'run-different-receiver-geometry'
    comparison.receivers[0].width_mm += 1

    render(
      <RayTraceResultWindow
        open
        result={baseline}
        reportCases={[
          { caseId: 'case-1', name: 'CASE 01', cadName: 'a.step', result: baseline },
          { caseId: 'case-2', name: 'CASE 02', cadName: 'b.step', result: comparison },
        ]}
        onOpenChange={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: 'Compare cases' }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Compare Receiver' }), {
      target: { value: 'name:front receiver' },
    })
    expect(screen.getAllByText('50.0')).toHaveLength(1)
  })

  it('deletes one Receiver result from the current report Case', async () => {
    const result = createRayTraceResultFixture()
    result.receivers[0].display_name = 'Front Receiver'
    const sideReceiver = {
      ...structuredClone(result.receivers[0]),
      receiver_id: 'receiver_002',
      display_name: 'Side Receiver',
    }
    result.receivers.push(sideReceiver)
    result.metrics[sideReceiver.receiver_id] = structuredClone(
      result.metrics[result.receivers[0].receiver_id],
    )
    const onDeleteCaseReceiverResult = vi.fn()

    render(
      <RayTraceResultWindow
        open
        result={result}
        reportCases={[
          { caseId: 'case-1', name: 'CASE 01', cadName: 'a.step', result },
        ]}
        onDeleteCaseReceiverResult={onDeleteCaseReceiverResult}
        onOpenChange={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: 'Receiver' }))
    const deleteButton = await screen.findByRole('button', {
      name: 'Delete Side Receiver result from current case',
    })
    fireEvent.click(deleteButton)
    expect(onDeleteCaseReceiverResult).toHaveBeenCalledWith(
      'case-1',
      sideReceiver.receiver_id,
    )
    await waitFor(() => {
      expect(
        screen.queryByRole('button', {
          name: 'Delete Side Receiver result from current case',
        }),
      ).toBeNull()
    })
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

  it('opens a save-location picker when saving a comparison report', async () => {
    const result = createRayTraceResultFixture()
    const write = vi.fn().mockResolvedValue(undefined)
    const close = vi.fn().mockResolvedValue(undefined)
    const showSaveFilePicker = vi.fn(function (this: unknown) {
      expect(this).toBe(window)
      return Promise.resolve({
        createWritable: vi.fn().mockResolvedValue({ write, close }),
      })
    })
    vi.stubGlobal('showSaveFilePicker', showSaveFilePicker)

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
    fireEvent.click(screen.getByRole('button', { name: 'Save report' }))

    await waitFor(() => expect(showSaveFilePicker).toHaveBeenCalledOnce())
    expect(showSaveFilePicker).toHaveBeenCalledWith(
      expect.objectContaining({
        suggestedName: expect.stringMatching(
          /^ray-analysis-\d{4}-\d{2}-\d{2}\.bitsam-report$/,
        ),
      }),
    )
    expect(write).toHaveBeenCalledWith(expect.any(Blob))
    expect(close).toHaveBeenCalledOnce()
  })

  it('downloads the report when the native save picker fails', async () => {
    const result = createRayTraceResultFixture()
    const pickerError = new DOMException('Blocked by policy', 'NotAllowedError')
    vi.stubGlobal(
      'showSaveFilePicker',
      vi.fn().mockRejectedValue(pickerError),
    )
    const createObjectURL = vi.fn().mockReturnValue('blob:analysis-report')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const anchorClick = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined)
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => undefined)

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
    fireEvent.click(screen.getByRole('button', { name: 'Save report' }))

    await waitFor(() => expect(anchorClick).toHaveBeenCalledOnce())
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob))
    expect(alert).toHaveBeenCalledWith(
      expect.stringContaining('다운로드 폴더에 저장했습니다'),
    )
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
    expect(dialog.style.width).toBe('1120px')
    expect(dialog.style.height).toBe('880px')
  })

  it('maximizes, restores, and exposes Windows-style resize edges', () => {
    vi.stubGlobal('innerWidth', 1200)
    vi.stubGlobal('innerHeight', 1000)

    render(
      <RayTraceResultWindow
        open
        result={createRayTraceResultFixture()}
        onOpenChange={vi.fn()}
      />,
    )

    const dialog = screen.getByRole('dialog', {
      name: 'Ray Tracing Analysis Result',
    })
    expect(
      dialog.querySelectorAll('[data-result-resize-edge]'),
    ).toHaveLength(8)

    const maximizeButton = screen.getByRole('button', {
      name: 'Maximize result window',
    })
    const maximizeIcon = maximizeButton.querySelector('svg')
    expect(maximizeIcon).not.toBeNull()
    fireEvent.pointerDown(maximizeIcon!, { clientX: 1100, clientY: 72 })
    fireEvent.pointerMove(window, { clientX: 800, clientY: 300 })
    fireEvent.pointerUp(window)
    expect(dialog.style.left).toBe('24px')
    expect(dialog.style.top).toBe('58px')

    fireEvent.click(maximizeButton)
    expect(dialog.getAttribute('data-window-state')).toBe('maximized')
    expect(dialog.style.left).toBe('0px')
    expect(dialog.style.top).toBe('0px')
    expect(dialog.style.width).toBe('1200px')
    expect(dialog.style.height).toBe('1000px')
    expect(
      dialog.querySelectorAll('[data-result-resize-edge]'),
    ).toHaveLength(0)

    fireEvent.click(
      screen.getByRole('button', { name: 'Restore result window' }),
    )
    expect(dialog.getAttribute('data-window-state')).toBe('windowed')
    expect(dialog.style.left).toBe('24px')
    expect(dialog.style.top).toBe('58px')
    expect(dialog.style.width).toBe('1120px')
    expect(dialog.style.height).toBe('880px')

    const titlebar = screen.getByTestId('result-window-titlebar')
    fireEvent.doubleClick(titlebar)
    expect(dialog.getAttribute('data-window-state')).toBe('maximized')
    fireEvent.doubleClick(titlebar)
    expect(dialog.getAttribute('data-window-state')).toBe('windowed')
  })

  it('resizes from either side and keeps the window inside the viewport', () => {
    vi.stubGlobal('innerWidth', 1200)
    vi.stubGlobal('innerHeight', 1000)

    render(
      <RayTraceResultWindow
        open
        result={createRayTraceResultFixture()}
        onOpenChange={vi.fn()}
      />,
    )

    const dialog = screen.getByRole('dialog', {
      name: 'Ray Tracing Analysis Result',
    })
    const eastHandle = dialog.querySelector<HTMLElement>(
      '[data-result-resize-edge="e"]',
    )
    expect(eastHandle).not.toBeNull()
    fireEvent.pointerDown(eastHandle!, { clientX: 1100, clientY: 400 })
    fireEvent.pointerMove(window, { clientX: 980, clientY: 400 })
    fireEvent.pointerUp(window)
    expect(dialog.style.width).toBe('1000px')

    const westHandle = dialog.querySelector<HTMLElement>(
      '[data-result-resize-edge="w"]',
    )
    fireEvent.pointerDown(westHandle!, { clientX: 24, clientY: 400 })
    fireEvent.pointerMove(window, { clientX: 124, clientY: 400 })
    fireEvent.pointerUp(window)
    expect(dialog.style.left).toBe('124px')
    expect(dialog.style.width).toBe('900px')
  })

  it('clamps the complete result window when the viewport is smaller', () => {
    vi.stubGlobal('innerWidth', 1000)
    vi.stubGlobal('innerHeight', 700)

    render(
      <RayTraceResultWindow
        open
        result={createRayTraceResultFixture()}
        onOpenChange={vi.fn()}
      />,
    )

    const dialog = screen.getByRole('dialog', {
      name: 'Ray Tracing Analysis Result',
    })
    const left = Number.parseFloat(dialog.style.left)
    const top = Number.parseFloat(dialog.style.top)
    const width = Number.parseFloat(dialog.style.width)
    const height = Number.parseFloat(dialog.style.height)
    expect(left + width).toBeLessThanOrEqual(992)
    expect(top + height).toBeLessThanOrEqual(692)
  })

  it('tracks viewport changes and cancels a stale resize operation', () => {
    vi.stubGlobal('innerWidth', 1200)
    vi.stubGlobal('innerHeight', 1000)

    render(
      <RayTraceResultWindow
        open
        result={createRayTraceResultFixture()}
        onOpenChange={vi.fn()}
      />,
    )

    const dialog = screen.getByRole('dialog', {
      name: 'Ray Tracing Analysis Result',
    })
    const eastHandle = dialog.querySelector<HTMLElement>(
      '[data-result-resize-edge="e"]',
    )
    fireEvent.pointerDown(eastHandle!, { clientX: 1144, clientY: 400 })

    vi.stubGlobal('innerWidth', 900)
    vi.stubGlobal('innerHeight', 600)
    fireEvent(window, new Event('resize'))
    expect(dialog.style.left).toBe('8px')
    expect(dialog.style.top).toBe('8px')
    expect(dialog.style.width).toBe('884px')
    expect(dialog.style.height).toBe('584px')

    fireEvent.pointerMove(window, { clientX: 800, clientY: 400 })
    fireEvent.pointerUp(window)
    expect(dialog.style.left).toBe('8px')
    expect(dialog.style.width).toBe('884px')

    fireEvent.click(
      screen.getByRole('button', { name: 'Maximize result window' }),
    )
    vi.stubGlobal('innerWidth', 1024)
    vi.stubGlobal('innerHeight', 768)
    fireEvent(window, new Event('resize'))
    expect(dialog.style.left).toBe('0px')
    expect(dialog.style.top).toBe('0px')
    expect(dialog.style.width).toBe('1024px')
    expect(dialog.style.height).toBe('768px')
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
    const comparisonResult = structuredClone(result)
    comparisonResult.run_id = 'run-comparison-scale'
    comparisonResult.metrics.receiver_001 = {
      ...(comparisonResult.metrics.receiver_001 as Record<string, unknown>),
      peak_nit_est: 25,
    }
    const createImageData = vi.fn(
      (width: number, height: number) =>
        ({
          data: new Uint8ClampedArray(width * height * 4),
          height,
          width,
        }) as ImageData,
    )
    const putImageData = vi.fn()
    const onOpenChange = vi.fn()
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
          scene={createSceneFixture()}
          componentNameOverrides={{ 1: 'Renamed chassis' }}
          reportCases={[
            { caseId: 'case-1', name: 'CASE 01', cadName: 'a.step', result },
            { caseId: 'case-2', name: 'CASE 02', cadName: 'b.step', result: comparisonResult },
          ]}
          onOpenChange={onOpenChange}
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

    const autoScaleButton = screen.getByRole('button', { name: 'Auto' })
    expect(autoScaleButton.getAttribute('aria-pressed')).toBe('true')
    const scale = screen.getByTestId('receiver_001-luminance-scale')
    expect(scale.getAttribute('data-scale-mode')).toBe('auto')
    expect(Number(scale.getAttribute('data-scale-max-nit'))).toBeCloseTo(12.5)
    fireEvent.click(screen.getByRole('button', { name: 'Compare' }))
    expect(scale.getAttribute('data-scale-mode')).toBe('compare')
    expect(Number(scale.getAttribute('data-scale-max-nit'))).toBeCloseTo(25)
    fireEvent.click(screen.getByRole('button', { name: 'Customize' }))
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Custom luminance scale minimum' }),
      { target: { value: '1' } },
    )
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Custom luminance scale maximum' }),
      { target: { value: '20' } },
    )
    expect(scale.getAttribute('data-scale-mode')).toBe('customize')
    expect(scale.getAttribute('data-scale-min-nit')).toBe('1')
    expect(scale.getAttribute('data-scale-max-nit')).toBe('20')

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
    const yProfileFrame = screen.getByTestId(
      'receiver_001-y-profile-frame',
    )
    expect(xAxis.querySelector('[data-axis-tick="0"]')).not.toBeNull()
    expect(yAxis.querySelector('[data-axis-tick="0"]')).not.toBeNull()
    expect(yProfileFrame.firstElementChild?.className).toContain(
      'absolute inset-0',
    )
    const xProfileCard = document.querySelector<HTMLElement>(
      '[data-profile-axis="X"]',
    )
    const yProfileCard = document.querySelector<HTMLElement>(
      '[data-profile-axis="Y"]',
    )
    const yProfileSummary = document.querySelector<HTMLElement>(
      '[data-profile-summary="Y"]',
    )
    expect(xProfileCard?.textContent).toContain('Y =')
    expect(yProfileCard?.textContent).toContain('X =')
    expect(yProfileSummary?.className).toContain('text-[10px]')
    expect(yProfileSummary?.textContent).toContain('Peak')
    expect(yProfileSummary?.textContent).toContain('Scale')
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
    const drawCountBeforeMono = putImageData.mock.calls.length
    expect(drawCountBeforeMono).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: 'Mono' }))
    expect(screen.getByRole('button', { name: 'Mono' }).getAttribute('aria-pressed')).toBe('true')
    expect(putImageData).toHaveBeenCalledTimes(drawCountBeforeMono + 1)

    const xProfile = screen.getByRole('img', {
      name: 'X-axis luminance profile',
    })
    vi.spyOn(xProfile, 'getBoundingClientRect').mockReturnValue({
      bottom: 80,
      height: 80,
      left: 0,
      right: 400,
      top: 0,
      width: 400,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    })
    fireEvent.pointerMove(xProfile, { clientX: 200, clientY: 20 })
    expect(screen.getAllByRole('tooltip').some((item) => item.textContent?.includes('nit'))).toBe(true)
    fireEvent.pointerLeave(xProfile)

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
    expect(xProfileCard?.textContent).toContain('mm')
    expect(xProfileCard?.textContent).toContain('nit')
    fireEvent.click(screen.getByRole('button', { name: 'Error map' }))
    expect(screen.getByRole('button', { name: 'Error map' }).getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: 'Analyze area' }))
    fireEvent.pointerDown(viewport, { clientX: 100, clientY: 50, pointerId: 2 })
    fireEvent.pointerMove(viewport, { clientX: 300, clientY: 150, pointerId: 2 })
    fireEvent.pointerUp(viewport, { clientX: 300, clientY: 150, pointerId: 2 })
    expect(screen.getByText('Selected-area ray contribution')).not.toBeNull()
    expect(screen.getAllByText('Renamed chassis').length).toBeGreaterThan(0)
    expect(screen.getByTestId('receiver_001-analysis-region')).not.toBeNull()
    expect(tooltip.textContent).toContain('Incident flux')
    expect(tooltip.textContent).toContain('Pixel error')
    fireEvent.click(screen.getByRole('button', { name: /Direct to Receiver/ }))
    expect(workspaceStore.getState().highlightedRayPathSelection).toEqual({
      runId: result.run_id,
      pathIndices: [0],
      label: 'Direct to Receiver',
    })
    expect(onOpenChange).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Reset view' }))
    expect(screen.getByTestId('receiver_001-zoom').textContent).toBe(
      '1.00×',
    )

    contextSpy.mockRestore()
  })
})
