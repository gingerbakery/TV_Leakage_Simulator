import type { RayTraceRequest, ScenePayload } from '@/api'
import type { PrototypeLeakagePreviewData, LeakagePreviewSample } from './leakage-preview-data'
import { buildLeakageApertureMasks } from './leakage-aperture-mask'
import { buildLeakageApertureField, type LeakageApertureField } from './leakage-aperture-field'

export interface LeakageSurfacePreview {
  fields: LeakageApertureField[]
  fallbackSamples: LeakagePreviewSample[]
  reconstructedSampleCount: number
  reason?: string
}

/** Reuse only the completed result. No new optical request or automatic exposure. */
export function buildLeakageSurfacePreview(
  scene: ScenePayload,
  preview: PrototypeLeakagePreviewData,
  request: RayTraceRequest | undefined,
): LeakageSurfacePreview {
  const fallback = (reason: string): LeakageSurfacePreview => ({
    fields: [], fallbackSamples: preview.samples, reconstructedSampleCount: 0, reason,
  })
  if (preview.status !== 'ready') return fallback(preview.reason)
  if (preview.samples.length === 0) return fallback('no_samples')
  if (!preview.coverage.fullCapture) return fallback('partial_path_capture')
  if (!request) return fallback('request_missing')
  // The current aperture extractor operates on the authored planar skin.
  // Keep transformed/general geometry explicit point evidence until supported.
  if (request.transform_rules.some((rule) => rule.enabled && rule.target_type === 'component' &&
    [rule.move.x, rule.move.y, rule.move.z, rule.tilt.x, rule.tilt.y, rule.tilt.z]
      .some((value) => value !== 0))) return fallback('transformed_aperture_not_supported')
  const masks = buildLeakageApertureMasks(scene, preview.bounds, preview.samples)
  const fields = masks.masks.map((mask) => buildLeakageApertureField(mask, preview.samples))
    .filter((field) => field.status === 'ready' && field.usedSampleKeys.size > 0)
  const used = new Set(fields.flatMap((field) => [...field.usedSampleKeys]))
  return {
    fields,
    fallbackSamples: preview.samples.filter((sample) => !used.has(sample.runId + ':' + sample.pathIndex)),
    reconstructedSampleCount: used.size,
    reason: fields.length ? undefined : (masks.reason ?? 'aperture_samples_unavailable'),
  }
}
