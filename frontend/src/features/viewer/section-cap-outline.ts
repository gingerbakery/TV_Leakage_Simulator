import { BufferGeometry, EdgesGeometry, Group } from 'three'
import { LineSegments2 } from 'three/examples/jsm/lines/LineSegments2.js'
import { LineSegmentsGeometry } from 'three/examples/jsm/lines/LineSegmentsGeometry.js'
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js'

/** Extract only non-coplanar/boundary edges: triangulation diagonals must
 * never be mistaken for the actual outer and hole contours of the cap. */
export function createSectionCapOutline(cap:BufferGeometry, name:string):Group {
  const root = new Group()
  root.name = name
  const edges = new EdgesGeometry(cap,30)
  const positions = edges.getAttribute('position').array as Float32Array
  for (const [color,width,order,label] of [[0x101820,4.5,130,'halo'],[0x4cddff,2.2,131,'edge']] as const) {
    const geometry = new LineSegmentsGeometry()
    geometry.setPositions(positions)
    const material = new LineMaterial({color,linewidth:width,worldUnits:false,depthTest:true,depthWrite:false,toneMapped:false,
      polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1})
    const lines = new LineSegments2(geometry,material)
    lines.name = `${name}-${label}`
    lines.renderOrder = order
    root.add(lines)
  }
  edges.dispose()
  return root
}
