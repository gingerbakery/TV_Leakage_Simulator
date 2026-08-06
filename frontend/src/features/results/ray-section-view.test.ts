import { describe, expect, it } from 'vitest'

import { createDatumReceiver } from '@/features/raytracing'

import { computeSectionPlaneBasis } from './ray-section-view'

describe('computeSectionPlaneBasis', () => {
  it('returns a viewNormal perpendicular to the receiver normal for a well-behaved orientation', () => {
    // rotation [90,0,0] -> normal ~= (0,-1,0), not parallel to world-up.
    const receiver = createDatumReceiver(
      'receiver_001',
      [100, 50, 25],
      [90, 0, 0],
    )
    const basis = computeSectionPlaneBasis(receiver)
    expect(basis).not.toBeNull()
    const normal = receiver.normal
    const dotWithNormal =
      basis!.viewNormal.x * normal[0] +
      basis!.viewNormal.y * normal[1] +
      basis!.viewNormal.z * normal[2]
    expect(dotWithNormal).toBeCloseTo(0, 5)
    const upDotNormal =
      basis!.up.x * normal[0] +
      basis!.up.y * normal[1] +
      basis!.up.z * normal[2]
    expect(upDotNormal).toBeCloseTo(0, 5)
    expect(basis!.viewNormal.length()).toBeCloseTo(1, 5)
    expect(basis!.origin.toArray()).toEqual(receiver.center)
  })

  it('returns a valid, perpendicular viewNormal for a diagonal orientation', () => {
    const receiver = createDatumReceiver(
      'receiver_002',
      [0, 0, 0],
      [20, -30, 45],
    )
    const basis = computeSectionPlaneBasis(receiver)
    expect(basis).not.toBeNull()
    const normal = receiver.normal
    const dotWithNormal =
      basis!.viewNormal.x * normal[0] +
      basis!.viewNormal.y * normal[1] +
      basis!.viewNormal.z * normal[2]
    expect(dotWithNormal).toBeCloseTo(0, 5)
    expect(basis!.viewNormal.length()).toBeCloseTo(1, 5)
  })

  it('falls back to a world-X-based normal when the receiver faces straight up (degenerate case)', () => {
    // The default rotation [0,0,0] normal is world +Z, parallel to world-up -
    // cross(normal, worldUp) is ~zero here, forcing the fallback branch.
    const receiver = createDatumReceiver(
      'receiver_003',
      [0, 0, 0],
      [0, 0, 0],
    )
    expect(receiver.normal[2]).toBeCloseTo(1, 5)
    const basis = computeSectionPlaneBasis(receiver)
    expect(basis).not.toBeNull()
    expect(Number.isFinite(basis!.viewNormal.x)).toBe(true)
    expect(Number.isFinite(basis!.viewNormal.y)).toBe(true)
    expect(Number.isFinite(basis!.viewNormal.z)).toBe(true)
    expect(basis!.viewNormal.length()).toBeCloseTo(1, 5)
    const dotWithNormal =
      basis!.viewNormal.x * receiver.normal[0] +
      basis!.viewNormal.y * receiver.normal[1] +
      basis!.viewNormal.z * receiver.normal[2]
    expect(dotWithNormal).toBeCloseTo(0, 5)
  })

  it('returns null when the receiver has a zero-length normal', () => {
    const receiver = createDatumReceiver('receiver_004', [0, 0, 0], [0, 0, 0])
    receiver.normal = [0, 0, 0]
    expect(computeSectionPlaneBasis(receiver)).toBeNull()
  })
})
