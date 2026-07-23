import { createHttpClient, type HttpClientOptions } from './http'
import type {
  CadUploadResponse,
  DevStatus,
  RayTraceJob,
  RayTraceRequest,
  RayTraceResult,
  ScenePayload,
} from './types'

export interface ApiRequestOptions {
  signal?: AbortSignal
}

export interface LeakageApiClient {
  getScene(cadPath: string, options?: ApiRequestOptions): Promise<ScenePayload>
  uploadCad(
    file: Blob,
    filename: string,
    options?: ApiRequestOptions,
  ): Promise<CadUploadResponse>
  startRayTrace(
    request: RayTraceRequest,
    options?: ApiRequestOptions,
  ): Promise<RayTraceJob>
  getRayTraceJob(
    jobId: string,
    options?: ApiRequestOptions,
  ): Promise<RayTraceJob>
  runRayTraceDirect(
    request: RayTraceRequest,
    options?: ApiRequestOptions,
  ): Promise<RayTraceResult>
  getDevStatus(options?: ApiRequestOptions): Promise<DevStatus>
  getHealth(options?: ApiRequestOptions): Promise<string>
  ping(options?: ApiRequestOptions): Promise<string>
}

export function createApiClient(
  options: HttpClientOptions = {},
): LeakageApiClient {
  const http = createHttpClient(options)

  return {
    getScene(cadPath, requestOptions) {
      const query = new URLSearchParams({ cad: cadPath })
      return http.requestJson<ScenePayload>(`/api/scene?${query}`, {
        signal: requestOptions?.signal,
      })
    },

    uploadCad(file, filename, requestOptions) {
      const query = new URLSearchParams({ filename })
      return http.requestJson<CadUploadResponse>(`/api/upload?${query}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/octet-stream',
        },
        body: file,
        signal: requestOptions?.signal,
      })
    },

    startRayTrace(rayTraceRequest, requestOptions) {
      return http.requestJson<RayTraceJob>('/api/raytrace/start', {
        method: 'POST',
        json: rayTraceRequest,
        signal: requestOptions?.signal,
      })
    },

    getRayTraceJob(jobId, requestOptions) {
      const query = new URLSearchParams({ job_id: jobId })
      return http.requestJson<RayTraceJob>(`/api/raytrace/status?${query}`, {
        signal: requestOptions?.signal,
      })
    },

    runRayTraceDirect(rayTraceRequest, requestOptions) {
      return http.requestJson<RayTraceResult>('/api/raytrace/direct', {
        method: 'POST',
        json: rayTraceRequest,
        signal: requestOptions?.signal,
      })
    },

    getDevStatus(requestOptions) {
      return http.requestJson<DevStatus>('/dev-status', {
        signal: requestOptions?.signal,
      })
    },

    getHealth(requestOptions) {
      return http.requestText('/health', {
        signal: requestOptions?.signal,
      })
    },

    ping(requestOptions) {
      return http.requestText('/_ping', {
        signal: requestOptions?.signal,
      })
    },
  }
}

export const apiClient = createApiClient({
  baseUrl: import.meta.env.VITE_API_BASE_URL,
})
