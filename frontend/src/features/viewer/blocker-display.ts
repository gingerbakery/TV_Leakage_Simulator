import { BoxGeometry, BufferGeometry, CylinderGeometry, DoubleSide, EdgesGeometry, Float32BufferAttribute, Group, LineBasicMaterial, LineSegments, Matrix4, Mesh, MeshBasicMaterial, Plane, Raycaster, Vector3, type Object3D } from 'three'
import type { ExpandedBlocker } from '@/features/leak-preview/blocker-groups'

export interface BlockerSelection { groupId: string; regionId: string | null }
export function blockerIsSelected(blocker: ExpandedBlocker, selected: BlockerSelection | null): boolean {
  return selected?.groupId === blocker.groupId && (selected.regionId === null || selected.regionId === blocker.regionId)
}

export function createBlockerDisplay(blocker: ExpandedBlocker, selected: BlockerSelection | null, plane: Plane | null): Group {
  const active = blockerIsSelected(blocker, selected), color = active ? 0xff7a00 : 0x050505
  const group = new Group()
  group.name = `${blocker.id}-display`
  group.userData.blockerSelection = { groupId: blocker.groupId, regionId: blocker.regionId }
  const normal = new Vector3(...blocker.normal).normalize().multiplyScalar(blocker.reverse ? -1 : 1)
  group.position.set(...blocker.baseCenter).addScaledVector(normal, blocker.offsetMm + blocker.depthMm/2)
  // Reverse flips two basis axes to keep a proper rotation (determinant +1).
  // A reflected basis cannot be represented by a quaternion and distorted
  // the displayed solid relative to the trace geometry for tilted blocks.
  const vAxis = new Vector3(...blocker.vAxis).normalize().multiplyScalar(blocker.reverse ? -1 : 1)
  group.setRotationFromMatrix(new Matrix4().makeBasis(new Vector3(...blocker.uAxis).normalize(), vAxis, normal))
  const geometry = blocker.shape === 'ellipse'
    ? new CylinderGeometry(1, 1, Math.max(blocker.depthMm, .01), 48).rotateX(Math.PI/2).scale(blocker.widthMm/2, blocker.heightMm/2, 1)
    : new BoxGeometry(Math.max(blocker.widthMm, .01), Math.max(blocker.heightMm, .01), Math.max(blocker.depthMm, .01))
  const solid = new Mesh(geometry, new MeshBasicMaterial({ color, transparent: true, opacity: active ? .55 : .28,
    depthTest: false, depthWrite: false, side: DoubleSide, toneMapped: false, clippingPlanes: plane ? [plane] : null }))
  solid.name = `${blocker.id}-solid`
  solid.userData.blockerSolid = true
  solid.renderOrder = 214
  const outline = new LineSegments(new EdgesGeometry(geometry), new LineBasicMaterial({ color, transparent: true, opacity: 1,
    depthTest: false, depthWrite: false, toneMapped: false, clippingPlanes: plane ? [plane] : null }))
  outline.name = `${blocker.id}-outline`
  outline.renderOrder = 215
  group.add(solid, outline)
  return group
}

function disposeDisplay(object: Object3D): void {
  object.removeFromParent()
  object.traverse((child) => {
    if (!(child instanceof Mesh) && !(child instanceof LineSegments)) return
    child.geometry.dispose()
    for (const material of Array.isArray(child.material) ? child.material : [child.material]) material.dispose()
  })
}

/** Keep geometry and GPU buffers for unchanged plugs when adding/selecting
 * another one. Display-only changes must not rebuild every cylinder. */
export function syncBlockerDisplays(root: Group, blockers: ExpandedBlocker[], selected: BlockerSelection | null, plane: Plane | null): void {
  const previous = new Map(root.children.map((child) => [child.userData.blockerId as string, child]))
  for (const blocker of blockers) {
    if (blocker.visible === false) continue
    const key = JSON.stringify([blocker.shape,blocker.baseCenter,blocker.uAxis,blocker.vAxis,blocker.normal,
      blocker.widthMm,blocker.heightMm,blocker.depthMm,blocker.offsetMm,blocker.reverse])
    let display = previous.get(blocker.id)
    previous.delete(blocker.id)
    if (display && display.userData.geometryKey !== key) { disposeDisplay(display); display = undefined }
    if (!display) {
      display = createBlockerDisplay(blocker,selected,plane)
      display.userData.blockerId = blocker.id; display.userData.geometryKey = key
      root.add(display)
    }
    const active = blockerIsSelected(blocker,selected)
    display.traverse((child) => {
      if (!(child instanceof Mesh) && !(child instanceof LineSegments)) return
      const material = child.material
      if (!(material instanceof MeshBasicMaterial) && !(material instanceof LineBasicMaterial)) return
      material.color.setHex(active ? 0xff7a00 : 0x050505)
      if (child instanceof Mesh) material.opacity = active ? .55 : .28
      if ((material.clippingPlanes?.[0] ?? null) !== plane) {
        material.clippingPlanes = plane ? [plane] : null
        material.needsUpdate = true
      }
    })
  }
  for (const stale of previous.values()) disposeDisplay(stale)
}

