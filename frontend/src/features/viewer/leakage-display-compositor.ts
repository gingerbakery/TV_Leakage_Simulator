import {
  Color, HalfFloatType, LinearFilter, MirroredRepeatWrapping, NoBlending,
  ShaderMaterial, Vector2, Vector4, WebGLRenderTarget,
  type Camera, type Group, type Material, type Object3D, type Scene, type WebGLRenderer,
} from 'three'
import { FullScreenQuad } from 'three/examples/jsm/postprocessing/Pass.js'

/** A fixed display PSF, not a calibrated camera or new optical transport. */
export const leakageDisplayPsfFraction = 0.12
export const leakageDisplayPsfSigmaCssPixels = 1
export const leakageDisplayMaximumPixels = 786_432
const maximumDimension = 1536
const kernelRadius = 3
const unnormalizedKernel = Array.from({ length: kernelRadius * 2 + 1 }, (_, index) =>
  Math.exp(-0.5 * ((index - kernelRadius) / leakageDisplayPsfSigmaCssPixels) ** 2),
)
const kernelSum = unnormalizedKernel.reduce((sum, value) => sum + value, 0)
export const leakageDisplayPsfKernel: readonly number[] = Object.freeze(
  unnormalizedKernel.map((value) => value / kernelSum),
)

export function leakageDisplayTargetSize(
  cssWidth: number,
  cssHeight: number,
  pixelRatio: number,
  maximumTextureSize: number,
): { width: number; height: number } | null {
  if (![cssWidth, cssHeight, pixelRatio, maximumTextureSize].every((value) => Number.isFinite(value) && value > 0)) return null
  const limit = Math.min(maximumDimension, Math.floor(maximumTextureSize))
  if (limit < 1) return null
  const scale = Math.min(pixelRatio, limit / cssWidth, limit / cssHeight,
    Math.sqrt(leakageDisplayMaximumPixels / (cssWidth * cssHeight)))
  return { width: Math.max(1, Math.floor(cssWidth * scale)), height: Math.max(1, Math.floor(cssHeight * scale)) }
}

function objectMaterials(object: Object3D): Material[] {
  const material = (object as Object3D & { material?: Material | Material[] }).material
  return material ? (Array.isArray(material) ? material : [material]) : []
}

const quadVertexShader = `
  varying vec2 screenUv;
  void main() {
    screenUv=uv;
    gl_Position=vec4(position.xy,0.0,1.0);
  }
`

function target(depth: boolean, samples: number): WebGLRenderTarget {
  const result = new WebGLRenderTarget(1, 1, {
    type: HalfFloatType, minFilter: LinearFilter, magFilter: LinearFilter,
    depthBuffer: depth, stencilBuffer: false, samples,
  })
  // Reflection at the frame boundary preserves both a constant image and
  // energy; clamping would duplicate an edge emitter during the blur.
  result.texture.wrapS = MirroredRepeatWrapping
  result.texture.wrapT = MirroredRepeatWrapping
  result.texture.generateMipmaps = false
  return result
}

/**
 * Two depth-aware scene passes + two small separable PSF passes + one composite.
 * Only receiver-derived leakage enters the PSF. Exterior display lights are
 * tone-mapped exactly once as before; leakage is compressed once after linear
 * filtering, then added in linear display space with one final output transfer.
 */
export class LeakageDisplayCompositor {
  private readonly renderer: WebGLRenderer
  private readonly baseTarget: WebGLRenderTarget
  private readonly lightTarget: WebGLRenderTarget
  private readonly horizontalTarget = target(false, 0)
  private readonly verticalTarget = target(false, 0)
  private readonly blurMaterial: ShaderMaterial
  private readonly compositeMaterial: ShaderMaterial
  private readonly quad: FullScreenQuad
  private needsFramebufferCheck = true
  private failed = false
  private disposed = false

