import { useId, useRef, useState } from 'react'
import { AppDialog } from '@/components/common'
import { Button } from '@/components/ui/button'
import { useWorkspaceStore, workspaceSelectors } from '@/stores'
import { bsdfAssetName } from './bsdf-asset'
import { BsdfAssetPanel } from './bsdf-asset-panel'
import { findBaseMaterial, findSurfaceProperty, opticalProfilePresets } from './material-catalog'
import { CompiledPreview } from './optical-profile-editor'

export function MaterialLibrary() {
  const [open, setOpen] = useState(false)
  const [profileId, setProfileId] = useState(opticalProfilePresets[0].id)
  const [bsdfId, setBsdfId] = useState('')
  const trigger = useRef<HTMLButtonElement>(null)
  const fieldId = useId()
  const custom = useWorkspaceStore(workspaceSelectors.customOpticalProfiles)
  const assets = useWorkspaceStore(workspaceSelectors.bsdfAssets)
  const profile = [...opticalProfilePresets, ...custom].find((item) => item.id === profileId)
  const bsdf = assets.find((item) => item.id === profile?.bsdfAssetId)

  return <>
    <Button ref={trigger} type="button" variant="outline" size="sm" className="w-full" onClick={() => setOpen(true)}>
      Material Library
    </Button>
    <AppDialog open={open} onOpenChange={setOpen} title="Material Library" returnFocusRef={trigger}
      help="CAD를 불러오기 전에도 기본 소재와 내 소재를 조회하고 BSDF 파일을 등록할 수 있습니다. 소재를 Component에 적용하려면 CAD를 불러온 뒤 Material에서 선택하세요. 라이브러리는 프로젝트 파일에 함께 저장됩니다."
      footer={<Button type="button" variant="outline" onClick={() => setOpen(false)}>닫기</Button>}>
      <div className="space-y-3">
        <label htmlFor={fieldId} className="block text-sm font-medium">소재</label>
        <select id={fieldId} value={profileId} onChange={(event) => setProfileId(event.currentTarget.value)}
          className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm">
          <optgroup label="기본 소재">{opticalProfilePresets.map((item) =>
            <option key={item.id} value={item.id}>{item.name} · 수동</option>)}</optgroup>
          {custom.length ? <optgroup label="내 소재">{custom.map((item) =>
            <option key={item.id} value={item.id}>{item.name} · {item.bsdfAssetId ? 'BSDF' : '수동'}</option>)}</optgroup> : null}
        </select>
        {profile ? <section className="space-y-2 rounded-lg border border-border p-3 text-xs" aria-label="Material details">
          <div>{findBaseMaterial(profile.baseMaterialId).name} · {findSurfaceProperty(profile.surfaceId).name}</div>
          {profile.bsdfAssetId
            ? <div>BSDF · {bsdf ? bsdfAssetName(bsdf) : '누락된 파일'}</div>
            : <CompiledPreview baseMaterialId={profile.baseMaterialId} surfaceId={profile.surfaceId} opticalOverride={'opticalOverride' in profile ? profile.opticalOverride : undefined} />}
        </section> : null}
        {open ? <BsdfAssetPanel value={bsdfId} onChange={setBsdfId} library /> : null}
      </div>
    </AppDialog>
  </>
}
