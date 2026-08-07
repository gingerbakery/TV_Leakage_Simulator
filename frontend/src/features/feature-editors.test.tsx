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
import { AppProviders } from '@/app/providers'
import { MaterialEditorDialog } from '@/features/materials'
import {
  createFaceEmitter,
  RayTracingPanel,
} from '@/features/raytracing'
import { RoiSelectionPanel } from '@/features/roi'
import { TransformEditorDialog } from '@/features/transforms'
import { ViewerWorkspace } from '@/components/layout/viewer-workspace'
import { workspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'

vi.mock('@/features/viewer', () => ({
  ThreeViewerCanvas: ({
    editingComponentMode,
    onComponentContextMenu,
    onRayObjectContextMenu,
  }: {
    editingComponentMode?: 'material' | 'transform' | null
    onComponentContextMenu?(target: {
      clientX: number
      clientY: number
      componentId: number
      returnFocusElement: HTMLElement
    }): void
    onRayObjectContextMenu?(target: {
      clientX: number
      clientY: number
      id: string
      kind: 'emitter' | 'receiver'
      returnFocusElement: HTMLElement
    }): void
  }) => (
    <canvas
      aria-label="Interactive 3D CAD viewer"
      data-editing-component-mode={editingComponentMode ?? ''}
      onContextMenu={(event) => {
        if (event.shiftKey) {
          onRayObjectContextMenu?.({
            clientX: event.clientX,
            clientY: event.clientY,
            id: 'emitter_001',
            kind: 'emitter',
            returnFocusElement: event.currentTarget,
          })
          return
        }
        onComponentContextMenu?.({
          clientX: event.clientX,
          clientY: event.clientY,
          componentId: 1,
          returnFocusElement: event.currentTarget,
        })
      }}
    />
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
    expect(axisSize).toHaveProperty('value', '50')
    expect(axisSize).toHaveProperty('max', '100')
    fireEvent.change(axisSize, { target: { value: '100' } })
    expect(axisSize).toHaveProperty('value', '100')
    expect(screen.getByText('100%')).not.toBeNull()

    act(() => {
      workspaceStore.getState().actions.setSelectedComponentIds([1])
      workspaceStore.getState().actions.setSelectedFaceIds([0])
    })

    expect(screen.getByText('Face selected')).not.toBeNull()
    expect(
      screen.getByText('Component · STEP Solid 1'),
    ).not.toBeNull()
    expect(
      screen.getByText('2 visible · 1 component'),
    ).not.toBeNull()
  })

  it('shows the active component editor target in the Viewer', async () => {
    render(
      <ViewerWorkspace
        scene={createSceneFixture()}
        editingComponentId={1}
        editingComponentMode="transform"
      />,
    )

    expect(
      await screen.findByText('Transform target · STEP Solid 1'),
    ).not.toBeNull()
    expect(
      screen
        .getByLabelText('Interactive 3D CAD viewer')
        .getAttribute('data-editing-component-mode'),
    ).toBe('transform')
  })

  it('restores component actions on the Viewer context menu', async () => {
    const onEditMaterial = vi.fn()
    render(
      <ViewerWorkspace
        scene={createSceneFixture()}
        onEditMaterial={onEditMaterial}
      />,
    )
    const viewer = await screen.findByLabelText(
      'Interactive 3D CAD viewer',
    )

    fireEvent.contextMenu(viewer)
    expect(await screen.findByText('STEP Solid 1')).not.toBeNull()
    fireEvent.click(
      screen.getByRole('menuitem', { name: /Traceability Off/ }),
    )
    expect(workspaceStore.getState().excludedComponentIds).toEqual([1])

    fireEvent.contextMenu(viewer)
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /Material/ }),
    )
    expect(onEditMaterial).toHaveBeenCalledWith({
      componentId: 1,
      returnFocusElement: viewer,
    })
    expect(workspaceStore.getState().selectedComponentIds).toEqual([1])
  })

  it('opens Emitter settings from the Viewer context menu', async () => {
    const onEditRayObject = vi.fn()
    act(() => {
      workspaceStore.getState().actions.upsertEmitter({
        ...createFaceEmitter('emitter_001', [0]),
        ray_count: 2500,
      })
    })
    render(
      <ViewerWorkspace
        scene={createSceneFixture()}
        onEditRayObject={onEditRayObject}
      />,
    )
    const viewer = await screen.findByLabelText(
      'Interactive 3D CAD viewer',
    )

    fireEvent.contextMenu(viewer, { shiftKey: true })
    expect(
      await screen.findByRole('menu', {
        name: 'Emitter actions for emitter_001',
      }),
    ).not.toBeNull()
    fireEvent.click(
      screen.getByRole('menuitem', { name: 'Edit settings' }),
    )

    expect(onEditRayObject).toHaveBeenCalledWith({
      id: 'emitter_001',
      kind: 'emitter',
    })
  })

  it('adds and activates a coordinate ROI scope', () => {
    render(
      <AppProviders>
        <RoiSelectionPanel scene={createSceneFixture()} />
      </AppProviders>,
    )

    fireEvent.change(screen.getByLabelText('ROI 이름'), {
      target: { value: 'rear-point' },
    })
    fireEvent.change(screen.getByLabelText('ROI X coordinate'), {
      target: { value: '38' },
    })
    fireEvent.change(screen.getByLabelText('ROI Y coordinate'), {
      target: { value: '22' },
    })
    fireEvent.change(screen.getByLabelText('ROI Z coordinate'), {
      target: { value: '13' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: '좌표로 ROI 추가' }),
    )

    expect(screen.getByText('rear-point')).not.toBeNull()
    expect(workspaceStore.getState().roiScopes).toEqual([
      expect.objectContaining({
        scopeId: 'rear-point',
        source: 'point',
        active: true,
        components: [
          expect.objectContaining({
            componentId: 2,
            faceIds: [3],
          }),
        ],
      }),
    ])

    fireEvent.click(screen.getByLabelText('rear-point 활성화'))
    expect(workspaceStore.getState().roiScopes[0].active).toBe(false)
  })

  it('shows ROI explanations from title help tooltips', async () => {
    render(
      <AppProviders>
        <RoiSelectionPanel scene={createSceneFixture()} />
      </AppProviders>,
    )

    const boxHelp = screen.getByRole('button', {
      name: '박스 드래그 도움말',
    })
    expect(
      screen.getByRole('button', { name: 'ROI List 도움말' }),
    ).not.toBeNull()
    expect(
      screen.getByRole('button', { name: '활성 ROI 도움말' }),
    ).not.toBeNull()
    expect(
      screen.queryByText(/보이는 컴포넌트만 대상으로/),
    ).toBeNull()

    fireEvent.focus(boxHelp)

    expect((await screen.findByRole('tooltip')).textContent).toContain(
      '보이는 컴포넌트만 대상으로',
    )
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

    fireEvent.click(
      screen.getByRole('button', { name: 'Material for Cover Deco' }),
    )
    expect(workspaceStore.getState().selectedComponentIds).toEqual([1])
    expect(workspaceStore.getState().selectedFaceIds).toEqual([])
    expect(workspaceStore.getState().hiddenComponentIds).toEqual([])
  })

  it('shows only active ROI component subsets in the component tree', () => {
    const scene = createSceneFixture()
    act(() => {
      workspaceStore.getState().actions.addRoiScope({
        label: 'cover-only',
        source: 'box',
        view: 'front_xy',
        clipBox: { xMin: 0, xMax: 30, yMin: 0, yMax: 30 },
        components: [
          {
            componentId: 1,
            componentName: 'STEP Solid 1',
            faceIds: [0],
            areaMm2: 1800,
            bboxMin: { x: 0, y: 0, z: 0 },
            bboxMax: { x: 30, y: 30, z: 10 },
          },
        ],
      })
    })

    render(
      <ComponentTreePanel
        scene={scene}
        onEditMaterial={vi.fn()}
        onEditTransform={vi.fn()}
        onDelete={vi.fn()}
      />,
    )

    expect(screen.getByText('1 components')).not.toBeNull()
    expect(screen.getByText('1,800 mm²')).not.toBeNull()
    expect(
      screen.queryByRole('button', { name: 'Select STEP Solid 2' }),
    ).toBeNull()
  })

  it('shows the active ROI subset in Transform and Material editors', () => {
    const component = createSceneFixture().components[0]
    act(() => {
      workspaceStore.getState().actions.addRoiScope({
        label: 'cover-only',
        source: 'box',
        view: 'front_xy',
        clipBox: { xMin: 0, xMax: 30, yMin: 0, yMax: 30 },
        components: [
          {
            componentId: 1,
            componentName: 'STEP Solid 1',
            faceIds: [0],
            areaMm2: 1800,
            bboxMin: { x: 0, y: 0, z: 0 },
            bboxMax: { x: 30, y: 30, z: 10 },
          },
        ],
      })
    })

    const transformView = render(
      <AppProviders>
        <TransformEditorDialog
          open
          onOpenChange={vi.fn()}
          component={component}
          componentName="Cover Deco"
        />
      </AppProviders>,
    )
    expect(
      screen.getByRole('dialog', { name: 'Transform editor' })
        .textContent,
    ).toContain('ROI')
    transformView.unmount()

    render(
      <AppProviders>
        <MaterialEditorDialog
          open
          onOpenChange={vi.fn()}
          component={component}
          componentName="Cover Deco"
        />
      </AppProviders>,
    )
    expect(
      screen.getByRole('dialog', { name: 'Material assignment' })
        .textContent,
    ).toContain('ROI')
  })

  it('creates a compiled part material assignment', () => {
    const component = createSceneFixture().components[0]
    const onOpenChange = vi.fn()
    render(
      <AppProviders>
        <MaterialEditorDialog
          open
          onOpenChange={onOpenChange}
          component={component}
          componentName="Cover Deco"
        />
      </AppProviders>,
    )
    expect(
      screen
        .getByRole('dialog', { name: 'Material assignment' })
        .hasAttribute('data-floating-panel'),
    ).toBe(true)

    fireEvent.change(screen.getByLabelText('Base material'), {
      target: { value: 'powder_coated_secc_black' },
    })
    const surfacePropertyField = screen.getByLabelText('Surface property')
    fireEvent.change(surfacePropertyField, {
      target: { value: 'metal_gloss' },
    })
    fireEvent.keyDown(surfacePropertyField, { key: 'Enter' })

    expect(workspaceStore.getState().materialAssignments).toEqual([
      expect.objectContaining({
        assignmentId: 'material-part-1',
        componentId: 1,
        targetType: 'part',
        baseMaterialId: 'powder_coated_secc_black',
        surfaceId: 'metal_gloss',
      }),
    ])
    // Applying the part material keeps the dialog open, since the user may
    // still want to assign face-specific materials in the same session.
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it('creates a component transform rule with move and tilt vectors', () => {
    const component = createSceneFixture().components[0]
    const onOpenChange = vi.fn()
    render(
      <AppProviders>
        <TransformEditorDialog
          open
          onOpenChange={onOpenChange}
          component={component}
          componentName="Cover Deco"
        />
      </AppProviders>,
    )
    expect(
      screen
        .getByRole('dialog', { name: 'Transform editor' })
        .hasAttribute('data-floating-panel'),
    ).toBe(true)

    fireEvent.change(screen.getByRole('spinbutton', { name: 'x' }), {
      target: { value: '2.5' },
    })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Rx' }), {
      target: { value: '5' },
    })
    fireEvent.keyDown(
      screen.getByRole('spinbutton', { name: 'Rx' }),
      { key: 'Enter' },
    )

    expect(workspaceStore.getState().transformRules).toEqual([
      expect.objectContaining({
        ruleId: 'transform-component-1',
        componentId: 1,
        targetType: 'component',
        move: { x: 2.5, y: 0, z: 0 },
        tilt: { x: 5, y: 0, z: 0 },
        pivot: null,
      }),
    ])
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('creates a component transform rule with a custom tilt pivot', () => {
    const component = createSceneFixture().components[0]
    const onOpenChange = vi.fn()
    render(
      <AppProviders>
        <TransformEditorDialog
          open
          onOpenChange={onOpenChange}
          component={component}
          componentName="Cover Deco"
        />
      </AppProviders>,
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Custom point' }),
    )
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Pivot x' }), {
      target: { value: '10' },
    })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Pivot y' }), {
      target: { value: '20' },
    })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Pivot z' }), {
      target: { value: '0' },
    })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Rz' }), {
      target: { value: '90' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: 'Apply transform' }),
    )

    expect(workspaceStore.getState().transformRules).toEqual([
      expect.objectContaining({
        ruleId: 'transform-component-1',
        componentId: 1,
        tilt: { x: 0, y: 0, z: 90 },
        pivot: { x: 10, y: 20, z: 0 },
      }),
    ])
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('adopts a viewer-picked point as the tilt pivot', () => {
    const component = createSceneFixture().components[0]
    render(
      <AppProviders>
        <TransformEditorDialog
          open
          onOpenChange={() => {}}
          component={component}
          componentName="Cover Deco"
        />
      </AppProviders>,
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Custom point' }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: '뷰어에서 좌표 선택' }),
    )
    expect(workspaceStore.getState().pivotPickArmed).toBe(true)

    // Simulate what ThreeViewerCanvas does on a real surface click: write
    // the picked point to the store and disarm - the dialog has no direct
    // reference to the viewer, so this is the only channel between them.
    act(() => {
      workspaceStore
        .getState()
        .actions.setPivotPickPoint({ x: 12, y: -4, z: 6 })
      workspaceStore.getState().actions.setPivotPickArmed(false)
    })

    expect(
      (screen.getByRole('spinbutton', { name: 'Pivot x' }) as HTMLInputElement)
        .value,
    ).toBe('12.0')
    expect(
      (screen.getByRole('spinbutton', { name: 'Pivot y' }) as HTMLInputElement)
        .value,
    ).toBe('-4.0')
    expect(
      (screen.getByRole('spinbutton', { name: 'Pivot z' }) as HTMLInputElement)
        .value,
    ).toBe('6.0')
    // The point must be consumed exactly once, not left sitting in the
    // store where a later-opened dialog could pick it up unexpectedly.
    expect(workspaceStore.getState().pivotPickPoint).toBeNull()

    fireEvent.click(
      screen.getByRole('button', { name: 'Apply transform' }),
    )
    expect(workspaceStore.getState().transformRules).toEqual([
      expect.objectContaining({
        ruleId: 'transform-component-1',
        pivot: { x: 12, y: -4, z: 6 },
      }),
    ])
  })

  it('disarms pivot picking when the dialog is closed mid-pick', () => {
    const component = createSceneFixture().components[0]
    const { rerender } = render(
      <AppProviders>
        <TransformEditorDialog
          open
          onOpenChange={() => {}}
          component={component}
          componentName="Cover Deco"
        />
      </AppProviders>,
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Custom point' }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: '뷰어에서 좌표 선택' }),
    )
    expect(workspaceStore.getState().pivotPickArmed).toBe(true)

    rerender(
      <AppProviders>
        <TransformEditorDialog
          open={false}
          onOpenChange={() => {}}
          component={component}
          componentName="Cover Deco"
        />
      </AppProviders>,
    )

    expect(workspaceStore.getState().pivotPickArmed).toBe(false)
  })

  it('creates Emitter and Current View Receiver contracts for Step 10', () => {
    render(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={{
            target: [10, 20, 30],
            normal: [0, 0, -1],
            uAxis: [1, 0, 0],
            vAxis: [0, -1, 0],
          }}
        />
      </AppProviders>,
    )

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Add datum plane emitter',
      }),
    )
    expect(workspaceStore.getState().placementPreviewEmitter).toEqual(
      expect.objectContaining({
        emitter_type: 'datum_plane',
        center: [30, 30, 10],
      }),
    )
    expect(
      screen
        .getByRole('dialog', { name: 'Datum plane emitter' })
        .hasAttribute('data-floating-panel'),
    ).toBe(true)
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Emitter rays' }),
      { target: { value: '2500' } },
    )
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Emitter center X' }),
      { target: { value: '12.5' } },
    )
    expect(workspaceStore.getState().placementPreviewEmitter?.center).toEqual(
      [12.5, 30, 10],
    )
    fireEvent.keyDown(
      screen.getByRole('spinbutton', { name: 'Emitter center X' }),
      { key: 'Enter' },
    )
    expect(workspaceStore.getState().placementPreviewEmitter).toBeNull()
    expect(
      screen.queryByRole('dialog', { name: 'Datum plane emitter' }),
    ).toBeNull()

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Add current view receiver',
      }),
    )
    expect(workspaceStore.getState().placementPreviewReceiver).toEqual(
      expect.objectContaining({
        placement_mode: 'current_view',
        center: [10, 20, 60],
        width_mm: 30,
        height_mm: 30,
      }),
    )
    expect(
      screen
        .getByRole('dialog', { name: 'Current view receiver' })
        .hasAttribute('data-floating-panel'),
    ).toBe(true)
    expect(
      screen.getByRole('spinbutton', { name: 'View distance (mm)' }),
    ).toHaveProperty('value', '30')
    expect(
      screen.getByRole('spinbutton', { name: 'Receiver width (mm)' }),
    ).toHaveProperty('value', '30')
    expect(
      screen.getByRole('spinbutton', { name: 'Receiver height (mm)' }),
    ).toHaveProperty('value', '30')
    fireEvent.change(screen.getByRole('textbox', { name: 'Receiver name' }), {
      target: { value: 'Camera RX' },
    })
    fireEvent.keyDown(
      screen.getByRole('textbox', { name: 'Receiver name' }),
      { key: 'Enter' },
    )
    expect(workspaceStore.getState().placementPreviewReceiver).toBeNull()
    expect(
      screen.queryByRole('dialog', { name: 'Current view receiver' }),
    ).toBeNull()

    expect(workspaceStore.getState().emitters).toEqual([
      expect.objectContaining({
        emitter_id: 'emitter_001',
        emitter_type: 'datum_plane',
        ray_count: 2500,
      }),
    ])
    expect(workspaceStore.getState().receivers).toEqual([
      expect.objectContaining({
        receiver_id: 'receiver_001',
        display_name: 'Camera RX',
        placement_mode: 'current_view',
        center: [10, 20, 60],
        view_distance_mm: 30,
        width_mm: 30,
        height_mm: 30,
      }),
    ])

    fireEvent.click(
      screen.getByRole('button', { name: 'Edit emitter_001' }),
    )
    expect(
      screen.getByRole('dialog', { name: 'Edit emitter_001' }),
    ).not.toBeNull()
    expect(
      screen.getByRole('spinbutton', { name: 'Emitter rays' }),
    ).toHaveProperty('value', '2500')
    expect(
      screen.getByRole('spinbutton', { name: 'Emitter center X' }),
    ).toHaveProperty('value', '12.5')
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Emitter rays' }),
      { target: { value: '3500' } },
    )
    fireEvent.change(
      screen.getByRole('spinbutton', {
        name: 'Emitter width (mm)',
      }),
      { target: { value: '24' } },
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Save emitter' }),
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Edit receiver_001' }),
    )
    expect(
      screen.getByRole('dialog', { name: 'Edit Camera RX' }),
    ).not.toBeNull()
    expect(
      screen.getByRole('spinbutton', {
        name: 'Receiver width (mm)',
      }),
    ).toHaveProperty('value', '30')
    expect(
      screen.getByRole('spinbutton', {
        name: 'Receiver center X',
      }),
    ).toHaveProperty('value', '10.0')
    expect(
      screen.getByRole('spinbutton', {
        name: 'Receiver tilt Z',
      }),
    ).toHaveProperty('value', '0.0')
    fireEvent.change(
      screen.getByRole('spinbutton', {
        name: 'Receiver width (mm)',
      }),
      { target: { value: '42' } },
    )
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'View distance (mm)' }),
      { target: { value: '45' } },
    )
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Receiver center X' }),
      { target: { value: '14' } },
    )
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Receiver tilt Z' }),
      { target: { value: '15' } },
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Save receiver' }),
    )

    expect(workspaceStore.getState().emitters).toEqual([
      expect.objectContaining({
        emitter_id: 'emitter_001',
        ray_count: 3500,
        width_mm: 24,
      }),
    ])
    expect(workspaceStore.getState().receivers).toEqual([
      expect.objectContaining({
        receiver_id: 'receiver_001',
        center: [14, 20, 75],
        view_distance_mm: 45,
        width_mm: 42,
        position_offset_mm: [4, 0, 0],
        tilt_xyz_deg: [0, 0, 15],
      }),
    ])
    expect(
      screen.getByRole('button', { name: 'Run ray tracing' }),
    ).toHaveProperty('disabled', false)
  })

  it('keeps CAD surface selection interactive while configuring an emitter', () => {
    render(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
        />
      </AppProviders>,
    )

    act(() => {
      workspaceStore.getState().actions.setSelectedFaceIds([4])
    })
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Add CAD surface emitter',
      }),
    )

    const dialog = screen.getByRole('dialog', {
      name: 'CAD surface emitter',
    })
    expect(dialog.getAttribute('aria-modal')).toBeNull()
    expect(
      document.querySelector('[data-slot="dialog-overlay"]'),
    ).toBeNull()
    expect(workspaceStore.getState().selectedFaceIds).toEqual([])
    expect(
      workspaceStore.getState().emitterFaceSelectionArmed,
    ).toBe(true)

    act(() => {
      workspaceStore.getState().actions.setSelectedFaceIds([0, 1])
    })
    expect(screen.getByText('Selected faces · 선택됨')).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Add emitter' }))
    expect(workspaceStore.getState().emitters).toEqual([
      expect.objectContaining({
        emitter_id: 'emitter_001',
        emitter_type: 'face',
        face_indices: [0, 1],
      }),
    ])
    expect(
      workspaceStore.getState().emitterFaceSelectionArmed,
    ).toBe(false)
    expect(workspaceStore.getState().selectedFaceIds).toEqual([])
  })

  it('updates every emitter ray count from Run options', () => {
    act(() => {
      workspaceStore.getState().actions.upsertEmitter({
        ...createFaceEmitter('emitter_001', [0]),
        ray_count: 2500,
      })
      workspaceStore.getState().actions.upsertEmitter({
        ...createFaceEmitter('emitter_002', [1]),
        ray_count: 5000,
      })
    })

    render(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
        />
      </AppProviders>,
    )

    const runOptionRayCount = screen.getByRole('spinbutton', {
      name: 'Run option emitter rays',
    })
    expect(runOptionRayCount).toHaveProperty('value', '2500')

    fireEvent.change(runOptionRayCount, {
      target: { value: '6000' },
    })

    expect(
      workspaceStore
        .getState()
        .emitters.map((emitter) => emitter.ray_count),
    ).toEqual([6000, 6000])
  })
})
