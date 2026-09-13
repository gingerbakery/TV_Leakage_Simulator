// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createWorkspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { createBitsamProject } from './bitsam-project'
import { createPortableProjectClient, isPortableProject, loadPortableProject, savePortableProject } from './portable-project'

function fixture() {
  const store = createWorkspaceStore()
  const cad = { path: 'original.step', displayName: 'original.step' }
  store.getState().actions.setActiveCad(cad)
  const scene = createSceneFixture()
  const result = createRayTraceResultFixture()
  result.emitters.forEach(store.getState().actions.upsertEmitter)
  result.receivers.forEach(store.getState().actions.upsertReceiver)
  const project = createBitsamProject(scene, store.getState(), new Date(), result)
  project.workspace.faceColorOverrides = [{ componentId: 1, faceIds: [0, 1], color: '#ef4444' }]
  return { scene, project, cad, result }
}

function mockClient() {
  return {
    exportProject: vi.fn(async () => ({ download_url: '/api/projects/download/token', filename: 'project.bitsam', size_bytes: 4, trace_cached: true })),
    importProject: vi.fn(async () => ({ project: fixture().project, cad: fixture().cad, trace_cached: true })),
    download: vi.fn(async () => new Response(new Uint8Array([80, 75, 3, 4]))),
  }
}

afterEach(() => {
  delete (window as Window & { showSaveFilePicker?: unknown }).showSaveFilePicker
  vi.restoreAllMocks()
})

describe('portable BITSAM packages', () => {
  it('detects v2 ZIP without parsing the whole file and retains JSON legacy detection', async () => {
    expect(await isPortableProject(new File([new Uint8Array([80, 75, 3, 4])], 'one.bitsam'))).toBe(true)
    expect(await isPortableProject(new File(['{"schema_version":"bitsam-project.v1"}'], 'old.bitsam'))).toBe(false)
    await expect(isPortableProject(new File(['{}'], 'wrong.txt'))).rejects.toThrow('.bitsam')
  })

  it('streams binary chunks into the selected file without creating a giant Blob', async () => {
    const write = vi.fn(async (_data: Uint8Array) => undefined)
    const close = vi.fn(async () => undefined)
    const client = mockClient()
    Object.assign(window, { showSaveFilePicker: vi.fn(function (this: unknown) {
      expect(this).toBe(window)
      return Promise.resolve({ createWritable: async () => ({ write, close }) })
    }) })
    const progress = vi.fn()
    const result = await savePortableProject(fixture().project, 'fresh-token', progress, client)
    expect(client.exportProject).toHaveBeenCalledWith('fresh-token', expect.objectContaining({ analysis_result: expect.any(Object) }))
    expect(Array.from(write.mock.calls[0][0])).toEqual([80, 75, 3, 4])
    expect(close).toHaveBeenCalledOnce()
    expect(progress).toHaveBeenLastCalledWith('파일 저장 중 · 100%')
    expect(result).toEqual({ cancelled: false, traceCached: true, downloaded: false })
  })

  it('does not generate a package when the save picker is cancelled', async () => {
    Object.assign(window, { showSaveFilePicker: vi.fn(async () => { throw new DOMException('cancel', 'AbortError') }) })
    const client = mockClient()
    expect(await savePortableProject(fixture().project, 'token', vi.fn(), client)).toEqual({ cancelled: true })
    expect(client.exportProject).not.toHaveBeenCalled()
  })

  it('uses a server download when native file writing is unavailable', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const client = mockClient()
    expect((await savePortableProject(fixture().project, 'token', vi.fn(), client)).downloaded).toBe(true)
    expect(click).toHaveBeenCalledOnce()
    expect(client.download).not.toHaveBeenCalled()
  })

  it('aborts an incomplete file instead of reporting save success', async () => {
    const abort = vi.fn(async () => undefined)
    const close = vi.fn(async () => undefined)
    Object.assign(window, { showSaveFilePicker: async () => ({ createWritable: async () => ({ write: async () => undefined, close, abort }) }) })
    const client = mockClient()
    client.download.mockResolvedValueOnce(new Response(new Uint8Array([80])))
    await expect(savePortableProject(fixture().project, 'token', vi.fn(), client)).rejects.toThrow('완료되지')
    expect(close).not.toHaveBeenCalled()
    expect(abort).toHaveBeenCalledOnce()
  })

  it('restores results, receiver definitions and face colors with a fresh CAD session', async () => {
    const { project, scene } = fixture()
    const client = mockClient()
    client.importProject.mockResolvedValueOnce({ project, cad: { path: 'new-session/original.step', displayName: 'original.step' }, trace_cached: true })
    const sceneClient = { getScene: vi.fn(async () => scene) }
    const restored = await loadPortableProject(new File(['PK'], 'one.bitsam'), vi.fn(), client, sceneClient)
    expect(sceneClient.getScene).toHaveBeenCalledWith('new-session/original.step')
    expect(restored.project.analysis_result).toEqual(project.analysis_result)
    const store = createWorkspaceStore()
    store.getState().actions.addCadCase(restored.cad)
    store.getState().actions.restoreProjectState(restored.project.workspace)
    store.getState().actions.setRestoredRayTraceResult(restored.project.analysis_result ?? null)
    expect(store.getState().receivers).toEqual(project.workspace.receivers)
    expect(store.getState().faceColorOverrides).toEqual(project.workspace.faceColorOverrides)
    expect(store.getState().activeRayTraceJobId).toBeNull()
    expect(store.getState().restoredRayTraceResult).toEqual(project.analysis_result)
  })

  it('rejects mismatching geometry without silently dropping face-bound settings', async () => {
    const client = mockClient()
    const scene = createSceneFixture()
    scene.metadata.face_count += 1
    await expect(loadPortableProject(new File(['PK'], 'one.bitsam'), vi.fn(), client, { getScene: async () => scene })).rejects.toThrow('일치하지')
  })

  it('keeps API requests same-origin and does not upload a CAD path supplied by the browser', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { headers: { 'Content-Type': 'application/json' } }))
    const client = createPortableProjectClient({ baseUrl: 'http://127.0.0.1:8788', fetch: fetcher })
    await client.exportProject('scene-known', fixture().project)
    const [url, options] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://127.0.0.1:8788/api/projects/export')
    expect(JSON.parse(options.body as string).scene_token).toBe('scene-known')
    expect(JSON.parse(options.body as string).cad_path).toBeUndefined()
  })
})
