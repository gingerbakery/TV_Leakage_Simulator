import { createServer } from '../frontend/node_modules/vite/dist/node/index.js'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const sampleDirectory = resolve(process.argv[2] ?? resolve(root, 'samples/side_seam'))
const outputDirectory = resolve(process.argv[3] ?? resolve(root, 'outputs/side-seam-validation'))
const baselineDirectory = resolve(process.argv[4] ?? resolve(root, 'outputs/side-seam-ray-density-validation/100k'))
const cases = [
  { name: 'closed', gapMm: 0 },
  { name: 'gap_0p1', gapMm: 0.1 },
  { name: 'gap_0p3', gapMm: 0.3 },
  { name: 'gap_0p5', gapMm: 0.5 },
]
// These independent design dimensions come from the synthetic fixture contract.
// The geometry extractor receives only the imported scene and ray samples.
const design = { face: 'x_max', planeMm: 120.4, yMinimumMm: 8, yMaximumMm: 28, zMinimumMm: 12 }
const geometryToleranceMm = 1e-7
const readJson = async (path) => JSON.parse(await readFile(path, 'utf8'))
const quantile = (sorted, fraction) => sorted.length ? sorted[Math.floor((sorted.length - 1) * fraction)] : 0
const countReasons = (items) => {
  const counts = {}
  for (const item of items) counts[item.reason] = (counts[item.reason] ?? 0) + 1
  return counts
}
const server = await createServer({
  root: resolve(root, 'frontend'),
  server: { middlewareMode: true },
  appType: 'custom',
})

