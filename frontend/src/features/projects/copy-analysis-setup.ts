import type { SceneComponent, ScenePayload } from '@/api'

export interface ComponentMatchResult {
  componentIdMap: Record<number, number>
  matched: number
  unmatched: number
}

function normalizedComponentName(value: string): string {
  return value.trim().replace(/\s+/g, ' ').toLocaleLowerCase()
}

function uniqueComponentMatch(
  candidates: SceneComponent[],
  usedTargetIds: Set<number>,
): SceneComponent | null {
  const available = candidates.filter(
    (candidate) => !usedTargetIds.has(candidate.component_id),
  )
  return available.length === 1 ? available[0] : null
}

export function matchSetupComponents(
  source: ScenePayload,
  target: ScenePayload,
): ComponentMatchResult {
  const byPair = new Map<string, SceneComponent[]>()
  const byComponentName = new Map<string, SceneComponent[]>()
  const byObjectName = new Map<string, SceneComponent[]>()
  const add = (
    index: Map<string, SceneComponent[]>,
    key: string,
    component: SceneComponent,
  ) => index.set(key, [...(index.get(key) ?? []), component])
  for (const component of target.components) {
    const componentName = normalizedComponentName(component.component_name)
    const objectName = normalizedComponentName(component.object_name)
    add(byPair, `${componentName}\u0000${objectName}`, component)
    add(byComponentName, componentName, component)
    add(byObjectName, objectName, component)
  }

  const componentIdMap: Record<number, number> = {}
  const usedTargetIds = new Set<number>()
  let unmatched = 0
  for (const component of source.components) {
    const componentName = normalizedComponentName(component.component_name)
    const objectName = normalizedComponentName(component.object_name)
    const matched =
      uniqueComponentMatch(
        byPair.get(`${componentName}\u0000${objectName}`) ?? [],
        usedTargetIds,
      ) ??
      uniqueComponentMatch(
        byComponentName.get(componentName) ?? [],
        usedTargetIds,
      ) ??
      uniqueComponentMatch(byObjectName.get(objectName) ?? [], usedTargetIds)
    if (!matched) {
      unmatched += 1
      continue
    }
    componentIdMap[component.component_id] = matched.component_id
    usedTargetIds.add(matched.component_id)
  }
  return {
    componentIdMap,
    matched: Object.keys(componentIdMap).length,
    unmatched,
  }
}
