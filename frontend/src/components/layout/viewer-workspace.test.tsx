// @vitest-environment jsdom

import { useState, type ComponentProps } from 'react'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { createRayTraceResultSourceContext } from '@/features/raytracing'
import type { ThreeViewerCanvas } from '@/features/viewer'
import { workspaceStore } from '@/stores'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'

import { ViewerWorkspace } from './viewer-workspace'

const { viewerCanvasMock } = vi.hoisted(() => ({
  viewerCanvasMock: vi.fn((_props: unknown) => null),
}))

vi.mock('@/features/viewer', () => ({ ThreeViewerCanvas: viewerCanvasMock }))

function bindResultSource(
  result: ReturnType<typeof createRayTraceResultFixture>,
  scene: ReturnType<typeof createSceneFixture>,
  caseId: string,
  cadName: string,
) {
  result.source_context = createRayTraceResultSourceContext(
    scene,
    {
      scene_token: scene.metadata.scene_token,
      project_name: cadName,
      emitters: structuredClone(result.emitters),
      receivers: structuredClone(result.receivers),
      optical_profiles: structuredClone(result.optical_profiles),
      optical_assignments: [],
      transform_rules: [],
      excluded_component_ids: [],
      config: structuredClone(result.config),
    },
    caseId,
  )
  return result
}

function LeakagePreviewHarness({
  scene,
  result,
  onResultOpenChange,
}: {
  scene: ReturnType<typeof createSceneFixture>
  result: ReturnType<typeof createRayTraceResultFixture>
  onResultOpenChange(open: boolean): void
}) {
  const [resultOpen, setResultOpen] = useState(true)
  return (
    <ViewerWorkspace
      scene={scene}
      rayTraceResult={result}
      rayTraceResultOpen={resultOpen}
      onRayTraceResultOpenChange={(open) => {
        onResultOpenChange(open)
        setResultOpen(open)
      }}
    />
  )
}

afterEach(() => {
  cleanup()
  viewerCanvasMock.mockClear()
  workspaceStore.getState().actions.resetWorkspace()
})

