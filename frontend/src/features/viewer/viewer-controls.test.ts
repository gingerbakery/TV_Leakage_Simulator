// @vitest-environment jsdom

import { MathUtils, PerspectiveCamera, Vector3 } from 'three'
import { afterEach, describe, expect, it } from 'vitest'

import {
  attachViewerNavigationShortcuts,
  DEFAULT_VIEWER_PAN_SPEED,
  projectionAwarePanSpeed,
  ViewerTrackballControls,
} from './viewer-controls'
import {
  AXIS_CAMERA_FOV_DEGREES,
  DEFAULT_CAMERA_FOV_DEGREES,
} from './viewer-display'

afterEach(() => document.body.replaceChildren())

function createControls(fov: number, zoom = 1) {
  const canvas = document.createElement('canvas')
  canvas.getBoundingClientRect = () => ({
    left: 0, top: 0, right: 1200, bottom: 800,
    width: 1200, height: 800, x: 0, y: 0, toJSON: () => ({}),
  })
  canvas.setPointerCapture = () => undefined
  canvas.releasePointerCapture = () => undefined
  document.body.append(canvas)
  const camera = new PerspectiveCamera(fov, 1.5, 0.1, 100000)
  camera.zoom = zoom
  camera.position.set(0, 0, 1000)
  camera.updateProjectionMatrix()
  const controls = new ViewerTrackballControls(camera, canvas)
  controls.staticMoving = true
  controls.update()
  return { canvas, camera, controls }
}

function rightDrag(canvas: HTMLCanvasElement, dx: number, dy: number) {
  for (const [type, x, y] of [
    ['pointerdown', 600, 400],
    ['pointermove', 600 + dx, 400 + dy],
    ['pointerup', 600 + dx, 400 + dy],
  ] as const) {
    const event = new MouseEvent(type, {
      button: 2,
      buttons: type === 'pointerup' ? 0 : 2,
      clientX: x,
      clientY: y,
      bubbles: true,
    })
    Object.defineProperties(event, {
      pointerId: { value: 1 },
      pointerType: { value: 'mouse' },
      pageX: { value: x },
      pageY: { value: y },
    })
    canvas.dispatchEvent(event)
  }
}

function wheel(canvas: HTMLCanvasElement, deltaY: number) {
  canvas.dispatchEvent(new WheelEvent('wheel', {
    deltaY,
    bubbles: true,
    cancelable: true,
  }))
}

describe('projection-aware viewer panning', () => {
  it('zooms in when the wheel is rolled from front to back', () => {
    const { canvas, camera, controls } = createControls(DEFAULT_CAMERA_FOV_DEGREES)
    controls.zoomSpeed = -1.2
    const before = camera.position.distanceTo(controls.target)
    wheel(canvas, 120)
    controls.update()
    expect(camera.position.distanceTo(controls.target)).toBeLessThan(before)
    controls.dispose()
  })

  it('zooms out when the wheel is rolled from back to front', () => {
    const { canvas, camera, controls } = createControls(DEFAULT_CAMERA_FOV_DEGREES)
    controls.zoomSpeed = -1.2
    const before = camera.position.distanceTo(controls.target)
    wheel(canvas, -120)
    controls.update()
    expect(camera.position.distanceTo(controls.target)).toBeGreaterThan(before)
    controls.dispose()
  })

  it('reduces the default ISO sensitivity by 20 percent', () => {
    const camera = new PerspectiveCamera(DEFAULT_CAMERA_FOV_DEGREES)
    expect(projectionAwarePanSpeed(camera)).toBeCloseTo(0.2 * 0.8)
  })

  it.each([
    [DEFAULT_CAMERA_FOV_DEGREES, 1],
    [AXIS_CAMERA_FOV_DEGREES, 1],
    [AXIS_CAMERA_FOV_DEGREES, 4],
    [DEFAULT_CAMERA_FOV_DEGREES, 0.5],
  ])('keeps the same projected drag distance at fov=%s and zoom=%s', (fov, zoom) => {
    const { canvas, camera, controls } = createControls(fov, zoom)
    const before = new Vector3().project(camera)
    const distanceBefore = camera.position.distanceTo(controls.target)
    rightDrag(canvas, 20, 20)
    controls.update()
    camera.updateMatrixWorld(true)
    const after = new Vector3().project(camera)
    const expectedMovement = 20 * DEFAULT_VIEWER_PAN_SPEED /
      (2 * Math.tan(MathUtils.degToRad(DEFAULT_CAMERA_FOV_DEGREES) / 2))
    expect((after.x - before.x) * 600).toBeCloseTo(expectedMovement / camera.aspect, 6)
    expect(-(after.y - before.y) * 400).toBeCloseTo(expectedMovement, 6)
    expect(camera.position.distanceTo(controls.target)).toBeCloseTo(distanceBefore)
    const stoppedPosition = camera.position.clone()
    controls.update()
    expect(camera.position.distanceTo(stoppedPosition)).toBeLessThan(1e-10)
    controls.dispose()
  })

  it('updates sensitivity immediately when switching an existing ROI camera between views', () => {
    const { canvas, camera, controls } = createControls(DEFAULT_CAMERA_FOV_DEGREES)
    camera.fov = AXIS_CAMERA_FOV_DEGREES
    camera.zoom = 2
    camera.updateProjectionMatrix()
    rightDrag(canvas, 0, 20)
    controls.update()
    expect(controls.panSpeed).toBeCloseTo(projectionAwarePanSpeed(camera))
    expect(controls.panSpeed).toBeLessThan(0.002)
    camera.fov = DEFAULT_CAMERA_FOV_DEGREES
    camera.zoom = 1
    camera.updateProjectionMatrix()
    controls.update()
    expect(controls.panSpeed).toBeCloseTo(DEFAULT_VIEWER_PAN_SPEED)
    controls.dispose()
  })

  it('does not move the camera on a stationary right click', () => {
    const { canvas, camera, controls } = createControls(AXIS_CAMERA_FOV_DEGREES)
    const before = camera.position.clone()
    rightDrag(canvas, 0, 0)
    controls.update()
    expect(camera.position.equals(before)).toBe(true)
    expect(controls.target.equals(new Vector3())).toBe(true)
    controls.dispose()
  })
})


