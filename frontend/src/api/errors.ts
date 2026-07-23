export type ApiErrorKind = 'http' | 'network' | 'response'

interface ApiErrorOptions {
  kind: ApiErrorKind
  status?: number
  statusText?: string
  url: string
  payload?: unknown
  cause?: unknown
}

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null
  readonly statusText: string
  readonly url: string
  readonly payload: unknown

  constructor(message: string, options: ApiErrorOptions) {
    super(message, { cause: options.cause })
    this.name = 'ApiError'
    this.kind = options.kind
    this.status = options.status ?? null
    this.statusText = options.statusText ?? ''
    this.url = options.url
    this.payload = options.payload
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}
