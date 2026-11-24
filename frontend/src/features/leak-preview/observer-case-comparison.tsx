import { useEffect, useRef, useState } from 'react'
import { apiClient, type RayTraceRequest, type ScenePayload } from '@/api'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { HelpTooltip } from '@/components/common/help-tooltip'
import { NumberInput } from '@/components/ui/number-input'
import { cadCaseRayTraceSourcePaths, snapshotCadCasesForRayTrace, useWorkspaceStore, workspaceStore } from '@/stores'
import { buildLeakPreviewRequest, type LeakPreviewCandidate, type LeakPreviewProjectState } from './leak-preview-model'

async function sampleWindow(request: RayTraceRequest, cells: number, signal: AbortSignal) {
  signal.throwIfAborted()
  const started = await apiClient.startRayTrace(request)
  try {
    while (true) {
      signal.throwIfAborted()
      const job = await apiClient.getRayTraceJob(started.job_id)
      if (job.status === 'failed') throw new Error(job.error)
      if (job.status === 'cancelled') throw new Error('비교가 중단되었습니다.')
      if (job.status === 'completed') {
        const summary = job.result.metrics._observer_probe_summary as unknown as {
          complete?: boolean
          comparison_grid_nit?: number[]
        }
        const grid = summary?.comparison_grid_nit
        if (job.phase !== 'completed' || !summary?.complete || grid?.length !== cells || grid.some(nit => !Number.isFinite(nit) || nit < 0)) {
          throw new Error('비교 영역의 관찰 휘도 계산이 완료되지 않았습니다.')
        }
        return grid
      }
      await new Promise(resolve => setTimeout(resolve, 300))
    }
  } catch (error) {
    await apiClient.stopRayTrace(started.job_id).catch(() => undefined)
    throw error
  }
}

