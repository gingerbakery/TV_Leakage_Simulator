import { describe, expect, it } from 'vitest'

import {
  AXIS_CAMERA_FOV_DEGREES,
  cameraFovForPreset,
  DEFAULT_CAMERA_FOV_DEGREES,
  getAxisCameraPresetAxes,
  resolveComponentColor,
  resolveComponentColorHex,
  surfaceOpacityFromTransparency,
} from './viewer-display'
import { createSceneFixture } from '@/test/scene-fixture'

describe('viewer display settings', () => {
  it('keeps positive Y pointing upward in both YZ views', () => {
    expect(getAxisCameraPresetAxes('YZ')).toEqual({
      direction: [1, 0, 0],
      up: [0, 1, 0],
    })
    expect(getAxisCameraPresetAxes('-YZ')).toEqual({
      direction: [-1, 0, 0],
      up: [0, 1, 0],
    })
  })

  it('maps transparency percentages to bounded surface opacity', () => {
    expect(surfaceOpacityFromTransparency(0)).toBe(1)
    expect(surfaceOpacityFromTransparency(35)).toBe(0.65)
    expect(surfaceOpacityFromTransparency(85)).toBeCloseTo(0.15)
    expect(surfaceOpacityFromTransparency(-10)).toBe(1)
    expect(surfaceOpacityFromTransparency(100)).toBeCloseTo(0.1)
  })

  it('uses a telephoto field of view for axis-aligned presets', () => {
    for (const preset of ['XY', '-XY', 'YZ', '-YZ', 'ZX', '-ZX'] as const) {
      expect(cameraFovForPreset(preset, DEFAULT_CAMERA_FOV_DEGREES)).toBe(
        AXIS_CAMERA_FOV_DEGREES,
      )
    }
    expect(
      cameraFovForPreset('Iso', AXIS_CAMERA_FOV_DEGREES),
    ).toBe(DEFAULT_CAMERA_FOV_DEGREES)
    expect(
      cameraFovForPreset('Fit', AXIS_CAMERA_FOV_DEGREES),
    ).toBe(AXIS_CAMERA_FOV_DEGREES)
  })

  it('shares the exact component fallback colors with Viewer menus', () => {
    const scene = createSceneFixture()
    const authoredComponent = {
      ...scene.components[0],
      color: '#ff0000',
    }

    expect(resolveComponentColor(scene.components[0], 0)).toBe(0x64748b)
    expect(resolveComponentColor(scene.components[1], 1)).toBe(0x526b7a)
    expect(resolveComponentColorHex(scene.components[1], 1)).toBe('#526b7a')
    expect(resolveComponentColor(authoredComponent, 0)).toBe(0xff0000)
  })
})
