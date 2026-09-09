import { describe, expect, it } from 'vitest'
import {
  DoubleSide,
  ShaderLib,
  UniformsUtils,
  type Material,
  type WebGLRenderer,
} from 'three'

import { createRoiCapMaterial } from './roi-cap-material'

describe('ROI section hatch material', () => {
  it.each([false, true])('hatches only cap color without changing geometry or opacity (wireframe=%s)', (wireframe) => {
    const material = createRoiCapMaterial(wireframe, 0.55)
    const original = wireframe ? ShaderLib.basic : ShaderLib.standard
    const shader = {
      uniforms: UniformsUtils.clone(original.uniforms),
      vertexShader: original.vertexShader,
      fragmentShader: original.fragmentShader,
    } as Parameters<Material['onBeforeCompile']>[0]
    material.onBeforeCompile(shader, {} as WebGLRenderer)

    expect(shader.vertexShader).toBe(original.vertexShader)
    expect(shader.fragmentShader).toContain('gl_FragCoord.x + gl_FragCoord.y')
    expect(shader.fragmentShader).toContain('fwidth(roiHatchCoordinate)')
    expect(shader.fragmentShader).toContain('diffuseColor.rgb = mix(')
    expect(shader.fragmentShader).toContain('#include <clipping_planes_fragment>')
    expect(shader.uniforms.roiHatchPixelRatio.value).toBe(1)
    expect(shader.uniforms.roiHatchColor.value.getHex())
      .toBe(wireframe ? 0x93b2c2 : 0x304d5c)
    expect(material.side).toBe(DoubleSide)
    expect(material.depthTest).toBe(true)
    expect(material.opacity).toBe(wireframe ? 0.75 : 0.55)
    expect(material.transparent).toBe(true)
    expect(material.customProgramCacheKey()).toBe('roi-section-hatch-v1')
    material.dispose()
  })

  it('keeps screen-space spacing when rendering resolution changes', () => {
    const material = createRoiCapMaterial(false, 1)
    const shader = {
      uniforms: {},
      vertexShader: ShaderLib.standard.vertexShader,
      fragmentShader: ShaderLib.standard.fragmentShader,
    } as Parameters<Material['onBeforeCompile']>[0]
    material.onBeforeCompile(shader, {} as WebGLRenderer)
    for (const pixelRatio of [2, 1, 0.75, 2]) {
      const renderArguments = [
        { getPixelRatio: () => pixelRatio },
      ] as unknown as Parameters<Material['onBeforeRender']>
      material.onBeforeRender(...renderArguments)
      expect(shader.uniforms.roiHatchPixelRatio.value).toBe(pixelRatio)
    }
    expect(material.opacity).toBe(1)
    expect(material.transparent).toBe(false)
    expect(material.depthWrite).toBe(true)
    material.dispose()
  })
})
