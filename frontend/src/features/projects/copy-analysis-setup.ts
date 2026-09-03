import type {
  SceneComponentMatchMetadata,
  ScenePayload,
  Vec3,
} from '@/api'

interface ComponentMatchScene {
  components: readonly SceneComponentMatchMetadata[]
}

export function sceneComponentMatchMetadata(
  scene: ScenePayload,
): SceneComponentMatchMetadata[] {
  return scene.components.map((component) => ({
    component_id: component.component_id,
    component_name: component.component_name,
    object_name: component.object_name,
    face_count: component.face_count,
    area_mm2: component.area_mm2,
    bbox_min: [...component.bbox_min],
    bbox_max: [...component.bbox_max],
  }))
}

export interface ComponentMatchBreakdown {
  name: number
  geometry: number
  componentId: number
  orderedFallback: number
}

export interface ComponentMatchResult {
  componentIdMap: Record<number, number>
  matched: number
  unmatched: number
  breakdown: ComponentMatchBreakdown
}

function normalizedComponentName(value: string): string {
  return value
    .normalize('NFKC')
    .trim()
    .replace(/[_./\\-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .toLocaleLowerCase()
}

function uniqueComponentMatch(
  candidates: SceneComponentMatchMetadata[],
  usedTargetIds: Set<number>,
): SceneComponentMatchMetadata | null {
  const available = candidates.filter(
    (candidate) => !usedTargetIds.has(candidate.component_id),
  )
  return available.length === 1 ? available[0] : null
}

function componentCenter(component: SceneComponentMatchMetadata): Vec3 {
  return [
    (component.bbox_min[0] + component.bbox_max[0]) / 2,
    (component.bbox_min[1] + component.bbox_max[1]) / 2,
    (component.bbox_min[2] + component.bbox_max[2]) / 2,
  ]
}

function componentSize(component: SceneComponentMatchMetadata): Vec3 {
  return [
    Math.abs(component.bbox_max[0] - component.bbox_min[0]),
    Math.abs(component.bbox_max[1] - component.bbox_min[1]),
    Math.abs(component.bbox_max[2] - component.bbox_min[2]),
  ]
}

function relativeDifference(left: number, right: number): number {
  return Math.abs(left - right) / Math.max(Math.abs(left), Math.abs(right), 1)
}

function sceneDiagonal(scene: ComponentMatchScene): number {
  if (scene.components.length === 0) return 1
  const minimum: Vec3 = [Infinity, Infinity, Infinity]
  const maximum: Vec3 = [-Infinity, -Infinity, -Infinity]
  for (const component of scene.components) {
    for (let axis = 0; axis < 3; axis += 1) {
      minimum[axis] = Math.min(minimum[axis], component.bbox_min[axis])
      maximum[axis] = Math.max(maximum[axis], component.bbox_max[axis])
    }
  }
  return Math.max(
    Math.hypot(
      maximum[0] - minimum[0],
      maximum[1] - minimum[1],
      maximum[2] - minimum[2],
    ),
    1,
  )
}

/** Lower is more likely to be the same physical component. */
function componentGeometryDistance(
  source: SceneComponentMatchMetadata,
  target: SceneComponentMatchMetadata,
  positionScale: number,
): number {
  const sourceSize = componentSize(source)
  const targetSize = componentSize(target)
  const sizeDifference =
    sourceSize.reduce(
      (sum, value, axis) =>
        sum + relativeDifference(value, targetSize[axis]),
      0,
    ) / 3
  const sourceCenter = componentCenter(source)
  const targetCenter = componentCenter(target)
  const centerDifference =
    Math.hypot(
      sourceCenter[0] - targetCenter[0],
      sourceCenter[1] - targetCenter[1],
      sourceCenter[2] - targetCenter[2],
    ) / positionScale
  const areaDifference = relativeDifference(
    source.area_mm2,
    target.area_mm2,
  )
  const faceDifference = relativeDifference(
    source.face_count,
    target.face_count,
  )
  return (
    sizeDifference * 0.45 +
    centerDifference * 0.25 +
    areaDifference * 0.2 +
    faceDifference * 0.1
  )
}

export function matchSetupComponents(
  source: ComponentMatchScene,
  target: ComponentMatchScene,
): ComponentMatchResult {
  const byPair = new Map<string, SceneComponentMatchMetadata[]>()
  const byComponentName = new Map<string, SceneComponentMatchMetadata[]>()
  const byObjectName = new Map<string, SceneComponentMatchMetadata[]>()
  const add = (
    index: Map<string, SceneComponentMatchMetadata[]>,
    key: string,
    component: SceneComponentMatchMetadata,
  ) => {
    if (!key) return
    index.set(key, [...(index.get(key) ?? []), component])
  }
  for (const component of target.components) {
    const componentName = normalizedComponentName(component.component_name)
    const objectName = normalizedComponentName(component.object_name)
    add(byPair, `${componentName}\u0000${objectName}`, component)
    add(byComponentName, componentName, component)
    add(byObjectName, objectName, component)
  }

  const componentIdMap: Record<number, number> = {}
  const usedTargetIds = new Set<number>()
  const breakdown: ComponentMatchBreakdown = {
    name: 0,
    geometry: 0,
    componentId: 0,
    orderedFallback: 0,
  }
  const assign = (
    sourceComponent: SceneComponentMatchMetadata,
    targetComponent: SceneComponentMatchMetadata,
    strategy: keyof ComponentMatchBreakdown,
  ) => {
    componentIdMap[sourceComponent.component_id] = targetComponent.component_id
    usedTargetIds.add(targetComponent.component_id)
    breakdown[strategy] += 1
  }

  // 1) Authored STEP/NX names are the strongest identity signal. Normalize
  // underscores and punctuation so "Frame_Middle" and "Frame-Middle" match.
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
    if (matched) assign(component, matched, 'name')
  }

  // 2) Generic or duplicated STEP names cannot identify a part. Match
  // unmistakably similar bounding boxes/area/position before relying on IDs.
  const positionScale = Math.max(sceneDiagonal(source), sceneDiagonal(target))
  const geometryCandidates = source.components
    .filter((component) => componentIdMap[component.component_id] === undefined)
    .flatMap((sourceComponent) =>
      target.components
        .filter((targetComponent) =>
          !usedTargetIds.has(targetComponent.component_id),
        )
        .map((targetComponent) => ({
          sourceComponent,
          targetComponent,
          distance: componentGeometryDistance(
            sourceComponent,
            targetComponent,
            positionScale,
          ),
        })),
    )
    .filter((candidate) => candidate.distance <= 0.12)
    .sort((left, right) => left.distance - right.distance)
  for (const candidate of geometryCandidates) {
    if (
      componentIdMap[candidate.sourceComponent.component_id] !== undefined ||
      usedTargetIds.has(candidate.targetComponent.component_id)
    ) {
      continue
    }
    assign(candidate.sourceComponent, candidate.targetComponent, 'geometry')
  }

  // 3) The optimized importer intentionally keeps Component IDs stable when
  // the assembly order is stable, even if a CAD author renamed the bodies.
  for (const component of source.components) {
    if (componentIdMap[component.component_id] !== undefined) continue
    const sameId = target.components.find(
      (candidate) =>
        candidate.component_id === component.component_id &&
        !usedTargetIds.has(candidate.component_id),
    )
    if (sameId) assign(component, sameId, 'componentId')
  }

  // 4) When both assemblies contain the same number of components, pair the
  // remaining one-to-one slots deterministically. Earlier high-confidence
  // matches have already removed renamed/reordered parts, so this mainly
  // covers generic duplicate names whose geometry intentionally changed.
  if (source.components.length === target.components.length) {
    const remainingSources = source.components
      .filter((component) => componentIdMap[component.component_id] === undefined)
      .sort((left, right) => left.component_id - right.component_id)
    const remainingTargets = target.components
      .filter((component) => !usedTargetIds.has(component.component_id))
      .sort((left, right) => left.component_id - right.component_id)
    if (remainingSources.length === remainingTargets.length) {
      remainingSources.forEach((component, index) => {
        assign(component, remainingTargets[index], 'orderedFallback')
      })
    }
  }

  const matched = Object.keys(componentIdMap).length
  return {
    componentIdMap,
    matched,
    unmatched: source.components.length - matched,
    breakdown,
  }
}
