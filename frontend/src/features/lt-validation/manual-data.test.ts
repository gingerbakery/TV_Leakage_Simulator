import { describe, expect, it } from 'vitest'
import { buildManualDataset, createManualDraft, mapExtractedFlux } from './manual-data'

describe('LT manual raw data', () => {
  it('keeps unknown physical conditions and raw validity unknown', () => {
    const dataset = buildManualDataset(createManualDraft(null, undefined), null, null)
    expect(dataset.sources).toEqual([])
    expect(dataset.receivers[0]).toMatchObject({ raw: null, quantity: 'unknown', flux_lm: null, total_flux_lm: null, center_mm: null, u_axis: null, v_axis: null, normal: null, acceptance_angle_deg: null, aperture: null })
  })

  it('reorders coordinate-labelled data by physical U/V labels, including descending rows and columns', () => {
    const draft = { ...createManualDraft(null, undefined), raw: 'raw' as const, unit: 'flux_lm' as const, tableFormat: 'coordinates' as const, uEdges: '0, 1, 2', vEdges: '0, 1, 2', matrix: 'Y\\X\t1.5\t0.5\n1.5\t4\t3\n0.5\t2\t1', total: '10' }
    const result = buildManualDataset(draft, null, null)
    expect(result.receivers[0].flux_lm).toEqual([[1, 2], [3, 4]])
    expect(result.receivers[0].provenance?.coordinate_order_confirmed).toBe(true)
    expect(() => buildManualDataset({ ...draft, matrix: 'Y\\X 1.4 0.5\n1.5 4 3\n0.5 2 1' }, null, null)).toThrow('중심좌표')
  })

  it('converts illuminance using each nonuniform cell area in square metres', () => {
    const result = buildManualDataset({ ...createManualDraft(null, undefined), unit: 'illuminance_lx', coordinateOrderConfirmed: true, matrix: '100 100\n100 100', uEdges: '0 1 3', vEdges: '0 2 3', total: '0.0009' }, null, null)
    expect(result.receivers[0].flux_lm).toEqual([[0.0002, 0.0004], [0.0001, 0.0002]])
    expect(result.receivers[0].raw).toBeNull()
  })

  it('does not silently transpose, flip, normalize, or repair invalid unlabeled inputs', () => {
    const draft = { ...createManualDraft(null, undefined), unit: 'flux_lm' as const, matrix: '1 2\n3 4', uEdges: '0 1 2', vEdges: '0 1 2', total: '1000' }
    expect(() => buildManualDataset(draft, null, null)).toThrow('좌표 순서')
    const confirmed = { ...draft, coordinateOrderConfirmed: true }
    const result = buildManualDataset(confirmed, null, null).receivers[0]
    expect(result.flux_lm).toEqual([[1, 2], [3, 4]])
    expect(result.total_flux_lm).toBe(1000)
    expect(() => buildManualDataset({ ...confirmed, matrix: '1 2 3 4' }, null, null)).toThrow('2행 × U 2열')
    expect(() => buildManualDataset({ ...confirmed, matrix: '1 -2\n3 4' }, null, null)).toThrow('음수')
    expect(() => buildManualDataset({ ...confirmed, matrix: '1 NaN\n3 4' }, null, null)).toThrow('유한한 숫자')
    expect(() => buildManualDataset({ ...confirmed, matrix: '1,2,\n3,4,' }, null, null)).toThrow('빈 셀')
    expect(() => buildManualDataset({ ...confirmed, center: '1,0,' }, null, null)).toThrow('빈 셀')
    expect(() => buildManualDataset({ ...confirmed, matrix: '1\t2\n3\t4\t' }, null, null)).toThrow('빈 TSV')
    expect(() => buildManualDataset({ ...confirmed, matrix: '\t1\t2\n3\t4' }, null, null)).toThrow('빈 TSV')
    expect(() => buildManualDataset({ ...confirmed, matrix: '1\t\t2\t3\n4\t5\t6', uEdges: '0 1 2 3' }, null, null)).toThrow('빈 TSV')
    expect(buildManualDataset({ ...confirmed, matrix: '  1   2\n  3  4  ' }, null, null).receivers[0].flux_lm).toEqual([[1, 2], [3, 4]])
  })

  it('requires every extracted ordering choice and maps only the declared order', () => {
    expect(() => mapExtractedFlux([1, 2, 3, 4], '0 1 2', '0 1 2', '', '', '', [2, 2])).toThrow('모두 확인')
    expect(mapExtractedFlux([4, 2, 3, 1], '0 1 2', '0 1 2', 'V', 'descending', 'descending', [2, 2])).toEqual([[1, 2], [3, 4]])
    expect(() => mapExtractedFlux([1, 2, 3], '0 1 2', '0 1 2', 'U', 'ascending', 'ascending', [2, 2])).toThrow('추출 값')
    expect(() => mapExtractedFlux([1, 2, 3, 4], '0 1 2 3 4', '0 1', 'U', 'ascending', 'ascending', [2, 2])).toThrow('원본 mesh')
    expect(() => mapExtractedFlux([1, 2, 3, 4], '0 1 2', '0 1 2', 'U', 'ascending', 'ascending', undefined)).toThrow('원본 mesh')
  })

  it('validates original LTS dimensions and cells even when directly editing the table', () => {
    const original = buildManualDataset(createManualDraft(null, undefined), null, null)
    const receiver = original.receivers[0]
    receiver.provenance = { raw_dimensions: [2, 2], raw_flux_lm_flat: [1, 2, 3, 4] }
    const draft = { ...createManualDraft(original, receiver), unit: 'flux_lm' as const, coordinateOrderConfirmed: true, uEdges: '0 1 2', vEdges: '0 1 2', matrix: '1 2\n3 4', total: '10', extractedOrder: 'fast=U;U=ascending;V=ascending' }
    const mapped = buildManualDataset(draft, original, receiver.id)
    expect(mapped.receivers[0].flux_lm).toEqual([[1, 2], [3, 4]])
    expect(createManualDraft(mapped, mapped.receivers[0]).extractedOrder).toBe(draft.extractedOrder)
    expect(() => buildManualDataset({ ...draft, uEdges: '0 1 2 3 4', vEdges: '0 1', matrix: '1 2 3 4' }, original, receiver.id)).toThrow('원본 mesh')
    expect(() => buildManualDataset({ ...draft, matrix: '2 1\n3 4' }, original, receiver.id)).toThrow('원시값')
    expect(() => buildManualDataset({ ...draft, extractedOrder: undefined }, original, receiver.id)).toThrow('저장 순서')
    delete receiver.provenance.raw_flux_lm_flat
    expect(() => buildManualDataset({ ...draft, matrix: '2 1\n3 4' }, original, receiver.id)).toThrow('원시 배열')
  })

  it('normalizes structured source fields and keeps unentered source/Aim fields unknown', () => {
    const draft = createManualDraft(null, undefined)
    draft.sources = JSON.stringify([{ id: 's1', name: 'Lamp', total_flux_lm: '0.5', center_mm: '4.5, 0, 0', aim: { width_mm: '2', center_mm: '' } }])
    const result = buildManualDataset(draft, null, null)
    expect(result.sources[0]).toMatchObject({ total_flux_lm: 0.5, center_mm: [4.5, 0, 0], flux_reference: null, normal: null, aim: { center_mm: null, width_mm: 2, height_mm: null } })
  })

  it('does not relabel known radiometric input by changing the manual unit selection', () => {
    const source = buildManualDataset(createManualDraft(null, undefined), null, null)
    source.receivers[0].quantity = 'radiometric'
    const draft = { ...createManualDraft(source, source.receivers[0]), unit: 'flux_lm' as const }
    expect(buildManualDataset(draft, source, source.receivers[0].id).receivers[0].quantity).toBe('radiometric')
  })
})
