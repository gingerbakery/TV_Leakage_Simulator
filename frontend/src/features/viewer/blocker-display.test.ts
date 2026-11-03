import { expect, it, vi } from 'vitest'
import { Box3, Group, Mesh, MeshBasicMaterial, Plane, Raycaster, Vector3 } from 'three'
import { createBlockGroup, expandBlockerGroups } from '@/features/leak-preview/blocker-groups'
import { blockerIsSelected, createBlockerDisplay, createBlockerSectionCap, pickBlocker, syncBlockerDisplays, syncBlockerSectionCaps } from './blocker-display'
import { setObjectClippingPlane } from './section-view'

function blocker() { return expandBlockerGroups([{ ...createBlockGroup('Block 1', 'ellipse'), id:'holes',
  regions: [{ ...createBlockGroup('', 'ellipse'), id:'one', regions:undefined, baseCenter:[0,0,0], widthMm:6, heightMm:4 }] }])[0] }

it('renders a symmetric 5 mm plug and shares selection identity with its group and region', () => {
  const region = blocker(), display = createBlockerDisplay(region, { groupId:'holes', regionId:'one' }, null)
  display.updateMatrixWorld(true)
  const bounds = new Box3().setFromObject(display)
  expect(bounds.min.z).toBeCloseTo(-2.5, 5); expect(bounds.max.z).toBeCloseTo(2.5, 5)
  expect(blockerIsSelected(region, { groupId:'holes', regionId:null })).toBe(true)
  expect(blockerIsSelected(region, { groupId:'holes', regionId:'other' })).toBe(false)
  expect((display.children[0] as Mesh<never,MeshBasicMaterial>).material.color.getHex()).toBe(0xff7a00)
  const ray = new Raycaster(new Vector3(0,0,10), new Vector3(0,0,-1))
  expect(pickBlocker([display], ray, null)).toEqual({ groupId:'holes', regionId:'one' })
  display.visible = false
  expect(pickBlocker([display], ray, null)).toBeNull()
})

it('reuses existing mesh buffers when adding or highlighting plugs and disposes replaced/removed geometry', () => {
  const root = new Group(), first = blocker(), second = { ...first,id:'two',regionId:'two',baseCenter:[20,0,0] as [number,number,number] }
  syncBlockerDisplays(root,[first],null,null)
  const original = root.children[0], solid = original.children[0] as Mesh
  const dispose = vi.spyOn(solid.geometry,'dispose')
  syncBlockerDisplays(root,[first,second],{groupId:first.groupId,regionId:first.regionId},null)
  expect(root.children[0]).toBe(original)
  expect((original.children[0] as Mesh).geometry).toBe(solid.geometry)
  expect(dispose).not.toHaveBeenCalled()
  syncBlockerDisplays(root,[{ ...first,widthMm:18 },second],null,null)
  expect(dispose).toHaveBeenCalledOnce()
  expect(root.children.some((child) => child === original)).toBe(false)
  syncBlockerDisplays(root,[],null,null)
  expect(root.children).toHaveLength(0)
})

it('reuses section caps for selection changes and rebuilds them only when the cut moves', () => {
  const displays = new Group(), caps = new Group(), region = blocker(), plane = new Plane(new Vector3(0,0,-1),0)
  syncBlockerDisplays(displays,[region],null,plane)
  syncBlockerSectionCaps(caps,displays,plane)
  const cap = caps.children[0] as Mesh<import('three').BufferGeometry,MeshBasicMaterial>
  const dispose = vi.spyOn(cap.geometry,'dispose')
  syncBlockerDisplays(displays,[region],{groupId:region.groupId,regionId:region.regionId},plane)
  syncBlockerSectionCaps(caps,displays,plane)
  expect(caps.children[0]).toBe(cap)
  expect(cap.material.color.getHex()).toBe(0xff7a00)
  expect(dispose).not.toHaveBeenCalled()
  plane.constant = -1
  syncBlockerSectionCaps(caps,displays,plane)
  expect(caps.children[0]).not.toBe(cap)
  expect(dispose).toHaveBeenCalledOnce()
  syncBlockerSectionCaps(caps,displays,null)
  expect(caps.children).toHaveLength(0)
})

it('clips both the solid and outline, picks the kept section cap, and ignores removed surfaces', () => {
  const plane = new Plane(new Vector3(0,0,-1), 0), display = createBlockerDisplay(blocker(), null, plane)
  const solid = display.children[0] as Mesh<import('three').BufferGeometry, MeshBasicMaterial>
  const cap = createBlockerSectionCap(solid, plane)!
  expect(cap).not.toBeNull()
  expect(new Box3().setFromObject(cap).max.z).toBeCloseTo(0, 6)
  expect(cap.userData.blockerSelection).toEqual({ groupId:'holes', regionId:'one' })
  const caps = new Group(); caps.add(cap); caps.updateMatrixWorld(true)
  const ray = new Raycaster(new Vector3(0,0,10), new Vector3(0,0,-1))
  expect(pickBlocker([display,caps], ray, plane)).toEqual({ groupId:'holes', regionId:'one' })
  plane.constant = -10
  expect(createBlockerSectionCap(solid, plane)).toBeNull()
  expect(pickBlocker([display], ray, plane)).toBeNull()
  setObjectClippingPlane(display, null)
  expect(solid.material.clippingPlanes).toBeNull()
})
