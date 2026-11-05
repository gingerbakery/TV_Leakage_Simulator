import { MathUtils, PerspectiveCamera, Quaternion, Vector3 } from 'three'
import { TrackballControls } from 'three/examples/jsm/controls/TrackballControls.js'

import { DEFAULT_CAMERA_FOV_DEGREES } from './viewer-display'
import { viewerShortcutAllowed } from './viewer-shortcuts'

export const DEFAULT_VIEWER_PAN_SPEED = 0.16
export const KEYBOARD_VIEWER_PAN_SPEED = 0.24

export function projectionAwarePanSpeed(camera: PerspectiveCamera): number {
  return DEFAULT_VIEWER_PAN_SPEED *
    Math.tan(MathUtils.degToRad(camera.getEffectiveFOV()) / 2) /
    Math.tan(MathUtils.degToRad(DEFAULT_CAMERA_FOV_DEGREES) / 2)
}

export class ViewerTrackballControls extends TrackballControls {
  override update(): void {
    if (this.object instanceof PerspectiveCamera) {
      this.panSpeed = projectionAwarePanSpeed(this.object)
    }
    super.update()
  }
}

/** Capture Ctrl/Cmd or an explicitly anchored surface drag before navigation.
 * Unanchored ordinary drags and right/middle/wheel keep their normal route. */
export function attachBlockerBoxDrawing(
  canvas:HTMLCanvasElement, enabled:()=>boolean, begin:(event:PointerEvent)=>void,
  surfaceSelected:()=>boolean = () => false,
):()=>void {
  const down = (event:PointerEvent) => {
    if (!enabled() || event.button !== 0 || !(event.ctrlKey || event.metaKey || surfaceSelected())) return
    event.preventDefault()
    event.stopImmediatePropagation()
    begin(event)
  }
  canvas.addEventListener('pointerdown',down,true)
  return () => canvas.removeEventListener('pointerdown',down,true)
}

/** Escape leaves explicit Block creation, without stealing text/dialog Escape. */
export function attachBlockerDrawingEscape(enabled:()=>boolean, cancel:()=>void):()=>void {
  const editable = (target:EventTarget|null) => target instanceof Element &&
    Boolean(target.closest('input,textarea,select,[contenteditable="true"],[role="textbox"]'))
  const keydown = (event:KeyboardEvent) => {
    if (event.key !== 'Escape' || event.defaultPrevented || event.repeat || event.isComposing ||
      !enabled() || editable(event.target) || editable(document.activeElement)) return
    for (const dialog of document.querySelectorAll('[role="dialog"]')) {
      if (!dialog.closest('[hidden],[aria-hidden="true"]')) return
    }
    event.preventDefault()
    cancel()
  }
  window.addEventListener('keydown',keydown)
  return () => window.removeEventListener('keydown',keydown)
}


