import { MathUtils, PerspectiveCamera } from 'three'
import { TrackballControls } from 'three/examples/jsm/controls/TrackballControls.js'

import { DEFAULT_CAMERA_FOV_DEGREES } from './viewer-display'

export const DEFAULT_VIEWER_PAN_SPEED = 0.16

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
