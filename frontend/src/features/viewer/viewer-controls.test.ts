// @vitest-environment jsdom

import { MathUtils, PerspectiveCamera, Vector3 } from 'three'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  attachViewerNavigationShortcuts,
  attachBlockerBoxDrawing,
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
  pointerDrag(canvas,2,dx,dy)
}
function pointerDrag(canvas:HTMLCanvasElement,button:number,dx:number,dy:number,ctrlKey=false) {
  for (const [type, x, y] of [
    ['pointerdown', 600, 400],
    ['pointermove', 600 + dx, 400 + dy],
    ['pointerup', 600 + dx, 400 + dy],
  ] as const) {
    const event = new MouseEvent(type, {
      button,
      buttons: type === 'pointerup' ? 0 : button === 2 ? 2 : 1,
      ctrlKey,
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

describe('Block drawing with camera navigation', () => {
  it('captures a plain left drag after a reference face is selected and restores orbit when it is cleared', () => {
    const { canvas, camera, controls } = createControls(DEFAULT_CAMERA_FOV_DEGREES)
    let selected = true
    const draw = vi.fn(() => { controls.enabled = false })
    const detach = attachBlockerBoxDrawing(canvas, () => true, draw, () => selected)
    const before = camera.position.clone()
    pointerDrag(canvas, 0, 40, 20); controls.update()
    expect(draw).toHaveBeenCalledOnce()
    expect(camera.position.distanceTo(before)).toBe(0)
    selected = false; controls.enabled = true
    pointerDrag(canvas, 0, 40, 20); controls.update()
    expect(draw).toHaveBeenCalledOnce()
    expect(camera.position.distanceTo(before)).toBeGreaterThan(0)
    detach(); controls.dispose()
  })

  it('lets ordinary mouse drags rotate, right drags pan and the wheel zoom while only Ctrl drag draws a block', () => {
    const {canvas,camera,controls} = createControls(DEFAULT_CAMERA_FOV_DEGREES)
    const draw = vi.fn(() => { controls.enabled = false })
    const detach = attachBlockerBoxDrawing(canvas,() => true,draw)
    let before = camera.position.clone()
    pointerDrag(canvas,0,40,20); controls.update()
    expect(camera.position.distanceTo(before)).toBeGreaterThan(0)
    before = controls.target.clone()
    rightDrag(canvas,20,20); controls.update()
    expect(controls.target.distanceTo(before)).toBeGreaterThan(0)
    const distance = camera.position.distanceTo(controls.target)
    wheel(canvas,120); controls.update()
    expect(camera.position.distanceTo(controls.target)).not.toBe(distance)
    before = camera.position.clone()
    pointerDrag(canvas,0,50,50,true); controls.update()
    expect(draw).toHaveBeenCalledOnce()
    expect(camera.position).toEqual(before)
    controls.enabled = true
    detach(); controls.dispose()
  })
  it.each(['F1','F2','F3','F4'])('keeps %s working while Block drawing is armed', (code) => {
    const {canvas,camera,controls} = createControls(DEFAULT_CAMERA_FOV_DEGREES)
    const draw = vi.fn(), detachDraw = attachBlockerBoxDrawing(canvas,() => true,draw)
    const detachNavigation = attachViewerNavigationShortcuts(canvas,controls)
    canvas.dispatchEvent(new MouseEvent('pointerenter',{clientX:600,clientY:400}))
    camera.position.set(10,20,100)
    const before = camera.position.clone()
    window.dispatchEvent(new KeyboardEvent('keydown',{code,key:code,cancelable:true}))
    canvas.dispatchEvent(new MouseEvent('pointermove',{clientX:650,clientY:450,buttons:0}))
    expect(camera.position.distanceTo(before)).toBeGreaterThan(0)
    expect(draw).not.toHaveBeenCalled()
    window.dispatchEvent(new KeyboardEvent('keyup',{code,key:code}))
    detachDraw(); detachNavigation(); controls.dispose()
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
  it('allows function keys with an input focused and cancels held navigation on blur', () => {
    const { camera, move, key, dispose } = setup()
    const input = document.createElement('input')
    document.body.append(input)
    input.focus()
    const before = camera.position.clone()
    key('F1'); move(650, 450)
    expect(camera.position.distanceTo(before)).toBeGreaterThan(0)
    input.blur()
    key('F1'); move(700, 500)
    expect(camera.position.distanceTo(before)).toBeGreaterThan(0)
    window.dispatchEvent(new Event('blur'))
    const stopped = camera.position.clone()
    move(750, 550)
    expect(camera.position).toEqual(stopped)
    dispose()
  })
  it.each(['F1','F2','F3','F4'])('%s still works when a workflow button retains keyboard focus', (code) => {
    const { camera,move,key,dispose } = setup()
    const panel = document.createElement('div')
    panel.setAttribute('role','dialog') // A nonmodal, floating editor.
    const button = document.createElement('button')
    panel.append(button); document.body.append(panel); button.focus()
    camera.position.set(10,20,100)
    const before = camera.position.clone()
    key(code); move(650,450)
    expect(camera.position.distanceTo(before)).toBeGreaterThan(0)
    key(code,'keyup'); dispose()
  })
  it('does not navigate behind a modal dialog', () => {
    const { camera,move,key,dispose } = setup()
    const modal = document.createElement('div')
    modal.setAttribute('role','dialog'); modal.setAttribute('aria-modal','true')
    document.body.append(modal)
    const before = camera.position.clone()
    key('F1'); move(650,450)
    expect(camera.position).toEqual(before)
    dispose()
  })
  it.each(['F1','F2','F3','F4'])('%s works with ROI rotation lock and Ctrl/Shift selection modifiers', (code) => {
    const {camera,controls,canvas,move,dispose} = setup()
    controls.noRotate = true
    camera.position.set(10,20,100)
    const input = document.createElement('input'); input.inputMode = 'decimal'; document.body.append(input); input.focus()
    const before = camera.position.clone()
    const event = new KeyboardEvent('keydown',{code,key:code,ctrlKey:true,shiftKey:true,cancelable:true})
    window.dispatchEvent(event); move(650,450)
    expect(event.defaultPrevented).toBe(true)
    expect(camera.position.distanceTo(before)).toBeGreaterThan(0)
    expect(controls.noRotate).toBe(true)
    expect(document.activeElement).toBe(input)
    expect(canvas).not.toBe(document.activeElement)
    dispose()
  })
  it('ends an unfinished selection drag before navigating, then restores mouse controls on release', () => {
    const {canvas,camera,controls} = createControls(DEFAULT_CAMERA_FOV_DEGREES)
    const beforeNavigate = vi.fn(() => { controls.enabled = true; return true })
    const detach = attachViewerNavigationShortcuts(canvas,controls,() => true,{beforeNavigate})
    canvas.dispatchEvent(new MouseEvent('pointerenter',{clientX:600,clientY:400}))
    controls.enabled = false
    const before = camera.position.clone()
    window.dispatchEvent(new KeyboardEvent('keydown',{code:'F1',ctrlKey:true,cancelable:true}))
    canvas.dispatchEvent(new MouseEvent('pointermove',{clientX:650,clientY:450,buttons:1}))
    expect(beforeNavigate).toHaveBeenCalledOnce()
    expect(camera.position.distanceTo(before)).toBeGreaterThan(0)
    window.dispatchEvent(new KeyboardEvent('keyup',{code:'F1'}))
    expect(controls.enabled).toBe(true)
    detach(); controls.dispose()
  })
  it.each(['F1','F2','F3','F4'])('%s navigates the selected mini-view camera without moving the main camera', (code) => {
    const {canvas,camera,controls} = createControls(DEFAULT_CAMERA_FOV_DEGREES)
    const mini = createControls(DEFAULT_CAMERA_FOV_DEGREES)
    mini.camera.position.set(10,20,100)
    const detach = attachViewerNavigationShortcuts(canvas,controls,() => true,{target:() => mini.controls,viewport:() => ({width:220,height:160})})
    canvas.dispatchEvent(new MouseEvent('pointerenter',{clientX:600,clientY:400}))
    const mainBefore = camera.position.clone(), miniBefore = mini.camera.position.clone()
    window.dispatchEvent(new KeyboardEvent('keydown',{code,cancelable:true}))
    canvas.dispatchEvent(new MouseEvent('pointermove',{clientX:620,clientY:420,buttons:0}))
    expect(camera.position).toEqual(mainBefore)
    expect(mini.camera.position.distanceTo(miniBefore)).toBeGreaterThan(0)
    detach(); controls.dispose(); mini.controls.dispose()
  })
})
