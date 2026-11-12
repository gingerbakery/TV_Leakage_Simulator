import type { LeakPreviewObserverSettings } from './leak-preview-model'
import type { Vec3 } from '@/api'
import { receiverViewDirection } from '@/features/raytracing/receiver-view'

export type ObserverSamplingMode = 'fast' | 'balanced' | 'fine' | 'manual'
export const observerViewLimit = 121
export const observerSampleLimit = 500_000
export const fullDetailSampleLimit = 100_000_000
export interface ObserverProjection { minimum: Vec3; maximum: Vec3; direction: import('./leak-preview-model').LeakPreviewDirection }

export function planObserverSampling(observer: LeakPreviewObserverSettings, sourceCount = 2, projection?: ObserverProjection) {
  const { range_deg: radius, horizontal_deg: horizontal, vertical_deg: vertical } = observer
  const step = observer.step_deg
  const radiance = observer.measurement === 'radiance'
  const viewLimit = observer.coverage === 'hemisphere' ? 2048 : observerViewLimit
  let views = 0
  let error: string | null = null
  let anglePairs: Array<[number, number]> = []
  const valid = [radius, step, horizontal, vertical].every(Number.isFinite) && radius >= 0 && radius <= 10 && observer.step_deg >= .1 && observer.step_deg <= 90 && Math.abs(horizontal) <= 89.9 && Math.abs(vertical) <= 89.9
  if (valid) {
    const offsets = new Set([0, -radius, radius])
    for (let i = -Math.floor(radius / step); i <= Math.floor(radius / step); i++) offsets.add(i * step)
    if (observer.refine_angles) {
      const ordered = [...offsets].sort((a, b) => a-b)
      ordered.slice(1).forEach((value, i) => offsets.add((ordered[i]+value)/2))
    }
    const values = (center: number) => [...new Set([...offsets].map((offset) => Number(Math.max(-89.9, Math.min(89.9, center + offset)).toFixed(8))))]
    anglePairs = values(horizontal).flatMap((h) => values(vertical).map((v): [number, number] => [h, v]))
    if (observer.coverage === 'hemisphere') {
      const n = 2 * Math.ceil(90 / step) * (observer.refine_angles ? 2 : 1)
      if (n > 45) { error = '180° 전체 검사의 각도 간격을 늘리세요.'; anglePairs = [] }
      else {
        const values = Array.from({ length: n + 1 }, (_, i) => Number((-90 + 180*i/n).toFixed(8)))
        anglePairs = [[0, -90], ...values.flatMap((h) => values.slice(1, -1).map((v): [number, number] => [h, v])), [0, 90]]
      }
    }
    views = anglePairs.length
    if (views > viewLimit) error = `각도 조합 ${views.toLocaleString()}개 · 한도 ${viewLimit}개. 각도 간격을 늘리거나 주변 범위를 줄이세요.`
  } else error = '시야각·주변 범위·각도 간격을 확인하세요.'
  let resolution = observer.resolution
  let repeats = observer.samples_per_cell
  const fullDetail = observer.search_mode === 'full_detail'
  if (fullDetail) {
    resolution = 64; repeats = radiance ? observer.samples_per_cell >= 64 ? 64 : observer.samples_per_cell >= 32 ? 32 : observer.samples_per_cell >= 16 ? 16 : 8 : 2
    const spacing = observer.spacing_mm ?? 2
    let plannedCount = 0
    if (!Number.isFinite(spacing) || spacing < .1 || spacing > 100) error = '검사 간격은 0.1~100 mm로 지정하세요.'
    else if (!projection) error = '전체 정밀 검색의 모델 범위가 필요합니다.'
    else if (!error) {
      const size = projection.maximum.map((value, i) => Math.max(.001, value - projection.minimum[i]))
      for (const [horizontal_deg, vertical_deg] of anglePairs) {
        const d = receiverViewDirection({ axis: projection.direction, horizontal_deg, vertical_deg, range_deg: 0, step_deg: step })
        const raw = Math.abs(d[2]) > .95 ? [d[2], 0, -d[0]] : [-d[1], d[0], 0]
        const length = Math.hypot(...raw), u = raw.map((value) => value / length)
        const v = [d[1]*u[2]-d[2]*u[1], d[2]*u[0]-d[0]*u[2], d[0]*u[1]-d[1]*u[0]]
        const extent = (axis: number[]) => size.reduce((sum, value, i) => sum + value*Math.abs(axis[i]), 0)
        const grid = Math.max(1, Math.ceil(extent(u)/spacing)) * Math.max(1, Math.ceil(extent(v)/spacing)) * repeats
        plannedCount += radiance ? grid : grid + Math.max(grid, sourceCount)
      }
      if (plannedCount > fullDetailSampleLimit) error = `관찰 표본 ${plannedCount.toLocaleString()}개 · 한도 100,000,000개. 검사 간격을 늘리거나 각도 수를 줄이세요.`
    }
    return { resolution, samples_per_cell: repeats, views, plannedCount, error, fullDetail,
      suggestedStep: observer.coverage === 'hemisphere' ? 15 : Math.max(.1, Math.ceil(radius / 5 * 10) / 10) * (observer.refine_angles ? 2 : 1) }
  }
  const samples = (n: number, r: number) => {
    const base = n*n*r
    // Match the server guard as well as its extra source-projection samples.
    return radiance ? views*base : Math.max(views*base*2, views*(base + Math.max(base, sourceCount)))
  }
  const mode = observer.sampling ?? 'manual'
  if (!error && mode !== 'manual') {
    const preset = { fast: { resolution: 32, repeats: radiance ? 4 : 1, budget: 100_000 }, balanced: { resolution: 64, repeats: radiance ? 8 : 2, budget: 300_000 }, fine: { resolution: 128, repeats: radiance ? 16 : 2, budget: 480_000 } }[mode]
    resolution = preset.resolution
    repeats = preset.repeats
    if (!radiance && samples(resolution, repeats) > preset.budget) repeats = 1
    while (resolution > 8 && samples(resolution, repeats) > preset.budget) resolution--
  }
  const plannedCount = samples(resolution, repeats)
  if (!error && (!Number.isInteger(resolution) || resolution < 8 || resolution > 256 || !Number.isInteger(repeats) || repeats < 1 || repeats > 16)) error = '해상도·셀 반복을 확인하세요.'
  if (!error && plannedCount > observerSampleLimit) error = `역추적 표본 ${plannedCount.toLocaleString()}개 · 한도 500,000개. 검사 수준 또는 수동 설정을 낮추세요.`
  return { resolution, samples_per_cell: repeats, views, plannedCount, error, fullDetail,
    suggestedStep: observer.coverage === 'hemisphere' ? 15 : Math.max(.1, Math.ceil(radius / 5 * 10) / 10) * (observer.refine_angles ? 2 : 1) }
}
