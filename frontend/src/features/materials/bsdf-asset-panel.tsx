import { useEffect, useId, useRef, useState } from 'react'
import { apiClient } from '@/api'
import { Button } from '@/components/ui/button'
import { useWorkspaceStore, workspaceSelectors, workspaceStore } from '@/stores'
import { bsdfAssetName, bsdfPreparationMessage, bsdfTransportProblem } from './bsdf-asset'

export function BsdfAssetPanel({ value, onChange, disabled = false }: {
  value: string
  onChange(id: string): void
  disabled?: boolean
}) {
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  const assets = useWorkspaceStore(workspaceSelectors.bsdfAssets)
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [imported, setImported] = useState('')
  const generation = useRef(0)
  useEffect(() => () => { generation.current += 1 }, [])
  const selected = assets.find((asset) => asset.id === value)

  async function importFiles(files: FileList | null) {
    if (!files?.length) return
    const ticket = ++generation.current
    let contextChanged = false
    const unsubscribe = workspaceStore.subscribe((state, previous) => {
      if (state.activeCadCaseId !== previous.activeCadCaseId || state.activeCad !== previous.activeCad) contextChanged = true
    })
    setBusy(true); setError(''); setImported('')
    try {
      const inputs = Array.from(files)
      if (inputs.length > 64 || inputs.some((file) => file.size > 2 * 1024 * 1024) || inputs.reduce((sum, file) => sum + file.size, 0) > 8 * 1024 * 1024) {
        throw new Error('BSDF 파일 묶음은 총 8 MiB 이하, 각 파일 2 MiB 이하, 64개 이하로 선택하세요.')
      }
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
      setImported(`${bsdfAssetName(asset)} 등록 완료. 아래 목록에서 선택하고 Apply를 눌러 연결하세요.`)
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
      <span className="font-semibold">BSDF 데이터</span>
      <Button type="button" variant="outline" size="sm" disabled={busy || disabled} onClick={() => input.current?.click()}>
        {busy ? '검증 중…' : 'LT ASCII 파일 등록'}
      </Button>
      <input ref={input} type="file" accept=".txt,.dat,.bsdf" multiple className="hidden" aria-label="Import LT ASCII BSDF files"
        disabled={busy || disabled} onChange={(event) => { void importFiles(event.currentTarget.files) }} />
    </div>
    <p className="text-muted-foreground">LT ASCII: master + 산란 표 + RT 파일을 함께 선택. 단일 .bsdf 형식은 아직 지원하지 않습니다.</p>
    <label htmlFor={id} className="block font-medium">연결할 BSDF</label>
    <select id={id} value={value} disabled={disabled} onChange={(event) => onChange(event.currentTarget.value)}
      className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm">
      <option value="">연결 없음 · 기존 광학 값 사용</option>
      {value && !selected ? <option value={value}>누락된 BSDF · {value}</option> : null}
      {assets.map((asset) => <option key={asset.id} value={asset.id}>{bsdfAssetName(asset)}</option>)}
    </select>
    {selected ? <>
      <p>{selected.data.grids.length}개 산란 표 · {selected.data.wavelengthsNm.join(', ')} nm · 입사각 {selected.data.incidenceAnglesDeg.join(', ')}°</p>
      <p className="text-muted-foreground">원본 {selected.files.length}개 파일 포함 · Polar / Relative BSDF · POI 0°</p>
      <p>반사율 R: {(Math.min(...selected.data.rt.map((row) => row.reflectance)) * 100).toFixed(2)}–{(Math.max(...selected.data.rt.map((row) => row.reflectance)) * 100).toFixed(2)}% · 투과율 T: 0%</p>
      <p>BSDF를 연결하면 반사율은 RT, 반사 방향은 산란 표로 계산합니다. 기존 Reflectance·Specular/Diffuse·Gaussian 각도와 Optical preview는 이 계산에 사용하지 않습니다.</p>
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
      <p className="text-muted-foreground">선택 면 양쪽에 같은 표 적용 · 측정 입사각 범위 밖은 끝값 사용. 이 옵션은 같은 파일을 연결한 부품/면에 함께 적용됩니다.</p>
      <p className="text-muted-foreground">LT 비교 시 Anisotropic OFF, No Shift로 맞추세요. Shift with Stretch는 아직 지원하지 않습니다.</p>
      {selected.warnings.map((warning, index) => <p key={index} className="text-amber-700 dark:text-amber-400">{warning}</p>)}
    </> : null}
    {value ? <p role="status" className="text-amber-700 dark:text-amber-400">{selected ? bsdfTransportProblem(selected) ?? 'BSDF 표 계산 사용 · CPU·NVIDIA GPU / source sampling · LT 실측 비교 검증 대기' : bsdfPreparationMessage}</p> : null}
    {imported ? <p role="status">{imported}</p> : null}
    {error ? <p role="alert" className="text-destructive">{error}</p> : null}
  </section>
}
