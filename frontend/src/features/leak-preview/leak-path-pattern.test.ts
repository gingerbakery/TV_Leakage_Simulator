import { expect, it } from 'vitest'
import { leakPathPattern, similarLeakPathPatterns } from './leak-path-pattern'

it('matches collinear subdivisions and nearby ray patterns while rejecting different routes and reversals', () => {
  const reference = leakPathPattern([[0,0,-10],[0,0,0],[0,0,20]], 3)
  const subdivided = leakPathPattern([[1,0,-10],[1,0,-5],[1,0,0],[20,0,20]], 3)
  expect(similarLeakPathPatterns(reference, subdivided,3)).toBe(true)
  expect(similarLeakPathPatterns(reference, leakPathPattern([[0,0,-10],[20,0,-5],[0,0,0],[0,0,20]],3),3)).toBe(false)
  expect(similarLeakPathPatterns(reference, leakPathPattern([[0,0,-10],[0,0,-15],[0,0,0],[0,0,20]],3),3)).toBe(false)
  expect(similarLeakPathPatterns(reference, null,3)).toBe(false)
})
