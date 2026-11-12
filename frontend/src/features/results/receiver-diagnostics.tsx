import { CircleHelp } from 'lucide-react'
import { Popover } from 'radix-ui'
import { Button } from '@/components/ui/button'
import { readReceiverDiagnostics, receiverDiagnosticLabels, type DiagnosticCategory } from './receiver-diagnostics-model'

export function ReceiverDiagnostics({ value, requested = false }: { value: unknown; requested?: boolean }) {
  const report = readReceiverDiagnostics(value)
  if (!report) return requested ? <div role="status" className="text-xs">순방향 진단 정보 없음 · 진단을 켜고 재실행 필요</div> : null
  return <section aria-label="순방향 원인 진단" className="space-y-2 rounded-lg border p-3 text-xs">
    <div className="flex items-center gap-1 font-semibold">순방향 원인 진단
      <Popover.Root><Popover.Trigger asChild><button aria-label="순방향 진단 결과 도움말" type="button" className="inline-flex size-5 items-center justify-center rounded-full focus-visible:ring-2"><CircleHelp className="size-3.5" /></button></Popover.Trigger>
        <Popover.Portal><Popover.Content side="bottom" className="z-50 max-h-80 w-80 max-w-[90vw] overflow-auto rounded-md border bg-popover p-3 text-xs leading-5 text-popover-foreground shadow-md">
          각 실제 진행 구간을 센서별로 한 번 분류합니다. 평행 → 뒤쪽 교점 → 면적 밖 → 형상 충돌 → 다른 센서 → 뒷면·수용각 → ROI → 검출 순서입니다. 면적 밖은 연장선의 투영이며 실제 도달이 아닙니다. 도달은 앞선 형상·다른 센서 없이 센서 면적까지 진행한 구간입니다.
          반사 구간이 포함되어 발사 Ray 수보다 클 수 있습니다. 구간 광량은 해당 구간의 가중 패킷 합이며 여러 센서·반사 구간 간 합산해서 누출 광량으로 해석하지 않습니다. 최종 검출 광량만 해당 Receiver의 수광량입니다.
          장면 이탈은 더 만날 형상·수용 센서가 없는 종료 구간이며 특정 틈의 통과를 뜻하지 않습니다. 종료 횟수는 전체 실행 기준이고 특정 센서 미검출의 확정 원인이 아닙니다. 0 검출은 빛샘 없음의 증거가 아니며 작은 광량의 안정성은 독립 반복으로 확인해야 합니다.
          진단은 CPU에서 수행하며 선택한 교차 계산 장치를 유지합니다. 대표 경로나 가상 검출을 결과에 더하지 않습니다.
        </Popover.Content></Popover.Portal>
      </Popover.Root>
    </div>
    {!report.complete ? <div role="status">부분 진단 · 완료 구간 기준</div> : null}
    <div>발사 {report.primary_ray_count.toLocaleString()} Ray · 장면 이탈 {report.escaped_segments.toLocaleString()}구간 · {report.escaped_segment_flux_lumen.toExponential(3)} lm</div>
    {report.receivers.map(row => <details key={row.receiver_id} className="rounded border p-2">
      <summary className="cursor-pointer">{row.receiver_id} · 도달 {row.arrived_count.toLocaleString()} · 검출 {row.actual_receiver_hits.toLocaleString()} · {row.actual_receiver_flux_lumen.toExponential(3)} lm</summary>
      {!row.consistent ? <div role="alert">진단·검출 집계 불일치 · 원인 판정 보류</div> : <div role="status" className="my-2">{row.actual_receiver_hits > 0 ? '검출 있음' : row.arrived_count > 0 ? '센서 도달 후 조건 제외' : '센서 면적 도달 표본 없음'}</div>}
      <table className="w-full tabular-nums"><thead><tr><th className="text-left">분류</th><th>구간 수</th><th>구간 광량 (lm)</th></tr></thead><tbody>
        {(Object.keys(receiverDiagnosticLabels) as DiagnosticCategory[]).map(key => <tr key={key}><th className="text-left font-normal">{receiverDiagnosticLabels[key]}</th><td className="text-right">{row.counts[key].toLocaleString()}</td><td className="text-right">{row.segment_flux_lumen[key].toExponential(3)}</td></tr>)}
      </tbody></table>
    </details>)}
    <div>전체 종료 · 에너지 컷오프 {String(report.termination.energy_cutoff_count ?? '—')} · 반사 한도 {String(report.termination.depth_limit_count ?? '—')} · 확률적 종료 {String(report.termination.roulette_terminated_count ?? '—')}</div>
    <Button size="sm" variant="outline" onClick={() => {
      const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}))
      const a=document.createElement('a'); a.href=url; a.download='forward-receiver-diagnostics.json'; a.click()
      setTimeout(()=>URL.revokeObjectURL(url),1000)
    }}>진단 JSON 저장</Button>
  </section>
}
