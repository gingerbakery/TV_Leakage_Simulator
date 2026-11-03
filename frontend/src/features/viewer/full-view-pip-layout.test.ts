import { expect, it } from 'vitest'
import { fullViewPipRect } from './full-view-pip-layout'

it('keeps the original default size and bottom-right placement', () => {
  expect(fullViewPipRect({ width: 1000, height: 600 }, null)).toEqual({ x: 766, y: 426, width: 220, height: 160 })
  expect(fullViewPipRect({ width: 500, height: 300 }, null)).toEqual({ x: 316, y: 184, width: 170, height: 102 })
})

it('allows enlargement beyond the old 34 percent limit while keeping both margins on screen', () => {
  expect(fullViewPipRect({ width: 1000, height: 600 }, { width: 640, height: 400 }))
    .toEqual({ x: 346, y: 186, width: 640, height: 400 })
  expect(fullViewPipRect({ width: 1000, height: 600 }, { width: 2000, height: 1200 }))
    .toEqual({ x: 14, y: 14, width: 972, height: 572 })
})

it('clamps to minimum dimensions or the available space in very small viewports', () => {
  expect(fullViewPipRect({ width: 1000, height: 600 }, { width: 1, height: 1 }))
    .toEqual({ x: 866, y: 496, width: 120, height: 90 })
  expect(fullViewPipRect({ width: 100, height: 80 }, { width: 300, height: 200 }))
    .toEqual({ x: 14, y: 14, width: 72, height: 52 })
  expect(fullViewPipRect({ width: 50, height: 80 }, null)).toBeNull()
})
