// @vitest-environment jsdom

import { useRef, useState } from 'react'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ComponentContextMenu,
  ConfirmationDialog,
  ViewerComponentActionMenu,
} from '@/components/common'
import { AppDialog } from '@/components/common/app-dialog'

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
  vi.restoreAllMocks()
})

describe('common overlays', () => {
  it('opens a confirmation dialog and runs the confirmed action', async () => {
    const onConfirm = vi.fn()

    function DialogHarness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Open dialog
          </button>
          <ConfirmationDialog
            open={open}
            onOpenChange={setOpen}
            title="Delete component?"
            description="This cannot be undone."
            confirmLabel="Delete"
            onConfirm={onConfirm}
          />
        </>
      )
    }

    render(<DialogHarness />)
    fireEvent.click(screen.getByRole('button', { name: 'Open dialog' }))

    expect(
      await screen.findByRole('dialog', { name: 'Delete component?' }),
    ).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))

    expect(onConfirm).toHaveBeenCalledOnce()
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
    })
  })

  it('exposes the migrated component actions from a context menu', async () => {
    const onAction = vi.fn()

    render(
      <ComponentContextMenu
        componentName="Display Panel A"
        visible
        traceable
        onAction={onAction}
      >
        <button type="button">CAD component</button>
      </ComponentContextMenu>,
    )

    fireEvent.contextMenu(
      screen.getByRole('button', { name: 'CAD component' }),
    )

    expect(await screen.findByRole('menu')).not.toBeNull()
    expect(screen.getByText('Visible · Traceability on')).not.toBeNull()

    fireEvent.click(screen.getByRole('menuitem', { name: 'Material' }))

    expect(onAction).toHaveBeenCalledWith('material')
  })

  it('forwards wheel zoom while the Viewer context menu stays open', () => {
    const wheelTarget = document.createElement('canvas')
    const onWheel = vi.fn()
    wheelTarget.addEventListener('wheel', onWheel)
    document.body.append(wheelTarget)

    render(
      <ViewerComponentActionMenu
        open
        componentName="Display Panel A"
        position={{ x: 200, y: 160 }}
        visible
        traceable
        colorOverride={null}
        fallbackColor="#64748b"
        wheelTarget={wheelTarget}
        onOpenChange={vi.fn()}
        onColorChange={vi.fn()}
        onAction={vi.fn()}
      />,
    )

    fireEvent.wheel(screen.getByRole('menu'), { deltaY: -120 })

    expect(onWheel).toHaveBeenCalledOnce()
    expect(onWheel.mock.calls[0][0]).toMatchObject({ deltaY: -120 })
    expect(screen.getByRole('menu')).not.toBeNull()
    wheelTarget.remove()
  })

  it('returns focus to a programmatic dialog opener', async () => {
    function FocusHarness() {
      const [open, setOpen] = useState(false)
      const triggerRef = useRef<HTMLButtonElement>(null)

      return (
        <>
          <button
            ref={triggerRef}
            type="button"
            onClick={() => setOpen(true)}
          >
            Open details
          </button>
          <AppDialog
            open={open}
            onOpenChange={setOpen}
            title="Details"
            returnFocusRef={triggerRef}
            footer={
              <button type="button" onClick={() => setOpen(false)}>
                Done
              </button>
            }
          />
        </>
      )
    }

    render(<FocusHarness />)
    const trigger = screen.getByRole('button', { name: 'Open details' })

    fireEvent.click(trigger)
    fireEvent.click(await screen.findByRole('button', { name: 'Done' }))

    await waitFor(() => {
      expect(document.activeElement).toBe(trigger)
    })
  })

  it('submits a settings dialog on plain Enter from a field', () => {
    const onSubmit = vi.fn()

    render(
      <AppDialog
        open
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
        title="Keyboard settings"
        footer={<button type="button">Apply</button>}
      >
        <input aria-label="Setting value" />
        <textarea aria-label="Setting notes" />
      </AppDialog>,
    )

    fireEvent.keyDown(screen.getByLabelText('Setting value'), {
      key: 'Enter',
    })
    expect(onSubmit).toHaveBeenCalledOnce()

    fireEvent.keyDown(screen.getByLabelText('Setting notes'), {
      key: 'Enter',
    })
    fireEvent.keyDown(screen.getByRole('button', { name: 'Apply' }), {
      key: 'Enter',
    })
    expect(onSubmit).toHaveBeenCalledOnce()
  })

  it('keeps a floating settings dialog non-modal and draggable', async () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
      function (this: HTMLElement) {
        const isViewer = this.hasAttribute('data-viewer-workspace')
        const left = isViewer
          ? 352
          : Number.parseFloat(this.style.left || '0')
        const top = isViewer
          ? 52
          : Number.parseFloat(this.style.top || '0')
        const width = isViewer ? 1568 : 340
        const height = isViewer ? 1028 : 600
        return {
          x: left,
          y: top,
          left,
          top,
          width,
          height,
          right: left + width,
          bottom: top + height,
          toJSON: () => undefined,
        }
      },
    )

    render(
      <>
        <main data-viewer-workspace />
        <AppDialog
          open
          floating
          onOpenChange={vi.fn()}
          title="Floating settings"
        >
          <div>Editor content</div>
        </AppDialog>
      </>,
    )

    const dialog = screen.getByRole('dialog', {
      name: 'Floating settings',
    })
    const header = dialog.querySelector('[data-slot="dialog-header"]')

    expect(dialog.getAttribute('aria-modal')).toBeNull()
    expect(dialog.hasAttribute('data-floating-panel')).toBe(true)
    expect(
      document.querySelector('[data-slot="dialog-overlay"]'),
    ).toBeNull()
    expect(header).not.toBeNull()
    expect(dialog.style.left).toBe('592px')
    expect(dialog.style.top).toBe('64px')

    fireEvent.pointerDown(header!, { clientX: 380, clientY: 70 })
    fireEvent.pointerMove(window, { clientX: 480, clientY: 150 })
    fireEvent.pointerUp(window)

    await waitFor(() => {
      expect(dialog.style.left).toBe('676px')
      expect(dialog.style.top).toBe('144px')
    })
  })
})
