import { queryOptions } from '@tanstack/react-query'

import { apiClient, type LeakageApiClient } from './client'
import type { RayTraceJob } from './types'

type SceneApi = Pick<LeakageApiClient, 'getScene'>
type RayTraceJobApi = Pick<LeakageApiClient, 'getRayTraceJob'>
type SystemApi = Pick<LeakageApiClient, 'getDevStatus'>
type GpuCudaStatusApi = Pick<LeakageApiClient, 'getGpuCudaStatus'>

const RAY_TRACE_POLL_INTERVAL_MS = 300

export const apiQueryKeys = {
  all: ['leakage-api'] as const,
  system: () => [...apiQueryKeys.all, 'system'] as const,
  devStatus: () => [...apiQueryKeys.system(), 'dev-status'] as const,
  gpuCudaStatus: () => [...apiQueryKeys.system(), 'gpu-cuda-status'] as const,
  scenes: () => [...apiQueryKeys.all, 'scenes'] as const,
  scene: (cadPath: string) =>
    [...apiQueryKeys.scenes(), cadPath] as const,
  rayTrace: () => [...apiQueryKeys.all, 'ray-trace'] as const,
  rayTraceJobs: () => [...apiQueryKeys.rayTrace(), 'jobs'] as const,
  rayTraceJob: (jobId: string) =>
    [...apiQueryKeys.rayTraceJobs(), jobId] as const,
}

export function getRayTracePollingInterval(
  job: RayTraceJob | undefined,
): number | false {
  return job?.status === 'queued' || job?.status === 'running'
    ? RAY_TRACE_POLL_INTERVAL_MS
    : false
}

export function devStatusQueryOptions(
  client: SystemApi = apiClient,
) {
  return queryOptions({
    queryKey: apiQueryKeys.devStatus(),
    queryFn: ({ signal }) => client.getDevStatus({ signal }),
    staleTime: 5_000,
  })
}

export function gpuCudaStatusQueryOptions(
  client: GpuCudaStatusApi = apiClient,
) {
  return queryOptions({
    queryKey: apiQueryKeys.gpuCudaStatus(),
    queryFn: ({ signal }) => client.getGpuCudaStatus({ signal }),
    // Re-enabling this query means the user explicitly selected GPU again.
    // Re-read the backend verdict even though the backend may reuse its probe.
    staleTime: 0,
    retry: false,
  })
}

export function sceneQueryOptions(
  cadPath: string,
  client: SceneApi = apiClient,
) {
  return queryOptions({
    queryKey: apiQueryKeys.scene(cadPath),
    queryFn: ({ signal }) => client.getScene(cadPath, { signal }),
    enabled: cadPath.trim().length > 0,
    staleTime: Number.POSITIVE_INFINITY,
    // A scene token is server-memory scoped. Discard it when no screen uses it
    // so returning to the CAD always obtains a token that the backend owns.
    gcTime: 0,
  })
}

export function rayTraceJobQueryOptions(
  jobId: string,
  client: RayTraceJobApi = apiClient,
) {
  return queryOptions({
    queryKey: apiQueryKeys.rayTraceJob(jobId),
    queryFn: ({ signal }) => client.getRayTraceJob(jobId, { signal }),
    enabled: jobId.trim().length > 0,
    staleTime: 0,
    refetchInterval: (query) =>
      getRayTracePollingInterval(query.state.data),
    refetchIntervalInBackground: true,
  })
}
