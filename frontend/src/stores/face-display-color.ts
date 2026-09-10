export interface FaceDisplayColor {
  componentId: number
  faceIds: number[]
  color: string
}

export function updateFaceDisplayColors(
  existing: FaceDisplayColor[], componentId: number, faceIds: Iterable<number>, color: string | null,
): FaceDisplayColor[] {
  if (!Number.isSafeInteger(componentId) || componentId < 0) return existing
  const normalizedColor = color?.trim().toLowerCase() ?? null
  if (normalizedColor !== null && !/^#[0-9a-f]{6}$/.test(normalizedColor)) return existing
  const selected = new Set([...faceIds].filter((id) => Number.isSafeInteger(id) && id >= 0))
  if (!selected.size) return existing
  const next = existing.flatMap((entry) => {
    if (entry.componentId !== componentId) return [entry]
    if (entry.color === normalizedColor) {
      entry.faceIds.forEach((id) => selected.add(id))
      return []
    }
    const remaining = entry.faceIds.filter((id) => !selected.has(id))
    return remaining.length ? [{ ...entry, faceIds: remaining }] : []
  })
  if (normalizedColor !== null) next.push({ componentId, faceIds: [...selected].sort((left, right) => left - right), color: normalizedColor })
  return next
}

export function normalizeFaceDisplayColors(entries: FaceDisplayColor[] = []): FaceDisplayColor[] {
  return entries.reduce((result, entry) => updateFaceDisplayColors(result, entry.componentId, entry.faceIds, entry.color), [] as FaceDisplayColor[])
}
