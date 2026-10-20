import { createHttpClient } from '@/api/http'
import type { ValidationCase, ValidationDataset, ValidationResponse } from './types'

export function importValidationFile(file: Blob, filename: string, signal?: AbortSignal) {
  return createHttpClient().requestJson<ValidationDataset>(`/api/lt-validation/import?filename=${encodeURIComponent(filename)}`, {
    method: 'POST', body: file, signal,
  })
}

export function compareValidationCases(cases: ValidationCase[], baselineId: string, signal?: AbortSignal) {
  return createHttpClient().requestJson<ValidationResponse>('/api/lt-validation/compare', {
    method: 'POST', json: { cases, baseline_id: baselineId }, signal,
  })
}