describe('ViewerWorkspace display controls', () => {
  it('keeps the viewer toolbar bounded and wraps camera presets on narrow screens', () => {
    const view = render(<ViewerWorkspace />)
    const toolbar = view.container.querySelector<HTMLElement>(
      '[data-viewer-toolbar]',
    )
    const cameraPresets = screen.getByLabelText('Camera presets')
    const renderModes = screen.getByLabelText('Render modes')

    expect(toolbar?.className).toContain('w-full')
    expect(toolbar?.className).toContain('min-w-0')
    expect(cameraPresets.className).toContain('grid-cols-4')
    expect(cameraPresets.className).toContain('min-[390px]:flex')
    expect(renderModes.className).toContain('max-w-full')
    expect(renderModes.className).toContain('flex-wrap')
  })

  it('enables surface transparency only for surface render modes', () => {
    render(<ViewerWorkspace />)

    const transparencySlider = screen.getByRole('slider', {
      name: 'Surface transparency',
    }) as HTMLInputElement

    expect(transparencySlider.disabled).toBe(false)
    expect(transparencySlider.value).toBe('0')

    fireEvent.change(transparencySlider, {
      target: { value: '45' },
    })
    expect(transparencySlider.value).toBe('45')

    fireEvent.click(
      screen.getByRole('button', { name: 'Wireframe' }),
    )
    expect(transparencySlider.disabled).toBe(true)

    fireEvent.click(
      screen.getByRole('button', { name: 'Surface' }),
    )
    expect(transparencySlider.disabled).toBe(false)
  })

  it('opens the selected source-bound Case in the leakage preview and closes Result', () => {
    const scene = createSceneFixture()
    const actions = workspaceStore.getState().actions

    actions.addCadCase({ path: 'case-a.step', displayName: 'case-a.step' })
    const caseAId = workspaceStore.getState().activeCadCaseId!
    const first = bindResultSource(
      createRayTraceResultFixture(),
      scene,
      caseAId,
      'case-a.step',
    )
    first.run_id = 'run-case-a'
    first.receiver_hit_count = 0
    actions.setActiveCadCaseResult(first)

    actions.addCadCase({ path: 'case-b.step', displayName: 'case-b.step' })
    const caseBId = workspaceStore.getState().activeCadCaseId!
    const second = bindResultSource(
      createRayTraceResultFixture(),
      scene,
      caseBId,
      'case-b.step',
    )
    second.run_id = 'run-case-b'
    second.receiver_hit_count = 0
    actions.setActiveCadCaseResult(second)
    actions.setActiveCadCase(caseAId)

    const onResultOpenChange = vi.fn()
    render(
      <LeakagePreviewHarness
        scene={scene}
        result={first}
        onResultOpenChange={onResultOpenChange}
      />,
    )

    fireEvent.change(
      screen.getByRole('combobox', { name: 'Report active case' }),
      { target: { value: caseBId } },
    )
    fireEvent.click(screen.getByRole('button', { name: '3D 빛샘 보기' }))

    expect(workspaceStore.getState().activeCadCaseId).toBe(caseBId)
    expect(
      workspaceStore.getState().cadCases.find((item) => item.caseId === caseBId)
        ?.visible,
    ).toBe(true)
    expect(onResultOpenChange).toHaveBeenCalledWith(false)
    expect(
      screen.queryByRole('dialog', { name: 'Ray Tracing Analysis Result' }),
    ).toBeNull()
    expect(
      screen.getByRole('heading', { name: '3D 빛샘 보기' }),
    ).not.toBeNull()
    expect(screen.getByText('시제품 · 외곽 경계 추정')).not.toBeNull()
    expect(
      screen.getByText(
        '출구 점 표본 시제품 · 연속 표시 기준 미충족 · 상대 표시 하한 적용 · 밝기·크기 미보정 · 외관 보조 조명 · run-case-b',
      ),
    ).not.toBeNull()

    act(() => {
      actions.upsertTransformRule({
        ruleId: 'changed-after-run',
        componentId: scene.components[0].component_id,
        targetType: 'component',
        selectionMethod: 'click',
        faceIds: [],
        move: { x: 1, y: 0, z: 0 },
        tilt: { x: 0, y: 0, z: 0 },
        enabled: true,
      })
    })
    expect(screen.getByRole('heading', { name: '3D Viewer' })).not.toBeNull()
    expect(screen.queryByText('시제품 · 외곽 경계 추정')).toBeNull()
  })

  it('adjusts exterior display without replacing leakage samples or the completed result', () => {
    const scene = createSceneFixture()
    const actions = workspaceStore.getState().actions
    actions.addCadCase({ path: 'display.step', displayName: 'display.step' })
    const caseId = workspaceStore.getState().activeCadCaseId!
    const fixture = createRayTraceResultFixture()
    fixture.stored_paths = [fixture.stored_paths[0]]
    fixture.stored_paths[0][0].point = [10, 10, 10]
    fixture.stored_paths[0][1].point = [10, 10, 30]
    fixture.receiver_hit_count = 1
    fixture.receiver_grids[0].hit_count = 1
    const result = bindResultSource(fixture, scene, caseId, 'display.step')
    actions.setActiveCadCaseResult(result)
    const storedResult = workspaceStore.getState().cadCases[0].latestResult
    const resultSnapshot = structuredClone(storedResult)

    render(
      <LeakagePreviewHarness
        scene={scene}
        result={result}
        onResultOpenChange={vi.fn()}
      />,
    )
    expect(screen.queryByRole('slider', { name: '외관 밝기' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '3D 빛샘 보기' }))

    const initialProps = viewerCanvasMock.mock.lastCall![0] as ComponentProps<
      typeof ThreeViewerCanvas
    >
    expect(initialProps.leakagePreviewSamples).toHaveLength(1)
    expect(initialProps.leakageExteriorBrightness).toBe(60)
    expect(initialProps.leakageExteriorColor).toBe('black')
    expect(initialProps.leakageExteriorFinish).toBe('matte')
    expect(
      screen.getByText('외관 확인용 조명 · 빛샘 밝기에는 영향 없음'),
    ).not.toBeNull()

    fireEvent.change(screen.getByRole('slider', { name: '외관 밝기' }), {
      target: { value: '85' },
    })
    fireEvent.change(screen.getByRole('combobox', { name: '외관 색상' }), {
      target: { value: 'silver' },
    })
    fireEvent.change(screen.getByRole('combobox', { name: '표면 재질' }), {
      target: { value: 'satin' },
    })

    const changedProps = viewerCanvasMock.mock.lastCall![0] as ComponentProps<
      typeof ThreeViewerCanvas
    >
    expect(changedProps.leakageExteriorBrightness).toBe(85)
    expect(changedProps.leakageExteriorColor).toBe('silver')
    expect(changedProps.leakageExteriorFinish).toBe('satin')
    expect(changedProps.leakagePreviewSamples).toBe(initialProps.leakagePreviewSamples)
    expect(changedProps.leakagePreviewRequest).toBe(initialProps.leakagePreviewRequest)
    expect(changedProps.cameraRequestId).toBe(initialProps.cameraRequestId)
    expect(workspaceStore.getState().cadCases[0].latestResult).toBe(storedResult)
    expect(storedResult).toEqual(resultSnapshot)

    fireEvent.change(screen.getByRole('combobox', { name: '관측 방향' }), {
      target: { value: 'YZ' },
    })
    const sideProps = viewerCanvasMock.mock.lastCall![0] as ComponentProps<
      typeof ThreeViewerCanvas
    >
    expect(sideProps.cameraPreset).toBe('YZ')
    expect(sideProps.cameraRequestId).toBe(initialProps.cameraRequestId + 1)
    expect(sideProps.leakagePreviewSamples).toBe(initialProps.leakagePreviewSamples)
    expect(sideProps.leakageSurfacePreview).toBe(initialProps.leakageSurfacePreview)
    expect(sideProps.leakagePreviewRequest).toBe(initialProps.leakagePreviewRequest)
    expect(storedResult).toEqual(resultSnapshot)

    fireEvent.click(screen.getByRole('button', { name: 'Result로 돌아가기' }))
    expect(screen.queryByRole('slider', { name: '외관 밝기' })).toBeNull()
    expect(screen.queryByRole('combobox', { name: '외관 색상' })).toBeNull()
    expect(screen.queryByRole('combobox', { name: '표면 재질' })).toBeNull()
    expect(screen.getByRole('slider', { name: 'Surface transparency' })).not.toBeNull()
  })
  it('compares completed results directly while retaining the chosen view and source settings', () => {
    const scene = createSceneFixture()
    const actions = workspaceStore.getState().actions
    const completedCases = [1, 4].map((power) => {
      actions.addCadCase({ path: 'power-' + power + '.step', displayName: 'power-' + power + '.step' })
      const caseId = workspaceStore.getState().activeCadCaseId!
      const fixture = createRayTraceResultFixture()
      fixture.run_id = 'completed-power-' + power
      fixture.emitters.forEach((emitter) => { emitter.power_mode = 'total'; emitter.power_lumen = power })
      fixture.stored_paths = [fixture.stored_paths[0]]
      fixture.stored_paths[0][0].point = [10, 10, 10]
      fixture.stored_paths[0][1].point = [10, 10, 30]
      fixture.stored_paths[0][1].incoming_energy_lumen = power * 0.001
      fixture.receiver_hit_count = 1
      fixture.receiver_grids[0].hit_count = 1
      const result = bindResultSource(fixture, scene, caseId, 'power-' + power + '.step')
      actions.setActiveCadCaseResult(result)
      return { caseId, result }
    })
    const [first, second] = completedCases
    actions.setActiveCadCase(first.caseId)
    const storedResults = workspaceStore.getState().cadCases.map((item) => item.latestResult)
    const resultSnapshots = structuredClone(storedResults)
    const onResultOpenChange = vi.fn()
    render(<LeakagePreviewHarness scene={scene} result={first.result} onResultOpenChange={onResultOpenChange} />)
    fireEvent.click(screen.getByRole('button', { name: '3D 빛샘 보기' }))
    fireEvent.change(screen.getByRole('combobox', { name: '관측 방향' }), { target: { value: 'YZ' } })
    const before = viewerCanvasMock.mock.lastCall![0] as ComponentProps<typeof ThreeViewerCanvas>
    expect(before.cameraPreset).toBe('YZ')
    const resultWriter = vi.spyOn(actions, 'setActiveCadCaseResult')

    fireEvent.change(screen.getByRole('combobox', { name: '3D 비교 결과' }), { target: { value: second.caseId } })

    const after = viewerCanvasMock.mock.lastCall![0] as ComponentProps<typeof ThreeViewerCanvas>
    const secondStored = workspaceStore.getState().cadCases.find((item) => item.caseId === second.caseId)!.latestResult!
    expect(workspaceStore.getState().activeCadCaseId).toBe(second.caseId)
    expect(after.leakagePreviewActive).toBe(true)
    expect(after.cameraPreset).toBe('YZ')
    expect(after.cameraRequestId).toBe(before.cameraRequestId)
    expect(after.leakagePreviewSamples?.[0].runId).toBe(second.result.run_id)
    expect(after.leakagePreviewSamples?.[0].weight).toBeCloseTo(before.leakagePreviewSamples![0].weight * 4)
    expect(after.leakagePreviewRequest).toBe(secondStored.source_context!.requests.at(-1))
    expect(screen.queryByRole('dialog', { name: 'Ray Tracing Analysis Result' })).toBeNull()
    expect(screen.getByRole('heading', { name: '3D 빛샘 보기' })).not.toBeNull()
    expect(resultWriter).not.toHaveBeenCalled()
    expect(workspaceStore.getState().cadCases.map((item) => item.latestResult)).toEqual(resultSnapshots)
    storedResults.forEach((result, index) => expect(workspaceStore.getState().cadCases[index].latestResult).toBe(result))
    resultWriter.mockRestore()
  })

  it('retries a failed comparison scene while retaining Cases, results and the captured camera', () => {
    const scene = createSceneFixture()
    const actions = workspaceStore.getState().actions
    const completedCases = ['first', 'second'].map((name) => {
      actions.addCadCase({ path: name + '.step', displayName: name + '.step' })
      const caseId = workspaceStore.getState().activeCadCaseId!
      const fixture = createRayTraceResultFixture()
      fixture.run_id = 'recovery-' + name
      fixture.receiver_hit_count = 0
      const result = bindResultSource(fixture, scene, caseId, name + '.step')
      actions.setActiveCadCaseResult(result)
      return { caseId, result }
    })
    actions.setActiveCadCase(completedCases[0].caseId)
    const retry = vi.fn()
    function RecoveryHarness({ error = false, loading = false }: { error?: boolean; loading?: boolean }) {
      const [resultOpen, setResultOpen] = useState(true)
      return <ViewerWorkspace scene={error || loading ? undefined : scene}
        isSceneLoading={loading}
        sceneErrorMessage={error ? 'Python API 서버에 연결할 수 없습니다.' : undefined}
        onRetryScene={retry}
        rayTraceResult={completedCases[0].result}
        rayTraceResultOpen={resultOpen} onRayTraceResultOpenChange={setResultOpen} />
    }
    const view = render(<RecoveryHarness />)
    fireEvent.click(screen.getByRole('button', { name: '3D 빛샘 보기' }))
    const before = viewerCanvasMock.mock.lastCall![0] as ComponentProps<typeof ThreeViewerCanvas>
    const camera = { position: [33, 22, 11] as [number, number, number], target: [4, 5, 6] as [number, number, number],
      up: [0, 1, 0] as [number, number, number], fov: 35, near: 0.01, far: 1000 }
    act(() => before.onCameraSnapshotChange?.(camera))
    fireEvent.change(screen.getByRole('combobox', { name: '3D 비교 결과' }), { target: { value: completedCases[1].caseId } })
    const expectedCases = workspaceStore.getState().cadCases
    const expectedResults = expectedCases.map((item) => item.latestResult)
    const selected = viewerCanvasMock.mock.lastCall![0] as ComponentProps<typeof ThreeViewerCanvas>
    expect(selected.leakageCameraRestore?.snapshot).toEqual(camera)

    view.rerender(<RecoveryHarness error />)
    fireEvent.click(screen.getByRole('button', { name: '다시 연결' }))
    expect(retry).toHaveBeenCalledOnce()
    expect(workspaceStore.getState().cadCases).toBe(expectedCases)
    expect(workspaceStore.getState().activeCadCaseId).toBe(completedCases[1].caseId)
    expect(screen.getByRole('heading', { name: '3D 빛샘 보기' })).not.toBeNull()
    view.rerender(<RecoveryHarness loading />)
    expect(screen.queryByRole('button', { name: '다시 연결' })).toBeNull()
    view.rerender(<RecoveryHarness />)

    const recovered = viewerCanvasMock.mock.lastCall![0] as ComponentProps<typeof ThreeViewerCanvas>
    expect(recovered.leakagePreviewActive).toBe(true)
    expect(recovered.leakageCameraRestore).toEqual(selected.leakageCameraRestore)
    expectedResults.forEach((result, index) => expect(workspaceStore.getState().cadCases[index].latestResult).toBe(result))
    expect(screen.queryByRole('dialog', { name: 'Ray Tracing Analysis Result' })).toBeNull()
  })

})

