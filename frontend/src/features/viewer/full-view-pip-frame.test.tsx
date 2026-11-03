// @vitest-environment jsdom
import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { FullViewPipFrame } from './full-view-pip-frame'
import type { FullViewPipSize } from './full-view-pip-layout'

afterEach(cleanup)

function Frame({ viewport = { width: 1000, height: 600 }, onPointerDown = () => undefined, onSync = () => undefined }) {
  const [size, setSize] = useState<FullViewPipSize | null>(null)
  return <div onPointerDown={onPointerDown}>
    <FullViewPipFrame viewport={viewport} size={size} onSizeChange={setSize}>
      <button type="button" onClick={onSync}>Sync</button>
    </FullViewPipFrame>
  </div>
}

function handle() {
  const button = screen.getByRole('button', { name: 'Full View 크기 조절' })
  let captured: number | null = null
  button.setPointerCapture = vi.fn((id) => { captured = id })
  button.hasPointerCapture = (id) => captured === id
  button.releasePointerCapture = vi.fn(() => { captured = null })
  return button
}

function pointer(button: HTMLElement, type: string, x: number, y: number, id = 1) {
  const event = new MouseEvent(type, { button: 0, buttons: type === 'pointerup' ? 0 : 1,
    clientX: x, clientY: y, bubbles: true, cancelable: true })
  Object.defineProperty(event, 'pointerId', { value: id })
  fireEvent(button, event)
}

it('resizes from the upper-left corner without propagating a camera drag or changing sync', () => {
  const onPointerDown = vi.fn(), onSync = vi.fn()
  render(<Frame onPointerDown={onPointerDown} onSync={onSync} />)
  const button = handle()
  pointer(button, 'pointerdown', 780, 440)
  pointer(button, 'pointermove', 640, 340)
  const frame = screen.getByTestId('full-view-pip-frame')
  expect(frame.style.width).toBe('360px')
  expect(frame.style.height).toBe('260px')
  expect(frame.style.right).toBe('14px')
  expect(frame.style.bottom).toBe('14px')
  expect(button.setPointerCapture).toHaveBeenCalledWith(1)
  expect(onPointerDown).not.toHaveBeenCalled()
  expect(onSync).not.toHaveBeenCalled()
  pointer(button, 'pointerup', 640, 340)
  expect(button.releasePointerCapture).toHaveBeenCalledWith(1)
  fireEvent.click(screen.getByRole('button', { name: 'Sync' }))
  expect(onSync).toHaveBeenCalledOnce()
})

it('ignores other pointers and ends resizing on cancellation', () => {
  render(<Frame />)
  const button = handle()
  pointer(button, 'pointerdown', 500, 300)
  pointer(button, 'pointermove', 100, 100, 2)
  expect(screen.getByTestId('full-view-pip-frame').style.width).toBe('220px')
  pointer(button, 'pointercancel', 500, 300)
  pointer(button, 'pointermove', 100, 100)
  expect(screen.getByTestId('full-view-pip-frame').style.width).toBe('220px')
})

it('supports keyboard resizing, minimum bounds, and resetting with Home or double-click', () => {
  render(<Frame />)
  const button = handle(), frame = screen.getByTestId('full-view-pip-frame')
  fireEvent.keyDown(button, { key: 'ArrowLeft', shiftKey: true })
  fireEvent.keyDown(button, { key: 'ArrowUp' })
  expect(frame.style.width).toBe('260px')
  expect(frame.style.height).toBe('170px')
  fireEvent.keyDown(button, { key: 'Home' })
  expect(frame.style.width).toBe('220px')
  expect(frame.style.height).toBe('160px')
  pointer(button, 'pointerdown', 500, 300)
  pointer(button, 'pointermove', 2000, 2000)
  pointer(button, 'pointerup', 2000, 2000)
  expect(frame.style.width).toBe('120px')
  expect(frame.style.height).toBe('90px')
  fireEvent.doubleClick(button)
  expect(frame.style.width).toBe('220px')
  expect(frame.style.height).toBe('160px')
})

it('keeps a preferred size when the viewer shrinks and restores it when space returns', () => {
  const view = render(<Frame />)
  const button = handle()
  pointer(button, 'pointerdown', 780, 440)
  pointer(button, 'pointermove', 360, 200)
  pointer(button, 'pointerup', 360, 200)
  expect(screen.getByTestId('full-view-pip-frame').style.width).toBe('640px')
  view.rerender(<Frame viewport={{ width: 400, height: 250 }} />)
  expect(screen.getByTestId('full-view-pip-frame').style.width).toBe('372px')
  expect(screen.getByTestId('full-view-pip-frame').style.height).toBe('222px')
  view.rerender(<Frame />)
  expect(screen.getByTestId('full-view-pip-frame').style.width).toBe('640px')
  expect(screen.getByTestId('full-view-pip-frame').style.height).toBe('400px')
})
