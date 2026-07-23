import { describe, expect, it, vi } from 'vitest'

import { ApiError } from './errors'
import { createHttpClient } from './http'

describe('createHttpClient', () => {
  it('joins the base URL and serializes a JSON request', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    const http = createHttpClient({
      baseUrl: 'http://127.0.0.1:8787/',
      fetch: fetchMock,
    })

    await expect(
      http.requestJson<{ ok: boolean }>('/api/test', {
        method: 'POST',
        json: { value: 42 },
      }),
    ).resolves.toEqual({ ok: true })

    const [url, init] = fetchMock.mock.calls[0] ?? []
    expect(url).toBe('http://127.0.0.1:8787/api/test')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe('{"value":42}')
    expect(new Headers(init?.headers).get('Accept')).toBe('application/json')
    expect(new Headers(init?.headers).get('Content-Type')).toBe(
      'application/json',
    )
  })

  it('normalizes a JSON backend error into ApiError', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ error: 'CAD file is required' }), {
          status: 400,
          statusText: 'Bad Request',
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    const http = createHttpClient({ fetch: fetchMock })

    const error = await http
      .requestJson('/api/scene')
      .catch((reason: unknown) => reason)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      kind: 'http',
      status: 400,
      message: 'CAD file is required',
      payload: { error: 'CAD file is required' },
    })
  })

  it('normalizes the upload endpoint text error into ApiError', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response('Upload failed: unsupported file type', { status: 400 }),
      )
    const http = createHttpClient({ fetch: fetchMock })

    await expect(
      http.requestJson('/api/upload', {
        method: 'POST',
        body: new Blob(['cad']),
      }),
    ).rejects.toMatchObject({
      kind: 'http',
      status: 400,
      message: 'Upload failed: unsupported file type',
    })
  })

  it('reports malformed successful responses separately', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response('<html>not json</html>'))
    const http = createHttpClient({ fetch: fetchMock })

    await expect(http.requestJson('/api/test')).rejects.toMatchObject({
      kind: 'response',
      status: 200,
    })
  })
})
