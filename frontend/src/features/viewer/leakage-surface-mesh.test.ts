import { describe, expect, it, vi } from 'vitest'
import { AdditiveBlending, NearestFilter } from 'three'
import { buildLeakageApertureField } from '@/features/results/leakage-aperture-field'
import type { LeakageApertureMask } from '@/features/results/leakage-aperture-mask'
import { createLeakageSurfaceMesh } from './leakage-surface-mesh'

function field() {
  const mask: LeakageApertureMask = {
    face: 'z_max', origin: [0,0], stepMm:1, width:3, height:1, plane:2,
    open:new Uint8Array([1,0,1]), componentIds:new Int32Array([0,-1,1]),
  }
  return buildLeakageApertureField(mask, [
    {runId:'result',pathIndex:0,receiverId:'front',exitFaces:['z_max'],exitPoint:[0.5,0.5,2],outgoingDirection:[0,0,1],weight:0.001},
    {runId:'result',pathIndex:1,receiverId:'front',exitFaces:['z_max'],exitPoint:[2.5,0.5,2],outgoingDirection:[0,0,1],weight:0.002},
  ])
}

describe('aperture surface rendering', () => {
  it('keeps real depth occlusion and draws no surface over the metal barrier', () => {
    const rendered=createLeakageSurfaceMesh(field(),0.002)
    const material=rendered.mesh.material
    expect(material.depthTest).toBe(true)
    expect(material.depthWrite).toBe(false)
    expect(material.toneMapped).toBe(false)
    expect(material.blending).toBe(AdditiveBlending)
    const vertices=rendered.mesh.geometry.getAttribute('position')
    expect(vertices.count).toBe(12)
    for(let i=0;i<vertices.count;i++){
      expect(vertices.getZ(i)).toBeCloseTo(2.002)
      expect(vertices.getX(i)<=1 || vertices.getX(i)>=2).toBe(true)
    }
    rendered.mesh.geometry.dispose()
    material.dispose()
  })

  it('uploads linear energy once and releases every custom texture on exit', () => {
    const source=field()
    const rendered=createLeakageSurfaceMesh(source,0.002)
    const material=rendered.mesh.material
    const textures=['density0','density1','density2','components'].map(key=>material.uniforms[key].value)
    const disposed=textures.map(texture=> {
      const callback=vi.fn()
      texture.addEventListener('dispose',callback)
      expect(texture.minFilter).toBe(NearestFilter)
      return callback
    })
    const packed=textures[0].image.data as Float32Array
    expect(packed[0]).toBeCloseTo(0.001)
    expect(packed[4]).toBe(0)
    expect(packed[8]).toBeCloseTo(0.002)
    expect(textures[3].image.data[4]).toBe(-1)
    rendered.mesh.geometry.dispose()
    material.dispose()
    disposed.forEach(callback=>expect(callback).toHaveBeenCalledOnce())
  })
})
