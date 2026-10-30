import type { LeakPreviewObserverSettings } from './leak-preview-model'

export type ObserverSamplingMode = 'fast' | 'balanced' | 'fine' | 'manual'
export const observerViewLimit = 121
export const observerSampleLimit = 500_000

export function planObserverSampling(observer: LeakPreviewObserverSettings, sourceCount = 2) {
  const { range_deg: radius, step_deg: step, horizontal_deg: horizontal, vertical_deg: vertical } = observer
  let views = 0
  let error: string | null = null
  const valid = [radius, step, horizontal, vertical].every(Number.isFinite) && radius >= 0 && radius <= 10 && step >= .1 && step <= 10 && Math.abs(horizontal) <= 89.9 && Math.abs(vertical) <= 89.9
  if (valid) {
    const offsets = new Set([0, -radius, radius])
    for (let i = -Math.floor(radius / step); i <= Math.floor(radius / step); i++) offsets.add(i * step)
    const count = (center: number) => new Set([...offsets].map((offset) => Math.max(-89.9, Math.min(89.9, center + offset)).toFixed(8))).size
    views = count(horizontal) * count(vertical)
    if (views > observerViewLimit) error = `각도 조합 ${views.toLocaleString()}개 · 한도 ${observerViewLimit}개. 각도 간격을 늘리거나 주변 범위를 줄이세요.`
  } else error = '시야각·주변 범위·각도 간격을 확인하세요.'
  let resolution = observer.resolution
  let repeats = observer.samples_per_cell
  const samples = (n: number, r: number) => {
    const base = n*n*r
    // Match the server guard as well as its extra source-projection samples.
    return Math.max(views*base*2, views*(base + Math.max(base, sourceCount)))
  }
  const mode = observer.sampling ?? 'manual'
  if (!error && mode !== 'manual') {
    const preset = { fast: { resolution: 32, repeats: 1, budget: 100_000 }, balanced: { resolution: 64, repeats: 2, budget: 300_000 }, fine: { resolution: 128, repeats: 2, budget: 480_000 } }[mode]
    resolution = preset.resolution
    repeats = preset.repeats
    if (samples(resolution, repeats) > preset.budget) repeats = 1
    while (resolution > 8 && samples(resolution, repeats) > preset.budget) resolution--
  }
  const plannedCount = samples(resolution, repeats)
  if (!error && (!Number.isInteger(resolution) || resolution < 8 || resolution > 256 || !Number.isInteger(repeats) || repeats < 1 || repeats > 16)) error = '해상도·셀 반복을 확인하세요.'
  if (!error && plannedCount > observerSampleLimit) error = `역추적 표본 ${plannedCount.toLocaleString()}개 · 한도 500,000개. 검사 수준 또는 수동 설정을 낮추세요.`
  return { resolution, samples_per_cell: repeats, views, plannedCount, error,
    suggestedStep: Math.max(.1, Math.ceil(radius / 5 * 10) / 10) }
}
