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
      '/api/scene?cad=C%3A%5CCAD+files%5CTV+%26+frame.step&format=binary',
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

  it('requests a geometry section cap from the cached CAD scene', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          axis: 'x',
          position: 0,
          contours: [],
          open_chain_count: 0,
        }),
        { headers: { 'Content-Type': 'application/json' } },
      ),
    )
    const client = createApiClient({ fetch: fetchMock })

    await client.getSectionCap({
      scene_token: 'scene-1',
      axis: 'x',
      position: 0,
      hidden_component_ids: [2],
      transform_rules: [],
    })

    const [url, init] = fetchMock.mock.calls[0] ?? []
    expect(url).toBe('/api/scene/section-cap')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toMatchObject({
      scene_token: 'scene-1',
      hidden_component_ids: [2],
    })
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

  it('probes GPU CUDA readiness only through the explicit status endpoint', async () => {
    const response = {
      available: true,
      reason_code: null,
      device_name: 'NVIDIA RTX Test',
      compute_capability: '8.6',
      device_id: 0,
      numba_version: '0.66.0',
      toolkit_layout: 'windows_cuda13_x64_compat',
      strict_float64: true,
      kernel_executed: true,
      kernel_verified: true,
      preflight_scope: 'production_ray_bvh',
      provider_contract: 'strict_float64_bvh_v1',
    } as const
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () =>
        new Response(JSON.stringify(response), {
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    const client = createApiClient({ fetch: fetchMock })

    await expect(client.getGpuCudaStatus()).resolves.toEqual(response)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/gpu-cuda/status')

    await client.getGpuCudaStatus({ refresh: true })
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      '/api/gpu-cuda/status?refresh=true',
    )
  })
})
