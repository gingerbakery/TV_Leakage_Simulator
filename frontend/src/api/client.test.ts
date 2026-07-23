import { describe, expect, it, vi } from 'vitest'

import { createApiClient } from './client'

describe('createApiClient', () => {
  it('encodes CAD paths in the scene query', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ schema_version: 'mesh-scene.v1' }), {
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    const client = createApiClient({ fetch: fetchMock })

    await client.getScene('C:\\CAD files\\TV & frame.step')

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/scene?cad=C%3A%5CCAD+files%5CTV+%26+frame.step',
    )
  })

  it('uploads raw CAD bytes without multipart conversion', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            ok: true,
            display_name: 'frame.step',
            path: 'C:\\uploads\\frame.step',
          }),
          { headers: { 'Content-Type': 'application/json' } },
        ),
      )
    const client = createApiClient({ fetch: fetchMock })
    const file = new Blob(['STEP DATA'])

    await client.uploadCad(file, 'TV frame.step')

    const [url, init] = fetchMock.mock.calls[0] ?? []
    expect(url).toBe('/api/upload?filename=TV+frame.step')
    expect(init?.body).toBe(file)
    expect(new Headers(init?.headers).get('Content-Type')).toBe(
      'application/octet-stream',
    )
  })

  it('passes an abort signal to status polling requests', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            job_id: 'job-1',
            status: 'queued',
            phase: 'queued',
          }),
          { headers: { 'Content-Type': 'application/json' } },
        ),
      )
    const client = createApiClient({ fetch: fetchMock })
    const controller = new AbortController()

    await client.getRayTraceJob('job 1', { signal: controller.signal })

    const [url, init] = fetchMock.mock.calls[0] ?? []
    expect(url).toBe('/api/raytrace/status?job_id=job+1')
    expect(init?.signal).toBe(controller.signal)
  })
})
