import { createEscapeEnclosureReceivers, detectLeakPreviewCandidates, type SavedLeakPreviewResult } from '@/features/leak-preview/leak-preview-model'
import { createSceneFixture } from './scene-fixture'
import { createRayTraceResultFixture } from './raytrace-fixture'

export function createSavedLeakPreviewFixture(): SavedLeakPreviewResult {
  const scene = createSceneFixture(), result = createRayTraceResultFixture()
  const receiver = createEscapeEnclosureReceivers(scene)[0]
  result.receivers = [receiver]
  result.receiver_grids = [{ receiver_id: receiver.receiver_id, resolution: [3,1],
    bin_area_mm2: 1, flux_lumen: [[1,0,.5]], hit_count: 2 }]
  result.stored_paths = []
  const detection = detectLeakPreviewCandidates(scene, result)
  return { format: 'leak-preview-result.v1', result, ...detection,
    selectedCandidateId: detection.candidates[0]?.id ?? null, highlight: null, inputSignature: null }
}
