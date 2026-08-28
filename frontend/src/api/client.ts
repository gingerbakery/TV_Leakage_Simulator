import { createHttpClient, type HttpClientOptions } from './http'
import type {
  CadUploadResponse,
  DevStatus,
  GpuCudaStatus,
  RayTraceJob,
  RayTraceRequest,
  RayTraceResult,
  ScenePayload,
  SectionCapRequest,
  SectionCapResponse,
} from './types'
import { decodeSceneBinary } from './scene-binary'

export interface ApiRequestOptions {
  signal?: AbortSignal
}

export interface GpuCudaStatusRequestOptions extends ApiRequestOptions {
  refresh?: boolean
}

export interface LeakageApiClient {
  getScene(cadPath: string, options?: ApiRequestOptions): Promise<ScenePayload>
  getSectionCap(
    request: SectionCapRequest,
    options?: ApiRequestOptions,
  ): Promise<SectionCapResponse>
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
  stopRayTrace(jobId: string, options?: ApiRequestOptions): Promise<RayTraceJob>
  runRayTraceDirect(
    request: RayTraceRequest,
    options?: ApiRequestOptions,
  ): Promise<RayTraceResult>
  getDevStatus(options?: ApiRequestOptions): Promise<DevStatus>
  getGpuCudaStatus(options?: GpuCudaStatusRequestOptions): Promise<GpuCudaStatus>
  getHealth(options?: ApiRequestOptions): Promise<string>
  ping(options?: ApiRequestOptions): Promise<string>
}

export function createApiClient(
  options: HttpClientOptions = {},
): LeakageApiClient {
  const http = createHttpClient(options)

  return {
    async getScene(cadPath, requestOptions) {
      const binaryQuery = new URLSearchParams({ cad: cadPath, format: 'binary' })
      try {
        const buffer = await http.requestArrayBuffer(
          `/api/scene?${binaryQuery}`,
          {
            headers: { Accept: 'application/vnd.bitsam.scene-binary' },
            signal: requestOptions?.signal,
          },
        )
        const magic = new TextDecoder().decode(
          new Uint8Array(buffer, 0, Math.min(8, buffer.byteLength)),
        )
        if (magic === 'BITSAMSC') return decodeSceneBinary(buffer)
        return JSON.parse(new TextDecoder().decode(buffer)) as ScenePayload
      } catch (error) {
        if (requestOptions?.signal?.aborted) throw error
        console.warn('CAD Binary scene load failed; retrying JSON.', error)
        const jsonQuery = new URLSearchParams({ cad: cadPath })
        return http.requestJson<ScenePayload>(`/api/scene?${jsonQuery}`, {
          signal: requestOptions?.signal,
        })
      }
    },

    getSectionCap(request, requestOptions) {
      return http.requestJson<SectionCapResponse>('/api/scene/section-cap', {
        method: 'POST',
        json: request,
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

    stopRayTrace(jobId, requestOptions) {
      const query = new URLSearchParams({ job_id: jobId })
      return http.requestJson<RayTraceJob>(`/api/raytrace/stop?${query}`, {
        method: 'POST',
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

    getGpuCudaStatus(requestOptions) {
      const path = requestOptions?.refresh
        ? '/api/gpu-cuda/status?refresh=true'
        : '/api/gpu-cuda/status'
      return http.requestJson<GpuCudaStatus>(path, {
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
