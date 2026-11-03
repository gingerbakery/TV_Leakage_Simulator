import {expect,it} from 'vitest'
import {Path,Shape,ShapeGeometry,Vector4,type WebGLRenderer} from 'three'
import {LineSegments2} from 'three/examples/jsm/lines/LineSegments2.js'
import {createSectionCapOutline} from './section-cap-outline'

it('draws the outer contour and hole boundaries without triangulation diagonals', () => {
  const shape = new Shape().moveTo(-5,-5).lineTo(5,-5).lineTo(5,5).lineTo(-5,5).closePath()
  const hole = new Path().moveTo(-1,-1).lineTo(-1,1).lineTo(1,1).lineTo(1,-1).closePath()
  shape.holes.push(hole)
  const geometry = new ShapeGeometry(shape)
  const outline = createSectionCapOutline(geometry,'section-test')
  expect(outline.children).toHaveLength(2)
  const [halo,edge] = outline.children as LineSegments2[]
  for (const line of [halo,edge]) {
    expect(line.geometry.getAttribute('instanceStart').count).toBe(8)
    expect(line.material.depthTest).toBe(true)
    expect(line.material.depthWrite).toBe(false)
    expect(line.material.worldUnits).toBe(false)
  }
  expect(halo.material.color.getHex()).toBe(0x101820)
  expect(edge.material.color.getHex()).toBe(0x4cddff)
  expect(halo.material.linewidth).toBeGreaterThan(edge.material.linewidth)
  expect(edge.material.linewidth).toBeGreaterThan(1)
  expect(edge.renderOrder).toBeGreaterThan(halo.renderOrder)
  for (const [width,height] of [[800,600],[220,160]]) {
    edge.onBeforeRender({getViewport:(viewport:Vector4) => viewport.set(0,0,width,height)} as WebGLRenderer)
    expect(edge.material.resolution.toArray()).toEqual([width,height])
  }
})