function Heatmap({ values, columns, scale, label, difference = false }: {
  values: number[]; columns: number; scale: number; label: string; difference?: boolean
}) {
  return <div className="min-w-0 space-y-1">
    <div className="text-center text-xs">{label}</div>
    <div className="grid overflow-hidden rounded border" style={{ aspectRatio: columns / (values.length / columns), gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
      {values.map((value, index) => <div key={index} title={`${(index % columns) + 1}, ${Math.floor(index / columns) + 1} · ${value.toPrecision(3)} nit`}
        style={{ gridRow: values.length / columns - Math.floor(index / columns), gridColumn: index % columns + 1, backgroundColor: `hsl(${difference && value < 0 ? 210 : 32} 90% ${12 + 52 * (scale > 0 ? Math.abs(value) / scale : 0)}%)` }} />)}
    </div>
  </div>
}

export function ObserverCaseComparison({ candidate, scene, source, onClose }: {
  candidate: LeakPreviewCandidate & { observerEvidence: NonNullable<LeakPreviewCandidate['observerEvidence']> }
  scene: ScenePayload
  source: LeakPreviewProjectState
  onClose(): void
}) {
  const cases = useWorkspaceStore(state => state.cadCases)
  const active = useWorkspaceStore(state => state.activeCadCaseId)
  const [target, setTarget] = useState(() => cases.find(item => item.caseId !== active)?.caseId ?? '')
  const view = candidate.observerEvidence
  const center = view.footprintCenter ?? candidate.center
  const [width, setWidth] = useState(() => Math.max(2, (view.footprintWidthMm ?? candidate.widthMm) + 2))
  const [height, setHeight] = useState(() => Math.max(2, (view.footprintHeightMm ?? candidate.heightMm) + 2))
  const [spacing, setSpacing] = useState(() => Math.max(.1, width / 32, height / 32))
  const [threshold, setThreshold] = useState(.1)
  const [progress, setProgress] = useState('')
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ before: number[]; after: number[]; columns: number; area: number }>()
  const run = useRef<AbortController | null>(null)
  useEffect(() => () => run.current?.abort(), [])
  const validGrid = [width, height, spacing].every(value => Number.isFinite(value) && value >= .1) && spacing <= 100 &&
    Math.ceil(width / spacing) <= 64 && Math.ceil(height / spacing) <= 64

  const compare = async () => {
    if (!active || !target || run.current || !validGrid) return
    const controller = new AbortController()
    run.current = controller
    setError('')
    setResult(undefined)
    setProgress('변경 전 준비 중')
    try {
      const [before, after] = snapshotCadCasesForRayTrace(workspaceStore.getState(), [active, target])
      if (!before || !after) throw new Error('비교할 Case를 확인하세요.')
      const columns = Math.ceil(width / spacing)
      const cells = columns * Math.ceil(height / spacing)
      const observer = {
        enabled: true, measurement: 'radiance' as const, search_mode: 'full_detail' as const,
        coverage: 'local' as const, horizontal_deg: view.horizontalDeg, vertical_deg: view.verticalDeg,
        range_deg: 0, step_deg: 1, resolution: 64, samples_per_cell: 64,
        spacing_mm: spacing, min_luminance_nit: 0,
        fixed_window: { center_mm: center, size_mm: [width, height] as [number, number] },
      }
      const request = (item: typeof before, model: ScenePayload, settings?: LeakPreviewProjectState) => {
        const setup = item.workspaceState
        const emitters = setup.emitters.filter(emitter => emitter.enabled &&
          (settings?.sourceMode !== 'emitters' || settings.sourceEmitterIds?.includes(emitter.emitter_id)))
        const sourceFaceIds = settings?.sourceMode === 'face' ? settings.sourceFaceIds : []
        const sourceComponentIds = settings?.sourceMode === 'body' ? settings.sourceComponentIds : []
        const useEmitters = !sourceFaceIds.length && !sourceComponentIds.length
        if (useEmitters ? !emitters.length : !sourceFaceIds.length && !sourceComponentIds.length) {
          throw new Error(`${item.name}: Preview 광원 또는 활성 Emitter를 설정하세요.`)
        }
        const config = before.workspaceState.rayTraceConfig
        const previewRequest = buildLeakPreviewRequest({
          scene: model, sourceFaceIds, sourceComponentIds, existingEmitters: useEmitters ? emitters : undefined,
          quality: 'fast', maxDepth: config.max_depth, precisionConfig: config, computeBackend: config.compute_backend,
          detectionMode: 'escape_directions', observer, directions: [view.axis],
          materialAssignments: setup.materialAssignments, bsdfAssets: setup.bsdfAssets, transformRules: setup.transformRules,
          deletedComponentIds: setup.deletedComponentIds, excludedComponentIds: setup.excludedComponentIds,
          blockers: settings?.blockers ?? [],
        })
        // The shared Preview job requires a forward dispatch. This result uses
        // only the reverse estimator, whose source power is independent of ray count.
        previewRequest.emitters = previewRequest.emitters.map(emitter => ({ ...emitter, ray_count: 1 }))
        previewRequest.config.ray_count = previewRequest.emitters.length
        previewRequest.config.store_ray_paths = false
        return previewRequest
      }
      const [path, ...accessories] = cadCaseRayTraceSourcePaths(after)
      if (!path) throw new Error('비교 Case의 CAD 경로를 확인하세요.')
      const targetScene = await apiClient.getScene(path, { signal: controller.signal }, accessories)
      const beforeRequest = request(before, scene, source)
      const afterRequest = request(after, targetScene, after.leakPreviewState)
      setProgress('변경 전 역추적 중')
      const beforeGrid = await sampleWindow(beforeRequest, cells, controller.signal)
      setProgress('변경 후 역추적 중')
      const afterGrid = await sampleWindow(afterRequest, cells, controller.signal)
      setResult({ before: beforeGrid, after: afterGrid, columns, area: width * height / cells })
    } catch (failure) {
      setError(controller.signal.aborted ? '비교를 중단했습니다.' : String(failure))
    } finally {
      run.current = null
      setProgress('')
    }
  }

  const metrics = (values: number[], area: number) => [
    Math.max(...values), values.reduce((sum, value) => sum + value, 0) / values.length,
    values.filter(value => value >= threshold).length * area,
  ]
  const before = result ? metrics(result.before, result.area) : []
  const after = result ? metrics(result.after, result.area) : []
  const delta = result?.after.map((value, index) => value - result.before[index]) ?? []
  const scale = result ? Math.max(...result.before, ...result.after) : 0
  return <Dialog open onOpenChange={open => { if (!open && !run.current) onClose() }}>
    <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
      <DialogHeader><DialogTitle>Case 비교</DialogTitle></DialogHeader>
      <fieldset disabled={Boolean(progress)} className="space-y-3">
        <label className="flex items-center gap-2 text-sm">변경 후 Case
          <select className="rounded border bg-background p-1" value={target} onChange={event => { setTarget(event.currentTarget.value); setResult(undefined) }}>
            {cases.filter(item => item.caseId !== active).map(item => <option key={item.caseId} value={item.caseId}>{item.name || item.cad.displayName}</option>)}
          </select>
          <HelpTooltip label="Case 비교 도움말">두 Case의 원본 CAD 좌표에 같은 관찰 격자를 적용합니다. 광원·재질·Block은 각 Case의 설정을 사용하므로 형상만 비교하려면 광원·재질 조건을 맞추세요. 휘도는 실측 미보정이며 0은 이번 표본에서 기여가 잡히지 않았다는 뜻입니다.</HelpTooltip>
        </label>
        <div className="text-xs">좌우 {view.horizontalDeg}° / 상하 {view.verticalDeg}° · 중심 {center.map(value => value.toFixed(2)).join(', ')} mm</div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          {([{ label: '가로 (mm)', value: width, setter: setWidth }, { label: '세로 (mm)', value: height, setter: setHeight },
            { label: '격자 간격 (mm)', value: spacing, setter: setSpacing }]).map(({ label, value, setter }) =>
            <label key={label}>{label}<NumberInput aria-label={label} min={.1} value={value} onValueChange={next => { setter(next); setResult(undefined) }} className="w-full rounded border p-1" /></label>)}
        </div>
        {!validGrid && <div role="alert" className="text-xs text-destructive">격자는 축당 64셀 이하로 지정하세요.</div>}
        <Button size="sm" disabled={!target || !validGrid} onClick={() => void compare()}>비교 실행</Button>
      </fieldset>
      {progress && <div className="flex items-center gap-2 text-sm" role="status">{progress}<Button size="sm" variant="outline" onClick={() => run.current?.abort()}>중지</Button></div>}
      {error && <div role="alert" className="text-sm text-destructive">{error}</div>}
      {result && <>
        <div className="grid grid-cols-3 gap-2">
          <Heatmap values={result.before} columns={result.columns} scale={scale} label="변경 전" />
          <Heatmap values={result.after} columns={result.columns} scale={scale} label="변경 후" />
          <Heatmap values={delta} columns={result.columns} scale={Math.max(...delta.map(Math.abs))} label="차이 · 후−전" difference />
        </div>
        <div className="text-xs">전·후 공통 색상 범위 0–{scale.toPrecision(3)} nit</div>
        <label className="text-xs">기준 휘도 (nit) <NumberInput min={0} value={threshold} onValueChange={setThreshold} className="w-20 rounded border p-1" /></label>
        <table className="w-full text-right text-xs"><thead><tr><th /><th>변경 전</th><th>변경 후</th><th>변화율</th></tr></thead><tbody>
          {['최고 셀 (nit)', '영역 평균 (nit)', '기준 이상 면적 (mm²)'].map((label, index) => <tr key={label}><th className="text-left">{label}</th>
            <td>{before[index].toPrecision(3)}</td><td>{after[index].toPrecision(3)}</td><td>{before[index] > 0 ? `${((after[index] / before[index] - 1) * 100).toFixed(1)}%` : '—'}</td></tr>)}
        </tbody></table>
        <div role="status" className="text-xs">단일 계산 · 불확실성 미평가</div>
      </>}
    </DialogContent>
  </Dialog>
}
