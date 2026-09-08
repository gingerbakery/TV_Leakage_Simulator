import { createServer } from '../frontend/node_modules/vite/dist/node/index.js'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
const root = resolve(import.meta.dirname, '..')
const baseUrl = process.argv[2] ?? 'http://127.0.0.1:8788'
const outputDirectory = resolve(root, 'outputs/horizontal-seam-demo-validation')
const vite = await createServer({ root: resolve(root, 'frontend'), server: { middlewareMode: true }, appType: 'custom' })
let report = { generatedAtUtc: new Date().toISOString(), baseUrl, passed: false, checks: [], steps: [] }
const check = (name, passed, detail) => { report.checks.push({ name, passed: Boolean(passed), detail }); if (!passed) throw Error(name) }
try {
  await mkdir(outputDirectory, { recursive: true })
  const [{ createApiClient }, { parseBitsamProject, compareBitsamProjectScene }, { buildRayTraceRequest }, context, previewModule, surfaceModule, { createWorkspaceStore }] = await Promise.all([
    vite.ssrLoadModule('/src/api/client.ts'),
    vite.ssrLoadModule('/src/features/projects/bitsam-project.ts'),
    vite.ssrLoadModule('/src/features/raytracing/ray-tracing-model.ts'),
    vite.ssrLoadModule('/src/features/raytracing/ray-result-source-context.ts'),
    vite.ssrLoadModule('/src/features/results/leakage-preview-data.ts'),
    vite.ssrLoadModule('/src/features/results/leakage-surface-preview.ts'),
    vite.ssrLoadModule('/src/stores/workspace-store.ts'),
  ])
  const api = createApiClient({ baseUrl })
  const manifestResponse = await fetch(baseUrl + '/outputs/horizontal-seam-demo.json')
  check('manifest HTTP 200', manifestResponse.status === 200)
  const manifest = await manifestResponse.json()
  check('explicit horizontal demo schema', manifest.schema === 'horizontal-seam-demo.v1')
  const entry = manifest.cases.find(item => item.id === '16x')
  check('16 lm real trace fixture exists', entry?.sourceLumen === 16 && manifest.requiresRealTrace === true)
  const [cadResponse, projectResponse] = await Promise.all([fetch(baseUrl + entry.cadUrl), fetch(baseUrl + entry.projectUrl)])
  check('CAD and project files available', cadResponse.status === 200 && projectResponse.status === 200)
  const project = parseBitsamProject(await projectResponse.text())
  check('project is settings-only', project.analysis_result == null)
  const uploaded = await api.uploadCad(await cadResponse.blob(), entry.cadName)
  report.upload = uploaded
  const cad = { path: uploaded.path, displayName: entry.cadName }
  // upload's exact response property is checked before requesting a scene.
  check('upload yields server CAD path', typeof cad.path === 'string' && cad.path.length > 0, uploaded)
  const scene = await api.getScene(cad.path)
  const compatibility = compareBitsamProjectScene(project, scene, cad)
  check('imported CAD matches project geometry', compatibility.compatible, compatibility)
  const store = createWorkspaceStore()
  store.getState().actions.addCadCase(cad)
  store.getState().actions.restoreProjectState(project.workspace)
  const state = store.getState()
  const request = buildRayTraceRequest({ scene, projectName: cad.displayName,
    emitters: state.emitters, receivers: state.receivers, materialAssignments: state.materialAssignments,
    transformRules: state.transformRules, excludedComponentIds: state.excludedComponentIds,
    deletedComponentIds: state.deletedComponentIds, roiScopes: state.roiScopes, config: state.rayTraceConfig })
  check('request remains CPU with 500k rays and 16 lm', request.config.compute_backend === 'cpu' && request.config.ray_count === 500000 &&
    request.emitters.reduce((sum,item) => sum + item.power_lumen,0) === 16)
  const source = context.createRayTraceResultSourceContext(scene, request, state.activeCadCaseId)
  const startedAt = performance.now()
  const reusedJobId = process.argv[3]
  let job = reusedJobId ? await api.getRayTraceJob(reusedJobId) : await api.startRayTrace(request)
  report.reusedCompletedJob = reusedJobId ?? null
  if (reusedJobId) report.replayScope = 'Post-fix reconstruction reuses the prior completed job. The original API pipeline passed import, request/source-context binding and full capture before reconstruction failed. This replay binds the same served CAD and settings to a fresh equivalent scene for local reconstruction only; it does not claim a newly submitted job.'
  report.jobId = job.job_id
  report.steps.push({ phase: reusedJobId ? 'prior completed CPU job reused' : 'real CPU job submitted', at: new Date().toISOString(), jobId: job.job_id })
  console.log(JSON.stringify(report.steps.at(-1)))
  for (let count=0; count < 600 && !['completed', 'failed', 'cancelled'].includes(job.status); count++) {
    await delay(500)
    job = await api.getRayTraceJob(job.job_id)
  }
  check('real CPU job completed', job.status === 'completed' && Boolean(job.result), { status: job.status, error: job.error })
  report.traceMs = reusedJobId ? null : performance.now() - startedAt
  report.actualJobElapsedSeconds = job.elapsed_sec
  const result = context.attachRayTraceResultSourceContext(job.result, source)
  store.getState().actions.setActiveCadCaseResult(result)
  const saved = store.getState().cadCases.find(item => item.caseId === state.activeCadCaseId)?.latestResult
  check(reusedJobId ? 'latest case and equivalent served scene match for replay' : 'latest case result and launch-time source match', saved?.run_id === result.run_id &&
    saved.source_context.cad_case_id === state.activeCadCaseId && saved.source_context.scene.mesh_signature === context.createRayTraceSceneMeshSignature(scene))
  await writeFile(resolve(outputDirectory,'scene.json'),JSON.stringify(scene))
  await writeFile(resolve(outputDirectory,'request.json'),JSON.stringify(request,null,2))
  await writeFile(resolve(outputDirectory,'bound-result.json'),JSON.stringify(saved,null,2))
  const preview = previewModule.buildPrototypeLeakagePreviewData(saved, scene)
  const surface = surfaceModule.buildLeakageSurfacePreview(scene, preview, request)
  check('stored receiver paths support 3D result', preview.status === 'ready' && preview.coverage.fullCapture && preview.samples.length > 0,
    { status: preview.status, reason: preview.reason, sampleCount: preview.samples.length, coverage: preview.coverage })
  let areaMm2=0, litAreaMm2=0, invalidCells=0, integratedFluxLumen=0, acceptedFluxLumen=0
  for (const field of surface.fields) {
    const mask=field.mask, area=mask.stepMm**2
    acceptedFluxLumen += field.totalDepositedEnergy
    for (let index=0;index<mask.open.length;index++) {
      integratedFluxLumen += field.density[index]*area
      if (!mask.open[index]) { if(field.density[index]!==0) invalidCells++; continue }
      areaMm2 += area
      if(field.density[index]>0) litAreaMm2+=area
      const y=mask.origin[0]+index%mask.width*mask.stepMm
      const z=mask.origin[1]+Math.floor(index/mask.width)*mask.stepMm
      if(mask.face!=='x_max'||Math.abs(mask.plane-Math.fround(120.4))>1e-10||y<8-1e-7||y+mask.stepMm>8.3+1e-7||z<2-1e-7||z+mask.stepMm>14+1e-7) invalidCells++
    }
  }
  check('actual horizontal gap field is ready without points', surface.fields.length===1 && surface.reconstructedSampleCount===preview.samples.length && surface.fallbackSamples.length===0)
  check('continuous field fills only 0.3 by 12 mm aperture', Math.abs(areaMm2-3.6)<1e-7 && Math.abs(litAreaMm2-3.6)<1e-7 && invalidCells===0,{areaMm2,litAreaMm2,invalidCells})
  check('field conserves actual ray flux', Math.abs(integratedFluxLumen/acceptedFluxLumen-1)<1e-10,{integratedFluxLumen,acceptedFluxLumen})
  report.result={ runId:result.run_id, rays:result.total_rays, hits:result.receiver_hit_count, samples:preview.samples.length,
    fields:surface.fields.length, areaMm2,litAreaMm2, integratedFluxLumen, meshSignature:source.scene.mesh_signature,
    computeExecutionState:result.compute_execution_state }
  report.scope='Actual served files, CAD import, settings restoration, CPU job and production 3D data pipeline verified. Browser navigation, WebGL rendering and visible camera framing were not observed.'
  report.passed=true
  await writeFile(resolve(outputDirectory,'request.json'),JSON.stringify(request,null,2))
  await writeFile(resolve(outputDirectory,'bound-result.json'),JSON.stringify(result,null,2))
  console.log(JSON.stringify(report,null,2))
} catch(error) {
  report.error=String(error?.stack??error)
  console.error(report.error)
  process.exitCode=1
} finally {
  await writeFile(resolve(outputDirectory,'validation.json'),JSON.stringify(report,null,2))
  await vite.close()
}