  constructor(renderer: WebGLRenderer) {
    this.renderer = renderer
    const samples = Math.min(4, renderer.capabilities.maxSamples)
    this.baseTarget = target(true, samples)
    this.lightTarget = target(true, samples)
    this.blurMaterial = new ShaderMaterial({
      name: 'leakage-normalized-psf', depthTest: false, depthWrite: false,
      blending: NoBlending, toneMapped: false,
      uniforms: {
        inputLight: { value: this.lightTarget.texture },
        stepUv: { value: new Vector2() },
        weights: { value: [...leakageDisplayPsfKernel] },
      },
      vertexShader: quadVertexShader,
      fragmentShader: `
        uniform sampler2D inputLight;
        uniform vec2 stepUv;
        uniform float weights[7];
        varying vec2 screenUv;
        void main() {
          vec3 sum=vec3(0.0);
          for (int index=0;index<7;index++) {
            sum+=texture2D(inputLight,screenUv+stepUv*float(index-3)).rgb*weights[index];
          }
          gl_FragColor=vec4(sum,1.0);
        }
      `,
    })
    this.compositeMaterial = new ShaderMaterial({
      name: 'leakage-linear-display-composite', depthTest: false, depthWrite: false,
      blending: NoBlending, toneMapped: true,
      uniforms: {
        baseImage: { value: this.baseTarget.texture },
        directLight: { value: this.lightTarget.texture },
        spreadLight: { value: this.verticalTarget.texture },
        spreadFraction: { value: leakageDisplayPsfFraction },
      },
      vertexShader: quadVertexShader,
      fragmentShader: `
        uniform sampler2D baseImage;
        uniform sampler2D directLight;
        uniform sampler2D spreadLight;
        uniform float spreadFraction;
        varying vec2 screenUv;
        void main() {
          vec3 base=texture2D(baseImage,screenUv).rgb;
          #ifdef TONE_MAPPING
            base=toneMapping(base);
          #endif
          vec3 energy=max(mix(texture2D(directLight,screenUv).rgb,
            texture2D(spreadLight,screenUv).rgb,spreadFraction),vec3(0.0));
          vec3 light=vec3(1.0)-exp(-energy);
          gl_FragColor=vec4(base+light,1.0);
          #include <colorspace_fragment>
        }
      `,
    })
    this.quad = new FullScreenQuad(this.blurMaterial)
  }

