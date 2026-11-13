/** Compare analysis values independently of JSON property insertion order.
 * Arrays retain their order: emitter/transform ordering can affect execution.
 */
export function previewSignature(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) => {
    if (item && typeof item === 'object' && !Array.isArray(item)) {
      return Object.fromEntries(Object.entries(item).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0))
    }
    return item
  })
}

/** Only settings consumed by full-detail Preview affect its saved evidence. */
export function previewPrecisionConfig(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(['max_depth', 'epsilon_mm', 'k_abs', 'k_brdf',
    'angle_dependent_reflectance', 'min_energy', 'min_energy_basis', 'termination_mode',
    'store_ray_paths', 'max_stored_paths', 'compute_backend'].map(key =>
    [key, key === 'max_stored_paths' && typeof value[key] === 'number' ? Math.min(4000, value[key]) : value[key]]))
}

export function samePreviewSignature(a: string | null, b: string | null): boolean {
  if (a === b) return true
  if (!a || !b) return false
  try {
    const normalize = (signature: string) => {
      const value = JSON.parse(signature)
      if (value?.precisionConfig) value.precisionConfig = previewPrecisionConfig(value.precisionConfig)
      return previewSignature(value)
    }
    return normalize(a) === normalize(b)
  } catch {
    return false
  }
}
