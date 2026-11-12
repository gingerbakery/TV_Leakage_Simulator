import { describe, expect, it } from 'vitest'
import { defaultObserverSettings as newDefaults, buildLeakPreviewRequest } from './leak-preview-model'
import { planObserverSampling } from './observer-sampling'
import { createSceneFixture } from '@/test/scene-fixture'
import { createDefaultLeakPreviewProjectState, leakPreviewStore } from './leak-preview-store'

// Pin legacy path sampling regression fixtures independently of new defaults.
const defaultObserverSettings = { ...newDefaults, measurement: 'paths' as const, refine_angles: false }

describe('automatic observer sampling', () => {
  it('keeps full-detail spacing and view angles fixed at 32/64 radiance repeats', () => {
    const projection={minimum:[0,0,0] as [number,number,number],maximum:[10,6,1] as [number,number,number],direction:'neg_z' as const}
    const spec={...newDefaults,enabled:true,measurement:'radiance' as const,search_mode:'full_detail' as const,
      range_deg:0,horizontal_deg:0,vertical_deg:0,spacing_mm:1}
    const base=planObserverSampling({...spec,samples_per_cell:8},1,projection)
    for (const repeats of [32,64]) {
      const plan=planObserverSampling({...spec,samples_per_cell:repeats},1,projection)
      expect(plan.error).toBeNull()
      expect(plan.samples_per_cell).toBe(repeats)
      expect(plan.plannedCount).toBe(base.plannedCount*repeats/8)
      const request=buildLeakPreviewRequest({scene:createSceneFixture(),sourceFaceIds:[0],quality:'fast',computeBackend:'cpu',
        materialAssignments:[],transformRules:[],excludedComponentIds:[],deletedComponentIds:[],detectionMode:'escape_directions',observer:{...spec,samples_per_cell:repeats}})
      expect(request.observer_probe).toMatchObject({samples_per_cell:repeats,spacing_mm:1,horizontal_deg:0,vertical_deg:0})
    }
  })
  it('doubles full-detail radiance samples at 16 repeats without coarsening space or angles and fails on budget overflow', () => {
    const projection={minimum:[0,0,0] as [number,number,number],maximum:[100,60,10] as [number,number,number],direction:'neg_z' as const}
    const spec={...newDefaults,enabled:true,search_mode:'full_detail' as const,range_deg:0,horizontal_deg:0,vertical_deg:0,spacing_mm:1}
    const eight=planObserverSampling({...spec,samples_per_cell:8},1,projection)
    const sixteen=planObserverSampling({...spec,samples_per_cell:16},1,projection)
    expect(sixteen.error).toBeNull()
    expect(sixteen.samples_per_cell).toBe(16)
    expect(sixteen.plannedCount).toBe(eight.plannedCount*2)
    expect(sixteen.views).toBe(eight.views)
    const request=buildLeakPreviewRequest({scene:createSceneFixture(),sourceFaceIds:[0],quality:'fast',computeBackend:'cpu',materialAssignments:[],transformRules:[],excludedComponentIds:[],deletedComponentIds:[],detectionMode:'escape_directions',observer:{...spec,samples_per_cell:16}})
    expect(request.observer_probe).toMatchObject({samples_per_cell:16,spacing_mm:1,horizontal_deg:0,vertical_deg:0})
    const tooLarge=planObserverSampling({...spec,samples_per_cell:16,spacing_mm:.1},1,{...projection,maximum:[10000,10000,100]})
    expect(tooLarge.error).toContain('한도')
  })
  it('defaults to radiance and explores zero-hit angular midpoints with an explicit budget', () => {
    expect(newDefaults).toMatchObject({ measurement: 'radiance', refine_angles: true, min_luminance_nit: .01 })
    const plan = planObserverSampling({ ...newDefaults, coverage: 'hemisphere', step_deg: 15 })
    expect(plan).toMatchObject({ views: 577, error: null })
    expect(plan.plannedCount).toBeLessThanOrEqual(500_000)
    const projection = { minimum: [0,0,0] as [number,number,number], maximum: [100,60,10] as [number,number,number], direction: 'neg_z' as const }
    const full = planObserverSampling({ ...newDefaults, search_mode: 'full_detail', range_deg: 0,
      horizontal_deg: 0, vertical_deg: 0, spacing_mm: 1 }, 1, projection)
    expect(full).toMatchObject({ samples_per_cell: 8, plannedCount: 48_000, error: null })
  })
  it('covers the complete hemisphere including the rim and unique poles', () => {
    const observer = { ...defaultObserverSettings, coverage: 'hemisphere' as const, step_deg: 30 }
    const plan = planObserverSampling(observer)
    expect(plan).toMatchObject({ views: 37, error: null })
    expect(plan.plannedCount).toBeLessThanOrEqual(500_000)
    expect(planObserverSampling({ ...observer, step_deg: 15 })).toMatchObject({ views: 145, error: null })
    expect(planObserverSampling({ ...observer, step_deg: 1 }).error).not.toBeNull()
    const request = buildLeakPreviewRequest({ scene: createSceneFixture(), sourceFaceIds: [0], quality: 'fast', computeBackend: 'cpu',
      materialAssignments: [], transformRules: [], excludedComponentIds: [], deletedComponentIds: [], detectionMode: 'escape_directions', observer: { ...observer, enabled: true } })
    expect(request.observer_probe).toMatchObject({ coverage: 'hemisphere', step_deg: 30 })
  })

  it('plans fixed millimeter spacing without the Preview budget reducing resolution', () => {
    const observer = { ...defaultObserverSettings, enabled: true, search_mode: 'full_detail' as const,
      spacing_mm: 1, horizontal_deg: 0, vertical_deg: 0, range_deg: 0 }
    const projection = { minimum: [0,0,0] as [number,number,number], maximum: [1000,600,10] as [number,number,number], direction: 'neg_z' as const }
    const plan = planObserverSampling(observer, 1, projection)
    expect(plan).toMatchObject({ fullDetail: true, plannedCount: 2_400_000, samples_per_cell: 2, error: null })
    expect(planObserverSampling({ ...observer, spacing_mm: 2 }, 1, projection).plannedCount).toBe(600_000)
    expect(planObserverSampling({ ...observer, spacing_mm: .5 }, 1, projection).plannedCount).toBe(9_600_000)
    expect(planObserverSampling({ ...observer, range_deg: 10, step_deg: 2, spacing_mm: .1 }, 1, projection).error).toContain('100,000,000')
    expect(planObserverSampling(observer).error).toContain('모델 범위')
  })

  it('uses precision CAD and actual material assignments for full detail without changing the requested spacing or depth', () => {
    const request = buildLeakPreviewRequest({ scene: createSceneFixture(), sourceFaceIds: [0], quality: 'fast',
      computeBackend: 'cpu', maxDepth: 50, materialAssignments: [], transformRules: [],
      excludedComponentIds: [], deletedComponentIds: [], detectionMode: 'escape_directions',
      observer: { ...defaultObserverSettings, enabled: true, search_mode: 'full_detail', spacing_mm: .5, range_deg: 0 } })
    expect(request.geometry_mode).toBe('precision')
    expect(request.observer_probe).toMatchObject({ search_mode: 'full_detail', spacing_mm: .5, samples_per_cell: 2 })
    expect(request.config).toMatchObject({ max_depth: 50, primary_sampling_strategy: 'source', bounce_sampling_strategy: 'source' })
    expect(request.analysis_roi).toBeUndefined()
    expect(request.roi_faces).toBeUndefined()
    expect(request.optical_profiles.some((profile) => profile.profile_id === 'default')).toBe(false)
  })

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

  it('does not silently reinterpret an old saved observer as a radiance calculation', () => {
    const old = { ...newDefaults }
    delete old.measurement; delete old.refine_angles; delete old.min_luminance_nit
    leakPreviewStore.getState().restoreProjectState({ ...createDefaultLeakPreviewProjectState(), observer: old })
    expect(leakPreviewStore.getState().observer).toMatchObject({ measurement: 'paths', refine_angles: false })
    leakPreviewStore.getState().clear()
  })
})
