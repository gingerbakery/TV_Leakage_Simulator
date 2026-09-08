import { describe, expect, it, vi } from 'vitest'
import {
  BoxGeometry, Color, Group, Mesh, MeshStandardMaterial, PerspectiveCamera,
  Scene, ShaderMaterial, Vector4, type WebGLRenderer, type WebGLRenderTarget,
} from 'three'
import {
  createLeakageDisplayCompositor, leakageDisplayMaximumPixels,
  leakageDisplayPsfFraction, leakageDisplayPsfKernel, leakageDisplayTargetSize,
} from './leakage-display-compositor'

function reflect(index: number, length: number): number {
  const reflected = ((index % (2 * length)) + 2 * length) % (2 * length)
  return reflected < length ? reflected : 2 * length - reflected - 1
}

/** Reference convolution using the same uploaded, normalized shader kernel. */
function filtered(input: readonly number[], width: number, height: number): number[] {
  const pass = (values: readonly number[], horizontal: boolean): number[] => values.map((_, index) => {
    const x = index % width
    const y = Math.floor(index / width)
    return leakageDisplayPsfKernel.reduce((sum, weight, tap) => {
      const sampleX = horizontal ? reflect(x + tap - 3, width) : x
      const sampleY = horizontal ? y : reflect(y + tap - 3, height)
      return sum + values[sampleY * width + sampleX] * weight
    }, 0)
  })
  const blurred = pass(pass(input, true), false)
  return input.map((value, index) => (1 - leakageDisplayPsfFraction) * value + leakageDisplayPsfFraction * blurred[index])
}

function fixture() {
  const scene = new Scene()
  scene.background = new Color(0x102030)
  const material = new MeshStandardMaterial()
  const occluder = new Mesh(new BoxGeometry(), material)
  scene.add(occluder)
  const leakageRoot = new Group()
  const lightMaterial = new ShaderMaterial({ uniforms: { leakageLinearOutput: { value: 0 } } })
  lightMaterial.depthTest = true
  lightMaterial.depthWrite = false
  leakageRoot.add(new Mesh(new BoxGeometry(), lightMaterial))
  scene.add(leakageRoot)
  return { scene, material, lightMaterial, leakageRoot, camera: new PerspectiveCamera() }
}

function rendererHarness(data: ReturnType<typeof fixture>, failAtRender = -1, complete = true) {
  let renderTarget: WebGLRenderTarget | null = null
  let viewport = new Vector4(3, 4, 100, 70)
  let scissor = new Vector4(6, 7, 80, 60)
  let scissorTest = true
  const clearColor = new Color(0x123456)
  let clearAlpha = 0.25
  let rendered = 0
  const targets = new Set<WebGLRenderTarget>()
  const calls: Array<{
    kind: string; target: WebGLRenderTarget | null; visible?: boolean;
    colorWrite?: boolean; depthWrite?: boolean; raw?: unknown; background?: unknown;
    input?: unknown; step?: number[]; fraction?: unknown; weights?: unknown;
  }> = []
  const gl = {
    FRAMEBUFFER: 36160, FRAMEBUFFER_COMPLETE: 36053,
    isContextLost: () => false,
    checkFramebufferStatus: () => complete ? 36053 : 36054,
  }
  const renderer = {
    capabilities: { maxSamples: 4, maxTextureSize: 4096 },
    extensions: { has: vi.fn(() => true) },
    autoClear: true,
    getContext: () => gl,
    getPixelRatio: () => 2,
    getRenderTarget: () => renderTarget,
    setRenderTarget: (value: WebGLRenderTarget | null) => { renderTarget = value; if (value) targets.add(value) },
    getViewport: (out: Vector4) => out.copy(viewport),
    setViewport: (value: Vector4) => { viewport = value.clone() },
    getScissor: (out: Vector4) => out.copy(scissor),
    setScissor: (value: Vector4) => { scissor = value.clone() },
    getScissorTest: () => scissorTest,
    setScissorTest: (value: boolean) => { scissorTest = value },
    getClearColor: (out: Color) => out.copy(clearColor),
    getClearAlpha: () => clearAlpha,
    setClearColor: (color: Color | number, alpha: number) => { clearColor.set(color); clearAlpha = alpha },
    clear: vi.fn(),
    render: (object: Scene | Mesh) => {
      rendered += 1
      if (object === data.scene) {
        calls.push({ kind: 'scene', target: renderTarget, visible: data.leakageRoot.visible,
          colorWrite: data.material.colorWrite, depthWrite: data.material.depthWrite,
          raw: data.lightMaterial.uniforms.leakageLinearOutput.value, background: data.scene.background })
      } else {
        const material = (object as Mesh).material as ShaderMaterial
        calls.push({ kind: material.name, target: renderTarget,
          input: material.uniforms.inputLight?.value, step: material.uniforms.stepUv?.value.toArray(),
          fraction: material.uniforms.spreadFraction?.value, weights: material.uniforms.weights?.value })
      }
      if (rendered === failAtRender) throw new Error('Injected display failure')
    },
  }
  return { renderer: renderer as unknown as WebGLRenderer, calls, targets,
    state: () => ({ renderTarget, viewport: viewport.toArray(), scissor: scissor.toArray(), scissorTest, clearColor: clearColor.getHex(), clearAlpha, autoClear: renderer.autoClear }) }
}

