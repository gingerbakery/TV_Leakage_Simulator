import { describe, expect, it } from 'vitest'

import type { SceneComponent, ScenePayload } from '@/api'
import { createSceneFixture } from '@/test/scene-fixture'

import { matchSetupComponents } from './copy-analysis-setup'

function renamedDuplicateComponents(scene: ScenePayload): ScenePayload {
  const copy = structuredClone(scene)
  copy.components = copy.components.map((component) => ({
    ...component,
    component_name: 'NX Solid Body',
    object_name: 'NX Solid Body',
  }))
  copy.objects = copy.components
  return copy
}

function swapComponentIds(scene: ScenePayload): ScenePayload {
  const copy = structuredClone(scene)
  const ids = copy.components.map((component) => component.component_id).reverse()
  copy.components = copy.components.map((component, index) => ({
    ...component,
    component_id: ids[index],
  }))
  copy.objects = copy.components
  return copy
}

function changeGeometry(component: SceneComponent, amount: number): SceneComponent {
  return {
    ...component,
    bbox_max: [
      component.bbox_max[0] + amount,
      component.bbox_max[1],
      component.bbox_max[2],
    ],
    area_mm2: component.area_mm2 * (1 + amount / 10),
    face_count: component.face_count + Math.round(amount),
  }
}

describe('matchSetupComponents', () => {
  it('normalizes common STEP name separators', () => {
    const source = createSceneFixture()
    source.components[0].component_name = 'Frame_Middle-FMB'
    source.components[0].object_name = 'Frame_Middle-FMB'
    const target = structuredClone(source)
    target.components[0].component_name = 'Frame Middle FMB'
    target.components[0].object_name = 'Frame Middle FMB'

    const result = matchSetupComponents(source, target)

    expect(result.componentIdMap[1]).toBe(1)
    expect(result.breakdown.name).toBe(2)
  })

  it('matches duplicated generic names by component geometry when IDs reorder', () => {
    const source = renamedDuplicateComponents(createSceneFixture())
    const target = swapComponentIds(renamedDuplicateComponents(source))

    const result = matchSetupComponents(source, target)

    expect(result.componentIdMap).toEqual({ 1: 2, 2: 1 })
    expect(result.breakdown.geometry).toBe(2)
    expect(result.unmatched).toBe(0)
  })

  it('uses stable IDs for renamed components whose geometry changed', () => {
    const source = renamedDuplicateComponents(createSceneFixture())
    const target = renamedDuplicateComponents(createSceneFixture())
    target.components = target.components.map((component, index) => ({
      ...changeGeometry(component, 4 + index),
      component_name: `Changed Part ${index + 1}`,
      object_name: `Changed Part ${index + 1}`,
    }))
    target.objects = target.components

    const result = matchSetupComponents(source, target)

    expect(result.componentIdMap).toEqual({ 1: 1, 2: 2 })
    expect(result.breakdown.componentId).toBe(2)
    expect(result.unmatched).toBe(0)
  })
})
