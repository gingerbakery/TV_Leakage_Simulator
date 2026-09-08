#!/usr/bin/env node
/** Stage only the synthetic horizontal-seam demo fixtures for the existing
 * local /outputs/{basename} endpoint. No saved result or backend mutation.
 */
import { readFile, copyFile, writeFile, mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
const root = resolve(import.meta.dirname, '..')
const source = resolve(root, 'samples/side_seam_horizontal')
const output = resolve(root, 'outputs')
await mkdir(output, { recursive: true })
const cases = []
for (const [id, power] of [['off', 0], ['1x', 1], ['4x', 4], ['16x', 16]]) {
  const stem = 'horizontal_gap_0p3_' + id
  const outputStem = 'demo-horizontal-gap-0p3-' + id
  const project = JSON.parse(await readFile(resolve(source, stem + '.bitsam'), 'utf8'))
  if (project.analysis_result) throw new Error('Demo must require a fresh real trace')
  if (project.workspace.rayTraceConfig.compute_backend !== 'cpu') throw new Error('Demo must remain CPU')
  if (project.workspace.emitters[0].power_lumen !== power) throw new Error('Source-power mismatch')
  for (const suffix of ['step', 'bitsam']) {
    await copyFile(resolve(source, stem + '.' + suffix), resolve(output, outputStem + '.' + suffix))
  }
  cases.push({
    id, label: power === 0 ? '광원 끔 · 0 lm' : '광원 ' + power + ' lm',
    sourceLumen: power,
    cadName: stem + '.step',
    cadUrl: '/outputs/' + outputStem + '.step',
    projectUrl: '/outputs/' + outputStem + '.bitsam',
  })
}
const manifest = {
  schema: 'horizontal-seam-demo.v1',
  title: 'TV 오른쪽 측면 가로 틈',
  description: '0.3 mm 높이 × 12 mm 길이의 실제 가로 틈을 가진 밀폐 개발용 모델',
  defaultCase: '16x',
  gapMm: 0.3,
  lengthMm: 12,
  orientation: 'right',
  computeBackend: 'cpu',
  calibratedAppearance: false,
  requiresRealTrace: true,
  cases,
}
await writeFile(resolve(output, 'horizontal-seam-demo.json'), JSON.stringify(manifest, null, 2), 'utf8')
console.log(JSON.stringify(manifest))
