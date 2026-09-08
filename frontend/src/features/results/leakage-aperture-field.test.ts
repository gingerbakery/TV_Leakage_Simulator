import { describe, expect, it } from 'vitest'

import type { Vec3 } from '@/api'
import type { LeakageApertureMask } from './leakage-aperture-mask'
import type { LeakagePreviewSample } from './leakage-preview-data'
import { buildLeakageApertureField } from './leakage-aperture-field'

function mask(width: number, height: number, stepMm = 1): LeakageApertureMask {
  return {
    face: 'z_max', origin: [0, 0], stepMm, width, height, plane: 10,
    open: new Uint8Array(width * height).fill(1),
    componentIds: new Int32Array(width * height),
  }
}

function sample(
  pathIndex: number,
  x: number,
  y: number,
  weight = 1,
  outgoingDirection: Vec3 = [0, 0, 1],
): LeakagePreviewSample {
  return {
    runId: 'field', pathIndex, receiverId: 'receiver',
    exitPoint: [x, y, 10], exitFaces: ['z_max'], outgoingDirection, weight,
  }
}

function expectDensitiesClose(left: Float64Array, right: Float64Array, scale = 1): void {
  expect(left.length).toBe(right.length)
  for (let index = 0; index < left.length; index += 1) {
    expect(left[index]).toBeCloseTo(right[index] * scale, 12)
  }
}

function uniformSlotSamples(count: number): LeakagePreviewSample[] {
  return Array.from({ length: count }, (_, index) =>
    sample(index, (index + 0.5) * 20 / count, 0.25, 1 / count),
  )
}

