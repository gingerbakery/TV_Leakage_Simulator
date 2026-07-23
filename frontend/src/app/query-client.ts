import { QueryClient } from '@tanstack/react-query'

import { isApiError } from '@/api/errors'

const QUERY_STALE_TIME_MS = 30_000
const QUERY_GARBAGE_COLLECTION_MS = 5 * 60_000

export function shouldRetryQuery(
  failureCount: number,
  error: unknown,
): boolean {
  const hasRetryRemaining = failureCount < 1
  if (!hasRetryRemaining) {
    return false
  }

  if (!isApiError(error)) {
    return true
  }

  if (error.kind === 'network') {
    return true
  }

  return error.kind === 'http' && (error.status ?? 0) >= 500
}

export function createAppQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: QUERY_STALE_TIME_MS,
        gcTime: QUERY_GARBAGE_COLLECTION_MS,
        retry: shouldRetryQuery,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  })
}
