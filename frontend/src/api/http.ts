import { ApiError } from './errors'

type FetchImplementation = typeof globalThis.fetch

export interface HttpClientOptions {
  baseUrl?: string
  fetch?: FetchImplementation
}

export interface JsonRequestOptions extends Omit<RequestInit, 'body'> {
  body?: BodyInit | null
  json?: unknown
}

export interface HttpClient {
  requestJson<T>(path: string, options?: JsonRequestOptions): Promise<T>
  requestText(path: string, options?: RequestInit): Promise<string>
}

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.trim().replace(/\/+$/, '')
}

function resolveUrl(baseUrl: string, path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path
  }

  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${baseUrl}${normalizedPath}`
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

function messageFromPayload(
  payload: unknown,
  status: number,
  statusText: string,
): string {
  if (typeof payload === 'string' && payload.trim()) {
    return payload.trim()
  }

  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>
    for (const key of ['error', 'message']) {
      const value = record[key]
      if (typeof value === 'string' && value.trim()) {
        return value.trim()
      }
    }
  }

  return `API request failed (${status}${statusText ? ` ${statusText}` : ''})`
}

async function readPayload(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) {
    return undefined
  }

  const contentType = response.headers.get('content-type') ?? ''
  const looksLikeJson = /^[\s]*[[{]/.test(text)
  if (contentType.includes('json') || looksLikeJson) {
    try {
      return JSON.parse(text) as unknown
    } catch {
      return text
    }
  }

  return text
}

export function createHttpClient(options: HttpClientOptions = {}): HttpClient {
  const baseUrl = normalizeBaseUrl(options.baseUrl ?? '')
  const fetchImplementation = options.fetch ?? globalThis.fetch

  async function request(
    path: string,
    requestOptions: RequestInit,
  ): Promise<Response> {
    const url = resolveUrl(baseUrl, path)
    let response: Response

    try {
      response = await fetchImplementation(url, requestOptions)
    } catch (error) {
      if (isAbortError(error)) {
        throw error
      }

      throw new ApiError('Python API 서버에 연결할 수 없습니다.', {
        kind: 'network',
        url,
        cause: error,
      })
    }

    if (!response.ok) {
      const payload = await readPayload(response)
      throw new ApiError(
        messageFromPayload(payload, response.status, response.statusText),
        {
          kind: 'http',
          status: response.status,
          statusText: response.statusText,
          url: response.url || url,
          payload,
        },
      )
    }

    return response
  }

  return {
    async requestJson<T>(
      path: string,
      requestOptions: JsonRequestOptions = {},
    ): Promise<T> {
      const { json, ...init } = requestOptions
      if (json !== undefined && init.body !== undefined) {
        throw new TypeError('Use either `json` or `body`, not both.')
      }

      const headers = new Headers(init.headers)
      if (!headers.has('Accept')) {
        headers.set('Accept', 'application/json')
      }
      if (json !== undefined && !headers.has('Content-Type')) {
        headers.set('Content-Type', 'application/json')
      }

      const response = await request(path, {
        ...init,
        headers,
        body: json === undefined ? init.body : JSON.stringify(json),
      })
      const payload = await readPayload(response)

      if (payload === undefined || typeof payload === 'string') {
        throw new ApiError('API가 유효한 JSON 응답을 반환하지 않았습니다.', {
          kind: 'response',
          status: response.status,
          statusText: response.statusText,
          url: response.url || resolveUrl(baseUrl, path),
          payload,
        })
      }

      return payload as T
    },

    async requestText(
      path: string,
      requestOptions: RequestInit = {},
    ): Promise<string> {
      const response = await request(path, requestOptions)
      return response.text()
    },
  }
}