describe('aperture-constrained leakage field', () => {
  it('halves every linear density when source flux halves, without normalizing the peak', () => {
    const aperture = mask(80, 2, 0.25)
    const samples = uniformSlotSamples(40)
    const original = buildLeakageApertureField(aperture, samples)
    const dimmed = buildLeakageApertureField(aperture, samples.map((item) => ({ ...item, weight: item.weight / 2 })))

    expect(original.status).toBe('ready')
    expect(original.usedSampleKeys.size).toBe(40)
    expect(original.totalInputEnergy).toBeCloseTo(1, 12)
    expect(original.totalDepositedEnergy).toBeCloseTo(1, 12)
    expect(dimmed.totalDepositedEnergy).toBeCloseTo(0.5, 12)
    expectDensitiesClose(dimmed.density, original.density, 0.5)
    for (const bin of original.bins) {
      expectDensitiesClose(dimmed.bins[bin.id].density, bin.density, 0.5)
    }
  })

  it('keeps the same field when each ray is split into two half-energy rays', () => {
    const aperture = mask(80, 2, 0.25)
    const samples = uniformSlotSamples(20).map((item, index) => ({
      ...item, outgoingDirection: [index % 2 === 0 ? 0.8 : -0.8, 0, 0.6] as Vec3,
    }))
    const split = samples.flatMap((item, index) => [
      { ...item, pathIndex: index * 2, weight: item.weight / 2 },
      { ...item, pathIndex: index * 2 + 1, weight: item.weight / 2 },
    ])
    const original = buildLeakageApertureField(aperture, samples)
    const refined = buildLeakageApertureField(aperture, split)

    expectDensitiesClose(refined.density, original.density)
    expect(refined.totalDepositedEnergy).toBeCloseTo(original.totalDepositedEnergy, 12)
    for (const bin of original.bins) {
      expectDensitiesClose(refined.bins[bin.id].density, bin.density)
      bin.direction.forEach((value, axis) => expect(refined.bins[bin.id].direction[axis]).toBeCloseTo(value, 12))
    }
  })

  it('produces no light from empty or zero-energy observations', () => {
    const aperture = mask(8, 3, 0.25)
    for (const samples of [[], [sample(0, 1, 0.25, 0)]]) {
      const field = buildLeakageApertureField(aperture, samples)
      expect(field.status).toBe('ready')
      expect(field.totalDepositedEnergy).toBe(0)
      expect(field.usedSampleKeys.size).toBe(0)
      expect(field.density.every((value) => value === 0)).toBe(true)
    }
  })

  it('does not send energy into a separate unobserved opening', () => {
    const aperture = mask(12, 5)
    // Two parallel openings separated by a solid row.
    for (let x = 0; x < aperture.width; x += 1) {
      aperture.open[2 * aperture.width + x] = 0
      aperture.componentIds[2 * aperture.width + x] = -1
      for (let y = 3; y < 5; y += 1) aperture.componentIds[y * aperture.width + x] = 1
    }
    const field = buildLeakageApertureField(aperture, [sample(0, 5.5, 1.5)])

    expect(field.totalDepositedEnergy).toBeCloseTo(1, 12)
    expect(field.density.slice(2 * aperture.width).every((value) => value === 0)).toBe(true)
  })

  it('cannot cross a solid barrier even when both sides connect around a distant corner', () => {
    const aperture = mask(9, 9)
    // This remains one connected opening around the top of the wall.
    for (let y = 0; y < 8; y += 1) {
      aperture.open[y * aperture.width + 4] = 0
      aperture.componentIds[y * aperture.width + 4] = -1
    }
    const field = buildLeakageApertureField(aperture, [sample(0, 3.5, 1.5)])

    expect(field.totalDepositedEnergy).toBeCloseTo(1, 12)
    for (let y = 0; y < aperture.height; y += 1) {
      for (let x = 4; x < aperture.width; x += 1) {
        expect(field.density[y * aperture.width + x]).toBe(0)
      }
    }
  })

  it('keeps the footprint local instead of filling an entire long open slot', () => {
    const aperture = mask(200, 2, 0.25)
    const field = buildLeakageApertureField(aperture, [sample(0, 10, 0.25)])
    let illuminatedCount = 0
    for (let y = 0; y < aperture.height; y += 1) {
      for (let x = 0; x < aperture.width; x += 1) {
        const value = field.density[y * aperture.width + x]
        if (Math.abs((x + 0.5) * aperture.stepMm - 10) > 4) expect(value).toBe(0)
        if (value > 0) illuminatedCount += 1
      }
    }
    expect(illuminatedCount).toBeGreaterThan(16)
    expect(illuminatedCount).toBeLessThan(aperture.width * aperture.height / 2)
    expect(field.totalDepositedEnergy).toBeCloseTo(1, 12)
  })

  it('keeps opposing grazing directions in separate bins instead of averaging toward the normal', () => {
    const field = buildLeakageApertureField(mask(10, 2), [
      sample(0, 5.5, 0.5, 1, [0.995, 0, 0.1]),
      sample(1, 5.5, 0.5, 1, [-0.995, 0, 0.1]),
    ])
    const litBins = field.bins.filter((bin) => bin.totalEnergy > 0)
    expect(litBins).toHaveLength(2)
    expect(litBins.map((bin) => bin.id)).toEqual([1, 5])
    expect(litBins[0].direction[0]).toBeGreaterThan(0.99)
    expect(litBins[1].direction[0]).toBeLessThan(-0.99)
    expect(field.totalDepositedEnergy).toBeCloseTo(2, 12)
  })

  it('accounts only for its own unambiguous face and refuses inward rays', () => {
    const wrongFace = { ...sample(0, 2.5, 0.5, 7), exitFaces: ['z_min'] as const }
    const ambiguous = { ...sample(1, 2.5, 0.5, 5), exitFaces: ['x_max', 'z_max'] as const }
    const field = buildLeakageApertureField(mask(5, 2), [
      { ...wrongFace, exitFaces: [...wrongFace.exitFaces] },
      { ...ambiguous, exitFaces: [...ambiguous.exitFaces] },
      sample(2, 2.5, 0.5, 3, [0, 0, -1]),
      sample(3, 2.5, 0.5, 2),
    ])
    expect(field.totalInputEnergy).toBe(5)
    expect(field.totalDepositedEnergy).toBeCloseTo(2, 12)
    expect([...field.usedSampleKeys]).toEqual(['field:3'])
    expect(field.rejected).toEqual([{ key: 'field:2', reason: 'inward_direction', weight: 3 }])
  })

  it('uses the same physical grid coordinates on an x-min aperture', () => {
    const aperture = { ...mask(12, 2, 0.25), face: 'x_min' as const, origin: [30, 40] as [number, number] }
    const field = buildLeakageApertureField(aperture, [{
      ...sample(0, 0, 0), exitPoint: [10, 31.5, 40.25],
      exitFaces: ['x_min'], outgoingDirection: [-1, 0, 0],
    }])
    expect(field.usedSampleKeys.size).toBe(1)
    expect(field.bins[0].direction).toEqual([-1, 0, 0])
    expect(field.totalDepositedEnergy).toBeCloseTo(1, 12)
  })

  it('retains boundary-cell energy for fallback rather than moving it across the mask', () => {
    const aperture = mask(5, 3)
    aperture.open[7] = 0
    aperture.componentIds[7] = -1
    const field = buildLeakageApertureField(aperture, [sample(0, 2.01, 1.01, 2)])
    expect(field.usedSampleKeys.size).toBe(0)
    expect(field.totalInputEnergy).toBe(2)
    expect(field.totalDepositedEnergy).toBe(0)
    expect(field.rejected).toEqual([{ key: 'field:0', reason: 'closed_mask_cell', weight: 2 }])
  })

  it('fails the entire field on resource exhaustion without leaving partially used samples', () => {
    const aperture = mask(100, 2, 0.25)
    const samples = [sample(0, 2, 0.25), sample(1, 20, 0.25)]
    const workLimited = buildLeakageApertureField(aperture, samples, { maxVisitedCells: 50 })
    expect(workLimited).toMatchObject({ status: 'unsupported', reason: 'work_budget_exceeded', totalDepositedEnergy: 0 })
    expect(workLimited.usedSampleKeys.size).toBe(0)
    expect(workLimited.density.length).toBe(0)
    expect(buildLeakageApertureField(aperture, samples, { maxCells: 10 }).reason).toBe('cell_budget_exceeded')
    expect(buildLeakageApertureField(aperture, samples, { maxSamples: 1 }).reason).toBe('sample_budget_exceeded')
  })

  it('converges under spatial sampling refinement at fixed total flux', () => {
    const aperture = mask(160, 4, 0.125)
    const sparse = buildLeakageApertureField(aperture, uniformSlotSamples(10))
    const medium = buildLeakageApertureField(aperture, uniformSlotSamples(40))
    const dense = buildLeakageApertureField(aperture, uniformSlotSamples(160))
    const difference = (left: Float64Array, right: Float64Array) => {
      let error = 0
      // Exclude slot ends, where a bounded normalized kernel redistributes flux.
      for (let y = 0; y < aperture.height; y += 1) {
        for (let x = 40; x < 120; x += 1) {
          error += Math.abs(left[y * aperture.width + x] - right[y * aperture.width + x]) * aperture.stepMm ** 2
        }
      }
      return error
    }
    expect(sparse.totalDepositedEnergy).toBeCloseTo(1, 12)
    expect(medium.totalDepositedEnergy).toBeCloseTo(1, 12)
    expect(dense.totalDepositedEnergy).toBeCloseTo(1, 12)
    expect(difference(medium.density, dense.density)).toBeLessThan(difference(sparse.density, dense.density))
    expect(difference(medium.density, dense.density)).toBeLessThan(0.025)
  })
  it('keeps angular assignments and density unchanged when observation order changes', () => {
    const aperture = mask(80, 2, 0.25)
    const samples = uniformSlotSamples(30).map((item, index) => ({
      ...item, weight: (index + 1) / 465,
      outgoingDirection: [Math.cos(index), Math.sin(index), 0.5] as Vec3,
    }))
    const forward = buildLeakageApertureField(aperture, samples)
    const reversed = buildLeakageApertureField(aperture, [...samples].reverse())
    expectDensitiesClose(reversed.density, forward.density)
    for (const bin of forward.bins) {
      expectDensitiesClose(reversed.bins[bin.id].density, bin.density)
      bin.direction.forEach((value, axis) => expect(reversed.bins[bin.id].direction[axis]).toBeCloseTo(value, 12))
    }
  })

  it('keeps physical flux and nearly the same slot profile when the grid is refined', () => {
    const samples = uniformSlotSamples(40)
    const coarse = buildLeakageApertureField(mask(80, 2, 0.25), samples)
    const fine = buildLeakageApertureField(mask(160, 4, 0.125), samples)
    let profileDifference = 0
    for (let y = 0; y < 2; y += 1) {
      for (let x = 0; x < 80; x += 1) {
        const fineMean = (
          fine.density[2 * y * 160 + 2 * x] + fine.density[2 * y * 160 + 2 * x + 1] +
          fine.density[(2 * y + 1) * 160 + 2 * x] + fine.density[(2 * y + 1) * 160 + 2 * x + 1]
        ) / 4
        profileDifference += Math.abs(coarse.density[y * 80 + x] - fineMean) * 0.25 ** 2
      }
    }
    expect(coarse.totalDepositedEnergy).toBeCloseTo(1, 12)
    expect(fine.totalDepositedEnergy).toBeCloseTo(1, 12)
    expect(profileDifference).toBeLessThan(0.025)
  })

})
