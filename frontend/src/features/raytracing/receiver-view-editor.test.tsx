// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'
import { createFaceEmitter } from './ray-tracing-model'
import { createViewAngleReceiver } from './receiver-view'
import { RayTracingPanel } from './ray-tracing-panel'
import { leakPreviewStore } from '@/features/leak-preview/leak-preview-store'

afterEach(() => { cleanup(); workspaceStore.getState().actions.resetWorkspace(); leakPreviewStore.getState().clear() })
function change(label: string, value: number) {
  const field=screen.getByRole('spinbutton',{name:label})
  fireEvent.change(field,{target:{value:String(value)}}); fireEvent.blur(field)
}
function renderPanel(run=vi.fn()) {
  workspaceStore.getState().actions.addCadCase({path:'receiver.step',displayName:'Receiver'})
  workspaceStore.getState().actions.upsertEmitter(createFaceEmitter('emitter_001',[0]))
  render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} onRunReceiverViewSweep={run} /></AppProviders>)
  return run
}

it('places and edits a view-angle Receiver without a camera or Preview result', () => {
  renderPanel()
  fireEvent.click(screen.getByRole('button',{name:'Add View Angle Receiver'}))
  expect(screen.getByRole('dialog',{name:'시야각 Receiver'})).toBeTruthy()
  expect(workspaceStore.getState().placementPreviewReceiver).toMatchObject({center:[30,30,-3],normal_flip:false,view_angles:{axis:'neg_z',range_deg:0}})
  change('Receiver 좌우 시야각 (°)',30)
  change('Receiver 주변 범위 (±°)',1)
  change('Receiver width (mm)',12)
  fireEvent.click(screen.getByRole('button',{name:'Add Receiver'}))
  const receiver=workspaceStore.getState().receivers[0]
  expect(receiver).toMatchObject({width_mm:12,view_angles:{horizontal_deg:30,range_deg:1,step_deg:1}})
  expect(leakPreviewStore.getState().result).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Edit Receiver 1'}))
  expect(screen.getByRole('spinbutton',{name:'Receiver 좌우 시야각 (°)'})).toHaveProperty('value','30.0')
  expect(screen.getByRole('spinbutton',{name:'Receiver 주변 범위 (±°)'})).toHaveProperty('value','1.0')
  fireEvent.click(screen.getByRole('button',{name:'Save Receiver'}))
  expect(workspaceStore.getState().receivers[0].view_angles).toEqual(receiver.view_angles)
})

it('dispatches a configured sweep and blocks an excessive angle grid before saving', () => {
  const run=renderPanel()
  fireEvent.click(screen.getByRole('button',{name:'Add View Angle Receiver'}))
  change('Receiver 주변 범위 (±°)',10)
  change('Receiver 각도 간격 (°)',.1)
  expect(screen.getByRole('alert').textContent).toContain('121')
  expect((screen.getByRole('button',{name:'Add Receiver'}) as HTMLButtonElement).disabled).toBe(true)
  change('Receiver 주변 범위 (±°)',1)
  change('Receiver 각도 간격 (°)',1)
  fireEvent.click(screen.getByRole('button',{name:'Add Receiver'}))
  expect(screen.getByText(/9각도/).textContent).toContain('90,000 Ray')
  fireEvent.click(screen.getByRole('button',{name:'Run Ray Tracing'}))
  expect(run).toHaveBeenCalledWith('receiver_001')
  act(() => { const source=workspaceStore.getState().receivers[0]; workspaceStore.getState().actions.upsertReceiver({...source,receiver_id:'other',view_angles:undefined}) })
  expect(screen.getByRole('alert').textContent).toContain('하나만 활성화')
  expect((screen.getByRole('button',{name:'Run Ray Tracing'}) as HTMLButtonElement).disabled).toBe(true)

})

it('copies entered Preview viewing conditions without running Preview', () => {
  renderPanel()
  act(() => { leakPreviewStore.getState().toggleDirection('pos_x'); leakPreviewStore.getState().setObserver({horizontal_deg:20,vertical_deg:-10,range_deg:2,step_deg:1}) })
  fireEvent.click(screen.getByRole('button',{name:'Add View Angle Receiver'}))
  fireEvent.click(screen.getByRole('button',{name:'Preview 각도 가져오기'}))
  fireEvent.click(screen.getByRole('button',{name:'Add Receiver'}))
  expect(workspaceStore.getState().receivers[0].view_angles).toEqual({axis:'pos_x',horizontal_deg:20,vertical_deg:-10,range_deg:2,step_deg:1})
  expect(leakPreviewStore.getState().jobId).toBeNull()
})

it('preserves a Preview angle receiver plane when opening and saving without a legacy distance offset', () => {
  renderPanel()
  const original = { ...createViewAngleReceiver('receiver_001', createSceneFixture(), [30, 30, 10],
    { axis: 'neg_z', horizontal_deg: 80, vertical_deg: 20, range_deg: 0, step_deg: .5 }, 3),
    reference_mode: 'leak_preview_candidate', display_name: 'Preview Receiver 1', width_mm: 26, height_mm: 38 }
  act(() => workspaceStore.getState().actions.upsertReceiver(original))
  const config = structuredClone(workspaceStore.getState().rayTraceConfig)
  fireEvent.click(screen.getByRole('button', { name: 'Edit Preview Receiver 1' }))
  expect(screen.getByRole('spinbutton', { name: 'Receiver 좌우 시야각 (°)' })).toHaveProperty('value', '80.0')
  expect(screen.getByRole('spinbutton', { name: 'Receiver 상하 시야각 (°)' })).toHaveProperty('value', '20.0')
  expect(screen.getByRole('spinbutton', { name: 'Receiver 주변 범위 (±°)' })).toHaveProperty('value', '0.0')
  expect(workspaceStore.getState().placementPreviewReceiver?.center).toEqual(original.center)
  fireEvent.click(screen.getByRole('button', { name: 'Save Receiver' }))
  const saved = workspaceStore.getState().receivers[0]
  expect(saved.center).toEqual(original.center)
  expect(saved.normal).toEqual(original.normal)
  expect(saved.view_angles).toEqual(original.view_angles)
  expect(saved.base_center).toEqual(original.base_center)
  expect(saved.width_mm).toBe(original.width_mm)
  expect(saved.height_mm).toBe(original.height_mm)
  expect(saved.reference_mode).toBe('leak_preview_candidate')
  expect(workspaceStore.getState().rayTraceConfig).toEqual(config)
})