try {
  await mkdir(outputDirectory, { recursive: true })
  const { createRayTraceResultSourceContext } = await server.ssrLoadModule('/src/features/raytracing/ray-result-source-context.ts')
  const { buildPrototypeLeakagePreviewData } = await server.ssrLoadModule('/src/features/results/leakage-preview-data.ts')
  const { buildLeakageSurfacePreview } = await server.ssrLoadModule('/src/features/results/leakage-surface-preview.ts')
  const { buildLeakageApertureMasks } = await server.ssrLoadModule('/src/features/results/leakage-aperture-mask.ts')
  const { buildLeakageApertureField, leakageApertureFieldDefaults } = await server.ssrLoadModule('/src/features/results/leakage-aperture-field.ts')
  const sourceManifest = await readJson(resolve(sampleDirectory, 'validation.json'))
  const rows = []
  const currentFields = new Map()
  const assertions = []
  const check = (caseName, requirement, passed, details) => {
    assertions.push({ caseName, requirement, passed: Boolean(passed), details })
  }

  for (const { name, gapMm } of cases) {
    const scene = await readJson(resolve(sampleDirectory, name + '.scene.json'))
    const request = await readJson(resolve(sampleDirectory, name + '.request.json'))
    const result = await readJson(resolve(sampleDirectory, name + '.result.json'))
    const manifestCase = sourceManifest.cases.find((item) => item.case === name)
    check(name, 'result matches completed fixture manifest', manifestCase?.ray_count === result.total_rays, { manifestRays: manifestCase?.ray_count, resultRays: result.total_rays })
    result.source_context = createRayTraceResultSourceContext(scene, request, 'side-seam-validation:' + name)
    const start = performance.now()
    const preview = buildPrototypeLeakagePreviewData(result, scene)
    const rendered = buildLeakageSurfacePreview(scene, preview, request)
    const reconstructionMs = performance.now() - start
    check(name, 'source-bound preview available', preview.status === 'ready', preview.status === 'ready' ? null : preview.reason)
    check(name, 'all receiver hits captured', preview.status === 'ready' && preview.coverage.fullCapture,
      preview.status === 'ready' ? preview.coverage : null)
    const fields = rendered.fields
    currentFields.set(name, fields)
    let fieldDiagnostics = []
    if (gapMm > 0 && fields.length === 0 && preview.status === 'ready') {
      const masks = buildLeakageApertureMasks(scene, preview.bounds, preview.samples)
      fieldDiagnostics = masks.masks.map((mask) => {
        const field = buildLeakageApertureField(mask, preview.samples)
        return { face: mask.face, gridCells: mask.open.length, stepMm: mask.stepMm,
          status: field.status, reason: field.reason, visitedCells: field.visitedCellCount,
          rejectedReasons: countReasons(field.rejected) }
      })
      if (masks.reason) fieldDiagnostics.push({ maskReason: masks.reason })
    }
    const fieldRows = fields.map((field) => {
      const { mask, density } = field
      const cellArea = mask.stepMm ** 2
      let openCellCount = 0
      let litCellCount = 0
      let outsideDesignOpenCells = 0
      let nonfiniteOrNegativeCells = 0
      let blockedIlluminatedCells = 0
      let independentIntegral = 0
      const positive = []
      for (let index = 0; index < density.length; index += 1) {
        const value = density[index]
        if (!Number.isFinite(value) || value < 0) nonfiniteOrNegativeCells += 1
        independentIntegral += value * cellArea
        if (!mask.open[index]) {
          if (value !== 0 || field.bins.some((bin) => bin.density[index] !== 0)) blockedIlluminatedCells += 1
          continue
        }
        openCellCount += 1
        const lowerY = mask.origin[0] + index % mask.width * mask.stepMm
        const lowerZ = mask.origin[1] + Math.floor(index / mask.width) * mask.stepMm
        if (mask.face !== design.face || Math.abs(mask.plane - design.planeMm) > geometryToleranceMm ||
          lowerY < design.yMinimumMm - geometryToleranceMm ||
          lowerY + mask.stepMm > design.yMaximumMm + geometryToleranceMm ||
          lowerZ < design.zMinimumMm - geometryToleranceMm ||
          lowerZ + mask.stepMm > design.zMinimumMm + gapMm + geometryToleranceMm) {
          outsideDesignOpenCells += 1
        }
        if (value > 0) { litCellCount += 1; positive.push(value) }
      }
      positive.sort((a, b) => a - b)
      const acceptedEnergy = preview.samples.reduce((sum, sample) =>
        sum + (field.usedSampleKeys.has(sample.runId + ':' + sample.pathIndex) ? sample.weight : 0), 0)
      const relativeConservationError = acceptedEnergy > 0 ? Math.abs(independentIntegral - acceptedEnergy) / acceptedEnergy : Math.abs(independentIntegral)
      return {
        face: mask.face, planeMm: mask.plane, stepMm: mask.stepMm,
        widthCells: mask.width, heightCells: mask.height, gridCellCount: mask.open.length,
        openCellCount, litCellCount, openAreaMm2: openCellCount * cellArea,
        positiveAreaMm2: litCellCount * cellArea,
        litFraction: openCellCount ? litCellCount / openCellCount : 0,
        usedSampleCount: field.usedSampleKeys.size,
        rejectedSampleCount: field.rejected.length, rejectedReasons: countReasons(field.rejected),
        inputSampleEnergyLumen: field.totalInputEnergy, acceptedSampleEnergyLumen: acceptedEnergy,
        depositedSampleEnergyLumen: field.totalDepositedEnergy,
        independentIntegralLumen: independentIntegral, relativeConservationError,
        nonfiniteOrNegativeCells, blockedIlluminatedCells, outsideDesignOpenCells,
        visitedCellCount: field.visitedCellCount,
        densityLumenPerMm2: { minimumPositive: positive[0] ?? 0, p05: quantile(positive, 0.05),
          median: quantile(positive, 0.5), p95: quantile(positive, 0.95), maximum: positive.at(-1) ?? 0 },
      }
    })
    const expectedAreaMm2 = (design.yMaximumMm - design.yMinimumMm) * gapMm
    const representedAreaMm2 = fieldRows.reduce((sum, row) => sum + row.openAreaMm2, 0)
    const positiveAreaMm2 = fieldRows.reduce((sum, row) => sum + row.positiveAreaMm2, 0)
    const sampleCount = preview.samples.length
    const row = {
      name, gapMm, expectedAreaMm2, representedAreaMm2, positiveAreaMm2,
      areaFraction: expectedAreaMm2 ? representedAreaMm2 / expectedAreaMm2 : 0,
      litAreaFractionOfDesign: expectedAreaMm2 ? positiveAreaMm2 / expectedAreaMm2 : 0,
      rays: result.total_rays, receiverHits: result.receiver_hit_count,
      traceElapsedSeconds: manifestCase?.elapsed_seconds ?? null,
      computeBackend: request.compute_backend, computeExecutionState: result.compute_execution_state,
      fullCapture: preview.status === 'ready' ? preview.coverage.fullCapture : false,
      sampleCount, reconstructedSampleCount: rendered.reconstructedSampleCount,
      fallbackSampleCount: rendered.fallbackSamples.length,
      sampleSupportFraction: sampleCount ? rendered.reconstructedSampleCount / sampleCount : 0,
      extractionFaces: [...new Set(preview.samples.flatMap((sample) => sample.exitFaces))],
      reconstructionMs, reason: rendered.reason ?? null,
      fields: fieldRows, diagnostics: fieldDiagnostics,
    }
    rows.push(row)
    if (gapMm === 0) {
      check(name, 'closed enclosure has no receiver hits', result.receiver_hit_count === 0, result.receiver_hit_count)
      check(name, 'closed enclosure has no field or fallback light', fields.length === 0 && sampleCount === 0 && rendered.fallbackSamples.length === 0, { fields: fields.length, sampleCount })
    } else {
      check(name, 'side slit has receiver-bound observations', result.receiver_hit_count > 0, result.receiver_hit_count)
      check(name, 'all samples exit the intended side', row.extractionFaces.length === 1 && row.extractionFaces[0] === design.face, row.extractionFaces)
      check(name, 'side aperture field available', fields.length > 0 && fieldRows.every((field) => field.face === design.face), { reason: rendered.reason, fieldDiagnostics })
      check(name, 'at least 90 percent of CAD aperture area represented', row.areaFraction >= 0.9 && row.areaFraction <= 1 + 1e-7, row.areaFraction)
      check(name, 'at least 90 percent of observations reconstructed', row.sampleSupportFraction >= 0.9, row.sampleSupportFraction)
      check(name, 'at least 90 percent of aperture has local sample support', row.litAreaFractionOfDesign >= 0.9, row.litAreaFractionOfDesign)
      check(name, 'no light or open cells through metal', fieldRows.every((field) => field.blockedIlluminatedCells === 0 && field.outsideDesignOpenCells === 0), fieldRows.map((field) => ({ blockedIlluminatedCells: field.blockedIlluminatedCells, outsideDesignOpenCells: field.outsideDesignOpenCells })))
      check(name, 'nonnegative finite field conserves accepted flux', fieldRows.every((field) => field.nonfiniteOrNegativeCells === 0 && field.relativeConservationError < 1e-10), fieldRows.map((field) => field.relativeConservationError))
    }
    await writeFile(resolve(outputDirectory, name + '.bound-result.json'), JSON.stringify(result, null, 2))
  }
  const rayCountComparisons = []
  const baselineManifest = await readJson(resolve(baselineDirectory, 'validation.json')).catch((error) => {
    if (error.code === 'ENOENT') return null
    throw error
  })
  const cellMap = (fields) => {
    const map = new Map()
    for (const field of fields) {
      const { mask } = field
      for (let index = 0; index < field.density.length; index += 1) {
        if (!mask.open[index]) continue
        const key = mask.face + ':' + mask.plane.toFixed(6) + ':' +
          (mask.origin[0] + (index % mask.width + 0.5) * mask.stepMm).toFixed(6) + ':' +
          (mask.origin[1] + (Math.floor(index / mask.width) + 0.5) * mask.stepMm).toFixed(6)
        map.set(key, { density: field.density[index], area: mask.stepMm ** 2 })
      }
    }
    return map
  }
  if (baselineManifest) {
    for (const row of rows.filter((item) => item.gapMm > 0)) {
      const baselineCase = baselineManifest.cases.find((item) => item.case === row.name)
      if (!baselineCase || baselineCase.ray_count >= row.rays) continue
      const baselineScene = await readJson(resolve(baselineDirectory, row.name + '.scene.json'))
      const baselineRequest = await readJson(resolve(baselineDirectory, row.name + '.request.json'))
      const baselineResult = await readJson(resolve(baselineDirectory, row.name + '.result.json'))
      baselineResult.source_context = createRayTraceResultSourceContext(baselineScene, baselineRequest, 'side-seam-baseline:' + row.name)
      const baselinePreview = buildPrototypeLeakagePreviewData(baselineResult, baselineScene)
      const baselineSurface = buildLeakageSurfacePreview(baselineScene, baselinePreview, baselineRequest)
      const higherFields = currentFields.get(row.name)
      if (!baselineSurface.fields.length || !higherFields.length ||
        baselineSurface.fields[0].mask.stepMm !== higherFields[0].mask.stepMm) {
        rayCountComparisons.push({ name: row.name, reason: 'field_or_matching_resolution_unavailable' })
        continue
      }
      const lower = cellMap(baselineSurface.fields)
      const higher = cellMap(higherFields)
      const lowerEnergy = [...lower.values()].reduce((sum, item) => sum + item.density * item.area, 0)
      const higherEnergy = [...higher.values()].reduce((sum, item) => sum + item.density * item.area, 0)
      let rawL1 = 0
      let normalizedShapeL1 = 0
      for (const key of new Set([...lower.keys(), ...higher.keys()])) {
        const low = lower.get(key)
        const high = higher.get(key)
        const area = low?.area ?? high.area
        const lowDensity = low?.density ?? 0
        const highDensity = high?.density ?? 0
        rawL1 += Math.abs(lowDensity - highDensity) * area
        normalizedShapeL1 += Math.abs(lowDensity / lowerEnergy - highDensity / higherEnergy) * area
      }
      rayCountComparisons.push({ name: row.name, lowerRays: baselineResult.total_rays,
        higherRays: row.rays, lowerHits: baselineResult.receiver_hit_count, higherHits: row.receiverHits,
        lowerIntegratedSampleEnergyLumen: lowerEnergy, higherIntegratedSampleEnergyLumen: higherEnergy,
        higherToLowerEnergyRatio: higherEnergy / lowerEnergy,
        rawL1RelativeToHigherIntegral: rawL1 / higherEnergy, normalizedShapeL1,
        interpretation: 'Difference between two finite sample estimates; not an error against physical ground truth or a convergence certificate.' })
    }
  }
  const report = {
    generatedAtUtc: new Date().toISOString(), sourceDirectory: sampleDirectory,
    reconstruction: 'Completed CPU result only; source provenance rebound to matching local scene and request. No optical retrace in this script.',
    interpretation: 'Conservation and coverage of stored receiver-terminal sample energy, not absolute camera luminance, visual detectability or physical all-angle validation.',
    design, fieldSettings: leakageApertureFieldDefaults, passed: assertions.every((assertion) => assertion.passed),
    rows, assertions, rayCountComparisons,
  }
  await writeFile(resolve(outputDirectory, 'surface-validation.json'), JSON.stringify(report, null, 2))
  const concise = rows.map((row) => ({ name: row.name, hits: row.receiverHits, samples: row.sampleCount,
    reconstructed: row.reconstructedSampleCount, areaFraction: row.areaFraction,
    litAreaFraction: row.litAreaFractionOfDesign, reconstructionMs: row.reconstructionMs,
    reason: row.reason, diagnostics: row.diagnostics }))
  console.log(JSON.stringify({ passed: report.passed, rows: concise, failures: assertions.filter((assertion) => !assertion.passed), rayCountComparisons }, null, 2))
  if (!report.passed) process.exitCode = 1
} finally {
  await server.close()
}
