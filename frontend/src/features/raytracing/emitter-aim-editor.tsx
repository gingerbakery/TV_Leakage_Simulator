import type { EmitterAimSpec, Vec3 } from '@/api'
import { NumberInput } from '@/components/ui/number-input'
import { Button } from '@/components/ui/button'
import { createEmitterAim, isEmitterAimValid } from './emitter-aim'
import { planeAxesFromRotation, rotationFromPlaneAxes } from './ray-tracing-model'

const inputClassName = 'h-8 w-full rounded-lg border border-input bg-background px-2 text-sm'

export function EmitterAimEditor({
  aim, defaultCenter, onChange,
}: {
  aim: EmitterAimSpec
  defaultCenter: Vec3
  onChange(aim: EmitterAimSpec): void
}) {
  const rotation = rotationFromPlaneAxes(aim.u_axis, aim.v_axis, null)
  const setRotation = (next: Vec3) => {
    const axes = planeAxesFromRotation(next)
    onChange({ ...aim, u_axis: axes.uAxis, v_axis: axes.vAxis })
  }
  return (
    <details className="group rounded-xl border border-border/70">
      <summary data-enter-submit="false" className="flex cursor-pointer list-none items-center justify-between px-3 py-2 text-sm font-semibold">
        <span><span className="mr-2 inline-block group-open:rotate-90">›</span>Aim / Target</span>
        <span className="text-xs font-normal text-muted-foreground">{aim.enabled ? 'On' : 'Off'}</span>
      </summary>
      <div className="space-y-3 border-t border-border/60 p-3">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={aim.enabled} aria-label="Enable Aim Area"
            onChange={(event) => onChange({ ...aim, enabled: event.currentTarget.checked })} />
          Target 방향으로 발광
        </label>
        {aim.enabled ? <>
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
            <Button size="sm" variant="ghost" onClick={() => onChange({ ...createEmitterAim(defaultCenter), enabled: true })}>Reset Target</Button>
          </div>
          <div className="text-xs text-muted-foreground">Target 면적 균일 · 입력 광량 전체 배분</div>
          {!isEmitterAimValid(aim) ? <div role="alert" className="text-xs text-destructive">Target 크기를 0보다 크게 입력하세요.</div> : null}
        </> : null}
      </div>
    </details>
  )
}
