import { describe, expect, it, vi } from 'vitest'

import { ApiError } from './errors'
import {
  apiQueryKeys,
  getRayTracePollingInterval,
  sceneQueryOptions,
} from './query-options'
import type { RayTraceJob, RayTraceResult, ScenePayload } from './types'
import { createAppQueryClient, shouldRetryQuery } from '@/app/query-client'

function rayTraceJob(
  status: RayTraceJob['status'],
): RayTraceJob {
  const progress = {
    job_id: 'job-1',
    processed_rays: 0,
    total_rays: 100,
    progress: 0,
    elapsed_sec: 0,
    estimated_remaining_sec: null,
    rays_per_sec: 0,
    created_at: 1,
  }

  if (status === 'completed') {
    return {
      ...progress,
      status,
      phase: 'completed',
      result: {} as RayTraceResult,
      completed_at: 2,
    }
  }

  if (status === 'failed') {
    return {
      ...progress,
      status,
      phase: 'failed',
      error: 'failed',
      completed_at: 2,
    }
  }

  if (status === 'queued') {
    return {
      ...progress,
      status,
      phase: 'queued',
    }
  }

  return {
    ...progress,
    status: 'running',
    phase: 'tracing',
  }
}

describe('API query options', () => {
  it('uses stable hierarchical query keys', () => {
    expect(apiQueryKeys.scene('C:\\frame.step')).toEqual([
      'leakage-api',
      'scenes',
      'C:\\frame.step',
    ])
    expect(apiQueryKeys.rayTraceJob('job-1')).toEqual([
      'leakage-api',
      'ray-trace',
      'jobs',
      'job-1',
    ])
  })

  it('passes TanStack Query cancellation to the scene API', async () => {
    const scene = {
      schema_version: 'mesh-scene.v1',
    } as ScenePayload
    const getScene = vi.fn().mockResolvedValue(scene)
    const queryClient = createAppQueryClient()

    const result = await queryClient.fetchQuery(
      sceneQueryOptions('C:\\frame.step', { getScene }),
    )

    expect(result).toBe(scene)
    expect(getScene).toHaveBeenCalledOnce()
    expect(getScene.mock.calls[0]?.[0]).toBe('C:\\frame.step')
    expect(getScene.mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal)
  })

  it('polls only queued and running ray trace jobs', () => {
    expect(getRayTracePollingInterval(rayTraceJob('queued'))).toBe(300)
    expect(getRayTracePollingInterval(rayTraceJob('running'))).toBe(300)
    expect(getRayTracePollingInterval(rayTraceJob('completed'))).toBe(false)
    expect(getRayTracePollingInterval(rayTraceJob('failed'))).toBe(false)
    expect(getRayTracePollingInterval(undefined)).toBe(false)
  })

  it('retries network and server failures once, but not client errors', () => {
    const networkError = new ApiError('offline', {
      kind: 'network',
      url: '/api/scene',
    })
    const clientError = new ApiError('invalid', {
      kind: 'http',
      status: 400,
      url: '/api/scene',
    })
    const serverError = new ApiError('failed', {
      kind: 'http',
      status: 500,
      url: '/api/scene',
    })

    expect(shouldRetryQuery(0, networkError)).toBe(true)
    expect(shouldRetryQuery(1, networkError)).toBe(false)
    expect(shouldRetryQuery(0, clientError)).toBe(false)
    expect(shouldRetryQuery(0, serverError)).toBe(true)
  })
})
