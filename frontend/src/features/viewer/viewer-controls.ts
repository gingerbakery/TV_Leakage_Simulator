import { MathUtils, PerspectiveCamera, Quaternion, Vector3 } from 'three'
import { TrackballControls } from 'three/examples/jsm/controls/TrackballControls.js'

import { DEFAULT_CAMERA_FOV_DEGREES } from './viewer-display'

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


/** Add held-key navigation without synthesizing clicks or changing mouse bindings. */
export function attachViewerNavigationShortcuts(
  canvas: HTMLCanvasElement,
  controls: ViewerTrackballControls,
  canNavigate: () => boolean = () => true,
): () => void {
  let hovered = false
  let mode: 'F1' | 'F2' | 'F3' | null = null
  let pointer: { x: number; y: number } | null = null
  const stop = () => {
    if (mode) controls.dispatchEvent({ type: 'end' })
    mode = null
  }
  const editable = (element: Element | null) => element?.closest(
    'input,textarea,select,button,[contenteditable="true"],[role="textbox"],[role="dialog"]',
  )
  const enter = (event: PointerEvent) => {
    hovered = true
    pointer = { x: event.clientX, y: event.clientY }
  }
  const leave = () => { hovered = false; pointer = null; stop() }
  const keydown = (event: KeyboardEvent) => {
    const key = event.code || event.key
    if (!['F1', 'F2', 'F3', 'F4'].includes(key)) return
    if ((!hovered && document.activeElement !== canvas) || !controls.enabled || !canNavigate()) return
    if (event.ctrlKey || event.altKey || event.metaKey || event.shiftKey || editable(document.activeElement)) return
    if (document.querySelector('[role="dialog"][aria-modal="true"]')) return
    event.preventDefault()
    if (event.repeat) return
    stop()
    if (key === 'F4') {
      controls.dispatchEvent({ type: 'start' })
      snapViewerToNearestView(controls)
      controls.dispatchEvent({ type: 'end' })
    } else {
      mode = key as typeof mode
      controls.dispatchEvent({ type: 'start' })
    }
  }
  const keyup = (event: KeyboardEvent) => {
    if ((event.code || event.key) === mode) stop()
  }
  const move = (event: PointerEvent) => {
    const previous = pointer
    pointer = { x: event.clientX, y: event.clientY }
    if (!mode || !previous) return
    if (!controls.enabled || !canNavigate() || event.buttons || editable(document.activeElement)) { stop(); return }
    const bounds = canvas.getBoundingClientRect()
    if (!bounds.width || !bounds.height) return
    const dx = (event.clientX - previous.x) / bounds.width
    const dy = (event.clientY - previous.y) / bounds.height
    const camera = controls.object
    const eye = camera.position.clone().sub(controls.target)
    const up = camera.up.clone().normalize()
    const right = new Vector3().crossVectors(up, eye).normalize()
    if (mode === 'F1') {
      const speed = (camera instanceof PerspectiveCamera ? projectionAwarePanSpeed(camera) : DEFAULT_VIEWER_PAN_SPEED)
        * (KEYBOARD_VIEWER_PAN_SPEED / DEFAULT_VIEWER_PAN_SPEED)
      const pan = right.multiplyScalar(-dx).addScaledVector(up, dy).multiplyScalar(eye.length() * speed)
      camera.position.add(pan)
      controls.target.add(pan)
    } else if (mode === 'F2') {
      const factor = Math.exp(MathUtils.clamp(dy * controls.zoomSpeed, -2, 2))
      eye.setLength(MathUtils.clamp(eye.length() * factor, Math.max(controls.minDistance, 1e-6), controls.maxDistance))
      camera.position.copy(controls.target).add(eye)
    } else {
      const rotation = new Quaternion().setFromAxisAngle(up, -dx * Math.PI * controls.rotateSpeed)
        .premultiply(new Quaternion().setFromAxisAngle(right, -dy * Math.PI * controls.rotateSpeed))
      camera.position.copy(controls.target).add(eye.applyQuaternion(rotation))
      camera.up.applyQuaternion(rotation).normalize()
    }
    camera.lookAt(controls.target)
    controls.update()
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

export function snapViewerToNearestView(controls: ViewerTrackballControls): void {
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
