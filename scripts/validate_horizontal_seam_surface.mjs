import { createServer } from '../frontend/node_modules/vite/dist/node/index.js'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const sampleDirectory = resolve(process.argv[2] ?? resolve(root, 'samples/side_seam_horizontal'))
const outputDirectory = resolve(process.argv[3] ?? resolve(root, 'outputs/horizontal-seam-validation'))
const cases = [
  { name: 'horizontal_gap_0p3_off', gapMm: 0.3, powerLumen: 0 },
  { name: 'horizontal_gap_0p3_1x', gapMm: 0.3, powerLumen: 1 },
  { name: 'horizontal_gap_0p3_4x', gapMm: 0.3, powerLumen: 4 },
  { name: 'horizontal_gap_0p3_16x', gapMm: 0.3, powerLumen: 16 },
  { name: 'horizontal_gap_0p1_1x', gapMm: 0.1, powerLumen: 1 },
  { name: 'horizontal_gap_0p5_1x', gapMm: 0.5, powerLumen: 1 },
]
// Independent design coordinates; the app receives no fixture gap metadata.
const design = { face: 'x_max', planeMm: 120.4, yMinimumMm: 8, zMinimumMm: 2, zMaximumMm: 14 }
const toleranceMm = 1e-7
const readJson = async (path) => JSON.parse(await readFile(path, 'utf8'))
const countReasons = (items) => {
  const counts = {}
  for (const item of items) counts[item.reason] = (counts[item.reason] ?? 0) + 1
  return counts
}
const withoutPowerOrLabels = (request) => {
  const copy = structuredClone(request)
  delete copy.project_name
  delete copy.scene_token
  copy.emitters.forEach((emitter) => { delete emitter.power_lumen })
  return JSON.stringify(copy)
}
const server = await createServer({ root: resolve(root, 'frontend'), server: { middlewareMode: true }, appType: 'custom' })

