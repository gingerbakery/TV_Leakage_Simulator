import { useEffect, useId, useRef, useState } from 'react'
import { apiClient } from '@/api'
import { HelpTooltip } from '@/components/common'
import { Button } from '@/components/ui/button'
import { useWorkspaceStore, workspaceSelectors, workspaceStore } from '@/stores'
import { bsdfAssetName, bsdfImportLimits, bsdfPreparationMessage, bsdfTransportProblem } from './bsdf-asset'

const mib = (bytes: number) => `${(bytes / (1024 * 1024)).toFixed(bytes >= 1024 * 1024 ? 1 : 2)} MiB`

export function BsdfAssetPanel({ value, onChange, disabled = false, library = false }: {
  value: string
  onChange(id: string): void
  disabled?: boolean
  library?: boolean
}) {
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  const assets = useWorkspaceStore(workspaceSelectors.bsdfAssets)
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [imported, setImported] = useState('')
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const generation = useRef(0)
  useEffect(() => () => { generation.current += 1 }, [])
  const selected = assets.find((asset) => asset.id === value)

  function stageFiles(files: FileList | null) {
    if (!files?.length) return
    setError(''); setImported('')
    const merged = new Map(pendingFiles.map((file) => [file.name.toLocaleLowerCase(), file]))
    Array.from(files).forEach((file) => merged.set(file.name.toLocaleLowerCase(), file))
    const next = Array.from(merged.values())
    const totalBytes = next.reduce((sum, file) => sum + file.size, 0)
    if (next.length > bsdfImportLimits.maxFiles || next.some((file) => file.size > bsdfImportLimits.maxFileBytes) || totalBytes > bsdfImportLimits.maxTotalBytes) {
      setError(`BSDF 파일 묶음은 총 ${mib(bsdfImportLimits.maxTotalBytes)} 이하, 각 파일 ${mib(bsdfImportLimits.maxFileBytes)} 이하, ${bsdfImportLimits.maxFiles}개 이하로 추가하세요.`)
      if (input.current) input.current.value = ''
      return
    }
    setPendingFiles(next)
    if (input.current) input.current.value = ''
  }

  async function importFiles() {
    if (!pendingFiles.length) return
    const ticket = ++generation.current
    let contextChanged = false
    const unsubscribe = workspaceStore.subscribe((state, previous) => {
      if (state.activeCadCaseId !== previous.activeCadCaseId || state.activeCad !== previous.activeCad) contextChanged = true
    })
    setBusy(true); setError(''); setImported('')
    try {
      const inputs = pendingFiles
      const sourceFiles = await Promise.all(inputs.map(async (file) => {
        let text: string
        try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer()) }
        catch { throw new Error(`${file.name}: ASCII/UTF-8 텍스트만 지원합니다. 인코딩을 확인하세요.`) }
        return { name: file.name, text }
      }))
      const asset = await apiClient.importBsdf(sourceFiles)
      if (ticket !== generation.current) return
      if (contextChanged) throw new Error('가져오는 동안 CAD/케이스가 변경되어 등록을 취소했습니다. 현재 케이스에서 다시 선택하세요.')
      const existing = workspaceStore.getState().bsdfAssets?.find((item) => item.id === asset.id)
      // Registering the same source bundle must not reset options on linked parts.
      actions.addBsdfAsset(existing ? { ...asset, transport: existing.transport, simulationReady: Boolean(existing.transport) } : asset)
      setPendingFiles([])
      setImported(`${bsdfAssetName(asset)} 등록 완료`)
      if (library) onChange(asset.id)
    } catch (cause) {
      if (ticket === generation.current) setError(cause instanceof Error ? cause.message : 'BSDF 파일을 읽지 못했습니다.')
    } finally {
      unsubscribe()
      if (ticket === generation.current) {
        setBusy(false)
        if (input.current) input.current.value = ''
      }
    }
  }

  return <section aria-label="BSDF data" className="space-y-2 rounded-lg border border-border bg-muted/20 p-3 text-xs">
    <div className="flex items-center justify-between gap-2">
      <span className="flex items-center gap-1 font-semibold">BSDF 데이터
        <HelpTooltip label="BSDF 파일 추가 도움말">파일 추가는 등록 전까지 누적됩니다. 같은 이름을 다시 추가하면 최신 파일로 교체됩니다.</HelpTooltip>
      </span>
      <Button type="button" variant="outline" size="sm" disabled={busy || disabled} onClick={() => input.current?.click()}>
        파일 추가
      </Button>
      <input ref={input} type="file" accept=".txt,.dat,.bsdf" multiple className="hidden" aria-label="Import LT ASCII BSDF files"
        disabled={busy || disabled} onChange={(event) => stageFiles(event.currentTarget.files)} />
    </div>
    {pendingFiles.length ? <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-background px-2 py-1.5">
      <span>대기 {pendingFiles.length}개 · {mib(pendingFiles.reduce((sum, file) => sum + file.size, 0))}</span>
      <span className="flex gap-1">
        <Button type="button" variant="outline" size="sm" disabled={busy || disabled} onClick={() => { setPendingFiles([]); setError('') }}>비우기</Button>
        <Button type="button" size="sm" disabled={busy || disabled} onClick={() => { void importFiles() }}>{busy ? '검증 중…' : '등록'}</Button>
      </span>
    </div> : null}
    <label htmlFor={id} className="block font-medium">{library ? 'BSDF 라이브러리' : '연결할 BSDF'}</label>
    <select id={id} value={value} disabled={disabled} onChange={(event) => onChange(event.currentTarget.value)}
      className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm">
      <option value="">{library ? '선택' : '연결 없음 · 기존 광학 값 사용'}</option>
      {value && !selected ? <option value={value}>누락된 BSDF · {value}</option> : null}
      {assets.map((asset) => <option key={asset.id} value={asset.id}>{bsdfAssetName(asset)}</option>)}
    </select>
    {selected ? <>
      <p>{selected.data.grids.length}개 산란 표 · {selected.data.wavelengthsNm.join(', ')} nm · 입사각 {selected.data.incidenceAnglesDeg.join(', ')}°</p>
      <p className="text-muted-foreground">원본 {selected.files.length}개 파일 포함 · Polar / Relative BSDF · POI 0°</p>
      <p>반사율 R: {(Math.min(...selected.data.rt.map((row) => row.reflectance)) * 100).toFixed(2)}–{(Math.max(...selected.data.rt.map((row) => row.reflectance)) * 100).toFixed(2)}% · 투과율 T: 0%</p>
      <div className="flex items-center gap-1">계산 옵션<HelpTooltip label="BSDF 계산 옵션 도움말">BSDF를 연결하면 반사율은 RT, 반사 방향은 산란 표로 계산합니다. 기존 Reflectance·Specular/Diffuse·Gaussian 각도와 Optical preview는 이 계산에 사용하지 않습니다. 선택 면 양쪽에 같은 표를 적용하며 측정 입사각 범위 밖은 끝값을 사용합니다. 옵션은 같은 파일을 연결한 부품과 면에 함께 적용됩니다. LT 비교 시 Anisotropic OFF, No Shift로 맞추세요. Shift with Stretch는 지원하지 않습니다.</HelpTooltip></div>
      <label className="flex items-center gap-2">
        <input type="checkbox" aria-label="Enable isotropic No Shift BSDF tracing" disabled={disabled}
          checked={Boolean(selected.transport)} onChange={(event) => actions.addBsdfAsset({ ...selected,
            simulationReady: event.currentTarget.checked,
            transport: event.currentTarget.checked ? { model: 'isotropic_no_shift', wavelengthNm: selected.data.wavelengthsNm[0], aoiExtrapolation: 'clamp' } : undefined,
          })} />
        등방성 · No Shift로 계산
      </label>
      {selected.transport ? <label className="flex items-center gap-2">계산 파장 (nm)
        <input type="number" aria-label="BSDF calculation wavelength nm" disabled={disabled}
          className="h-8 w-24 rounded border border-input bg-background px-2" min={Math.min(...selected.data.wavelengthsNm)} max={Math.max(...selected.data.wavelengthsNm)}
          value={selected.transport.wavelengthNm} onChange={(event) => {
            const wavelengthNm = event.currentTarget.valueAsNumber
            if (Number.isFinite(wavelengthNm) && wavelengthNm > 0) actions.addBsdfAsset({ ...selected, transport: { ...selected.transport!, wavelengthNm } })
          }} />
      </label> : null}
      {selected.warnings.map((warning, index) => <p key={index} className="text-amber-700 dark:text-amber-400">{warning}</p>)}
    </> : null}
    {value ? <p role="status" className="text-amber-700 dark:text-amber-400">{selected ? bsdfTransportProblem(selected) ?? 'BSDF 표 계산 사용 · CPU·NVIDIA GPU / source sampling · LT 실측 비교 검증 대기' : bsdfPreparationMessage}</p> : null}
    {imported ? <p role="status">{imported}</p> : null}
    {error ? <p role="alert" className="text-destructive">{error}</p> : null}
  </section>
}
