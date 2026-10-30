import { describe, expect, it } from 'vitest'
import { defaultObserverSettings, buildLeakPreviewRequest } from './leak-preview-model'
import { planObserverSampling } from './observer-sampling'
import { createSceneFixture } from '@/test/scene-fixture'
import { createDefaultLeakPreviewProjectState, leakPreviewStore } from './leak-preview-store'

describe('automatic observer sampling', () => {
  it('keeps the normal default and reduces sampling to fit a dense valid angle sweep', () => {
    expect(planObserverSampling(defaultObserverSettings)).toMatchObject({ views: 9, resolution: 64, samples_per_cell: 2, error: null })
    const observer = { ...defaultObserverSettings, horizontal_deg: 0, vertical_deg: 0, range_deg: 2, step_deg: .5 }
    const plan = planObserverSampling(observer)
    expect(plan).toMatchObject({ views: 81, resolution: 43, samples_per_cell: 1, error: null })
    expect(plan.plannedCount).toBeLessThanOrEqual(300_000)
    const request = buildLeakPreviewRequest({ scene: createSceneFixture(), sourceFaceIds: [0], quality: 'fast', computeBackend: 'cpu',
      materialAssignments: [], transformRules: [], excludedComponentIds: [], deletedComponentIds: [], detectionMode: 'escape_directions', observer: { ...observer, enabled: true } })
    expect(request.observer_probe).toMatchObject({ range_deg: 2, step_deg: .5, resolution: 43, samples_per_cell: 1 })
    expect(request.observer_probe).not.toHaveProperty('sampling')
    expect(observer.resolution).toBe(64)
  })

  it('fits each preset to the hard limit without changing the requested angle sweep', () => {
    for (const sampling of ['fast', 'balanced', 'fine'] as const) {
      for (const [range_deg, step_deg] of [[0, .1], [.5, .1], [2, .5], [4, 2]]) {
        const plan = planObserverSampling({ ...defaultObserverSettings, horizontal_deg: 0, vertical_deg: 0, sampling, range_deg, step_deg })
        expect(plan.error).toBeNull()
        expect(plan.plannedCount).toBeLessThanOrEqual(500_000)
        expect(plan.resolution).toBeGreaterThanOrEqual(8)
        expect(plan.samples_per_cell).toBeGreaterThanOrEqual(1)
      }
    }
    const input = { ...defaultObserverSettings, horizontal_deg: 0, range_deg: 2, step_deg: .5 }
    const resolutions = (['fast', 'balanced', 'fine'] as const).map((sampling) => planObserverSampling({ ...input, sampling }).resolution)
    expect(resolutions[0]).toBeLessThan(resolutions[1])
    expect(resolutions[1]).toBeLessThan(resolutions[2])
  })

  it('reports excessive angles and offers a valid explicit interval adjustment', () => {
    for (const range_deg of [1, 2, 4, 10]) {
      const input = { ...defaultObserverSettings, horizontal_deg: 0, vertical_deg: 0, range_deg, step_deg: .1 }
      const plan = planObserverSampling(input)
      if (plan.views > 121) expect(plan.error).toContain('각도 조합')
      const repaired = planObserverSampling({ ...input, step_deg: plan.suggestedStep })
      expect(repaired.error).toBeNull()
      expect(repaired.views).toBeLessThanOrEqual(121)
    }
  })

  it('preserves manual values and catches an impossible source-count budget', () => {
    const manual = planObserverSampling({ ...defaultObserverSettings, sampling: 'manual', horizontal_deg: 0, vertical_deg: 0, range_deg: 2, step_deg: .5 })
    expect(manual).toMatchObject({ resolution: 64, samples_per_cell: 2 })
    expect(manual.error).toContain('한도')
    expect(planObserverSampling({ ...defaultObserverSettings, range_deg: 1, step_deg: .1, horizontal_deg: 0 }, 10000).error).not.toBeNull()
  })

  it('restores old projects in manual mode and preserves new automatic choices', () => {
    const project = createDefaultLeakPreviewProjectState()
    const observer = { ...defaultObserverSettings, resolution: 80, samples_per_cell: 3 }
    delete observer.sampling
    leakPreviewStore.getState().restoreProjectState({ ...project, observer })
    expect(leakPreviewStore.getState().observer).toMatchObject({ sampling: 'manual', resolution: 80, samples_per_cell: 3 })
    leakPreviewStore.getState().restoreProjectState({ ...project, observer: { ...observer, sampling: 'fine' } })
    expect(leakPreviewStore.getState().observer.sampling).toBe('fine')
    leakPreviewStore.getState().clear()
  })
})
