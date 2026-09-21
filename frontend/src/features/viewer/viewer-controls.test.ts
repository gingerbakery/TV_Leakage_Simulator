// @vitest-environment jsdom

import { MathUtils, PerspectiveCamera, Vector3 } from 'three'
import { afterEach, describe, expect, it } from 'vitest'

import {
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
