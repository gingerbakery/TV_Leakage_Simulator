import type { BsdfAsset } from '@/features/materials/bsdf-asset'

/** Synthetic parser-response fixture; not a measured material. */
export function createBsdfFixture(): BsdfAsset {
  return {
    id: `bsdf-${'a'.repeat(64)}`, format: 'lighttools-ascii-bsdf-v1', parserVersion: 1,
    simulationReady: false, warnings: ['Synthetic test data'],
    files: [{ name: 'master.txt', text: '#UDOP AngGridMultiple 1.0\n' }, { name: 'grid.txt', text: 'Synthetic grid' }, { name: 'rt.txt', text: 'DFAT Version 1.0\n' }],
    data: { symmetry: 'unspecified', meshFormat: 'polar', valueType: 'bsdf_relative', refractiveIndex: 1,
      wavelengthsNm: [550], incidenceAnglesDeg: [0],
      grids: [{ fileName: 'grid.txt', wavelengthNm: 550, incidenceAngleDeg: 0, planeOfIncidenceDeg: 0, side: 'front',
        longitudesDeg: [0, 180, 360], latitudesDeg: [90, 0], values: [[0, 0, 0], [1, 1, 1]] }],
      rt: [{ wavelengthNm: 550, incidenceAngleDeg: 0, reflectance: 0.3, transmittance: 0 }],
    },
  }
}
