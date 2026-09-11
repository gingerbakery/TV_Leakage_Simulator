import type { EmitterAimSpec, Vec3 } from '@/api'
import { NumberInput } from '@/components/ui/number-input'
import { Button } from '@/components/ui/button'
import { HelpTooltip } from '@/components/common'
import { createEmitterAim, isEmitterAimValid, setEmitterAimMode } from './emitter-aim'
import { planeAxesFromRotation, rotationFromPlaneAxes } from './ray-tracing-model'

const inputClassName = 'h-8 w-full rounded-lg border border-input bg-background px-2 text-sm'

export function EmitterAimEditor({
  aim, defaultCenter, onChange,
}: {
  aim: EmitterAimSpec
  defaultCenter: Vec3
  onChange(aim: EmitterAimSpec): void
}) {
  const mode = aim.enabled ? aim.mode ?? 'area' : 'off'
  const rotation = rotationFromPlaneAxes(aim.u_axis, aim.v_axis, null)
    .map((value) => Number(value.toFixed(10))) as Vec3
  const setRotation = (next: Vec3) => {
    const axes = planeAxesFromRotation(next)
    onChange({ ...aim, u_axis: axes.uAxis, v_axis: axes.vAxis })
  }
  return (
    <details className="group rounded-xl border border-border/70">
      <summary data-enter-submit="false" className="flex cursor-pointer list-none items-center justify-between px-3 py-2 text-sm font-semibold">
        <span><span className="mr-2 inline-block group-open:rotate-90">›</span>Aim / Target</span>
        <span className="text-xs font-normal text-muted-foreground">{mode === 'off' ? 'Off' : mode === 'sphere' ? 'Sphere' : 'Area'}</span>
      </summary>
      <div className="space-y-3 border-t border-border/60 p-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="flex items-center gap-1.5">발광 방식
            <HelpTooltip label="Aim 방식 도움말">기본: 원래 각도 분포. Area: 특정 면을 향해 발광. Sphere: 지정한 각도 범위로 발광.</HelpTooltip>
          </span>
          <select data-enter-submit="false" aria-label="Emitter aiming mode" value={mode} className={inputClassName}
            onChange={(event) => onChange(setEmitterAimMode(aim, event.currentTarget.value as 'off' | 'area' | 'sphere'))}>
            <option value="off">기본 분포</option>
            <option value="area">Aim Area</option>
            <option value="sphere">Aim Sphere</option>
          </select>
        </label>
        {mode === 'sphere' ? <>
          <fieldset className="space-y-2">
            <legend className="flex items-center gap-1.5 text-xs text-muted-foreground">방사각 범위 (deg)
              <HelpTooltip label="Sphere 방사각 도움말">중심축에서 측정하는 각도입니다. 0~180°: 앞뒤 전방위. 0~90°: 반구. 0~0°: 평행광. 방위각은 축 주위 360°입니다.</HelpTooltip>
            </legend>
            <div className="grid grid-cols-2 gap-2">
              {([['sphere_upper_deg', 'Upper', 0], ['sphere_lower_deg', 'Lower', 180]] as const).map(([key, label, fallback]) =>
                <label key={key} className="flex min-w-0 flex-col gap-1 text-xs">{label}
                  <NumberInput className={inputClassName} aria-label={`Aim Sphere ${label}`} value={aim[key] ?? fallback} min={0} max={180} step="any"
                    onValueChange={(value) => onChange({ ...aim, [key]: value })} />
                </label>,
              )}
            </div>
            <div className="flex flex-wrap gap-1">
              {([['전방위', 0, 180], ['반구', 0, 90], ['좁은 빔', 0, 15], ['평행광', 0, 0]] as const).map(([label, upper, lower]) =>
                <Button key={label} size="sm" variant="outline" onClick={() => onChange({ ...aim, sphere_upper_deg: upper, sphere_lower_deg: lower })}>{label}</Button>,
              )}
            </div>
          </fieldset>
          <fieldset className="space-y-1">
            <legend className="flex items-center gap-1.5 text-xs text-muted-foreground">중심축 방향 · World (deg)
              <HelpTooltip label="Sphere 방향 도움말">World +Z축을 X축으로 Alpha, 다음 Y축으로 Beta만큼 회전합니다. 광원 위치·면의 기울기와 독립적이며 모든 발광점에 같은 각도 범위를 적용합니다.</HelpTooltip>
            </legend>
            <div className="grid grid-cols-2 gap-2">
              {([['sphere_alpha_deg', 'Alpha · X'], ['sphere_beta_deg', 'Beta · Y']] as const).map(([key, label]) =>
                <label key={key} className="flex min-w-0 flex-col gap-1 text-xs">{label}
                  <NumberInput className={inputClassName} aria-label={`Aim Sphere ${key === 'sphere_alpha_deg' ? 'Alpha' : 'Beta'}`} value={aim[key] ?? 0} step="any"
                    onValueChange={(value) => onChange({ ...aim, [key]: value })} />
                </label>,
              )}
            </div>
          </fieldset>
          <div className="flex items-center justify-between gap-2">
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" aria-label="Show Aim Sphere" checked={aim.show_in_viewer}
                onChange={(event) => onChange({ ...aim, show_in_viewer: event.currentTarget.checked })} />각도 가이드 표시
            </label>
            <Button size="sm" variant="ghost" onClick={() => onChange({ ...aim, sphere_upper_deg: 0, sphere_lower_deg: 180, sphere_alpha_deg: 0, sphere_beta_deg: 0, show_in_viewer: true })}>Reset Sphere</Button>
          </div>
          <div className="text-xs text-muted-foreground">입체각 균일 · 입력 광량 전체 배분</div>
          {!isEmitterAimValid(aim) ? <div role="alert" className="text-xs text-destructive">0 ≤ Upper &lt; Lower ≤ 180°로 입력하세요. 평행광은 0° / 0°입니다.</div> : null}
        </> : null}
        {mode === 'area' ? <>
          <div className="grid grid-cols-2 gap-2">
            <label className="flex min-w-0 flex-col gap-1 text-sm">Shape
              <select data-enter-submit="false" className={inputClassName} aria-label="Aim shape" value={aim.shape}
                onChange={(event) => onChange({ ...aim, shape: event.currentTarget.value as EmitterAimSpec['shape'] })}>
                <option value="rectangle">Rectangle</option>
                <option value="circle">Circle</option>
              </select>
            </label>
            <div className="flex items-end justify-end pb-2 text-xs text-muted-foreground">World coordinates · mm</div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {(aim.shape === 'circle' ? ['diameter'] : ['width_mm', 'height_mm']).map((dimension) => {
              const label = dimension === 'diameter' ? 'Diameter' : dimension === 'width_mm' ? 'Width' : 'Height'
              const value = dimension === 'diameter' ? aim.radius_mm * 2 : dimension === 'width_mm' ? aim.width_mm : aim.height_mm
                return <label key={dimension} className="flex min-w-0 flex-col gap-1 text-sm">{label} (mm)
                <NumberInput aria-label={`Aim ${label.toLowerCase()} (mm)`} className={inputClassName}
                  value={value} min={0.001} step="any"
                  onValueChange={(next) => onChange({ ...aim, [dimension === 'diameter' ? 'radius_mm' : dimension]: dimension === 'diameter' ? next / 2 : next })} />
              </label>
            })}
          </div>
          {(['Position (mm)', 'Tilt (deg)'] as const).map((label) => {
            const values = label === 'Position (mm)' ? aim.center : rotation
            return <fieldset key={label} className="space-y-1">
              <legend className="text-xs text-muted-foreground">{label}</legend>
              <div className="grid grid-cols-3 gap-2">
                {['X', 'Y', 'Z'].map((axis, index) => <label key={axis} className="flex min-w-0 flex-col gap-1 text-xs">{axis}
                  <NumberInput className={inputClassName} aria-label={`Aim ${label === 'Position (mm)' ? 'position' : 'tilt'} ${axis}`}
                    value={values[index]} step="any" onValueChange={(next) => {
                      const updated: Vec3 = [...values]
                      updated[index] = next
                      if (label === 'Position (mm)') onChange({ ...aim, center: updated })
                      else setRotation(updated)
                    }} />
                </label>)}
              </div>
            </fieldset>
          })}
          <div className="flex items-center justify-between gap-2">
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" aria-label="Show Aim Target" checked={aim.show_in_viewer}
                onChange={(event) => onChange({ ...aim, show_in_viewer: event.currentTarget.checked })} />
              Show Target
            </label>
            <Button size="sm" variant="ghost" onClick={() => onChange({ ...createEmitterAim(defaultCenter), enabled: true,
              sphere_upper_deg: aim.sphere_upper_deg, sphere_lower_deg: aim.sphere_lower_deg,
              sphere_alpha_deg: aim.sphere_alpha_deg, sphere_beta_deg: aim.sphere_beta_deg,
            })}>Reset Target</Button>
          </div>
          <div className="text-xs text-muted-foreground">Target 면적 균일 · 입력 광량 전체 배분</div>
          {!isEmitterAimValid(aim) ? <div role="alert" className="text-xs text-destructive">Target 크기를 0보다 크게 입력하세요.</div> : null}
        </> : null}
      </div>
    </details>
  )
}
