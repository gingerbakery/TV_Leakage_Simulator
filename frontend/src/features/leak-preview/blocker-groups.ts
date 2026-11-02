import type { Vec3 } from '@/api'
import type { RoiClipBox } from '@/stores'
import type { LeakPreviewBlocker, LeakPreviewIgnoreArea } from './leak-preview-model'

export interface ExpandedBlocker extends LeakPreviewBlocker { groupId: string; regionId: string | null }
export const HOLE_PLUG_DEPTH_MM = 5

export function expandBlockerGroups(groups: LeakPreviewBlocker[]): ExpandedBlocker[] {
  return groups.flatMap<ExpandedBlocker>((group) => group.regions === undefined ? [{ ...group, groupId: group.id, regionId: null }] : group.regions.map((region) => ({
    ...region, id: `${group.id}:${region.id}`, label: group.label,
    groupId: group.id, regionId: region.id,
    enabled: group.enabled && region.enabled, visible: group.visible !== false && region.visible !== false,
  })))
}

export function nextBlockName(blockers: LeakPreviewBlocker[]): string {
  const maximum = Math.max(blockers.length, 0, ...blockers.map((blocker) =>
    blocker.sequence ?? Number(/^Block (\d+)$/.exec(blocker.label)?.[1] ?? 0)))
  return `Block ${maximum + 1}`
}

export function createBlockGroup(label: string, shape: 'box' | 'ellipse', center: Vec3 = [0, 0, 0]): LeakPreviewBlocker {
  return { id: `block-${crypto.randomUUID()}`, label, sequence: Number(/^Block (\d+)$/.exec(label)?.[1]) || undefined, shape, regions: [], enabled: true, baseCenter: center,
    uAxis: [1, 0, 0], vAxis: [0, 1, 0], normal: [0, 0, 1], widthMm: 1, heightMm: 1,
    offsetMm: shape === 'ellipse' ? -HOLE_PLUG_DEPTH_MM / 2 : 0, depthMm: shape === 'ellipse' ? HOLE_PLUG_DEPTH_MM : 1.5, reverse: false }
}

// A legacy Allowed Area becomes a real solid with the same spatial bounds.
export function migrateAllowedAreas(blockers: LeakPreviewBlocker[], areas: LeakPreviewIgnoreArea[], visible = true): LeakPreviewBlocker[] {
  const result = [...blockers]
  for (const area of areas) {
    const group = createBlockGroup(nextBlockName(result), 'box')
    group.id = `allowed-block:${area.id}`
    group.enabled = area.enabled
    group.visible = visible && area.visible !== false
    group.label = /^Allowed Area/.test(area.label) ? group.label : area.label
    group.regions = area.regions.map((box, index) => regionFromBox(box, `${group.id}:${index}`))
    result.push(group)
  }
  return result
}

function regionFromBox(box: RoiClipBox, id: string): LeakPreviewBlocker {
  const group = createBlockGroup('', 'box')
  const z0 = box.zMin ?? 0, z1 = box.zMax ?? z0
  return { ...group, regions: undefined, id,
    baseCenter: [(box.xMin + box.xMax)/2, (box.yMin + box.yMax)/2, z0],
    widthMm: Math.max(.1, box.xMax - box.xMin), heightMm: Math.max(.1, box.yMax - box.yMin),
    depthMm: Math.max(.1, z1-z0) }
}