describe('bounded light-only display PSF', () => {
  it('preserves total linear light for both centered and frame-edge emitters', () => {
    expect(leakageDisplayPsfKernel.reduce((sum, weight) => sum + weight, 0)).toBeCloseTo(1, 14)
    for (const emitter of [0, 40, 80]) {
      const image = Array.from({ length: 81 }, (_, index) => index === emitter ? 1 : 0)
      const result = filtered(image, 9, 9)
      expect(result.reduce((sum, value) => sum + value, 0)).toBeCloseTo(1, 13)
      expect(result[emitter]).toBeGreaterThan(1 - leakageDisplayPsfFraction)
    }
  })

  it('preserves a constant field and produces no light from an off image', () => {
    for (const value of [0, 0.2, 3]) {
      const result = filtered(new Array(35).fill(value), 7, 5)
      result.forEach((pixel) => expect(pixel).toBeCloseTo(value, 13))
    }
  })

  it('keeps the 1:4:16 relation before the shared display compression', () => {
    const image = Array.from({ length: 81 }, (_, index) => index % 9 === 4 ? (index + 1) / 100 : 0)
    const baseline = filtered(image, 9, 9)
    for (const power of [4, 16]) {
      const result = filtered(image.map((value) => value * power), 9, 9)
      result.forEach((value, index) => expect(value).toBeCloseTo(baseline[index] * power, 12))
    }
  })

  it('caps target storage while preserving the viewport aspect and valid dimensions', () => {
    for (const [width, height, ratio, maximum] of [[1280, 720, 2, 4096], [7680, 4320, 3, 4096], [320, 900, 1, 512]]) {
      const size = leakageDisplayTargetSize(width, height, ratio, maximum)!
      expect(size.width * size.height).toBeLessThanOrEqual(leakageDisplayMaximumPixels)
      expect(Math.max(size.width, size.height)).toBeLessThanOrEqual(Math.min(1536, maximum))
      expect(Math.abs(size.width / size.height - width / height)).toBeLessThan(0.01)
    }
    expect(leakageDisplayTargetSize(0, 50, 1, 1024)).toBeNull()
  })

  it('keeps exterior out of the light pass, retains CAD depth and restores all state', () => {
    const data = fixture()
    const harness = rendererHarness(data)
    const before = harness.state()
    const background = data.scene.background
    const compositor = createLeakageDisplayCompositor(harness.renderer)!
    expect(compositor.render(data.scene, data.camera, data.leakageRoot, 100, 70)).toBe(true)
    expect(harness.calls).toHaveLength(5)
    expect(harness.calls[0]).toMatchObject({ kind: 'scene', visible: false, colorWrite: true, depthWrite: true, raw: 0 })
    expect(harness.calls[1]).toMatchObject({ kind: 'scene', visible: true, colorWrite: false, depthWrite: true, raw: 1, background: null })
    expect(harness.calls[2]).toMatchObject({ kind: 'leakage-normalized-psf', step: [1 / 100, 0], weights: [...leakageDisplayPsfKernel] })
    expect(harness.calls[2].input).toBe(harness.calls[1].target!.texture)
    expect(harness.calls[3]).toMatchObject({ kind: 'leakage-normalized-psf', step: [0, 1 / 70] })
    expect(harness.calls[4]).toMatchObject({ kind: 'leakage-linear-display-composite', target: null, fraction: leakageDisplayPsfFraction })
    expect(harness.state()).toEqual(before)
    expect(data.scene.background).toBe(background)
    expect(data.material.colorWrite).toBe(true)
    expect(data.leakageRoot.visible).toBe(true)
    expect(data.lightMaterial.uniforms.leakageLinearOutput.value).toBe(0)
    const disposal = [...harness.targets].map((image) => {
      const listener = vi.fn()
      image.addEventListener('dispose', listener)
      return listener
    })
    compositor.dispose()
    compositor.dispose()
    expect(disposal).toHaveLength(4)
    disposal.forEach((listener) => expect(listener).toHaveBeenCalledOnce())
  })

  it('runs the same exterior path when the zero-light Case has no leakage meshes', () => {
    const data = fixture()
    data.leakageRoot.clear()
    const harness = rendererHarness(data)
    const compositor = createLeakageDisplayCompositor(harness.renderer)!
    expect(compositor.render(data.scene, data.camera, data.leakageRoot, 100, 70)).toBe(true)
    expect(harness.calls).toHaveLength(5)
    expect(harness.calls[1].colorWrite).toBe(false)
    compositor.dispose()
  })

  it('restores the original preview contract after a failure during the light pass', () => {
    const data = fixture()
    const harness = rendererHarness(data, 2)
    const before = harness.state()
    const background = data.scene.background
    const compositor = createLeakageDisplayCompositor(harness.renderer)!
    expect(compositor.render(data.scene, data.camera, data.leakageRoot, 100, 70)).toBe(false)
    expect(harness.state()).toEqual(before)
    expect(data.material.colorWrite).toBe(true)
    expect(data.leakageRoot.visible).toBe(true)
    expect(data.scene.background).toBe(background)
    expect(data.lightMaterial.uniforms.leakageLinearOutput.value).toBe(0)
    expect(compositor.render(data.scene, data.camera, data.leakageRoot, 100, 70)).toBe(false)
    expect(harness.calls).toHaveLength(2)
    compositor.dispose()
  })

  it('keeps unsupported or incomplete framebuffers on the direct path', () => {
    const data = fixture()
    const unsupported = rendererHarness(data)
    vi.spyOn(unsupported.renderer.extensions, 'has').mockReturnValue(false)
    expect(createLeakageDisplayCompositor(unsupported.renderer)).toBeNull()
    const incomplete = rendererHarness(data, -1, false)
    const compositor = createLeakageDisplayCompositor(incomplete.renderer)!
    const before = incomplete.state()
    expect(compositor.render(data.scene, data.camera, data.leakageRoot, 100, 70)).toBe(false)
    expect(incomplete.calls).toHaveLength(0)
    expect(incomplete.state()).toEqual(before)
    compositor.dispose()
  })
})