  render(scene: Scene, camera: Camera, leakageRoot: Group, cssWidth: number, cssHeight: number): boolean {
    if (this.failed || this.disposed) return false
    const renderer = this.renderer
    const size = leakageDisplayTargetSize(cssWidth, cssHeight, renderer.getPixelRatio(), renderer.capabilities.maxTextureSize)
    if (!size) return false
    const previousTarget = renderer.getRenderTarget()
    // This compositor owns the main canvas only. Keep other render targets on
    // the existing direct path instead of changing their color/output contract.
    if (previousTarget !== null) return false
    const previousViewport = renderer.getViewport(new Vector4())
    const previousScissor = renderer.getScissor(new Vector4())
    const previousScissorTest = renderer.getScissorTest()
    const previousClearColor = renderer.getClearColor(new Color())
    const previousClearAlpha = renderer.getClearAlpha()
    const previousAutoClear = renderer.autoClear
    const previousBackground = scene.background
    const previousVisibility = leakageRoot.visible
    const colorWrites = new Map<Material, boolean>()
    const linearUniforms = new Map<{ value: unknown }, unknown>()
    try {
      const lightObjects = new Set<Object3D>()
      leakageRoot.traverse((object) => {
        lightObjects.add(object)
        for (const material of objectMaterials(object)) {
          if (!(material instanceof ShaderMaterial) || !material.uniforms.leakageLinearOutput) {
            throw new Error('Leakage material has no linear display contract')
          }
          const uniform = material.uniforms.leakageLinearOutput
          linearUniforms.set(uniform, uniform.value)
        }
      })
      const targets = [this.baseTarget, this.lightTarget, this.horizontalTarget, this.verticalTarget]
      for (const image of targets) {
        if (image.width !== size.width || image.height !== size.height) {
          image.setSize(size.width, size.height)
          this.needsFramebufferCheck = true
        }
      }
      renderer.autoClear = false
      renderer.setScissorTest(false)
      renderer.setClearColor(0x000000, 1)
      if (this.needsFramebufferCheck) {
        const gl = renderer.getContext()
        if (gl.isContextLost()) throw new Error('WebGL context unavailable')
        for (const image of targets) {
          renderer.setRenderTarget(image)
          if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
            throw new Error('Half-float display target unavailable')
          }
        }
        this.needsFramebufferCheck = false
      }

      // Base pass retains the original scene's opaque exterior and helper lights.
      leakageRoot.visible = false
      renderer.setRenderTarget(this.baseTarget)
      renderer.clear(true, true, true)
      renderer.render(scene, camera)

      // Preserve every visible object's depth behavior. Suppressing only color
      // writes lets foreground CAD still occlude light on a farther aperture.
      scene.traverse((object) => {
        if (lightObjects.has(object)) return
        for (const material of objectMaterials(object)) {
          if (!colorWrites.has(material)) colorWrites.set(material, material.colorWrite)
          material.colorWrite = false
        }
      })
      for (const uniform of linearUniforms.keys()) uniform.value = 1
      scene.background = null
      leakageRoot.visible = previousVisibility
      renderer.setRenderTarget(this.lightTarget)
      renderer.clear(true, true, true)
      renderer.render(scene, camera)
      for (const [material, value] of colorWrites) material.colorWrite = value
      for (const [uniform, value] of linearUniforms) uniform.value = value
      scene.background = previousBackground

      this.quad.material = this.blurMaterial
      this.blurMaterial.uniforms.inputLight.value = this.lightTarget.texture
      // UV offsets correspond to CSS pixels even when the render target is
      // downsampled by the memory cap or the display has a high pixel ratio.
      this.blurMaterial.uniforms.stepUv.value.set(1 / cssWidth, 0)
      renderer.setRenderTarget(this.horizontalTarget)
      this.quad.render(renderer)
      this.blurMaterial.uniforms.inputLight.value = this.horizontalTarget.texture
      this.blurMaterial.uniforms.stepUv.value.set(0, 1 / cssHeight)
      renderer.setRenderTarget(this.verticalTarget)
      this.quad.render(renderer)

      this.quad.material = this.compositeMaterial
      renderer.setRenderTarget(null)
      renderer.setViewport(previousViewport)
      renderer.setScissor(previousScissor)
      renderer.setScissorTest(previousScissorTest)
      this.quad.render(renderer)
      return true
    } catch {
      // A failed optional display pass must not suppress the existing preview.
      this.failed = true
      return false
    } finally {
      for (const [material, value] of colorWrites) material.colorWrite = value
      for (const [uniform, value] of linearUniforms) uniform.value = value
      leakageRoot.visible = previousVisibility
      scene.background = previousBackground
      renderer.autoClear = previousAutoClear
      renderer.setRenderTarget(previousTarget)
      renderer.setViewport(previousViewport)
      renderer.setScissor(previousScissor)
      renderer.setScissorTest(previousScissorTest)
      renderer.setClearColor(previousClearColor, previousClearAlpha)
    }
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.baseTarget.dispose()
    this.lightTarget.dispose()
    this.horizontalTarget.dispose()
    this.verticalTarget.dispose()
    this.blurMaterial.dispose()
    this.compositeMaterial.dispose()
    this.quad.dispose()
  }
}

export function createLeakageDisplayCompositor(renderer: WebGLRenderer): LeakageDisplayCompositor | null {
  if (!renderer.extensions.has('EXT_color_buffer_float') && !renderer.extensions.has('EXT_color_buffer_half_float')) return null
  try { return new LeakageDisplayCompositor(renderer) } catch { return null }
}
