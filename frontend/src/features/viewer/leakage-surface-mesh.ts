import {
  AdditiveBlending, BufferGeometry, DataTexture, DoubleSide,
  Float32BufferAttribute, FloatType, NearestFilter, Mesh,
  RGBAFormat, ShaderMaterial, Vector3,
} from 'three'
import type { LeakageApertureField } from '@/features/results/leakage-aperture-field'

// Common relative display gain for energy per square millimetre, not calibrated cd/m².
export const leakageSurfaceDensityGain = 8000

/** Draw each verified open cell once; direction bins accumulate before tone compression. */
export function createLeakageSurfaceMesh(field: LeakageApertureField, surfaceOffset: number) {
  const { mask } = field
  const count = mask.width * mask.height
  const packed = Array.from({length:3}, () => new Float32Array(count * 4))
  const directions = Array.from({length:9}, () => new Vector3(0, 0, 1))
  for (const bin of field.bins) {
    directions[bin.id].set(...bin.direction)
    const data = packed[Math.floor(bin.id / 4)]
    for (let index = 0; index < count; index++) data[index * 4 + bin.id % 4] = bin.density[index]
  }
  const componentData = new Float32Array(count * 4)
  for (let index=0; index<count; index++) componentData[index*4] = mask.componentIds[index]
  const textures = [...packed, componentData].map((data) => {
    const texture = new DataTexture(data, mask.width, mask.height, RGBAFormat, FloatType)
    texture.minFilter = NearestFilter
    texture.magFilter = NearestFilter
    texture.generateMipmaps = false
    texture.needsUpdate = true
    return texture
  })
  const positions:number[] = []
  const uvs:number[] = []
  const components:number[] = []
  const normalAxis = mask.face[0] === 'x' ? 0 : mask.face[0] === 'y' ? 1 : 2
  const axes = normalAxis === 0 ? [1,2] : normalAxis === 1 ? [0,2] : [0,1]
  const sign = mask.face.endsWith('max') ? 1 : -1
  const corners = [[0,0],[1,0],[1,1],[0,0],[1,1],[0,1]]
  const focus = new Vector3()
  let totalDensity = 0
  for (let index=0; index<count; index++) {
    if (!mask.open[index] || field.density[index] <= 0) continue
    const x=index % mask.width, y=Math.floor(index / mask.width)
    for (const [dx,dy] of corners) {
      const point = [0,0,0]
      point[normalAxis] = mask.plane + sign * surfaceOffset
      point[axes[0]] = mask.origin[0] + (x+dx) * mask.stepMm
      point[axes[1]] = mask.origin[1] + (y+dy) * mask.stepMm
      positions.push(...point)
      uvs.push((x+dx)/mask.width, (y+dy)/mask.height)
      components.push(mask.componentIds[index])
    }
    const center = new Vector3()
    center.setComponent(normalAxis, mask.plane)
    center.setComponent(axes[0], mask.origin[0]+(x+0.5)*mask.stepMm)
    center.setComponent(axes[1], mask.origin[1]+(y+0.5)*mask.stepMm)
    focus.addScaledVector(center, field.density[index])
    totalDensity += field.density[index]
  }
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new Float32BufferAttribute(positions,3))
  geometry.setAttribute('uv', new Float32BufferAttribute(uvs,2))
  geometry.setAttribute('apertureComponent', new Float32BufferAttribute(components,1))
  if (positions.length) geometry.computeBoundingSphere()
  const material = new ShaderMaterial({
    transparent:true, depthTest:true, depthWrite:false, side:DoubleSide,
    blending:AdditiveBlending, toneMapped:false,
    uniforms:{
      density0:{value:textures[0]}, density1:{value:textures[1]}, density2:{value:textures[2]},
      directions:{value:directions}, gain:{value:leakageSurfaceDensityGain},
      leakageLinearOutput:{value:0},
      components:{value:textures[3]}, gridSize:{value:[mask.width,mask.height]},
    },
    vertexShader:`
      attribute float apertureComponent;
      varying float fieldComponent;
      varying vec2 fieldUv;
      varying vec3 fieldWorld;
      void main() {
        fieldComponent = apertureComponent;
        fieldUv = uv;
        vec4 world = modelMatrix * vec4(position,1.0);
        fieldWorld = world.xyz;
        gl_Position = projectionMatrix * viewMatrix * world;
      }
    `,
    fragmentShader:`
      uniform sampler2D density0;
      uniform sampler2D density1;
      uniform sampler2D density2;
      uniform vec3 directions[9];
      uniform float gain;
      uniform float leakageLinearOutput;
      uniform sampler2D components;
      uniform vec2 gridSize;
      varying float fieldComponent;
      varying vec2 fieldUv;
      varying vec3 fieldWorld;
      float angular(vec3 direction, vec3 viewDirection) {
        return smoothstep(0.35,0.95,max(dot(direction,viewDirection),0.0));
      }
      // Manual bilinear sampling works without float-linear extensions and
      // never borrows light from another aperture at a diagonal corner.
      vec4 densityAt(sampler2D image) {
        vec2 cell=fieldUv*gridSize-vec2(0.5);
        vec2 base=floor(cell);
        vec2 fraction=fract(cell);
        vec4 value=vec4(0.0);
        float total=0.0;
        for (int y=0;y<2;y++) for (int x=0;x<2;x++) {
          vec2 offset=vec2(float(x),float(y));
          vec2 index=base+offset;
          if(index.x<0.0 || index.y<0.0 || index.x>=gridSize.x || index.y>=gridSize.y) continue;
          vec2 coordinate=(index+vec2(0.5))/gridSize;
          if(abs(texture2D(components,coordinate).r-fieldComponent)>0.5) continue;
          float weight=(x==0 ? 1.0-fraction.x : fraction.x)*(y==0 ? 1.0-fraction.y : fraction.y);
          value+=texture2D(image,coordinate)*weight;
          total+=weight;
        }
        return total>0.0 ? value/total : vec4(0.0);
      }
      void main() {
        vec4 a=densityAt(density0);
        vec4 b=densityAt(density1);
        float c=densityAt(density2).r;
        vec3 viewDirection=normalize(cameraPosition-fieldWorld);
        float density =
          a.r*angular(directions[0],viewDirection)+a.g*angular(directions[1],viewDirection)+
          a.b*angular(directions[2],viewDirection)+a.a*angular(directions[3],viewDirection)+
          b.r*angular(directions[4],viewDirection)+b.g*angular(directions[5],viewDirection)+
          b.b*angular(directions[6],viewDirection)+b.a*angular(directions[7],viewDirection)+
          c*angular(directions[8],viewDirection);
        float energy=max(density*gain,0.0);
        float strength=(1.0-exp(-energy))*smoothstep(0.002,0.01,energy);
        if (strength < 0.001) discard;
        float outputEnergy=leakageLinearOutput>0.5
          ? energy*smoothstep(0.002,0.01,energy) : strength;
        gl_FragColor=vec4(vec3(0.82,0.91,1.0)*outputEnergy,1.0);
        #include <colorspace_fragment>
      }
    `,
  })
  // Three.js material disposal does not dispose custom sampler textures.
  material.addEventListener('dispose', () => textures.forEach((texture) => texture.dispose()))
  const mesh=new Mesh(geometry,material)
  mesh.name='leakage-preview-aperture-density'
  mesh.renderOrder=20
  return {mesh, focus: totalDensity > 0 ? focus.multiplyScalar(1/totalDensity) : null}
}
