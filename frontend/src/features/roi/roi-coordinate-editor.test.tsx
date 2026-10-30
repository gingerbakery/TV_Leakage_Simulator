// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'
import { groupRoiFacesByComponent } from './roi-selection'
import { RoiSelectionPanel } from './roi-selection-panel'

afterEach(() => {
  cleanup()
  workspaceStore.getState().actions.resetWorkspace()
})

function setup(unbounded: boolean) {
  const scene = createSceneFixture()
  act(() => {
    workspaceStore.getState().actions.addRoiScope({
      label: 'Preview ROI', source: 'box', view: 'coordinate',
      clipBox: { plane: unbounded ? 'xy' : 'xyz', zUnbounded: unbounded,
        xMin: 0, xMax: 60, yMin: 0, yMax: 60, zMin: -1, zMax: 1 },
      observerView: { horizontalDeg: 30, verticalDeg: 10, direction: [0, 0, -1] },
      components: groupRoiFacesByComponent(scene, [0, 1, 2]),
    })
    workspaceStore.getState().actions.setRoiScopeActive('roi-1', false)
  })
  render(<AppProviders><RoiSelectionPanel scene={scene} /></AppProviders>)
  const original = structuredClone(workspaceStore.getState().roiScopes[0])
  fireEvent.click(screen.getByRole('button', { name: 'Preview ROI ROI 좌표 수정' }))
  return original
}

it('edits automatic ROI bounds in place, recomputes faces at all Z depths and retains viewing metadata', () => {
  const original = setup(true)
  expect(screen.getByLabelText('Preview ROI Z1')).toHaveProperty('readOnly', true)
  expect(screen.getByLabelText('Preview ROI Z1')).toHaveProperty('value', '−∞')
  for (const name of ['X1', 'Y1']) fireEvent.change(screen.getByLabelText(`Preview ROI ${name}`), { target: { value: '5' } })
  for (const name of ['X2', 'Y2']) fireEvent.change(screen.getByLabelText(`Preview ROI ${name}`), { target: { value: '55' } })
  expect(workspaceStore.getState().roiScopes[0]).toEqual(original)
  fireEvent.click(screen.getByRole('button', { name: 'Preview ROI 좌표 적용' }))
  const updated = workspaceStore.getState().roiScopes[0]
  expect(workspaceStore.getState().roiScopes).toHaveLength(1)
  expect(updated).toMatchObject({ id: original.id, scopeId: original.scopeId, active: false,
    observerView: original.observerView,
    clipBox: { plane: 'xy', zUnbounded: true, xMin: 5, xMax: 55, yMin: 5, yMax: 55, zMin: -1, zMax: 1 } })
  expect(updated.components.find((item) => item.componentId === 2)?.faceIds).toEqual([3, 4])
  expect(screen.getByLabelText('Preview ROI X1')).toHaveProperty('value', '5.000')
})

it('rejects invalid and empty regions without changing the original ROI and supports cancelling a draft', () => {
  const original = setup(true)
  const input = screen.getByLabelText('Preview ROI X1')
  const apply = screen.getByRole('button', { name: 'Preview ROI 좌표 적용' })
  fireEvent.change(input, { target: { value: '' } })
  fireEvent.click(apply)
  expect(screen.getByRole('alert').textContent).toContain('숫자')
  fireEvent.change(input, { target: { value: '100' } })
  fireEvent.change(screen.getByLabelText('Preview ROI X2'), { target: { value: '110' } })
  fireEvent.click(apply)
  expect(screen.getByRole('alert').textContent).toContain('찾지 못했습니다')
  expect(workspaceStore.getState().roiScopes[0]).toEqual(original)
  fireEvent.click(screen.getByRole('button', { name: 'Preview ROI 좌표 수정 취소' }))
  expect(input).toHaveProperty('value', '0.000')
  expect(screen.queryByRole('alert')).toBeNull()
})

it('also edits finite XYZ bounds, normalizes reversed corners and replaces the selected faces', () => {
  setup(false)
  fireEvent.change(screen.getByLabelText('Preview ROI Z1'), { target: { value: '20' } })
  fireEvent.change(screen.getByLabelText('Preview ROI Z2'), { target: { value: '11' } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview ROI 좌표 적용' }))
  const updated = workspaceStore.getState().roiScopes[0]
  expect(updated.clipBox).toMatchObject({ plane: 'xyz', zMin: 11, zMax: 20 })
  expect(updated.clipBox?.zUnbounded).not.toBe(true)
  expect(updated.components.map((item) => item.faceIds)).toEqual([[3, 4]])
})
