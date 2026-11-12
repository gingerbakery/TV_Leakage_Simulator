import { createHttpClient, type HttpClientOptions } from './http'
import type {
  CadUploadResponse,
  DevStatus,
  GpuCudaStatus,
  RayTraceJob,
  RayTraceRequest,
  RayTraceResult,
  ScenePayload,
  SceneRefreshResponse,
  SectionCapRequest,
  SectionCapResponse,
} from './types'
import { decodeSceneBinary } from './scene-binary'
import { isBsdfAsset, type BsdfAsset } from '@/features/materials/bsdf-asset'

export interface ApiRequestOptions {
  signal?: AbortSignal
}

export interface SensorComparisonOptions {
  receiver_id: string; batches: number; forward_samples: number; reverse_samples: number; tolerance_percent: number
}
export interface SensorComparisonBatch {
  index?: number;
  forward_flux_lumen?: number; reverse_flux_lumen?: number;
  forward_hits?: number; reverse_positive_samples?: number;
  forward_flux_square_sum?: number; reverse_second_moment?: number; reverse_sample_count?: number;
  forward_sec?: number; reverse_sec?: number;
  forward_execution: Record<string, unknown>; reverse_execution: Record<string, unknown>;
}
export interface SensorComparisonReport {
  physical_input_sha256?: string
  forward_diagnostics?: Array<{ index: number; report: unknown }>
  reference_replay?: ForwardPathReplay
  witness?: Array<{ receiver_id: string; path_valid: boolean; diagnosis: string }>;
  sensor: { receiver_id: string; width_mm?: number; height_mm?: number; acceptance_angle_deg?: number }; options: SensorComparisonOptions; verdict: 'match' | 'mismatch' | 'inconclusive'; reason: string;
  gpu_execution_proven?: boolean; compute_execution_state?: string;
  batches?: SensorComparisonBatch[];
  forward_flux_lumen?: number; reverse_flux_lumen?: number; difference_interval_lumen?: number[]; tolerance_lumen?: number;
  forward_hits?: number; reverse_positive_samples?: number; forward_effective_samples?: number; reverse_effective_samples?: number;
  original_termination_probe?: { forward_flux_lumen: number; forward_hits: number; diagnostics?: unknown }
}
export interface ForwardPathReplay {
  error?: string
  state: string; reason: string; included_in_flux: false; execution_backend: 'cpu'
  path_reproduced?: boolean; matched_surface_count?: number
  first_difference?: {segment_index:number;expected_point:number[];actual_point:number[]|null}|null
  actual_events?: unknown[]; diagnostics?: unknown
}
export interface SensorComparisonJob {
  input_request?: RayTraceRequest; input_request_sha256?: string
  job_id: string; status: 'queued' | 'running' | 'completed' | 'stopped' | 'failed'; phase: string; progress: number;
  batch: number; scene_token?: string; stop_requested: boolean; error: string | null; report: SensorComparisonReport | null
}

export interface GpuCudaStatusRequestOptions extends ApiRequestOptions {
  refresh?: boolean
}

export interface LeakageApiClient {
  startSensorComparison(request: RayTraceRequest, options: SensorComparisonOptions): Promise<SensorComparisonJob>
  getSensorComparison(): Promise<SensorComparisonJob | null>
  stopSensorComparison(jobId: string): Promise<SensorComparisonJob>
  validateObserverReceiver(request: RayTraceRequest, options?: ApiRequestOptions): Promise<{
    simulation_run_started: boolean; geometry_cache_hit: boolean; runtime_sec: number;
    receiver_checks: Array<{ receiver_id: string; path_valid: boolean; diagnosis: string; forward_hit_count: number | null; visible_endpoint_kind?: string | null; forward_replay?: ForwardPathReplay }>
  }>
  importBsdf(files: { name: string; text: string }[], options?: ApiRequestOptions): Promise<BsdfAsset>
  getScene(cadPath: string, options?: ApiRequestOptions, accessoryPaths?: string[]): Promise<ScenePayload>
  refreshScene(cadPath: string, options?: ApiRequestOptions, accessoryPaths?: string[]): Promise<SceneRefreshResponse>
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
    startSensorComparison(request, options) {
      return http.requestJson('/api/raytrace/compare-sensor/start', { method: 'POST', json: { request, options } })
    },
    getSensorComparison() { return http.requestJson('/api/raytrace/compare-sensor/status') },
    stopSensorComparison(jobId) { return http.requestJson(`/api/raytrace/compare-sensor/stop?job_id=${encodeURIComponent(jobId)}`, { method: 'POST' }) },
    async importBsdf(files, requestOptions) {
      const asset = await http.requestJson<unknown>('/api/bsdf/import', {
        method: 'POST', json: { files }, signal: requestOptions?.signal,
      })
      if (!isBsdfAsset(asset)) throw new Error('BSDF 응답 형식이 올바르지 않습니다. 서버 버전을 확인하세요.')
      return asset
    },
    async getScene(cadPath, requestOptions, accessoryPaths = []) {
      const binaryQuery = new URLSearchParams({ cad: cadPath, format: 'binary' })
      accessoryPaths.forEach((path) => binaryQuery.append('aux', path))
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
        accessoryPaths.forEach((path) => jsonQuery.append('aux', path))
        return http.requestJson<ScenePayload>(`/api/scene?${jsonQuery}`, {
          signal: requestOptions?.signal,
        })
      }
    },

    refreshScene(cadPath, requestOptions, accessoryPaths = []) {
      return http.requestJson<SceneRefreshResponse>('/api/scene/refresh', {
        method: 'POST',
        json: {
          cad: cadPath,
          ...(accessoryPaths.length > 0 ? { aux: accessoryPaths } : {}),
        },
        signal: requestOptions?.signal,
      })
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

    validateObserverReceiver(request, requestOptions) {
      return http.requestJson('/api/raytrace/validate-observer', {
        method: 'POST', json: request, signal: requestOptions?.signal,
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