it('automatically opens a source-bound demo once with a right-side context view and retains later user direction', async () => {
  const scene = createSceneFixture()
  const actions = workspaceStore.getState().actions
  actions.addCadCase({ path: 'demo.step', displayName: 'demo.step' })
  const caseId = workspaceStore.getState().activeCadCaseId!
  const result = bindResultSource(createRayTraceResultFixture(), scene, caseId, 'demo.step')
  result.receiver_hit_count = 0
  actions.setActiveCadCaseResult(result)
  const request = { caseId, runId: result.run_id, result, initialCameraPreset: 'YZ' as const, contextDistanceScale: 3.5 }
  const onOpenChange = vi.fn()
  const view = render(<ViewerWorkspace scene={scene} leakagePreviewRequest={request} onRayTraceResultOpenChange={onOpenChange} />)
  await act(async () => undefined)
  expect(screen.getByRole('heading', { name: '3D 빛샘 보기' })).not.toBeNull()
  const canvasProps = () => viewerCanvasMock.mock.calls.at(-1)?.[0] as ComponentProps<typeof ThreeViewerCanvas>
  expect(canvasProps().cameraPreset).toBe('YZ')
  expect(canvasProps().leakageInitialView).toEqual({ id: caseId + ':' + result.run_id, preset: 'YZ', distanceScale: 3.5 })
  expect(onOpenChange).toHaveBeenCalledTimes(1)
  fireEvent.change(screen.getByRole('combobox', { name: '관측 방향' }), { target: { value: 'ZX' } })
  view.rerender(<ViewerWorkspace scene={scene} leakagePreviewRequest={{ ...request }} onRayTraceResultOpenChange={onOpenChange} />)
  expect(canvasProps().cameraPreset).toBe('ZX')
  expect(onOpenChange).toHaveBeenCalledTimes(1)
})