try {
  await mkdir(outputDirectory, { recursive: true })
  const { createRayTraceResultSourceContext } = await server.ssrLoadModule('/src/features/raytracing/ray-result-source-context.ts')
  const { buildPrototypeLeakagePreviewData } = await server.ssrLoadModule('/src/features/results/leakage-preview-data.ts')
  const { buildLeakageSurfacePreview } = await server.ssrLoadModule('/src/features/results/leakage-surface-preview.ts')
  const { buildLeakageApertureMasks } = await server.ssrLoadModule('/src/features/results/leakage-aperture-mask.ts')
  const { buildLeakageApertureField, leakageApertureFieldDefaults } = await server.ssrLoadModule('/src/features/results/leakage-aperture-field.ts')
  const manifest = await readJson(resolve(sampleDirectory, 'validation.json'))
  const assertions = []
  const rows = []
  const records = new Map()
  const check = (name, requirement, passed, details = null) => assertions.push({ name, requirement, passed: Boolean(passed), details })

  for (const item of cases) {
    const { name, gapMm, powerLumen } = item
    const scene = await readJson(resolve(sampleDirectory, name + '.scene.json'))
    const request = await readJson(resolve(sampleDirectory, name + '.request.json'))
    const result = await readJson(resolve(sampleDirectory, name + '.result.json'))
    const fixture = manifest.cases.find((entry) => (entry.case ?? entry.name) === name)
    check(name, 'result matches completed fixture manifest', fixture?.ray_count === result.total_rays,
      { manifestRays: fixture?.ray_count, resultRays: result.total_rays })
    const sourcePower = request.emitters.filter((emitter) => emitter.enabled !== false).reduce((sum, emitter) => sum + emitter.power_lumen, 0)
    check(name, 'source power matches the comparison label', sourcePower === powerLumen, { expected: powerLumen, actual: sourcePower })
    result.source_context = createRayTraceResultSourceContext(scene, request, 'horizontal-seam-validation:' + name)
    const start = performance.now()
    const preview = buildPrototypeLeakagePreviewData(result, scene)
    const surface = buildLeakageSurfacePreview(scene, preview, request)
    const reconstructionMs = performance.now() - start
    const receiverPaths = result.stored_paths.filter((path) => path.at(-1)?.event_type === 'receiver')
    const rawPathCounts = new Map()
    let rawReceiverPathEnergyLumen = 0
    for (const path of receiverPaths) {
      const terminal = path.at(-1)
      rawPathCounts.set(terminal.receiver_id, (rawPathCounts.get(terminal.receiver_id) ?? 0) + 1)
      rawReceiverPathEnergyLumen += terminal.incoming_energy_lumen
    }
    const gridHits = new Map()
    for (const grid of result.receiver_grids) gridHits.set(grid.receiver_id, (gridHits.get(grid.receiver_id) ?? 0) + grid.hit_count)
    const rawFullCapture = receiverPaths.length === result.receiver_hit_count &&
      [...gridHits.values()].reduce((sum, value) => sum + value, 0) === result.receiver_hit_count &&
      [...new Set([...gridHits.keys(), ...rawPathCounts.keys()])].every((id) =>
        (gridHits.get(id) ?? 0) === (rawPathCounts.get(id) ?? 0))
    if (powerLumen > 0) check(name, 'source-bound preview is available', preview.status === 'ready', preview.status === 'ready' ? null : preview.reason)
    check(name, 'all receiver-terminal paths are captured', rawFullCapture &&
      (powerLumen === 0 || (preview.status === 'ready' && preview.coverage.fullCapture)),
      { rawReceiverPaths: receiverPaths.length, receiverHits: result.receiver_hit_count, previewStatus: preview.status })
    const diagnostics = []
    if (powerLumen > 0 && surface.fields.length === 0 && preview.status === 'ready') {
      const masks = buildLeakageApertureMasks(scene, preview.bounds, preview.samples)
      if (masks.reason) diagnostics.push({ reason: masks.reason })
      for (const mask of masks.masks) {
        const field = buildLeakageApertureField(mask, preview.samples)
        diagnostics.push({ face: mask.face, cells: mask.open.length, stepMm: mask.stepMm, status: field.status,
          reason: field.reason, visitedCells: field.visitedCellCount, rejectedReasons: countReasons(field.rejected) })
      }
    }
    const fieldRows = surface.fields.map((field) => {
      const { mask } = field
      const area = mask.stepMm ** 2
      let openCells = 0, litCells = 0, outsideDesignCells = 0, blockedIlluminatedCells = 0, invalidDensityCells = 0
      let integral = 0, peakDensity = 0
      let yMinimumMm = Infinity, yMaximumMm = -Infinity, zMinimumMm = Infinity, zMaximumMm = -Infinity
      for (let index = 0; index < field.density.length; index += 1) {
        const density = field.density[index]
        if (!Number.isFinite(density) || density < 0) invalidDensityCells += 1
        integral += density * area
        peakDensity = Math.max(peakDensity, density)
        if (!mask.open[index]) {
          if (density !== 0 || field.bins.some((bin) => bin.density[index] !== 0)) blockedIlluminatedCells += 1
          continue
        }
        openCells += 1
        if (density > 0) litCells += 1
        const y = mask.origin[0] + index % mask.width * mask.stepMm
        const z = mask.origin[1] + Math.floor(index / mask.width) * mask.stepMm
        yMinimumMm = Math.min(yMinimumMm, y)
        yMaximumMm = Math.max(yMaximumMm, y + mask.stepMm)
        zMinimumMm = Math.min(zMinimumMm, z)
        zMaximumMm = Math.max(zMaximumMm, z + mask.stepMm)
        if (mask.face !== design.face || Math.abs(mask.plane - design.planeMm) > toleranceMm ||
          y < design.yMinimumMm - toleranceMm || y + mask.stepMm > design.yMinimumMm + gapMm + toleranceMm ||
          z < design.zMinimumMm - toleranceMm || z + mask.stepMm > design.zMaximumMm + toleranceMm) outsideDesignCells += 1
      }
      const acceptedEnergy = preview.samples.reduce((sum, sample) => sum +
        (field.usedSampleKeys.has(sample.runId + ':' + sample.pathIndex) ? sample.weight : 0), 0)
      const relativeConservationError = acceptedEnergy > 0 ? Math.abs(integral - acceptedEnergy) / acceptedEnergy : Math.abs(integral)
      return { face: mask.face, planeMm: mask.plane, stepMm: mask.stepMm, gridCellCount: mask.open.length,
        openCells, litCells, apertureAreaMm2: openCells * area, litAreaMm2: litCells * area,
        yMinimumMm, yMaximumMm, zMinimumMm, zMaximumMm,
        ySpanMm: yMaximumMm - yMinimumMm, zSpanMm: zMaximumMm - zMinimumMm,
        acceptedSampleCount: field.usedSampleKeys.size, rejectedSampleCount: field.rejected.length,
        rejectedReasons: countReasons(field.rejected), inputSampleEnergyLumen: field.totalInputEnergy,
        acceptedSampleEnergyLumen: acceptedEnergy, depositedSampleEnergyLumen: field.totalDepositedEnergy,
        independentIntegralLumen: integral, relativeConservationError, peakDensityLumenPerMm2: peakDensity,
        visitedCells: field.visitedCellCount, outsideDesignCells, blockedIlluminatedCells, invalidDensityCells }
    })
    const expectedAreaMm2 = (design.zMaximumMm - design.zMinimumMm) * gapMm
    const areaMm2 = fieldRows.reduce((sum, field) => sum + field.apertureAreaMm2, 0)
    const litAreaMm2 = fieldRows.reduce((sum, field) => sum + field.litAreaMm2, 0)
    const row = { name, gapMm, powerLumen, rays: result.total_rays, hits: result.receiver_hit_count,
      fullCapture: rawFullCapture, previewStatus: preview.status, rawReceiverPathEnergyLumen,
      previewCoverage: preview.status === 'ready' ? preview.coverage : null,
      samples: preview.samples.length, reconstructedSamples: surface.reconstructedSampleCount,
      fallbackSamples: surface.fallbackSamples.length, expectedAreaMm2, apertureAreaMm2: areaMm2,
      litAreaMm2, reconstructionMs, traceElapsedSeconds: fixture?.elapsed_seconds ?? null,
      meshSignature: result.source_context.scene.mesh_signature, reason: surface.reason ?? null, fields: fieldRows, diagnostics }
    rows.push(row)
    records.set(name, { scene, request, result, preview, surface, row })
    if (powerLumen === 0) {
      check(name, 'zero-power run has zero energy and no reconstructed light', rawReceiverPathEnergyLumen === 0 &&
        preview.samples.every((sample) => sample.weight === 0) && surface.fields.length === 0 &&
        surface.fallbackSamples.every((sample) => sample.weight === 0),
        { geometricReceiverHits: result.receiver_hit_count, energyLumen: rawReceiverPathEnergyLumen, samples: preview.samples.length, fields: surface.fields.length })
    } else {
      check(name, 'observations reconstruct on the actual horizontal side gap', fieldRows.length > 0 &&
        fieldRows.every((field) => field.face === design.face && Math.abs(field.ySpanMm - gapMm) < toleranceMm &&
          Math.abs(field.zSpanMm - 12) < toleranceMm && field.zSpanMm > field.ySpanMm), fieldRows.map((field) => ({ face: field.face, ySpanMm: field.ySpanMm, zSpanMm: field.zSpanMm })))
      check(name, 'at least 90 percent of samples are reconstructed', preview.samples.length > 0 &&
        surface.reconstructedSampleCount / preview.samples.length >= 0.9, { samples: preview.samples.length, reconstructed: surface.reconstructedSampleCount })
      check(name, 'at least 90 percent of CAD gap area is represented and locally supported',
        areaMm2 >= expectedAreaMm2 * 0.9 && areaMm2 <= expectedAreaMm2 * (1 + 1e-7) && litAreaMm2 >= expectedAreaMm2 * 0.9,
        { expectedAreaMm2, representedAreaMm2: areaMm2, litAreaMm2 })
      check(name, 'no open or illuminated cells cross metal', fieldRows.every((field) => field.outsideDesignCells === 0 && field.blockedIlluminatedCells === 0),
        fieldRows.map((field) => ({ outsideDesignCells: field.outsideDesignCells, blockedIlluminatedCells: field.blockedIlluminatedCells })))
      check(name, 'linear field conserves accepted sample energy', fieldRows.every((field) => field.invalidDensityCells === 0 && field.relativeConservationError < 1e-10),
        fieldRows.map((field) => field.relativeConservationError))
    }
    await writeFile(resolve(outputDirectory, name + '.bound-result.json'), JSON.stringify(result, null, 2))
  }

  const baseline = records.get('horizontal_gap_0p3_1x')
  const powerComparisons = []
  for (const [name, scale] of [['horizontal_gap_0p3_4x', 4], ['horizontal_gap_0p3_16x', 16]]) {
    const candidate = records.get(name)
    const sameGeometry = candidate.row.meshSignature === baseline.row.meshSignature
    const sameOpticsExceptPower = withoutPowerOrLabels(candidate.request) === withoutPowerOrLabels(baseline.request)
    check(name, 'brightness comparison uses identical geometry', sameGeometry)
    check(name, 'brightness comparison changes only source power', sameOpticsExceptPower)
    const baselineSamples = new Map(baseline.preview.samples.map((sample) => [sample.pathIndex, sample]))
    let mismatchedPaths = 0, maxPathPositionDifferenceMm = 0, maxPathDirectionDifference = 0, maxRayWeightRelativeError = 0
    for (const sample of candidate.preview.samples) {
      const reference = baselineSamples.get(sample.pathIndex)
      if (!reference || sample.receiverId !== reference.receiverId || JSON.stringify(sample.exitFaces) !== JSON.stringify(reference.exitFaces)) {
        mismatchedPaths += 1
        continue
      }
      for (let axis = 0; axis < 3; axis += 1) {
        maxPathPositionDifferenceMm = Math.max(maxPathPositionDifferenceMm, Math.abs(sample.exitPoint[axis] - reference.exitPoint[axis]))
        maxPathDirectionDifference = Math.max(maxPathDirectionDifference, Math.abs(sample.outgoingDirection[axis] - reference.outgoingDirection[axis]))
      }
      const referencePath = baseline.result.stored_paths[reference.pathIndex]
      const actualPath = candidate.result.stored_paths[sample.pathIndex]
      if (referencePath.length !== actualPath.length) { mismatchedPaths += 1; continue }
      for (let eventIndex = 0; eventIndex < referencePath.length; eventIndex += 1) {
        const expectedEvent = referencePath[eventIndex]
        const actualEvent = actualPath[eventIndex]
        if (expectedEvent.event_type !== actualEvent.event_type || expectedEvent.receiver_id !== actualEvent.receiver_id ||
          expectedEvent.face_index !== actualEvent.face_index || expectedEvent.depth !== actualEvent.depth) mismatchedPaths += 1
        for (let axis = 0; axis < 3; axis += 1) {
          maxPathPositionDifferenceMm = Math.max(maxPathPositionDifferenceMm, Math.abs(expectedEvent.point[axis] - actualEvent.point[axis]))
          maxPathDirectionDifference = Math.max(maxPathDirectionDifference, Math.abs(expectedEvent.normal[axis] - actualEvent.normal[axis]))
        }
        for (const energyKey of ['incoming_energy_lumen', 'outgoing_energy_lumen', 'receiver_flux_lumen']) {
          const expectedEnergy = expectedEvent[energyKey]
          const actualEnergy = actualEvent[energyKey]
          if (expectedEnergy == null || actualEnergy == null) {
            if (expectedEnergy !== actualEnergy) mismatchedPaths += 1
            continue
          }
          const difference = Math.abs(actualEnergy - expectedEnergy * scale)
          const relativeError = expectedEnergy > 0 ? difference / (expectedEnergy * scale) : difference === 0 ? 0 : Infinity
          maxRayWeightRelativeError = Math.max(maxRayWeightRelativeError, relativeError)
        }
      }
      maxRayWeightRelativeError = Math.max(maxRayWeightRelativeError, Math.abs(sample.weight - reference.weight * scale) / (reference.weight * scale))
    }
    const samePaths = mismatchedPaths === 0 && candidate.preview.samples.length === baseline.preview.samples.length &&
      maxPathPositionDifferenceMm < 1e-10 && maxPathDirectionDifference < 1e-12
    check(name, 'all receiver paths and outgoing directions remain identical', samePaths,
      { mismatchedPaths, baselineSamples: baseline.preview.samples.length, samples: candidate.preview.samples.length,
        maxPathPositionDifferenceMm, maxPathDirectionDifference })
    check(name, 'each ray energy follows the requested power ratio', maxRayWeightRelativeError < 1e-10, maxRayWeightRelativeError)
    let maxCellRelativeError = 0, maxCellAbsoluteError = 0, maxBinRelativeError = 0, maxBinDirectionDifference = 0
    let comparedCells = 0, gridMismatch = candidate.surface.fields.length !== baseline.surface.fields.length
    for (const reference of baseline.surface.fields) {
      const field = candidate.surface.fields.find((item) => item.mask.face === reference.mask.face)
      if (!field || JSON.stringify({ ...field.mask, open: Array.from(field.mask.open), componentIds: Array.from(field.mask.componentIds) }) !==
        JSON.stringify({ ...reference.mask, open: Array.from(reference.mask.open), componentIds: Array.from(reference.mask.componentIds) })) { gridMismatch = true; continue }
      for (let index = 0; index < reference.density.length; index += 1) {
        const expected = reference.density[index] * scale
        const difference = Math.abs(field.density[index] - expected)
        maxCellAbsoluteError = Math.max(maxCellAbsoluteError, difference)
        if (expected > 0) maxCellRelativeError = Math.max(maxCellRelativeError, difference / expected)
        else if (difference !== 0) maxCellRelativeError = Infinity
        for (const referenceBin of reference.bins) {
          const bin = field.bins.find((item) => item.id === referenceBin.id)
          const expectedBin = referenceBin.density[index] * scale
          if (!bin) { gridMismatch = true; continue }
          const binDifference = Math.abs(bin.density[index] - expectedBin)
          if (expectedBin > 0) maxBinRelativeError = Math.max(maxBinRelativeError, binDifference / expectedBin)
          else if (binDifference !== 0) maxBinRelativeError = Infinity
        }
        if (reference.mask.open[index]) comparedCells += 1
      }
      for (const referenceBin of reference.bins) {
        const bin = field.bins.find((item) => item.id === referenceBin.id)
        if (!bin) continue
        for (let axis = 0; axis < 3; axis += 1) maxBinDirectionDifference = Math.max(maxBinDirectionDifference, Math.abs(bin.direction[axis] - referenceBin.direction[axis]))
      }
    }
    const baselineInput = baseline.row.fields.reduce((sum, field) => sum + field.inputSampleEnergyLumen, 0)
    const input = candidate.row.fields.reduce((sum, field) => sum + field.inputSampleEnergyLumen, 0)
    const baselineDeposit = baseline.row.fields.reduce((sum, field) => sum + field.depositedSampleEnergyLumen, 0)
    const deposit = candidate.row.fields.reduce((sum, field) => sum + field.depositedSampleEnergyLumen, 0)
    const inputRatio = input / baselineInput, depositedRatio = deposit / baselineDeposit
    check(name, 'every cell and angular bin scales linearly before display', !gridMismatch && comparedCells > 0 &&
      maxCellRelativeError < 1e-10 && maxBinRelativeError < 1e-10 && maxBinDirectionDifference < 1e-12,
      { comparedCells, gridMismatch, maxCellRelativeError, maxCellAbsoluteError, maxBinRelativeError, maxBinDirectionDifference })
    check(name, 'input and deposited totals follow the power ratio', Math.abs(inputRatio / scale - 1) < 1e-10 && Math.abs(depositedRatio / scale - 1) < 1e-10,
      { expectedRatio: scale, inputRatio, depositedRatio })
    powerComparisons.push({ name, expectedRatio: scale, sameGeometry, sameOpticsExceptPower, samePaths,
      inputRatio, depositedRatio, comparedCells, maxRayWeightRelativeError,
      maxCellRelativeError, maxCellAbsoluteError, maxBinRelativeError, maxBinDirectionDifference })
  }

  const report = {
    generatedAtUtc: new Date().toISOString(), sourceDirectory: sampleDirectory,
    reconstruction: 'Only completed CPU results are used. Matching local scene/request source context is attached with the production helper; no ray tracing runs in this script.',
    design, fieldSettings: leakageApertureFieldDefaults,
    interpretation: {
      linearQuantity: 'Stored receiver-terminal ray flux per aperture area in lm/mm²; conserving this quantity does not establish physical camera luminance.',
      powerComparison: 'At fixed geometry, optical settings, sample paths and viewpoint, the linear input and field scale 1:4:16. A shared nonlinear display curve intentionally compresses the screen pixel ratio.',
      angularApproximation: 'Nine directional bins with energy-weighted representative directions feed an empirical viewing-angle kernel. There is no physical solid-angle or projected-area normalization for camera radiance.',
      scope: 'Continuous spatial support and relative input-power scaling are tested. Absolute nit, real eye/camera detectability, across-angle photometry and statistical convergence are not validated.',
    },
    passed: assertions.every((assertion) => assertion.passed), rows, powerComparisons, assertions,
  }
  await writeFile(resolve(outputDirectory, 'surface-validation.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ passed: report.passed,
    rows: rows.map((row) => ({ name: row.name, gapMm: row.gapMm, powerLumen: row.powerLumen, rays: row.rays,
      hits: row.hits, reconstructed: row.reconstructedSamples, fallback: row.fallbackSamples,
      expectedAreaMm2: row.expectedAreaMm2, apertureAreaMm2: row.apertureAreaMm2, litAreaMm2: row.litAreaMm2,
      reconstructionMs: row.reconstructionMs, reason: row.reason, diagnostics: row.diagnostics })),
    powerComparisons, failures: assertions.filter((assertion) => !assertion.passed),
  }, null, 2))
  if (!report.passed) process.exitCode = 1
} finally {
  await server.close()
}
