import type { RayHit, Vec3 } from '@/api'
import type {
  RayPathDisplayFilter,
  RayPathDisplayFilters,
} from '@/stores'

export type RayPathSegment = [Vec3, Vec3]

export interface RayPathStyle {
  color: number
  label: string
  opacity: number
}

export const rayPathStyles: Record<
  RayPathDisplayFilter,
  RayPathStyle
> = {
  receiver_direct: {
    color: 0x4ade80,
    label: 'Receiver 도달 · Direct',
    opacity: 1,
  },
  receiver_reflected: {
    color: 0xfacc15,
    label: 'Receiver 도달 · 반사광',
    opacity: 1,
  },
  direct: {
    color: 0x60a5fa,
    label: 'Direct / 최초 진행',
    opacity: 0.62,
  },
  specular: {
    color: 0xfb923c,
    label: 'Specular',
    opacity: 0.76,
  },
  lambertian: {
    color: 0xc084fc,
    label: 'Lambertian',
    opacity: 0.76,
  },
  gaussian: {
    color: 0x22d3ee,
    label: 'Gaussian',
    opacity: 0.76,
  },
}

export const rayPathFilterOrder: RayPathDisplayFilter[] = [
  'receiver_direct',
  'receiver_reflected',
  'direct',
  'specular',
  'lambertian',
  'gaussian',
]

export interface RayPathVisualization {
  groups: Record<RayPathDisplayFilter, RayPathSegment[]>
  totalPathCount: number
  visiblePathCount: number
}

function validPath(path: RayHit[]): boolean {
  return (
    path.length >= 2 &&
    Array.isArray(path[0]?.point) &&
    Array.isArray(path[path.length - 1]?.point)
  )
}

export function rayPathReachesReceiver(path: RayHit[]): boolean {
  return (
    validPath(path) &&
    path[path.length - 1]?.event_type === 'receiver'
  )
}

export function receiverPathFilter(
  path: RayHit[],
): 'receiver_direct' | 'receiver_reflected' | null {
  if (!rayPathReachesReceiver(path)) return null
  return path.some((event) => event.event_type === 'surface')
    ? 'receiver_reflected'
    : 'receiver_direct'
}

function reflectedFilter(event: RayHit | undefined) {
  const kind = event?.ray_kind
  return kind === 'specular' ||
    kind === 'lambertian' ||
    kind === 'gaussian'
    ? kind
    : null
}

export function isRayPathVisible(
  path: RayHit[],
  filters: RayPathDisplayFilters,
): boolean {
  if (!validPath(path)) return false
  const receiverFilter = receiverPathFilter(path)
  if (receiverFilter) return filters[receiverFilter]
  if (filters.direct) return true

  for (let index = 2; index < path.length; index += 1) {
    const filter =
      reflectedFilter(path[index]) ??
      reflectedFilter(path[index - 1])
    if (filter && filters[filter]) return true
  }
  return false
}

export function buildRayPathVisualization(
  paths: RayHit[][],
  filters: RayPathDisplayFilters,
  maxRenderedPaths = 500,
): RayPathVisualization {
  const groups: Record<RayPathDisplayFilter, RayPathSegment[]> = {
    receiver_direct: [],
    receiver_reflected: [],
    direct: [],
    specular: [],
    lambertian: [],
    gaussian: [],
  }
  const visiblePathCount = paths.reduce(
    (count, path) => count + Number(isRayPathVisible(path, filters)),
    0,
  )

  for (const path of paths.slice(0, maxRenderedPaths)) {
    if (!validPath(path)) continue
    const receiverFilter = receiverPathFilter(path)
    if (receiverFilter) {
      if (!filters[receiverFilter]) continue
      for (let index = 1; index < path.length; index += 1) {
        groups[receiverFilter].push([
          path[index - 1].point,
          path[index].point,
        ])
      }
      continue
    }

    for (let index = 1; index < path.length; index += 1) {
      if (index === 1) {
        if (filters.direct) {
          groups.direct.push([
            path[index - 1].point,
            path[index].point,
          ])
        }
        continue
      }
      const filter =
        reflectedFilter(path[index]) ??
        reflectedFilter(path[index - 1])
      if (filter && filters[filter]) {
        groups[filter].push([
          path[index - 1].point,
          path[index].point,
        ])
      }
    }
  }

  return {
    groups,
    totalPathCount: paths.length,
    visiblePathCount,
  }
}
