import {
  Color,
  DoubleSide,
  MeshBasicMaterial,
  MeshStandardMaterial,
} from 'three'

export function createRoiCapMaterial(wireframe: boolean, opacity: number) {
  const material = wireframe
    ? new MeshBasicMaterial({
        color: 0x314a5c,
        transparent: true,
        opacity: 0.75,
        side: DoubleSide,
        depthTest: true,
        depthWrite: true,
        toneMapped: false,
      })
    : new MeshStandardMaterial({
        color: 0x6f9fb5,
        roughness: 0.78,
        metalness: 0.02,
        flatShading: true,
        transparent: opacity < 1,
        opacity,
        side: DoubleSide,
        depthTest: true,
        depthWrite: opacity >= 1,
      })

  const pixelRatio = { value: 1 }
  const hatchColor = { value: new Color(wireframe ? 0x93b2c2 : 0x304d5c) }
  material.name = 'ROI section cap · display only'
  material.onBeforeRender = (renderer) => {
    pixelRatio.value = renderer.getPixelRatio()
  }
  material.onBeforeCompile = (shader) => {
    shader.uniforms.roiHatchPixelRatio = pixelRatio
    shader.uniforms.roiHatchColor = hatchColor
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        `#include <common>
uniform float roiHatchPixelRatio;
uniform vec3 roiHatchColor;`,
      )
      .replace(
        '#include <color_fragment>',
        `#include <color_fragment>
float roiHatchCoordinate = (gl_FragCoord.x + gl_FragCoord.y)
  * 0.70710678 / max(roiHatchPixelRatio, 0.1);
float roiHatchDistance = abs(fract(roiHatchCoordinate / 12.0 + 0.5) - 0.5) * 12.0;
float roiHatchAntialias = max(fwidth(roiHatchCoordinate) * 0.5, 0.1);
float roiHatchWeight = 1.0 - smoothstep(
  0.7 - roiHatchAntialias, 0.7 + roiHatchAntialias, roiHatchDistance);
diffuseColor.rgb = mix(diffuseColor.rgb, roiHatchColor, roiHatchWeight);`,
      )
  }
  material.customProgramCacheKey = () => 'roi-section-hatch-v1'
  return material
}
