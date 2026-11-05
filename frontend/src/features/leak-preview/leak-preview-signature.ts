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

export function samePreviewSignature(a: string | null, b: string | null): boolean {
  if (a === b) return true
  if (!a || !b) return false
  try {
    return previewSignature(JSON.parse(a)) === previewSignature(JSON.parse(b))
  } catch {
    return false
  }
}
