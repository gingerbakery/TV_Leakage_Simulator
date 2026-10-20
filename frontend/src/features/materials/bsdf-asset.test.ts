import { describe, expect, it, vi } from 'vitest'
import { createApiClient } from '@/api'
import { createBsdfFixture } from '@/test/bsdf-fixture'
import { createSceneFixture } from '@/test/scene-fixture'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { createWorkspaceStore, type MaterialAssignment } from '@/stores'
import { createBitsamProject, parseBitsamProject, serializeBitsamProject } from '@/features/projects/bitsam-project'
import { buildRayTraceRequest } from '@/features/raytracing/ray-tracing-model'
import { isBsdfAsset } from './bsdf-asset'

describe('portable BSDF foundation', () => {
  it.each(['cpu', 'gpu_cuda'] as const)('rebuilds the %s request only from restored project settings', (backend) => {
    const original = createWorkspaceStore()
    const actions = original.getState().actions
    actions.addCadCase({ path: 'source.step', displayName: 'source.step' })
    const asset = createBsdfFixture()
    asset.transport = { model: 'isotropic_no_shift', wavelengthNm: 550, aoiExtrapolation: 'clamp' }
    actions.addBsdfAsset(asset)
    actions.upsertMaterialAssignment({ assignmentId: 'surface', componentId: 1,
      targetType: 'faces', faceIds: [0, 1], baseMaterialId: 'pc_black',
      surfaceId: 'matte_black_resin', profileId: '', bsdfAssetId: asset.id, enabled: true })
    const result = createRayTraceResultFixture()
    result.emitters.forEach(actions.upsertEmitter)
    result.receivers.forEach(actions.upsertReceiver)
    actions.setRayTraceConfig({ ...original.getState().rayTraceConfig, compute_backend: backend,
      primary_sampling_strategy: 'source', bounce_sampling_strategy: 'source' })
    const scene = createSceneFixture()
    const requestFromStore = (store: ReturnType<typeof createWorkspaceStore>) => {
      const state = store.getState()
      return buildRayTraceRequest({ scene, projectName: 'restored', emitters: state.emitters,
        receivers: state.receivers, materialAssignments: state.materialAssignments,
        transformRules: state.transformRules, excludedComponentIds: state.excludedComponentIds,
        deletedComponentIds: state.deletedComponentIds, roiScopes: state.roiScopes,
        config: state.rayTraceConfig, bsdfAssets: state.bsdfAssets })
    }
    const before = requestFromStore(original)
    const project = parseBitsamProject(serializeBitsamProject(
      createBitsamProject(scene, original.getState(), new Date(), result),
    ))
    const restored = createWorkspaceStore()
    restored.getState().actions.addCadCase({ path: 'restored/original.step', displayName: 'source.step' })
    restored.getState().actions.restoreProjectState(project.workspace)
    expect(requestFromStore(restored)).toEqual(before)
    expect(project.analysis_result).toEqual(result)
    const receiver = restored.getState().receivers[0]
    restored.getState().actions.upsertReceiver({ ...receiver, width_mm: 10 })
    const changed = requestFromStore(restored)
    expect(changed.receivers[0].width_mm).toBe(10)
    expect(changed.optical_profiles).toEqual(before.optical_profiles)
    expect(changed.optical_assignments).toEqual(before.optical_assignments)
    expect(changed.bsdf_assets).toEqual(before.bsdf_assets)
    expect(changed.config.compute_backend).toBe(backend)
  })

  it('validates transport options and corrupt grids independently of the readiness flag', () => {
    const asset = createBsdfFixture()
    expect(isBsdfAsset(asset)).toBe(true)
    expect(isBsdfAsset({ ...asset, simulationReady: true })).toBe(true)
    expect(isBsdfAsset({ ...asset, transport: { model: 'shift_with_stretch' } })).toBe(false)
    const corrupt = structuredClone(asset)
    corrupt.data.grids[0].values[0].pop()
    expect(isBsdfAsset(corrupt)).toBe(false)
    const malicious = structuredClone(asset)
    malicious.files[0].name = '../master.txt'
    expect(isBsdfAsset(malicious)).toBe(false)
  })

  it('posts an exact multi-file bundle, validates server data and rejects unsupported files', async () => {
    const asset = createBsdfFixture()
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(asset), { headers: { 'Content-Type': 'application/json' } }))
    const api = createApiClient({ fetch: fetchMock })
    await expect(api.importBsdf(asset.files)).resolves.toEqual(asset)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/bsdf/import')
    expect(JSON.parse(String(init?.body))).toEqual({ files: asset.files })
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ ...asset, format: 'unknown' })))
    await expect(api.importBsdf(asset.files)).rejects.toThrow('응답 형식')
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: 'Unsupported native .bsdf' }), { status: 400 }))
    await expect(api.importBsdf(asset.files)).rejects.toThrow()
  })

  it('round trips sources and references through project save, case switching and setup copy', () => {
    const store = createWorkspaceStore()
    const actions = store.getState().actions
    actions.addCadCase({ path: 'first.step', displayName: 'first.step' })
    const first = store.getState().activeCadCaseId!
    const asset = createBsdfFixture()
    asset.transport = { model: 'isotropic_no_shift', wavelengthNm: 550, aoiExtrapolation: 'clamp' }
    actions.addBsdfAsset(asset)
    actions.addBsdfAsset(asset)
    const part: MaterialAssignment = { assignmentId: 'part', componentId: 1, targetType: 'part', faceIds: [],
      baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin', profileId: '', bsdfAssetId: asset.id, enabled: true }
    actions.upsertMaterialAssignment(part)
    actions.addCadCase({ path: 'second.step', displayName: 'second.step' })
    const second = store.getState().activeCadCaseId!
    actions.setActiveCadCase(first)
    expect(store.getState().bsdfAssets).toEqual([asset])
    actions.copyActiveSetupToCases([{ caseId: second, componentIdMap: { 1: 1 } }])
    actions.setActiveCadCase(second)
    expect(store.getState().bsdfAssets).toEqual([asset])
    expect(store.getState().materialAssignments[0].bsdfAssetId).toBe(asset.id)
    const project = createBitsamProject(createSceneFixture(), store.getState())
    const restored = parseBitsamProject(serializeBitsamProject(project))
    expect(restored.schema_version).toBe('bitsam-project.v1-bsdf1')
    expect(restored.workspace.bsdfAssets).toEqual([asset])
    const wronglyTagged = { ...restored, schema_version: 'bitsam-project.v1' }
    expect(() => parseBitsamProject(JSON.stringify(wronglyTagged))).toThrow('확장 버전')
    expect(JSON.parse(serializeBitsamProject({ ...project, schema_version: 'bitsam-project.v1' })).schema_version).toBe('bitsam-project.v1-bsdf1')
    const corrupted = structuredClone(project)
    corrupted.workspace.bsdfAssets![0].data.rt[0].reflectance = 2
    expect(() => parseBitsamProject(serializeBitsamProject(corrupted))).toThrow()
    const legacy = structuredClone(project)
    delete legacy.workspace.bsdfAssets
    expect(parseBitsamProject(serializeBitsamProject(legacy)).workspace.bsdfAssets).toBeUndefined()
  })

  it('blocks active BSDF references even when missing but allows disabled or unlinked assignments', () => {
    const store = createWorkspaceStore()
    const part: MaterialAssignment = { assignmentId: 'part', componentId: 1, targetType: 'part', faceIds: [],
      baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin', profileId: '', bsdfAssetId: 'missing-legacy-asset', enabled: true }
    const request = (assignments: MaterialAssignment[]) => buildRayTraceRequest({ scene: createSceneFixture(), projectName: 'BSDF test',
      emitters: [], receivers: [], materialAssignments: assignments, transformRules: [], excludedComponentIds: [], deletedComponentIds: [], roiScopes: [], config: store.getState().rayTraceConfig })
    expect(() => request([part])).toThrow('연결된 BSDF 파일이 없습니다')
    expect(request([{ ...part, enabled: false }]).optical_profiles).toHaveLength(0)
    expect(request([{ ...part, bsdfAssetId: '' }]).optical_profiles).toHaveLength(1)
  })

  it('sends only linked assets and requires explicit supported transport settings', () => {
    const store = createWorkspaceStore()
    const asset = createBsdfFixture()
    const part: MaterialAssignment = { assignmentId: 'part', componentId: 1, targetType: 'part', faceIds: [],
      baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin', profileId: '', bsdfAssetId: asset.id, enabled: true }
    const config = { ...store.getState().rayTraceConfig, compute_backend: 'cpu' as const,
      primary_sampling_strategy: 'source' as const, bounce_sampling_strategy: 'source' as const }
    const input = { scene: createSceneFixture(), projectName: 'BSDF', emitters: [], receivers: [], materialAssignments: [part],
      transformRules: [], excludedComponentIds: [], deletedComponentIds: [], roiScopes: [], config, bsdfAssets: [asset] }
    expect(() => buildRayTraceRequest(input)).toThrow('계산')
    asset.transport = { model: 'isotropic_no_shift', wavelengthNm: 550, aoiExtrapolation: 'clamp' }
    expect(buildRayTraceRequest(input).bsdf_assets).toEqual([asset])
    expect(buildRayTraceRequest({ ...input, config: { ...config, compute_backend: 'gpu_cuda' } }).bsdf_assets).toEqual([asset])
    expect(() => buildRayTraceRequest({ ...input, config: { ...config, bounce_sampling_strategy: 'receiver_mis' } })).toThrow('Source')
  })
})
