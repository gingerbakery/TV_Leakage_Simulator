#!/usr/bin/env node
/**
 * Package the synthetic right-side seam STEP/setup fixtures as settings-only
 * BITSAM projects. A fresh live Run binds the result to the imported Case.
 * No optical conditions need to be re-entered in the UI.
 *
 * node scripts/side_seam_project_helper.mjs
 */
import { readFile, writeFile } from 'node:fs/promises'
import { resolve, basename } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from '../frontend/node_modules/vite/dist/node/index.js'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const directory = resolve(root, 'samples/side_seam')
const cases = ['closed', 'gap_0p1', 'gap_0p3', 'gap_0p5']
const server = await createServer({ root: resolve(root, 'frontend'), server: { middlewareMode: true }, appType: 'custom' })
try {
const { createClosedEnclosureProject } = await server.ssrLoadModule(resolve(root, 'scripts/closed_enclosure_project_helper.mjs'))
for (const name of cases) {
  const scene = JSON.parse(await readFile(resolve(directory, name + '.scene.json'), 'utf8'))
  const request = JSON.parse(await readFile(resolve(directory, name + '.request.json'), 'utf8'))
  const { source, project } = createClosedEnclosureProject({
    scene, request, cadName: name + '.step',
  })
  if (project.analysis_result) throw new Error('A sample preset must require its own live result binding')
  const output = resolve(directory, name + '.bitsam')
  await writeFile(output, source, 'utf8')
  console.log(JSON.stringify({
    preset: basename(output), rays: project.workspace.rayTraceConfig.ray_count,
    compute_backend: project.workspace.rayTraceConfig.compute_backend,
    settings_only: true, optical_conditions_preconfigured: true,
  }))
}

} finally { await server.close() }