/** Add held-key navigation without synthesizing clicks or changing mouse bindings. */
export function attachViewerNavigationShortcuts(
  canvas: HTMLCanvasElement,
  controls: ViewerTrackballControls,
  canNavigate: () => boolean = () => true,
  options: {
    beforeNavigate?():boolean
    target?():ViewerNavigationTarget
    viewport?():{width:number;height:number}
  } = {},
): () => void {
  let hovered = false
  let mode: 'F1' | 'F2' | 'F3' | null = null
  let pointer: { x: number; y: number } | null = null
  let navigation: ViewerNavigationTarget = controls
  let suspendedPointer = false
  let navigationViewport: {width:number;height:number} | undefined
  const stop = () => {
    if (suspendedPointer) { controls.enabled = true; suspendedPointer = false }
    if (mode) navigation.dispatchEvent({ type: 'end' })
    mode = null
  }
  const enter = (event: PointerEvent) => {
    hovered = true
    pointer = { x: event.clientX, y: event.clientY }
  }
  const leave = () => { hovered = false; pointer = null; stop() }
  const keydown = (event: KeyboardEvent) => {
    const key = event.code || event.key
    if (!['F1', 'F2', 'F3', 'F4'].includes(key)) return
    if (event.defaultPrevented || !viewerShortcutAllowed(canvas,hovered,true) || !canNavigate() || event.altKey || event.metaKey) return
    event.preventDefault()
    if (event.repeat) return
    stop()
    const cancelledDrawing = options.beforeNavigate?.() ?? false
    if (!controls.enabled) return
    navigation = options.target?.() ?? controls
    navigationViewport = options.viewport?.()
    if (key === 'F4') {
      navigation.dispatchEvent({ type: 'start' })
      snapViewerToNearestView(navigation)
      navigation.dispatchEvent({ type: 'end' })
    } else {
      mode = key as typeof mode
      if (cancelledDrawing) { controls.enabled = false; suspendedPointer = true }
      navigation.dispatchEvent({ type: 'start' })
    }
  }
  const keyup = (event: KeyboardEvent) => {
    if ((event.code || event.key) === mode) stop()
  }
  const move = (event: PointerEvent) => {
    const previous = pointer
    pointer = { x: event.clientX, y: event.clientY }
    if (!mode || !previous) return
    if ((!controls.enabled && !suspendedPointer) || !canNavigate() || (event.buttons && !suspendedPointer) ||
      !viewerShortcutAllowed(canvas,hovered,true)) { stop(); return }
    const bounds = navigationViewport ?? canvas.getBoundingClientRect()
    if (!bounds.width || !bounds.height) return
    const dx = (event.clientX - previous.x) / bounds.width
    const dy = (event.clientY - previous.y) / bounds.height
    const camera = navigation.object
    const eye = camera.position.clone().sub(navigation.target)
    const up = camera.up.clone().normalize()
    const right = new Vector3().crossVectors(up, eye).normalize()
    if (mode === 'F1') {
      const speed = (camera instanceof PerspectiveCamera ? projectionAwarePanSpeed(camera) : DEFAULT_VIEWER_PAN_SPEED)
        * (KEYBOARD_VIEWER_PAN_SPEED / DEFAULT_VIEWER_PAN_SPEED)
      const pan = right.multiplyScalar(-dx).addScaledVector(up, dy).multiplyScalar(eye.length() * speed)
      camera.position.add(pan)
      navigation.target.add(pan)
    } else if (mode === 'F2') {
      const factor = Math.exp(MathUtils.clamp(dy * navigation.zoomSpeed, -2, 2))
      eye.setLength(MathUtils.clamp(eye.length() * factor, Math.max(navigation.minDistance, 1e-6), navigation.maxDistance))
      camera.position.copy(navigation.target).add(eye)
    } else {
      const rotation = new Quaternion().setFromAxisAngle(up, -dx * Math.PI * navigation.rotateSpeed)
        .premultiply(new Quaternion().setFromAxisAngle(right, -dy * Math.PI * navigation.rotateSpeed))
      camera.position.copy(navigation.target).add(eye.applyQuaternion(rotation))
      camera.up.applyQuaternion(rotation).normalize()
    }
    camera.lookAt(navigation.target)
    navigation.update()
  }
  const down = () => stop()
  const visibility = () => { if (document.hidden) stop() }
  canvas.addEventListener('pointerenter', enter)
  canvas.addEventListener('pointerleave', leave)
  canvas.addEventListener('pointermove', move)
  canvas.addEventListener('pointerdown', down)
  window.addEventListener('keydown', keydown)
  window.addEventListener('keyup', keyup)
  window.addEventListener('blur', stop)
  document.addEventListener('visibilitychange', visibility)
  return () => {
    stop()
    canvas.removeEventListener('pointerenter', enter)
    canvas.removeEventListener('pointerleave', leave)
    canvas.removeEventListener('pointermove', move)
    canvas.removeEventListener('pointerdown', down)
    window.removeEventListener('keydown', keydown)
    window.removeEventListener('keyup', keyup)
    window.removeEventListener('blur', stop)
    document.removeEventListener('visibilitychange', visibility)
  }
}

export type ViewerNavigationTarget = Pick<ViewerTrackballControls,'object'|'target'|'rotateSpeed'|'zoomSpeed'|'minDistance'|'maxDistance'|'dispatchEvent'|'update'>

export function snapViewerToNearestView(controls: Pick<ViewerNavigationTarget,'object'|'target'|'update'>): void {
  const camera = controls.object
  const eye = camera.position.clone().sub(controls.target)
  const distance = eye.length()
  if (distance < 1e-9) return
  const axes = [new Vector3(1, 0, 0), new Vector3(0, 1, 0), new Vector3(0, 0, 1)]
  const direction = axes.reduce((best, axis) => Math.abs(eye.dot(axis)) > Math.abs(eye.dot(best)) ? axis : best)
    .clone()
  direction.multiplyScalar(Math.sign(eye.dot(direction)))
  const upCandidates = axes.filter((axis) => Math.abs(axis.dot(direction)) < 0.5)
    .flatMap((axis) => [axis.clone(), axis.clone().negate()])
  const up = upCandidates.reduce((best, axis) => camera.up.dot(axis) > camera.up.dot(best) ? axis : best)
  camera.position.copy(controls.target).addScaledVector(direction, distance)
  camera.up.copy(up)
  camera.lookAt(controls.target)
  controls.update()
}
