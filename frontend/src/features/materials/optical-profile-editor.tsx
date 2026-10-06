import { Sparkles } from 'lucide-react'
import type { OpticalValueOverride } from '@/stores'
import { NumberInput } from '@/components/ui/number-input'
import { HelpTooltip } from '@/components/common'
import { resolveOpticalProfile } from './optical-profile'
import type { CompiledOpticalProfile, ScatterModel } from './material-catalog'

const inputClass = 'h-9 w-full min-w-0 rounded-lg border border-input bg-background px-2.5 text-sm'

export function OpticalProfilePreview({ profile, label = 'Compiled optical preview' }: { profile: CompiledOpticalProfile; label?: string }) {
  const specularRatio = profile.scatterModel === 'specular' || profile.scatterModel === 'gaussian'
    ? 1 : profile.scatterModel === 'mixed' ? profile.specularRatio : 0
  const diffuseRatio = profile.scatterModel === 'lambertian'
    ? 1 : profile.scatterModel === 'mixed' ? profile.diffuseRatio : 0
  const values = [
    ['Reflectance', profile.reflectance.toFixed(3)],
    ['Loss', profile.loss.toFixed(3)],
    ['Specular', specularRatio.toFixed(2)],
    ['Diffuse', diffuseRatio.toFixed(2)],
  ]

  return <section aria-label={label} className="space-y-3 rounded-xl border border-primary/20 bg-primary/5 p-3">
    <div className="flex items-center gap-2 text-sm font-semibold">
      <Sparkles className="size-3.5 text-primary" />
      <span>{label}</span>
    </div>
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {values.map(([name, value]) => <div key={name} className="rounded-lg border border-border bg-background/45 p-2">
        <div className="text-sm text-muted-foreground">{name}</div>
        <div className="mt-1 font-mono text-base font-semibold">{value}</div>
      </div>)}
    </div>
    <div className="text-xs text-muted-foreground">{profile.scatterModel} · roughness {profile.roughness.toFixed(2)}
      {profile.scatterModel === 'gaussian' || profile.scatterModel === 'mixed' ? ` · σ ${profile.scatterSigmaDeg.toFixed(1)}°` : ''}</div>
  </section>
}

export function CompiledPreview({ baseMaterialId, surfaceId, opticalOverride }: {
  baseMaterialId: string; surfaceId: string; opticalOverride?: OpticalValueOverride; compact?: boolean
}) {
  return <OpticalProfilePreview profile={resolveOpticalProfile(baseMaterialId, surfaceId, opticalOverride)} />
}

export function OpticalValueEditor({ value, onChange }: {
  value: OpticalValueOverride; onChange(value: OpticalValueOverride): void
}) {
  const model = value.scatterModel ?? 'mixed'
  const field = (label: string, number: number, update: (next: number) => void, maximum?: number, minimum = 0) =>
    <label className="grid min-w-0 gap-1 text-xs font-medium">
      <span>{label}</span><NumberInput aria-label={`Custom ${label}`} className={inputClass} value={number}
        min={minimum} max={maximum} step={0.01} onValueChange={update} />
    </label>
  return <div className="space-y-3">
    <label className="grid gap-1 text-xs font-medium"><span>Reflection model</span>
      <select aria-label="Custom Reflection model" className={inputClass} value={model}
        onChange={(event) => {
          const scatterModel = event.currentTarget.value as ScatterModel
          onChange({ ...value, scatterModel,
            ...(scatterModel === 'none' ? { reflectance: 0, loss: 1 } : {}),
            specularRatio: scatterModel === 'specular' || scatterModel === 'gaussian' ? 1 : scatterModel === 'mixed' ? 0.5 : 0,
            diffuseRatio: scatterModel === 'lambertian' ? 1 : scatterModel === 'mixed' ? 0.5 : 0 })
        }}>
        <option value="specular">Specular · 경면</option><option value="lambertian">Lambertian · 확산</option>
        <option value="gaussian">Gaussian · 경면 주변 산란</option><option value="mixed">Mixed · 경면 계열 + 확산</option>
        <option value="none">None · 반사 없음</option>
      </select>
    </label>
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {field('Reflectance', value.reflectance, (reflectance) => onChange({ ...value, reflectance, loss: 1 - reflectance }), 1)}
      <label className="grid gap-1 text-xs font-medium"><span>Loss · 1 − Reflectance</span>
        <input aria-label="Custom Loss" readOnly className={`${inputClass} text-muted-foreground`} value={Number(value.loss.toPrecision(8))} /></label>
      {field('Roughness', value.roughness ?? 0.5, (roughness) => onChange({ ...value, roughness }), 1)}
      {model === 'gaussian' || model === 'mixed'
        ? field('Gaussian sigma (deg)', value.scatterSigmaDeg ?? 18, (scatterSigmaDeg) => onChange({ ...value, scatterSigmaDeg }), undefined, 0.000001) : null}
      {model === 'mixed' ? <>
        {field('Specular / Gaussian ratio', value.specularRatio, (specularRatio) => onChange({ ...value, specularRatio, diffuseRatio: 1 - specularRatio }), 1)}
        <label className="grid gap-1 text-xs font-medium"><span>Lambertian ratio · 나머지</span>
          <input aria-label="Custom Diffuse" readOnly className={`${inputClass} text-muted-foreground`} value={Number(value.diffuseRatio.toPrecision(8))} /></label>
      </> : null}
    </div>
    <div className="flex items-center gap-1 text-xs text-muted-foreground">반사 모델 기준
      <HelpTooltip label="Custom optical model 도움말">Mixed의 경면 계열은 σ ≤ 0.01°이면 Specular, 그보다 크면 Gaussian입니다. Angle-dependent reflectance가 ON이면 입력 반사율에 입사각 보정이 추가됩니다.</HelpTooltip>
    </div>
  </div>
}