describe('held function-key navigation', () => {
  function setup() {
    const result = createControls(DEFAULT_CAMERA_FOV_DEGREES)
    const detach = attachViewerNavigationShortcuts(result.canvas, result.controls)
    const move = (x: number, y: number) => result.canvas.dispatchEvent(new MouseEvent('pointermove', {
      clientX: x, clientY: y, buttons: 0, bubbles: true,
    }))
    result.canvas.dispatchEvent(new MouseEvent('pointerenter', { clientX: 600, clientY: 400 }))
    const key = (code: string, type = 'keydown') => window.dispatchEvent(new KeyboardEvent(type, { code, key: code, cancelable: true }))
    return { ...result, move, key, dispose: () => { detach(); result.controls.dispose() } }
  }
  it.each(['F1', 'F2', 'F3'])('%s changes the camera without a mouse button and stops on key release', (code) => {
    const { camera, controls, move, key, dispose } = setup()
    const before = camera.position.clone()
    const target = controls.target.clone()
    key(code)
    move(650, 450)
    expect(camera.position.distanceTo(before)).toBeGreaterThan(0)
    if (code === 'F1') expect(controls.target.distanceTo(target)).toBeGreaterThan(0)
    else expect(controls.target).toEqual(target)
    key(code, 'keyup')
    const released = camera.position.clone()
    move(700, 500)
    expect(camera.position).toEqual(released)
    dispose()
  })
  it('F4 snaps to a principal direction without changing target, distance, or zoom', () => {
    const { camera, controls, key, dispose } = setup()
    camera.position.set(10, 20, 100)
    controls.target.set(2, 3, 4)
    camera.up.set(0.2, 1, 0.1).normalize()
    const distance = camera.position.distanceTo(controls.target)
    const zoom = camera.zoom
    key('F4')
    expect(camera.position.clone().sub(controls.target).normalize().toArray()).toEqual([0, 0, 1])
    expect(camera.up.toArray()).toEqual([0, 1, 0])
    expect(camera.position.distanceTo(controls.target)).toBeCloseTo(distance)
    expect(camera.zoom).toBe(zoom)
    expect(controls.target.toArray()).toEqual([2, 3, 4])
    dispose()
  })
  it('ignores shortcuts during text entry and cancels held navigation on blur', () => {
    const { camera, move, key, dispose } = setup()
    const input = document.createElement('input')
    document.body.append(input)
    input.focus()
    const before = camera.position.clone()
    key('F1'); move(650, 450)
    expect(camera.position).toEqual(before)
    input.blur()
    key('F1'); move(700, 500)
    expect(camera.position.distanceTo(before)).toBeGreaterThan(0)
    window.dispatchEvent(new Event('blur'))
    const stopped = camera.position.clone()
    move(750, 550)
    expect(camera.position).toEqual(stopped)
    dispose()
  })
})
