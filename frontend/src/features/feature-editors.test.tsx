// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ComponentTreePanel } from '@/features/components'
import { MaterialEditorDialog } from '@/features/materials'
import { TransformEditorDialog } from '@/features/transforms'
import { ViewerWorkspace } from '@/components/layout/viewer-workspace'
import { workspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'

vi.mock('@/features/viewer', () => ({
  ThreeViewerCanvas: () => (
    <canvas aria-label="Interactive 3D CAD viewer" />
  ),
}))

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

afterEach(() => {
  cleanup()
  workspaceStore.getState().actions.resetWorkspace()
})

describe('Step 07·08 feature editors', () => {
  it('renders ScenePayload components in the Viewer state bridge', async () => {
    render(<ViewerWorkspace scene={createSceneFixture()} />)

    expect(
      await screen.findByLabelText('Interactive 3D CAD viewer'),
    ).not.toBeNull()
    const axisSize = screen.getByRole('slider', {
      name: 'Axis size',
    })
    expect(axisSize).toHaveProperty('value', '100')
    fireEvent.change(axisSize, { target: { value: '125' } })
    expect(axisSize).toHaveProperty('value', '125')
    expect(screen.getByText('125%')).not.toBeNull()

    act(() => {
      workspaceStore.getState().actions.setSelectedComponentIds([1])
      workspaceStore.getState().actions.setSelectedFaceIds([0])
    })

    expect(screen.getByText('Face 0')).not.toBeNull()
    expect(
      screen.getByText('2 visible · 1 component · 1 face'),
    ).not.toBeNull()
  })

  it('connects component selection, visibility, traceability, and rename to Zustand', () => {
    const scene = createSceneFixture()
    render(
      <ComponentTreePanel
        scene={scene}
        onEditMaterial={vi.fn()}
        onEditTransform={vi.fn()}
        onDelete={vi.fn()}
      />,
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Select STEP Solid 1' }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Hide STEP Solid 1' }),
    )
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Traceability off for STEP Solid 1',
      }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Rename STEP Solid 1' }),
    )
    const nameInput = screen.getByRole('textbox', {
      name: 'Component name',
    })
    fireEvent.change(nameInput, { target: { value: 'Cover Deco' } })
    fireEvent.keyDown(nameInput, { key: 'Enter' })

    expect(workspaceStore.getState()).toMatchObject({
      selectedComponentIds: [1],
      hiddenComponentIds: [1],
      excludedComponentIds: [1],
      componentNameOverrides: { 1: 'Cover Deco' },
    })
    expect(
      screen.getByRole('button', { name: 'Select Cover Deco' }),
    ).not.toBeNull()
  })

  it('creates a compiled part material assignment', () => {
    const component = createSceneFixture().components[0]
    render(
      <MaterialEditorDialog
        open
        onOpenChange={vi.fn()}
        component={component}
        componentName="Cover Deco"
      />,
    )

    fireEvent.change(screen.getByLabelText('Base material'), {
      target: { value: 'black_powder_coated_aluminum' },
    })
    fireEvent.change(screen.getByLabelText('Surface property'), {
      target: { value: 'corrosion_medium' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: 'Apply material' }),
    )

    expect(workspaceStore.getState().materialAssignments).toEqual([
      expect.objectContaining({
        assignmentId: 'material-part-1',
        componentId: 1,
        targetType: 'part',
        baseMaterialId: 'black_powder_coated_aluminum',
        surfaceId: 'corrosion_medium',
      }),
    ])
  })

  it('creates a component transform rule with move and tilt vectors', () => {
    const component = createSceneFixture().components[0]
    render(
      <TransformEditorDialog
        open
        onOpenChange={vi.fn()}
        component={component}
        componentName="Cover Deco"
      />,
    )

    fireEvent.change(screen.getByRole('spinbutton', { name: 'x' }), {
      target: { value: '2.5' },
    })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Rx' }), {
      target: { value: '5' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: 'Apply transform' }),
    )

    expect(workspaceStore.getState().transformRules).toEqual([
      expect.objectContaining({
        ruleId: 'transform-component-1',
        componentId: 1,
        targetType: 'component',
        move: { x: 2.5, y: 0, z: 0 },
        tilt: { x: 5, y: 0, z: 0 },
      }),
    ])
  })
})