export function syncBlockerSectionCaps(root: Group, displays: Group, plane: Plane | null): void {
  const previous = new Map(root.children.map((child) => [child.userData.sourceSolid as string,child]))
  if (plane) displays.traverse((solid) => {
    if (!(solid instanceof Mesh) || !solid.userData.blockerSolid || !(solid.material instanceof MeshBasicMaterial)) return
    solid.updateWorldMatrix(true,false)
    const key = JSON.stringify([...plane.normal.toArray(),plane.constant,...solid.matrixWorld.elements])
    let cap = previous.get(solid.uuid) as Mesh<BufferGeometry,MeshBasicMaterial> | undefined
    previous.delete(solid.uuid)
    if (cap && cap.userData.sectionKey !== key) { disposeDisplay(cap); cap = undefined }
    if (!cap) {
      cap = createBlockerSectionCap(solid,plane) ?? undefined
      if (!cap) return
      cap.userData.sourceSolid = solid.uuid; cap.userData.sectionKey = key
      root.add(cap)
    }
    cap.material.color.copy(solid.material.color)
  })
  for (const stale of previous.values()) disposeDisplay(stale)
}

/** A convex Blocker has a convex planar section; draw a real cap so a cut
 * cylinder remains visible and pickable instead of appearing hollow. */
export function createBlockerSectionCap(solid: Mesh<BufferGeometry, MeshBasicMaterial>, plane: Plane): Mesh<BufferGeometry,MeshBasicMaterial> | null {
  solid.updateWorldMatrix(true, false)
  const positions = solid.geometry.getAttribute('position'), indices = solid.geometry.index
  const points: Vector3[] = []
  const add = (point: Vector3) => { if (!points.some((p) => p.distanceToSquared(point) < 1e-12)) points.push(point) }
  for (let i = 0; i < (indices?.count ?? positions.count); i += 3) {
    const vertices = [0,1,2].map((j) => new Vector3().fromBufferAttribute(positions, indices ? indices.getX(i+j) : i+j).applyMatrix4(solid.matrixWorld))
    for (let j = 0; j < 3; j++) {
      const a = vertices[j], b = vertices[(j+1)%3], da = plane.distanceToPoint(a), db = plane.distanceToPoint(b)
      if (Math.abs(da) < 1e-7) add(a)
      if (da*db < 0) add(a.clone().lerp(b, da/(da-db)))
    }
  }
  if (points.length < 3) return null
  const center = points.reduce((sum, p) => sum.add(p), new Vector3()).multiplyScalar(1/points.length)
  const u = new Vector3().crossVectors(plane.normal, Math.abs(plane.normal.y) < .9 ? new Vector3(0,1,0) : new Vector3(0,0,1)).normalize()
  const v = new Vector3().crossVectors(plane.normal, u)
  points.sort((a,b) => {
    const pa = a.clone().sub(center), pb = b.clone().sub(center)
    return Math.atan2(pa.dot(v), pa.dot(u))-Math.atan2(pb.dot(v), pb.dot(u))
  })
  const triangles: number[] = []
  for (let i = 0; i < points.length; i++) triangles.push(...center.toArray(), ...points[i].toArray(), ...points[(i+1)%points.length].toArray())
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new Float32BufferAttribute(triangles, 3))
  const cap = new Mesh(geometry, new MeshBasicMaterial({ color: solid.material.color.clone(), side: DoubleSide,
    depthTest: false, depthWrite: false, toneMapped: false }))
  cap.name = `${solid.name}-section-cap`
  cap.userData.blockerSelection = solid.parent?.userData.blockerSelection
  cap.renderOrder = 216
  return cap
}

export function pickBlocker(roots: Object3D[], raycaster: Raycaster, plane: Plane | null): BlockerSelection | null {
  for (const hit of raycaster.intersectObjects(roots, true)) {
    if (!(hit.object instanceof Mesh) || (plane && plane.distanceToPoint(hit.point) < -1e-6)) continue
    let selection: BlockerSelection | null = null, visible = true
    for (let object: Object3D | null = hit.object; object; object = object.parent) {
      if (!object.visible) visible = false
      selection ??= object.userData.blockerSelection ?? null
    }
    if (visible && selection) return selection
  }
  return null
}
