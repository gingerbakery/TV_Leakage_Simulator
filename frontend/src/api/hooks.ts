import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import { apiClient, type LeakageApiClient } from './client'
import {
  apiQueryKeys,
  devStatusQueryOptions,
  rayTraceJobQueryOptions,
  sceneQueryOptions,
} from './query-options'
import type { RayTraceRequest } from './types'

type UploadApi = Pick<LeakageApiClient, 'uploadCad'>
type StartRayTraceApi = Pick<LeakageApiClient, 'startRayTrace'>
type DirectRayTraceApi = Pick<LeakageApiClient, 'runRayTraceDirect'>

export interface UploadCadVariables {
  file: Blob
  filename: string
  signal?: AbortSignal
}

export interface RayTraceMutationVariables {
  request: RayTraceRequest
  signal?: AbortSignal
}

export function useDevStatusQuery() {
  return useQuery(devStatusQueryOptions())
}

export function useSceneQuery(cadPath: string) {
  return useQuery(sceneQueryOptions(cadPath))
}

export function useRayTraceJobQuery(jobId: string | null) {
  return useQuery(rayTraceJobQueryOptions(jobId ?? ''))
}

export function useUploadCadMutation(
  client: UploadApi = apiClient,
) {
  return useMutation({
    mutationKey: [...apiQueryKeys.scenes(), 'upload'],
    mutationFn: ({ file, filename, signal }: UploadCadVariables) =>
      client.uploadCad(file, filename, { signal }),
  })
}

export function useStartRayTraceMutation(
  client: StartRayTraceApi = apiClient,
) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationKey: [...apiQueryKeys.rayTrace(), 'start'],
    mutationFn: ({ request, signal }: RayTraceMutationVariables) =>
      client.startRayTrace(request, { signal }),
    onSuccess: (job) => {
      queryClient.setQueryData(apiQueryKeys.rayTraceJob(job.job_id), job)
    },
  })
}

export function useDirectRayTraceMutation(
  client: DirectRayTraceApi = apiClient,
) {
  return useMutation({
    mutationKey: [...apiQueryKeys.rayTrace(), 'direct'],
    mutationFn: ({ request, signal }: RayTraceMutationVariables) =>
      client.runRayTraceDirect(request, { signal }),
  })
}
